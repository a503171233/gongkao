# -*- coding: utf-8 -*-
"""自动采集：零配置自主找题（课程大类/学科/知识树叶子词池）+ 入库知识节点自动打标。"""

# ============================================================
# 自主采集（零配置找题）+ 入库自动打标（知识节点关联）
# ============================================================

# 课程大类（与 question_bank.category 枚举、老师管理 course_category 一致）
_COURSE_CATEGORIES = ("言语理解", "判断推理", "数量关系", "资料分析",
                      "常识判断", "申论", "面试", "综合")


def _collect_tree_leaves(nodes: list[dict], acc: list[dict]) -> None:
    """递归收集知识树的叶子节点（无 children 的节点 = 最细粒度考点）。"""
    for n in nodes or []:
        children = n.get("children") or []
        if children:
            _collect_tree_leaves(children, acc)
        else:
            acc.append(n)


def _knowledge_leaf_names(teacher_id: str) -> list[str]:
    """某老师知识树（tree_type=knowledge）全部叶子节点名（去重保序）。"""
    from .knowledge import get_knowledge_store
    try:
        tree = get_knowledge_store().get_tree(teacher_id, "", "knowledge")
    except Exception:  # noqa: BLE001  知识树缺失/损坏不影响采集
        return []
    leaves: list[dict] = []
    _collect_tree_leaves(tree.get("nodes") or [], leaves)
    out: list[str] = []
    seen: set[str] = set()
    for n in leaves:
        name = (n.get("name") or "").strip()
        if len(name) >= 2 and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _autonomous_query_pool(teacher_ids: list[str]) -> list[str]:
    """零配置搜索词池：课程大类 + 启用老师学科 + 知识树叶子节点名。
    顺序稳定（去重保序），供游标轮转逐批探索，无需人工配置搜索词。"""
    pool: list[str] = []
    seen: set[str] = set()

    def add(kw: str) -> None:
        kw = (kw or "").strip()
        if not kw or kw in seen:
            return
        seen.add(kw)
        pool.append(kw)

    for cat in _COURSE_CATEGORIES:
        add(f"公务员考试 {cat} 真题及答案")
    for tid in teacher_ids:
        subj = ""
        try:
            from .config import Config
            subj = getattr(Config().get_teacher(tid), "teacher_subject", "") or ""
        except Exception:  # noqa: BLE001
            subj = ""
        if subj:
            add(f"{subj} 公务员考试 真题")
    for tid in teacher_ids:
        for name in _knowledge_leaf_names(tid):
            add(f"{name} 公务员 真题")
    return pool


def _autonomous_plan(state: dict, teacher_ids: list[str], cap: int) -> dict:
    """按持久化游标从稳定词池取 cap 条（游标只读，推进由调用方写入 state）。"""
    pool = _autonomous_query_pool(teacher_ids)
    if not pool:
        return {"queries": [], "next_cursor": 0}
    cursor = int(state.get("autonomous_cursor", 0) or 0) % len(pool)
    queries = [pool[(cursor + i) % len(pool)] for i in range(min(cap, len(pool)))]
    return {"queries": queries, "next_cursor": (cursor + cap) % len(pool)}


def _knowledge_node_index(teacher_ids: list[str]) -> dict[str, str]:
    """跨启用老师知识树取叶子节点名→node_id 并集（同名保留首个，即先到老师优先）。"""
    from .knowledge import get_knowledge_store
    index: dict[str, str] = {}
    for tid in teacher_ids:
        try:
            tree = get_knowledge_store().get_tree(tid, "", "knowledge")
        except Exception:  # noqa: BLE001
            continue
        leaves: list[dict] = []
        _collect_tree_leaves(tree.get("nodes") or [], leaves)
        for n in leaves:
            name = (n.get("name") or "").strip().lower()
            nid = (n.get("node_id") or "").strip()
            if len(name) >= 2 and nid:
                index.setdefault(name, nid)
    return index


def _associate_category_id(question: dict, index: dict[str, str]) -> str:
    """按知识树叶子节点名做最长子串匹配，命中则返回 node_id（否则空串）。
    匹配域 = 题干 + 知识点 + 课程大类，最长命中优先（越具体越准），零 LLM 成本。"""
    if not index:
        return ""
    hay = " ".join([
        question.get("question") or "",
        question.get("knowledge_point") or "",
        question.get("category") or "",
    ]).lower()
    best = ""
    for name in index:
        if len(name) >= 2 and name in hay and len(name) > len(best):
            best = name
    return index.get(best, "")

