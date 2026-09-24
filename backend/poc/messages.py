# -*- coding: utf-8 -*-
"""消息中心（message.db）：站内信/系统通知。

设计（#19 P1，与 banner/tracking 同构）：
  - 独立 message.db（SQLite 标准库），表 messages（消息定义）+ message_reads（已读记录）。
  - target=all 全员可见；target=user 定向（仅 target_user_id 可见）。
  - 用户接口返回可见消息 + read 状态；管理端接口返回全量 + 送达/已读统计。
"""
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import Config


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MessageStore:
    def __init__(self, db_path: str | Path | None = None):
        default = Config.data_dir / "message.db"
        self.db_path = Path(db_path or default)
        if not self.db_path.is_absolute():
            self.db_path = Config.ROOT / self.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                title          TEXT NOT NULL DEFAULT '',
                content        TEXT NOT NULL DEFAULT '',
                target         TEXT NOT NULL DEFAULT 'all',
                target_user_id TEXT NOT NULL DEFAULT '',
                created_at     TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS message_reads (
                message_id INTEGER NOT NULL,
                user_id    TEXT NOT NULL,
                read_at    TEXT NOT NULL,
                PRIMARY KEY (message_id, user_id)
            );
            """)

    def create(self, title: str = "", content: str = "",
               target: str = "all", target_user_id: str = "") -> dict:
        """创建消息。target ∈ {all, user}；target=user 时 target_user_id 必填。"""
        now = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO messages(title, content, target, target_user_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ((title or "").strip(), (content or "").strip(),
                 "user" if target == "user" else "all",
                 (target_user_id or "").strip(), now))
            row = conn.execute(
                "SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def get(self, message_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id=?", (int(message_id),)).fetchone()
        return dict(row) if row else None

    def delete(self, message_id: int) -> bool:
        with self._connect() as conn:
            conn.execute("DELETE FROM message_reads WHERE message_id=?",
                         (int(message_id),))
            cur = conn.execute("DELETE FROM messages WHERE id=?",
                               (int(message_id),))
        return cur.rowcount > 0

    def list_all(self) -> list[dict]:
        """管理端全量（含已读人数）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT m.*, (SELECT COUNT(*) FROM message_reads r "
                "WHERE r.message_id = m.id) AS read_count "
                "FROM messages m ORDER BY m.id DESC").fetchall()
        return [dict(r) for r in rows]

    def _visible_sql(self, user_id: str) -> str:
        """可见性条件（target=all 或定向给该用户），防注入。"""
        uid = (user_id or "").replace("'", "''")
        return "(target='all' OR (target='user' AND target_user_id='" + uid + "'))"

    def list_for_user(self, user_id: str, page: int = 1,
                      size: int = 20) -> dict:
        """用户可见消息（倒序）+ read 状态 + 未读总数。"""
        vis = self._visible_sql(user_id)
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) n FROM messages WHERE " + vis).fetchone()["n"]
            rows = conn.execute(
                "SELECT m.id, m.title, m.content, m.target, m.created_at, "
                "(r.read_at IS NOT NULL) AS read "
                "FROM messages m LEFT JOIN message_reads r "
                "ON r.message_id = m.id AND r.user_id=? "
                "WHERE " + vis + " ORDER BY m.id DESC LIMIT ? OFFSET ?",
                (user_id, int(size), (max(0, int(page) - 1)) * int(size))).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["read"] = 1 if d.get("read") else 0
            items.append(d)
        return {"total": total, "messages": items}

    def unread_count(self, user_id: str) -> int:
        vis = self._visible_sql(user_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) n FROM messages m WHERE " + vis +
                " AND NOT EXISTS (SELECT 1 FROM message_reads r "
                "WHERE r.message_id = m.id AND r.user_id=?)",
                (user_id,)).fetchone()
        return row["n"]

    def mark_read(self, user_id: str, message_id: int) -> bool:
        """标记已读；消息不存在或对用户不可见 → False。"""
        m = self.get(message_id)
        if not m:
            return False
        if m["target"] == "user" and m["target_user_id"] != (user_id or ""):
            return False
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO message_reads(message_id, user_id, read_at) "
                "VALUES (?, ?, ?)", (message_id, user_id, _now_iso()))
        return True

    def mark_all_read(self, user_id: str) -> int:
        """全部可见消息标记已读；返回本次新标记条数。"""
        vis = self._visible_sql(user_id)
        marked = 0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM messages WHERE " + vis +
                " AND NOT EXISTS (SELECT 1 FROM message_reads r "
                "WHERE r.message_id = messages.id AND r.user_id=?)",
                (user_id,)).fetchall()
            for r in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO message_reads(message_id, user_id, read_at) "
                    "VALUES (?, ?, ?)", (r["id"], user_id, _now_iso()))
                marked += 1
        return marked


_store: MessageStore | None = None
_store_lock = threading.Lock()


def get_message_store() -> MessageStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = MessageStore()
    return _store
