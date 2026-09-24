# -*- coding: utf-8 -*-
"""#36 分类专项练习 / 错题重练 / 学情报告 / AI 批改 —— 本地纯逻辑单测。

运行：cd backend && python -m pytest tests/test_36_practice.py -q
（无 pytest 时：python tests/test_36_practice.py）
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from poc.study import StudyStore  # noqa: E402


def _store() -> StudyStore:
    tmp = tempfile.mkdtemp(prefix="gk36_")
    return StudyStore(db_path=Path(tmp) / "study.db")


def _seed(st: StudyStore) -> dict:
    """T999 题库：3 道言语理解 + 2 道判断推理 + 1 道申论。"""
    ids = {}
    for i, (qtype, q, ans, cat) in enumerate([
        ("choice", "言语题A：下列正确的是？", "A", "言语理解"),
        ("choice", "言语题B：主旨概括选？", "B", "言语理解"),
        ("choice", "言语题C：逻辑填空选？", "C", "言语理解"),
        ("choice", "判断题D：图形推理规律？", "D", "判断推理"),
        ("judge", "判断题E：对错判断？", "对", "判断推理"),
        ("essay", "申论题F：请围绕基层治理写一段议论。", "要点：治理现代化", "申论"),
    ], start=1):
        opts = ["A. 甲", "B. 乙", "C. 丙", "D. 丁"] if qtype == "choice" else None
        d = st.add_question("T999", qtype, q, ans, options=opts,
                            analysis="测试解析", category=cat)
        ids[q] = d["id"]
    return ids


def test_migration_columns():
    st = _store()
    import sqlite3
    with st._connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(practice_logs)").fetchall()}
        mcols = {r[1] for r in conn.execute("PRAGMA table_info(mistakes)").fetchall()}
    assert {"question_id", "category", "grade_report"} <= cols, cols
    assert "question_id" in mcols, mcols


def test_practice_categories():
    st = _store()
    _seed(st)
    cats = st.practice_categories("T999")
    m = {c["category"]: c["count"] for c in cats}
    assert m.get("言语理解") == 3 and m.get("判断推理") == 2 and m.get("申论") == 1


def test_next_practice_category_filter():
    st = _store()
    ids = _seed(st)
    for _ in range(12):
        d = st.get_next_practice("T999", "u-cat", category="言语理解")
        assert d["category"] == "言语理解", d
        assert d["type"] == "choice"
        assert d["id"] in (ids["言语题A：下列正确的是？"], ids["言语题B：主旨概括选？"],
                           ids["言语题C：逻辑填空选？"])
    # 申论专项：type=essay 且不带 options
    d = st.get_next_practice("T999", "u-cat", category="申论")
    assert d["type"] == "essay" and "options" not in d and d["category"] == "申论"


def test_wrong_mode_and_repractice():
    st = _store()
    ids = _seed(st)
    # u-wrong 做对 A 题、做错 D 题
    st.submit_practice("u-wrong", "T999", "言语题A：下列正确的是？", "A",
                       question_id=ids["言语题A：下列正确的是？"])
    st.submit_practice("u-wrong", "T999", "判断题D：图形推理规律？", "A",
                       question_id=ids["判断题D：图形推理规律？"])
    # 错题池：只含 D 题（A 已做对不进池）
    for _ in range(5):
        d = st.get_next_practice("T999", "u-wrong", mode="wrong")
        assert d["id"] == ids["判断题D：图形推理规律？"], d
    # 错题池空的用户 → 回落普通抽题并标记
    d2 = st.get_next_practice("T999", "u-clean", mode="wrong")
    assert d2.get("mode_fallback") == "no_wrong", d2
    # 指定题重练
    d3 = st.get_next_practice("T999", "u-wrong",
                              question_id=ids["言语题B：主旨概括选？"])
    assert d3["id"] == ids["言语题B：主旨概括选？"] and d3["category"] == "言语理解"


def test_submit_records_qid_category_and_mistake_link():
    st = _store()
    ids = _seed(st)
    log = st.submit_practice("u-rec", "T999", "判断题D：图形推理规律？", "A",
                             question_id=ids["判断题D：图形推理规律？"])
    assert log["score"] == 0.0 and log["question_id"] == ids["判断题D：图形推理规律？"]
    assert log["category"] == "判断推理"
    ms = st.list_mistakes("u-rec", "T999")
    assert len(ms) == 1 and ms[0]["question_id"] == ids["判断题D：图形推理规律？"]
    # practice_logs 落库校验
    logs = st.list_practice_logs("u-rec", "T999")
    assert logs[0]["question_id"] == ids["判断题D：图形推理规律？"]
    assert logs[0]["category"] == "判断推理"


def test_practice_report():
    st = _store()
    ids = _seed(st)
    # u-rpt：言语 2对1错（66.7%），判断 0对3错（0%）
    st.submit_practice("u-rpt", "T999", "言语题A：下列正确的是？", "A",
                       question_id=ids["言语题A：下列正确的是？"])
    st.submit_practice("u-rpt", "T999", "言语题B：主旨概括选？", "B",
                       question_id=ids["言语题B：主旨概括选？"])
    st.submit_practice("u-rpt", "T999", "言语题C：逻辑填空选？", "A",
                       question_id=ids["言语题C：逻辑填空选？"])
    for _ in range(3):
        st.submit_practice("u-rpt", "T999", "判断题D：图形推理规律？", "A",
                           question_id=ids["判断题D：图形推理规律？"])
    r = st.practice_report("u-rpt", "T999")
    assert r["graded"] == 6 and r["correct"] == 2
    assert abs(r["accuracy"] - 0.333) < 1e-9, r["accuracy"]
    m = {c["category"]: c for c in r["by_category"]}
    assert m["言语理解"]["total"] == 3 and m["言语理解"]["correct"] == 2
    assert m["判断推理"]["accuracy"] == 0
    # 薄弱点：判断推理（3 题 0%）在列，言语理解（66.7%）不在
    assert "判断推理" in r["weak"] and "言语理解" not in r["weak"]
    assert r["essay"]["pending"] == 0
    # essay 批改后：pending 减、avg_score 出现
    log_e = st.submit_practice("u-rpt", "T999", "申论题F：请围绕基层治理写一段议论。",
                                "基层治理现代化需要多方共治……",
                                question_id=ids["申论题F：请围绕基层治理写一段议论。"])
    st.save_grade("u-rpt", log_e["id"], 0.84, "[AI批改 84分] 测试",
                  {"scores": {"立意": 85}, "total": 84})
    r2 = st.practice_report("u-rpt", "T999")
    assert r2["essay"]["pending"] == 0
    assert abs(r2["essay"]["avg_score"] - 0.84) < 1e-9, r2["essay"]


def test_get_log_save_grade_roundtrip():
    st = _store()
    ids = _seed(st)
    log = st.submit_practice("u-g", "T999", "申论题F：请围绕基层治理写一段议论。",
                             "基层治理是国家治理的基石……",
                             question_id=ids["申论题F：请围绕基层治理写一段议论。"])
    # essay：返回 score=None（前端据此走待批分支），库内落 -1
    assert log["score"] is None
    got = st.get_practice_log("u-g", log["id"])
    assert got["score"] == -1 and got["grade_report"] is None
    report = {"scores": {"立意": 80, "结构": 70, "论证": 75, "语言": 85},
              "total": 78, "comment": "结构完整", "suggestions": ["加强论证"],
              "model_answer": "……"}
    assert st.save_grade("u-g", log["id"], 0.78, "[AI批改 78分] 结构完整", report)
    got2 = st.get_practice_log("u-g", log["id"])
    assert got2["score"] == 0.78
    assert got2["grade_report"]["total"] == 78
    assert got2["feedback"].startswith("[AI批改 78分]")


def test_get_question_by_id():
    st = _store()
    ids = _seed(st)
    q = st.get_question_by_id("T999", ids["言语题A：下列正确的是？"])
    assert q and q["category"] == "言语理解" and q["options"][0] == "A. 甲"
    assert st.get_question_by_id("T999", 99999) is None
    assert st.get_question_by_id("T888", ids["言语题A：下列正确的是？"]) is None
    # 既有单参 get_question(qid) 不受影响（update_question 依赖）
    q2 = st.get_question(ids["言语题A：下列正确的是？"])
    assert q2 and q2["id"] == ids["言语题A：下列正确的是？"]


def test_extract_json_obj():
    from poc.api import _extract_json_obj
    ok = '{"total": 78}'
    assert _extract_json_obj(ok)["total"] == 78
    assert _extract_json_obj("```json\n" + ok + "\n```")["total"] == 78
    assert _extract_json_obj("批改结果如下：\n" + ok + "\n以上。")["total"] == 78
    # #36 加固：前后杂文 + 多 JSON 块（思考型模型），优先含 scores/total 的对象
    messy = '思考过程 {"draft": 1} 结论 {"total": 85, "scores": {"立意": 90}} 完'
    assert _extract_json_obj(messy)["total"] == 85
    assert _extract_json_obj('前缀文字\n{"a": {"b": 1}}\n后缀')["a"]["b"] == 1
    for bad in ("", "not json", "[]", "没有花括号"):
        try:
            _extract_json_obj(bad)
            raise AssertionError(f"should fail: {bad!r}")
        except ValueError:
            pass


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} ALL PASS")
