# -*- coding: utf-8 -*-
"""C2 后台业务逻辑：知识体系 AI 导图 + 文档/搜索建树（从 admin.py 拆分）。"""
import json

from .admin_llm import _llm_call, _parse_llm_json_array


# ---------- 知识体系 AI 思维导图（#21：LLM 节点增强） ----------

def enhance_mindmap(teacher_id: str, root_id: str = "", save_back: bool = False) -> dict:
    """AI 思维导图：取知识树 → LLM 为 description 为空的节点生成一句话考点说明 →
    返回增强后的嵌套树 + Markdown + FreeMind 三格式。
    save_back=True 时把增强结果回写 knowledge_nodes.description（数据联动）。
    """
    from . import llm
    from .config import Config
    from .knowledge import get_knowledge_store

    ks = get_knowledge_store()
    cfg = Config()
    tcfg = cfg.get_teacher(teacher_id)
    subject = getattr(tcfg, "teacher_subject", "") if tcfg else ""

    tree = ks.to_nested(teacher_id, root_id)

    # 收集待增强节点（BFS，上限 60 个防 LLM 滥用/超时）
    pending: list[dict] = []
    queue = [tree]
    while queue:
        cur = queue.pop(0)
        for c in cur.get("children", []):
            if not (c.get("description") or "").strip():
                pending.append(c)
            queue.append(c)
    if len(pending) > 60:
        pending = pending[:60]

    title = tree.get("name") if tree.get("node_id") else f"{subject or '公考'}知识体系"

    if pending:
        # 一次性让 LLM 批量生成（node_id 定位，严格 JSON）
        items = [{"node_id": n["node_id"], "name": n["name"],
                  "path": _node_path(tree, n["node_id"])} for n in pending]
        sys_prompt = (
            "你是公考教研专家。为知识体系的每个节点生成一句话考点说明（≤40字，"
            "说明该知识点的核心考查内容或易错点）。输出严格 JSON 数组，不要输出 JSON 以外的文字："
            '[{"node_id": "与输入一致", "description": "一句话说明"}]'
            f"该科目方向：{subject or '公考'}。"
        )
        user_prompt = "知识节点列表：\n" + json.dumps(items, ensure_ascii=False)
        eff = _aim_eff()
        raw = _llm_call(
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": user_prompt}],
            tcfg,
            base_url=(eff or {}).get("base_url") if eff else None,
            api_key=(eff or {}).get("api_key") if eff else None,
        )
        descs = _parse_mindmap_enhance(raw)
        for n in pending:
            d = descs.get(n["node_id"])
            if d:
                n["description"] = d[:500]

    if save_back:
        for n in pending:
            if (n.get("description") or "").strip():
                try:
                    ks.update_node(n["node_id"], description=n["description"])
                except ValueError:
                    pass  # 回写失败不阻断导出

    return {
        "tree": tree,
        "title": title,
        # 注意：基于增强后的内存树生成（save_back=False 时重查库会丢增强结果）
        "markdown": ks.to_markdown(teacher_id, root_id, title=title, tree=tree),
        "freemind": ks.to_freemind(teacher_id, root_id, title=title, tree=tree),
        "enhanced": len(pending),
        "total_nodes": _count_nodes(tree),
    }


def _node_path(tree: dict, node_id: str) -> str:
    """从树根到目标节点的名称路径（给 LLM 上下文）。"""
    def walk(node: dict, trail: list[str]):
        if node.get("node_id") == node_id:
            return trail + [node.get("name", "")]
        for c in node.get("children", []):
            r = walk(c, trail + [node.get("name", "")])
            if r is not None:
                return r
        return None
    p = walk(tree, [])
    return " / ".join(x for x in (p or []) if x)


def _count_nodes(tree: dict) -> int:
    n = 0
    queue = [tree]
    while queue:
        cur = queue.pop(0)
        n += len(cur.get("children", []))
        queue.extend(cur.get("children", []))
    return n


def _parse_mindmap_enhance(raw: str) -> dict[str, str]:
    """LLM 增强 JSON → {node_id: description}。解析失败返回空 dict（导图仍可纯结构生成）。"""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return {}
    try:
        arr = json.loads(raw[start:end + 1])
    except ValueError:
        return {}
    out: dict[str, str] = {}
    if isinstance(arr, list):
        for it in arr:
            if isinstance(it, dict) and it.get("node_id") and (it.get("description") or "").strip():
                out[str(it["node_id"])] = str(it["description"]).strip()
    return out


# ---------- #24 R3：文档建树 / 联网搜索建树（知识体系自动编写） ----------

# LLM 输入资料截断上限（字符）：建树只需大纲级信息，过长反而稀释注意力
BUILD_TREE_MAX_CHARS = 12000
# 单次建树节点数上限与层级深度上限（防 LLM 失控输出 / 滥用）
_BUILD_TREE_MAX_NODES = 50
_BUILD_TREE_MAX_DEPTH = 4


def _llm_build_knowledge_tree(material: str, subject: str, source_hint: str) -> list[dict]:
    """把整理好的资料文本交给 LLM → 多级知识点树 JSON（复用 R1 容错解析器）。

    返回 [{"name","description","children":[...]}] 嵌套列表；失败抛 ValueError（带原因）。
    """
    from . import llm

    sys_prompt = (
        "你是公考教研专家。请把给定资料整理成多级知识点树，用于构建该科目方向的知识体系。"
        f"科目方向：{subject or '公考'}。资料来源：{source_hint}。\n"
        "要求：\n"
        "1. 顶层按大模块划分（如 判断推理/言语理解/数量关系…），每个大模块下细分小类，"
        "最多 4 层、总计不超过 40 个节点；\n"
        "2. 每个节点必须有简短名称（≤20字），并为每层节点写一句话考点说明（description，≤50字）；\n"
        "3. 只依据资料内容归纳，资料未覆盖的常见考点可补充但需通用准确；\n"
        "4. 输出严格 JSON 数组（顶层为各大模块），不要输出 JSON 以外的任何文字：\n"
        '[{"name": "模块名", "description": "一句话说明", "children": ['
        '{"name": "子类名", "description": "…", "children": []}]}]'
    )
    user_prompt = f"资料内容：\n{material[:BUILD_TREE_MAX_CHARS]}"
    raw = ""
    for attempt in range(3):  # 对齐 R1：解析失败带错误原因重试
        msgs = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt}]
        if attempt > 0:
            msgs.append({"role": "user", "content":
                         "你上一次的输出无法解析（结构必须是 JSON 数组且含 name 字段）。"
                         "请重新输出完整、合法、未截断的 JSON。"})
        eff = _aim_eff()
        raw = _llm_call(
            msgs, _build_tree_tcfg(),
            model=(eff or {}).get("model_id") if eff else None,
            temperature=(eff or {}).get("temperature") if eff else None,
            base_url=(eff or {}).get("base_url") if eff else None,
            api_key=(eff or {}).get("api_key") if eff else None,
        )
        arr, err = _parse_llm_json_array(raw)
        if err is None and isinstance(arr, list) and arr:
            return arr
        err = err or "输出为空数组"
    raise ValueError(
        f"AI 生成知识点树失败（已自动重试 2 次）：{err}。"
        f"模型原始输出片段：{(raw or '')[:120]!r}。可缩短资料后重试。")


def _build_tree_tcfg():
    """建树用全局默认老师配置（管理端操作，不绑定具体老师的模型偏好）。"""
    from .config import Config
    return Config().get_teacher(Config().teacher_id)


def _aim_eff() -> dict | None:
    """平台级 AI 生效配置（模型管理页默认模型；含模型自身通道 base_url/api_key）。
    DB 异常返回 None → 调用方回退全局 .env 配置。"""
    try:
        from .models import get_ai_model_store
        return get_ai_model_store().effective_default()
    except Exception:  # noqa: BLE001  DB 异常不阻断
        return None


def _create_nodes_recursive(ks, teacher_id: str, parent_id: str,
                            children: list, tree_type: str,
                            depth: int = 1, counter: list | None = None) -> int:
    """递归落库 LLM 生成的嵌套树 → knowledge_nodes。返回创建节点数。

    硬限制：深度 ≤ 4 层、总数 ≤ 50（超限截断，保证建树永远可控）。
    """
    counter = counter if counter is not None else [0]
    created = 0
    for it in children or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name or counter[0] >= _BUILD_TREE_MAX_NODES or depth > _BUILD_TREE_MAX_DEPTH:
            continue
        node = ks.create_node(
            teacher_id, name[:50], parent_id=parent_id,
            description=str(it.get("description") or "").strip()[:500],
            tree_type=tree_type)
        counter[0] += 1
        created += 1
        created += _create_nodes_recursive(
            ks, teacher_id, node["node_id"], it.get("children"),
            tree_type, depth + 1, counter)
    return created


def build_tree_from_document(teacher_id: str, doc_name: str, root_name: str = "",
                             parent_id: str = "", tree_type: str = "knowledge",
                             save: bool = True) -> dict:
    """#24 R3 文档建树：读取该老师已上传的文档全文 → LLM 整理知识点清单 → 落库成多级树。

    save=False 为预演模式：只返回 LLM 生成的树结构（children 嵌套），不落库。
    """
    from .config import Config
    from .ingest import extract_text
    from .knowledge import get_knowledge_store

    cfg = Config()
    tcfg = cfg.get_teacher(teacher_id)
    subject = getattr(tcfg, "teacher_subject", "") if tcfg else ""

    safe_name = (doc_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    path = cfg.upload_dir(teacher_id) / safe_name
    if not path.exists():
        raise ValueError(f"原始文件不存在: {safe_name}（请先在「文档管理」上传该文档）")
    try:
        text = extract_text(path)
    except ValueError as e:
        raise ValueError(str(e).replace("入库失败:", "解析失败:"))
    text = (text or "").strip()
    if not text:
        raise ValueError(f"未能从 {safe_name} 中解析出文本内容")

    arr = _llm_build_knowledge_tree(text, subject, f"老师课件《{safe_name}》")

    if not save:
        return {"doc_name": safe_name, "chars": len(text), "preview_tree": arr,
                "would_create": _count_preview_nodes(arr), "created": 0, "saved": False}

    ks = get_knowledge_store()
    root_id = (parent_id or "").strip()
    root_name = (root_name or "").strip()
    if root_id:
        ks.get_node(root_id)  # 校验目标父节点存在（不存在会抛错）
    elif root_name:
        root_id = ks.create_node(teacher_id, root_name[:50],
                                 tree_type=tree_type)["node_id"]
    created = _create_nodes_recursive(ks, teacher_id, root_id, arr, tree_type)
    if not root_id and not created:
        raise ValueError("AI 未生成任何有效知识点，未落库")
    return {
        "doc_name": safe_name, "chars": len(text),
        "root_id": root_id, "created": created, "saved": True,
        "preview_tree": arr if not created else None,
    }


def _count_preview_nodes(arr: list) -> int:
    n = 0
    stack = list(arr or [])
    while stack:
        it = stack.pop()
        if isinstance(it, dict) and (it.get("name") or "").strip():
            n += 1
            stack.extend(it.get("children") or [])
    return n


def enhance_tree_from_search(teacher_id: str, query: str, root_id: str = "",
                             root_name: str = "", tree_type: str = "knowledge",
                             save: bool = True, allow_fallback: bool = True) -> dict:
    """#24 R3 联网搜索建树：web_search 检索资料 → LLM 归纳成知识点树 → 落库。

    allow_fallback=True 时搜索失败不阻断：基于 LLM 已有知识继续生成，warning 透传前端。
    """
    from .config import Config
    from .knowledge import get_knowledge_store
    from .search import web_search, search_available

    cfg = Config()
    tcfg = cfg.get_teacher(teacher_id)
    subject = getattr(tcfg, "teacher_subject", "") if tcfg else ""

    q = (query or "").strip()
    if not q:
        raise ValueError("搜索关键词不能为空")
    if not search_available():
        raise ValueError("联网搜索未启用（服务器 SEARCH_PROVIDER=none），无法使用该功能")

    results, warning = web_search(q, max_results=6)
    material = ""
    source_hint = "联网搜索资料"
    if results:
        parts = [f"【{i + 1}】{r['title']}\n{r['snippet']}" for i, r in enumerate(results)]
        material = "\n\n".join(parts)
        source_hint = f"联网搜索「{q}」的 {len(results)} 条资料"
    elif not allow_fallback:
        raise ValueError(f"联网搜索失败，已终止：{warning}")

    arr = _llm_build_knowledge_tree(
        material or f"主题：{q}（无搜索资料，请基于你掌握的公考知识整理）",
        subject, source_hint if results else f"基于模型已有知识（搜索失败：{warning[:80]}）")

    if not save:
        return {"query": q, "results": len(results), "warning": warning,
                "preview_tree": arr, "would_create": _count_preview_nodes(arr),
                "created": 0, "saved": False}

    ks = get_knowledge_store()
    target = (root_id or "").strip()
    rid = target
    if not rid:
        rname = (root_name or "").strip() or q[:50]
        rid = ks.create_node(teacher_id, rname[:50], tree_type=tree_type)["node_id"]
    created = _create_nodes_recursive(ks, teacher_id, rid, arr, tree_type)
    if not created:
        raise ValueError("AI 未生成任何有效知识点，未落库")
    return {"query": q, "results": len(results), "warning": warning,
            "root_id": rid, "created": created, "saved": True}
