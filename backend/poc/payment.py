# -*- coding: utf-8 -*-
"""C1 · 会员付费闭环 — 支付/订单/充值码模块。

职责：
  - 定价方案（月/季/年三档）
  - 订单管理（建单/查询/状态流转）
  - 充值码管理（生成/校验/一次性使用）
  - 支付回调验签（阶段一预留接口，阶段二接第三方支付）

数据存储：独立 pay.db（SQLite 标准库），与 auth.db/chat.db 隔离。
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import Config

# ---------- 定价配置（单位：分） ----------
# B3 定价可配置：默认占位价，环境变量可覆盖（无需改代码即可调价）。
# 约定：PLAN_<KEY>_PRICE（分）、PLAN_<KEY>_NAME、PLAN_<KEY>_DAYS、PLAN_<KEY>_BENEFITS
def _plan_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)).strip())
    except ValueError:
        return default


PLANS: dict[str, dict] = {
    "month": {
        "plan": "month",
        "name": os.environ.get("PLAN_MONTH_NAME", "月度会员"),
        "price": _plan_int("PLAN_MONTH_PRICE", 2900),          # ¥29.00
        "duration_days": _plan_int("PLAN_MONTH_DAYS", 30),
        "benefits": os.environ.get("PLAN_MONTH_BENEFITS", "每日 200 次额度 + 增值功能"),
    },
    "quarter": {
        "plan": "quarter",
        "name": os.environ.get("PLAN_QUARTER_NAME", "季度会员"),
        "price": _plan_int("PLAN_QUARTER_PRICE", 7900),        # ¥79.00
        "duration_days": _plan_int("PLAN_QUARTER_DAYS", 90),
        "benefits": os.environ.get("PLAN_QUARTER_BENEFITS", "每日 200 次额度 + 增值功能"),
    },
    "year": {
        "plan": "year",
        "name": os.environ.get("PLAN_YEAR_NAME", "年度会员"),
        "price": _plan_int("PLAN_YEAR_PRICE", 29900),          # ¥299.00
        "duration_days": _plan_int("PLAN_YEAR_DAYS", 365),
        "benefits": os.environ.get("PLAN_YEAR_BENEFITS", "每日 200 次额度 + 增值功能"),
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class PaymentStore:
    """订单/充值码持久化（独立 pay.db）。"""

    def __init__(self, db_path: str | Path | None = None):
        default = Config.data_dir / "pay.db"
        self.db_path = Path(db_path or os.environ.get("PAY_DB") or default)
        if not self.db_path.is_absolute():
            self.db_path = Config.ROOT / self.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id  TEXT PRIMARY KEY,
                user_id   TEXT NOT NULL,
                plan      TEXT NOT NULL,
                amount    INTEGER NOT NULL,
                status    TEXT NOT NULL DEFAULT 'pending',
                pay_channel TEXT NOT NULL DEFAULT 'recharge_code',
                txn_id    TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                paid_at   TEXT,
                coupon_id TEXT DEFAULT '',
                discount_amount INTEGER DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_txn
                ON orders(txn_id) WHERE txn_id != '';
            -- 订单是流水，允许同用户多笔；(user_id, created_at) 唯一索引是错误设计
            -- （建单+激活同秒即撞 UNIQUE），已改为普通索引。迁移：drop 旧索引幂等。
            DROP INDEX IF EXISTS idx_orders_user_plan;
            CREATE INDEX IF NOT EXISTS idx_orders_user
                ON orders(user_id, created_at);

            CREATE TABLE IF NOT EXISTS recharge_codes (
                code       TEXT PRIMARY KEY,
                plan       TEXT NOT NULL,
                amount     INTEGER NOT NULL,
                used_by    TEXT DEFAULT '',   -- user_id（已用时填写）
                used_at    TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rcode_used
                ON recharge_codes(used_by) WHERE used_by != '';

            CREATE TABLE IF NOT EXISTS coupons (
                id              TEXT PRIMARY KEY,
                code            TEXT UNIQUE NOT NULL,
                type            TEXT NOT NULL DEFAULT 'fixed',
                value           INTEGER NOT NULL DEFAULT 0,
                min_order_amount INTEGER NOT NULL DEFAULT 0,
                max_uses        INTEGER NOT NULL DEFAULT 0,
                used_count      INTEGER NOT NULL DEFAULT 0,
                expires_at      TEXT DEFAULT '',
                enabled         INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT NOT NULL,
                created_by      TEXT DEFAULT '',
                note            TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_coupon_code ON coupons(code);
            CREATE INDEX IF NOT EXISTS idx_coupon_enabled ON coupons(enabled);
            """)
            # 迁移：coupon_id / discount_amount 订单列（#24 优惠券）
            for ddl2 in (
                "ALTER TABLE orders ADD COLUMN coupon_id TEXT DEFAULT ''",
                "ALTER TABLE orders ADD COLUMN discount_amount INTEGER DEFAULT 0",
            ):
                try:
                    conn.execute(ddl2)
                except sqlite3.OperationalError:
                    pass
            # #24 R4 迁移：批次/渠道/作废/备注（向后兼容）
            for ddl in (
                "ALTER TABLE recharge_codes ADD COLUMN batch_id TEXT DEFAULT ''",
                "ALTER TABLE recharge_codes ADD COLUMN channel TEXT DEFAULT ''",
                "ALTER TABLE recharge_codes ADD COLUMN voided INTEGER DEFAULT 0",
                "ALTER TABLE recharge_codes ADD COLUMN note TEXT DEFAULT ''",
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass  # 列已存在
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rcode_batch ON recharge_codes(batch_id)")

    # ========== 定价 ==========
    def list_plans(self) -> list[dict]:
        """返回定价列表（公开接口用）。"""
        return list(PLANS.values())

    def list_plans_by_key(self, key: str) -> dict | None:
        """按 key 取单个套餐（管理端/内部用）。"""
        return PLANS.get(key)

    # ========== 订单 ==========
    def create_order(self, user_id: str, plan: str, coupon_code: str = "") -> dict:
        """新建订单，返回订单信息（含 order_id 和支付入口）。支持优惠码（#24）。"""
        if plan not in PLANS:
            raise ValueError(f"未知套餐: {plan}")
        p = PLANS[plan]
        order_id = uuid.uuid4().hex
        now = _now_iso()
        discount = 0
        coupon_id = ""
        if coupon_code.strip():
            c = self.validate_coupon(coupon_code.strip(), p["price"])
            discount = c["discount"]
            coupon_id = c["id"]
            self._bump_coupon_count(coupon_id)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO orders
                   (order_id, user_id, plan, amount, status, created_at,
                    coupon_id, discount_amount)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (order_id, user_id, plan, p["price"], now,
                 coupon_id, discount),
            )
        final_amount = p["price"] - discount
        result = {
            "order_id": order_id,
            "plan": plan,
            "plan_name": p["name"],
            "amount": p["price"],
            "discount_amount": discount,
            "final_amount": final_amount,
            "status": "pending",
            "pay_channel": "recharge_code",
            "coupon_id": coupon_id or None,
            "created_at": now,
            "pay_entry": {
                "type": "recharge_code",
                "hint": "请联系管理员获取充值码，或输入已有充值码激活",
            },
        }
        if coupon_code.strip():
            result["coupon_code"] = coupon_code.strip()
            result["coupon_discount"] = discount
        return result

    def get_order(self, order_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE order_id=?", (order_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_orders(self, user_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ========== 充值码 ==========
    def generate_recharge_codes(
        self, plan: str, count: int = 1, amount_override: int | None = None,
        batch_id: str = "", channel: str = "", note: str = ""
    ) -> list[str]:
        """生成一次性充值码，供管理员分发给学员。batch_id/channel/note 供批次管理（#24 R4）。"""
        if plan not in PLANS:
            raise ValueError(f"未知套餐: {plan}")
        p = PLANS[plan]
        codes: list[str] = []
        now = _now_iso()
        with self._connect() as conn:
            for _ in range(count):
                code = self._gen_code()
                amt = amount_override or p["price"]
                conn.execute(
                    """INSERT INTO recharge_codes
                       (code, plan, amount, batch_id, channel, note, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (code, plan, amt, (batch_id or "").strip()[:40],
                     (channel or "").strip()[:30], (note or "").strip()[:100], now),
                )
                codes.append(code)
        return codes

    def activate_recharge_code(
        self, code: str, user_id: str, auth_store=None
    ) -> dict | None:
        """
        使用充值码激活订单（幂等）。
        成功返回订单信息；无效码返回 None。
        若传入 auth_store，则同步开通会员。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM recharge_codes WHERE code=?", (code,)
            ).fetchone()
        if row is None:
            return None  # 码不存在
        # #24 R4：已作废的码按无效处理（防流出后继续兑换）
        if row["voided"]:
            return None

        # 幂等：同一码同一用户再次激活直接返回原订单
        if row["used_by"] and row["used_by"] == user_id:
            # 仍同步会员状态
            if auth_store:
                auth_store.activate_membership(user_id, PLANS.get(row["plan"], {}).get("duration_days", 30))
            return {
                "order_id": f"RC-{code[:8]}",
                "plan": row["plan"],
                "amount": row["amount"],
                "status": "paid",
                "activated_at": row["used_at"],
                "duration_days": PLANS.get(row["plan"], {}).get("duration_days", 30),
            }

        # 已被其他用户使用 → 拒绝
        if row["used_by"]:
            raise ValueError("该充值码已被使用")

        # 激活
        plan_key = row["plan"]
        duration = PLANS.get(plan_key, {}).get("duration_days", 30)
        order_id = f"RC-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """UPDATE recharge_codes
                   SET used_by=?, used_at=? WHERE code=?""",
                (user_id, now, code),
            )
            conn.execute(
                """INSERT INTO orders
                   (order_id, user_id, plan, amount, status,
                    pay_channel, created_at, paid_at)
                   VALUES (?, ?, ?, ?, 'paid', 'recharge_code', ?, ?)""",
                (order_id, user_id, plan_key, row["amount"], now, now),
            )
        
        # 同步开通会员
        if auth_store:
            auth_store.activate_membership(user_id, duration)
        
        return {
            "order_id": order_id,
            "plan": plan_key,
            "plan_name": PLANS.get(plan_key, {}).get("name", plan_key),
            "amount": row["amount"],
            "status": "paid",
            "pay_channel": "recharge_code",
            "created_at": now,
            "paid_at": now,
            "duration_days": duration,
        }

    # ========== 充值码批次管理（#24 R4） ==========

    def list_codes(self, limit: int = 100, offset: int = 0, status: str = "all",
                   batch_id: str = "", channel: str = "", keyword: str = "") -> list[dict]:
        """充值码列表（多条件筛选）。status: all | unused | used | voided。"""
        sql = "SELECT * FROM recharge_codes WHERE 1=1"
        args: list = []
        if status == "unused":
            sql += " AND used_by='' AND voided=0"
        elif status == "used":
            sql += " AND used_by!=''"
        elif status == "voided":
            sql += " AND voided=1"
        if batch_id:
            sql += " AND batch_id=?"
            args.append(batch_id.strip())
        if channel:
            sql += " AND channel=?"
            args.append(channel.strip())
        if keyword:
            sql += " AND (code LIKE ? OR used_by LIKE ? OR note LIKE ?)"
            kw = f"%{keyword.strip()}%"
            args += [kw, kw, kw]
        sql += " ORDER BY created_at DESC, code LIMIT ? OFFSET ?"
        args += [max(1, min(int(limit), 2000)), max(0, int(offset))]
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def count_codes(self, status: str = "all", batch_id: str = "",
                    channel: str = "", keyword: str = "") -> int:
        """同 list_codes 条件的总数（分页用）。"""
        sql = "SELECT COUNT(*) n FROM recharge_codes WHERE 1=1"
        args: list = []
        if status == "unused":
            sql += " AND used_by='' AND voided=0"
        elif status == "used":
            sql += " AND used_by!=''"
        elif status == "voided":
            sql += " AND voided=1"
        if batch_id:
            sql += " AND batch_id=?"
            args.append(batch_id.strip())
        if channel:
            sql += " AND channel=?"
            args.append(channel.strip())
        if keyword:
            sql += " AND (code LIKE ? OR used_by LIKE ? OR note LIKE ?)"
            kw = f"%{keyword.strip()}%"
            args += [kw, kw, kw]
        with self._connect() as conn:
            return conn.execute(sql, args).fetchone()["n"]

    def void_codes(self, codes: list[str] | None = None, batch_id: str = "") -> dict:
        """作废充值码（仅未使用的可作废；已使用的跳过）。按码列表或按批次。"""
        if not codes and not batch_id:
            raise ValueError("必须提供 codes 或 batch_id")
        with self._connect() as conn:
            if codes:
                ph = ",".join("?" * len(codes))
                cur = conn.execute(
                    f"UPDATE recharge_codes SET voided=1 "
                    f"WHERE code IN ({ph}) AND used_by='' AND voided=0", list(codes))
                voided = cur.rowcount
                skipped = len(codes) - voided
            else:
                cur = conn.execute(
                    "UPDATE recharge_codes SET voided=1 "
                    "WHERE batch_id=? AND used_by='' AND voided=0", (batch_id.strip(),))
                voided = cur.rowcount
                total = conn.execute(
                    "SELECT COUNT(*) n FROM recharge_codes WHERE batch_id=?",
                    (batch_id.strip(),)).fetchone()["n"]
                skipped = total - voided
        return {"voided": voided, "skipped": skipped}

    def import_codes(self, items: list[dict], batch_id: str = "") -> dict:
        """批量导入充值码（外部渠道分发回收）。items: [{code, plan, amount, channel?, note?}]。
        校验：码格式（字母数字-，6~40 位）/ 套餐合法 / 金额>0；重复码跳过。"""
        import re as _re
        if not items:
            raise ValueError("导入列表为空")
        if len(items) > 1000:
            raise ValueError("单次导入上限 1000 个")
        valid, invalid = [], []
        for it in items:
            code = str(it.get("code", "")).strip()
            plan = str(it.get("plan", "")).strip()
            try:
                amount = int(it.get("amount", 0))
            except (TypeError, ValueError):
                amount = 0
            if (not _re.fullmatch(r"[A-Za-z0-9-]{6,40}", code)
                    or plan not in PLANS or amount <= 0):
                invalid.append({"code": code[:40], "reason": "码格式/套餐/金额非法"})
                continue
            valid.append({
                "code": code, "plan": plan, "amount": amount,
                "channel": str(it.get("channel", "")).strip()[:30],
                "note": str(it.get("note", "")).strip()[:100],
            })
        imported, duplicated = 0, 0
        now = _now_iso()
        with self._connect() as conn:
            for v in valid:
                dup = conn.execute(
                    "SELECT code FROM recharge_codes WHERE code=?", (v["code"],)).fetchone()
                if dup:
                    duplicated += 1
                    continue
                conn.execute(
                    """INSERT INTO recharge_codes
                       (code, plan, amount, batch_id, channel, note, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (v["code"], v["plan"], v["amount"], (batch_id or "").strip()[:40],
                     v["channel"], v["note"], now),
                )
                imported += 1
        return {"imported": imported, "duplicated": duplicated, "invalid": invalid,
                "batch_id": (batch_id or "").strip()}

    def recharge_stats(self) -> dict:
        """按批次/渠道统计使用情况（#24 R4 运营看板）。"""
        with self._connect() as conn:
            by_batch = conn.execute(
                """SELECT batch_id,
                          COUNT(*) total,
                          SUM(CASE WHEN used_by!='' THEN 1 ELSE 0 END) used,
                          SUM(CASE WHEN voided=1 THEN 1 ELSE 0 END) voided,
                          SUM(CASE WHEN used_by='' AND voided=0 THEN 1 ELSE 0 END) remaining,
                          SUM(amount) amount_sum
                   FROM recharge_codes GROUP BY batch_id ORDER BY total DESC LIMIT 50"""
            ).fetchall()
            by_channel = conn.execute(
                """SELECT channel,
                          COUNT(*) total,
                          SUM(CASE WHEN used_by!='' THEN 1 ELSE 0 END) used,
                          SUM(CASE WHEN voided=1 THEN 1 ELSE 0 END) voided,
                          SUM(CASE WHEN used_by='' AND voided=0 THEN 1 ELSE 0 END) remaining
                   FROM recharge_codes GROUP BY channel ORDER BY total DESC LIMIT 50"""
            ).fetchall()
            overall = conn.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN used_by!='' THEN 1 ELSE 0 END) used,
                          SUM(CASE WHEN voided=1 THEN 1 ELSE 0 END) voided,
                          SUM(CASE WHEN used_by='' AND voided=0 THEN 1 ELSE 0 END) remaining
                   FROM recharge_codes"""
            ).fetchone()
        none_batch = "（无批次）"
        return {
            "overall": dict(overall) if overall else {},
            "by_batch": [
                {"batch_id": (r["batch_id"] or none_batch), "total": r["total"],
                 "used": r["used"], "voided": r["voided"], "remaining": r["remaining"],
                 "amount_sum": r["amount_sum"] or 0} for r in by_batch],
            "by_channel": [
                {"channel": (r["channel"] or "（无渠道）"), "total": r["total"],
                 "used": r["used"], "voided": r["voided"], "remaining": r["remaining"]}
                for r in by_channel],
        }

    # ========== 支付回调（阶段一预留，阶段二接入第三方支付） ==========
    def handle_payment_notify(self, notify_data: dict) -> dict:
        """
        处理支付平台回调。
        阶段一：模拟回调（前端可触发 POST /api/orders/notify 带 sig=xxx）。
        阶段二：验签后执行同名逻辑。

        notify_data 应包含：
          - order_id: 订单号
          - txn_id:   支付平台流水号（唯一）
          - sig:      服务端验签摘要（HMAC-SHA256）
        """
        order_id = notify_data.get("order_id", "")
        txn_id = notify_data.get("txn_id", "")

        if not order_id:
            raise ValueError("缺少 order_id")

        # 幂等检查：已有 paid 状态的订单不再处理
        existing = self.get_order(order_id)
        if existing and existing["status"] == "paid":
            return {
                "order_id": order_id,
                "status": "already_paid",
                "message": "订单已支付，无需重复处理",
            }

        if not existing:
            raise ValueError(f"订单不存在: {order_id}")

        # #15 第三方支付适配层：验签（可配置模式，见 _verify_signature）
        if not self._verify_signature(notify_data):
            raise ValueError("验签失败")

        # 更新订单状态
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """UPDATE orders
                   SET status='paid', paid_at=?, txn_id=?
                   WHERE order_id=?""",
                (now, txn_id, order_id),
            )

        return {
            "order_id": order_id,
            "status": "paid",
            "paid_at": now,
            "txn_id": txn_id,
        }

    # ========== 内部工具 ==========
    def _gen_code(self, length: int = 16) -> str:
        """生成加密安全的充值码。"""
        return secrets.token_hex(length // 2)

    # ---------- #15 第三方支付验签框架（可配置，条件一备即可启用） ----------
    # 模式（PAY_SIGN_MODE）：
    #   none        —— 不验签（阶段一/模拟回调，默认，保持现状）
    #   hmac_md5    —— HMAC-MD5（微信/支付宝常用类）
    #   hmac_sha256 —— HMAC-SHA256（通用）
    #   md5_sort    —— 参数按 key 排序拼接 + MD5（旧版接口常见）
    # 密钥从 PAY_SIGN_KEY 读取；开启方式：设 PAY_SIGN_MODE + PAY_SIGN_KEY。
    # 约定回调体 sign 字段为签名（base64/hex 视具体渠道，这里统一 hex 小写比较）。
    def _verify_signature(self, notify_data: dict) -> bool:
        mode = os.environ.get("PAY_SIGN_MODE", "none").strip().lower()
        key = os.environ.get("PAY_SIGN_KEY", "").strip()
        if mode == "none" or not key:
            # 阶段一：未配置 → 放行（保证现有模拟回调/充值码激活链路不受影响）
            return True

        sign = str(notify_data.get("sign", "") or "").strip()
        if not sign:
            return False

        # 参与签名参数：去掉 sign/sign_type/sig，其余原样
        data = {k: v for k, v in notify_data.items()
                if k not in ("sign", "sign_type", "sig")}

        if mode == "hmac_md5":
            raw = "&".join(f"{k}={v}" for k, v in sorted(data.items()))
            expect = hmac.new(key.encode("utf-8"), raw.encode("utf-8"),
                              hashlib.md5).hexdigest()
        elif mode == "hmac_sha256":
            raw = "&".join(f"{k}={v}" for k, v in sorted(data.items()))
            expect = hmac.new(key.encode("utf-8"), raw.encode("utf-8"),
                              hashlib.sha256).hexdigest()
        elif mode == "md5_sort":
            raw = "&".join(f"{k}={v}" for k, v in sorted(data.items())) + key
            expect = hashlib.md5(raw.encode("utf-8")).hexdigest()
        else:
            # 未知模式：保守拒绝（不静默放行）
            return False

        return hmac.compare_digest(sign.lower(), expect)

    # ========== 优惠券管理（#24 优惠券/限时折扣） ==========

    def generate_coupons(self, code_prefix: str, ctype: str, value: int,
                         count: int = 1, max_uses: int = 1, expires_at: str = "",
                         min_order_amount: int = 0, created_by: str = "",
                         note: str = "") -> list[dict]:
        """生成优惠券。ctype: fixed(固定金额,分) | percent(百分比,例20=打8折)。"""
        if ctype not in ("fixed", "percent"):
            raise ValueError("type must be fixed or percent")
        if count < 1 or count > 1000:
            raise ValueError("count must be 1-1000")
        if ctype == "fixed":
            value = max(0, int(value))
        else:
            value = max(1, min(99, int(value)))
        now = _now_iso()
        codes: list[dict] = []
        prefix = (code_prefix or "COUPON").strip().upper()[:8]
        with self._connect() as conn:
            for _ in range(count):
                cid = uuid.uuid4().hex[:12]
                code = f"{prefix}-{secrets.token_hex(4).upper()}"
                conn.execute(
                    """INSERT INTO coupons (id, code, type, value, min_order_amount,
                       max_uses, used_count, expires_at, enabled, created_at, created_by, note)
                       VALUES (?, ?, ?, ?, ?, ?, 0, ?, 1, ?, ?, ?)""",
                    (cid, code, ctype, value, min_order_amount,
                     max_uses, expires_at, now, created_by or "", (note or "")[:200]),
                )
                codes.append({"id": cid, "code": code, "type": ctype,
                              "value": value, "max_uses": max_uses,
                              "min_order_amount": min_order_amount,
                              "expires_at": expires_at})
        return codes

    def validate_coupon(self, code: str, order_amount: int) -> dict:
        """校验优惠券；合法返回 {id, discount, type, value}；不合法抛 ValueError。"""
        code = code.strip().upper()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM coupons WHERE code=?", (code,)
            ).fetchone()
        if row is None:
            raise ValueError("优惠券不存在")
        if not row["enabled"]:
            raise ValueError("优惠券已失效")
        if row["expires_at"] and row["expires_at"] < _now_iso():
            raise ValueError("优惠券已过期")
        if row["max_uses"] > 0 and row["used_count"] >= row["max_uses"]:
            raise ValueError("优惠券已用完")
        if row["min_order_amount"] > 0 and order_amount < row["min_order_amount"]:
            need_yuan = row["min_order_amount"] / 100
            raise ValueError(f"订单金额需满 ¥{need_yuan:.0f} 才可使用此券")
        discount = 0
        if row["type"] == "fixed":
            discount = min(row["value"], order_amount)
        else:
            discount = order_amount * row["value"] // 100
        return {"id": row["id"], "code": row["code"], "type": row["type"],
                "value": row["value"], "discount": discount}

    def _bump_coupon_count(self, coupon_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE coupons SET used_count = used_count + 1 WHERE id=?",
                (coupon_id,))

    def list_coupons(self, limit: int = 100, offset: int = 0,
                     status: str = "all", keyword: str = "") -> list[dict]:
        """优惠券列表（管理端）。status: all | enabled | disabled | expired。"""
        sql = "SELECT * FROM coupons WHERE 1=1"
        args: list = []
        now = _now_iso()
        if status == "enabled":
            sql += " AND enabled=1 AND (expires_at='' OR expires_at>?)"
            args.append(now)
        elif status == "disabled":
            sql += " AND enabled=0"
        elif status == "expired":
            sql += " AND enabled=1 AND expires_at!='' AND expires_at<=?"
            args.append(now)
        if keyword:
            sql += " AND (code LIKE ? OR note LIKE ?)"
            kw = f"%{keyword.strip()}%"
            args += [kw, kw]
        sql += " ORDER BY created_at DESC, code LIMIT ? OFFSET ?"
        args += [max(1, min(int(limit), 2000)), max(0, int(offset))]
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def count_coupons(self, status: str = "all", keyword: str = "") -> int:
        """同 list_coupons 条件的总数。"""
        sql = "SELECT COUNT(*) n FROM coupons WHERE 1=1"
        args: list = []
        now = _now_iso()
        if status == "enabled":
            sql += " AND enabled=1 AND (expires_at='' OR expires_at>?)"
            args.append(now)
        elif status == "disabled":
            sql += " AND enabled=0"
        elif status == "expired":
            sql += " AND enabled=1 AND expires_at!='' AND expires_at<=?"
            args.append(now)
        if keyword:
            sql += " AND (code LIKE ? OR note LIKE ?)"
            kw = f"%{keyword.strip()}%"
            args += [kw, kw]
        with self._connect() as conn:
            row = conn.execute(sql, args).fetchone()
        return row["n"] if row else 0

    def void_coupon(self, coupon_id: str) -> bool:
        """作废（禁用）优惠券。返回是否成功。"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE coupons SET enabled=0 WHERE id=?", (coupon_id,))
            return cur.rowcount > 0

    def coupon_stats(self) -> dict:
        """优惠券统计（管理端仪表盘）。"""
        with self._connect() as conn:
            by_type = conn.execute(
                """SELECT type, COUNT(*) total,
                   SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) active,
                   SUM(used_count) used
                   FROM coupons GROUP BY type"""
            ).fetchall()
            overall = conn.execute(
                """SELECT COUNT(*) total,
                   SUM(used_count) total_used,
                   SUM(CASE WHEN enabled=1 AND (expires_at='' OR expires_at>?)
                        THEN 1 ELSE 0 END) active
                   FROM coupons""", (_now_iso(),)
            ).fetchone()
        return {
            "overall": dict(overall) if overall else {},
            "by_type": [dict(r) for r in by_type],
        }

    def get_coupon(self, code: str) -> dict | None:
        """按码查询优惠券信息。#24 前端校验预览用。"""
        code = code.strip().upper()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM coupons WHERE code=?", (code,)
            ).fetchone()
        return dict(row) if row else None


# 模块级单例
_payment_store: Optional[PaymentStore] = None


def get_payment_store() -> PaymentStore:
    global _payment_store
    if _payment_store is None:
        _payment_store = PaymentStore()
    return _payment_store
