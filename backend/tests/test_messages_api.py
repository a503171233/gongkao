# -*- coding: utf-8 -*-
"""#19 消息中心接口断言：未读/列表/已读/全部已读/管理端群发与统计。

运行：cd backend && python tests/test_messages_api.py
"""
import sys
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import poc.api as api
from poc.messages import MessageStore


def _main():
    store = MessageStore(db_path=Path(tempfile.mkdtemp(prefix="gk_msg_")) / "message.db")

    def fake_resolve(authorization):
        t = (authorization or "").replace("Bearer ", "", 1).strip()
        if t == "tok-admin":
            return {"user_id": "u-x", "username": "管理员", "role": "admin"}
        if t == "tok-b":
            return {"user_id": "u-b", "username": "小刚", "role": "member"}
        if t:
            return {"user_id": "u-a", "username": "小明", "role": "free"}
        return None

    orig_store, orig_resolve = api.message_store, api._resolve_user
    api.message_store = store
    api._resolve_user = fake_resolve
    client = TestClient(api.app)
    try:
        # 未登录访问用户消息接口 → 401；非 admin 访问管理端 → 403
        assert client.get("/messages").status_code == 401
        assert client.get("/messages/unread").status_code == 401
        assert client.get("/admin/messages").status_code == 403
        h_free = {"Authorization": "Bearer tok-free"}
        assert client.get("/admin/messages", headers=h_free).status_code == 403

        # 空数据：未读 0，列表空
        assert client.get("/messages/unread", headers=h_free).json()["count"] == 0
        assert client.get("/messages", headers=h_free).json()["total"] == 0

        h = {"Authorization": "Bearer tok-admin"}

        # 参数校验：空标题/内容 → 400；定向缺 user_id → 400
        assert client.post("/admin/messages", json={
            "title": "", "content": "x", "target": "all"}, headers=h).status_code == 400
        assert client.post("/admin/messages", json={
            "title": "x", "content": "x", "target": "user"}, headers=h).status_code == 400

        # 群发一条全员消息
        r = client.post("/admin/messages", json={
            "title": "平台公告", "content": "本周六 9:00 系统升级维护",
            "target": "all"}, headers=h)
        assert r.status_code == 200, r.text
        m1 = r.json()
        assert m1["id"] and m1["target"] == "all"

        # 定向一条给 user-b
        r = client.post("/admin/messages", json={
            "title": "会员到期提醒", "content": "您的会员将于 3 天后到期",
            "target": "user", "user_id": "u-b"}, headers=h)
        m2 = r.json()
        assert m2["target"] == "user" and m2["target_user_id"] == "u-b"

        # user-a：仅可见群发消息（定向 u-b 的不可见）；未读 1
        d = client.get("/messages", headers=h_free).json()
        assert d["total"] == 1 and d["unread"] == 1
        assert [x["title"] for x in d["messages"]] == ["平台公告"]
        assert d["messages"][0]["read"] == 0

        # user-b：可见 2 条（群发 + 定向）
        h_b = {"Authorization": "Bearer tok-b"}
        assert client.get("/messages/unread", headers=h_b).json()["count"] == 2
        d = client.get("/messages", headers=h_b).json()
        assert d["total"] == 2

        # 单条已读 → 未读递减
        r = client.post("/messages/%d/read" % m1["id"], headers=h_free)
        assert r.json()["ok"] is True
        assert client.get("/messages/unread", headers=h_free).json()["count"] == 0

        # 对不可见消息标记已读 → 404
        assert client.post("/messages/%d/read" % m2["id"], headers=h_free).status_code == 404
        # 对不存在消息 → 404
        assert client.post("/messages/99999/read", headers=h_free).status_code == 404

        # user-b 全部已读
        r = client.post("/messages/read_all", headers=h_b)
        assert r.json()["ok"] is True and r.json()["marked"] == 2
        assert client.get("/messages/unread", headers=h_b).json()["count"] == 0
        # 幂等：再次全部已读 marked=0
        assert client.post("/messages/read_all", headers=h_b).json()["marked"] == 0

        # 管理端列表：统计字段 delivered/read
        lst = client.get("/admin/messages", headers=h).json()["messages"]
        by_id = {x["id"]: x for x in lst}
        assert by_id[m1["id"]]["delivered"] >= 1 and by_id[m1["id"]]["read"] >= 2
        assert by_id[m2["id"]]["delivered"] == 1 and by_id[m2["id"]]["read"] == 1

        # 删除：先删后删 → 404；用户侧不再可见
        assert client.delete("/admin/messages/%d" % m2["id"], headers=h).json()["ok"] is True
        assert client.delete("/admin/messages/%d" % m2["id"], headers=h).status_code == 404
        assert client.get("/messages", headers=h_b).json()["total"] == 1

        print("[PASS] 消息中心接口断言全部通过")
    finally:
        api.message_store = orig_store
        api._resolve_user = orig_resolve


if __name__ == "__main__":
    _main()
