# -*- coding: utf-8 -*-
"""共享状态、常量、工具函数、Pydantic 模型与中间件函数。

本模块是 RAG 内核服务的"共享层"：所有模块级单例（store 实例）、运行时常量、
公共请求体模型与无 app 依赖的 helper 都集中在这里，由 app.py 构建 FastAPI
实例时复用，路由模块通过 `from .deps import ...` 引用同一批单例。
"""
import json
import os as _os
import threading
import time as _time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .auth import AuthStore
from .config import Config
from .gitee import GiteeOAuth
from . import store
from .answer import ask as rag_ask  # noqa: F401  (ask 路由使用)
from .chat import ChatStore
from .guard import guard_counter
from .metrics import metrics
from .payment import get_payment_store
from .sensitive import check_sensitive, SENSITIVE_ENABLED  # noqa: F401
from .study import get_study_store
from .incentive import get_incentive_store as _get_incentive
from .mockexam import get_mockexam_store as _get_mockexam
from .smartexam import get_smartexam_store as _get_smartexam
from .forum import get_forum_store as _get_forum
from .tracking import get_tracking_store as _get_tracking
from .banner import get_banner_store as _get_banner
from .messages import get_message_store as _get_messages

# ---------------------------------------------------------------------------
# 配置与业务 store 单例
# ---------------------------------------------------------------------------
cfg = Config()
chat_store = ChatStore()
auth_store = AuthStore()
gitee_oauth = GiteeOAuth()  # 未配置 GITEE_CLIENT_ID/SECRET → enabled=False
payment_store = get_payment_store()
study_store = get_study_store()
incentive_store = _get_incentive()
mockexam_store = _get_mockexam()
smartexam_store = _get_smartexam()
forum_store = _get_forum()
tracking_store = _get_tracking()
banner_store = _get_banner()
message_store = _get_messages()

# ---------------------------------------------------------------------------
# 运行时常量
# ---------------------------------------------------------------------------
# Gitee OAuth state 防 CSRF：{state: 过期时间戳}
_oauth_states: dict[str, float] = {}
_STATE_TTL = int(_os.environ.get("OAUTH_STATE_TTL", "600"))  # 10 分钟

HISTORY_LIMIT = int(_os.environ.get("HISTORY_LIMIT", "10"))  # 注入 LLM 的历史轮数上限

# ---------- C5 安全加固：登录/注册限流（进程内滑动窗口，防暴力破解） ----------
_LOGIN_LIMIT = int(_os.environ.get("LOGIN_LIMIT", "10"))        # 每 IP 每窗口最多尝试次数
_LOGIN_WINDOW = int(_os.environ.get("LOGIN_WINDOW", "300"))     # 窗口 5 分钟
_login_attempts: dict[str, list[float]] = {}   # ip -> [timestamps]
_login_lock = threading.Lock()


def _login_rate_limit(ip: str):
    """登录/注册限流：仅统计窗口内失败尝试（成功登录不计数，避免共享出口 IP/巡检误伤）。
    超限抛 429，旧时间戳惰性清理。失败计数见 _login_fail_record。"""
    now = _time.monotonic()
    with _login_lock:
        ts_list = _login_attempts.setdefault(ip, [])
        # 清理窗口外的时间戳
        _login_attempts[ip] = [t for t in ts_list if now - t < _LOGIN_WINDOW]
        if len(_login_attempts[ip]) >= _LOGIN_LIMIT:
            raise HTTPException(status_code=429, detail="尝试过于频繁，请 5 分钟后再试")


def _login_fail_record(ip: str):
    """记录一次失败登录/注册尝试（防暴力破解计数；成功登录不计数）。"""
    with _login_lock:
        _login_attempts.setdefault(ip, []).append(_time.monotonic())


def _client_ip(request: Request) -> str:
    """取客户端 IP（优先 X-Real-IP，nginx 已设；回退 remote）。"""
    xff = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------- C5 安全加固：CSRF 纵深防御（Origin 校验中间件） ----------
_CSRF_ALLOWED_ORIGINS = {
    o.strip().rstrip("/") for o in _os.environ.get("CSRF_ALLOWED_ORIGINS", "").split(",") if o.strip()
}
_CSRF_MUTATING = {"POST", "PUT", "DELETE", "PATCH"}

# ---------- 密保答错限流（进程内滑动窗口，防暴力枚举密保答案） ----------
_SECURITY_LIMIT = int(_os.environ.get("SECURITY_LIMIT", "5"))       # 每用户名每窗口最多尝试 5 次
_SECURITY_WINDOW = int(_os.environ.get("SECURITY_WINDOW", "600"))   # 窗口 10 分钟
_security_attempts: dict[str, list[float]] = {}
_security_lock = threading.Lock()


def _security_rate_limit(key: str):
    """密保校验限流：超限抛 429。"""
    now = _time.monotonic()
    with _security_lock:
        ts_list = _security_attempts.setdefault(key, [])
        _security_attempts[key] = [t for t in ts_list if now - t < _SECURITY_WINDOW]
        if len(_security_attempts[key]) >= _SECURITY_LIMIT:
            raise HTTPException(status_code=429, detail="尝试过于频繁，请 10 分钟后再试")
        _security_attempts[key].append(now)


# ---------------------------------------------------------------------------
# 公共 Pydantic 模型
# ---------------------------------------------------------------------------
class AskReq(BaseModel):
    query: str = Field(..., max_length=500, description="问题内容，≤500 字")
    teacher_id: str = "T001"
    stream: bool = False
    session_id: str = ""


class RegisterReq(BaseModel):
    username: str
    password: str


class LoginReq(BaseModel):
    username: str
    password: str


class ForgotQuestionReq(BaseModel):
    username: str


class ForgotAnswerReq(BaseModel):
    username: str
    answer: str


class ForgotResetReq(BaseModel):
    ticket: str
    new_password: str


class SetSecurityReq(BaseModel):
    question: str
    answer: str


# ---------------------------------------------------------------------------
# 公共工具函数
# ---------------------------------------------------------------------------
def _safe_count(teacher_id: str) -> int:
    try:
        return store.collection_count(teacher_id)
    except Exception:
        return 0


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _check_teacher(teacher_id: str):
    try:
        return cfg.get_teacher(teacher_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"老师不存在: {teacher_id}")


def _resolve_user(authorization: str) -> dict:
    """解析 Authorization → 用户 dict；无 token 返回 None（匿名可用，不限额度）。"""
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    if not token:
        return None
    return auth_store.user_from_token(token)


def _session_owner_guard(sess: dict | None, user: dict | None) -> None:
    """会话归属校验（越权防护）：
    匿名会话（owner=anonymous）公开可读；登录用户的会话仅本人可读/删。
    """
    if sess is None:
        return
    owner = sess.get("user_id") or "anonymous"
    if owner != "anonymous" and (user is None or user["user_id"] != owner):
        raise HTTPException(status_code=403, detail="无权访问该会话")


def _quota_guard(user: dict | None):
    """登录用户才计额度；无 token 匿名放行（不打断验收）。"""
    if user is None:
        return
    if not auth_store.check_quota(user["user_id"], user["role"]):
        raise HTTPException(status_code=429,
                            detail="今日免费额度已用完，请升级会员或明日再来")


# ---------- F3 论坛写操作频控（进程内冷却） ----------
_forum_write_cooldown: dict[str, float] = {}
_forum_write_lock = threading.Lock()


def _forum_write_ok(user_id: str, gap: float) -> bool:
    with _forum_write_lock:
        last = _forum_write_cooldown.get(user_id, 0.0)
        if _time.time() - last < gap:
            return False
        _forum_write_cooldown[user_id] = _time.time()
        return True


def _forum_require_admin(user: dict) -> None:
    """管理端操作统一鉴权（治理/后台多域复用）。"""
    if (user or {}).get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")


# ---------------------------------------------------------------------------
# 中间件函数（在 app.py 中注册；此处无 app 依赖）
# ---------------------------------------------------------------------------
async def unhandled_exception(request: Request, exc: Exception):
    """全局 500 兜底：未捕获异常不再让 uvicorn 默认白屏/断连，统一 JSON + 完整日志。
    HTTPException 有专属 handler，不会走到这里。"""
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误，请稍后再试"})


async def csrf_origin_check(request: Request, call_next):
    """状态变更请求的 Origin 校验（纵深防御；不改鉴权语义）。"""
    if request.method in _CSRF_MUTATING:
        origin = request.headers.get("origin", "").strip().rstrip("/")
        if origin:
            host = request.headers.get("host", "")
            same_origin = False
            try:
                from urllib.parse import urlparse
                same_origin = (urlparse(origin).netloc == host)
            except Exception:
                same_origin = False
            if not same_origin and origin not in _CSRF_ALLOWED_ORIGINS:
                return PlainTextResponse("CSRF: Origin 校验失败", status_code=403)
    return await call_next(request)


async def http_metrics(request: Request, call_next):
    """全链路请求指标：方法/路径/状态码/耗时（说明书 §10.1）。"""
    t0 = _time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        metrics.record(request.method, request.url.path, 500)
        raise
    ms = (_time.monotonic() - t0) * 1000
    metrics.record(request.method, request.url.path, response.status_code, latency_ms=ms)
    return response