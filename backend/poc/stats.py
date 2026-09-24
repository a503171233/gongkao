# -*- coding: utf-8 -*-
"""C3 · 运营数据看板 — 业务维度聚合层（纯逻辑，无 FastAPI 依赖）。

本模块只做「只读聚合 + 派生指标」，不修改 auth.db/chat.db/pay.db/metrics/guard
的任何写入路径。所有 DB 访问一律用 SQLite `mode=ro` 只读连接，杜绝误写。

数据来源：
  - auth.db/users   → 总用户/会员数/今日新增/额度分布
  - chat.db/messages+ sessions → 今日 ask / 会话数
  - pay.db/orders   → 营收（C1 未上线时表不存在 → 优雅降级）
  - metrics.snapshot() / guard_counter.stats() → 进程内运行指标（由 api.py 传入）

约定（与 02-数据契约 一致）：
  - users.created_at / messages.created_at / sessions.created_at 均为 UTC
  - auth.db 时间格式 "YYYY-MM-DD HH:MM:SS"；chat.db 为 ISO "YYYY-MM-DDTHH:MM:SS+00:00"
  - guard_counter 是进程内内存计数（重启清零、无时间戳）→ 拦截数只能给累计值
"""
import csv
import io
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# ---------- 内部工具：只读连接 ----------

def _ro(db_path: str | os.PathLike) -> sqlite3.Connection:
    """以只读模式打开 SQLite（禁止任何写操作）。文件不存在返回 None 由调用方判空。"""
    p = Path(db_path)
    if not p.exists():
        return None
    uri = f"file:{p.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


# ---------- overview：总用户/会员/今日新增/今日 ask/拦截 ----------

def overview(auth_db: str, chat_db: str, metrics_snap: dict, guard_stats: dict) -> dict:
    """运营总览。返回结构与 /api/admin/stats/overview 对齐。"""
    total_users = member_users = today_new = 0
    conn = _ro(auth_db)
    if conn is not None:
        try:
            total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            member_users = conn.execute(
                "SELECT COUNT(*) c FROM users WHERE role='member'").fetchone()["c"]
            today_new = conn.execute(
                "SELECT COUNT(*) c FROM users WHERE created_at >= ?",
                (_utc_today() + " 00:00:00",),
            ).fetchone()["c"]
        finally:
            conn.close()

    # 今日 ask = chat.db messages 里 role='user' 且 created_at 属今天（UTC）
    today_ask = 0
    total_sessions = 0
    conn = _ro(chat_db)
    if conn is not None:
        try:
            today_ask = conn.execute(
                "SELECT COUNT(*) c FROM messages WHERE role='user' AND created_at >= ?",
                (_utc_today() + "T00:00:00",),
            ).fetchone()["c"]
            total_sessions = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
        finally:
            conn.close()

    ask_total = metrics_snap.get("ask", {}).get("total", 0)
    ask_fail = metrics_snap.get("ask", {}).get("fail", 0)

    return {
        "total_users": total_users,
        "member_users": member_users,
        "member_rate": round(member_users / total_users, 4) if total_users else 0.0,
        "today_new_users": today_new,
        "today_ask": today_ask,
        "total_sessions": total_sessions,
        "ask_total": ask_total,          # 进程内累计（重启清零）
        "ask_fail": ask_fail,
        "guard_total": guard_stats.get("total", 0),  # 进程内累计拦截
        "avg_latency_ms": metrics_snap.get("avg_latency_ms", 0.0),
        "uptime_seconds": metrics_snap.get("uptime_seconds", 0),
    }


# ---------- quota：额度消耗分布（按日/用户） ----------

def quota_dist(auth_db: str) -> dict:
    """额度消耗分布。按日（quota_date 聚合 today_count）+ 按用户明细。"""
    by_day: dict[str, int] = {}
    by_user: list[dict] = []
    conn = _ro(auth_db)
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT username, role, today_count, quota_date FROM users"
            ).fetchall()
            for r in rows:
                d = dict(r)
                by_user.append({
                    "username": d["username"],
                    "role": d["role"],
                    "today_count": d["today_count"],
                    "quota_date": d["quota_date"],
                })
                qd = d["quota_date"] or ""
                if qd:
                    by_day[qd] = by_day.get(qd, 0) + (d["today_count"] or 0)
        finally:
            conn.close()

    by_user.sort(key=lambda x: x["today_count"], reverse=True)
    return {
        "by_day": [{"date": k, "used": v} for k, v in sorted(by_day.items(), reverse=True)],
        "by_user": by_user[:200],  # 前端表格防超长
        "user_count": len(by_user),
    }


# ---------- revenue：营收（依赖 C1，pay.db 不存在/无 orders 表 → 优雅降级） ----------

def revenue(pay_db: str) -> dict:
    """订单营收聚合。C1 未上线（pay.db 缺失或无 orders 表）→ 返回 enabled=false 全零。"""
    result = {
        "enabled": False,
        "order_count": 0,
        "paid_count": 0,
        "total_amount_yuan": 0.0,
        "today_paid_yuan": 0.0,
        "pending_count": 0,
    }
    conn = _ro(pay_db)
    if conn is None:
        return result
    try:
        if not _table_exists(conn, "orders"):
            return result
        result["enabled"] = True
        result["order_count"] = conn.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"]
        result["paid_count"] = conn.execute(
            "SELECT COUNT(*) c FROM orders WHERE status='paid'").fetchone()["c"]
        result["pending_count"] = conn.execute(
            "SELECT COUNT(*) c FROM orders WHERE status='pending'").fetchone()["c"]
        total = conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status='paid'"
        ).fetchone()["s"]
        today = conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status='paid' AND paid_at >= ?",
            (_utc_today() + "T00:00:00",),
        ).fetchone()["s"]
        result["total_amount_yuan"] = round(total / 100.0, 2)   # 单位：分 → 元
        result["today_paid_yuan"] = round(today / 100.0, 2)
    finally:
        conn.close()
    return result


# ---------- teachers：老师维度请求/拦截/满意度 ----------

def teacher_stats(metrics_snap: dict, guard_stats: dict,
                  teachers_meta: list[dict]) -> dict:
    """老师维度热度：请求数（by_teacher）/拦截数/满意度（派生 1-拦截/请求）。"""
    by_teacher = metrics_snap.get("by_teacher", {}) or {}
    guard_by_teacher = guard_stats.get("by_teacher", {}) or {}
    out = []
    for t in teachers_meta:
        tid = t["teacher_id"]
        req = int(by_teacher.get(tid, 0) or 0)
        block = int(guard_by_teacher.get(tid, 0) or 0)
        # 满意度 = 1 - 拦截率（无请求时记 None，前端显示 —）
        satisfaction = round(1 - (block / req), 4) if req else None
        out.append({
            "teacher_id": tid,
            "teacher_name": t.get("teacher_name", tid),
            "teacher_subject": t.get("teacher_subject", ""),
            "requests": req,
            "blocks": block,
            "block_rate": round(block / req, 4) if req else 0.0,
            "satisfaction": satisfaction,
        })
    out.sort(key=lambda x: x["requests"], reverse=True)
    return {"teachers": out}


# ---------- export：核心指标 CSV（标准库 csv + UTF-8 BOM） ----------

def export_csv(auth_db: str, chat_db: str, pay_db: str,
               metrics_snap: dict, guard_stats: dict,
               teachers_meta: list[dict]) -> str:
    """导出核心指标 CSV（UTF-8 with BOM，Excel 打开不乱码）。"""
    ov = overview(auth_db, chat_db, metrics_snap, guard_stats)
    qd = quota_dist(auth_db)
    rev = revenue(pay_db)
    ts = teacher_stats(metrics_snap, guard_stats, teachers_meta)

    buf = io.StringIO()
    buf.write("\ufeff")  # BOM
    w = csv.writer(buf)

    w.writerow(["多老师公考 RAG 平台 · 运营数据导出", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")])
    w.writerow([])

    # --- overview 区块 ---
    w.writerow(["== 总览 =="])
    for k, v in ov.items():
        w.writerow([k, v])
    w.writerow([])

    # --- 老师维度区块 ---
    w.writerow(["== 老师维度 =="])
    w.writerow(["teacher_id", "teacher_name", "teacher_subject", "requests", "blocks", "block_rate", "satisfaction"])
    for t in ts["teachers"]:
        w.writerow([t["teacher_id"], t["teacher_name"], t["teacher_subject"],
                    t["requests"], t["blocks"], t["block_rate"],
                    t["satisfaction"] if t["satisfaction"] is not None else ""])
    w.writerow([])

    # --- 额度分布区块 ---
    w.writerow(["== 额度消耗分布（按日） =="])
    w.writerow(["date", "used"])
    for d in qd["by_day"]:
        w.writerow([d["date"], d["used"]])
    w.writerow([])
    w.writerow(["== 额度消耗分布（按用户，Top200） =="])
    w.writerow(["username", "role", "today_count", "quota_date"])
    for u in qd["by_user"]:
        w.writerow([u["username"], u["role"], u["today_count"], u["quota_date"]])
    w.writerow([])

    # --- 营收区块 ---
    w.writerow(["== 营收（C1） =="])
    for k, v in rev.items():
        w.writerow([k, v])
    w.writerow([])

    # --- 最近事件 ---
    w.writerow(["== 最近请求事件 =="])
    w.writerow(["time", "method", "path", "status", "teacher_id", "ms"])
    for r in metrics_snap.get("recent", []) or []:
        w.writerow([r.get("t", ""), r.get("method", ""), r.get("path", ""),
                    r.get("status", ""), r.get("teacher_id", ""), r.get("ms", "")])

    return buf.getvalue()
