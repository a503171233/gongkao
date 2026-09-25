# -*- coding: utf-8 -*-
"""批次14 支付安全三项收口 —— 本地单测。

覆盖（巡检报告 2026-09-25 §1.2/§3.3）：
  1. pending 订单 30 分钟未支付 → get_order 惰性置 expired
  2. 新建订单不受影响（仍 pending）
  3. 过期订单回调被拒（ValueError）
  4. 充值码激活试码频控（5 次/10 分钟，超限 429）

运行：cd backend && python -m pytest tests/test_payment_hardening.py -q
（无 pytest 时：python tests/test_payment_hardening.py）
"""
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from poc.payment import PaymentStore  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return PaymentStore(db_path=tmp_path / "pay.db")


def _insert_stale_pending(store: PaymentStore, order_id: str, mins_ago: int = 31) -> None:
    """直接落一条 created_at 为 N 分钟前的 pending 单（绕过 create_order 的 now）。"""
    stale = (datetime.now(timezone.utc) - timedelta(minutes=mins_ago)
             ).strftime("%Y-%m-%dT%H:%M:%SZ")
    import sqlite3
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        """INSERT INTO orders (order_id, user_id, plan, amount, status, created_at)
           VALUES (?, 'u-test', 'month', 2900, 'pending', ?)""",
        (order_id, stale),
    )
    conn.commit()
    conn.close()


def test_stale_pending_expires_on_read(store):
    _insert_stale_pending(store, "STALE-1", mins_ago=31)
    o = store.get_order("STALE-1")
    assert o["status"] == "expired", "超时 pending 单读取时应被置 expired"
    # 再读一次：落库持久（不是临时内存态）
    assert store.get_order("STALE-1")["status"] == "expired"


def test_fresh_order_stays_pending(store):
    store.create_order("u-test", "month")
    o = store.list_orders("u-test")[0]
    assert o["status"] == "pending", "新订单不应被误伤过期"


def test_ttl_boundary_not_expired(store):
    _insert_stale_pending(store, "EDGE-1", mins_ago=29)
    assert store.get_order("EDGE-1")["status"] == "pending", "TTL 边界内（29 分钟）不置过期"


def test_expired_order_rejected_in_notify(store):
    _insert_stale_pending(store, "STALE-2", mins_ago=31)
    with pytest.raises(ValueError, match="过期"):
        store.handle_payment_notify({"order_id": "STALE-2", "txn_id": "TX-1"})


def test_paid_order_not_touched_by_expiry(store):
    # 已 paid 的单不受过期逻辑影响（幂等 already_paid 优先）
    _insert_stale_pending(store, "PAID-1", mins_ago=60)
    import sqlite3
    conn = sqlite3.connect(store.db_path)
    conn.execute("UPDATE orders SET status='paid', paid_at=? WHERE order_id=?",
                 (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "PAID-1"))
    conn.commit()
    conn.close()
    r = store.handle_payment_notify({"order_id": "PAID-1", "txn_id": "TX-2"})
    assert r["status"] == "already_paid"


def test_redeem_rate_limit_blocks_6th_try():
    from poc import api as api_mod
    key = f"u-ratelimit-{time.monotonic_ns()}"   # 独立 key，避免与其他用例串扰
    api_mod._redeem_attempts.pop(key, None)
    for _ in range(5):
        api_mod._redeem_rate_limit(key)          # 前 5 次放行
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        api_mod._redeem_rate_limit(key)          # 第 6 次 429
    assert ei.value.status_code == 429


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
