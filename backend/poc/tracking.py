# -*- coding: utf-8 -*-
"""前台行为埋点（tracking.db）：宣传卡点击等服务端统计。

独立 tracking.db（SQLite 标准库）。事件表通用(event/target/page/ip/ua)，
营销/运营侧按 event 聚合，默认低频写入、只增不改。
"""
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config

PROMO_TARGETS = ("mock", "mind", "forum", "articles")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_prefix(iso_ts: str) -> str:
    return (iso_ts or "")[:10]


class TrackingStore:
    def __init__(self, db_path: str | Path | None = None):
        default = Config.data_dir / "tracking.db"
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
            CREATE TABLE IF NOT EXISTS tracking_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event      TEXT NOT NULL,
                target     TEXT NOT NULL DEFAULT '',
                page       TEXT NOT NULL DEFAULT '',
                ip         TEXT NOT NULL DEFAULT '',
                ua         TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_track_ev ON tracking_events(event, created_at);
            CREATE INDEX IF NOT EXISTS idx_track_target ON tracking_events(event, target);
            """)

    def log(self, event: str, target: str = "", page: str = "",
            ip: str = "", ua: str = "") -> int:
        now = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO tracking_events(event,target,page,ip,ua,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (event[:40], (target or "")[:40], (page or "")[:40],
                 (ip or "")[:64], (ua or "")[:240], now))
            return cur.lastrowid

    def count_since(self, event: str, days: int = 9999) -> int:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) n FROM tracking_events WHERE event=? AND created_at>=?",
                (event, since)).fetchone()
            return row["n"] if row else 0

    def promo_stats(self, days: int = 7) -> dict:
        """宣传入口点击：按 target 聚合 total 与近 days 天，附近 14 天日趋势。"""
        since7 = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        by_target: list[dict] = []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT target, COUNT(*) n FROM tracking_events "
                "WHERE event='promo_click' GROUP BY target "
                "ORDER BY n DESC").fetchall()
            recent = conn.execute(
                "SELECT target, COUNT(*) n FROM tracking_events "
                "WHERE event='promo_click' AND created_at>=? GROUP BY target",
                (since7,)).fetchall()
            recent_map = {r["target"]: r["n"] for r in recent}
            for r in rows:
                by_target.append({
                    "target": r["target"],
                    "total": r["n"],
                    "last7": recent_map.get(r["target"], 0),
                })
            trend_rows = conn.execute(
                "SELECT substr(created_at,1,10) day, COUNT(*) n FROM tracking_events "
                "WHERE event='promo_click' "
                "GROUP BY day ORDER BY day DESC LIMIT 14").fetchall()
            days = []
            for r in reversed(trend_rows):
                days.append({"day": r["day"], "count": r["n"]})
        return {"total": self.count_since("promo_click"),
                "by_target": by_target, "trend": days}


_store_lock = threading.Lock()
_store: TrackingStore | None = None


def get_tracking_store() -> TrackingStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = TrackingStore()
    return _store
