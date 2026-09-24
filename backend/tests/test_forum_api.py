# -*- coding: utf-8 -*-
"""F3 论坛 —— 接口级断言（TestClient + 临时 forum.db）。

运行：cd backend && python tests/test_forum_api.py
"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GOOD = "tok-user-a"
GOOD2 = "tok-user-b"
ADMIN = "tok-admin"


def _main():
    import poc.api as api
    from poc.forum import ForumStore
    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    store = ForumStore(db_path=Path(tempfile.mkdtemp(prefix="gk_forum_")) / "forum.db")

    def fake_resolve(authorization):
        t = (authorization or "").replace("Bearer ", "", 1).strip()
        if t == GOOD:
            return {"user_id": "u-a", "username": "小明", "role": "free"}
        if t == GOOD2:
            return {"user_id": "u-b", "username": "小红", "role": "free"}
        if t == ADMIN:
            return {"user_id": "u-x", "username": "管理员", "role": "admin"}
        raise HTTPException(status_code=401, detail="未登录")

    orig_store = api.forum_store
    orig_resolve = api._resolve_user
    orig_write_ok = api._forum_write_ok
    api.forum_store = store
    api._resolve_user = fake_resolve
    api._forum_write_ok = lambda uid, gap: True
    client = TestClient(api.app)
    try:
        head = {"Authorization": "Bearer " + GOOD}
        head2 = {"Authorization": "Bearer " + GOOD2}
        head_admin = {"Authorization": "Bearer " + ADMIN}

        assert client.get("/forum/categories").status_code == 200

        r = client.get("/forum/posts")
        assert r.status_code == 200 and r.json()["total"] == 0
        assert client.post("/forum/posts", json={
            "title": "匿名发帖", "content": "不该成功"}).status_code == 401

        r = client.post("/forum/posts", json={
            "title": "言语理解备考求助", "category": "备考求助",
            "content": "这道主旨概括题总选错，求大神指点迷津。", "x": 1}, headers=head)
        assert r.status_code == 200, r.text
        p1 = r.json()
        assert p1["title"] == "言语理解备考求助" and p1["category"] == "备考求助"
        assert p1["username"] == "小明" and p1["likes"] == 0

        assert client.post("/forum/posts", json={"title": "短", "content": "x" * 20},
                           headers=head).status_code == 400
        assert client.post("/forum/posts", json={"title": "标题长度合格", "content": "太短"},
                           headers=head).status_code == 400

        time.sleep(1.05)
        r = client.post("/forum/posts", json={
            "title": "申论大作文经验", "category": "申论",
            "content": "申论大作文应该怎么立意才能得高分，欢迎讨论。"}, headers=head)
        assert r.status_code == 200
        p2 = r.json()

        time.sleep(1.05)
        r = client.post("/forum/posts", json={
            "title": "判断推理图形对称技巧", "category": "判断推理",
            "content": "对称性考点整理笔记，全部内容示例。", "x": 1}, headers=head)
        assert r.status_code == 200
        p3 = r.json()

        r = client.get("/forum/posts", params={"sort": "new"})
        assert r.status_code == 200 and r.json()["total"] == 3
        assert r.json()["items"][0]["post_id"] == p3["post_id"]
        assert "excerpt" in r.json()["items"][0]

        assert client.get("/forum/posts", params={"keyword": "申论"}).json()["total"] == 1
        assert client.get("/forum/posts", params={"category": "判断推理"}).json()["total"] == 1

        assert client.post(f"/forum/posts/{p1['post_id']}/like",
                           headers=head2).json() == {"liked": True, "likes": 1}
        assert client.get(f"/forum/posts/{p1['post_id']}", headers=head2).json()["liked"] is True
        assert client.get(f"/forum/posts/{p1['post_id']}").json()["liked"] is False

        r = client.post(f"/forum/posts/{p1['post_id']}/replies",
                        json={"content": "先找主题句再看转折，我一般看首尾。"}, headers=head2)
        assert r.status_code == 200, r.text
        rep1 = r.json()
        r = client.post(f"/forum/posts/{p1['post_id']}/replies",
                        json={"content": "同求！顶一下。"}, headers=head)
        assert r.status_code == 200
        r = client.get(f"/forum/posts/{p1['post_id']}/replies")
        assert r.status_code == 200 and r.json()["total"] == 2
        assert r.json()["items"][0]["reply_id"] == rep1["reply_id"]

        det = client.get(f"/forum/posts/{p1['post_id']}").json()
        assert det["reply_count"] == 2 and det["views"] >= 2

        hot = client.get("/forum/posts", params={"sort": "hot"}).json()
        assert hot["items"][0]["post_id"] == p1["post_id"]

        assert client.delete(f"/forum/posts/{p2['post_id']}", headers=head2).status_code == 403
        assert client.delete(f"/forum/replies/{rep1['reply_id']}",
                             headers=head).status_code == 403
        assert client.delete(f"/forum/replies/{rep1['reply_id']}",
                             headers=head_admin).status_code == 200
        r = client.get(f"/forum/posts/{p1['post_id']}/replies")
        assert r.json()["total"] == 1
        assert client.delete(f"/forum/posts/{p2['post_id']}",
                             headers=head_admin).status_code == 200
        assert client.get(f"/forum/posts/{p2['post_id']}").status_code == 404
        assert client.get(f"/forum/posts/{p2['post_id']}/replies").status_code == 404

        assert client.post("/forum/posts/not-exist-xxxxxxxx/like",
                           headers=head).status_code == 404
        assert client.post("/forum/posts",
                           json={"title": "小红也来发一帖", "content": "登录用户都能发帖交流。"},
                           headers=head2).status_code == 200

        print("[PASS] F3 论坛接口级断言全部通过（发帖/列表/分类/搜索/热度/点赞/回帖/权限/删除）")
    finally:
        api.forum_store = orig_store
        api._resolve_user = orig_resolve
        api._forum_write_ok = orig_write_ok


if __name__ == "__main__":
    _main()
