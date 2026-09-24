# -*- coding: utf-8 -*-
"""支付/充值码安全与契约 · 单元测试（批次2 安全加固）
运行: cd backend && python tests/test_payment.py
覆盖:
  - 下单/查询/幂等回调（already_paid）
  - 防伪造收口：PAY_SIGN_MODE=none 时真实订单回调拒绝、TEST- 测试单放行、
    PAY_ALLOW_UNSIGNED=1 逃生开关
  - 三种验签模式（hmac_md5/hmac_sha256/md5_sort）正确签名通过 + 篡改拒绝 + 缺 sign 拒绝
  - 充值码：生成→激活→重复激活幂等→他人使用拒绝→作废后无效
"""
import hmac
import hashlib
import os
import sys
import tempfile
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-pay-test-")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["PAY_DB"] = os.path.join(TMP, "pay.db")
os.environ["AUTH_DB"] = os.path.join(TMP, "auth.db")
# 隔离默认环境：确保用默认 none 模式（测试内显式切换各模式）
os.environ.pop("PAY_SIGN_MODE", None)
os.environ.pop("PAY_SIGN_KEY", None)
os.environ.pop("PAY_ALLOW_UNSIGNED", None)

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


from poc.payment import PaymentStore, PLANS  # noqa: E402

p = PaymentStore()
uid = "u-" + uuid.uuid4().hex[:8]


def make_order(plan="month"):
    return p.create_order(uid, plan)["order_id"]


def insert_test_order(order_id="TEST-demo01", plan="month"):
    """直插 TEST- 前缀测试单（模拟/演示链路用，不入 create_order 正常空间）。"""
    import sqlite3
    conn = sqlite3.connect(str(p.db_path))
    conn.execute(
        "INSERT INTO orders (order_id, user_id, plan, amount, status, created_at) "
        "VALUES (?, ?, ?, ?, 'pending', '2026-01-01T00:00:00')",
        (order_id, uid, plan, PLANS[plan]["price"]),
    )
    conn.commit()
    conn.close()
    return order_id


# ── 1) 下单/查询 ────────────────────────────────────────────────
oid = make_order()
o = p.get_order(oid)
check("下单成功 pending", o and o["status"] == "pending", str(o))
try:
    p.create_order(uid, "no-such-plan")
    check("未知套餐拒绝", False)
except ValueError:
    check("未知套餐拒绝", True)

# ── 2) 防伪造收口（PAY_SIGN_MODE=none 默认） ─────────────────────
try:
    p.handle_payment_notify({"order_id": oid, "txn_id": "TX1"})
    check("真实订单无签名回调被拒", False)
except ValueError as e:
    check("真实订单无签名回调被拒", "未启用验签" in str(e), str(e))
check("被拒订单仍是 pending", p.get_order(oid)["status"] == "pending")

toid = insert_test_order()
r = p.handle_payment_notify({"order_id": toid, "txn_id": "TX-TEST"})
check("TEST- 测试单放行", r["status"] == "paid", str(r))
r2 = p.handle_payment_notify({"order_id": toid, "txn_id": "TX-TEST2"})
check("回调幂等 already_paid", r2["status"] == "already_paid", str(r2))

try:
    p.handle_payment_notify({"order_id": "no-such", "txn_id": "X"})
    check("订单不存在拒绝", False)
except ValueError:
    check("订单不存在拒绝", True)

os.environ["PAY_ALLOW_UNSIGNED"] = "1"
try:
    oid2 = make_order()
    r = p.handle_payment_notify({"order_id": oid2, "txn_id": "TX2"})
    check("PAY_ALLOW_UNSIGNED=1 逃生放行", r["status"] == "paid", str(r))
finally:
    os.environ.pop("PAY_ALLOW_UNSIGNED", None)

# ── 3) 三种验签模式：正确通过 / 篡改拒绝 / 缺 sign 拒绝 ───────────
KEY = "pay-secret-key"


def signed(mode, data, tamper=False, drop_sign=False):
    payload = {k: v for k, v in data.items() if k != "sign"}
    if tamper:
        payload["amount"] = "1"
    raw = "&".join(f"{k}={v}" for k, v in sorted(payload.items()))
    if mode == "hmac_md5":
        sig = hmac.new(KEY.encode(), raw.encode(), hashlib.md5).hexdigest()
    elif mode == "hmac_sha256":
        sig = hmac.new(KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
    else:  # md5_sort
        sig = hashlib.md5((raw + KEY).encode()).hexdigest()
    out = dict(data)
    if not drop_sign:
        out["sign"] = sig
    return out


for mode in ("hmac_md5", "hmac_sha256", "md5_sort"):
    os.environ["PAY_SIGN_MODE"] = mode
    os.environ["PAY_SIGN_KEY"] = KEY
    t = insert_test_order(f"TEST-sign-{mode}")
    # 验签模式下 handle_payment_notify：TEST- 放行守卫 + _verify_signature 真校验
    good = signed(mode, {"order_id": t, "txn_id": f"TX-{mode}"})
    r = p.handle_payment_notify(good)
    check(f"{mode}: 正确签名通过", r["status"] == "paid", str(r))

    t2 = insert_test_order(f"TEST-sign2-{mode}")
    bad = signed(mode, {"order_id": t2, "txn_id": f"TX-{mode}-bad"}, tamper=True)
    try:
        p.handle_payment_notify(bad)
        check(f"{mode}: 篡改被拒", False)
    except ValueError as e:
        check(f"{mode}: 篡改被拒", "验签失败" in str(e), str(e))

    t3 = insert_test_order(f"TEST-sign3-{mode}")
    try:
        p.handle_payment_notify({"order_id": t3, "txn_id": f"TX-{mode}-nosign"})
        check(f"{mode}: 缺 sign 被拒", False)
    except ValueError as e:
        check(f"{mode}: 缺 sign 被拒", "验签失败" in str(e), str(e))

os.environ.pop("PAY_SIGN_MODE", None)
os.environ.pop("PAY_SIGN_KEY", None)

# ── 4) 充值码全生命周期 ─────────────────────────────────────────
codes = p.generate_recharge_codes("month", 1)
code = codes[0]
check("充值码生成", bool(code), str(codes))

r = p.activate_recharge_code(code, uid)
check("充值码激活 paid", r and r["status"] == "paid", str(r))
r2 = p.activate_recharge_code(code, uid)
check("重复激活幂等（同用户）", r2 and r2["status"] == "paid" and r2["order_id"] == r["order_id"])
try:
    other = "u-other-" + uuid.uuid4().hex[:6]
    p.activate_recharge_code(code, other)
    check("他人使用已激活码拒绝", False)
except ValueError:
    check("他人使用已激活码拒绝", True)

c2 = p.generate_recharge_codes("quarter", 1)[0]
vr = p.void_codes(codes=[c2])
check("作废未使用码 voided=1", vr["voided"] == 1, str(vr))
check("作废后激活返回 None", p.activate_recharge_code(c2, uid) is None)

# 充值码激活走的订单是 RC- 前缀，不受 /orders/notify 防伪造守卫影响
orders = p.get_order(r["order_id"])
check("充值码订单 RC- 前缀已 paid", orders and orders["status"] == "paid", str(orders))

print()
print("PASS" if ok else "FAIL", "- test_payment")
sys.exit(0 if ok else 1)
