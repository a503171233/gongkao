# -*- coding: utf-8 -*-
"""行为埋点路由（首页宣传入口点击上报 + 管理端聚合统计）。"""
import threading
import time as _time

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from .. import deps as _deps
from ..tracking import PROMO_TARGETS as _PROMO_TARGETS

router = APIRouter()


class TrackPromoReq(BaseModel):
    target: str = ""
    page: str = ""


_track_limiter: dict[str, list[float]] = {}
_track_lock = threading.Lock()
_TRACK_WINDOW = 60
_TRACK_MAX = 20


def _track_allowed(ip: str) -> bool:
    now = _time.time()
    with _track_lock:
        if len(_track_limiter) > 4000:
            _track_limiter.clear()
        arr = [t for t in _track_limiter.get(ip, []) if now - t < _TRACK_WINDOW]
        if len(arr) >= _TRACK_MAX:
            _track_limiter[ip] = arr
            return False
        arr.append(now)
        _track_limiter[ip] = arr
        return True


@router.post("/track/promo")
def track_promo(req: TrackPromoReq, request: Request,
                user_agent: str = Header(default="")):
    """前台宣传卡点击上报（无登录要求，低频）：target ∈ mock/mind/forum/articles。"""
    target = (req.target or "").strip().lower()
    if target not in _PROMO_TARGETS:
        raise HTTPException(status_code=400,
                            detail="target 必须是 mock / mind / forum / articles 之一")
    ip = _deps._client_ip(request)
    if not _track_allowed(ip):
        raise HTTPException(status_code=429, detail="上报过于频繁，请稍后再试")
    _deps.tracking_store.log("promo_click", target=target,
                             page=(req.page or "").strip()[:40], ip=ip, ua=user_agent)
    return {"ok": True}


@router.get("/admin/tracking/promo")
def admin_tracking_promo(authorization: str = Header(default="")):
    """宣传入口点击统计：total / 按 target 汇总 / 近 14 天趋势。"""
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    return _deps.tracking_store.promo_stats()