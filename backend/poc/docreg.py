# -*- coding: utf-8 -*-
"""C2 · 文档注册表（#24 R5）—— 文档元数据 / 分类 / 上下架权限控制。

背景：文档列表原来从 Chroma 元数据聚合（只有 doc_name/chunks），无分类、
无状态、无法检索过滤。本模块在 study.db 建 doc_registry 持久注册表：

  - register: /upload 入库时登记（大小/字符数/扩展名/分类）
  - set_meta: 后台编辑分类/标签/上下架（enabled=0 → 检索侧过滤该文档）
  - disabled_docs: store.retrieve 后置过滤依据（懒加载，避免模块环）
  - list_meta / categories: 后台分类浏览与检索

权限控制三层（本模块承担第③层）：
  ① 管理端全部接口 admin Bearer 鉴权（api._admin_user，已有）
  ② 文档按老师隔离（Chroma collection + 上传目录，已有）
  ③ 文档级上下架：enabled=0 的文档不参与学员问答检索（store.retrieve 过滤）
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import Config


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DocRegistry:
    """文档元数据注册表（study.db 内独立表，与学员域共库不同表）。"""

    def __init__(self, db_path: str | Path | None = None):
        import os
        default = Config.data_dir / "study.db"
        self.db_path = Path(db_path or os.environ.get("STUDY_DB") or default)
        if not self.db_path.is_absolute():
            self.db_path = Config.ROOT / self.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS doc_registry (
                teacher_id  TEXT NOT NULL,
                doc_name    TEXT NOT NULL,
                category    TEXT DEFAULT '',
                tags        TEXT DEFAULT '',
                enabled     INTEGER DEFAULT 1,
                size_bytes  INTEGER DEFAULT 0,
                chars       INTEGER DEFAULT 0,
                file_ext    TEXT DEFAULT '',
                uploaded_at TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                PRIMARY KEY (teacher_id, doc_name)
            );
            CREATE INDEX IF NOT EXISTS idx_docreg_cat ON doc_registry(teacher_id, category);
            """)

    # ---------- 登记 / 更新 / 删除 ----------

    def register(self, teacher_id: str, doc_name: str, category: str = "",
                 size_bytes: int = 0, chars: int = 0, file_ext: str = "") -> dict:
        """/upload 入库时登记（幂等：同名重传覆盖元数据，保留原分类/标签）。"""
        now = _now_iso()
        with self._connect() as conn:
            old = conn.execute(
                "SELECT category, tags, enabled FROM doc_registry "
                "WHERE teacher_id=? AND doc_name=?", (teacher_id, doc_name)).fetchone()
            if old:
                # 重传：保留人工维护的分类/标签/上下架状态，仅刷新度量
                conn.execute(
                    "UPDATE doc_registry SET size_bytes=?, chars=?, file_ext=?, updated_at=? "
                    "WHERE teacher_id=? AND doc_name=?",
                    (int(size_bytes), int(chars), (file_ext or "").lower()[:10], now,
                     teacher_id, doc_name))
            else:
                conn.execute(
                    "INSERT INTO doc_registry(teacher_id, doc_name, category, tags, enabled, "
                    "size_bytes, chars, file_ext, uploaded_at, updated_at) "
                    "VALUES(?,?,?,?,1,?,?,?,?,?)",
                    (teacher_id, doc_name, (category or "").strip()[:30], "",
                     int(size_bytes), int(chars), (file_ext or "").lower()[:10], now, now))
        return self.get(teacher_id, doc_name) or {}

    def set_meta(self, teacher_id: str, doc_name: str, category: str | None = None,
                 tags: str | None = None, enabled: bool | None = None) -> dict:
        """后台编辑元数据/上下架。上下架变更 → 失效该老师检索缓存。"""
        sets, args = [], []
        if category is not None:
            sets.append("category=?"); args.append((category or "").strip()[:30])
        if tags is not None:
            sets.append("tags=?"); args.append((tags or "").strip()[:100])
        if enabled is not None:
            sets.append("enabled=?"); args.append(1 if enabled else 0)
        if not sets:
            raise ValueError("未提供任何可更新字段")
        sets.append("updated_at=?"); args.append(_now_iso())
        args += [teacher_id, doc_name]
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE doc_registry SET {', '.join(sets)} "
                "WHERE teacher_id=? AND doc_name=?", args)
            if cur.rowcount == 0:
                raise ValueError(f"文档未登记: {doc_name}")
        # 上下架影响检索结果 → 失效该老师检索缓存（懒加载避模块环）
        from . import store
        store.retrieve_cache.invalidate_prefix(f"retr|{teacher_id}|")
        return self.get(teacher_id, doc_name) or {}

    def batch_set_meta(self, teacher_id: str, doc_names: list[str],
                       enabled: bool | None = None,
                       category: str | None = None) -> dict:
        """#25 批量上下架/归类：doc_names 集合内统一改 enabled 或 category。
        仅作用于已登记文档；返回 {applied, missing}。变更上下架 → 失效检索缓存。"""
        names = [n for n in (doc_names or []) if n]
        if not names:
            raise ValueError("doc_names 不能为空")
        if enabled is None and category is None:
            raise ValueError("未提供任何批量操作")
        applied, missing = 0, []
        for n in names:
            try:
                self.set_meta(teacher_id, n, category=category, enabled=enabled)
                applied += 1
            except ValueError:
                missing.append(n)
        return {"applied": applied, "missing": missing}

    def delete(self, teacher_id: str, doc_name: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM doc_registry WHERE teacher_id=? AND doc_name=?",
                (teacher_id, doc_name))
        return cur.rowcount > 0

    # ---------- 查询 ----------

    def get(self, teacher_id: str, doc_name: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM doc_registry WHERE teacher_id=? AND doc_name=?",
                (teacher_id, doc_name)).fetchone()
        return dict(row) if row else None

    def list_meta(self, teacher_id: str, category: str = "",
                  keyword: str = "") -> list[dict]:
        """该老师全部文档元数据（category/keyword 可选过滤）。"""
        sql = "SELECT * FROM doc_registry WHERE teacher_id=?"
        args: list = [teacher_id]
        if category:
            sql += " AND category=?"
            args.append(category.strip())
        if keyword:
            sql += " AND (doc_name LIKE ? OR tags LIKE ? OR category LIKE ?)"
            kw = f"%{keyword.strip()}%"
            args += [kw, kw, kw]
        sql += " ORDER BY uploaded_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def categories(self, teacher_id: str) -> list[dict]:
        """分类浏览聚合：[{category, docs}]。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT category, COUNT(*) n FROM doc_registry "
                "WHERE teacher_id=? GROUP BY category ORDER BY n DESC",
                (teacher_id,)).fetchall()
        return [{"category": r["category"] or "未分类", "docs": r["n"]} for r in rows]

    def disabled_docs(self, teacher_id: str) -> set[str]:
        """已下架文档名集合（store.retrieve 后置过滤用）。空集 = 全部可用。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT doc_name FROM doc_registry "
                "WHERE teacher_id=? AND enabled=0", (teacher_id,)).fetchall()
        return {r["doc_name"] for r in rows}


# ---------- 模块级单例 ----------
_registry: DocRegistry | None = None


def get_doc_registry() -> DocRegistry:
    global _registry
    if _registry is None:
        _registry = DocRegistry()
    return _registry
