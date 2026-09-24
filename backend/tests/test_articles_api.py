# -*- coding: utf-8 -*-
"""F4 文章/经验帖 —— 接口级断言（TestClient + 临时 forum.db + LLM 打桩）。

运行：cd backend && python tests/test_articles_api.py
"""
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _main():
    import poc.api as api
    from poc.forum import ForumStore
    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    store = ForumStore(db_path=Path(tempfile.mkdtemp(prefix="gk_art_")) / "forum.db")
    calls = {"llm": 0}

    def fake_resolve(authorization):
        t = (authorization or "").replace("Bearer ", "", 1).strip()
        if t == "tok-a":
            return {"user_id": "u-a", "username": "小明", "role": "free"}
        if t == "tok-b":
            return {"user_id": "u-b", "username": "小红", "role": "free"}
        raise HTTPException(status_code=401, detail="未登录")

    def fake_grade(messages, tcfg, max_tokens=1500):
        calls["llm"] += 1
        return json.dumps({
            "summary": "帖主强调每天精做一套资料分析并限时复盘。",
            "key_points": ["先提速后提质", "错题当天复盘", "控制单题时长"],
            "advice": "每天留 30 分钟做资料分析专项限时训练。",
            "suggest_categories": ["资料分析"],
        }, ensure_ascii=False)

    class FakeCfg:
        def get_teacher(self, teacher_id):
            return SimpleNamespace(teacher_id=teacher_id, teacher_name="星辰")

    orig_store = api.forum_store
    orig_resolve = api._resolve_user
    orig_grade = api._grade_llm_call
    orig_cfg = api.cfg
    api.forum_store = store
    api._resolve_user = fake_resolve
    api._grade_llm_call = fake_grade
    api.cfg = FakeCfg()
    api._article_cooldown.clear()
    client = TestClient(api.app)
    try:
        head = {"Authorization": "Bearer tok-a"}
        head2 = {"Authorization": "Bearer tok-b"}

        p1 = store.create_post("u-a", "小明", "资料分析提速实战经验",
                               "每天一套资料分析并限时 25 分钟，长期坚持效果显著。", "上岸经验")
        p2 = store.create_post("u-b", "小红", "申论素材怎么积累",
                               "按话题分类整理规范表述并晨读背诵。", "备考求助")
        store.add_reply(p1["post_id"], "u-b", "小红", "感谢分享，已开始每天限时训练。")

        r = client.get("/articles/home")
        assert r.status_code == 200, r.text
        home = r.json()
        assert home["total"] == 2
        first = home["items"][0]
        assert first["analyzed"] is False and first["analysis_summary"] == ""
        assert "excerpt" in first
        assert not any(x.get("content") for x in home["items"])

        assert client.get("/articles/not-exist").status_code == 404
        det = client.get(f"/articles/{p2['post_id']}").json()
        assert det["title"] == "申论素材怎么积累" and det["replies"] == [] and det["analysis"] is None

        assert client.post("/articles/analyze",
                           json={"post_id": p1["post_id"]}).status_code == 401
        assert client.post("/articles/analyze",
                           json={"post_id": "bad-id"}, headers=head).status_code == 404

        r = client.post("/articles/analyze",
                        json={"post_id": p1["post_id"]}, headers=head)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["cached"] is False and calls["llm"] == 1
        a = j["analysis"]
        assert len(a["key_points"]) == 3 and a["summary"].startswith("帖主")
        assert "资料分析" in a["suggest_categories"]

        r = client.post("/articles/analyze",
                        json={"post_id": p1["post_id"]}, headers=head)
        assert r.json()["cached"] is True and calls["llm"] == 1

        det = client.get(f"/articles/{p1['post_id']}").json()
        assert det["analysis"] is not None and len(det["replies"]) == 1
        assert det["analysis"]["key_points"][0] == "先提速后提质"

        r = client.get("/articles/home")
        assert r.json()["items"][0]["post_id"] == p1["post_id"]
        assert r.json()["items"][0]["analyzed"] is True
        assert r.json()["items"][0]["analysis_summary"].startswith("帖主")

        api._article_cooldown["u-b"] = time.time()
        assert client.post("/articles/analyze",
                           json={"post_id": p2["post_id"]}, headers=head2).status_code == 429

        print("[PASS] F4 文章/经验帖接口断言全部通过（热帖精选/详情/AI 提炼缓存与冷却/摘要回写）")
    finally:
        api.forum_store = orig_store
        api._resolve_user = orig_resolve
        api._grade_llm_call = orig_grade
        api.cfg = orig_cfg
        api._article_cooldown.clear()


if __name__ == "__main__":
    _main()
