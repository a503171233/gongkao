# -*- coding: utf-8 -*-
"""AI 模型管理路由（#25）：注册表 CRUD、默认模型、内置目录、远程模型拉取、
单模型/批量连通性测速。

{model_id:path} 为贪婪路径参数，静态路径（builtin/list-remote/effective/test/
test-all）必须声明在 GET/PATCH/DELETE {model_id:path} 之前，本文件保留原顺序。
"""

from fastapi import APIRouter, Body, Header, HTTPException
from pydantic import BaseModel

from .. import deps as _deps
from .. import store
from ..models import (
    get_ai_model_store as _get_aimodel,
    builtin_catalog,
    list_remote_models,
)

router = APIRouter()


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


@router.get("/admin/ai-models")
def admin_ai_models(authorization: str = Header(default="")):
    """AI 模型注册表列表。"""
    _deps._admin_user(authorization)
    return {"models": _get_aimodel().list_models()}


@router.post("/admin/ai-models")
def admin_ai_model_create(req: AiModelCreateReq = Body(default_factory=AiModelCreateReq),
                          authorization: str = Header(default="")):
    """新增模型。"""
    _deps._admin_user(authorization)
    if not req.name.strip() or not req.model_id.strip():
        raise HTTPException(status_code=400, detail="名称与模型标识不能为空")
    try:
        m = _get_aimodel().create_model(
            req.name, req.model_id, req.provider, req.base_url, req.api_key,
            req.enabled, req.temperature, int(req.max_tokens or 2000), req.remark)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"model": m}


@router.get("/admin/ai-models/builtin")
def admin_ai_models_builtin(authorization: str = Header(default="")):
    """内置模型目录（OpenAI 兼容协议；含已注册标记，兜底展示用）。"""
    _deps._admin_user(authorization)
    return {"models": builtin_catalog()}


@router.post("/admin/ai-models/list-remote")
def admin_ai_models_list_remote(
    base_url: str = Body(default=""),
    provider: str = Body(default=""),
    api_key: str = Body(default=""),
    authorization: str = Header(default=""),
):
    """获取远程模型列表（GET {base_url}/models，OpenAI 兼容协议）。

    失败自动兜底内置目录：used_fallback=true + error 说明；前端据此提示并降级。
    """
    _deps._admin_user(authorization)
    return list_remote_models(base_url=base_url, provider=provider, api_key=api_key)


@router.patch("/admin/ai-models/{model_id:path}")
def admin_ai_model_update(model_id: str,
                          req: AiModelUpdateReq = Body(default_factory=AiModelUpdateReq),
                          authorization: str = Header(default="")):
    """更新模型字段。"""
    _deps._admin_user(authorization)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    st = _get_aimodel()
    m = st.update_model(model_id, **fields)
    if not m:
        raise HTTPException(status_code=404, detail="模型不存在")
    return {"model": m}


@router.delete("/admin/ai-models/{model_id:path}")
def admin_ai_model_delete(model_id: str, authorization: str = Header(default="")):
    """删除模型（默认模型同时被清除，effective 回退 .env）。"""
    _deps._admin_user(authorization)
    st = _get_aimodel()
    m = st.get_model(model_id)
    if not m:
        raise HTTPException(status_code=404, detail="模型不存在")
    if m.get("is_default"):
        st.set_setting("default_model", "")
    st.delete_model(model_id)
    return {"deleted": model_id}


@router.post("/admin/ai-models/{model_id:path}/default")
def admin_ai_model_set_default(model_id: str, authorization: str = Header(default="")):
    """设为全局默认模型（平台级 AI：题库采集/抽取/知识建树等）。"""
    _deps._admin_user(authorization)
    st = _get_aimodel()
    try:
        m = st.set_default(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    st.set_setting("default_model", model_id)
    return {"default": m}


@router.get("/admin/ai-models/effective")
def admin_ai_model_effective(authorization: str = Header(default="")):
    """当前平台级 AI 生效配置（AI模型管理页顶部卡片）。api_key 脱敏后返回。"""
    _deps._admin_user(authorization)
    eff = dict(_get_aimodel().effective_default())
    eff["api_key"] = _get_aimodel()._mask_key(eff.get("api_key", ""))
    return {"effective": eff}


@router.get("/admin/ai-models/{model_id:path}")
def admin_ai_model_detail(model_id: str, authorization: str = Header(default="")):
    """模型详情（含完整 api_key，供编辑弹窗回显）。"""
    _deps._admin_user(authorization)
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


@router.post("/admin/ai-models/test")
def admin_ai_model_test(req: AiModelTestReq = Body(default_factory=AiModelTestReq),
                        authorization: str = Header(default="")):
    """连通性测试：对给定模型做一次极短补全，返回状态/延迟/内容摘要。
    密钥解析优先级：请求体显式 api_key > 注册表保存的 api_key > 全局 .env。
    base_url 规范化：自动去除尾部 /models（OpenAI 客户端自动拼 /chat/completions）。"""
    _deps._admin_user(authorization)
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


@router.post("/admin/ai-models/test-all")
async def admin_ai_model_test_all(authorization: str = Header(default="")):
    """批量测速全部启用模型（并发 4），用于"一键全部测速"。
    返回 [{model_id, ok, latency, error}]，按延迟升序。"""
    _deps._admin_user(authorization)
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