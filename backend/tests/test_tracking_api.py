# -*- coding: utf-8 -*-
"""埋点接口断言：/track/promo 上报/参数校验/频控 + /admin/tracking/promo 越权与聚合。

运行：cd backend && python tests/test_tracking_api.py
"""
import sys
import tempfile
import time
from pathlib import Path
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import poc.api as api
from poc.tracking import TrackingStore


def _main():
    store = TrackingStore(db_path=Path(tempfile.mkdtemp(prefix="gk_track_")) / "tracking.db")

    def fake_resolve(authorization):
        t = (authorization or "").replace("Bearer ", "", 1).strip()
        if t == "tok-admin":
            return {"user_id": "u-x", "username": "管理员", "role": "admin"}
        if t:
            return {"user_id": "u-a", "username": "小明", "role": "free"}
        raise HTTPException(status_code=401, detail="未登录")

    orig_store, orig_resolve = api.tracking_store, api._resolve_user
    api.tracking_store = store
    api._resolve_user = fake_resolve
    api._track_limiter.clear()
    client = TestClient(api.app)
    try:
        for tgt in ("mock", "mind", "forum", "articles"):
            r = client.post("/track/promo", json={"target": tgt, "page": "home"})
            assert r.status_code == 200 and r.json()["ok"] is True, r.text
        assert client.post("/track/promo", json={"target": "badx"}).status_code == 400
        assert client.post("/track/promo", json={}).status_code == 400
        assert client.get("/admin/tracking/promo").status_code == 401
        assert client.get("/admin/tracking/promo",
                          headers={"Authorization": "Bearer tok-free"}).status_code == 403

        api._track_limiter["testclient"] = [time.time()] * api._TRACK_MAX
        api._track_limiter["127.0.0.1"] = [time.time()] * api._TRACK_MAX
        r = client.post("/track/promo", json={"target": "mock"})
        assert r.status_code == 429, r.text
        api._track_limiter.clear()

        stats = client.get("/admin/tracking/promo",
                           headers={"Authorization": "Bearer tok-admin"}).json()
        assert stats["total"] == 4
        by = {x["target"]: x for x in stats["by_target"]}
        assert by["mock"]["total"] == 1 and by["forum"]["total"] == 1
        assert by["mind"]["total"] == 1 and by["articles"]["total"] == 1
        assert stats["trend"] and stats["trend"][-1]["day"] == time.strftime("%Y-%m-%d", time.gmtime())
        print("[PASS] 埋点接口断言全部通过")
    finally:
        api.tracking_store = orig_store
        api._resolve_user = orig_resolve
        api._track_limiter.clear()


if __name__ == "__main__":
    _main()
