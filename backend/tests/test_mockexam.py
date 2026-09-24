# -*- coding: utf-8 -*-
"""F1 模考解析系统 —— 本地纯逻辑单测（store + 任务引擎 + 老师路由）。

运行：cd backend && python -m pytest tests/test_mockexam.py -q
（无 pytest 时：python tests/test_mockexam.py）
"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from poc import mockexam  # noqa: E402


class FakeCfg:
    teacher_id = "T001"

    def list_teachers_all(self):
        return [
            {"teacher_id": "T001", "teacher_name": "星辰老师", "teacher_subject": "言语理解",
             "course_category": "", "company": "", "sort_order": 0, "enabled": True,
             "llm_model": "gpt-5.6-luna", "temperature": 0.3, "top_n": 6, "threshold": 0.5,
             "prompt_style": "classroom"},
            {"teacher_id": "T002", "teacher_name": "云舟老师", "teacher_subject": "逻辑判断",
             "course_category": "", "company": "", "sort_order": 1, "enabled": True,
             "llm_model": "gpt-5.6-luna", "temperature": 0.3, "top_n": 6, "threshold": 0.5,
             "prompt_style": "classroom"},
            {"teacher_id": "T003", "teacher_name": "谭老师", "teacher_subject": "申论",
             "course_category": "", "company": "", "sort_order": 2, "enabled": True,
             "llm_model": "gpt-5.6-luna", "temperature": 0.3, "top_n": 6, "threshold": 0.5,
             "prompt_style": "classroom"},
        ]

    def get_teacher(self, teacher_id):
        from poc.config import Config
        return Config().get_teacher(teacher_id)


def _store():
    tmp = tempfile.mkdtemp(prefix="gk_mockexam_")
    return mockexam.MockExamStore(db_path=Path(tmp) / "mockexam.db")


def _install(store, fake_extract, fake_answer=None, wait=False):
    from poc import admin
    orig_extract = admin.extract_questions_ai
    orig_answer = mockexam._answer_one
    orig_cfg = mockexam.Config
    orig_singleton = mockexam._store
    admin.extract_questions_ai = fake_extract
    if fake_answer is not None:
        mockexam._answer_one = fake_answer
    mockexam.Config = FakeCfg
    if store is not None:
        mockexam._store = store

    def restore():
        admin.extract_questions_ai = orig_extract
        mockexam._answer_one = orig_answer
        mockexam.Config = orig_cfg
        mockexam._store = orig_singleton

    return restore


def _fake_questions():
    return {
        "questions": [
            {"qtype": "choice", "question": "1. 下列词语使用正确的是？",
             "options": ["A. 甲", "B. 乙"], "answer": "A", "analysis": "",
             "difficulty": 1, "knowledge_point": "词语辨析", "category": "言语理解"},
            {"qtype": "choice", "question": "2. 主旨概括应选？",
             "options": ["A. 甲", "B. 乙"], "answer": "B", "analysis": "",
             "difficulty": 1, "knowledge_point": "主旨概括", "category": "言语理解"},
            {"qtype": "judge", "question": "3. 图形推理图形对称，判断对错？",
             "options": [], "answer": "对", "analysis": "",
             "difficulty": 1, "knowledge_point": "图形推理", "category": "判断推理"},
            {"qtype": "essay", "question": "4. 请围绕基层治理写一段议论。",
             "options": [], "answer": "治理现代化", "analysis": "",
             "difficulty": 2, "knowledge_point": "基层治理", "category": "申论"},
        ],
        "n_expected": 4,
        "warnings": [],
    }


def _default_answer(q):
    return {"answer": "A", "analysis": f"解析（{q.get('category')}）"}


def _wait_done(store, paper_id, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        p = store.heal_paper(paper_id)
        if not store.is_running(paper_id) and p and p["status"] in ("ready", "failed"):
            return p
        time.sleep(0.05)
    raise AssertionError("任务超时未结束")


def test_schema_and_paper_crud():
    st = _store()
    pid = st.create_paper("u1", "行测模考", "行测", "text", "", "测试文本" * 5)
    p = st.get_paper(pid)
    assert p and p["status"] == "pending" and p["user_id"] == "u1"
    assert st.get_paper_for_user(pid, "u2") is None
    total, papers = st.list_papers("u1")
    assert total == 1 and papers[0]["title"] == "行测模考"
    st.update_paper(pid, status="failed", message="boom")
    assert st.get_paper(pid)["message"] == "boom"
    assert st.count_questions(pid) == 0
    st.delete_paper(pid)
    assert st.get_paper(pid) is None and st.list_papers("u1")[0] == 0


def test_route_teacher_by_category():
    restore = _install(None, None)
    try:
        mockexam.Config = FakeCfg
        assert mockexam._route_teacher("言语理解")[0] == "T001"
        assert mockexam._route_teacher("判断推理")[0] == "T002"
        assert mockexam._route_teacher("申论")[0] == "T003"
        tid, name = mockexam._route_teacher("数量关系")
        assert tid == "T001" and name == "星辰老师"
    finally:
        restore()


def test_full_job_routes_and_scores():
    st = _store()
    restore = _install(st, lambda text: _fake_questions(), _default_answer)
    try:
        pid = st.create_paper("u1", "", "", "text", "", "整卷")
        assert mockexam.start_paper_job(pid) is True
        assert mockexam.start_paper_job(pid) is False
        p = _wait_done(st, pid)
        assert p["status"] == "ready", p["message"]
        assert p["exam_type"] == "行测"
        assert p["total_questions"] == 4 and p["done_questions"] == 4 and p["failed_questions"] == 0
        qs = st.list_questions(pid)
        teachers = [(q["category"], q["teacher_id"]) for q in qs]
        assert teachers[0] == ("言语理解", "T001")
        assert teachers[2] == ("判断推理", "T002")
        assert teachers[3] == ("申论", "T003")
        assert all(q["answer"] == "A" and q["analysis"] for q in qs)
        assert p["summary"]["total"] == 4 and p["summary"]["ok"] == 4
        assert p["summary"]["teachers"]["谭老师"]["total"] == 1
    finally:
        restore()


def test_partial_failure_summary():
    st = _store()

    def flaky_answer(q):
        if "3." in q.get("question"):
            raise ValueError("模拟解析失败")
        return _default_answer(q)

    restore = _install(st, lambda text: _fake_questions(), flaky_answer)
    try:
        pid = st.create_paper("u1", "行测模考", "", "text", "", "整卷")
        mockexam.start_paper_job(pid)
        p = _wait_done(st, pid)
        assert p["status"] == "ready"
        assert p["done_questions"] == 3 and p["failed_questions"] == 1
        qs = {q["question"][:2]: q for q in st.list_questions(pid)}
        assert qs["3."]["status"] == "failed" and qs["3."]["error"]
        assert p["summary"]["failed"] == 1 and p["summary"]["teachers"]["云舟老师"]["failed"] == 1
    finally:
        restore()


def test_retry_reset_and_reextract():
    st = _store()
    restore = _install(st, lambda text: _fake_questions(), _default_answer)
    try:
        pid = st.create_paper("u1", "", "", "text", "", "整卷")
        mockexam.start_paper_job(pid)
        p = _wait_done(st, pid)
        assert p["done_questions"] == 4
        st.reset_questions(pid)
        p2 = st.get_paper(pid)
        assert p2["done_questions"] == 0 and p2["failed_questions"] == 0
        assert all(q["status"] == "pending" for q in st.list_questions(pid))
        assert st.count_questions(pid) == 4
        st.delete_paper(pid)
        assert st.count_questions(pid) == 0
    finally:
        restore()


def test_stale_heal():
    st = _store()
    pid = st.create_paper("u1", "", "", "text", "", "x" * 30)
    st.update_paper(pid, status="extracting", message="进行中")
    import sqlite3
    with st._connect() as conn:
        conn.execute("UPDATE mockexam_papers SET updated_at='2020-01-01T00:00:00Z' WHERE id=?", (pid,))
    p = st.heal_paper(pid)
    assert p["status"] == "failed"


def _run_all():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[PASS] {name}")


if __name__ == "__main__":
    _run_all()
    print("mockexam 本地单测全部通过")
