# -*- coding: utf-8 -*-
"""知识体系管理（#20/#21/#24）与题库分类管理路由。

- 前台只读：/public/knowledge/tree|stats|export
- 题库分类：/admin/questions/categories CRUD
- 后台知识树：/admin/knowledge/*（节点 CRUD、批量、思维导图、文档/搜索建树）
"""

from fastapi import APIRouter, Body, Header, HTTPException
from pydantic import BaseModel

from .. import deps as _deps
from .. import admin as admin_biz

router = APIRouter()


class AdminKnowledgeNodeReq(BaseModel):
    teacher_id: str = "T001"
    name: str = ""
    parent_id: str = ""
    category: str = ""
    description: str = ""
    tree_type: str = "knowledge"  # #24 R2：knowledge=知识树 | qtype=题型树


class AdminKnowledgeUpdateReq(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    sort_order: int | None = None
    parent_id: str | None = None


class AdminKnowledgeBatchReq(BaseModel):
    node_ids: list[str] = []
    target_parent_id: str = ""


class AdminMindmapReq(BaseModel):
    teacher_id: str = "T001"
    root_id: str = ""
    save_back: bool = False


class QCatCreateReq(BaseModel):
    teacher_id: str = "T001"
    category_id: str = ""
    parent_id: str = ""
    name: str = ""
    description: str = ""
    sort_order: int = 0


class QCatUpdateReq(BaseModel):
    name: str | None = None
    parent_id: str | None = None
    description: str | None = None
    sort_order: int | None = None
    enabled: bool | None = None


class QCatReorderReq(BaseModel):
    teacher_id: str = "T001"
    ordered: list[dict] = []


class BuildFromDocReq(BaseModel):
    """文档建树请求体。save=False 预演（只返回 AI 生成的树，不落库）。"""
    teacher_id: str = "T001"
    doc_name: str = ""
    root_name: str = ""
    parent_id: str = ""
    tree_type: str = "knowledge"
    save: bool = True


class BuildFromSearchReq(BaseModel):
    """联网搜索建树请求体。allow_fallback=True 搜索失败降级为纯 LLM 生成。"""
    teacher_id: str = "T001"
    query: str = ""
    root_id: str = ""
    root_name: str = ""
    tree_type: str = "knowledge"
    save: bool = True
    allow_fallback: bool = True


def _knowledge_store():
    from ..knowledge import get_knowledge_store
    return get_knowledge_store()


def _qcategory_store():
    from ..qcategory import get_qcategory_store
    return get_qcategory_store()


# ---------- 前台只读知识树 ----------
@router.get("/public/knowledge/tree")
def public_knowledge_tree(
    teacher_id: str = "T001",
    keyword: str = "",
    tree_type: str = "knowledge",
):
    """前台只读：知识体系/题型分类树。返回 {nodes: 树, total}。"""
    _deps._check_teacher(teacher_id)
    try:
        data = _knowledge_store().get_tree(teacher_id, keyword, tree_type=tree_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"teacher_id": teacher_id, **data}


@router.get("/public/knowledge/stats")
def public_knowledge_stats(teacher_id: str = "T001", tree_type: str = "knowledge"):
    """前台只读：知识体系统计（节点总数 / 分类分布）。"""
    _deps._check_teacher(teacher_id)
    try:
        return {"teacher_id": teacher_id, "tree_type": tree_type,
                **(_knowledge_store().stats(teacher_id, tree_type))}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/public/knowledge/export")
def public_knowledge_export(
    teacher_id: str = "T001",
    tree_type: str = "knowledge",
    fmt: str = "md",
    title: str = "",
):
    """前台只读：知识树导出内容（md=Markdown 大纲 / mm=FreeMind）。按当前老师+树类型取整树。"""
    _deps._check_teacher(teacher_id)
    if fmt not in ("md", "mm"):
        raise HTTPException(status_code=400, detail="fmt 仅支持：md / mm")
    try:
        ks = _knowledge_store()
        tcfg = _deps.cfg.get_teacher(teacher_id)
        root_title = (title or "").strip() or f"{tcfg.teacher_name}-{tree_type}"
        data = ks.get_tree(teacher_id, "", tree_type=tree_type)
        pseudo = {"node_id": "", "name": root_title, "children": data["nodes"]}
        if fmt == "md":
            content = ks.to_markdown(teacher_id, title=root_title, tree=pseudo)
        else:
            content = ks.to_freemind(teacher_id, title=root_title, tree=pseudo)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ext = "md" if fmt == "md" else "mm"
    return {"teacher_id": teacher_id, "tree_type": tree_type,
            "filename": f"{root_title}.{ext}", "content": content}


# ---------- 题库分类管理 ----------
@router.get("/admin/questions/categories")
def admin_qcat_list(teacher_id: str = "T001", authorization: str = Header(default="")):
    """题库分类树/列表（含 path 标注）。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(teacher_id)
    cats = _qcategory_store().list_categories(teacher_id)
    return {"teacher_id": teacher_id, "categories": cats,
            "stats": _qcategory_store().stats(teacher_id)}


@router.post("/admin/questions/categories")
def admin_qcat_create(req: QCatCreateReq, authorization: str = Header(default="")):
    _deps._admin_user(authorization)
    _deps._check_teacher(req.teacher_id)
    try:
        cat = _qcategory_store().create_category(
            req.teacher_id, req.category_id, req.parent_id, req.name,
            req.description, req.sort_order)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return cat


@router.patch("/admin/questions/categories/{category_id}")
def admin_qcat_update(category_id: str, req: QCatUpdateReq, teacher_id: str = "T001",
                      authorization: str = Header(default="")):
    _deps._admin_user(authorization)
    _deps._check_teacher(teacher_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        cat = _qcategory_store().update_category(teacher_id, category_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if cat is None:
        raise HTTPException(status_code=404, detail=f"分类不存在: {category_id}")
    return cat


@router.delete("/admin/questions/categories/{category_id}")
def admin_qcat_delete(category_id: str, teacher_id: str = "T001",
                      cascade: bool = False, authorization: str = Header(default="")):
    _deps._admin_user(authorization)
    _deps._check_teacher(teacher_id)
    try:
        return _qcategory_store().delete_category(teacher_id, category_id, cascade=cascade)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/questions/categories/reorder")
def admin_qcat_reorder(req: QCatReorderReq, authorization: str = Header(default="")):
    _deps._admin_user(authorization)
    _deps._check_teacher(req.teacher_id)
    try:
        n = _qcategory_store().reorder(req.teacher_id, req.ordered)
        return {"updated": n}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- 后台知识树管理 ----------
@router.get("/admin/knowledge/tree")
def admin_knowledge_tree(
    teacher_id: str = "T001",
    keyword: str = "",
    tree_type: str = "knowledge",
    authorization: str = Header(default=""),
):
    """知识体系树（keyword 检索时保留命中节点+祖先链）。
    #24 R2：tree_type=qtype 返回题型细分树（如 行测-判断推理-图形推理-黑白块）。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(teacher_id)
    try:
        return _knowledge_store().get_tree(teacher_id, keyword, tree_type=tree_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/admin/knowledge/paths")
def admin_knowledge_paths(
    teacher_id: str = "T001",
    tree_type: str = "knowledge",
    authorization: str = Header(default=""),
):
    """#24 R2 全量节点路径映射 {node_id: '根/子/孙'}（题目列表展示分类路径用）。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(teacher_id)
    return {"teacher_id": teacher_id, "tree_type": tree_type,
            "paths": _knowledge_store().node_paths(teacher_id, tree_type)}


@router.get("/admin/knowledge/stats")
def admin_knowledge_stats(
    teacher_id: str = "T001",
    tree_type: str = "knowledge",
    authorization: str = Header(default=""),
):
    _deps._admin_user(authorization)
    _deps._check_teacher(teacher_id)
    return _knowledge_store().stats(teacher_id, tree_type=tree_type)


@router.post("/admin/knowledge/nodes")
def admin_knowledge_create(
    req: AdminKnowledgeNodeReq = Body(default_factory=AdminKnowledgeNodeReq),
    authorization: str = Header(default=""),
):
    """创建知识节点（parent_id 空 = 根级）。#24 R2：tree_type 区分知识树/题型树。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(req.teacher_id)
    try:
        return _knowledge_store().create_node(
            req.teacher_id, req.name, req.parent_id, req.category,
            req.description, req.tree_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/admin/knowledge/nodes/{node_id}")
def admin_knowledge_update(
    node_id: str,
    req: AdminKnowledgeUpdateReq = Body(default_factory=AdminKnowledgeUpdateReq),
    authorization: str = Header(default=""),
):
    """编辑节点（name/category/description/sort_order/parent_id 移动，含环检测）。"""
    _deps._admin_user(authorization)
    try:
        return _knowledge_store().update_node(
            node_id, req.name, req.category, req.description,
            req.sort_order, req.parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/admin/knowledge/nodes/{node_id}")
def admin_knowledge_delete(
    node_id: str,
    authorization: str = Header(default=""),
):
    """删除节点及整个子树（级联）。"""
    _deps._admin_user(authorization)
    try:
        return _knowledge_store().delete_node(node_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/admin/knowledge/batch-delete")
def admin_knowledge_batch_delete(
    req: AdminKnowledgeBatchReq = Body(default_factory=AdminKnowledgeBatchReq),
    authorization: str = Header(default=""),
):
    """批量删除（子树展开去重，幂等）。"""
    _deps._admin_user(authorization)
    try:
        return _knowledge_store().batch_delete(req.node_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/knowledge/batch-move")
def admin_knowledge_batch_move(
    req: AdminKnowledgeBatchReq = Body(default_factory=AdminKnowledgeBatchReq),
    authorization: str = Header(default=""),
):
    """批量移动到目标父节点（环检测，非法整体失败）。"""
    _deps._admin_user(authorization)
    try:
        return _knowledge_store().batch_move(req.node_ids, req.target_parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/knowledge/mindmap")
def admin_knowledge_mindmap(
    req: AdminMindmapReq = Body(default_factory=AdminMindmapReq),
    authorization: str = Header(default=""),
):
    """AI 思维导图：知识树 → LLM 增强节点说明 → 返回 tree/markdown/freemind 三格式。
    save_back=True 时增强结果回写节点 description（数据联动）。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(req.teacher_id)
    try:
        return admin_biz.enhance_mindmap(req.teacher_id, req.root_id, req.save_back)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/admin/knowledge/search-status")
def admin_knowledge_search_status(authorization: str = Header(default="")):
    """#24 R3 联网搜索是否可用（前端据此启停入口）。"""
    _deps._admin_user(authorization)
    from ..search import search_available, _provider
    return {"available": search_available(), "provider": _provider()}


@router.post("/admin/knowledge/build-from-doc")
def admin_knowledge_build_from_doc(
    req: BuildFromDocReq = Body(default_factory=BuildFromDocReq),
    authorization: str = Header(default=""),
):
    """#24 R3 文档建树：读取已上传文档（Word/PDF/Excel/md/txt/pptx）全文 →
    AI 整理知识点清单 → 落库成多级知识树（save=False 预演）。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(req.teacher_id)
    if not req.doc_name.strip():
        raise HTTPException(status_code=400, detail="doc_name 不能为空")
    try:
        return admin_biz.build_tree_from_document(
            req.teacher_id, req.doc_name.strip(), req.root_name.strip(),
            req.parent_id.strip(), req.tree_type, req.save)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/knowledge/build-from-search")
def admin_knowledge_build_from_search(
    req: BuildFromSearchReq = Body(default_factory=BuildFromSearchReq),
    authorization: str = Header(default=""),
):
    """#24 R3 联网搜索建树：搜索公考资料 → AI 归纳知识点 → 落库（自动编写完善知识体系）。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(req.teacher_id)
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")
    try:
        return admin_biz.enhance_tree_from_search(
            req.teacher_id, req.query.strip(), req.root_id.strip(),
            req.root_name.strip(), req.tree_type, req.save, req.allow_fallback)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))