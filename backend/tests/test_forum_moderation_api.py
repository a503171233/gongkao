# -*- coding: utf-8 -*-
"""F3 论坛治理 —— 举报/隐藏/置顶/公告/频控/违禁词 接口断言。

运行：cd backend && python tests/test_forum_moderation_api.py
"""
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOK_A = "tok-user-a"
TOK_B = "tok-user-b"
TOK_C = "tok-user-c"
TOK_ADMIN = "tok-admin"


def _main():
    import poc.api as api
    from poc.forum import ForumStore
    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    store = ForumStore(db_path=Path(tempfile.mkdtemp(prefix="gk_forumm_")) / "forum.db")

    def fake_resolve(authorization):
        t = (authorization or "").replace("Bearer ", "", 1).strip()
        mapping = {
            TOK_A: {"user_id": "u-a", "username": "小明", "role": "free"},
            TOK_B: {"user_id": "u-b", "username": "小红", "role": "free"},
            TOK_C: {"user_id": "u-c", "username": "小刚", "role": "free"},
            TOK_ADMIN: {"user_id": "u-x", "username": "管理员", "role": "admin"},
        }
        if t in mapping:
            return mapping[t]
        raise HTTPException(status_code=401, detail="未登录")

    orig_store = api.forum_store
    orig_resolve = api._resolve_user
    orig_write_ok = api._forum_write_ok
    api.forum_store = store
    api._resolve_user = fake_resolve
    api._forum_write_ok = lambda uid, gap: True
    api._forum_write_cooldown.clear()
    client = TestClient(api.app)
    try:
        ha = {"Authorization": "Bearer " + TOK_A}
        hb = {"Authorization": "Bearer " + TOK_B}
        hc = {"Authorization": "Bearer " + TOK_C}
        hadm = {"Authorization": "Bearer " + TOK_ADMIN}

        assert client.get("/public/forum/notice").status_code == 200

        r = client.post("/forum/posts", json={
            "title": "违规测试", "content": "这里宣传代考服务，一律禁止。"}, headers=ha)
        assert r.status_code == 400 and "违规词" in r.json()["detail"]

        r = client.post("/forum/posts", json={
            "title": "资料分析提速经验", "category": "上岸经验",
            "content": "限时训练 + 错题归类，两周见效。"}, headers=ha)
        assert r.status_code == 200, r.text
        p1 = r.json()

        time.sleep(0.1)
        r = client.post("/forum/posts", json={
            "title": "申论素材积累方法", "category": "申论",
            "content": "按话题整理规范表述并晨读。"}, headers=hb)
        assert r.status_code == 200
        p2 = r.json()

        def real_write_ok(uid, gap):
            last = api._forum_write_cooldown.get(uid, 0.0)
            if time.time() - last < gap:
                return False
            api._forum_write_cooldown[uid] = time.time()
            return True

        api._forum_write_ok = real_write_ok
        api._forum_write_cooldown.clear()
        api._forum_write_cooldown["u-a"] = time.time()
        r = client.post("/forum/posts", json={
            "title": "连发测试", "content": "刚发过再发应被频控拦截。"}, headers=ha)
        assert r.status_code == 429, r.text
        api._forum_write_cooldown.clear()
        api._forum_write_ok = lambda uid, gap: True

        r = client.post("/forum/reports", json={
            "target_kind": "post", "target_id": p2["post_id"],
            "reason": "疑似广告"}, headers=ha)
        assert r.status_code == 200, r.text
        assert client.post("/forum/reports", json={
            "target_kind": "post", "target_id": p2["post_id"],
            "reason": "重复举报"}, headers=ha).status_code == 404

        r = client.post("/forum/reports", json={
            "target_kind": "reply", "target_id": "no-such-reply",
            "reason": "test"}, headers=ha)
        assert r.status_code == 404
        assert client.post("/forum/reports", json={
            "target_kind": "post", "target_id": p2["post_id"],
            "reason": "第二条用户举报"}, headers=hb).status_code == 200
        assert client.post("/forum/reports", json={
            "target_kind": "post", "target_id": p2["post_id"],
            "reason": "匿名不行"}, ).status_code == 401

        assert store.get_post(p2["post_id"])["report_count"] == 2

        assert client.get("/forum/mod/reports", headers=ha).status_code == 403
        r = client.get("/forum/mod/reports", headers=hadm)
        assert r.status_code == 200, r.text
        mod = r.json()
        assert mod["total"] == 2 and mod["items"][0]["post_title"] == "申论素材积累方法"
        assert mod["items"][0]["reason"] == "疑似广告"

        assert client.get("/forum/mod/posts", headers=ha).status_code == 403
        assert client.post(f"/forum/mod/posts/{p2['post_id']}/state",
                           json={"status": "hidden"}, headers=hadm).status_code == 200
        assert store.get_post(p2["post_id"], include_hidden=True)["status"] == "hidden"
        assert store.get_post(p2["post_id"]) is None
        assert client.get(f"/forum/posts/{p2['post_id']}").status_code == 404
        assert client.get(f"/forum/posts/{p2['post_id']}/replies").status_code == 404
        assert client.get("/forum/categories").json()["categories"] is not None
        vis = client.get("/forum/posts", params={"keyword": "申论素材"}).json()
        assert vis["total"] == 0

        r = client.post(f"/forum/mod/posts/{p2['post_id']}/state",
                        json={"status": "open"}, headers=hadm)
        assert r.status_code == 200 and r.json()["status"] == "open"

        assert client.post(f"/forum/mod/posts/{p1['post_id']}/pin",
                           json={"pinned": True}, headers=hadm).status_code == 200
        assert store.get_post(p1["post_id"])["pinned"] is True
        new_list = client.get("/forum/posts", params={"sort": "new"}).json()
        assert new_list["items"][0]["post_id"] == p1["post_id"]
        assert client.post(f"/forum/mod/posts/{p1['post_id']}/pin",
                           json={"pinned": True}, headers=ha).status_code == 403
        assert client.post(f"/forum/mod/posts/{p1['post_id']}/pin",
                           json={"pinned": False}, headers=hadm).status_code == 200

        assert client.get("/forum/mod/reports", headers=hadm).json()["total"] == 0
        assert store.get_post(p2["post_id"])["report_count"] == 0

        assert client.post("/forum/reports", json={
            "target_kind": "post", "target_id": p2["post_id"],
            "reason": "隐藏后仍存在的举报"}, headers=hc).status_code == 200
        assert client.post("/forum/mod/reports/post/" + p2["post_id"] + "/resolve",
                           headers=hadm).json()["resolved"] == 1
        assert client.get("/forum/mod/reports", headers=hadm).json()["total"] == 0

        assert client.post("/forum/mod/notice",
                           json={"content": "欢迎交流，文明发言。"}, headers=ha).status_code == 403
        assert client.post("/forum/mod/notice",
                           json={"content": "欢迎交流，文明发言。"}, headers=hadm).status_code == 200
        notice = client.get("/public/forum/notice").json()
        assert notice["content"] == "欢迎交流，文明发言。"

        assert client.post(f"/forum/mod/posts/{p2['post_id']}/state",
                           json={"status": "hidden"}, headers=hadm).status_code == 200
        r = client.get("/forum/mod/posts", params={"status": "hidden"}, headers=hadm)
        assert r.json()["total"] == 1 and r.json()["items"][0]["post_id"] == p2["post_id"]
        assert client.post(f"/forum/mod/posts/{p2['post_id']}/state",
                           json={"status": "open"}, headers=hadm).status_code == 200

        print("[PASS] F3 论坛治理断言全部通过（违禁词/频控/举报/隐藏/置顶/公告/越权/解析）")
    finally:
        api.forum_store = orig_store
        api._resolve_user = orig_resolve
        api._forum_write_ok = orig_write_ok
        api._forum_write_cooldown.clear()


if __name__ == "__main__":
    _main()
