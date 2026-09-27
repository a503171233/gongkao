#!/usr/bin/env python3
"""批次26-F · 答案可信度门禁测试（选项范围 + 解析一致性 → pending 自动打标）"""
import os, sys, tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk_ans_guard_")
os.environ["DATA_DIR"] = str(Path(TMP) / "data")
os.environ["DB_PATH"] = str(Path(TMP) / "vector")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["CHAT_DB"] = str(Path(TMP) / "chat.db")

from poc.study import get_study_store, answer_consistency, StudyStore  # noqa: E402
import poc.autocollect as ac  # noqa: E402

PASS, FAIL = 0, 0
def chk(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")

OPTS_ABCD = ["A. 甲", "B. 乙", "C. 丙", "D. 丁"]

print("== 1. answer_consistency 单元：选项范围 ==")
chk("answer=A ∈ ABCD → 可信", answer_consistency("choice", "A", OPTS_ABCD) == "")
chk("answer=（A）容忍标点 → 可信", answer_consistency("choice", "（A）", OPTS_ABCD) == "")
chk("answer=E ∉ ABCD → pending",
    answer_consistency("choice", "E", OPTS_ABCD) == "pending")
chk("多选 ABD ⊆ ABCD → 可信", answer_consistency("choice", "ABD", OPTS_ABCD) == "")
chk("多选 ABF 含 F → pending", answer_consistency("choice", "ABF", OPTS_ABCD) == "pending")
chk("answer 无字母 → pending", answer_consistency("choice", "正确", OPTS_ABCD) == "pending")
chk("选项缺失不范围校验 → 可信", answer_consistency("choice", "A", None) == "")

print("== 2. answer_consistency 单元：解析一致性 ==")
chk("解析 故选B 与答案B 一致 → 可信",
    answer_consistency("choice", "B", OPTS_ABCD, "本题考查实词辨析，故选B。") == "")
chk("解析 故选C 与答案B 矛盾 → pending",
    answer_consistency("choice", "B", OPTS_ABCD, "本题考查实词辨析，故选C。") == "pending")
chk("解析 正确答案为D 与答案A 矛盾 → pending",
    answer_consistency("choice", "A", OPTS_ABCD, "正确答案为D。") == "pending")
chk("排除式引用（A错误，B正确，故选B）→ 可信",
    answer_consistency("choice", "B", OPTS_ABCD, "A项搭配不当，B项语义相符，故选B。") == "")
chk("无解析 → 不触发一致性校验", answer_consistency("choice", "B", OPTS_ABCD, "") == "")

print("== 3. judge 与 essay ==")
chk("judge 答案对+解析答案为对 → 可信",
    answer_consistency("judge", "对", None, "答案为对") == "")
chk("judge 答案对+解析答案是错误 → pending",
    answer_consistency("judge", "对", None, "答案是错误") == "pending")
chk("judge 无结论性解析 → 可信（防误伤）",
    answer_consistency("judge", "对", None, "本题考查法律常识，需理解条文含义") == "")
chk("essay 不校验", answer_consistency("essay", "参考要点：概括全面……", None) == "")

print("== 4. 入库自动打标 add_question ==")
st = get_study_store()
r1 = st.add_question("t1", "choice", "下列说法正确的是？甲乙丙丁。", "E",
                     options=OPTS_ABCD, analysis="解析：略")
chk("answer=E 入库成功（不拒收，宽容进）", bool(r1.get("id")))
chk("自动打标 pending", r1.get("answer_status") == "pending",
    f"got={r1.get('answer_status')}")
r2 = st.add_question("t1", "choice", "下列说法正确的是？甲乙丙丁（第二题）。", "B",
                     options=OPTS_ABCD, analysis="故选B")
chk("正常题 answer_status='' 可信", r2.get("answer_status") == "",
    f"got={r2.get('answer_status')}")
# 判分层隔离验证：pending 题不参与判分
row1 = st._parse_qrows([])  # 占位无意义，直接构造 dict 走 _grade_auto
q_pending = {"qtype": "choice", "answer": "E", "answer_status": "pending"}
q_ok = {"qtype": "choice", "answer": "B", "answer_status": ""}
chk("pending 题 _grade_auto=-1（不判分）", st._grade_auto(q_pending, "B") == -1.0)
chk("可信题 _grade_auto 正常判 0/1", st._grade_auto(q_ok, "B") == 1.0
    and st._grade_auto(q_ok, "C") == 0.0)
chk("_answer_trusted 识别 pending",
    StudyStore._answer_trusted(q_pending) is False
    and StudyStore._answer_trusted(q_ok) is True)

print("== 5. 批量入库透传与统计 ==")
items = [
    {"qtype": "choice", "question": "批量题1：经济利润问题计算。", "answer": "C",
     "options": OPTS_ABCD, "analysis": "故选C"},
    {"qtype": "choice", "question": "批量题2：行程问题速度计算。", "answer": "F",
     "options": OPTS_ABCD, "analysis": "略"},
]
rb = st.add_questions_batch("t1", items)
chk("批量入库 added=2", rb.get("added") == 2, f"rb={rb}")
import sqlite3, json as _json
with st._connect() as conn:
    rows = conn.execute(
        "SELECT answer, answer_status FROM question_bank WHERE question LIKE '批量题%' "
        "ORDER BY question").fetchall()
sts = {r["answer"]: r["answer_status"] for r in rows}
chk("批量题1(答案C) 可信", sts.get("C") == "", f"sts={sts}")
chk("批量题2(答案F) 自动 pending", sts.get("F") == "pending", f"sts={sts}")

print("== 6. 采集门禁：源头拒收 ==")
ok, why = ac._gongkao_real_question_shape(
    {"qtype": "choice", "question": "下列关于行政行为说法正确的是哪些？", "answer": "E",
     "options": OPTS_ABCD, "analysis": ""})
chk("采集门禁 answer=E 超范围拒收", ok is False and "超出选项范围" in why, f"why={why}")
ok, why = ac._gongkao_real_question_shape(
    {"qtype": "choice", "question": "下列关于行政行为说法正确的是哪些？", "answer": "ABD",
     "options": OPTS_ABCD, "analysis": ""})
chk("采集门禁 多选 ABD 放行", ok is True, f"why={why}")
ok, why = ac._gongkao_real_question_shape(
    {"qtype": "choice", "question": "下列关于行政行为说法正确的是哪些？", "answer": "正确",
     "options": OPTS_ABCD, "analysis": ""})
chk("采集门禁 无字母答案拒收", ok is False and "无法识别" in why, f"why={why}")
ok, why = ac._gongkao_real_question_shape(
    {"qtype": "essay", "question": "请根据给定资料概括基层治理的主要做法。", "answer": "要点一：党建引领……"})
chk("essay 不受字母校验影响", ok is True, f"why={why}")

print("== 7. 显式 answer_status 尊重调用方（可信来源通道） ==")
r3 = st.add_question("t1", "choice", "显式可信通道题：图形推理描述。", "D",
                     options=OPTS_ABCD, analysis="官方解析：答案 D",
                     answer_status="")
chk("显式传 '' 强制可信", r3.get("answer_status") == "")

print(f"\n通过 {PASS} · 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
