# -*- coding: utf-8 -*-
"""自动采集管理路由（/admin/autocollect/*）：状态/触发/单通道/多通道/LLM 通道管理/
日志/配置/历史/通知/试卷队列/套卷标记 + Webhook 联通测试。

路由按原声明顺序保留以维持 FastAPI 路径匹配（int idx vs /test 等）。
"""

from fastapi import APIRouter, Body, Header, HTTPException, Query
from pydantic import BaseModel

from .. import deps as _deps
from .. import autocollect as autocollect_mod
from ..models import list_remote_models

router = APIRouter()


@router.get("/admin/autocollect/status")
def admin_autocollect_status(authorization: str = Header(default="")):
    """自动采集守候状态：是否启用 / 上次运行时间与结果摘要。"""
    _deps._admin_user(authorization)
    return autocollect_mod.status()


@router.post("/admin/autocollect/run")
def admin_autocollect_run(authorization: str = Header(default="")):
    """P0 异步触发一轮自动采集（后台线程执行，立即返回 started，结果轮询 status）。"""
    _deps._admin_user(authorization)
    return autocollect_mod.trigger_run(retry_failed=False)


@router.post("/admin/autocollect/retry-failed")
def admin_autocollect_retry_failed(authorization: str = Header(default="")):
    """#12 一键补采：异步仅重跑上一轮失败的采集来源（失败 URL/文档/搜索词），
    跳过正常增量，用于失败后补采。"""
    _deps._admin_user(authorization)
    return autocollect_mod.trigger_run(retry_failed=True)


class AutocollectLlmReq(BaseModel):
    model: str = ""
    base_url: str = ""
    api_key: str = ""


@router.get("/admin/autocollect/llm")
def admin_autocollect_llm_get(authorization: str = Header(default="")):
    """自动采集专用模型通道只读视图（API_KEY 脱敏）。"""
    _deps._admin_user(authorization)
    return autocollect_mod.llm_channel_info()


@router.post("/admin/autocollect/llm")
def admin_autocollect_llm_set(
    req: AutocollectLlmReq = Body(default_factory=AutocollectLlmReq),
    authorization: str = Header(default=""),
):
    """配置自动采集专用模型通道；api_key 留空 = 保持已存密钥。"""
    _deps._admin_user(authorization)
    try:
        ch = autocollect_mod.save_llm_channel(req.model, req.base_url, req.api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, **ch}


@router.post("/admin/autocollect/llm/clear")
def admin_autocollect_llm_clear(authorization: str = Header(default="")):
    """清除专用通道（回落 AUTOCOLLECT_* / 平台默认模型）。"""
    _deps._admin_user(authorization)
    autocollect_mod.clear_llm_channel()
    return {"ok": True}


@router.post("/admin/autocollect/llm/models")
def admin_autocollect_llm_models(authorization: str = Header(default="")):
    """用已配置的采集专用通道拉取远端模型列表（服务端持有完整 API Key，前端无需回传）。

    未配置专用通道时返回 ok=False 且不兜底；通道网络失败时由 list_remote_models
    自动兜底内置目录（used_fallback=true）。
    """
    _deps._admin_user(authorization)
    ch = autocollect_mod.load_llm_channel()
    base_url = (ch.get("base_url") or "").strip()
    api_key = (ch.get("api_key") or "").strip()
    if not base_url:
        return {
            "ok": False,
            "models": [],
            "error": "尚未配置自动采集专用通道；请先在「AI 模型管理」面板配置 Base URL 与模型后再拉取",
            "used_fallback": False,
            "base_url": "",
        }
    return list_remote_models(base_url=base_url, provider="", api_key=api_key)


# ---- #40 自动采集多通道（多个平台/模型共存） ----

@router.get("/admin/autocollect/llm/channels")
def admin_autocollect_llm_channels_list(authorization: str = Header(default="")):
    """多平台专用通道列表（每条 API_KEY 脱敏）。"""
    _deps._admin_user(authorization)
    return autocollect_mod.llm_channels_info()


@router.get("/admin/autocollect/llm/channels/stats")
def admin_autocollect_llm_channels_stats(authorization: str = Header(default="")):
    """通道使用统计：每条通道成功/失败/平均延迟/最近成功时间 + 失活标红提示。"""
    _deps._admin_user(authorization)
    return autocollect_mod.channel_stats()


class AutocollectChannelReq(BaseModel):
    model: str = ""
    base_url: str = ""
    api_key: str = ""


@router.post("/admin/autocollect/llm/channels")
def admin_autocollect_llm_channels_add(
    req: AutocollectChannelReq = Body(default_factory=AutocollectChannelReq),
    authorization: str = Header(default=""),
):
    """追加一个专用通道（多模型共存）；api_key 空用全局密钥。"""
    _deps._admin_user(authorization)
    try:
        chs = autocollect_mod.add_llm_channel(req.model, req.base_url, req.api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return autocollect_mod.llm_channels_info()


@router.put("/admin/autocollect/llm/channels/{idx}")
def admin_autocollect_llm_channels_update(
    idx: int,
    req: AutocollectChannelReq = Body(default_factory=AutocollectChannelReq),
    authorization: str = Header(default=""),
):
    """更新第 idx 个专用通道；字段留空保持原值（api_key 留空保持原密钥）。"""
    _deps._admin_user(authorization)
    try:
        autocollect_mod.update_llm_channel(
            idx, req.model, req.base_url, req.api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return autocollect_mod.llm_channels_info()


@router.delete("/admin/autocollect/llm/channels/{idx}")
def admin_autocollect_llm_channels_delete(
    idx: int,
    authorization: str = Header(default=""),
):
    """删除第 idx 个专用通道。"""
    _deps._admin_user(authorization)
    try:
        autocollect_mod.delete_llm_channel(idx)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return autocollect_mod.llm_channels_info()


@router.post("/admin/autocollect/llm/channels/{idx}/test")
def admin_autocollect_llm_channels_test(
    idx: int,
    authorization: str = Header(default=""),
):
    """一键测活第 idx 个专用通道（最小 chat 请求，验证连通性与密钥）。"""
    _deps._admin_user(authorization)
    try:
        return autocollect_mod.test_llm_channel(idx)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/admin/autocollect/llm/channels/test")
def admin_autocollect_llm_channels_test_all(authorization: str = Header(default="")):
    """一键测活全部专用通道，返回逐条结果与汇总（路由须注册在 /{idx} 语义之外，
    FastAPI 按声明顺序匹配，"test" 不会与 int 路径参数冲突）。"""
    _deps._admin_user(authorization)
    return autocollect_mod.test_llm_channels_all()


@router.get("/admin/autocollect/logs")
def admin_autocollect_logs(
    limit: int = Query(80, ge=1, le=300),
    authorization: str = Header(default=""),
):
    """实时任务日志（进程内存环形缓冲，最新在前）；模型做的每个采集任务都实时滚动展示。"""
    _deps._admin_user(authorization)
    return {"logs": autocollect_mod.recent_task_logs(limit)}


class AutocollectConfigReq(BaseModel):
    enabled: bool | None = None
    interval_secs: int | None = None
    cpu_max: int | None = None
    mem_max: int | None = None
    max_per_cycle: int | None = None
    max_site_pages: int | None = None
    threads: int | None = None
    teachers: list[str] = []
    urls: list[str] = []
    search_queries: list[str] = []
    site_whitelist: list[str] = []   # saA 站点白名单信任域（直采 + 搜索豁免阻断）
    site_pages_map: dict[str, int] = {}   # saE 逐站页数额度差异化：{url或域名: 页数}
    fallback_models: list[str] = []
    autonomous: bool | None = None
    autonomous_queries: int | None = None
    schedule_enabled: bool | None = None
    schedule_start: int | None = None
    schedule_end: int | None = None
    retry_failed_auto: bool | None = None
    site_rules: list[dict] = []
    llm_budget_chars: int | None = None
    llm_price_per_1m: float | None = None
    round_budget_secs: int | None = None   # 轮次墙钟上限（秒，0=不限）
    # 采集质量增强配置：来源优先级 + AI 自评抽样 + Webhook 通知
    priorities: dict[str, str] = {}
    quality_sample_rate: float | None = None
    quality_rules: str = ""
    webhook_url: str = ""
    notify_on: list[str] = []
    fail_alert_threshold: int | None = None


@router.get("/admin/autocollect/config")
def admin_autocollect_config_get(authorization: str = Header(default="")):
    """自动采集 runtime 配置视图（生效值 + 老师候选 + 通道脱敏）。"""
    _deps._admin_user(authorization)
    return autocollect_mod.config_admin()


@router.put("/admin/autocollect/config")
def admin_autocollect_config_put(
    req: AutocollectConfigReq = Body(default_factory=AutocollectConfigReq),
    authorization: str = Header(default=""),
):
    """保存自动采集 runtime 配置（实时生效，无需重建容器）。"""
    _deps._admin_user(authorization)
    try:
        return autocollect_mod.save_config(req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/admin/autocollect/history")
def admin_autocollect_history(limit: int = 50,
                              authorization: str = Header(default="")):
    """自动采集运行历史（时间倒序，仅真实执行轮次）。"""
    _deps._admin_user(authorization)
    return {"runs": autocollect_mod.history(limit)}


@router.post("/admin/autocollect/notify-test")
def admin_autocollect_notify_test(authorization: str = Header(default="")):
    """向已配置的 webhook 发送一条测试通知，验证连通性。"""
    _deps._admin_user(authorization)
    return autocollect_mod.test_notify()


@router.get("/admin/autocollect/papers")
def admin_autocollect_papers(limit: int = Query(100, ge=1, le=2000),
                             authorization: str = Header(default="")):
    """试卷作业队列：当前在采的那张卷 + 待采明细 + 已阻塞明细。

    整卷串行采集的主视图：一张试卷片段全部跑完才入库，未采完的下轮从游标续采；
    连续多轮无进展的卷标 blocked（cursor/total/stall 说明卡在哪里）。"""
    _deps._admin_user(authorization)
    return autocollect_mod.paper_jobs_admin(limit=limit)


@router.get("/admin/autocollect/sets")
def admin_autocollect_sets(limit: int = Query(200, ge=1, le=2000),
                           url: str = Query(""),
                           authorization: str = Header(default="")):
    """套卷采集标记列表：每套题目链接的采集状态（complete/empty/incomplete）。
    传 url 精确查单页标记；否则返回最近 limit 条 + 汇总。"""
    _deps._admin_user(authorization)
    return autocollect_mod.collected_sets_admin(url=url, limit=limit)


@router.get("/admin/autocollect/sets/questions")
def admin_autocollect_set_questions(url: str = Query(..., min_length=8),
                                    authorization: str = Header(default="")):
    """套卷溯源回查：按采集来源页 URL 列出本站已入库的同套题目。
    采集题出现问题时直接调本网站定位原因（看题干/答案/解析/配图入库结果）。"""
    _deps._admin_user(authorization)
    rows = _deps.study_store.find_questions_by_source_url(url)
    mark = (autocollect_mod.collected_sets_admin(url=url) or {}).get("mark")
    return {"url": url, "mark": mark, "count": len(rows), "questions": rows}


class AutocollectSetsResetReq(BaseModel):
    urls: list[str] = []   # 空 = 清空全部套卷标记


@router.post("/admin/autocollect/sets/reset")
def admin_autocollect_sets_reset(req: AutocollectSetsResetReq = Body(
        default_factory=AutocollectSetsResetReq),
        authorization: str = Header(default="")):
    """重置套卷「已采集完整」标记，使对应链接下次采集重新提取（修复后重采入口）。"""
    _deps._admin_user(authorization)
    return autocollect_mod.reset_collected_sets(req.urls or None)