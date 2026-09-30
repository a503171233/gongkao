# -*- coding: utf-8 -*-
"""核心 RAG 路由：健康检查、老师信息、问答、会话、监控。

本模块包含 /health, /teachers, /info, /ask, /sessions, /guard/stats, /metrics。
"""

import uuid

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse

from ..deps import (
    cfg, chat_store, auth_store, HISTORY_LIMIT,
    _safe_count, _sse, _check_teacher,
    _resolve_user, _session_owner_guard, _quota_guard,
    metrics, guard_counter, AskReq,
    SENSITIVE_ENABLED, check_sensitive, rag_ask,
)

router = APIRouter()


@router.get("/health")
def health():
    """存活探针（docker healthcheck 与 nginx 轮询用）。"""
    return {"status": "ok", "teachers": [t["teacher_id"] for t in cfg.list_teachers()]}


@router.get("/health/detail")
def health_detail():
    """详情健康（C3 拓展）：LLM 被动可用度窗口 + 基础组件状态。公开只读。"""
    snap = metrics.snapshot()
    return {
        "status": "ok",
        "uptime_seconds": snap["uptime_seconds"],
        "llm": metrics.llm_health(),
        "teachers": [t["teacher_id"] for t in cfg.list_teachers()],
    }


@router.get("/teachers")
def teachers():
    return {"teachers": cfg.list_teachers()}


@router.get("/info")
def info(teacher_id: str = "T001"):
    try:
        tcfg = cfg.get_teacher(teacher_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"老师不存在: {teacher_id}")
    return {
        "teacher_id": tcfg.teacher_id,
        "teacher_name": tcfg.teacher_name,
        "teacher_subject": tcfg.teacher_subject,
        "llm_model": tcfg.llm_model,
        "embed_model": cfg.embed_model,
        "embed_provider": cfg.embed_provider,
        "chunk_count": _safe_count(teacher_id),
    }


@router.post("/ask")
def ask(req: AskReq, authorization: str = Header(default="")):
    _check_teacher(req.teacher_id)
    user = _resolve_user(authorization)
    _quota_guard(user)
    # A5 内容审核预检：提问敏感词扫描（SENSITIVE_WORDS 配置后生效）
    if SENSITIVE_ENABLED:
        hit = check_sensitive(req.query)
        if hit:
            raise HTTPException(status_code=400, detail=f"问题包含敏感词: {hit}")
    metrics.ask_started(req.teacher_id)

    # ── 会话管理：解析/创建 → 取历史(存user前) → 存user → 推理 → 存assistant ──
    sess = None
    if req.session_id:
        sess = chat_store.get_session(req.session_id)
        # 会话不存在或归属老师不符 → 重建新会话
        if sess is None or sess["teacher_id"] != req.teacher_id:
            req.session_id = ""
    if not req.session_id:
        # 登录用户会话绑定其 user_id（匿名仍用 anonymous，行为不变）
        owner = user["user_id"] if user else "anonymous"
        req.session_id = chat_store.create_session(req.teacher_id, owner)

    history = chat_store.get_messages(req.session_id, limit=HISTORY_LIMIT * 2)
    chat_store.add_message(req.session_id, "user", req.query)

    try:
        result = rag_ask(req.query, teacher_id=req.teacher_id, stream=req.stream, history=history)
    except Exception as e:
        metrics.ask_failed(str(e))
        raise HTTPException(status_code=500, detail=f"问答失败: {e}")

    if user is not None:
        auth_store.consume(user["user_id"])

    if req.stream:
        request_id = uuid.uuid4().hex[:12]

        def event_stream():
            answer_parts: list[str] = []
            blocked = False
            yield _sse("start", {"request_id": request_id, "teacher_id": req.teacher_id,
                                 "session_id": req.session_id})
            try:
                for item in result:
                    if item["type"] == "delta":
                        answer_parts.append(item["data"])
                        yield _sse("delta", {"text": item["data"]})
                    elif item["type"] == "refs":
                        yield _sse("refs", {"references": item["data"], "session_id": req.session_id})
                    elif item["type"] == "done":
                        metrics.ask_succeeded()  # C3 LLM 可用度窗口：流式完整成功
                        yield _sse("done", {"session_id": req.session_id})
                    elif item["type"] == "guard_block":
                        # 幻觉检测硬拦截：转发给前端；拦截的回答不落库为正常回复
                        blocked = True
                        yield _sse("guard_block", item.get("data", {}))
                    elif item["type"] == "guard_warn":
                        # 分级守卫 warn/confirm：内容照常，附提示条（不影响落库）
                        yield _sse("guard_warn", item.get("data", {}))
            except Exception as e:
                metrics.ask_failed(str(e))  # C3 LLM 可用度窗口：流式中途失败
                yield _sse("error", {"message": str(e)})
            finally:
                # 流式中途失败也保 user 消息；assistant 落库包裹 try 防炸断推送
                try:
                    text = "".join(answer_parts) or "（无输出）"
                    if blocked:
                        text = "（回答被幻觉检测拦截，未通过教学资料校验）"
                    chat_store.add_message(req.session_id, "assistant", text)
                except Exception:
                    pass

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲，保证真流式
                "Connection": "keep-alive",
            },
        )

    # 非流式
    metrics.ask_succeeded()  # C3 LLM 可用度窗口：非流式成功
    refs = [
        {"doc_name": h.get("meta", {}).get("doc_name", ""), "score": round(h["score"], 3)}
        for h in result.get("hits", [])
    ]
    chat_store.add_message(req.session_id, "assistant", result["answer"])
    return {
        "request_id": uuid.uuid4().hex[:12],
        "teacher_id": req.teacher_id,
        "session_id": req.session_id,
        "answer": result["answer"],
        "rejected": result.get("rejected", False),
        "references": refs,
        "guard_fails": result.get("guard_fails", []),
        "guard_level": result.get("guard_level", "pass"),
        "guard_notice": result.get("guard_notice", ""),
    }


@router.get("/sessions")
def sessions(user_id: str = "anonymous", limit: int = 50, offset: int = 0,
             authorization: str = Header(default="")):
    """某用户的会话列表（历史栏数据源）。limit/offset 分页。
    越权防护：登录用户强制查询自己的会话（忽略 user_id）；未登录仅可查匿名会话。"""
    user = _resolve_user(authorization)
    if user is not None:
        uid = user["user_id"]
    elif user_id != "anonymous":
        raise HTTPException(status_code=403, detail="无权查看他人会话")
    else:
        uid = "anonymous"
    return {
        "sessions": chat_store.list_sessions(uid, limit=limit),
        "offset": offset,
        "limit": limit,
    }


@router.get("/sessions/{session_id}/messages")
def session_messages(session_id: str, authorization: str = Header(default="")):
    """取某会话全部消息（前端刷新恢复用）。越权防护：归属校验。"""
    sess = chat_store.get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
    _session_owner_guard(sess, _resolve_user(authorization))
    return {"session_id": session_id, "teacher_id": sess["teacher_id"],
            "messages": chat_store.get_messages(session_id)}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, authorization: str = Header(default="")):
    """删除会话及其消息。越权防护：归属校验（防他人会话被匿名删除）。"""
    sess = chat_store.get_session(session_id)
    if sess is None:
        return {"deleted": session_id, "success": False}
    _session_owner_guard(sess, _resolve_user(authorization))
    chat_store.delete_session(session_id)
    return {"deleted": session_id, "success": True}


@router.get("/guard/stats")
def guard_stats():
    """幻觉检测拦截统计（运行中进程实时计数，供监控）。"""
    return guard_counter.stats()


@router.get("/metrics")
def metrics_json():
    """监控看板数据源（JSON）：uptime/请求计数/状态分布/ask统计/平均耗时/guard拦截。"""
    d = metrics.snapshot()
    d["guard"] = guard_counter.stats()
    return d


@router.get("/metrics/prometheus")
def metrics_prometheus():
    """Prometheus 文本格式（供外部抓取，如 node_exporter/prometheus）。"""
    return PlainTextResponse(metrics.prometheus_text())