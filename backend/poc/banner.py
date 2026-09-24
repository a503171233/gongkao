# -*- coding: utf-8 -*-
"""首页 Banner 管理（banner.db）：管理端配置轮播图（图片/链接/排序/启停），首页展示。

设计（#23 P1，与 tracking/forum 同构）：
  - 独立 banner.db（SQLite 标准库），表 banners。
  - 公开接口只返回 enabled=1 且按 sort 升序的条目；管理端接口返回全量。
  - 图片/链接仅接受 http(s) 或站内相对路径（防 javascript: 等 XSS 协议）。
"""
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import Config

_SAFE_URL_RE = re.compile(r"^(https?://|/|\.{0,2}/)[A-Za-z0-9_\-./?=&:%+#@!~()*,'\[\]{}|\\$^;]*$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_safe_url(url: str) -> bool:
    """图片/链接安全校验：仅 http(s)/站内相对路径，防 javascript:/data: 注入。"""
    u = (url or "").strip()
    if not u:
        return True
    return bool(_SAFE_URL_RE.match(u))


class BannerStore:
    def __init__(self, db_path: str | Path | None = None):
        default = Config.data_dir / "banner.db"
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
            CREATE TABLE IF NOT EXISTS banners (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL DEFAULT '',
                image      TEXT NOT NULL DEFAULT '',
                link       TEXT NOT NULL DEFAULT '',
                sort       INTEGER NOT NULL DEFAULT 0,
                enabled    INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)

    def list_all(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM banners ORDER BY sort ASC, id ASC").fetchall()
        return [dict(r) for r in rows]

    def list_enabled(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM banners WHERE enabled=1 ORDER BY sort ASC, id ASC").fetchall()
        return [dict(r) for r in rows]

    def create(self, title: str = "", image: str = "", link: str = "",
               sort: int = 0, enabled: int = 1) -> dict:
        now = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO banners(title, image, link, sort, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ((title or "").strip(), (image or "").strip(), (link or "").strip(),
                 int(sort), 1 if enabled else 0, now, now))
            row = conn.execute("SELECT * FROM banners WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def update(self, banner_id: int, **fields) -> dict | None:
        allowed = {"title", "image", "link", "sort", "enabled"}
        sets, args = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            args.append(int(v) if k in ("sort", "enabled") else (v or "").strip())
        if not sets:
            return self.get(banner_id)
        sets.append("updated_at=?")
        args.append(_now_iso())
        args.append(int(banner_id))
        with self._connect() as conn:
            conn.execute(f"UPDATE banners SET {', '.join(sets)} WHERE id=?", args)
            row = conn.execute("SELECT * FROM banners WHERE id=?", (int(banner_id),)).fetchone()
        return dict(row) if row else None

    def delete(self, banner_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM banners WHERE id=?", (int(banner_id),))
        return cur.rowcount > 0

    def reorder(self, ids: list[int]) -> list[dict]:
        """按传入顺序重排 sort（0,1,2…），返回全量列表。"""
        with self._connect() as conn:
            for i, bid in enumerate(ids or []):
                conn.execute("UPDATE banners SET sort=?, updated_at=? WHERE id=?",
                             (i, _now_iso(), int(bid)))
        return self.list_all()


_store: BannerStore | None = None
_store_lock = threading.Lock()


def get_banner_store() -> BannerStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = BannerStore()
    return _store
