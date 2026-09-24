# -*- coding: utf-8 -*-
"""C2 · 知识体系管理（#20）—— 节点 CRUD / 树检索 / 批量管理 / 导图转换。

职责（存储层，纯 SQLite，不含 FastAPI/LLM 依赖）：
  - knowledge_nodes 表：老师隔离的知识树（parent_id 父指针模型）
  - CRUD：创建 / 编辑 / 删除（级联子树）/ 移动（含环检测）
  - 检索：keyword 命中节点 + 保留祖先链（前端高亮路径）
  - 批量：批量删除 / 批量移动
  - 导出转换：嵌套树 JSON / Markdown 大纲 / FreeMind .mm（XML）

数据存储：study.db（与题库/收藏同库，SQLite WAL 并发友好）。
LLM 增强在 admin.py 编排层（本层保持纯存储职责）。
"""
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from .config import Config


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _xml_attr(s: str) -> str:
    """XML 属性值转义（&/</>/'/\" 全覆盖；默认 escape 不转义引号会破坏属性）。"""
    return xml_escape(s, {'"': "&quot;", "'": "&apos;"})


def _node_id() -> str:
    return "KN" + uuid.uuid4().hex[:10]


class KnowledgeStore:
    """知识体系节点存储。"""

    def __init__(self, db_path: str | Path | None = None):
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
            CREATE TABLE IF NOT EXISTS knowledge_nodes (
                node_id     TEXT PRIMARY KEY,
                teacher_id  TEXT NOT NULL,
                parent_id   TEXT DEFAULT '',
                name        TEXT NOT NULL,
                category    TEXT DEFAULT '',
                description TEXT DEFAULT '',
                sort_order  INTEGER DEFAULT 0,
                tree_type   TEXT DEFAULT 'knowledge',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kn_teacher ON knowledge_nodes(teacher_id);
            CREATE INDEX IF NOT EXISTS idx_kn_parent ON knowledge_nodes(parent_id);
            """)
            # 既有库平滑升级：tree_type（#24 R2 题型树与知识树共表分域）
            try:
                conn.execute("ALTER TABLE knowledge_nodes ADD COLUMN tree_type TEXT DEFAULT 'knowledge'")
            except sqlite3.OperationalError:
                pass  # 列已存在
            # 历史数据补默认值（ALTER 只对新行生效，旧行可能为 NULL）
            conn.execute("UPDATE knowledge_nodes SET tree_type='knowledge' WHERE tree_type IS NULL OR tree_type=''")

    # ---------- 通用校验 ----------

    @staticmethod
    def _clean_name(name: str) -> str:
        name = (name or "").strip()
        if not name:
            raise ValueError("节点名称不能为空")
        if len(name) > 60:
            raise ValueError("节点名称过长（≤60 字）")
        return name

    def _get(self, conn: sqlite3.Connection, node_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM knowledge_nodes WHERE node_id=?", (node_id,)).fetchone()
        if not row:
            raise ValueError(f"节点不存在: {node_id}")
        return row

    @staticmethod
    def _clean_tree_type(tree_type: str) -> str:
        tt = (tree_type or "knowledge").strip()
        if tt not in ("knowledge", "qtype"):
            raise ValueError("tree_type 必须是 knowledge 或 qtype")
        return tt

    def _assert_parent(self, conn: sqlite3.Connection, teacher_id: str, parent_id: str,
                       tree_type: str = "knowledge") -> None:
        """父节点必须存在、同老师且同树类型。空串 = 根级。"""
        if not parent_id:
            return
        row = self._get(conn, parent_id)
        if row["teacher_id"] != teacher_id:
            raise ValueError("父节点不属于该老师")
        if (row["tree_type"] or "knowledge") != tree_type:
            raise ValueError("父节点属于另一棵树（知识树/题型树不可混挂）")

    def _is_descendant(self, conn: sqlite3.Connection, ancestor_id: str, node_id: str) -> bool:
        """node_id 是否在 ancestor_id 的子树内（含自身）。沿 parent 链上溯，O(深度)。"""
        cur = node_id
        seen: set[str] = set()
        while cur:
            if cur == ancestor_id:
                return True
            if cur in seen:  # 数据异常环保护
                return True
            seen.add(cur)
            row = conn.execute(
                "SELECT parent_id FROM knowledge_nodes WHERE node_id=?", (cur,)).fetchone()
            cur = row["parent_id"] if row else ""
        return False

    # ---------- CRUD ----------

    def create_node(self, teacher_id: str, name: str, parent_id: str = "",
                    category: str = "", description: str = "",
                    tree_type: str = "knowledge") -> dict:
        tree_type = self._clean_tree_type(tree_type)
        name = self._clean_name(name)
        category = (category or "").strip()[:30]
        description = (description or "").strip()[:500]
        now = _now_iso()
        with self._connect() as conn:
            self._assert_parent(conn, teacher_id, parent_id, tree_type)
            # 同级末尾追加（sort_order = max+1）
            mx = conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) m FROM knowledge_nodes "
                "WHERE teacher_id=? AND parent_id=? AND tree_type=?",
                (teacher_id, parent_id, tree_type)).fetchone()["m"]
            node_id = _node_id()
            conn.execute(
                "INSERT INTO knowledge_nodes(node_id, teacher_id, parent_id, name, category, "
                "description, sort_order, tree_type, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (node_id, teacher_id, parent_id, name, category, description,
                 mx + 1, tree_type, now, now))
        return self.get_node(node_id)

    def get_node(self, node_id: str) -> dict:
        with self._connect() as conn:
            row = self._get(conn, node_id)
            return self._row2dict(row)

    def find_child(self, teacher_id: str, parent_id: str, name: str,
                   tree_type: str = "knowledge") -> dict | None:
        """按 父节点 + 名称 查同树下同名子节点（#33 R3 API 上传幂等跳过用）。

        命中返回节点 dict；无同名子节点返回 None。name 需为 create_node
        同口径清洗后的值（strip；这里再防御 strip 一次）。
        """
        tree_type = self._clean_tree_type(tree_type)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_nodes WHERE teacher_id=? AND parent_id=? "
                "AND name=? AND tree_type=? ORDER BY sort_order LIMIT 1",
                (teacher_id, (parent_id or "").strip(),
                 (name or "").strip(), tree_type),
            ).fetchone()
        return self._row2dict(row) if row else None

    @staticmethod
    def _row2dict(row: sqlite3.Row) -> dict:
        return {
            "node_id": row["node_id"], "teacher_id": row["teacher_id"],
            "parent_id": row["parent_id"], "name": row["name"],
            "category": row["category"], "description": row["description"],
            "sort_order": row["sort_order"],
            "tree_type": row["tree_type"] if "tree_type" in row.keys() else "knowledge",
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def update_node(self, node_id: str, name: str | None = None, category: str | None = None,
                    description: str | None = None, sort_order: int | None = None,
                    parent_id: str | None = None) -> dict:
        with self._connect() as conn:
            row = self._get(conn, node_id)
            sets: list[str] = []
            args: list = []
            if name is not None:
                sets.append("name=?"); args.append(self._clean_name(name))
            if category is not None:
                sets.append("category=?"); args.append((category or "").strip()[:30])
            if description is not None:
                sets.append("description=?"); args.append((description or "").strip()[:500])
            if sort_order is not None:
                sets.append("sort_order=?"); args.append(int(sort_order))
            if parent_id is not None:
                # 移动：环检测（不能移动到自身或自己的子孙下）+ 同老师/同树校验
                if parent_id and parent_id == node_id:
                    raise ValueError("不能将节点移动到自身之下")
                if parent_id and self._is_descendant(conn, node_id, parent_id):
                    raise ValueError("不能将节点移动到自己的子节点之下（会形成环）")
                self._assert_parent(conn, row["teacher_id"], parent_id,
                                    row["tree_type"] or "knowledge")
                sets.append("parent_id=?"); args.append(parent_id)
            if not sets:
                return self._row2dict(row)
            sets.append("updated_at=?"); args.append(_now_iso())
            args.append(node_id)
            conn.execute(f"UPDATE knowledge_nodes SET {', '.join(sets)} WHERE node_id=?", args)
        return self.get_node(node_id)

    def delete_node(self, node_id: str) -> dict:
        """删除节点及其整个子树（级联）。返回删除数量。"""
        with self._connect() as conn:
            self._get(conn, node_id)
            ids = self._subtree_ids(conn, node_id)
            conn.executemany(
                "DELETE FROM knowledge_nodes WHERE node_id=?", [(i,) for i in ids])
            return {"deleted": len(ids)}

    def _subtree_ids(self, conn: sqlite3.Connection, node_id: str) -> list[str]:
        """收集子树全部 node_id（BFS）。"""
        out: list[str] = []
        queue = [node_id]
        while queue:
            cur = queue.pop(0)
            if cur in out:
                continue  # 数据异常环保护
            out.append(cur)
            rows = conn.execute(
                "SELECT node_id FROM knowledge_nodes WHERE parent_id=?", (cur,)).fetchall()
            queue.extend(r["node_id"] for r in rows)
        return out

    # ---------- 批量 ----------

    def batch_delete(self, node_ids: list[str]) -> dict:
        """批量删除：先展开每个节点子树再去重（同时删两个 ancestor 关系节点不重复计）。"""
        if not isinstance(node_ids, list) or not node_ids:
            raise ValueError("node_ids 不能为空")
        if len(node_ids) > 200:
            raise ValueError("单次批量操作上限 200 个节点")
        with self._connect() as conn:
            all_ids: set[str] = set()
            for nid in node_ids:
                row = conn.execute(
                    "SELECT node_id FROM knowledge_nodes WHERE node_id=?", (nid,)).fetchone()
                if not row:
                    continue  # 不存在则跳过（幂等）
                all_ids.update(self._subtree_ids(conn, nid))
            conn.executemany(
                "DELETE FROM knowledge_nodes WHERE node_id=?", [(i,) for i in all_ids])
            return {"deleted": len(all_ids)}

    def batch_move(self, node_ids: list[str], target_parent_id: str) -> dict:
        """批量移动到目标父节点下。逐个环检测；任何一个非法则整体失败（事务）。"""
        if not isinstance(node_ids, list) or not node_ids:
            raise ValueError("node_ids 不能为空")
        if len(node_ids) > 200:
            raise ValueError("单次批量操作上限 200 个节点")
        with self._connect() as conn:
            parent = self._get(conn, target_parent_id) if target_parent_id else None
            moved = 0
            now = _now_iso()
            for nid in node_ids:
                row = conn.execute(
                    "SELECT * FROM knowledge_nodes WHERE node_id=?", (nid,)).fetchone()
                if not row:
                    continue
                if parent:
                    if parent["teacher_id"] != row["teacher_id"]:
                        raise ValueError("批量移动的节点与目标父节点不属于同一老师")
                    if (parent["tree_type"] or "knowledge") != (row["tree_type"] or "knowledge"):
                        raise ValueError("目标父节点与节点不属于同一棵树（知识树/题型树不可混挂）")
                    if nid == target_parent_id or self._is_descendant(conn, nid, target_parent_id):
                        raise ValueError(f"节点 {row['name']} 不能移动到自身或其子节点之下")
                conn.execute(
                    "UPDATE knowledge_nodes SET parent_id=?, updated_at=? WHERE node_id=?",
                    (target_parent_id, now, nid))
                moved += 1
            return {"moved": moved}

    # ---------- 树构建 / 检索 / 统计 ----------

    def get_tree(self, teacher_id: str, keyword: str = "",
                 tree_type: str = "knowledge") -> dict:
        """返回树形结构。keyword 非空时仅保留命中节点及其祖先链。"""
        tree_type = self._clean_tree_type(tree_type)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_nodes WHERE teacher_id=? AND tree_type=? "
                "ORDER BY sort_order, created_at", (teacher_id, tree_type)).fetchall()
        nodes = [self._row2dict(r) for r in rows]
        if keyword:
            kw = keyword.strip().lower()
            if kw:
                hit_ids = {
                    n["node_id"] for n in nodes
                    if kw in n["name"].lower()
                    or kw in (n["category"] or "").lower()
                    or kw in (n["description"] or "").lower()
                }
                # 保留祖先链
                by_id = {n["node_id"]: n for n in nodes}
                keep = set(hit_ids)
                for nid in hit_ids:
                    cur = by_id.get(nid)
                    while cur and cur["parent_id"] and cur["parent_id"] in by_id:
                        keep.add(cur["parent_id"])
                        cur = by_id[cur["parent_id"]]
                nodes = [n for n in nodes if n["node_id"] in keep]
        tree = self._build_tree(nodes)
        return {"nodes": tree, "total": len(nodes)}

    @staticmethod
    def _build_tree(nodes: list[dict]) -> list[dict]:
        by_parent: dict[str, list[dict]] = {}
        by_id = {n["node_id"]: n for n in nodes}
        for n in nodes:
            by_parent.setdefault(n["parent_id"], []).append(n)
        roots = by_parent.get("", [])
        # 孤儿节点（父不在结果集内，keyword 过滤后可能残留）挂到根级，防丢失
        orphans = [n for n in nodes if n["parent_id"] and n["parent_id"] not in by_id]
        roots = roots + orphans

        def attach(node: dict) -> dict:
            children = by_parent.get(node["node_id"], [])
            return {**node, "children": [attach(c) for c in children]}

        return [attach(r) for r in roots]

    def stats(self, teacher_id: str, tree_type: str = "knowledge") -> dict:
        tree_type = self._clean_tree_type(tree_type)
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) n FROM knowledge_nodes WHERE teacher_id=? AND tree_type=?",
                (teacher_id, tree_type)).fetchone()["n"]
            cats = conn.execute(
                "SELECT category, COUNT(*) n FROM knowledge_nodes "
                "WHERE teacher_id=? AND tree_type=? GROUP BY category ORDER BY n DESC",
                (teacher_id, tree_type)).fetchall()
        return {
            "total": total,
            "categories": [{"category": r["category"] or "未分类", "count": r["n"]} for r in cats],
        }

    # ---------- #24 R2 题型关联辅助（题目按分类过滤/展示路径） ----------

    def subtree_ids(self, teacher_id: str, node_id: str) -> list[str]:
        """公开子树 ID 收集（题目按分类筛选时展开子类）。"""
        with self._connect() as conn:
            self._get(conn, node_id)
            return self._subtree_ids(conn, node_id)

    def node_paths(self, teacher_id: str, tree_type: str = "knowledge") -> dict[str, str]:
        """全量节点路径映射 {node_id: '根/子/孙'}（前端展示分类路径用）。"""
        tree_type = self._clean_tree_type(tree_type)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT node_id, parent_id, name FROM knowledge_nodes "
                "WHERE teacher_id=? AND tree_type=?", (teacher_id, tree_type)).fetchall()
        by_id = {r["node_id"]: (r["parent_id"], r["name"]) for r in rows}
        out: dict[str, str] = {}
        for nid, (pid, name) in by_id.items():
            parts = [name]
            cur = pid
            seen = set()
            while cur and cur in by_id and cur not in seen:
                seen.add(cur)
                parts.append(by_id[cur][1])
                cur = by_id[cur][0]
            out[nid] = "/".join(reversed(parts))
        return out

    # ---------- 导图转换（#21：问题4 数据层） ----------

    def _subtree_dicts(self, teacher_id: str, root_id: str = "") -> list[dict] | dict:
        """取整树或指定根的子树（嵌套结构）。root_id 为空 = 全树（虚拟根）。"""
        with self._connect() as conn:
            if root_id:
                root_row = self._get(conn, root_id)
                if root_row["teacher_id"] != teacher_id:
                    raise ValueError("根节点不属于该老师")
                rows = conn.execute(
                    "SELECT * FROM knowledge_nodes WHERE teacher_id=? "
                    "ORDER BY sort_order, created_at", (teacher_id,)).fetchall()
                nodes = [self._row2dict(r) for r in rows]
                ids = set(self._subtree_ids(conn, root_id))
                nodes = [n for n in nodes if n["node_id"] in ids]
                by_parent: dict[str, list[dict]] = {}
                for n in nodes:
                    by_parent.setdefault(n["parent_id"], []).append(n)
                def attach(node: dict) -> dict:
                    return {**node, "children": [attach(c) for c in by_parent.get(node["node_id"], [])]}
                root = next(n for n in nodes if n["node_id"] == root_id)
                return attach(root)
            rows = conn.execute(
                "SELECT * FROM knowledge_nodes WHERE teacher_id=? "
                "ORDER BY sort_order, created_at", (teacher_id,)).fetchall()
            nodes = [self._row2dict(r) for r in rows]
            return {"node_id": "", "name": teacher_id, "children": self._build_tree(nodes)}

    def to_nested(self, teacher_id: str, root_id: str = "") -> dict:
        """嵌套树 JSON（前端渲染导图用）。"""
        return self._subtree_dicts(teacher_id, root_id)

    def to_markdown(self, teacher_id: str, root_id: str = "", title: str = "知识体系",
                    tree: dict | None = None) -> str:
        """Markdown 大纲（# 层级递进）。XMind/幕布/markmap 可直接导入。
        tree 可注入（AI 增强后的内存树，避免重查库丢失增强结果）。"""
        tree = tree if tree is not None else self._subtree_dicts(teacher_id, root_id)
        lines: list[str] = [f"# {title}"]

        def walk(node: dict, depth: int) -> None:
            pad = "  " * depth
            desc = (node.get("description") or "").replace("\n", " ")
            if desc:
                lines.append(f"{pad}- **{node['name']}**：{desc}")
            else:
                lines.append(f"{pad}- {node['name']}")
            for c in node.get("children", []):
                walk(c, depth + 1)

        for c in tree.get("children", []):
            walk(c, 1)
        return "\n".join(lines) + "\n"

    def to_freemind(self, teacher_id: str, root_id: str = "", title: str = "知识体系",
                    tree: dict | None = None) -> str:
        """FreeMind .mm 格式（标准思维导图 XML，XMind/MindManager 可导入）。
        tree 可注入（AI 增强后的内存树）。"""
        tree = tree if tree is not None else self._subtree_dicts(teacher_id, root_id)

        def node_xml(node: dict) -> str:
            attrs = f'TEXT="{_xml_attr(node["name"])}"'
            children = node.get("children", [])
            if not children:
                return f"<node {attrs}/>"
            inner = "".join(node_xml(c) for c in children)
            return f"<node {attrs}>{inner}</node>"

        body = "".join(node_xml(c) for c in tree.get("children", []))
        return (
            '<map version="1.0.1">'
            f'<node TEXT="{_xml_attr(title)}">{body}</node>'
            "</map>"
        )


# ---------- 模块级单例（同 StudyStore 模式） ----------
_store: KnowledgeStore | None = None


def get_knowledge_store() -> KnowledgeStore:
    global _store
    if _store is None:
        _store = KnowledgeStore()
    return _store
