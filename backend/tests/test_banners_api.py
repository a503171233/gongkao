# -*- coding: utf-8 -*-
"""#23 Banner 管理接口断言：公开轮播/管理端 CRUD/安全 URL 校验/排序。

运行：cd backend && python tests/test_banners_api.py
"""
import sys
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import poc.api as api
from poc.banner import BannerStore, is_safe_url


def _main():
    store = BannerStore(db_path=Path(tempfile.mkdtemp(prefix="gk_banner_")) / "banner.db")

    def fake_resolve(authorization):
        t = (authorization or "").replace("Bearer ", "", 1).strip()
        if t == "tok-admin":
            return {"user_id": "u-x", "username": "管理员", "role": "admin"}
        if t:
            return {"user_id": "u-a", "username": "小明", "role": "free"}
        return None

    orig_store, orig_resolve = api.banner_store, api._resolve_user
    api.banner_store = store
    api._resolve_user = fake_resolve
    client = TestClient(api.app)
    try:
        # 公开：空列表
        r = client.get("/banners")
        assert r.status_code == 200 and r.json()["banners"] == [], r.text

        # 越权：未登录/非管理员访问管理端 → 403（与真实 _resolve_user(None)+_forum_require_admin 一致）
        assert client.get("/admin/banners").status_code == 403
        assert client.get("/admin/banners",
                          headers={"Authorization": "Bearer tok-free"}).status_code == 403

        # 安全 URL 校验：javascript: 协议拒绝
        assert is_safe_url("javascript:alert(1)") is False
        assert is_safe_url("data:text/html,x") is False
        assert is_safe_url("https://img.example.com/a.png") is True
        assert is_safe_url("/api/qimg/T001/abc.png") is True

        h = {"Authorization": "Bearer tok-admin"}

        # 新增 2 条
        r = client.post("/admin/banners", json={
            "title": "暑期特训营", "image": "https://img.example.com/b1.png",
            "link": "/chat.html", "sort": 1, "enabled": 1}, headers=h)
        assert r.status_code == 200, r.text
        b1 = r.json()
        assert b1["id"] and b1["sort"] == 1 and b1["enabled"] == 1

        r = client.post("/admin/banners", json={
            "title": "真题精讲", "image": "/api/qimg/T001/aaaa.png",
            "link": "https://example.com/course", "sort": 0, "enabled": 1}, headers=h)
        b2 = r.json()

        # 新增 javascript: 协议 → 400
        assert client.post("/admin/banners", json={
            "title": "bad", "image": "javascript:alert(1)", "link": "/"}, headers=h).status_code == 400
        assert client.post("/admin/banners", json={
            "title": "bad", "image": "/a.png", "link": "javascript:alert(2)"}, headers=h).status_code == 400

        # 公开接口按 sort 升序，且只含启用项
        r = client.get("/banners").json()["banners"]
        assert [x["title"] for x in r] == ["真题精讲", "暑期特训营"], r

        # 停用后公开接口不再返回
        client.put(f"/admin/banners/{b1['id']}", json={
            "title": "暑期特训营", "image": "https://img.example.com/b1.png",
            "link": "/chat.html", "sort": 1, "enabled": 0}, headers=h)
        r = client.get("/banners").json()["banners"]
        assert [x["title"] for x in r] == ["真题精讲"], r
        # 管理端全量仍含停用项
        assert len(client.get("/admin/banners", headers=h).json()["banners"]) == 2

        # 重排
        r = client.post("/admin/banners/reorder", json={"ids": [b2["id"], b1["id"]]}, headers=h)
        assert [x["id"] for x in r.json()["banners"]] == [b2["id"], b1["id"]]
        assert client.post("/admin/banners/reorder", json={"ids": ["x"]}, headers=h).status_code == 400

        # 删除
        assert client.delete(f"/admin/banners/{b1['id']}", headers=h).json()["ok"] is True
        assert client.delete(f"/admin/banners/{b1['id']}", headers=h).status_code == 404
        assert len(client.get("/admin/banners", headers=h).json()["banners"]) == 1

        print("[PASS] Banner 接口断言全部通过")
    finally:
        api.banner_store = orig_store
        api._resolve_user = orig_resolve


if __name__ == "__main__":
    _main()
