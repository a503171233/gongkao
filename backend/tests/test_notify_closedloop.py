# -*- coding: utf-8 -*-
"""批次7：通知闭环 + 排行榜公开（清单 #18/#19 缺口补齐）。

运行：cd backend && python tests/test_notify_closedloop.py
覆盖：
  1) 论坛回复通知：回复他人帖子 → 作者收定向站内信；自回复不发；通知写库失败不阻断回复；
  2) 会员到期服务端提醒：≤3 天触发「即将到期」/已过期触发「续费提醒」/充裕不触发/
     30 天窗口去重（重复登录不重复发）；
  3) GET /me/incentive/board：登录可见、前 20 含 username/points/level、
     my_rank 竞赛名次（1 起同分并列）、响应不含 user_id；未登录 401。

隔离纪律：message_store/forum_store/incentive_store 均替换为 TMP 独立实例
（finally 恢复），auth_store 经 AUTH_DB env 隔离——不触碰真实 data/。
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = Path(tempfile.mkdtemp(prefix="gk_notify_loop_"))
os.environ["AUTH_DB"] = str(TMP / "auth.db")
os.environ["CHAT_DB"] = str(TMP / "chat.db")
os.environ["STUDY_DB"] = str(TMP / "study.db")
os.environ["DB_PATH"] = str(TMP / "vec")
os.environ["DATA_DIR"] = str(TMP / "data")
os.environ["MODEL_CACHE"] = str(TMP / "data" / "models")
os.environ["EMBED_PROVIDER"] = "selftest"

ok_count = 0
fail_count = 0


def check(name, cond, detail=""):
    global ok_count, fail_count
    if cond:
        print(f"  ✅ {name}")
        ok_count += 1
    else:
        print(f"  ❌ {name}" + (f"  | {detail}" if detail else ""))
        fail_count += 1


from fastapi.testclient import TestClient  # noqa: E402

from poc import api  # noqa: E402
from poc.forum import ForumStore  # noqa: E402
from poc.incentive import IncentiveStore  # noqa: E402
from poc.messages import MessageStore  # noqa: E402

# ---------- 单例替换（隔离真实 data/） ----------
_orig_msg, _orig_forum, _orig_inc = api.message_store, api.forum_store, api.incentive_store
api.message_store = MessageStore(db_path=TMP / "message.db")
api.forum_store = ForumStore(db_path=TMP / "forum.db")
api.incentive_store = IncentiveStore(db_path=TMP / "incentive.db")

client = TestClient(api.app)

try:
    # 准备用户
    uid_a = api.auth_store.register("楼主小张", "pass1234")
    uid_b = api.auth_store.register("回帖小李", "pass1234")
    uid_c = api.auth_store.register("潜水小王", "pass1234")
    tok_a = api.auth_store.login("楼主小张", "pass1234")
    tok_b = api.auth_store.login("回帖小李", "pass1234")
    tok_c = api.auth_store.login("潜水小王", "pass1234")

    # ==================== 1) 论坛回复通知 ====================
    r = client.post("/forum/posts", headers={"Authorization": f"Bearer {tok_a}"},
                    json={"title": "资料分析提速技巧", "content": "分享我的速算方法"})
    check("发帖成功", r.status_code == 200, r.text[:120])
    post_id = r.json().get("post_id") or r.json().get("id") or ""

    r = client.post(f"/forum/posts/{post_id}/replies",
                    headers={"Authorization": f"Bearer {tok_b}"},
                    json={"content": "收藏了，感谢分享！"})
    check("他人回复成功", r.status_code == 200, r.text[:120])

    inbox_a = api.message_store.list_for_user(uid_a, page=1, size=50)
    notify = [m for m in inbox_a.get("messages", [])
              if m.get("title") == "💬 帖子有新回复"]
    check("作者收到定向回复通知", len(notify) == 1, f"count={len(notify)}")
    check("通知内容含回复者与帖子标题",
          bool(notify) and "回帖小李" in (notify[0]["content"] or "")
          and "资料分析提速技巧" in (notify[0]["content"] or ""),
          (notify[0]["content"] or "")[:60] if notify else "")

    inbox_b = api.message_store.list_for_user(uid_b, page=1, size=50)
    check("回复者本人不收通知",
          not [m for m in inbox_b.get("messages", [])
               if m.get("title") == "💬 帖子有新回复"])

    # 自回复不发（patch 论坛写入限流：本测试只验证通知逻辑，限流另测）
    _orig_write_ok = api._forum_write_ok
    api._forum_write_ok = lambda *a, **k: True
    try:
        r = client.post(f"/forum/posts/{post_id}/replies",
                        headers={"Authorization": f"Bearer {tok_a}"},
                        json={"content": "补充一句：多练习"})
        check("作者自回复成功", r.status_code == 200, r.text[:120])
        inbox_a2 = api.message_store.list_for_user(uid_a, page=1, size=50)
        check("自回复不产生新通知",
              len([m for m in inbox_a2.get("messages", [])
                   if m.get("title") == "💬 帖子有新回复"]) == 1)
    finally:
        api._forum_write_ok = _orig_write_ok

    # 通知写库失败不阻断回复（同样 patch 限流）
    _orig_create = api.message_store.create
    api._forum_write_ok = lambda *a, **k: True
    api.message_store.create = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("db down"))
    try:
        r = client.post(f"/forum/posts/{post_id}/replies",
                        headers={"Authorization": f"Bearer {tok_b}"},
                        json={"content": "通知挂了也能回复"})
        check("通知故障时回复仍 200", r.status_code == 200, r.text[:120])
    finally:
        api.message_store.create = _orig_create
        api._forum_write_ok = _orig_write_ok

    # ==================== 2) 会员到期服务端提醒 ====================
    now = datetime.now()

    def _set_expire(uid: str, dt: datetime):
        conn = sqlite3.connect(os.environ["AUTH_DB"])
        conn.execute("UPDATE users SET member_expire_at=? WHERE user_id=?",
                     (dt.isoformat(), uid))
        conn.commit()
        conn.close()

    # 充裕（10 天后）→ 不发
    _set_expire(uid_c, now + timedelta(days=10))
    r = client.post("/login", json={"username": "潜水小王", "password": "pass1234"})
    check("充裕期登录正常", r.status_code == 200, r.text[:120])
    inbox_c = api.message_store.list_for_user(uid_c, page=1, size=50)
    check("充裕期不触达到期提醒",
          not [m for m in inbox_c.get("messages", []) if "到期" in m.get("title", "")])

    # ≤3 天 → 发「即将到期」
    _set_expire(uid_c, now + timedelta(days=2))
    r = client.post("/login", json={"username": "潜水小王", "password": "pass1234"})
    check("临近到期登录正常", r.status_code == 200, r.text[:120])
    inbox_c = api.message_store.list_for_user(uid_c, page=1, size=50)
    soon = [m for m in inbox_c.get("messages", [])
            if m.get("title") == "⏰ 会员即将到期"]
    check("≤3 天触发「即将到期」提醒", len(soon) == 1, f"count={len(soon)}")

    # 30 天窗口去重：再登录不发第二条
    client.post("/login", json={"username": "潜水小王", "password": "pass1234"})
    inbox_c = api.message_store.list_for_user(uid_c, page=1, size=50)
    check("30 天窗口去重（重复登录不重复发）",
          len([m for m in inbox_c.get("messages", [])
               if m.get("title") == "⏰ 会员即将到期"]) == 1)

    # 已过期 → 发「续费提醒」
    _set_expire(uid_c, now - timedelta(days=1))
    r = client.post("/login", json={"username": "潜水小王", "password": "pass1234"})
    check("过期后登录正常", r.status_code == 200, r.text[:120])
    inbox_c = api.message_store.list_for_user(uid_c, page=1, size=50)
    expired = [m for m in inbox_c.get("messages", [])
               if m.get("title") == "📌 会员已到期"]
    check("已过期触发「续费提醒」", len(expired) == 1, f"count={len(expired)}")

    # ==================== 3) 公开排行榜 ====================
    def _add_points(uid: str, pts: int):
        conn = sqlite3.connect(str(api.incentive_store.db_path))
        conn.execute(
            "INSERT INTO user_points(user_id, points, level, streak_days, "
            "total_answers, correct_count, solved_mistakes, updated_at) "
            "VALUES (?, ?, 1, 0, 0, 0, 0, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET points = points + ?",
            (uid, pts, datetime.now().isoformat(), pts))
        conn.commit()
        conn.close()

    _add_points(uid_b, 120)
    _add_points(uid_a, 60)
    _add_points(uid_c, 30)

    r = client.get("/me/incentive/board",
                   headers={"Authorization": f"Bearer {tok_b}"})
    check("公开榜登录 200", r.status_code == 200, r.text[:150])
    data = r.json()
    board = data.get("board") or []
    check("前 20 含 username/points/level 字段",
          bool(board) and all(
              {"username", "points", "level"} <= set(x) for x in board),
          str(board[:1]))
    check("榜首为积分最高者", bool(board) and board[0]["points"] == 120,
          str(board[:1]))
    check("自己名次 my_rank=1（榜首视角）", data.get("my_rank") == 1,
          data.get("my_rank"))
    check("响应不含 user_id", all("user_id" not in x for x in board))

    r = client.get("/me/incentive/board",
                   headers={"Authorization": f"Bearer {tok_c}"})
    check("第三名视角 my_rank=3", r.json().get("my_rank") == 3,
          r.json().get("my_rank"))

    check("未登录 401", client.get("/me/incentive/board").status_code == 401)

finally:
    api.message_store, api.forum_store, api.incentive_store = _orig_msg, _orig_forum, _orig_inc

# ==================== 汇总 ====================
print(f"\n结果：{ok_count} passed, {fail_count} failed")
sys.exit(1 if fail_count else 0)
