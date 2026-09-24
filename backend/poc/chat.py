# -*- coding: utf-8 -*-
"""会话历史持久化（SQLite 零依赖）。
对应说明书 §4.4 多轮对话：session_id 关联历史，调度层控制轮数上限。
表: sessions(id, session_id, user_id, teacher_id, created_at, last_active_at)
    messages(id, session_id, role, content, created_at)
"""
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import Config

_DEFAULT_DB = Path(os.environ.get("CHAT_DB", "")) if os.environ.get("CHAT_DB") else None


def _db_path() -> Path:
    """默认库位置：CHAT_DB 环境变量 → data/chat.db。"""
    env = os.environ.get("CHAT_DB")
    if env:
        return Path(env)
    return Config.data_dir / "chat.db"


class ChatStore:
    """SQLite 封装的会话存储。每个实例打开独立连接（线程安全：FastAPI 每请求新建）。"""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else _db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL 模式（FastAPI 并发读写防 database is locked）+ busy 等待
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _session(self):
        """带自动 commit/close 的数据库会话（防连接泄漏）。"""
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._session() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL,
                teacher_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_active_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
            """)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ---------- sessions ----------
    def create_session(self, teacher_id: str, user_id: str = "anonymous") -> str:
        """创建会话，返回 session_id（带短随机前缀防碰撞）。"""
        session_id = uuid.uuid4().hex[:16]
        now = self._now()
        with self._session() as conn:
            conn.execute(
                "INSERT INTO sessions(session_id, user_id, teacher_id, created_at, last_active_at) "
                "VALUES(?,?,?,?,?)",
                (session_id, user_id, teacher_id, now, now),
            )
        return session_id

    def get_session(self, session_id: str) -> dict | None:
        with self._session() as conn:
            row = conn.execute(
                "SELECT session_id, user_id, teacher_id, created_at, last_active_at "
                "FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, user_id: str, limit: int = 50) -> list[dict]:
        """某用户的会话列表（含消息数与最后活跃时间，按活跃倒序）。"""
        with self._session() as conn:
            rows = conn.execute(
                "SELECT s.session_id, s.teacher_id, s.created_at, s.last_active_at, "
                "(SELECT COUNT(*) FROM messages m WHERE m.session_id=s.session_id) AS message_count "
                "FROM sessions s WHERE s.user_id=? ORDER BY s.last_active_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- messages ----------
    def add_message(self, session_id: str, role: str, content: str) -> None:
        """追加一条消息；会话不存在抛 ValueError。"""
        if self.get_session(session_id) is None:
            raise ValueError(f"会话不存在: {session_id}")
        now = self._now()
        with self._session() as conn:
            conn.execute(
                "INSERT INTO messages(session_id, role, content, created_at) VALUES(?,?,?,?)",
                (session_id, role, content, now),
            )
            conn.execute(
                "UPDATE sessions SET last_active_at=? WHERE session_id=?",
                (now, session_id),
            )

    def get_messages(self, session_id: str, limit: int | None = None) -> list[dict]:
        """取某会话历史消息，按时间正序。limit=取最近 N 条（仍正序返回）。"""
        with self._session() as conn:
            if limit:
                rows = conn.execute(
                    "SELECT role, content, created_at FROM messages "
                    "WHERE session_id=? ORDER BY id DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
                rows = list(reversed(rows))
            else:
                rows = conn.execute(
                    "SELECT role, content, created_at FROM messages "
                    "WHERE session_id=? ORDER BY id ASC",
                    (session_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, session_id: str) -> None:
        with self._session() as conn:
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))


def build_history_messages(history: list[dict]) -> list[dict]:
    """把 DB 历史消息转成 LLM messages（排除 system，防注入）。"""
    out = []
    for m in history:
        role = m.get("role")
        content = m.get("content", "")
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out
