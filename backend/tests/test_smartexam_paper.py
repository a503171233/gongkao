# -*- coding: utf-8 -*-
"""智能组卷核心逻辑 · 单元测试（批次2 测试加固）
运行: cd backend && python tests/test_smartexam_paper.py
覆盖:
  - _grade_objective 判分矩阵：choice/judge 归一化、无答案/待核/主观题 -1
  - sample_questions：数量截断、条件过滤（qtype/difficulty/category/knowledge_point）、
    exclude_qids 去重、题库不足全量返回
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-smartexam-test-")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["SMARTEXAM_DB"] = os.path.join(TMP, "smartexam.db")
os.environ["STUDY_DB"] = os.path.join(TMP, "study.db")

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


from poc.smartexam import _grade_objective, SmartExamStore  # noqa: E402
from poc.study import StudyStore  # noqa: E402

# ── 1) _grade_objective 判分矩阵 ────────────────────────────────
check("choice 对", _grade_objective("choice", "A", "a") == 1.0)
check("choice 空格容错", _grade_objective("choice", "B", " b ") == 1.0)
check("choice 错", _grade_objective("choice", "A", "B") == 0.0)
check("choice 无用户答案", _grade_objective("choice", "A", "") == 0.0)
check("judge 对/正确归一", _grade_objective("judge", "对", "正确") == 1.0)
check("judge √/T 归一", _grade_objective("judge", "对", "T") == 1.0)
check("judge ×/F 归一", _grade_objective("judge", "错", "×") == 1.0)
check("judge 错误答案", _grade_objective("judge", "对", "错") == 0.0)
check("无答案题 -1", _grade_objective("choice", "", "A") == -1.0)
check("答案待核 -1", _grade_objective("choice", "A", "A", trusted=False) == -1.0)
check("主观题 -1", _grade_objective("essay", "参考要点", "随便答") == -1.0)

# ── 2) sample_questions 抽题 ────────────────────────────────────
study = StudyStore(os.environ["STUDY_DB"])
T = "T-TEST"
rows = []
# 6 道选择题：难度 1×3、2×3；知识点 kpA×4 kpB×2；分类 catA×3 catB×3
for i in range(6):
    rows.append((T, "choice", f"选择题{i}", '["A. x","B. y"]', "A", "解析", 1 if i < 3 else 2,
                 "kpA" if i < 4 else "kpB", 1, "catA" if i % 2 == 0 else "catB"))
rows.append((T, "judge", "判断题0", "", "对", "", 2, "kpA", 1, "catA"))
rows.append((T, "essay", "主观题0", "", "要点", "", 3, "kpB", 1, "catB"))
rows.append((T, "choice", "禁用题", "", "C", "", 1, "kpA", 0, "catA"))  # enabled=0 不应被抽出
conn = sqlite3.connect(os.environ["STUDY_DB"])
conn.executemany(
    """INSERT INTO question_bank (teacher_id, qtype, question, options, answer, analysis,
       difficulty, knowledge_point, enabled, created_at, category)
       VALUES (?,?,?,?,?,?,?,?,?, datetime('now'), ?)""", rows)
conn.commit()
conn.close()

pool = study.sample_questions(T, count=100)
check("题库全量（排除禁用题）= 8", len(pool) == 8, str(len(pool)))
check("禁用题未混入", all("禁用题" not in p["question"] for p in pool))

few = study.sample_questions(T, count=3)
check("count=3 截断", len(few) == 3, str(len(few)))

by_type = study.sample_questions(T, qtype="judge", count=10)
check("qtype 过滤 judge=1", len(by_type) == 1 and by_type[0]["qtype"] == "judge", str(len(by_type)))

by_diff = study.sample_questions(T, difficulty=2, count=10)
check("difficulty=2 过滤 = 4", len(by_diff) == 4, str(len(by_diff)))

by_kp = study.sample_questions(T, knowledge_point="kpB", count=10)
check("knowledge_point 过滤 kpB = 3", len(by_kp) == 3, str(len(by_kp)))

by_cat = study.sample_questions(T, category="catA", count=10)
check("category 过滤 catA", len(by_cat) >= 1 and all(
    (p.get("category") or "catA") == "catA" for p in by_cat), str(len(by_cat)))

# exclude_qids 去重：排除全部 8 题 → 空；排除 5 题 → 剩 3
all_ids = [p["id"] for p in pool]
rest = study.sample_questions(T, count=10, exclude_qids=all_ids[:5])
check("exclude 后不重叠", not (set(p["id"] for p in rest) & set(all_ids[:5])), str(len(rest)))
check("exclude 后数量 = 3", len(rest) == 3, str(len(rest)))

check("空库老师返回空", study.sample_questions("T-EMPTY", count=5) == [])

print()
print("PASS" if ok else "FAIL", "- test_smartexam_paper")
sys.exit(0 if ok else 1)
