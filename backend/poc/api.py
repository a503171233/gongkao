# -*- coding: utf-8 -*-
"""网站二（RAG 内核服务）FastAPI 入口。
多老师支持：所有接口带 teacher_id；SSE 流式输出。
对应说明书 V3.0：网站二职责 = 知识库管理/检索/调度/推理/输出校验。

契约：store/answer 均为 teacher_id 签名（见各模块 docstring）。
"""
import json
import hmac
import os as _os
import re as _re
import secrets
import threading
import time as _time
import uuid
from collections import deque
from pathlib import Path as _Path

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Header, Request, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse, StreamingResponse, JSONResponse, FileResponse
from pydantic import BaseModel, Field

from .auth import AuthStore
from .config import Config
from .gitee import GiteeOAuth
from .ingest import ingest_file
from . import store
from . import admin as admin_biz
from .answer import ask as rag_ask
from .chat import ChatStore
from .guard import guard_counter
from .metrics import metrics
from .models import (
    get_ai_model_store as _get_aimodel,
    builtin_catalog,
    list_remote_models,
)
from .payment import get_payment_store, PLANS
from .sensitive import check_sensitive, SENSITIVE_ENABLED
from .study import get_study_store
from .incentive import get_incentive_store as _get_incentive
from .mockexam import get_mockexam_store as _get_mockexam
from .mockexam import start_paper_job as _start_mockexam_job
from .smartexam import get_smartexam_store as _get_smartexam
from .forum import get_forum_store as _get_forum
from .tracking import get_tracking_store as _get_tracking
from .banner import get_banner_store as _get_banner
from .messages import get_message_store as _get_messages
from . import stats as stats_biz
from . import autocollect as autocollect_mod

# 共享状态与工厂（由 deps/app 模块集中管理）
from .deps import (
    cfg, chat_store, auth_store, gitee_oauth, payment_store, study_store,
    incentive_store, mockexam_store, smartexam_store, forum_store, tracking_store,
    banner_store, message_store,
    _oauth_states, _STATE_TTL, HISTORY_LIMIT,
    _login_rate_limit, _login_fail_record, _client_ip,
    _CSRF_ALLOWED_ORIGINS, _CSRF_MUTATING,
    _security_rate_limit,
    _sse, _check_teacher, _safe_count,
    _resolve_user, _session_owner_guard, _quota_guard,
    _forum_write_ok, _forum_write_cooldown, _forum_require_admin,
    _grade_llm_call, _extract_json_obj, _article_cooldown,
    _import_roots,
    AskReq, RegisterReq, LoginReq,
    ForgotQuestionReq, ForgotAnswerReq, ForgotResetReq, SetSecurityReq,
)
from .app import create_app
app = create_app()

# _grade_llm_call / _extract_json_obj 已移至 deps.py（练习批改 + 文章提炼共用）。
# _article_cooldown 也已移至 deps.py。

# re-export tracking state for test compatibility (test accesses api._track_limiter / api._TRACK_MAX)
from .routers.tracking import _track_limiter, _TRACK_MAX  # noqa: F401

# ---- 模块属性同步：当测试等代码对 api.X 赋值时，同步到 deps.X ----
# 路由模块通过 "from ..deps import X" 引用共享状态（import 时绑定）；若仅修改
# api 的本地绑定，路由仍用旧值。此处利用 Python 模块 __class__ 可写特性
# （PEP 3130）为可替换属性添加 property 描述符，使 api.X = Y 等价于 deps.X = Y。
import sys as _sys
from . import deps as _deps_mod

_PATCHABLE = {
    'cfg', 'chat_store', 'auth_store', 'gitee_oauth', 'payment_store',
    'study_store', 'incentive_store', 'mockexam_store', 'smartexam_store',
    'forum_store', 'tracking_store', 'banner_store', 'message_store',
    '_resolve_user',
    '_admin_user',
    '_forum_write_ok',
    '_grade_llm_call',
    '_login_rate_limit', '_login_fail_record', '_client_ip',
    '_security_rate_limit',
}

class _ApiProxy(type(_sys)):
    pass

for _attr in _PATCHABLE:
    def _mk_get(name):
        def fget(self):
            return getattr(_deps_mod, name)
        return fget
    def _mk_set(name):
        def fset(self, value):
            setattr(_deps_mod, name, value)
            # 使用 sys.modules 修改自身存储的 __dict__
            import sys
            sys.modules[__name__].__dict__[name] = value
        return fset
    def _mk_del(name):
        def fdel(self):
            delattr(_deps_mod, name)
            import sys
            del sys.modules[__name__].__dict__[name]
        return fdel
    setattr(_ApiProxy, _attr, property(_mk_get(_attr), _mk_set(_attr), _mk_del(_attr)))

_sys.modules[__name__].__class__ = _ApiProxy

















# ================================================================
# #20 知识体系管理 · #21 AI 思维导图
# ================================================================
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


def _knowledge_store():
    from .knowledge import get_knowledge_store
    return get_knowledge_store()


@app.get("/public/knowledge/tree")
def public_knowledge_tree(
    teacher_id: str = "T001",
    keyword: str = "",
    tree_type: str = "knowledge",
):
    """前台只读：知识体系/题型分类树。返回 {nodes: 树, total}。"""
    _check_teacher(teacher_id)
    try:
        data = _knowledge_store().get_tree(teacher_id, keyword, tree_type=tree_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"teacher_id": teacher_id, **data}


@app.get("/public/knowledge/stats")
def public_knowledge_stats(teacher_id: str = "T001", tree_type: str = "knowledge"):
    """前台只读：知识体系统计（节点总数 / 分类分布）。"""
    _check_teacher(teacher_id)
    try:
        return {"teacher_id": teacher_id, "tree_type": tree_type,
                **(_knowledge_store().stats(teacher_id, tree_type))}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/public/knowledge/export")
def public_knowledge_export(
    teacher_id: str = "T001",
    tree_type: str = "knowledge",
    fmt: str = "md",
    title: str = "",
):
    """前台只读：知识树导出内容（md=Markdown 大纲 / mm=FreeMind）。按当前老师+树类型取整树。"""
    _check_teacher(teacher_id)
    if fmt not in ("md", "mm"):
        raise HTTPException(status_code=400, detail="fmt 仅支持：md / mm")
    try:
        ks = _knowledge_store()
        tcfg = cfg.get_teacher(teacher_id)
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


# ---------- 题库分类管理（独立 question_category 表） ----------
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


def _qcategory_store():
    from .qcategory import get_qcategory_store
    return get_qcategory_store()


@app.get("/admin/questions/categories")
def admin_qcat_list(teacher_id: str = "T001", authorization: str = Header(default="")):
    """题库分类树/列表（含 path 标注）。"""
    _admin_user(authorization)
    _check_teacher(teacher_id)
    cats = _qcategory_store().list_categories(teacher_id)
    return {"teacher_id": teacher_id, "categories": cats,
            "stats": _qcategory_store().stats(teacher_id)}


@app.post("/admin/questions/categories")
def admin_qcat_create(req: QCatCreateReq, authorization: str = Header(default="")):
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    try:
        cat = _qcategory_store().create_category(
            req.teacher_id, req.category_id, req.parent_id, req.name,
            req.description, req.sort_order)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return cat


@app.patch("/admin/questions/categories/{category_id}")
def admin_qcat_update(category_id: str, req: QCatUpdateReq, teacher_id: str = "T001",
                      authorization: str = Header(default="")):
    _admin_user(authorization)
    _check_teacher(teacher_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        cat = _qcategory_store().update_category(teacher_id, category_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if cat is None:
        raise HTTPException(status_code=404, detail=f"分类不存在: {category_id}")
    return cat


@app.delete("/admin/questions/categories/{category_id}")
def admin_qcat_delete(category_id: str, teacher_id: str = "T001",
                      cascade: bool = False, authorization: str = Header(default="")):
    _admin_user(authorization)
    _check_teacher(teacher_id)
    try:
        return _qcategory_store().delete_category(teacher_id, category_id, cascade=cascade)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/questions/categories/reorder")
def admin_qcat_reorder(req: QCatReorderReq, authorization: str = Header(default="")):
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    try:
        n = _qcategory_store().reorder(req.teacher_id, req.ordered)
        return {"updated": n}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/admin/knowledge/tree")
def admin_knowledge_tree(
    teacher_id: str = "T001",
    keyword: str = "",
    tree_type: str = "knowledge",
    authorization: str = Header(default=""),
):
    """知识体系树（keyword 检索时保留命中节点+祖先链）。
    #24 R2：tree_type=qtype 返回题型细分树（如 行测-判断推理-图形推理-黑白块）。"""
    _admin_user(authorization)
    _check_teacher(teacher_id)
    try:
        return _knowledge_store().get_tree(teacher_id, keyword, tree_type=tree_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/admin/knowledge/paths")
def admin_knowledge_paths(
    teacher_id: str = "T001",
    tree_type: str = "knowledge",
    authorization: str = Header(default=""),
):
    """#24 R2 全量节点路径映射 {node_id: '根/子/孙'}（题目列表展示分类路径用）。"""
    _admin_user(authorization)
    _check_teacher(teacher_id)
    return {"teacher_id": teacher_id, "tree_type": tree_type,
            "paths": _knowledge_store().node_paths(teacher_id, tree_type)}


@app.get("/admin/knowledge/stats")
def admin_knowledge_stats(
    teacher_id: str = "T001",
    tree_type: str = "knowledge",
    authorization: str = Header(default=""),
):
    _admin_user(authorization)
    _check_teacher(teacher_id)
    return _knowledge_store().stats(teacher_id, tree_type=tree_type)


@app.post("/admin/knowledge/nodes")
def admin_knowledge_create(
    req: AdminKnowledgeNodeReq = Body(default_factory=AdminKnowledgeNodeReq),
    authorization: str = Header(default=""),
):
    """创建知识节点（parent_id 空 = 根级）。#24 R2：tree_type 区分知识树/题型树。"""
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    try:
        return _knowledge_store().create_node(
            req.teacher_id, req.name, req.parent_id, req.category,
            req.description, req.tree_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/admin/knowledge/nodes/{node_id}")
def admin_knowledge_update(
    node_id: str,
    req: AdminKnowledgeUpdateReq = Body(default_factory=AdminKnowledgeUpdateReq),
    authorization: str = Header(default=""),
):
    """编辑节点（name/category/description/sort_order/parent_id 移动，含环检测）。"""
    _admin_user(authorization)
    try:
        return _knowledge_store().update_node(
            node_id, req.name, req.category, req.description,
            req.sort_order, req.parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/admin/knowledge/nodes/{node_id}")
def admin_knowledge_delete(
    node_id: str,
    authorization: str = Header(default=""),
):
    """删除节点及整个子树（级联）。"""
    _admin_user(authorization)
    try:
        return _knowledge_store().delete_node(node_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/admin/knowledge/batch-delete")
def admin_knowledge_batch_delete(
    req: AdminKnowledgeBatchReq = Body(default_factory=AdminKnowledgeBatchReq),
    authorization: str = Header(default=""),
):
    """批量删除（子树展开去重，幂等）。"""
    _admin_user(authorization)
    try:
        return _knowledge_store().batch_delete(req.node_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/knowledge/batch-move")
def admin_knowledge_batch_move(
    req: AdminKnowledgeBatchReq = Body(default_factory=AdminKnowledgeBatchReq),
    authorization: str = Header(default=""),
):
    """批量移动到目标父节点（环检测，非法整体失败）。"""
    _admin_user(authorization)
    try:
        return _knowledge_store().batch_move(req.node_ids, req.target_parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/knowledge/mindmap")
def admin_knowledge_mindmap(
    req: AdminMindmapReq = Body(default_factory=AdminMindmapReq),
    authorization: str = Header(default=""),
):
    """AI 思维导图：知识树 → LLM 增强节点说明 → 返回 tree/markdown/freemind 三格式。
    save_back=True 时增强结果回写节点 description（数据联动）。"""
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    try:
        return admin_biz.enhance_mindmap(req.teacher_id, req.root_id, req.save_back)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- #24 R3：文档建树 / 联网搜索建树 ----------
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


@app.get("/admin/knowledge/search-status")
def admin_knowledge_search_status(authorization: str = Header(default="")):
    """#24 R3 联网搜索是否可用（前端据此启停入口）。"""
    _admin_user(authorization)
    from .search import search_available, _provider
    return {"available": search_available(), "provider": _provider()}


@app.post("/admin/knowledge/build-from-doc")
def admin_knowledge_build_from_doc(
    req: BuildFromDocReq = Body(default_factory=BuildFromDocReq),
    authorization: str = Header(default=""),
):
    """#24 R3 文档建树：读取已上传文档（Word/PDF/Excel/md/txt/pptx）全文 →
    AI 整理知识点清单 → 落库成多级知识树（save=False 预演）。"""
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    if not req.doc_name.strip():
        raise HTTPException(status_code=400, detail="doc_name 不能为空")
    try:
        return admin_biz.build_tree_from_document(
            req.teacher_id, req.doc_name.strip(), req.root_name.strip(),
            req.parent_id.strip(), req.tree_type, req.save)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/knowledge/build-from-search")
def admin_knowledge_build_from_search(
    req: BuildFromSearchReq = Body(default_factory=BuildFromSearchReq),
    authorization: str = Header(default=""),
):
    """#24 R3 联网搜索建树：搜索公考资料 → AI 归纳知识点 → 落库（自动编写完善知识体系）。"""
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")
    try:
        return admin_biz.enhance_tree_from_search(
            req.teacher_id, req.query.strip(), req.root_id.strip(),
            req.root_name.strip(), req.tree_type, req.save, req.allow_fallback)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ================================================================
# #25 问题2：AI 模型管理（注册表 + 默认模型 + 连通性测试）
# ================================================================
class AiModelCreateReq(BaseModel):
    name: str = ""
    model_id: str = ""
    provider: str = "anyyds"
    base_url: str = ""
    api_key: str = ""
    enabled: bool = True
    temperature: float = 0.3
    max_tokens: int = 2000
    remark: str = ""

class AiModelUpdateReq(BaseModel):
    name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    remark: str | None = None

class AiModelTestReq(BaseModel):
    model_id: str = ""
    base_url: str = ""
    api_key: str = ""


@app.get("/admin/ai-models")
def admin_ai_models(authorization: str = Header(default="")):
    """AI 模型注册表列表。"""
    _admin_user(authorization)
    return {"models": _get_aimodel().list_models()}


@app.post("/admin/ai-models")
def admin_ai_model_create(req: AiModelCreateReq = Body(default_factory=AiModelCreateReq),
                          authorization: str = Header(default="")):
    """新增模型。"""
    _admin_user(authorization)
    if not req.name.strip() or not req.model_id.strip():
        raise HTTPException(status_code=400, detail="名称与模型标识不能为空")
    try:
        m = _get_aimodel().create_model(
            req.name, req.model_id, req.provider, req.base_url, req.api_key,
            req.enabled, req.temperature, int(req.max_tokens or 2000), req.remark)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"model": m}


@app.get("/admin/ai-models/builtin")
def admin_ai_models_builtin(authorization: str = Header(default="")):
    """内置模型目录（OpenAI 兼容协议；含已注册标记，兜底展示用）。"""
    _admin_user(authorization)
    return {"models": builtin_catalog()}


@app.post("/admin/ai-models/list-remote")
def admin_ai_models_list_remote(
    base_url: str = Body(default=""),
    provider: str = Body(default=""),
    api_key: str = Body(default=""),
    authorization: str = Header(default=""),
):
    """获取远程模型列表（GET {base_url}/models，OpenAI 兼容协议）。

    失败自动兜底内置目录：used_fallback=true + error 说明；前端据此提示并降级。
    """
    _admin_user(authorization)
    return list_remote_models(base_url=base_url, provider=provider, api_key=api_key)


@app.patch("/admin/ai-models/{model_id:path}")
def admin_ai_model_update(model_id: str,
                          req: AiModelUpdateReq = Body(default_factory=AiModelUpdateReq),
                          authorization: str = Header(default="")):
    """更新模型字段。"""
    _admin_user(authorization)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    st = _get_aimodel()
    m = st.update_model(model_id, **fields)
    if not m:
        raise HTTPException(status_code=404, detail="模型不存在")
    return {"model": m}


@app.delete("/admin/ai-models/{model_id:path}")
def admin_ai_model_delete(model_id: str, authorization: str = Header(default="")):
    """删除模型（默认模型同时被清除，effective 回退 .env）。"""
    _admin_user(authorization)
    st = _get_aimodel()
    m = st.get_model(model_id)
    if not m:
        raise HTTPException(status_code=404, detail="模型不存在")
    if m.get("is_default"):
        st.set_setting("default_model", "")
    st.delete_model(model_id)
    return {"deleted": model_id}


@app.post("/admin/ai-models/{model_id:path}/default")
def admin_ai_model_set_default(model_id: str, authorization: str = Header(default="")):
    """设为全局默认模型（平台级 AI：题库采集/抽取/知识建树等）。"""
    _admin_user(authorization)
    st = _get_aimodel()
    try:
        m = st.set_default(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    st.set_setting("default_model", model_id)
    return {"default": m}


@app.get("/admin/ai-models/effective")
def admin_ai_model_effective(authorization: str = Header(default="")):
    """当前平台级 AI 生效配置（AI模型管理页顶部卡片）。api_key 脱敏后返回。"""
    _admin_user(authorization)
    eff = dict(_get_aimodel().effective_default())
    eff["api_key"] = _get_aimodel()._mask_key(eff.get("api_key", ""))
    return {"effective": eff}


@app.get("/admin/ai-models/{model_id:path}")
def admin_ai_model_detail(model_id: str, authorization: str = Header(default="")):
    """模型详情（含完整 api_key，供编辑弹窗回显）。"""
    _admin_user(authorization)
    st = _get_aimodel()
    m = st.get_model(model_id)
    if not m:
        raise HTTPException(status_code=404, detail="模型不存在")
    d = dict(m)
    d["enabled"] = bool(d["enabled"])
    d["is_default"] = bool(d["is_default"])
    return {"model": d}


def _aim_error_msg(e: Exception, model_id: str) -> str:
    """把网关/OpenAI SDK 异常提炼为可操作的中文提示（/test 与 /test-all 共用）。"""
    s = str(e)
    if "No available channel" in s or "无可用渠道" in s:
        return ("密钥分组下无此模型通道(503)：模型可能已从网关下线，或当前 API Key 无权使用；"
                "若此前测速可用，多为网关通道临时禁用（稍后自动恢复）。"
                "可重新「获取模型」查看最新列表，或在模型里配置有权限的独立密钥")
    if "尚未由管理员配置" in s or "has not been priced" in s:
        return "模型价格未配置(400)：站点管理员尚未为该模型定价，暂时无法使用"
    if "is an image model" in s or "is a video model" in s:
        return "该模型为图片/视频生成模型，不支持对话接口，无法用于问答"
    if "model_not_found" in s:
        return f"模型不存在(503)：{model_id} 在该 Base URL 下不存在，请核对模型标识"
    if "429" in s or "rpm exhausted" in s or "rate limit" in s.lower():
        return "限流(429)：该密钥请求频率超限（一键测速并发较高时易触发），稍后重试"
    if "401" in s or "authentication" in s.lower() or "Invalid API" in s:
        return "鉴权失败(401)：API Key 对该通道无效"
    if "403" in s:
        return "无权限(403)：上游通道拒绝（密钥无该模型权限或站点余额不足）"
    return f"{type(e).__name__}: {s[:140]}"


@app.post("/admin/ai-models/test")
def admin_ai_model_test(req: AiModelTestReq = Body(default_factory=AiModelTestReq),
                        authorization: str = Header(default="")):
    """连通性测试：对给定模型做一次极短补全，返回状态/延迟/内容摘要。
    密钥解析优先级：请求体显式 api_key > 注册表保存的 api_key > 全局 .env。
    base_url 规范化：自动去除尾部 /models（OpenAI 客户端自动拼 /chat/completions）。"""
    _admin_user(authorization)
    st = _get_aimodel()
    import time
    from openai import OpenAI

    m = None
    base_url = (req.base_url or "").strip()
    api_key = (req.api_key or "").strip()
    if req.model_id and not base_url:
        m = st.get_model(req.model_id)
        if not m:
            raise HTTPException(status_code=400, detail="模型不存在，且未提供 base_url")
        base_url = m.get("base_url", "")
    if not base_url:
        raise HTTPException(status_code=400, detail="未提供 Base URL，无法测试")
    base_url = st._norm_base_url(base_url)
    if not api_key:
        # 依次：注册表保存的明文密钥 → 全局 .env
        if m and m.get("api_key"):
            api_key = m["api_key"]
        else:
            api_key = store._GLOBAL.api_key
    if not api_key or api_key.startswith("sk-请") or "****" in api_key:
        raise HTTPException(status_code=400, detail="未配置有效 API_KEY，无法测试（请在模型里填写该通道的密钥）")
    key_source = ("表单密钥" if (req.api_key or "").strip()
                  else ("模型密钥" if (m and m.get("api_key")) else "全局密钥"))

    client = OpenAI(api_key=api_key, base_url=base_url,
                    timeout=min(store._GLOBAL.llm_timeout, 60), max_retries=0)
    t0 = time.time()
    try:
        # max_tokens=128：思考型模型的推理会消耗额度，太小正文为空
        resp = client.chat.completions.create(
            model=req.model_id, max_tokens=128,
            messages=[{"role": "user", "content": "请只回复：OK"}])
        lat = round(time.time() - t0, 2)
        # 兼容部分代理返回字符串/空结构而非标准对象
        try:
            content = (resp.choices[0].message.content or "")[:40]
        except (AttributeError, TypeError, IndexError):
            content = str(resp)[:40]
        # 反假阳性1：网关 200 却返回网页（Base URL 指向网站而非 API）
        if content.lstrip().lower().startswith(("<!doctype", "<html")):
            return {"ok": False, "model": req.model_id, "latency": lat,
                    "error": "通道返回网页而非模型回复：Base URL 可能指向网站页面而非 OpenAI 兼容 API",
                    "base_url": base_url, "key_source": key_source}
        # 反假阳性2：通道把错误提示当正文返回（限流/上游故障）
        if content.lstrip().startswith("⚠") or "所有模型均不可用" in content:
            return {"ok": False, "model": req.model_id, "latency": lat,
                    "error": f"通道返回错误提示而非模型回复（多为限流/上游故障，稍后重试）：{content.strip()[:60]}",
                    "base_url": base_url, "key_source": key_source}
        return {"ok": True, "model": req.model_id, "latency": lat,
                "reply": content, "base_url": base_url,
                "key_source": key_source}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "model": req.model_id,
                "latency": round(time.time() - t0, 2),
                "error": _aim_error_msg(e, req.model_id), "base_url": base_url,
                "key_source": key_source}


@app.post("/admin/ai-models/test-all")
async def admin_ai_model_test_all(authorization: str = Header(default="")):
    """批量测速全部启用模型（并发 4），用于"一键全部测速"。
    返回 [{model_id, ok, latency, error}]，按延迟升序。"""
    _admin_user(authorization)
    import asyncio
    import time
    from openai import OpenAI

    st = _get_aimodel()
    mods = st.list_models()
    enabled = [m for m in mods if m["enabled"]]
    if not enabled:
        raise HTTPException(status_code=400, detail="没有已启用的模型")

    import threading
    sem = threading.Semaphore(4)  # 并发上限 4：网关按密钥限 RPM，全量并发易触发 429

    def _one(m: dict) -> dict:
        # ⚠️ list_models 返回脱敏密钥，必须 get_model 取明文，否则含 **** 被判未配置
        raw = st.get_model(m["model_id"]) or m
        base_url = st._norm_base_url(raw.get("base_url") or "")
        api_key = (raw.get("api_key") or "").strip() or store._GLOBAL.api_key
        if not base_url or not api_key or api_key.startswith("sk-请"):
            return {"model_id": m["model_id"], "ok": False, "latency": 0,
                    "error": "未配置 Base URL / API Key"}
        client = OpenAI(api_key=api_key, base_url=base_url,
                        timeout=45, max_retries=0)
        t0 = time.time()
        try:
            with sem:
                # max_tokens=128：思考型模型推理耗额度，太小正文为空
                resp = client.chat.completions.create(
                    model=m["model_id"], max_tokens=128,
                    messages=[{"role": "user", "content": "请只回复：OK"}])
            lat = round(time.time() - t0, 2)
            try:
                content = (resp.choices[0].message.content or "")[:40]
            except (AttributeError, TypeError, IndexError):
                content = str(resp)[:40]
            # 反假阳性：200 但返回网页 = Base URL 指向网站而非 API
            if content.lstrip().lower().startswith(("<!doctype", "<html")):
                return {"model_id": m["model_id"], "ok": False, "latency": lat,
                        "error": "通道返回网页而非模型回复（Base URL 非 API 端点）"}
            # 反假阳性：通道把错误提示当正文返回（限流/上游故障）
            if content.lstrip().startswith("⚠") or "所有模型均不可用" in content:
                return {"model_id": m["model_id"], "ok": False, "latency": lat,
                        "error": f"通道返回错误提示（多为限流/上游故障，稍后重试）：{content.strip()[:60]}"}
            return {"model_id": m["model_id"], "ok": True,
                    "latency": lat, "error": "", "reply": content}
        except Exception as e:  # noqa: BLE001
            return {"model_id": m["model_id"], "ok": False,
                    "latency": round(time.time() - t0, 2),
                    "error": _aim_error_msg(e, m["model_id"])}

    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, _one, m) for m in enabled]
    results = await asyncio.gather(*tasks)
    results.sort(key=lambda r: (not r["ok"], r["latency"]))
    ok_n = sum(1 for r in results if r["ok"])
    return {"results": results, "ok_count": ok_n, "total": len(results)}


# ================================================================
# FastAPI startup：确保 admin 账号存在
# ================================================================
@app.on_event("startup")
def on_startup():
    import os
    if os.environ.get("ADMIN_USERNAME"):
        uid = auth_store.ensure_admin()
        if uid:
            print(f"[C2] admin 账号已初始化: {os.environ.get('ADMIN_USERNAME')}")
        else:
            print("[C2] ADMIN_USERNAME 已配置但密码为空，跳过 admin 初始化")
    autocollect_mod.start()


# ================================================================
# C3 · 运营数据看板路由（只读聚合，复用 stats_biz）
# 权限：双通道 —— ① X-Admin-Secret 头匹配 ADMIN_SECRET（任务书硬性契约）
#                  ② admin Bearer（C2 已上线，无缝切换，接口语义不变）
# 匿名/普通用户一律 401/403。
# ================================================================

def _admin_secret() -> str:
    """读取 ADMIN_SECRET 环境变量（未配置返回空串=关闭 X-Admin-Secret 通道）。"""
    import os
    return os.environ.get("ADMIN_SECRET", "").strip()


def _require_admin(authorization: str = "", x_admin_secret: str = ""):
    """运营看板鉴权：任一通道通过即可。
    - 通道① X-Admin-Secret == ADMIN_SECRET（任务书最简自洽方案）
    - 通道② Bearer token 为 admin 角色（C2 已上线，复用 _admin_user）
    两通道都失败 → 401/403。
    """
    secret = _admin_secret()
    if secret and x_admin_secret and hmac.compare_digest(x_admin_secret, secret):
        return {"source": "secret"}
    return _admin_user(authorization)


@app.get("/admin/stats/overview")
def admin_stats_overview(authorization: str = Header(default=""),
                         x_admin_secret: str = Header(default="")):
    """运营总览：总用户/会员数/今日新增/今日 ask/拦截/耗时。"""
    _require_admin(authorization, x_admin_secret)
    return stats_biz.overview(
        auth_store.db_path, chat_store.db_path,
        metrics.snapshot(), guard_counter.stats())


@app.get("/admin/stats/quota")
def admin_stats_quota(authorization: str = Header(default=""),
                      x_admin_secret: str = Header(default="")):
    """额度消耗分布（按日/用户）。"""
    _require_admin(authorization, x_admin_secret)
    return stats_biz.quota_dist(auth_store.db_path)


@app.get("/admin/stats/revenue")
def admin_stats_revenue(authorization: str = Header(default=""),
                        x_admin_secret: str = Header(default="")):
    """营收聚合（依赖 C1 pay.db；C1 未上线 → enabled=false 优雅降级）。"""
    _require_admin(authorization, x_admin_secret)
    return stats_biz.revenue(payment_store.db_path)


@app.get("/admin/stats/teachers")
def admin_stats_teachers(authorization: str = Header(default=""),
                         x_admin_secret: str = Header(default="")):
    """老师维度请求/拦截/满意度（热度与 /metrics by_teacher 一致）。"""
    _require_admin(authorization, x_admin_secret)
    return stats_biz.teacher_stats(
        metrics.snapshot(), guard_counter.stats(), cfg.list_teachers_all())


@app.get("/admin/stats/export")
def admin_stats_export(authorization: str = Header(default=""),
                       x_admin_secret: str = Header(default="")):
    """核心指标 CSV 导出（UTF-8 with BOM，Excel 打开不乱码）。"""
    _require_admin(authorization, x_admin_secret)
    csv_text = stats_biz.export_csv(
        auth_store.db_path, chat_store.db_path, payment_store.db_path,
        metrics.snapshot(), guard_counter.stats(), cfg.list_teachers_all())
    import time as _t
    return PlainTextResponse(
        csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="gk-stats-{_t.strftime("%Y%m%d-%H%M%S")}.csv"',
        },
    )


# ---------- #18 激励体系管理（配置/统计/排行榜/重算） ----------
class IncentiveConfigReq(BaseModel):
    """激励配置请求体：总开关 + 各事件积分（非法键/越界值自动忽略）。"""
    enabled: bool = True
    event_points: dict = {}


class IncentiveRecomputeReq(BaseModel):
    """激励重算请求体：user_id 留空 = 全部用户。"""
    user_id: str = ""


@app.get("/admin/incentive/stats")
def admin_incentive_stats(authorization: str = Header(default="")):
    """激励体系总览：配置 + 全局统计 + 积分榜 TOP20。"""
    _admin_user(authorization)
    return {
        "config": incentive_store.get_config(),
        "stats": incentive_store.admin_stats(),
        "board": incentive_store.board(limit=20),
    }


@app.post("/admin/incentive/config")
def admin_incentive_config(
    req: IncentiveConfigReq = Body(default_factory=IncentiveConfigReq),
    authorization: str = Header(default=""),
):
    """更新激励配置：总开关 + 各事件积分。"""
    _admin_user(authorization)
    return incentive_store.set_config(req.enabled, req.event_points)


@app.post("/admin/incentive/recompute")
def admin_incentive_recompute(
    req: IncentiveRecomputeReq = Body(default_factory=IncentiveRecomputeReq),
    authorization: str = Header(default=""),
):
    """激励数据全量重算：按 practice_logs 时间序幂等重放（管理员手动纠偏）。
    user_id 非空 = 仅重算该用户；留空 = 全部用户。"""
    _admin_user(authorization)
    logs = study_store.all_practice_logs()
    by_user: dict[str, list[dict]] = {}
    for log in logs:
        by_user.setdefault(log["user_id"], []).append(log)
    results = []
    for uid, ulogs in by_user.items():
        if req.user_id and uid != req.user_id:
            continue
        results.append(incentive_store.recompute_user(uid, ulogs, study_store))
    return {"recomputed": len(results), "results": results}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("poc.api:app", host="0.0.0.0", port=9000, reload=False)


# ================================================================
# C5 · P3 视频转写（预留接口，阻塞中不实现）
# 现状（docs/项目状态总览.md §六）：中转站无 whisper/audio 通道、服务器无
# ffmpeg、2C/3.6G 跑不动本地 ASR → 死胡同。此处仅预留 OpenAI 兼容 audio 接口
# 契约，待上游（中转站）暴露 whisper 后只需填 base_url/模型名即可启用。
# 启用条件：环境变量 WHISPER_ENABLED=1 + WHISPER_BASE_URL/WHISPER_API_KEY/WHISPER_MODEL。
# ================================================================
import os as _os_whisper


@app.get("/audio/transcriptions/status")
def audio_status():
    """转写能力状态（前端据此显示/隐藏视频上传入口）。"""
    enabled = (
        _os_whisper.getenv("WHISPER_ENABLED", "").strip() == "1"
        and bool(_os_whisper.getenv("WHISPER_API_KEY", "").strip())
    )
    return {
        "enabled": enabled,
        "note": "上游中转站暴露 whisper 后可启用；当前 2C/3.6G 不做本地 ASR（OOM 风险）",
    }


@app.post("/audio/transcriptions")
async def audio_transcriptions(request: Request):
    """OpenAI 兼容 audio/transcriptions 预留桩（multipart: file + model）。
    未启用 → 501 明确报错；启用 → 转发上游 OpenAI 兼容端点。
    """
    if not (_os_whisper.getenv("WHISPER_ENABLED", "").strip() == "1"):
        raise HTTPException(
            status_code=501,
            detail="视频转写未启用：上游无 whisper 通道且服务器资源不足。"
                   "详见 docs/项目状态总览.md §六（P3 阻塞项）。",
        )
    # ── 启用分支（上游就绪后无需改此桩，填环境变量即可） ──
    from openai import OpenAI
    wc = OpenAI(
        api_key=_os_whisper.getenv("WHISPER_API_KEY", ""),
        base_url=_os_whisper.getenv("WHISPER_BASE_URL", store._GLOBAL.api_base_url),
        timeout=float(_os_whisper.getenv("WHISPER_TIMEOUT", "300")),
        max_retries=2,
    )
    form = await request.form()
    f = form.get("file")
    model = form.get("model") or _os_whisper.getenv("WHISPER_MODEL", "whisper-1")
    if f is None:
        raise HTTPException(status_code=400, detail="缺少 file 字段")
    raw = await f.read()
    if len(raw) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="视频文件超过 100MB 上限")
    resp = wc.audio.transcriptions.create(model=model, file=("audio", raw, f.content_type or "audio/mpeg"))
    return {"text": resp.text}
