# -*- coding: utf-8 -*-
"""F1 模考解析 —— 接口级断言（FastAPI TestClient + 打桩任务引擎）。

运行：cd backend && python tests/test_mockexam_api.py
"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _patch():
    from poc import admin
    from poc import mockexam
    import poc.api as api
    from fastapi.testclient import TestClient

    store = mockexam.MockExamStore(db_path=Path(tempfile.mkdtemp(prefix="gk_meapi_")) / "mockexam.db")

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

    def fake_extract(text):
        return {"questions": [
            {"qtype": "choice", "question": "1. 下列成语使用恰当的是？", "options": ["A. 甲", "B. 乙"],
             "answer": "A", "analysis": "", "difficulty": 1, "knowledge_point": "成语", "category": "言语理解"},
            {"qtype": "judge", "question": "2. 图形对称性判断？", "options": [], "answer": "对",
             "analysis": "", "difficulty": 1, "knowledge_point": "图形推理", "category": "判断推理"},
        ], "n_expected": 2, "warnings": []}

    def fake_answer(q):
        return {"answer": "A", "analysis": "接口打桩解析：" + (q.get("category") or "")}

    orig = {
        "api_mock": api.mockexam_store,
        "resolve": api._resolve_user,
        "admin_extract": admin.extract_questions_ai,
        "answer": mockexam._answer_one,
        "cfg": mockexam.Config,
        "singleton": mockexam._store,
    }
    api.mockexam_store = store
    api._resolve_user = lambda authorization: {"user_id": "u-api-test", "username": "tester", "role": "free"}
    admin.extract_questions_ai = fake_extract
    mockexam._answer_one = fake_answer
    mockexam.Config = FakeCfg
    mockexam._store = store
    return TestClient(api.app), store, orig, api


def _main():
    client, store, orig, api = _patch()
    try:
        r = client.post("/mockexam/papers/from-text", json={
            "title": "行测接口测试卷", "exam_type": "行测", "text": "整卷内容：" + "模拟试卷题干" * 6})
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        assert r.json()["status"] in ("pending", "extracting", "answering", "ready"), r.json()

        r = client.get("/mockexam/papers")
        assert r.status_code == 200 and r.json()["total"] >= 1
        assert any(p["id"] == pid for p in r.json()["papers"])

        r = client.get(f"/mockexam/papers/{pid}")
        assert r.status_code == 200

        t0 = time.time()
        while time.time() - t0 < 10:
            r = client.get(f"/mockexam/papers/{pid}")
            p = r.json()
            if p["status"] in ("ready", "failed"):
                break
            time.sleep(0.1)
        assert p["status"] == "ready", p
        assert p["done_questions"] == 2 and p["total_questions"] == 2
        assert p["summary"]["teachers"]["星辰老师"]["total"] == 1
        assert p["summary"]["teachers"]["云舟老师"]["total"] == 1

        r = client.get(f"/mockexam/papers/{pid}/questions")
        assert r.status_code == 200
        qs = r.json()["questions"]
        assert len(qs) == 2
        assert qs[0]["teacher_id"] == "T001" and qs[0]["answer"] == "A"
        assert qs[1]["teacher_id"] == "T002" and qs[1]["status"] == "ok"

        r = client.delete(f"/mockexam/papers/{pid}")
        assert r.status_code == 200
        assert client.get(f"/mockexam/papers/{pid}").status_code == 404

        r = client.post("/mockexam/papers/from-text",
                        json={"text": "abc"})
        assert r.status_code == 422
        r = client.post("/mockexam/papers/from-text",
                        json={"title": "", "exam_type": "编程", "text": "x" * 40})
        assert r.status_code == 400

        print("[PASS] mockexam 接口级断言全部通过（创建/轮询/拆题/按老师路由/逐题解析/删除/校验）")
    finally:
        api._resolve_user = orig["resolve"]
        api.mockexam_store = orig["api_mock"]
        import poc.admin as _admin
        _admin.extract_questions_ai = orig["admin_extract"]
        import poc.mockexam as _me
        _me._answer_one = orig["answer"]
        _me.Config = orig["cfg"]
        _me._store = orig["singleton"]


if __name__ == "__main__":
    _main()
