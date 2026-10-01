# -*- coding: utf-8 -*-
"""#18 激励体系接口断言：鉴权 / /me/incentive / admin stats / config / recompute / 连续里程碑。

运行：cd backend && python tests/test_incentive_api.py
"""
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import poc.api as api
from poc.study import StudyStore
from poc.incentive import IncentiveStore


def _main():
    tmp = Path(tempfile.mkdtemp(prefix="gk_inc_"))
    study = StudyStore(db_path=tmp / "study.db")
    incentive = IncentiveStore(db_path=tmp / "incentive.db")

    def fake_resolve(authorization):
        t = (authorization or "").replace("Bearer ", "", 1).strip()
        if t == "tok-admin":
            return {"user_id": "u-admin", "username": "管理员", "role": "admin"}
        if t == "tok-a":
            return {"user_id": "u-a", "username": "小明", "role": "free"}
        if t == "tok-b":
            return {"user_id": "u-b", "username": "小刚", "role": "member"}
        return None

    def fake_admin_user(authorization):
        if not authorization:
            raise HTTPException(status_code=401, detail="请先登录")
        user = fake_resolve(authorization)
        if not user:
            raise HTTPException(status_code=401, detail="无效 Token")
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="无权操作")
        return user

    orig = (api.study_store, api.incentive_store, api._resolve_user, api._admin_user)
    api.study_store, api.incentive_store = study, incentive
    api._resolve_user, api._admin_user = fake_resolve, fake_admin_user
    client = TestClient(api.app)
    try:
        h_a = {"Authorization": "Bearer tok-a"}
        h_b = {"Authorization": "Bearer tok-b"}
        h_admin = {"Authorization": "Bearer tok-admin"}
        h_free = {"Authorization": "Bearer tok-free"}

        # ---- 鉴权 ----
        assert client.get("/me/incentive").status_code == 401
        assert client.get("/admin/incentive/stats").status_code == 401
        assert client.get("/admin/incentive/stats", headers=h_free).status_code == 401
        assert client.get("/admin/incentive/stats", headers=h_a).status_code == 403

        # ---- 初始面板：全 0、10 个成就未达成、无流水 ----
        d = client.get("/me/incentive", headers=h_a).json()
        assert d["enabled"] is True
        assert d["points"] == 0 and d["level"] == 1 and d["level_name"] == "青铜学徒"
        assert d["next_level"] == 100 and d["next_level_name"] == "白银学员"
        assert d["streak_days"] == 0
        assert len(d["achievements"]) == 10
        assert all(a["achieved_at"] is None for a in d["achievements"])
        assert d["ledger"] == []

        # ---- 答题记账：错题 answer+daily_first=7；同日再对 answer+correct=5 ----
        r = client.post("/practice/submit", json={
            "question": "Q1", "answer": "x", "score": 0.0, "teacher_id": "T001"}, headers=h_a)
        assert r.status_code == 200
        inc = r.json()["incentive"]
        assert inc["applied"] is True and inc["points_gained"] == 7, inc
        assert [e["event"] for e in inc["events"]] == ["answer", "daily_first"]

        r = client.post("/practice/submit", json={
            "question": "Q2", "answer": "y", "score": 1.0, "teacher_id": "T001"}, headers=h_a)
        inc = r.json()["incentive"]
        assert inc["points_gained"] == 5, inc
        assert [e["event"] for e in inc["events"]] == ["answer", "correct"]

        d = client.get("/me/incentive", headers=h_a).json()
        assert d["points"] == 12
        assert d["streak_days"] == 1 and d["last_active_date"]
        assert [l["event"] for l in d["ledger"]] == ["correct", "answer", "daily_first", "answer"]
        assert sum(l["points"] for l in d["ledger"]) == 12
        by_code = {a["code"]: a for a in d["achievements"]}
        assert by_code["first_practice"]["achieved_at"]
        assert by_code["streak_3"]["achieved_at"] is None
        assert d["stats"]["total"] == 2 and d["stats"]["accuracy"] == 0.5

        # ---- admin 总览 ----
        s = client.get("/admin/incentive/stats", headers=h_admin).json()
        assert s["config"]["enabled"] is True
        assert set(s["config"]["events"]) == {
            "answer", "correct", "daily_first", "review_item",
            "mistake_clear", "streak_3", "streak_7", "streak_30", "checkin"}
        st = s["stats"]
        assert st["users_with_points"] == 1 and st["total_points"] == 12
        assert st["achievements_granted"] == 1
        assert st["top"][0]["user_id"] == "u-a" and st["top"][0]["points"] == 12
        assert s["board"][0]["user_id"] == "u-a" and s["board"][0]["points"] == 12

        # ---- 配置：非法键丢弃 / 合法修改 / 关闭后不记账 ----
        cfg = client.post("/admin/incentive/config", json={
            "enabled": False,
            "event_points": {"answer": 5, "foo": 999}}, headers=h_admin).json()
        assert cfg["enabled"] is False and cfg["events"]["answer"] == 5
        assert "foo" not in cfg["events"] and cfg["events"]["correct"] == 3

        r = client.post("/practice/submit", json={
            "question": "Q3", "answer": "z", "score": 1.0, "teacher_id": "T001"}, headers=h_a)
        assert r.json()["incentive"]["applied"] is False
        assert client.get("/me/incentive", headers=h_a).json()["points"] == 12

        cfg = client.post("/admin/incentive/config", json={
            "enabled": True, "event_points": {"answer": 2}}, headers=h_admin).json()
        assert cfg["enabled"] is True and cfg["events"]["answer"] == 2

        # ---- 重算：3 条日志按时间序重放 → 7+5+5=17（幂等）----
        r = client.post("/admin/incentive/recompute", json={"user_id": ""}, headers=h_admin)
        assert r.status_code == 200
        assert r.json()["recomputed"] >= 1
        res = {x["user_id"]: x for x in r.json()["results"]}
        assert res["u-a"]["points"] == 17 and res["u-a"]["events"] == 3, res

        r = client.post("/admin/incentive/recompute", json={"user_id": "u-a"}, headers=h_admin)
        assert r.json()["recomputed"] == 1 and r.json()["results"][0]["user_id"] == "u-a"

        # ---- 连续 3 天里程碑（store 级：直接喂不同日期日志，验证明细不重发）----
        base = date(2026, 9, 1)
        for i in range(3):
            day = (base + timedelta(days=i)).isoformat() + "T10:00:00"
            r = incentive.record_practice(
                "u-b", {"created_at": day, "score": 1.0, "question_id": None}, study)
            assert r["applied"] is True and r["streak_days"] == i + 1, r

        d = incentive.summary("u-b", study)
        assert d["streak_days"] == 3
        assert "streak_3" in [l["event"] for l in d["ledger"]]
        assert sum(l["points"] for l in d["ledger"] if l["event"] == "streak_3") == 10
        by_code = {a["code"]: a for a in d["achievements"]}
        assert by_code["streak_3"]["achieved_at"]

        r = incentive.record_practice(
            "u-b", {"created_at": (base + timedelta(days=2)).isoformat() + "T12:00:00",
                    "score": 1.0, "question_id": None}, study)
        assert all(e["event"] != "streak_3" for e in r["events"]), r["events"]

        # 把 u-b 的 3 条跨天日志写入 study.practice_logs（供全量重放），
        # 验证重算时按 created_at 忠实重建连续/里程碑且幂等。
        with study._connect() as conn:
            for i in range(3):
                conn.execute(
                    "INSERT INTO practice_logs(user_id, teacher_id, question, answer, "
                    "score, feedback, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("u-b", "T001", f"BQ{i+1}", "ok", 1.0, "",
                     (base + timedelta(days=i)).isoformat() + "T10:00:00"))

        logs = study.all_practice_logs()
        ub_logs = [x for x in logs if x["user_id"] == "u-b"]
        assert len(ub_logs) == 3
        res = incentive.recompute_user("u-b", ub_logs, study)
        # 每天 answer2+correct3+daily_first5=10；第 3 天另加 streak_3=10 → 共 40
        assert res["points"] == 40 and res["events"] == 3, res
        d = incentive.summary("u-b", study)
        assert "streak_3" in [l["event"] for l in d["ledger"]]
        assert sum(l["points"] for l in d["ledger"] if l["event"] == "streak_3") == 10
        assert {a["code"]: a for a in d["achievements"]}["streak_3"]["achieved_at"]
        res2 = incentive.recompute_user("u-b", ub_logs, study)
        assert res2["points"] == 40 and res2["events"] == 3, res2

        # u-b 无练习日志时重算 → 归零
        res = incentive.recompute_user("u-b", [], study)
        assert res["points"] == 0 and res["events"] == 0

        print("[PASS] 激励体系接口断言全部通过")
    finally:
        api.study_store, api.incentive_store, api._resolve_user, api._admin_user = orig


if __name__ == "__main__":
    _main()
