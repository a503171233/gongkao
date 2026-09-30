# -*- coding: utf-8 -*-
"""运营数据看板（C3）与激励体系管理（#18）路由。

- C3 看板：/admin/stats/overview|quota|revenue|teachers|export（只读聚合）
  权限双通道：X-Admin-Secret 匹配 ADMIN_SECRET，或 admin Bearer token。
- 激励体系：/admin/incentive/stats|config|recompute
"""

import hmac

from fastapi import APIRouter, Body, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .. import deps as _deps
from .. import stats as stats_biz

router = APIRouter()


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
    return _deps._admin_user(authorization)


# ---------- C3 运营数据看板 ----------
@router.get("/admin/stats/overview")
def admin_stats_overview(authorization: str = Header(default=""),
                         x_admin_secret: str = Header(default="")):
    """运营总览：总用户/会员数/今日新增/今日 ask/拦截/耗时。"""
    _require_admin(authorization, x_admin_secret)
    return stats_biz.overview(
        _deps.auth_store.db_path, _deps.chat_store.db_path,
        _deps.metrics.snapshot(), _deps.guard_counter.stats())


@router.get("/admin/stats/quota")
def admin_stats_quota(authorization: str = Header(default=""),
                      x_admin_secret: str = Header(default="")):
    """额度消耗分布（按日/用户）。"""
    _require_admin(authorization, x_admin_secret)
    return stats_biz.quota_dist(_deps.auth_store.db_path)


@router.get("/admin/stats/revenue")
def admin_stats_revenue(authorization: str = Header(default=""),
                        x_admin_secret: str = Header(default="")):
    """营收聚合（依赖 C1 pay.db；C1 未上线 → enabled=false 优雅降级）。"""
    _require_admin(authorization, x_admin_secret)
    return stats_biz.revenue(_deps.payment_store.db_path)


@router.get("/admin/stats/teachers")
def admin_stats_teachers(authorization: str = Header(default=""),
                         x_admin_secret: str = Header(default="")):
    """老师维度请求/拦截/满意度（热度与 /metrics by_teacher 一致）。"""
    _require_admin(authorization, x_admin_secret)
    return stats_biz.teacher_stats(
        _deps.metrics.snapshot(), _deps.guard_counter.stats(),
        _deps.cfg.list_teachers_all())


@router.get("/admin/stats/export")
def admin_stats_export(authorization: str = Header(default=""),
                       x_admin_secret: str = Header(default="")):
    """核心指标 CSV 导出（UTF-8 with BOM，Excel 打开不乱码）。"""
    _require_admin(authorization, x_admin_secret)
    csv_text = stats_biz.export_csv(
        _deps.auth_store.db_path, _deps.chat_store.db_path,
        _deps.payment_store.db_path, _deps.metrics.snapshot(),
        _deps.guard_counter.stats(), _deps.cfg.list_teachers_all())
    import time as _t
    return PlainTextResponse(
        csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="gk-stats-{_t.strftime("%Y%m%d-%H%M%S")}.csv"',
        },
    )


# ---------- 激励体系管理 ----------
class IncentiveConfigReq(BaseModel):
    """激励配置请求体：总开关 + 各事件积分（非法键/越界值自动忽略）。"""
    enabled: bool = True
    event_points: dict = {}


class IncentiveRecomputeReq(BaseModel):
    """激励重算请求体：user_id 留空 = 全部用户。"""
    user_id: str = ""


@router.get("/admin/incentive/stats")
def admin_incentive_stats(authorization: str = Header(default="")):
    """激励体系总览：配置 + 全局统计 + 积分榜 TOP20。"""
    _deps._admin_user(authorization)
    return {
        "config": _deps.incentive_store.get_config(),
        "stats": _deps.incentive_store.admin_stats(),
        "board": _deps.incentive_store.board(limit=20),
    }


@router.post("/admin/incentive/config")
def admin_incentive_config(
    req: IncentiveConfigReq = Body(default_factory=IncentiveConfigReq),
    authorization: str = Header(default=""),
):
    """更新激励配置：总开关 + 各事件积分。"""
    _deps._admin_user(authorization)
    return _deps.incentive_store.set_config(req.enabled, req.event_points)


@router.post("/admin/incentive/recompute")
def admin_incentive_recompute(
    req: IncentiveRecomputeReq = Body(default_factory=IncentiveRecomputeReq),
    authorization: str = Header(default=""),
):
    """激励数据全量重算：按 practice_logs 时间序幂等重放（管理员手动纠偏）。
    user_id 非空 = 仅重算该用户；留空 = 全部用户。"""
    _deps._admin_user(authorization)
    logs = _deps.study_store.all_practice_logs()
    by_user: dict[str, list[dict]] = {}
    for log in logs:
        by_user.setdefault(log["user_id"], []).append(log)
    results = []
    for uid, ulogs in by_user.items():
        if req.user_id and uid != req.user_id:
            continue
        results.append(_deps.incentive_store.recompute_user(
            uid, ulogs, _deps.study_store))
    return {"recomputed": len(results), "results": results}