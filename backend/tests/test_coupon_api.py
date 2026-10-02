# -*- coding: utf-8 -*-
"""#24 优惠券/限时折扣 接口断言：校验 / 生成 / 列表 / 作废 / 统计。
运行：cd backend && python tests/test_coupon_api.py
"""
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import poc.api as api
from poc.payment import PaymentStore


def _main():
    tmp = Path(tempfile.mkdtemp(prefix="gk_coupon_"))
    pay = PaymentStore(db_path=tmp / "pay.db")

    def fake_resolve(authorization):
        t = (authorization or "").replace("Bearer ", "", 1).strip()
        if t == "tok-admin":
            return {"user_id": "u-admin", "username": "Admin", "role": "admin"}
        if t == "tok-user":
            return {"user_id": "u-a", "username": "小明", "role": "free"}
        return None

    orig_resolve = api._resolve_user
    orig_pay = api.payment_store
    api._resolve_user = fake_resolve
    api.payment_store = pay
    client = TestClient(api.app)
    try:
        # ── 1. 生成优惠券（管理端）──
        resp = client.post("/admin/coupons/generate",
                           headers={"Authorization": "Bearer tok-admin"},
                           json={"code_prefix": "TEST", "type": "fixed",
                                 "value": 1000, "count": 3, "max_uses": 10})
        assert resp.status_code == 200, f"generate fail: {resp.text}"
        data = resp.json()
        assert data["count"] == 3
        assert "coupons" in data
        c1 = data["coupons"][0]["code"]
        assert c1.startswith("TEST-")
        print(f"  ✅ generate 3 coupons: {c1} ...")

        # ── 2. 生成百分比券 ──
        resp = client.post("/admin/coupons/generate",
                           headers={"Authorization": "Bearer tok-admin"},
                           json={"code_prefix": "SALE", "type": "percent",
                                 "value": 20, "count": 1, "max_uses": 5,
                                 "min_order_amount": 5000, "note": "限时八折"})
        assert resp.status_code == 200
        pct_code = resp.json()["coupons"][0]["code"]
        print(f"  ✅ generate percent coupon: {pct_code}")

        # ── 3. 校验优惠券（用户端预览）──
        resp = client.post("/orders/apply-coupon",
                           headers={"Authorization": "Bearer tok-user"},
                           json={"code": c1, "plan": "month"})
        assert resp.status_code == 200, f"apply-coupon fail: {resp.text}"
        d = resp.json()
        assert d["discount"] == 1000  # ¥10.00 off
        assert d["type"] == "fixed"
        assert d["final_amount"] == 2900 - 1000
        print(f"  ✅ validate fixed coupon: ¥{d['discount']/100:.0f} off → ¥{d['final_amount']/100:.2f}")

        # ── 4. 百分比券校验 ──
        resp = client.post("/orders/apply-coupon",
                           headers={"Authorization": "Bearer tok-user"},
                           json={"code": pct_code, "plan": "year"})
        assert resp.status_code == 200
        d2 = resp.json()
        assert d2["type"] == "percent"
        assert d2["discount"] == 29900 * 20 // 100  # ¥299 * 20% = ¥59.80
        print(f"  ✅ validate percent coupon: {d2['value']}% off → ¥{d2['final_amount']/100:.2f}")

        # ── 5. 创建订单（带优惠券）──
        resp = client.post("/orders",
                           headers={"Authorization": "Bearer tok-user"},
                           json={"plan": "month", "coupon_code": c1})
        assert resp.status_code == 200, f"create order fail: {resp.text}"
        order = resp.json()
        assert order["coupon_code"] == c1
        assert order["discount_amount"] == 1000
        assert order["final_amount"] == 2900 - 1000
        print(f"  ✅ create order with coupon: discount ¥{order['discount_amount']/100:.0f}")

        # ── 6. 优惠券列表（管理端）──
        resp = client.get("/admin/coupons",
                          headers={"Authorization": "Bearer tok-admin"})
        assert resp.status_code == 200
        clist = resp.json()
        assert clist["total"] >= 4
        print(f"  ✅ list coupons: total={clist['total']}")

        # ── 7. 优惠券统计 ──
        resp = client.get("/admin/coupons/stats",
                          headers={"Authorization": "Bearer tok-admin"})
        assert resp.status_code == 200, f"stats fail: {resp.text}"
        stats = resp.json()
        assert "overall" in stats
        assert "by_type" in stats
        print(f"  ✅ coupon stats: overall={stats['overall']}")

        # ── 8. 作废优惠券 ──
        cid = data["coupons"][1]["id"]
        resp = client.post(f"/admin/coupons/{cid}/void",
                           headers={"Authorization": "Bearer tok-admin"})
        assert resp.status_code == 200, f"void fail: {resp.text}"
        print(f"  ✅ void coupon {cid}")

        # 校验已作废券
        code2 = data["coupons"][1]["code"]
        resp = client.post("/orders/apply-coupon",
                           headers={"Authorization": "Bearer tok-user"},
                           json={"code": code2, "plan": "month"})
        assert resp.status_code == 400, f"voided coupon should be rejected"
        print(f"  ✅ voided coupon rejected")

        # ── 9. 管理员扫码查询 ──
        resp = client.post("/admin/coupons/check",
                           headers={"Authorization": "Bearer tok-admin"},
                           json={"code": c1})
        assert resp.status_code == 200, f"check fail: {resp.text}"
        info = resp.json()
        assert info["code"] == c1
        assert info["used_count"] >= 1  # 创建订单时已 +1
        print(f"  ✅ admin check coupon: used_count={info['used_count']}")

        # ── 10. 无效优惠券校验 ──
        resp = client.post("/orders/apply-coupon",
                           headers={"Authorization": "Bearer tok-user"},
                           json={"code": "FAKE-XXXX", "plan": "month"})
        assert resp.status_code in (400, 404)
        print("  ✅ invalid coupon rejected")

        # ── 11. 未登录拦截 ──
        resp = client.post("/orders/apply-coupon",
                           json={"code": c1, "plan": "month"})
        assert resp.status_code == 401
        print("  ✅ auth enforced")

        print("\n✅ 全部 11 项 coupon 断言通过")

    finally:
        api._resolve_user = orig_resolve
        api.payment_store = orig_pay


if __name__ == "__main__":
    _main()