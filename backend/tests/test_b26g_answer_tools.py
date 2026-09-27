#!/usr/bin/env python3
"""批次26-G · AI 多角度解析 + 粉笔搜题核对 测试"""
import os, sys, json, tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk_q26g_")
os.environ["DATA_DIR"] = str(Path(TMP) / "data")
os.environ["DB_PATH"] = str(Path(TMP) / "vector")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["CHAT_DB"] = str(Path(TMP) / "chat.db")

from poc.fenbi_bind import FenbiBindStore, search_similar_local, _norm_stem
from poc.study import get_study_store, _norm_question

PASS, FAIL = 0, 0
def chk(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")

OPTS = json.dumps(["A. 甲", "B. 乙", "C. 丙", "D. 丁"], ensure_ascii=False)
ANALYSIS = "本题考查行政行为效力。具体行政行为确定后非经法定程序不得变更。故选B。"

print("== 1. 准备数据：粉笔错题快照 + 题库 ==")
store = FenbiBindStore()
# 对齐真实格式：content 为纯题干（选项独立存 options 字段）
with store._connect() as conn:
    for i, (qid, content, ans) in enumerate([
        ("1001", "下列关于行政许可的说法正确的是", "B"),
        ("1002", "下列关于行政许可实施程序的说法错误的是", "C"),
        ("1003", "行政行为的公定力理论如何理解", "C"),
    ]):
        conn.execute(
            "INSERT OR IGNORE INTO fenbi_wrong_questions(user_id, question_id, "
            "module, keypoint, content, options, answer, analysis, difficulty, "
            "source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("u1", qid, "常识判断", "行政法", content, OPTS, ans, ANALYSIS, 3,
             "2026模考卷", "2026-09-27T00:00:00Z"))

ss = get_study_store()
q1 = ss.add_question("T001", "choice", "下列关于行政许可的说法正确的是？",
                     "B", options=["A. 甲", "B. 乙", "C. 丙", "D. 丁"],
                     analysis=ANALYSIS, ext_id="1001")
q_pending = ss.add_question("T001", "choice", "待核题：行政行为的公定力如何理解？",
                            "X", options=["A. 甲", "B. 乙", "C. 丙", "D. 丁"])
# 强制置 pending（答案 X 无字母会自动 pending，稳妥起见直接验证）
with ss._connect() as conn:
    conn.execute("UPDATE question_bank SET answer_status='pending' WHERE id=?",
                 (q_pending["id"],))
print(f"  题库: q1(id={q1['id']}) q_pending(id={q_pending['id']})")

print("== 2. 本地相似搜题 ==")
hits = search_similar_local(store, "下列关于行政许可的说法正确的是？", top=3)
chk("命中 2 条相似题", len(hits) == 2, f"hits={len(hits)}")
chk("完全相同题相似度 ≥0.8", hits and hits[0]["similarity"] >= 0.8,
    f"sim={hits[0]['similarity'] if hits else '-'}")
chk("返回官方答案 B", hits and hits[0]["answer"] == "B")
chk("返回解析与来源", hits and "行政" in hits[0]["analysis"]
    and hits[0]["source"])
hits2 = search_similar_local(store, "完全无关的题目内容比如今天天气怎么样呢", top=3)
chk("无关题不误命中（相似度阈值）", all(h["similarity"] < 0.9 for h in hits2)
    or not hits2)
chk("过短题干直接拒绝", search_similar_local(store, "选啥？") == [])

print("== 3. _norm_stem 归一化 ==")
chk("去图片占位/空白/标点",
    _norm_stem("下列 说法[图]正确的是？ A. 甲") == "下列说法正确的是甲")

print("== 4. AI 解析缓存读写 ==")
key = _norm_question("下列关于行政许可的说法正确的是？")
chk("无缓存返回 None", ss.get_ai_analysis(key) is None)
fake = {"verdict": "agree", "ai_answer": "B", "reason": "独立解题与给定答案一致",
        "sections": [{"title": "考点定位", "content": "行政许可的生效规则"}]}
ss.save_ai_analysis(key, fake, "test-model")
got = ss.get_ai_analysis(key)
chk("缓存命中", got is not None and got["model"] == "test-model")
chk("缓存内容完整", got["content"]["sections"][0]["title"] == "考点定位"
    and got["content"]["ai_answer"] == "B")
ss.save_ai_analysis(key, {"verdict": "agree", "reason": "r2", "sections": fake["sections"]}, "m2")
chk("同 key 覆盖（UNIQUE 冲突更新）", ss.get_ai_analysis(key)["model"] == "m2")

print("== 5. verify_answer_with_fenbi（无 Cookie 全走本地路径） ==")
from poc import fenbi_bind as fbb
# 构造 pending 题：题干与快照近似但不完全相同 → 本地匹配
qv = ss.add_question("T001", "choice", "下列关于行政许可的说法正确的是？(2026回忆版)",
                     "B", options=["A. 甲", "B. 乙", "C. 丙", "D. 丁"], analysis="")
with ss._connect() as conn:
    conn.execute("UPDATE question_bank SET answer_status='pending' WHERE id=?",
                 (qv["id"],))
# 无有效绑定（last_check_ok=1 的 bound）→ 本地匹配路径
with store._connect() as conn:
    conn.execute("UPDATE fenbi_bindings SET last_check_ok=0")
rep = fbb.verify_answer_with_fenbi(store, limit=50)
chk("核对报告结构", all(k in rep for k in
    ("checked", "cleared", "mismatched", "unmatched")))
matched = [d for d in rep["details"] if d.get("result") in ("cleared", "mismatch")]
chk("近似题被本地匹配到", any(d["id"] == qv["id"] for d in matched),
    f"details={rep['details']}")
mine = [d for d in rep["details"] if d["id"] == qv["id"]]
if mine:
    chk("答案一致 → cleared 且恢复判分", mine[0]["result"] == "cleared",
        f"result={mine[0]}")
    with ss._connect() as conn:
        row = conn.execute("SELECT answer_status, analysis FROM question_bank WHERE id=?",
                           (qv["id"],)).fetchone()
    chk("answer_status 已清空", (row["answer_status"] or "") == "")
    chk("空解析回填官方解析", "行政" in (row["analysis"] or ""))
# q_pending 答案 X 与库内不一致 → mismatch 保留 pending
mine2 = [d for d in rep["details"] if d["id"] == q_pending["id"]]
if mine2:
    chk("答案不一致 → mismatch", mine2[0]["result"] == "mismatch",
        f"result={mine2[0]}")
    with ss._connect() as conn:
        row = conn.execute("SELECT answer_status FROM question_bank WHERE id=?",
                           (q_pending["id"],)).fetchone()
    chk("mismatch 保留 pending", (row["answer_status"] or "") == "pending")

print("== 6. 端点函数存在性与导入 ==")
import poc.api as api
chk("ai-analysis 端点注册", any(
    getattr(r, "path", "") == "/me/question/ai-analysis" for r in api.app.routes))
chk("fenbi-search 端点注册", any(
    getattr(r, "path", "") == "/me/question/fenbi-search" for r in api.app.routes))
chk("admin 核对端点注册", any(
    getattr(r, "path", "") == "/admin/answer-verify/fenbi" for r in api.app.routes))
chk("频控函数工作", api._ai_tool_allow("u_test", "ai", 30) is True)

print(f"\n通过 {PASS} · 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
