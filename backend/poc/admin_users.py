# -*- coding: utf-8 -*-
"""C2 后台业务逻辑：用户管理 + 充值码 + 后台统计（从 admin.py 拆分）。"""
from datetime import datetime, timedelta

from .config import Config
from .auth import AuthStore
from .payment import get_payment_store


# ---------- 用户管理 ----------

def list_users_admin(limit: int = 200, offset: int = 0, q: str = "",
                     tag: str = "") -> list[dict]:
    """用户列表（含 role/额度/到期/创建时间/#25 标签）。tag 非空则按标签精确过滤。"""
    auth = AuthStore()
    conds = []
    args: list = []
    if q:
        pat = f"%{q}%"
        conds.append("(username LIKE ? OR user_id LIKE ?)")
        args += [pat, pat]
    if tag:
        conds.append("tag = ?")
        args.append(tag.strip())
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    sql = ("SELECT user_id, username, role, today_count, quota_date, "
           "member_expire_at, created_at, status, tag FROM users" + where +
           " ORDER BY created_at DESC LIMIT ? OFFSET ?")
    args += [int(limit), int(offset)]
    with auth._connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def set_user_tag(user_id: str, tag: str) -> bool:
    """设置用户分群标签（#25）。空字符串=清除标签。"""
    auth = AuthStore()
    with auth._connect() as conn:
        cur = conn.execute("UPDATE users SET tag=? WHERE user_id=?",
                           ((tag or "").strip()[:30], user_id))
        return cur.rowcount > 0


def list_user_tags() -> list[dict]:
    """全部已使用标签 + 各标签用户数（#25 管理端筛选用）。"""
    auth = AuthStore()
    with auth._connect() as conn:
        rows = conn.execute(
            "SELECT tag, COUNT(*) n FROM users WHERE tag != '' "
            "GROUP BY tag ORDER BY n DESC"
        ).fetchall()
    return [{"tag": r["tag"], "count": r["n"]} for r in rows]


def get_user_admin(user_id: str) -> dict | None:
    auth = AuthStore()
    u = auth.get_user(user_id)
    if not u:
        return None
    info = auth.get_membership_info(user_id)
    return {
        "user_id": u["user_id"],
        "username": u["username"],
        "role": u["role"],
        "today_count": u.get("today_count", 0),
        "quota_date": u.get("quota_date", ""),
        "member_expire_at": u.get("member_expire_at", ""),
        "created_at": u.get("created_at", ""),
        "status": u.get("status", "active"),
        "is_member": info.get("is_member", False),
        "days_remaining": info.get("days_remaining", 0),
        "daily_limit": info.get("daily_limit", 20),
    }


def set_user_status(user_id: str, status: str) -> dict:
    """启用/禁用用户账号。status ∈ active|disabled。禁用后无法登录。"""
    status = (status or "").strip()
    if status not in ("active", "disabled"):
        raise ValueError("status 必须为 active 或 disabled")
    auth = AuthStore()
    if not auth.get_user(user_id):
        raise ValueError("用户不存在")
    with auth._connect() as conn:
        conn.execute("UPDATE users SET status=? WHERE user_id=?", (status, user_id))
        conn.commit()
    u = auth.get_user(user_id)
    return {"user_id": user_id, "username": (u or {}).get("username", ""), "status": status}


def user_stats_admin() -> dict:
    """后台用户统计：总量 / 角色分布 / 状态分布 / 今日活跃。"""
    auth = AuthStore()
    today = datetime.now().strftime("%Y-%m-%d")
    with auth._connect() as conn:
        total = conn.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        by_role = [dict(r) for r in conn.execute(
            "SELECT role, COUNT(*) n FROM users GROUP BY role ORDER BY n DESC").fetchall()]
        by_status = [dict(r) for r in conn.execute(
            "SELECT status, COUNT(*) n FROM users GROUP BY status ORDER BY n DESC").fetchall()]
        active_today = conn.execute(
            "SELECT COUNT(*) n FROM users WHERE quota_date=?", (today,)).fetchone()["n"]
        member_now = conn.execute(
            "SELECT COUNT(*) n FROM users WHERE role='member' "
            "AND member_expire_at <> '' AND member_expire_at >= ?",
            (today,)).fetchone()["n"]
    return {
        "total": total, "by_role": by_role, "by_status": by_status,
        "active_today": active_today, "member_now": member_now,
    }


def set_user_role(user_id: str, role: str, days: int = 30) -> dict:
    """手动开通/回退会员。days 仅 role=member 时生效。"""
    auth = AuthStore()
    if role not in ("free", "member", "admin"):
        raise ValueError("角色非法")
    if not auth.get_user(user_id):
        raise ValueError("用户不存在")
    if role == "admin":
        # 提权到 admin：保持当前到期时间（清空也无意义）
        auth.set_role(user_id, "admin")
    elif role == "member":
        # 续费 = 在原到期基础上叠加
        with auth._connect() as conn:
            row = conn.execute(
                "SELECT member_expire_at FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            old = (row["member_expire_at"] if row else "") or ""
            now = datetime.utcnow()
            base = now
            if old:
                try:
                    base = max(now, datetime.strptime(old[:19], "%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    base = now
            expire = base + timedelta(days=max(1, int(days)))
            expire_str = expire.strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute(
                "UPDATE users SET role='member', member_expire_at=? WHERE user_id=?",
                (expire_str, user_id),
            )
    else:
        # free
        auth.set_role(user_id, "free")
    return get_user_admin(user_id) or {}


def grant_user(user_id: str, kind: str, value: int = 0) -> dict:
    """手动充值：
    - kind='days' + value=N → 续期 N 天
    - kind='reset_today' → 重置今日已用次数
    """
    auth = AuthStore()
    if not auth.get_user(user_id):
        raise ValueError("用户不存在")
    if kind == "days":
        v = int(value)
        if v < 1 or v > 3650:
            raise ValueError("天数必须在 1~3650")
        with auth._connect() as conn:
            row = conn.execute(
                "SELECT role, member_expire_at FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            if not row:
                raise ValueError("用户不存在")
            now = datetime.utcnow()
            old = (row["member_expire_at"] if row else "") or ""
            base = now
            if old and row["role"] == "member":
                try:
                    base = max(now, datetime.strptime(old[:19], "%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    base = now
            expire = base + timedelta(days=v)
            expire_str = expire.strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute(
                "UPDATE users SET role='member', member_expire_at=? WHERE user_id=?",
                (expire_str, user_id),
            )
    elif kind == "reset_today":
        with auth._connect() as conn:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            conn.execute(
                "UPDATE users SET today_count=0, quota_date=? WHERE user_id=?",
                (today, user_id),
            )
    else:
        raise ValueError("kind 必须是 'days' 或 'reset_today'")
    return get_user_admin(user_id) or {}


# ---------- 后台统计 ----------

def admin_stats() -> dict:
    """后台首页统计：用户数/老师数/课程文档数/总块数。"""
    auth = AuthStore()
    with auth._connect() as conn:
        total_users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        members = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role='member'").fetchone()["n"]
        admins = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role='admin'").fetchone()["n"]

    cfg = Config()
    cfg.reload_teachers()
    teachers = cfg.list_teachers_all()
    enabled = sum(1 for t in teachers if t.get("enabled"))

    # 知识库总块数（跨老师）
    from . import store
    total_chunks = 0
    docs_total = 0
    for t in teachers:
        if not t.get("enabled"):
            continue
        try:
            total_chunks += store.collection_count(t["teacher_id"])
            docs_total += len(store.list_documents(t["teacher_id"]))
        except Exception:
            pass

    return {
        "users": {"total": total_users, "members": members, "admins": admins},
        "teachers": {"total": len(teachers), "enabled": enabled},
        "knowledge": {"total_chunks": total_chunks, "total_documents": docs_total},
    }


# ---------- 充值码管理（A3 后台 · #24 R4 扩展批次/渠道/作废/导入/统计） ----------

def list_recharge_codes(limit: int = 100, offset: int = 0, used_only: bool = False,
                        status: str = "all", batch_id: str = "", channel: str = "",
                        keyword: str = "") -> list[dict]:
    """充值码列表（管理端）。#24 R4：多条件筛选（状态/批次/渠道/关键词）+ 分页。

    used_only 为旧参数（向后兼容），等价于 status='used'。
    """
    if used_only and status == "all":
        status = "used"
    store = get_payment_store()
    return store.list_codes(limit=limit, offset=offset, status=status,
                            batch_id=batch_id, channel=channel, keyword=keyword)


def count_recharge_codes(status: str = "all", batch_id: str = "", channel: str = "",
                         keyword: str = "") -> int:
    """充值码总数（#24 R4，列表分页用）。"""
    return get_payment_store().count_codes(
        status=status, batch_id=batch_id, channel=channel, keyword=keyword)


def generate_recharge_codes_admin(plan: str, count: int = 1, batch_id: str = "",
                                  channel: str = "", note: str = "") -> dict:
    """生成充值码（管理端）。返回 {codes, plan, amount, batch_id}。"""
    count = max(1, min(int(count), 500))  # 单次最多 500，防滥用
    store = get_payment_store()
    codes = store.generate_recharge_codes(
        plan, count, batch_id=batch_id, channel=channel, note=note)
    p = store.list_plans_by_key(plan)
    return {"codes": codes, "count": len(codes), "plan": plan,
            "amount": p["price"] if p else 0, "batch_id": batch_id}


def reset_user_password_admin(user_id: str, new_password: str) -> dict:
    """管理员重置用户密码（无邮箱方案，管理员直改）。"""
    auth = AuthStore()
    if not auth.get_user(user_id):
        raise ValueError("用户不存在")
    if len(new_password) < 6:
        raise ValueError("新密码长度必须 ≥ 6")
    auth.set_password(user_id, new_password)
    return {"ok": True, "user_id": user_id}


# ---------- 用户管理扩展（#16 后台重构） ----------

def create_user_admin(username: str, password: str, role: str = "free") -> dict:
    """管理员新增用户。role=free|member；member 默认 30 天。"""
    auth = AuthStore()
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("用户名和密码必填")
    if len(password) < 6:
        raise ValueError("密码至少 6 位")
    try:
        user_id = auth.register(username, password)
    except ValueError:
        raise
    if role == "member":
        auth.activate_membership(user_id, 30)
    elif role == "admin":
        auth.set_role(user_id, "admin")
    return get_user_admin(user_id) or {"user_id": user_id, "username": username}


def delete_user_admin(user_id: str) -> dict:
    """删除用户（连同其 oauth 绑定）。admin 账号禁止删除自身/最后一个 admin。"""
    auth = AuthStore()
    u = auth.get_user(user_id)
    if not u:
        raise ValueError("用户不存在")
    if u.get("role") == "admin":
        with auth._connect() as conn:
            admin_count = conn.execute(
                "SELECT COUNT(*) n FROM users WHERE role='admin'").fetchone()["n"]
        if admin_count <= 1:
            raise ValueError("不能删除唯一的 admin 账号")
    with auth._connect() as conn:
        conn.execute("DELETE FROM oauth_links WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    return {"ok": True, "deleted": user_id}
