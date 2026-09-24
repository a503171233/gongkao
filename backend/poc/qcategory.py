# -*- coding: utf-8 -*-
"""题库分类管理（独立 question_category 表，管理端维护）。
与知识体系（knowledge_nodes）解耦：分类树面向【题库管理】的组织/排序/启停，
不含 AI 建树/题库关联的强耦合，仅做题库自身的分类维度。

仅提供纯业务方法，路由层在 api.py 装配。
"""
import os
import sqlite3
import threading
from pathlib import Path

from .config import Config

_store: "QuestionCategoryStore | None" = None


class QuestionCategoryStore:
    def __init__(self, db_path=None):
        default = Config.data_dir / "qcategory.db"
        self.db_path = Path(db_path or os.environ.get("QCATEGORY_DB") or default)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # 使 dict(row) 可按列名取值
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS question_category ("
                "  category_id TEXT PRIMARY KEY,"
                "  teacher_id  TEXT NOT NULL,"
                "  parent_id   TEXT DEFAULT '',"
                "  name        TEXT NOT NULL,"
                "  description TEXT DEFAULT '',"
                "  sort_order  INTEGER DEFAULT 0,"
                "  enabled     INTEGER DEFAULT 1,"
                "  created_at  TEXT NOT NULL,"
                "  updated_at  TEXT NOT NULL"
                ")")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qcat_parent ON question_category(parent_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qcat_teacher ON question_category(teacher_id)")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        from .auth import _now
        return _now()

    # ---------- 读 ----------
    def list_categories(self, teacher_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM question_category WHERE teacher_id=? "
                "ORDER BY parent_id, sort_order, created_at", (teacher_id,)).fetchall()
        items = [dict(r) for r in rows]
        return self._with_paths(items)

    def _with_paths(self, items: list[dict]) -> list[dict]:
        by_id = {i["category_id"]: i for i in items}
        for i in items:
            parts, cur, seen = [], i, set()
            while cur and cur["category_id"] not in seen:
                seen.add(cur["category_id"])
                parts.append(cur["name"])
                pid = (cur.get("parent_id") or "").strip()
                cur = by_id.get(pid) if pid else None
            i["path"] = "/".join(reversed(parts)) if parts else i["name"]
            i["is_root"] = 1 if not (i.get("parent_id") or "").strip() \
                              or (i.get("parent_id") or "").strip() not in by_id else 0
        return items

    def get_category(self, teacher_id: str, category_id: str) -> dict | None:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM question_category WHERE category_id=? AND teacher_id=?",
                (category_id, teacher_id)).fetchone()
        return dict(r) if r else None

    # ---------- 写 ----------
    def create_category(self, teacher_id: str, category_id: str, parent_id: str,
                        name: str, description: str = "", sort_order: int = 0) -> dict:
        cid = category_id.strip()
        name = name.strip()
        if not cid or not name:
            raise ValueError("category_id 与 name 必填")
        if not cid.replace("_", "").replace("-", "").isalnum() or len(cid) > 48:
            raise ValueError("category_id 只能含字母数字下划线连字符，长度 ≤ 48")
        parent_id = (parent_id or "").strip()
        with self._lock:
            conn = self._connect()
            try:
                if self.get_category(teacher_id, cid):
                    raise ValueError(f"分类已存在: {cid}")
                now = self._now()
                conn.execute(
                    "INSERT INTO question_category(category_id, teacher_id, parent_id, name,"
                    " description, sort_order, enabled, created_at, updated_at)"
                    " VALUES(?,?,?,?,?,?,1,?,?)",
                    (cid, teacher_id, parent_id, name, description, sort_order, now, now))
                conn.commit()
                return self.get_category(teacher_id, cid)
            finally:
                conn.close()

    def update_category(self, teacher_id: str, category_id: str, **fields) -> dict:
        allowed = ("name", "parent_id", "description", "sort_order", "enabled")
        if not any(k in fields for k in allowed):
            raise ValueError("无更新字段")
        with self._lock:
            conn = self._connect()
            try:
                # 父级环检测/自引用校验（需连接查询子树）
                if "parent_id" in fields:
                    p = (fields["parent_id"] or "").strip()
                    if p == category_id:
                        raise ValueError("不能把自身设为父级")
                    sub = self._subtree_ids(conn, teacher_id, category_id, set())
                    if p in sub:
                        raise ValueError("父级不能是自己的子孙节点")
                    fields["parent_id"] = p
                # 逐字段规范化，并按下表列顺序组装 SET 子句（键→值独立映射，避免错位覆盖）
                if "name" in fields:
                    if not str(fields["name"]).strip():
                        raise ValueError("name 不能为空")
                    fields["name"] = str(fields["name"]).strip()
                if "enabled" in fields:
                    fields["enabled"] = 1 if fields["enabled"] else 0
                if "sort_order" in fields:
                    fields["sort_order"] = int(fields["sort_order"] or 0)
                sets = [f"{k}=?" for k in allowed if k in fields]
                vals = [fields[k] for k in allowed if k in fields]
                vals.append(self._now())
                vals.append(category_id)
                vals.append(teacher_id)
                conn.execute(
                    f"UPDATE question_category SET {', '.join(sets)}, updated_at=? "
                    "WHERE category_id=? AND teacher_id=?", vals)
                conn.commit()
            finally:
                conn.close()
        return self.get_category(teacher_id, category_id)

    def _subtree_ids(self, conn, teacher_id: str, cid: str, seen: set) -> set:
        rows = conn.execute(
            "SELECT category_id FROM question_category WHERE teacher_id=? AND parent_id=?",
            (teacher_id, cid)).fetchall()
        for (r,) in rows:
            if r not in seen:
                seen.add(r)
                self._subtree_ids(conn, teacher_id, r, seen)
        return seen

    def delete_category(self, teacher_id: str, category_id: str, cascade: bool = False) -> dict:
        with self._lock:
            conn = self._connect()
            try:
                if not self.get_category(teacher_id, category_id):
                    raise ValueError(f"分类不存在: {category_id}")
                if cascade:
                    ids = self._subtree_ids(conn, teacher_id, category_id, set())
                    ids.add(category_id)
                    q = ",".join("?" * len(ids))
                    cur = conn.execute(
                        f"DELETE FROM question_category WHERE teacher_id=? AND category_id IN ({q})",
                        [teacher_id, *ids])
                    deleted = cur.rowcount
                    # 删除后子级一并移除（已含）
                else:
                    kids = conn.execute(
                        "SELECT COUNT(*) n FROM question_category WHERE teacher_id=? AND parent_id=?",
                        (teacher_id, category_id)).fetchone()["n"]
                    if kids:
                        raise ValueError(f"该分类下有 {kids} 个子分类，请先处理或使用级联删除")
                    cur = conn.execute(
                        "DELETE FROM question_category WHERE teacher_id=? AND category_id=?",
                        (teacher_id, category_id))
                    deleted = cur.rowcount
                conn.commit()
                return {"deleted": deleted}
            finally:
                conn.close()

    def reorder(self, teacher_id: str, ordered: list[dict]) -> int:
        # ordered: [{category_id, sort_order, parent_id}] 顺序即文档顺序，可携带上级调整
        with self._lock:
            conn = self._connect()
            try:
                n = 0
                ids = {c["category_id"]: c for c in ordered}
                valid = set(r["category_id"] for r in conn.execute(
                    "SELECT category_id FROM question_category WHERE teacher_id=?", (teacher_id,)))
                for c in ordered:
                    cid = c.get("category_id")
                    if cid not in valid:
                        continue
                    so = int(c.get("sort_order", 0) or 0)
                    conn.execute(
                        "UPDATE question_category SET sort_order=?, updated_at=? "
                        "WHERE category_id=? AND teacher_id=?",
                        (so, self._now(), cid, teacher_id))
                    n += 1
                conn.commit()
                return n
            finally:
                conn.close()

    def stats(self, teacher_id: str) -> dict:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) n FROM question_category WHERE teacher_id=?",
                (teacher_id,)).fetchone()["n"]
            roots = conn.execute(
                "SELECT COUNT(*) n FROM question_category WHERE teacher_id=? AND parent_id=''",
                (teacher_id,)).fetchone()["n"]
            enabled = conn.execute(
                "SELECT COUNT(*) n FROM question_category WHERE teacher_id=? AND enabled=1",
                (teacher_id,)).fetchone()["n"]
        return {"total": total, "roots": roots, "enabled": enabled, "disabled": max(total - enabled, 0)}


def get_qcategory_store() -> QuestionCategoryStore:
    global _store
    if _store is None:
        _store = QuestionCategoryStore()
    return _store