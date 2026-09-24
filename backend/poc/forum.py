# -*- coding: utf-8 -*-
"""F3 论坛：注册登录用户发帖交流。

独立 forum.db（SQLite 标准库）。公开只读列表/详情；发帖/回帖/点赞/删除需登录。
"""
import os
import sqlite3
import threading
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import Config

FORUM_CATEGORIES = ["言语理解", "判断推理", "数量关系", "资料分析",
                    "常识判断", "申论", "面试", "上岸经验", "备考求助", "闲聊交流"]
PAGE_SIZE_MAX = 50

FORBID_WORDS = ["代考", "枪手", "包过", "替考", "作弊", "内部答案", "改分",
                "卖答案", "论文代写", "刷题外挂"]

_bad_words: list[str] = FORBID_WORDS[:]
_bad_words_loaded = False
_bad_words_lock = threading.Lock()


def _load_bad_words() -> list[str]:
    global _bad_words, _bad_words_loaded
    if _bad_words_loaded:
        return _bad_words
    with _bad_words_lock:
        if _bad_words_loaded:
            return _bad_words
        words = FORBID_WORDS[:]
        extra = Config.data_dir / "forum_badwords.txt"
        try:
            if extra.exists():
                for line in extra.read_text(encoding="utf-8", errors="ignore").splitlines():
                    w = line.strip()
                    if w and not w.startswith("#"):
                        words.append(w)
        except OSError:
            pass
        _bad_words = words
        _bad_words_loaded = True
        return _bad_words


def check_forum_text(title: str, content: str) -> str | None:
    text = f"{title or ''} {content or ''}".lower().replace(" ", "").replace("\t", "")
    for w in _load_bad_words():
        if w.lower() in text:
            return f"内容包含违规词「{w}」，请修改后重试"
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _post_id() -> str:
    return uuid.uuid4().hex[:20]


def _clean_category(raw: str) -> str:
    v = str(raw or "").strip()
    if v in FORUM_CATEGORIES:
        return v
    for c in FORUM_CATEGORIES:
        if c in v:
            return c
    return "闲聊交流"


class ForumStore:
    def __init__(self, db_path: str | Path | None = None):
        default = Config.data_dir / "forum.db"
        self.db_path = Path(db_path or os.environ.get("FORUM_DB") or default)
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
            CREATE TABLE IF NOT EXISTS forum_posts (
                post_id      TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL,
                username     TEXT NOT NULL DEFAULT '',
                category     TEXT NOT NULL DEFAULT '闲聊交流',
                title        TEXT NOT NULL,
                content      TEXT NOT NULL,
                views        INTEGER NOT NULL DEFAULT 0,
                likes        INTEGER NOT NULL DEFAULT 0,
                reply_count  INTEGER NOT NULL DEFAULT 0,
                status       TEXT NOT NULL DEFAULT 'open',
                pinned       INTEGER NOT NULL DEFAULT 0,
                report_count INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_forum_posts_cat ON forum_posts(category, created_at);
            CREATE TABLE IF NOT EXISTS forum_replies (
                reply_id   TEXT PRIMARY KEY,
                post_id    TEXT NOT NULL,
                user_id    TEXT NOT NULL,
                username   TEXT NOT NULL DEFAULT '',
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_forum_replies_post ON forum_replies(post_id, created_at);
            CREATE TABLE IF NOT EXISTS forum_likes (
                post_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                PRIMARY KEY (post_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS forum_analyses (
                post_id      TEXT PRIMARY KEY,
                author_name  TEXT NOT NULL DEFAULT '',
                summary      TEXT NOT NULL DEFAULT '',
                key_points   TEXT NOT NULL DEFAULT '[]',
                advice       TEXT NOT NULL DEFAULT '',
                suggest_cats TEXT NOT NULL DEFAULT '[]',
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS forum_reports (
                report_id  TEXT PRIMARY KEY,
                target_id  TEXT NOT NULL,
                target_kind TEXT NOT NULL DEFAULT 'post',
                user_id    TEXT NOT NULL,
                reason     TEXT NOT NULL DEFAULT '',
                status     TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_forum_reports_status ON forum_reports(status, created_at);
            CREATE TABLE IF NOT EXISTS forum_notice (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                content    TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            """)
            self._migrate_columns(conn)
        if self._get_notice_raw() is None:
            with self._connect() as conn:
                conn.execute("INSERT OR IGNORE INTO forum_notice(id, content, updated_at) "
                             "VALUES(1, '', '')")

    def _migrate_columns(self, conn: sqlite3.Connection) -> None:
        adds = {
            "forum_posts": [
                ("status", "TEXT NOT NULL DEFAULT 'open'"),
                ("pinned", "INTEGER NOT NULL DEFAULT 0"),
                ("report_count", "INTEGER NOT NULL DEFAULT 0"),
            ],
        }
        for table, cols in adds.items():
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, ddl in cols:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def _get_notice_raw(self) -> str | None:
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT content FROM forum_notice WHERE id=1").fetchone()
                return row["content"] if row else None
        except sqlite3.Error:
            return None

    @staticmethod
    def _post_dict(row: sqlite3.Row) -> dict:
        return {
            "post_id": row["post_id"], "user_id": row["user_id"],
            "username": row["username"], "category": row["category"],
            "title": row["title"], "content": row["content"],
            "views": row["views"], "likes": row["likes"],
            "reply_count": row["reply_count"],
            "status": row["status"], "pinned": bool(row["pinned"]),
            "report_count": row["report_count"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    @staticmethod
    def _reply_dict(row: sqlite3.Row) -> dict:
        return {
            "reply_id": row["reply_id"], "post_id": row["post_id"],
            "user_id": row["user_id"], "username": row["username"],
            "content": row["content"], "created_at": row["created_at"],
        }

    def create_post(self, user_id: str, username: str, title: str,
                    content: str, category: str = "") -> dict:
        now = _now_iso()
        post_id = _post_id()
        cat = _clean_category(category)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO forum_posts(post_id,user_id,username,category,title,content,"
                "views,likes,reply_count,status,pinned,report_count,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,0,0,0,'open',0,0,?,?)",
                (post_id, user_id, (username or "").strip()[:40],
                 cat, title, content, now, now))
        return self.get_post(post_id)

    def get_post(self, post_id: str, include_hidden: bool = False) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM forum_posts WHERE post_id=?",
                               (post_id,)).fetchone()
        if row is None:
            return None
        if row["status"] == "hidden" and not include_hidden:
            return None
        return self._post_dict(row)

    def inc_view(self, post_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE forum_posts SET views=views+1 WHERE post_id=?",
                         (post_id,))

    def list_posts(self, category: str = "", sort: str = "new", keyword: str = "",
                   page: int = 1, size: int = 20) -> tuple[list, int]:
        page = max(1, int(page))
        size = min(PAGE_SIZE_MAX, max(1, int(size)))
        where: list[str] = ["status='open'"]
        args: list = []
        cat = _clean_category(category) if category else ""
        if cat:
            where.append("category=?")
            args.append(cat)
        kw = (keyword or "").strip()
        if kw:
            where.append("(title LIKE ? OR content LIKE ? OR username LIKE ?)")
            like = f"%{kw}%"
            args += [like, like, like]
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        order = {
            "hot": "pinned DESC, likes*10 + reply_count*4 + views DESC, created_at DESC",
            "replies": "pinned DESC, reply_count DESC, created_at DESC",
            "new": "pinned DESC, created_at DESC",
        }.get(sort, "pinned DESC, created_at DESC")
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) c FROM forum_posts {wsql}", args).fetchone()["c"]
            rows = conn.execute(
                f"SELECT * FROM forum_posts {wsql} ORDER BY {order} LIMIT ? OFFSET ?",
                args + [size, (page - 1) * size]).fetchall()
        return [self._post_dict(r) for r in rows], total

    def categories(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT category, COUNT(*) c FROM forum_posts WHERE status='open' "
                "GROUP BY category ORDER BY c DESC").fetchall()
        dist = {r["category"]: r["c"] for r in rows}
        return [{"category": c, "count": dist.get(c, 0)} for c in FORUM_CATEGORIES]

    def add_reply(self, post_id: str, user_id: str, username: str,
                  content: str) -> dict:
        post = self.get_post(post_id)
        if post is None:
            raise ValueError("帖子不存在")
        reply_id = uuid.uuid4().hex[:20]
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO forum_replies(reply_id,post_id,user_id,username,content,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (reply_id, post_id, user_id, (username or "").strip()[:40], content, now))
            conn.execute(
                "UPDATE forum_posts SET reply_count=reply_count+1, updated_at=? WHERE post_id=?",
                (now, post_id))
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM forum_replies WHERE reply_id=?",
                               (reply_id,)).fetchone()
        return self._reply_dict(row)

    def list_replies(self, post_id: str, page: int = 1, size: int = 50) -> tuple[list, int]:
        page = max(1, int(page))
        size = min(100, max(1, int(size)))
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) c FROM forum_replies WHERE post_id=?",
                (post_id,)).fetchone()["c"]
            rows = conn.execute(
                "SELECT * FROM forum_replies WHERE post_id=? ORDER BY created_at ASC LIMIT ? OFFSET ?",
                (post_id, size, (page - 1) * size)).fetchall()
        return [self._reply_dict(r) for r in rows], total

    def liked_by(self, post_id: str, user_id: str) -> bool:
        if not user_id:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM forum_likes WHERE post_id=? AND user_id=?",
                (post_id, user_id)).fetchone()
        return row is not None

    def toggle_like(self, post_id: str, user_id: str) -> dict:
        post = self.get_post(post_id)
        if post is None:
            raise ValueError("帖子不存在")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM forum_likes WHERE post_id=? AND user_id=?",
                (post_id, user_id)).fetchone()
            if row:
                conn.execute("DELETE FROM forum_likes WHERE post_id=? AND user_id=?",
                             (post_id, user_id))
                conn.execute("UPDATE forum_posts SET likes=MAX(0,likes-1) WHERE post_id=?",
                             (post_id,))
                liked = False
            else:
                conn.execute("INSERT INTO forum_likes(post_id,user_id) VALUES(?,?)",
                             (post_id, user_id))
                conn.execute("UPDATE forum_posts SET likes=likes+1 WHERE post_id=?",
                             (post_id,))
                liked = True
        p = self.get_post(post_id)
        return {"liked": liked, "likes": p["likes"]}

    def delete_post(self, post_id: str, user_id: str, role: str) -> None:
        post = self.get_post(post_id, include_hidden=True)
        if post is None:
            raise ValueError("帖子不存在")
        if not (post["user_id"] == user_id or role == "admin"):
            raise PermissionError("仅作者或管理员可删除")
        with self._connect() as conn:
            reply_ids = [r["reply_id"] for r in conn.execute(
                "SELECT reply_id FROM forum_replies WHERE post_id=?", (post_id,))]
            conn.execute("DELETE FROM forum_posts WHERE post_id=?", (post_id,))
            conn.execute("DELETE FROM forum_replies WHERE post_id=?", (post_id,))
            conn.execute("DELETE FROM forum_likes WHERE post_id=?", (post_id,))
            conn.execute("DELETE FROM forum_reports WHERE target_kind='post' AND target_id=?",
                         (post_id,))
            marks = ",".join("?" * len(reply_ids))
            if marks:
                conn.execute(
                    f"DELETE FROM forum_reports WHERE target_kind='reply' "
                    f"AND target_id IN ({marks})", reply_ids)

    def delete_reply(self, reply_id: str, user_id: str, role: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM forum_replies WHERE reply_id=?",
                               (reply_id,)).fetchone()
            if row is None:
                raise ValueError("回复不存在")
            if not (row["user_id"] == user_id or role == "admin"):
                raise PermissionError("仅作者或管理员可删除")
            conn.execute("DELETE FROM forum_replies WHERE reply_id=?", (reply_id,))
            conn.execute("DELETE FROM forum_reports WHERE target_kind='reply' AND target_id=?",
                         (reply_id,))
            conn.execute(
                "UPDATE forum_posts SET reply_count=MAX(0,reply_count-1) WHERE post_id=?",
                (row["post_id"],))

    def set_post_state(self, post_id: str, status: str | None = None,
                       pinned: bool | None = None) -> dict:
        post = self.get_post(post_id, include_hidden=True)
        if post is None:
            raise ValueError("帖子不存在")
        updates: list[str] = []
        args: list = []
        if status is not None:
            if status not in ("open", "hidden"):
                raise ValueError("状态不合法")
            updates.append("status=?")
            args.append(status)
        if pinned is not None:
            updates.append("pinned=?")
            args.append(1 if pinned else 0)
        if not updates:
            return post
        updates.append("updated_at=?")
        args.append(_now_iso())
        args.append(post_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE forum_posts SET {', '.join(updates)} WHERE post_id=?", args)
        return self.get_post(post_id, include_hidden=True)

    def mod_posts(self, status: str = "", category: str = "", keyword: str = "",
                  page: int = 1, size: int = 20) -> tuple[list, int]:
        page = max(1, int(page))
        size = min(100, max(1, int(size)))
        where: list[str] = []
        args: list = []
        if status in ("open", "hidden"):
            where.append("status=?")
            args.append(status)
        cat = _clean_category(category) if category else ""
        if cat:
            where.append("category=?")
            args.append(cat)
        kw = (keyword or "").strip()
        if kw:
            where.append("(title LIKE ? OR username LIKE ?)")
            args += [f"%{kw}%", f"%{kw}%"]
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) c FROM forum_posts {wsql}", args).fetchone()["c"]
            rows = conn.execute(
                f"SELECT * FROM forum_posts {wsql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                args + [size, (page - 1) * size]).fetchall()
        return [self._post_dict(r) for r in rows], total

    def add_report(self, target_kind: str, target_id: str, user_id: str,
                   reason: str) -> dict:
        if target_kind not in ("post", "reply"):
            raise ValueError("举报对象不合法")
        if target_kind == "post":
            if self.get_post(target_id, include_hidden=True) is None:
                raise ValueError("帖子不存在")
        else:
            with self._connect() as conn:
                row = conn.execute("SELECT 1 FROM forum_replies WHERE reply_id=?",
                                   (target_id,)).fetchone()
            if row is None:
                raise ValueError("回复不存在")
        with self._connect() as conn:
            dup = conn.execute(
                "SELECT 1 FROM forum_reports WHERE target_kind=? AND target_id=? "
                "AND user_id=? AND status='pending'",
                (target_kind, target_id, user_id)).fetchone()
            if dup:
                raise ValueError("你已举报过，正在处理中")
            conn.execute(
                "INSERT INTO forum_reports(report_id,target_kind,target_id,user_id,"
                "reason,status,created_at) VALUES(?,?,?,?,?, 'pending', ?)",
                (uuid.uuid4().hex[:20], target_kind, target_id, user_id,
                 (reason or "").strip()[:200], _now_iso()))
            if target_kind == "post":
                conn.execute(
                    "UPDATE forum_posts SET report_count=report_count+1, "
                    "updated_at=? WHERE post_id=?", (_now_iso(), target_id))
        return {"ok": True}

    def list_reports(self, status: str = "pending", page: int = 1,
                     size: int = 50) -> tuple[list, int]:
        page = max(1, int(page))
        size = min(100, max(1, int(size)))
        where = "WHERE r.status=?"
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) c FROM forum_reports r {where}", (status,)).fetchone()["c"]
            rows = conn.execute(
                f"SELECT r.report_id, r.target_kind, r.target_id, r.user_id, r.reason, "
                f"r.status, r.created_at, p.title AS post_title, p.username AS post_username, "
                f"rp.username AS reply_username, substr(rp.content,1,80) AS reply_snippet "
                f"FROM forum_reports r "
                f"LEFT JOIN forum_posts p ON r.target_kind='post' AND p.post_id=r.target_id "
                f"LEFT JOIN forum_replies rp ON r.target_kind='reply' AND rp.reply_id=r.target_id "
                f"{where} ORDER BY r.created_at ASC LIMIT ? OFFSET ?",
                (status, size, (page - 1) * size)).fetchall()
        out = []
        for r in rows:
            out.append({
                "report_id": r["report_id"], "target_kind": r["target_kind"],
                "target_id": r["target_id"], "user_id": r["user_id"],
                "reason": r["reason"], "status": r["status"],
                "created_at": r["created_at"], "post_title": r["post_title"],
                "post_username": r["post_username"], "reply_username": r["reply_username"],
                "reply_snippet": r["reply_snippet"],
            })
        return out, total

    def resolve_reports(self, target_kind: str, target_id: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE forum_reports SET status='resolved' WHERE target_kind=? "
                "AND target_id=? AND status='pending'",
                (target_kind, target_id))
            if target_kind == "post":
                conn.execute(
                    "UPDATE forum_posts SET report_count=0, updated_at=? WHERE post_id=?",
                    (_now_iso(), target_id))
        return max(0, cur.rowcount)

    def get_notice(self) -> dict:
        content = self._get_notice_raw() or ""
        with self._connect() as conn:
            row = conn.execute("SELECT updated_at FROM forum_notice WHERE id=1").fetchone()
        return {"content": content, "updated_at": row["updated_at"] if row else ""}

    def set_notice(self, content: str) -> dict:
        text = (content or "").strip()[:300]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO forum_notice(id, content, updated_at) VALUES(1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET content=excluded.content, "
                "updated_at=excluded.updated_at", (text, _now_iso()))
        return self.get_notice()

    def get_analysis(self, post_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM forum_analyses WHERE post_id=?",
                               (post_id,)).fetchone()
        if row is None:
            return None
        return {
            "post_id": row["post_id"], "author_name": row["author_name"],
            "summary": row["summary"], "key_points": self._json_list(row["key_points"]),
            "advice": row["advice"],
            "suggest_categories": self._json_list(row["suggest_cats"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def save_analysis(self, post_id: str, username: str, summary: str,
                      key_points: list, advice: str, suggest_categories: list) -> dict:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO forum_analyses(post_id,author_name,summary,key_points,advice,"
                "suggest_cats,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(post_id) DO UPDATE SET summary=excluded.summary, "
                "key_points=excluded.key_points, advice=excluded.advice, "
                "suggest_cats=excluded.suggest_cats, updated_at=excluded.updated_at",
                (post_id, (username or "").strip()[:40], (summary or "").strip()[:500],
                 json.dumps([str(x)[:160] for x in (key_points or [])][:8], ensure_ascii=False),
                 (advice or "").strip()[:500],
                 json.dumps([str(x)[:30] for x in (suggest_categories or [])][:6],
                            ensure_ascii=False),
                 now, now))
        return self.get_analysis(post_id)

    def analyses_map(self, post_ids: list) -> dict:
        ids = [str(x) for x in (post_ids or []) if str(x)]
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT post_id, summary FROM forum_analyses WHERE post_id IN ({marks})",
                ids).fetchall()
        return {r["post_id"]: r["summary"] for r in rows}

    @staticmethod
    def _json_list(val: str) -> list:
        try:
            v = json.loads(val or "[]")
            return v if isinstance(v, list) else []
        except (ValueError, TypeError):
            return []


_store: ForumStore | None = None
_store_lock = threading.Lock()


def get_forum_store() -> ForumStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ForumStore()
    return _store
