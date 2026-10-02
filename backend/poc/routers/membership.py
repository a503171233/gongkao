# -*- coding: utf-8 -*-
"""会员付费闭环路由：定价方案、订单、支付回调、充值码激活。"""

from fastapi import APIRouter, Body, Header, HTTPException, Request
from pydantic import BaseModel

from .. import deps
from ..deps import auth_store, payment_store
from ..payment import PLANS

router = APIRouter()


class OrderReq(BaseModel):
    """建单请求体（C1 契约：{plan: month|quarter|year, coupon_code?: ""}）。"""
    plan: str = "month"
    coupon_code: str = ""


class CodeReq(BaseModel):
    """充值码激活请求体（C1 契约：{code: "..."}）。"""
    code: str = ""


@router.get("/plans")
def plans():
    """定价方案列表（公开接口）。"""
    # 注意：必须转 list，dict.values() 视图对象 FastAPI 无法序列化（jsonable_encoder 会报 vars() 错）
    return {"plans": list(PLANS.values())}


@router.get("/orders/user")
def list_user_orders(authorization: str = Header(default="")):
    """查询当前用户的订单列表。"""
    user = deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")

    return {"orders": payment_store.list_orders(user["user_id"])}


@router.post("/orders")
def create_order(req: OrderReq = Body(default_factory=OrderReq),
                 authorization: str = Header(default="")):
    """新建订单（需登录）。阶段一返回充值码激活入口。"""
    user = deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")

    try:
        order = payment_store.create_order(user["user_id"], req.plan, req.coupon_code)
        return order
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/orders/{order_id}")
def get_order(order_id: str, authorization: str = Header(default="")):
    """查询订单状态。"""
    user = deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    order = payment_store.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"订单不存在: {order_id}")
    if order["user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权查看此订单")
    
    return order


@router.post("/orders/notify")
async def notify_payment(request: Request):
    """支付回调（验签、幂等、开通会员）。
    #15：验签实现在 payment.handle_payment_notify（PAY_SIGN_MODE/PAY_SIGN_KEY 可配；
    默认 none 放行，配置后校验失败抛 ValueError → 400）。
    """
    # 解析请求体
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体解析失败")

    try:
        result = payment_store.handle_payment_notify(body)
    except ValueError as e:
        # 验签失败/订单异常：统一 400（detail 透出）
        raise HTTPException(status_code=400, detail=str(e))

    # 若订单刚变为 paid，开通会员（pay_channel 非 recharge_code 的第三方单同样处理）
    order = payment_store.get_order(result["order_id"])
    if order and order.get("status") == "paid":
        try:
            auth_store.activate_membership(order["user_id"], 30)
        except Exception:  # noqa: BLE001  激活失败不阻断回调确认
            pass

    return result


@router.get("/me/membership")
def membership_status(authorization: str = Header(default="")):
    """当前用户会员状态。"""
    user = deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    return auth_store.get_membership_info(user["user_id"])


@router.post("/orders/recharge-code/activate")
def activate_recharge_code(
    req: CodeReq = Body(default_factory=CodeReq),
    authorization: str = Header(default=""),
):
    """使用充值码激活订单（阶段一主要入口）。
    C1 契约：body 为 JSON 对象 {"code": "..."}（与前端 GK.api JSON 序列化一致）。
    """
    user = deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")

    code = (req.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="缺少充值码")

    try:
        order = payment_store.activate_recharge_code(code, user["user_id"], auth_store)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if order is None:
        raise HTTPException(status_code=404, detail="充值码无效或已被使用")

    return {
        **order,
        "membership": auth_store.get_membership_info(user["user_id"]),
    }


# ── 优惠券（#24 优惠券/限时折扣）──

class CouponGenerateReq(BaseModel):
    """生成优惠券请求。"""
    code_prefix: str = "COUPON"
    type: str = "fixed"       # fixed(固定金额,分) | percent(百分比)
    value: int = 0
    count: int = 1
    max_uses: int = 1
    expires_at: str = ""      # ISO 8601 时间，空=永不过期
    min_order_amount: int = 0  # 最低消费门槛（分），0=无门槛
    note: str = ""


class ApplyCouponReq(BaseModel):
    """校验/预览优惠券。"""
    code: str = ""
    plan: str = "month"


@router.post("/orders/apply-coupon")
def apply_coupon(req: ApplyCouponReq = Body(default_factory=ApplyCouponReq),
                 authorization: str = Header(default="")):
    """校验优惠券并返回折扣预览（不创建订单；#24 前端即时预览用）。"""
    user = deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    if req.plan not in PLANS:
        raise HTTPException(status_code=404, detail="未知套餐")
    plan_price = PLANS[req.plan]["price"]
    code = (req.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="请输入优惠码")
    try:
        info = payment_store.validate_coupon(code, plan_price)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "code": info["code"],
        "type": info["type"],
        "value": info["value"],
        "discount": info["discount"],
        "original_amount": plan_price,
        "final_amount": plan_price - info["discount"],
    }


# ── 管理端优惠券 CRUD（#24） ──

@router.get("/admin/coupons")
def admin_list_coupons(limit: int = 100, offset: int = 0,
                       status: str = "all", keyword: str = "",
                       authorization: str = Header(default="")):
    """优惠券列表（管理端筛选）。"""
    user = deps._resolve_user(authorization)
    if user is None or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员")
    coupons = payment_store.list_coupons(
        limit=limit, offset=offset, status=status, keyword=keyword)
    total = payment_store.count_coupons(status=status, keyword=keyword)
    return {"coupons": coupons, "total": total}


@router.post("/admin/coupons/generate")
def admin_generate_coupons(req: CouponGenerateReq = Body(default_factory=CouponGenerateReq),
                           authorization: str = Header(default="")):
    """生成优惠券（管理端）。"""
    user = deps._resolve_user(authorization)
    if user is None or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员")
    try:
        codes = payment_store.generate_coupons(
            code_prefix=req.code_prefix, ctype=req.type, value=req.value,
            count=req.count, max_uses=req.max_uses, expires_at=req.expires_at,
            min_order_amount=req.min_order_amount, created_by=user["user_id"],
            note=req.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"coupons": codes, "count": len(codes)}


@router.post("/admin/coupons/{coupon_id}/void")
def admin_void_coupon(coupon_id: str, authorization: str = Header(default="")):
    """作废优惠券（管理端）。"""
    user = deps._resolve_user(authorization)
    if user is None or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员")
    ok = payment_store.void_coupon(coupon_id)
    if not ok:
        raise HTTPException(status_code=404, detail="优惠券不存在")
    return {"ok": True}


@router.get("/admin/coupons/stats")
def admin_coupon_stats(authorization: str = Header(default="")):
    """优惠券统计（管理端）。"""
    user = deps._resolve_user(authorization)
    if user is None or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员")
    return payment_store.coupon_stats()


@router.post("/admin/coupons/check")
def admin_check_coupon(code: str = Body(default="", embed=True),
                       authorization: str = Header(default="")):
    """查询单个优惠券详情（管理端扫码查询）。"""
    user = deps._resolve_user(authorization)
    if user is None or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员")
    c = payment_store.get_coupon(code)
    if c is None:
        raise HTTPException(status_code=404, detail="优惠券不存在")
    return c