# -*- coding: utf-8 -*-
"""#20 学习报告接口断言：鉴权 / 空态 / 模考成绩趋势 / 学情周报 / 复习与库存。

运行：cd backend && python tests/test_learning_report.py
"""
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import poc.api as api
from poc.study import StudyStore
from poc.smartexam import SmartExamStore


def _main():
    tmp = Path(tempfile.mkdtemp(prefix="gk_lr_"))
    study = StudyStore(db_path=tmp / "study.db")
    smartexam = SmartExamStore(db_path=tmp / "smartexam.db")

    def fake_resolve(authorization):
        t = (authorization or "").replace("Bearer ", "", 1).strip()
        if t == "tok-a":
            return {"user_id": "u-a", "username": "小明", "role": "free"}
        if t == "tok-admin":
            return {"user_id": "u-admin", "username": "管理员", "role": "admin"}
        return None

    orig = (api.study_store, api.smartexam_store, api._resolve_user)
    api.study_store, api.smartexam_store = study, smartexam
    api._resolve_user = fake_resolve
    client = TestClient(api.app)
    try:
        h_a = {"Authorization": "Bearer tok-a"}
        h_admin = {"Authorization": "Bearer tok-admin"}

        # ---- 鉴权 ----
        assert client.get("/me/learning-report").status_code == 401
        assert client.get("/me/learning-report",
                          headers={"Authorization": "Bearer tok-bad"}).status_code == 401

        # ---- 空态：全 0 / 空列表 / 无趋势 ----
        d = client.get("/me/learning-report", headers=h_a).json()
        assert d["exam"]["trend"] == []
        assert d["exam"]["summary"]["papers"] == 0
        assert d["exam"]["summary"]["avg_score"] is None
        assert d["weekly"]["graded"] == 0 and d["weekly"]["accuracy"] is None
        assert d["weekly"]["recent_days"] == [] and d["weekly"]["by_category"] == []
        assert d["weekly"]["weak"] == [] and d["weekly"]["essay"]["pending"] == 0
        assert d["review"]["due"] == 0 and d["review"]["in_queue"] == 0
        assert d["review"]["mastered"] == 0 and d["review"]["next_review_at"] is None
        assert d["inventory"]["favorites"] == 0 and d["inventory"]["mistakes"] == 0

        # ---- 学情数据：跨 2 天练习日志（含分类/主观题）+ 收藏 + 错题 + 复习排程 ----
        with study._connect() as conn:
            rows = [
                ("u-a", "T001", "Q1", "A", 1.0, "对", "2026-09-06T09:00:00", "行测", None),
                ("u-a", "T001", "Q2", "B", 0.0, "错", "2026-09-06T10:00:00", "行测", None),
                ("u-a", "T001", "Q3", "C", 1.0, "对", "2026-09-07T11:00:00", "申论", None),
                ("u-a", "T001", "Q4", "答", -1.0, "待批", "2026-09-07T12:00:00", "申论", None),
            ]
            for uid, tid, q, a, sc, fb, ts, cat, _ in rows:
                conn.execute(
                    "INSERT INTO practice_logs(user_id, teacher_id, question, answer, "
                    "score, feedback, created_at, category) VALUES (?,?,?,?,?,?,?,?)",
                    (uid, tid, q, a, sc, fb, ts, cat))
            conn.execute(
                "INSERT INTO favorites(user_id, teacher_id, question, answer, created_at) "
                "VALUES ('u-a','T001','F1','a','2026-09-07T09:00:00')")
            conn.execute(
                "INSERT INTO favorites(user_id, teacher_id, question, answer, created_at) "
                "VALUES ('u-a','T001','F2','b','2026-09-07T10:00:00')")
            conn.execute(
                "INSERT INTO mistakes(user_id, teacher_id, question, correct_note, created_at) "
                "VALUES ('u-a','T001','M1','n1','2026-09-06T10:00:00')")
            conn.execute(
                "INSERT INTO mistakes(user_id, teacher_id, question, correct_note, created_at) "
                "VALUES ('u-a','T001','M2','n2','2026-09-07T09:00:00')")
            conn.execute(
                "INSERT INTO mistakes(user_id, teacher_id, question, correct_note, created_at) "
                "VALUES ('u-a','T001','M3','n3','2026-09-07T10:00:00')")
            conn.execute(
                "INSERT INTO review_schedule(user_id, question_id, stage, next_review_at, updated_at) "
                "VALUES ('u-a', 100, 0, '2026-09-08T00:00:00', '2026-09-07T09:00:00')")
            conn.execute(
                "INSERT INTO review_schedule(user_id, question_id, stage, next_review_at, updated_at) "
                "VALUES ('u-a', 101, 1, '2026-09-09T00:00:00', '2026-09-07T09:00:00')")

        # ---- 模考成绩：跨 2 天 3 张已评分卷（score 0-1） ----
        with smartexam._connect() as conn:
            for qid, (day, score) in enumerate([
                ("2026-09-06T09:00:00", 0.6),
                ("2026-09-07T09:00:00", 0.8),
                ("2026-09-07T10:00:00", 0.9),
            ]):
                conn.execute(
                    "INSERT INTO smartexam_papers(user_id, title, teacher_id, config, questions, "
                    "answers, status, total, correct, score, created_at, updated_at) "
                    "VALUES ('u-a', ?, 'T001', '{}', '[]', '{}', 'graded', 10, 8, ?, ?, ?)",
                    (f"卷{qid+1}", score, day, day))

        d = client.get("/me/learning-report", headers=h_a).json()

        # ---- 模考成绩趋势 ----
        tr = d["exam"]["trend"]
        assert [x["d"] for x in tr] == ["2026-09-06", "2026-09-07"]
        assert tr[0]["papers"] == 1 and tr[0]["avg_score"] == 0.6 and tr[0]["best_score"] == 0.6
        assert tr[1]["papers"] == 2 and tr[1]["avg_score"] == 0.85 and tr[1]["best_score"] == 0.9
        s = d["exam"]["summary"]
        assert s["papers"] == 3 and s["avg_score"] == 0.7667 and s["best_score"] == 0.9

        # ---- 学情周报 ----
        w = d["weekly"]
        assert w["graded"] == 3 and w["correct"] == 2
        assert round(w["accuracy"], 3) == 0.667
        assert [x["d"] for x in w["recent_days"]] == ["2026-09-06", "2026-09-07"]
        # 趋势仅含已评分题（score>=0）：09-06 两题 1 对，09-07 一题 1 对（主观题 -1 不计）
        assert w["recent_days"][0]["n"] == 2 and w["recent_days"][0]["ok"] == 1
        assert w["recent_days"][1]["n"] == 1 and w["recent_days"][1]["ok"] == 1
        cats = {c["category"]: c for c in w["by_category"]}
        assert cats["行测"]["total"] == 2 and cats["行测"]["correct"] == 1
        assert cats["申论"]["total"] == 1 and cats["申论"]["correct"] == 1
        assert w["essay"]["pending"] == 1
        assert w["weak"] == []  # 薄弱需 ≥3 题且 <60%，行测仅 2 题不触发

        # ---- 复习与库存 ----
        rv = d["review"]
        assert rv["due"] == 1 and rv["in_queue"] == 2 and rv["mastered"] == 0
        assert rv["next_review_at"] == "2026-09-09T00:00:00"
        assert d["inventory"]["favorites"] == 2 and d["inventory"]["mistakes"] == 3

        # ---- 隔离性：admin 无任何数据 ----
        d2 = client.get("/me/learning-report", headers=h_admin).json()
        assert d2["exam"]["trend"] == [] and d2["weekly"]["graded"] == 0
        assert d2["inventory"]["favorites"] == 0 and d2["inventory"]["mistakes"] == 0

        print("[PASS] 学习报告接口断言全部通过")
    finally:
        api.study_store, api.smartexam_store, api._resolve_user = orig


if __name__ == "__main__":
    _main()
