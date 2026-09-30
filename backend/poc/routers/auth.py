# -*- coding: utf-8 -*-
"""认证路由：注册、登录、登出、Token 吊销、密保找回、Gitee OAuth。"""

import secrets
import time as _time

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..deps import (
    auth_store, gitee_oauth,
    _login_rate_limit, _login_fail_record, _client_ip,
    _resolve_user, _security_rate_limit,
    _oauth_states, _STATE_TTL,
    RegisterReq, LoginReq,
    ForgotQuestionReq, ForgotAnswerReq, ForgotResetReq, SetSecurityReq,
)

router = APIRouter()


# ---------- 注册 / 登录 ----------
@router.post("/register")
def register(req: RegisterReq, request: Request):
    """注册（免费用户），返回 token 和用户信息。C5 加固：IP 限流防批量注册。"""
    _login_rate_limit(_client_ip(request))
    try:
        user_id = auth_store.register(req.username, req.password)
    except ValueError as e:
        _login_fail_record(_client_ip(request))
        raise HTTPException(status_code=400, detail=str(e))
    token = auth_store._sign(user_id)
    user = auth_store.get_user(user_id)
    return {"token": token, "user_id": user_id, "username": user["username"], "role": user["role"]}


@router.post("/login")
def login(req: LoginReq, request: Request):
    """登录，返回 token 和用户信息。C5 加固：IP 限流防暴力破解。"""
    _login_rate_limit(_client_ip(request))
    try:
        token = auth_store.login(req.username, req.password)
    except ValueError as e:
        _login_fail_record(_client_ip(request))
        raise HTTPException(status_code=401, detail=str(e))
    user = auth_store.user_from_token(token)
    return {"token": token, "user_id": user["user_id"], "username": user["username"],
            "role": user["role"], "quota_left": auth_store.quota_left(user["user_id"], user["role"])}


# ---------- 个人信息 ----------
@router.get("/me")
def me(authorization: str = Header(default="")):
    """当前登录用户信息 + 剩余额度；未登录返回匿名。"""
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    user = auth_store.user_from_token(token) if token else None
    if user is None:
        return {"anonymous": True}
    # 会员状态信息（其内部 role 字段仅表会员档位，会覆盖真实 admin/member 角色，
    # 故剔除，避免 /me 把管理员误报为 free → 前端刷新校验 r.role==='admin' 恒失败）
    membership = {k: v for k, v in auth_store.get_membership_info(user["user_id"]).items()
                  if k != "role"}
    return {
        "anonymous": False,
        "username": user["username"],
        "role": user["role"],
        "quota_left": auth_store.quota_left(user["user_id"], user["role"]),
        **membership,
    }


# ---------- C5 安全加固：登出（token 吊销） ----------
@router.post("/me/logout")
def me_logout(authorization: str = Header(default="")):
    """登出：吊销当前 token，使其立即失效（持久化黑名单，重启不丢）。幂等。"""
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    if not token:
        return {"logged_out": False, "reason": "no_token"}
    user = auth_store.user_from_token(token)
    auth_store.revoke_token(token)
    return {"logged_out": True, "user_id": user["user_id"] if user else None}


@router.post("/me/logout-all")
def me_logout_all(authorization: str = Header(default="")):
    """吊销当前用户全部活跃 token（改密/设备丢失用）。
    实现：token_ver+1 → 该用户此前所有 token 因版本落后在 user_from_token
    校验失败，全部失效；重新登录后签发新版 token。"""
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    user = auth_store.user_from_token(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    new_ver = auth_store.revoke_all_tokens(user["user_id"])
    return {"logged_out_all": True, "user_id": user["user_id"], "token_ver": new_ver}


# ---------- #15 自助找回密码（密保问题通道） ----------
@router.post("/password/forgot/question")
def forgot_question(req: ForgotQuestionReq, request: Request):
    """找回第一步：按用户名取密保问题。不设密保的用户返回 can_recover=false
    （不泄露账号是否存在，统一口径）。"""
    _login_rate_limit(_client_ip(request))
    q = auth_store.get_security_question(req.username)
    if q is None:
        return {"can_recover": False, "username": req.username}
    return {"can_recover": True, "username": req.username,
            "security_question": q["security_question"],
            "premade": auth_store.PREMADE_QUESTIONS}


@router.post("/password/forgot/verify")
def forgot_verify(req: ForgotAnswerReq, request: Request):
    """找回第二步：校验密保答案 → 签发 10 分钟重置票据。"""
    _security_rate_limit(req.username)
    if not auth_store.verify_security_answer(req.username, req.answer):
        raise HTTPException(status_code=400, detail="密保答案不正确")
    ticket = auth_store.issue_reset_ticket(req.username)
    return {"verified": True, "ticket": ticket}


@router.post("/password/forgot/reset")
def forgot_reset(req: ForgotResetReq):
    """找回第三步：票据校验 + 设置新密码 + 全量吊销旧 token。"""
    user_id = auth_store.consume_reset_ticket(req.ticket)
    if user_id is None:
        raise HTTPException(status_code=400, detail="重置票据无效或已过期，请重新验证")
    try:
        auth_store.set_password(user_id, req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 改密后全量踢出旧会话（与 /me/logout-all 同语义）
    auth_store.revoke_all_tokens(user_id)
    return {"reset": True, "user_id": user_id}


@router.get("/me/security-question")
def get_my_security(authorization: str = Header(default="")):
    """当前登录用户是否已设密保（用于前端引导/展示，不返回答案）。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return {
        "has_security": bool((user.get("security_question") or "").strip()),
        "security_question": user.get("security_question") or "",
        "premade": auth_store.PREMADE_QUESTIONS,
    }


@router.post("/me/security-question")
def set_my_security(req: SetSecurityReq, authorization: str = Header(default="")):
    """设置/更新当前用户的密保问题。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        auth_store._set_security(user["user_id"], req.question, req.answer)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"set": True}


# ---------- Gitee OAuth 第三方登录 ----------
# 注意：nginx /api/ 规则会剥掉 /api 前缀（proxy_pass 尾斜杠），
# 故后端路由用 /auth/gitee/*；对外 URL 仍是 /api/auth/gitee/*（前端/回调地址用）。
@router.get("/auth/gitee/status")
def gitee_status():
    """Gitee 登录配置状态（前端据此显示/隐藏按钮）。"""
    return {"enabled": gitee_oauth.enabled, "name": "Gitee"}


@router.get("/auth/gitee/login")
def gitee_login():
    """跳转 Gitee 授权页。未配置 → 503。state 防 CSRF。"""
    if not gitee_oauth.enabled:
        raise HTTPException(status_code=503, detail="Gitee 登录未配置")
    state = secrets.token_urlsafe(16)
    _oauth_states[state] = _time.time() + _STATE_TTL
    return RedirectResponse(gitee_oauth.authorize_url(state))


@router.get("/auth/gitee/callback")
def gitee_callback(code: str = "", state: str = ""):
    """Gitee 回调：code 换 token → 拉用户 → 建号/绑定 → 签发本站 token → 302 回首页。"""
    exp = _oauth_states.pop(state, 0)
    if not state or exp < _time.time():
        raise HTTPException(status_code=400, detail="state 无效或已过期")
    if not code:
        raise HTTPException(status_code=400, detail="缺少 code")
    try:
        guser = gitee_oauth.login_with_code(code)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"Gitee 登录失败: {e}")
    uid = str(guser.get("id"))
    user = auth_store.find_user_by_oauth("gitee", uid)
    if user is None:
        user_id = auth_store.register_oauth("gitee", uid, guser.get("name", ""))
    else:
        user_id = user["user_id"]
    token = auth_store._sign(user_id)
    return RedirectResponse(f"/?token={token}")