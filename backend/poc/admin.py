# -*- coding: utf-8 -*-
"""C2 后台业务逻辑：老师 CRUD + 用户管理。
不直接 import fastapi；只做纯业务方法，路由层在 api.py 装配。
"""
import json
import os
import re
from datetime import datetime, timedelta
from typing import Any

from .config import Config, get   # get：环境变量读取（技能层开关，§九 P0-3）
from .auth import AuthStore, _now
from .payment import get_payment_store
from .guard import POLICY_FIELDS


# ---------- 老师 CRUD ----------

# 老师管理扩展（2026-09-06 用户决策：固定枚举）：新增分类时改这里即可
COURSE_CATEGORIES = ("言语理解", "判断推理", "数量关系", "资料分析", "常识判断",
                     "申论", "面试", "综合")
COMPANIES = ("本机构", "华图", "粉笔", "中公", "其他")


def _validate_enum(value: str, allowed: tuple, field: str) -> str:
    """枚举字段校验：空=未分类；非空必须在枚举内。"""
    v = str(value or "").strip()
    if not v:
        return ""
    if v not in allowed:
        raise ValueError(f"{field} 必须是 {'/'.join(allowed)} 之一（留空=未分类）")
    return v


def list_teachers_admin() -> list[dict]:
    """后台用：全量老师（含 enabled=false）。"""
    cfg = Config()
    return cfg.list_teachers_all()


def _validate_teacher_payload(p: dict, partial: bool = False) -> dict:
    """校验 + 规范化 CRUD 入参。
    partial=True 用于 update：只校验存在的字段，未传则保留原值。
    """
    if not isinstance(p, dict):
        raise ValueError("请求体非字典")
    out = {}

    if not partial or "teacher_id" in p:
        tid = str(p.get("teacher_id", "")).strip()
        if not tid and not partial:
            raise ValueError("teacher_id 必填")
        if tid:
            if not tid.replace("_", "").replace("-", "").isalnum() or len(tid) > 32:
                raise ValueError("teacher_id 只能含字母数字下划线连字符，长度 ≤ 32")
            out["teacher_id"] = tid

    if not partial or "teacher_name" in p:
        name = str(p.get("teacher_name", "")).strip()
        if not name and not partial:
            raise ValueError("teacher_name 必填")
        if name:
            out["teacher_name"] = name

    if not partial or "teacher_subject" in p:
        subject = str(p.get("teacher_subject", "")).strip()
        if not subject and not partial:
            raise ValueError("teacher_subject 必填")
        if subject:
            out["teacher_subject"] = subject

    if not partial or "course_category" in p:
        out["course_category"] = _validate_enum(p.get("course_category", ""),
                                                COURSE_CATEGORIES, "course_category")

    if not partial or "company" in p:
        out["company"] = _validate_enum(p.get("company", ""), COMPANIES, "company")

    if "sort_order" in p:
        try:
            so = int(p["sort_order"])
        except (TypeError, ValueError):
            raise ValueError("sort_order 必须为整数")
        if not (0 <= so <= 9999):
            raise ValueError("sort_order 必须在 0~9999 之间")
        out["sort_order"] = so

    if not partial or "llm_model" in p:
        m = str(p.get("llm_model", "gpt-5.6-luna")).strip()
        out["llm_model"] = m or "gpt-5.6-luna"

    if "temperature" in p:
        try:
            t = float(p["temperature"])
        except (TypeError, ValueError):
            raise ValueError("temperature 必须为浮点数")
        if not (0.0 <= t <= 2.0):
            raise ValueError("temperature 必须在 0~2 之间")
        out["temperature"] = t

    if "top_n" in p:
        try:
            n = int(p["top_n"])
        except (TypeError, ValueError):
            raise ValueError("top_n 必须为整数")
        if n < 1:
            raise ValueError("top_n 必须 ≥ 1")
        out["top_n"] = n

    if "threshold" in p:
        try:
            th = float(p["threshold"])
        except (TypeError, ValueError):
            raise ValueError("threshold 必须为浮点数")
        if not (0.0 <= th <= 1.0):
            raise ValueError("threshold 必须在 0~1 之间")
        out["threshold"] = th

    if "enabled" in p:
        out["enabled"] = bool(p["enabled"])

    if "guard_config" in p:
        raw = p["guard_config"]
        if raw in (None, "", {}, []):
            out["guard_config"] = ""  # 置空 = 回退学科预设
        else:
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    raise ValueError("guard_config 必须是合法 JSON 对象")
            if not isinstance(raw, dict):
                raise ValueError("guard_config 必须是 JSON 对象")
            clean = {}
            for k, v in raw.items():
                spec = POLICY_FIELDS.get(k)
                if spec is None:
                    raise ValueError(
                        f"guard_config 未知参数: {k}（可用: {', '.join(sorted(POLICY_FIELDS))}）")
                typ, lo, hi = spec
                if isinstance(v, bool):
                    raise ValueError(f"guard_config.{k} 必须为{typ.__name__}")
                try:
                    v2 = typ(v)
                except (TypeError, ValueError):
                    raise ValueError(f"guard_config.{k} 必须为{typ.__name__}")
                if not (lo <= v2 <= hi):
                    raise ValueError(f"guard_config.{k} 超出取值范围 [{lo}, {hi}]")
                clean[k] = v2
            if {"scope_pass_ratio", "scope_confirm_ratio", "scope_block_ratio"} <= clean.keys():
                if not (clean["scope_pass_ratio"] < clean["scope_confirm_ratio"]
                        < clean["scope_block_ratio"]):
                    raise ValueError("scope 三档比例必须严格递增: pass < confirm < block")
            out["guard_config"] = json.dumps(clean, ensure_ascii=False) if clean else ""

    if partial and not out:
        raise ValueError("请求体不能为空")
    return out


def create_teacher(payload: dict) -> dict:
    """新增老师（默认 enabled=True）。teacher_id 已存在 → ValueError。"""
    data = _validate_teacher_payload(payload, partial=False)
    # create 默认值补齐：与 teachers 表 DEFAULT 一致（_validate 仅校验传入字段，
    # 未传的 temperature/top_n/threshold 不在 data 中，INSERT 直取会 KeyError → 500）
    data.setdefault("enabled", True)
    data.setdefault("temperature", 0.3)
    data.setdefault("top_n", 6)
    data.setdefault("threshold", 0.5)
    data.setdefault("course_category", "")
    data.setdefault("company", "")
    data.setdefault("sort_order", 0)
    # llm_model 在 _validate 中已默认 'gpt-5.6-luna'，无需再补
    cfg = Config()
    cfg.reload_teachers()
    with Config._teachers_lock:
        if data["teacher_id"] in Config._teachers_cache:
            raise ValueError(f"老师已存在: {data['teacher_id']}")
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with Config._teachers_connect() as conn:
            conn.execute(
                "INSERT INTO teachers(teacher_id, teacher_name, teacher_subject, "
                "llm_model, temperature, top_n, threshold, enabled, guard_config, "
                "course_category, company, sort_order, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (data["teacher_id"], data["teacher_name"], data["teacher_subject"],
                 data["llm_model"], data["temperature"], data["top_n"], data["threshold"],
                 1 if data["enabled"] else 0, data.get("guard_config", ""),
                 data["course_category"], data["company"], data["sort_order"], now, now),
            )
    Config.reload_teachers()
    # 失效该老师的检索缓存
    from . import store
    try:
        store.retrieve_cache.invalidate_prefix(f"retr|{data['teacher_id']}|")
    except Exception:
        pass
    return data


def update_teacher(teacher_id: str, payload: dict) -> dict:
    """编辑老师参数（部分字段更新）。teacher_id 不允许改。"""
    # 1. 读取现有值
    with Config._teachers_connect() as conn:
        existing = conn.execute(
            "SELECT * FROM teachers WHERE teacher_id=?", (teacher_id,)
        ).fetchone()
        if not existing:
            raise ValueError(f"老师不存在: {teacher_id}")
        row = dict(existing)
    # 2. 部分字段校验，缺省取原值
    partial = _validate_teacher_payload(dict(payload or {}), partial=True)
    for k, v in partial.items():
        row[k] = v
    row["updated_at"] = _now()
    # 3. 写回 DB（guard_config/分类/排序缺省保留原值）
    with Config._teachers_connect() as conn:
        conn.execute(
            "UPDATE teachers SET teacher_name=?, teacher_subject=?, llm_model=?, "
            "temperature=?, top_n=?, threshold=?, enabled=?, guard_config=?, "
            "course_category=?, company=?, sort_order=?, updated_at=? WHERE teacher_id=?",
            (row["teacher_name"], row["teacher_subject"], row["llm_model"],
             row["temperature"], row["top_n"], row["threshold"],
             1 if row["enabled"] else 0,
             row.get("guard_config", ""),
             row.get("course_category", ""), row.get("company", ""),
             int(row.get("sort_order") or 0),
             row["updated_at"], teacher_id),
        )
    Config.reload_teachers()
    # 老师参数变更 → 失效该老师所有检索缓存
    from . import store
    try:
        store.retrieve_cache.invalidate_prefix(f"retr|{teacher_id}|")
    except Exception:
        pass
    return dict(row)


def disable_teacher(teacher_id: str) -> dict:
    """软下线（enabled=false）。不删除数据，历史会话/已入库文档可读。"""
    return update_teacher(teacher_id, {"enabled": False})


def enable_teacher(teacher_id: str) -> dict:
    """恢复上线。"""
    return update_teacher(teacher_id, {"enabled": True})


def sort_teachers(items: list) -> dict:
    """批量保存排序权重。items = [{teacher_id, sort_order}]。
    teacher 不存在 → ValueError；空列表 → ValueError。"""
    if not isinstance(items, list) or not items:
        raise ValueError("items 不能为空")
    cfg = Config()
    cfg.reload_teachers()
    clean: list[tuple[str, int]] = []
    with Config._teachers_lock:
        cache_ids = set(Config._teachers_cache.keys())
        for it in items[:200]:  # 上限 200，防滥用
            tid = str((it or {}).get("teacher_id", "")).strip()
            if tid not in cache_ids:
                raise ValueError(f"老师不存在: {tid}")
            try:
                so = int((it or {}).get("sort_order", 0))
            except (TypeError, ValueError):
                raise ValueError(f"sort_order 必须为整数: {tid}")
            if not (0 <= so <= 9999):
                raise ValueError(f"sort_order 必须在 0~9999 之间: {tid}")
            clean.append((so, tid))
    now = _now()
    with Config._teachers_connect() as conn:
        conn.executemany(
            "UPDATE teachers SET sort_order=?, updated_at=? WHERE teacher_id=?",
            [(so, now, tid) for so, tid in clean],
        )
    Config.reload_teachers()
    return {"updated": len(clean)}


# ---------- 用户管理 ----------

def list_users_admin(limit: int = 200, offset: int = 0, q: str = "") -> list[dict]:
    """用户列表（含 role/额度/到期/创建时间）。"""
    auth = AuthStore()
    with auth._connect() as conn:
        if q:
            pat = f"%{q}%"
            rows = conn.execute(
                "SELECT user_id, username, role, today_count, quota_date, "
                "member_expire_at, created_at, status FROM users "
                "WHERE username LIKE ? OR user_id LIKE ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (pat, pat, int(limit), int(offset)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT user_id, username, role, today_count, quota_date, "
                "member_expire_at, created_at, status FROM users "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (int(limit), int(offset)),
            ).fetchall()
    return [dict(r) for r in rows]


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


# ---------- 文档预览（#16 后台重构：读取原始课件文本） ----------

def preview_document(teacher_id: str, doc_name: str, max_chars: int = 4000) -> dict:
    """读取某老师原始课件文件内容（用于后台预览）。
    文件不存在 → ValueError；只返回前 max_chars 字符。
    """
    from .config import Config
    from .ingest import extract_text
    from pathlib import Path
    cfg = Config()
    safe_name = (doc_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    path = cfg.upload_dir(teacher_id) / safe_name
    if not path.exists():
        # 也尝试直接按 teacher 数据目录查找（历史文件）
        raise ValueError(f"原始文件不存在: {safe_name}")
    try:
        text = extract_text(path)
    except ValueError as e:
        raise ValueError(str(e))
    return {
        "doc_name": safe_name,
        "teacher_id": teacher_id,
        "chars": len(text),
        "preview": text[:max_chars],
        "truncated": len(text) > max_chars,
    }


# ---------- 题库 AI 采集（#16：LLM 提取题目；#19：文件上传解析） ----------

# AI 采集可上传的格式（与 ingest.extract_text 支持面一致）
AICOLLECT_EXTS = {".md", ".txt", ".text", ".docx", ".pptx", ".pdf"}
AICOLLECT_MAX_BYTES = 50 * 1024 * 1024  # 50MB（与前端 UI 承诺、nginx client_max_body_size 对齐）


# ---------- 运行时技能层接入（《运行时AI技能与MCP能力开发说明书》§九 P0-3） ----------
# 提取 prompt 已模板化：poc/skills/prompts/_baseline-q-extract.md = 迁移前内联
# prompt 逐字节摘录（默认，行为保持）；q-extract.md = §4.1 优化模板（正向门槛+
# 注入防御），经 poc/skills/selftest 离线比对门禁后显式切换：
#   SKILL_EXTRACT_ENABLED  =0 紧急回退旧内联循环（默认 1=技能路径）
#   SKILL_EXTRACT_TEMPLATE =baseline（默认）| optimized（§4.1 新模板）
def _skill_extract_enabled() -> bool:
    return (get("SKILL_EXTRACT_ENABLED", "1") or "1").strip().lower() \
        not in ("0", "off", "false", "no")


def _extract_skill_id() -> str:
    return "q-extract"   # 运行期由 SKILL_EXTRACT_TEMPLATE 选择模板


def _llm_call_skill(material: str, teacher_subject: str, tcfg,
                    max_tokens: int = 2000, model: str | None = None,
                    temperature: float | None = None,
                    base_url: str | None = None, api_key: str | None = None):
    """技能路径提取：prompt 在 poc/skills/prompts/，解析重试在技能层，
    LLM 出口注入 _llm_call（注册表默认模型优先/全局兜底语义不变）。
    返回 SkillResult：ok=题目 list；kind=llm 时 error=底层异常原文（调用方抛转 400）。"""
    from . import skills
    return skills.run(_extract_skill_id(),
                      {"material": material, "teacher_subject": teacher_subject or "公考",
                       "allowed_types": "choice,judge,essay", "source_url": "（未提供）"},
                      tcfg=tcfg, max_tokens=max_tokens, model=model,
                      temperature=temperature, base_url=base_url, api_key=api_key,
                      llm=lambda msgs, t, **kw: _llm_call(msgs, t, **kw))


def _render_extract_system(material: str, teacher_subject: str) -> str:
    """渲染现行提取模板 system（baseline=迁移前内联 prompt 逐字节等价）。
    供 SKILL_EXTRACT_ENABLED=0 回退路径与离线比对使用。"""
    from .skills.base import get_skill, render_prompt
    system, _embedded = render_prompt(get_skill("q-extract"),
                                      {"material": material,
                                       "teacher_subject": teacher_subject or "公考",
                                       "allowed_types": "choice,judge,essay",
                                       "source_url": "（未提供）"})
    return system


def parse_collect_file(filename: str, data: bytes, teacher_id: str = "") -> dict:
    """AI 采集文件上传解析：校验格式/大小 → 临时落盘 → ingest.extract_collect。

    与「文档管理」入库同源支持面（md/txt/docx/pptx/pdf），但走采集专用解析：
    #33 R2 图片按文档流顺序内联为 ![图N](/api/qimg/{tid}/{fname}) 标记，
    图片落盘 data/qimg/{teacher_id}/（内容寻址幂等）。
    返回 {doc_name, chars, text, n_img, images}。
    """
    from .ingest import extract_collect
    import tempfile
    from pathlib import Path

    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    ext = Path(name).suffix.lower()
    if ext not in AICOLLECT_EXTS:
        raise ValueError(f"暂不支持该格式: {ext or '(无扩展名)'}（支持 md/txt/docx/pptx/pdf）")
    if not data:
        raise ValueError("文件内容为空")
    if len(data) > AICOLLECT_MAX_BYTES:
        raise ValueError(f"文件超过 50MB 上限（当前 {len(data) / 1024 / 1024:.1f}MB）")

    # 临时文件保留扩展名（extract_collect 按扩展名分发解析器）
    tmp = tempfile.NamedTemporaryFile(suffix=ext, prefix="aicollect_", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        try:
            r = extract_collect(tmp.name, teacher_id)
        except ValueError as e:
            raise ValueError(str(e).replace("入库失败:", "解析失败:"))
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    text = (r["text"] or "").strip()
    if not text and not r["n_img"]:
        raise ValueError("未能从文件中解析出文本内容")
    return {"doc_name": name, "chars": len(text), "text": text,
            "n_img": r["n_img"], "images": r["images"]}


def _is_shenlun_paper(text: str) -> bool:
    """是否申论套卷文本（含「给定资料N」且带「作答要求/申论/根据…资料」信号）。"""
    t = text or ""
    if not re.search(r"给定资料\s*\d", t):
        return False
    return bool(re.search(r"作答要求|申论|根据.{0,40}资料\s*\d", t))


def _parse_given_materials(text: str) -> dict[int, str]:
    """提取材料块 → {N: 内容}。兼容"给定资料N/给定材料N/资料N/材料N"。

    采集端正文是去标签、压缩空白后的单行长文本（无换行），不能用行首定位；
    用「前 non 参考动词」区分"材料标题"与题干里的"根据给定资料N"之类引用。
    内容截到下一材料块或作答要求。"""
    if not text:
        return {}
    # 紧邻在材料标题前的汉字若为"参考类动词"（根/据/依/由/合/读/托/绕/参…），
    # 视为题干引用（"根据给定资料N/结合材料N"），而非材料标题。
    pat = re.compile(
        r"(?<![据由合读托绕参依根定材])(?:给定资料|给定材料|资料|材料)"
        r"\s*(\d{1,2})\s*[:：]?\s*")
    matches = list(pat.finditer(text))
    if not matches:
        return {}
    mats: dict[int, str] = {}
    anq = re.search(r"作答要求", text)
    anq_start = anq.start() if anq else len(text)
    for i, m in enumerate(matches):
        num = int(m.group(1))
        end = matches[i + 1].start() if i + 1 < len(matches) else anq_start
        raw = text[m.end(): max(end, m.end())]
        content = raw.lstrip(" ：:,，。").strip()
        if content:
            mats[num] = content
    # 只保留"块较长"的材料标题，剔除误命中（如题干里的 "材料1" 引用后无大段内容）
    return {k: v for k, v in mats.items() if len(v) >= 60}


def _referenced_materials(qtext: str) -> list[int]:
    """题干中引用的给定资料编号（升序去重）。兼容"给定资料N/给定材料N/资料N/材料N"简写。"""
    return sorted({int(n)
                   for n in re.findall(r"(?:给定资料|给定材料|资料|材料)\s*(\d{1,2})", qtext or "")})


def _attach_shenlun_materials(text: str, questions: list[dict]) -> list[dict]:
    """#问题1 申论材料回填：为引用「给定资料N」的主观题，把对应资料原文
    确定性回填进 question（保证材料不被丢弃，无论分批/模型是否丢料）。
    仅对 essay 生效；无材料或非申论原样返回。"""
    mats = _parse_given_materials(text)
    if not mats:
        return questions
    for q in questions:
        qt = (q.get("question") or "").strip()
        if not qt or q.get("qtype") != "essay":
            continue
        refs = _referenced_materials(qt)
        if not refs:
            continue
        blocks = []
        for n in refs:
            if n in mats:
                blocks.append(f"【给定资料{n}】\n{mats[n]}")
        if blocks:
            q["question"] = "\n\n".join(blocks) + "\n\n" + qt
    return questions


def extract_questions_ai(text: str, teacher_subject: str = "",
                         model: str | None = None, base_url: str | None = None,
                         api_key: str | None = None) -> dict:
    """调用 LLM 从文档文本中提取题目，返回结构化 JSON。
    返回 {"questions": [{qtype, question, options, answer, analysis, difficulty, knowledge_point}]}
    """
    from . import llm
    from .config import Config

    cfg = Config()
    tcfg = cfg.get_teacher(cfg.teacher_id)  # 用默认老师配置做 LLM 参数载体
    text = (text or "").strip()
    if not text:
        raise ValueError("文档内容为空")


    # #19 长文分批：>BATCH 的文档按题号/段落边界切批逐段提取，题干去重合并
    # （修复 #16 缺陷：原先静默截断 12000 字，长文档后半部分题目全丢）
    # #25 调优：BATCH 缩至 2500（题目密集时单批 JSON 输出会超 2000 token 被截断，
    #          此前 12000 的批常整批"模型返回为空"/截断无法解析）。
    #          抽取走 max_tokens=4096；残余截断由 _salvage_json_objects 抢救，
    #          仍失败的批按 1/2 递归细分重试（深度上限 2），最大程度回收题目。
    # #35 R2：MAX_BATCHES 12→60（单请求容量 3 万→15 万字，整套试卷不再截断丢弃）；
    #          批间并发提取（网络 IO，3 并发），结果按批序回主线程去重合并。
    BATCH = 2500
    MAX_BATCHES = 60
    MAX_EXTRACT_TOKENS = 4096
    # #问题1 申论材料：申论整卷（材料 + 作答要求）一次性下发给 LLM（不外部分批），
    # 让模型在同一上下文里看到全部给定材料与作答要求，从而把引用的材料回填进题目、
    # 并产出基于材料的参考要点。仅对识别为申论且篇幅可承受的文档生效。
    shenlun_single = _is_shenlun_paper(text)
    if shenlun_single and len(text) <= 15000:
        MAX_EXTRACT_TOKENS = 9000   # 材料回填后输出量大，放宽输出上限
        BATCH = max(len(text), 2500)

    all_questions: list[dict] = []
    seen_keys: set[str] = set()

    def absorb(qs: list[dict]) -> None:
        for q in qs:
            # #28 去重键改为"题干+选项前缀"：公考客观题常出现"下列关于…正确的是"
            # 同题干不同选项的题组，按题干去重会把多题误并为 1 题；
            # 选项也相同才算边界重复（分批切点两侧提取到同一题）。
            # #33 R2 再追加"题干末 60 字"：资料分析子题 question = 共享材料 + 子题问题，
            # 前 80 字全是材料，judge/essay 无选项 → 同材料多子题曾被误并为 1 题
            # （表现为"只采集到第一题"）；子题问题位于题干末尾，末 60 字可区分。
            key = (q["question"][:80] + "|" + "".join(q.get("options") or [])[:60]
                   + "|" + q["question"][-60:])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_questions.append(q)

    # #25 问题1：模型管理页默认模型接入（未配置时回退 tcfg/老师配置）
    eff = None
    try:
        from .models import get_ai_model_store
        eff = get_ai_model_store().effective_default()
    except Exception:  # noqa: BLE001  DB 异常不阻断采集
        eff = None

    def extract_piece(piece: str, depth: int = 0) -> list[dict]:
        """单批提取；3 次重试（带原因反馈）后仍失败，则短过半递归（depth<2）。
        #29 R3 数量校验回采：解析成功但明显漏题（模型合法输出却跳过/合并题目）时，
        同样拆半重采补齐，回收"同题干被合并"与"判断题漏识别"的题目。"""
        qs: list[dict] | None = None
        err = ""
        raw = ""
        llm_kw = dict(max_tokens=MAX_EXTRACT_TOKENS,
                      model=model or (eff or {}).get("model_id"),
                      temperature=(eff or {}).get("temperature"),
                      base_url=base_url or (eff or {}).get("base_url"),
                      api_key=api_key or (eff or {}).get("api_key"))
        if _skill_extract_enabled():
            # 技能路径（默认）：prompt 在模板、解析失败带原因重试与归一化在技能层完成，
            # 消息/parser/validator/重试话术与迁移前逐字节等价（LLM 出口仍走 _llm_call）。
            r = _llm_call_skill(piece, teacher_subject, tcfg, **llm_kw)
            if r.kind == "llm":
                raise r.exc                          # 等价迁移前：_llm_call 原始异常原样上抛
            raw, err = r.raw, (r.error or "")
            if r.ok:
                qs = r.data
        else:
            # 紧急回退（SKILL_EXTRACT_ENABLED=0）：旧内联消息 + 旧重试循环，
            # system 由 baseline 模板渲染（与迁移前同一文本），行为不变。
            sys_prompt = _render_extract_system(piece, teacher_subject)
            user_prompt = f"文档内容：\n{piece}"
            for attempt in range(3):
                msgs = [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ]
                if attempt > 0:
                    msgs.append({"role": "user", "content":
                                 f"你上一次的输出无法解析（{err}）。请重新输出：必须是完整、合法、"
                                 "未截断的 JSON 数组，不要包含 JSON 以外的任何文字。"})
                raw = _llm_call(msgs, tcfg, **llm_kw)
                arr, err = _parse_llm_json_array(raw)
                if err is None:
                    qs = _normalize_questions(arr)
                    break
        if qs is not None:
            # #29 R3：漏题回采 —— 文档题目下限明显大于回收数时拆半重采补齐。
            # 原 0.6 过严（302 场景仍只回收 2）；0.85 过激进（正常场景接近完整时
            # 也拆半重采 → 重复题膨胀 18/12）。定 0.75：仅"明显漏题"触发回采，
            # 子批内仍可再回采一层，最大程度回收且不引入可见重复。
            expected = _count_question_floor(piece)
            if (expected >= 3 and len(qs) < expected * 0.75
                    and depth < 3 and len(piece) >= 400):
                extra: list[dict] = []
                for sub in _half_split(piece):
                    sub_floor = _count_question_floor(sub)
                    try:
                        sub_qs = extract_piece(sub, depth + 1)
                        # 子批仍明显漏题时再拆半重采一次取优
                        if (depth < 2 and sub_qs and sub_floor >= 2
                                and len(sub_qs) < sub_floor * 0.75):
                            try:
                                sub2 = extract_piece(sub, depth + 2)
                                if len(sub2) > len(sub_qs):
                                    sub_qs = sub2
                            except ValueError:
                                pass
                        extra.extend(sub_qs)
                    except ValueError:
                        pass
                if extra:
                    # 合并原结果 + 回采结果（重复项由外层 absorb 按题干+选项去重）
                    return qs + extra
            return qs
        # 整批失败 → 若还够长，拆半递归（防"批内题目过多/输出超限"）
        if depth < 2 and len(piece) >= 300:
            out: list[dict] = []
            for sub in _half_split(piece):
                try:
                    out.extend(extract_piece(sub, depth + 1))
                except ValueError:
                    pass
            if out:
                return out
        raise ValueError(
            f"批内题目解析失败（已自动重试 2 次）：{err}。"
            f"模型原始输出片段：{(raw or '')[:120]!r}。"
            "可尝试：拆短文档后重新提取，或改用粘贴文本方式。")

    batches = _split_batches(text, BATCH, MAX_BATCHES)
    warnings: list[str] = []
    # #35 R2：超限丢弃显式告警（不再静默）
    handled_chars = sum(len(b) for b in batches)
    if len(text) > handled_chars:
        warnings.append(
            f"文档共 {len(text)} 字，超过单次提取上限（约 {BATCH * MAX_BATCHES} 字），"
            f"后 {len(text) - handled_chars} 字未参与本次提取 — 请拆分文档分次采集补齐")
    # #35 R2：批间并发（纯网络 IO；3 并发 ≈ 22 次/分钟，低于网关 RPM 限制）
    if len(batches) > 1:
        from concurrent.futures import ThreadPoolExecutor
        results: list[list[dict] | None] = [None] * len(batches)

        def _worker(idx: int, piece: str) -> None:
            try:
                results[idx] = extract_piece(piece)
            except ValueError:
                results[idx] = None

        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = [pool.submit(_worker, i, p) for i, p in enumerate(batches)]
            for f in futs:
                f.result()
        for i, qs in enumerate(results):
            if qs is None:
                # 并发失败 → 主线程串行重试一次（保留原始报错语义）
                try:
                    absorb(extract_piece(batches[i]))
                except ValueError as e:
                    warnings.append(f"第 {i + 1}/{len(batches)} 批解析失败已跳过：{e}")
            else:
                absorb(qs)
    else:
        for bi, piece in enumerate(batches, 1):
            try:
                absorb(extract_piece(piece))
            except ValueError as e:
                raise ValueError(f"第 {bi}/{len(batches)} 批题目解析失败：{e}")

    # #35 R2：全局数量对账（估算下限 vs 实际提取数，缺口>10% 告警而非静默）
    n_expected = _count_question_floor(text)
    n_got = len(all_questions)
    if n_expected >= 3 and n_got < n_expected * 0.9:
        warnings.append(
            f"检测到原文约 {n_expected} 道题，本次实际提取 {n_got} 道，"
            "可能存在漏题 — 建议将文档拆分为更小批次重新采集补齐")
    # #问题1 申论材料回填：无论分批/模型如何，把引用的给定资料原文确定性回填进 question
    all_questions = _attach_shenlun_materials(text, all_questions)
    return {"questions": all_questions, "n_expected": n_expected,
            "warnings": warnings}


_QNUM_LINE_RE = re.compile(r"(?m)^\s*\d{1,3}[.．、)）]\s*\S")


def _split_batches(text: str, batch_size: int, max_batches: int) -> list[str]:
    """按题号边界分批（#35 R2：不拦腰截断题目）。
    切点优先级：batch_size 附近最近的题号行 → 段落边界 → 硬切。
    超 max_batches 部分丢弃（由调用方产生显式告警）。"""
    if len(text) <= batch_size:
        return [text]
    batches: list[str] = []
    rest = text
    while rest and len(batches) < max_batches:
        if len(rest) <= batch_size:
            batches.append(rest)
            break
        lo, hi = batch_size // 2, batch_size
        # 优先：题号行边界（题目起点，切在这里保证批首是完整题目）
        qcut = -1
        for m in _QNUM_LINE_RE.finditer(rest[:hi]):
            if m.start() >= lo:
                qcut = m.start()
        if qcut > 0:
            cut = qcut
        else:
            # 次选：段落边界
            cut = rest.rfind("\n", lo, hi)
            if cut == -1:
                cut = batch_size
        batches.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    return batches


def _count_question_floor(piece: str) -> int:
    """粗略估计文档题目数下限（仅用于漏题回采判定）：
    "答案：X" 出现次数与 "编号行"（如 "1. 题干" / "1、"）取较大者。
    对"同题干不同选项"题组，因每题带独立"答案："行，可正确统计为多道。"""
    ans_cnt = len(re.findall(r"答案\s*[:：]", piece))
    num_cnt = len(re.findall(r"(?m)^\s*\d{1,3}[.．、)）]\s*\S", piece))
    return max(ans_cnt, num_cnt)


def _half_split(piece: str) -> list[str]:
    """按中间最近的段落边界拆半（避免把一道题拦腰切到两个子批）。"""
    mid = len(piece) // 2
    cut = piece.rfind("\n", mid // 2, mid + mid // 2)
    if cut == -1:
        cut = mid
    return [piece[:cut], piece[cut:].lstrip("\n")]


def _effective_llm_conf(teacher_id: str = "") -> tuple:
    """(#35) 取 LLM 调用参数载体：返回 (tcfg, eff)。eff 为模型注册表默认模型配置。"""
    from .config import Config

    cfg = Config()
    tcfg = cfg.get_teacher(teacher_id or cfg.teacher_id)
    eff = None
    try:
        from .models import get_ai_model_store
        eff = get_ai_model_store().effective_default()
    except Exception:  # noqa: BLE001  DB 异常不阻断
        eff = None
    return tcfg, eff


def _llm_call(messages, tcfg, max_tokens: int = 2000, model: str | None = None,
              temperature: float | None = None,
              base_url: str | None = None, api_key: str | None = None) -> str:
    """#35 修复：管理端 LLM 调用统一封装 —— 注册表默认模型优先，通道失败回退全局主模型。

    背景：模型注册表默认模型（eff）在网关侧可能无可用渠道（503 model_not_found），
    且 llm.call_llm 的 FALLBACK_MODELS 复用同一通道无法兜底；全局 .env 主模型
    （LLM_MODEL + API_BASE_URL）是独立兜底路径。两路全败时抛 ValueError
    （带模型名与处置建议），由各端点转 400 可读提示，替代裸 500。"""
    from . import llm
    has_eff_route = any(x is not None for x in (model, temperature, base_url, api_key))
    try:
        return llm.call_llm(messages, tcfg, max_tokens=max_tokens, model=model,
                            temperature=temperature, base_url=base_url, api_key=api_key)
    except ValueError:
        raise
    except Exception as first_err:
        if not has_eff_route:
            raise
        try:
            return llm.call_llm(messages, tcfg, max_tokens=max_tokens)
        except Exception as second_err:
            raise ValueError(
                f"AI 服务暂不可用：模型 {model or tcfg.llm_model} 调用失败"
                f"（{str(first_err)[:90]}），全局模型 {tcfg.llm_model} 兜底亦失败"
                f"（{str(second_err)[:90]}）。请稍后重试，或在「AI 模型管理」更换默认模型。")


def categorize_questions_ai(teacher_id: str, qids: list[int] | None = None,
                            limit: int = 50) -> dict:
    """#35 R1 批量 AI 补充课程分类：一次 LLM 调用判定 ≤50 道题的大类并落库。
    qids 为空时自动取该老师 category 为空的题。返回 {updated, failed, total}。"""
    from . import llm
    from .study import get_study_store, normalize_category

    st = get_study_store()
    limit = max(1, min(50, int(limit or 50)))
    qs = st.list_uncategorized_questions(teacher_id, limit=limit, qids=qids)
    if not qs:
        return {"updated": 0, "failed": 0, "total": 0,
                "message": "没有待分类的题目（全部题目已有分类）"}

    tcfg, eff = _effective_llm_conf(teacher_id)
    sys_prompt = (
        "你是公考教研助手。给定题目列表（id/题型/题干摘录），为每道题判定课程大类。\n"
        "分类只能从 [\"言语理解\",\"判断推理\",\"数量关系\",\"资料分析\",\"常识判断\","
        "\"申论\",\"面试\",\"综合\"] 中选恰好一个：\n"
        "片段阅读/逻辑填空/语句表达→言语理解；图形推理/定义判断/类比推理/逻辑判断→判断推理；"
        "数学运算/数字推理→数量关系；图表资料计算→资料分析；"
        "政治/法律/科技/人文/地理常识→常识判断；大作文/概括/对策→申论；"
        "结构化面试问答→面试；无法判断→综合。\n"
        "输出严格 JSON 数组（不要任何其他文字）："
        '[{"id": 题目id, "category": "分类"}]，每道输入题目都必须恰好输出一项。'
    )
    items = []
    for q in qs:
        snippet = (q.get("question") or "").strip().replace("\n", " ")[:120]
        snippet = snippet.replace('"', "'").replace("\\", "")
        items.append(f'{{"id": {int(q["id"])}, "qtype": "{q.get("qtype") or "essay"}", '
                     f'"question": "{snippet}"}}')
    user_prompt = "题目列表：\n[" + ",\n".join(items) + "]"
    raw = _llm_call(
        [{"role": "system", "content": sys_prompt},
         {"role": "user", "content": user_prompt}],
        tcfg, max_tokens=2048,
        model=(eff or {}).get("model_id"),
        temperature=(eff or {}).get("temperature"),
        base_url=(eff or {}).get("base_url"),
        api_key=(eff or {}).get("api_key"),
    )
    arr, err = _parse_llm_json_array(raw)
    if err is not None:
        raise ValueError(f"AI 分类输出解析失败：{err}")
    id2cat: dict[int, str] = {}
    for it in arr if isinstance(arr, list) else []:
        if isinstance(it, dict) and "id" in it:
            cat = normalize_category(it.get("category"))
            if cat:
                try:
                    id2cat[int(it["id"])] = cat
                except (TypeError, ValueError):
                    pass
    updated = 0
    failed = 0
    for q in qs:
        cat = id2cat.get(int(q["id"]))
        if cat:
            try:
                st.update_question(int(q["id"]), category=cat)
                updated += 1
                continue
            except ValueError:
                pass
        failed += 1
    return {"updated": updated, "failed": failed, "total": len(qs)}


def generate_ai_analysis(q: dict) -> str:
    """#35 R3 单题多角度 AI 解析（审题/思路/选项分析/易错警示/考点归纳）。
    q 为 question_bank 行 dict；返回生成的解析文本（纯文本，含【】段落标签）。"""
    from . import llm

    tcfg, eff = _effective_llm_conf(q.get("teacher_id") or "")
    qtype = q.get("qtype") or "essay"
    type_name = {"choice": "选择题", "judge": "判断题", "essay": "简答题"}.get(qtype, "题目")
    opts = "\n".join(q.get("options") or [])
    mid_rule = ("【选项分析】逐项分析每个选项：正确的为什么对，错误的错在哪；\n"
                if qtype == "choice" else
                "【判定依据】给出判断对/错的依据；\n" if qtype == "judge" else
                "【评分要点】列出参考答案的得分点与作答组织结构；\n")
    sys_prompt = (
        "你是资深公考教研专家，擅长从多角度深入解析题目。请对给定题目输出结构化解析，"
        "使用以下固定段落标签（【】形式），输出纯文本：\n"
        "【审题】拆解题干关键信息、限定条件与设问指向；\n"
        "【思路】给出解题路径（方法/公式/切入点），说明为什么这样入手；\n"
        + mid_rule +
        "【易错警示】指出常见误区、易混淆点与命题陷阱；\n"
        "【考点归纳】总结核心考点，并延伸同类题的备考建议。\n"
        "要求：内容具体、贴合本题，不要空话套话；每个段落 2~5 句。"
    )
    user_prompt = f"题目类型：{type_name}\n题干：{q.get('question') or ''}\n"
    if opts:
        user_prompt += f"选项：\n{opts}\n"
    user_prompt += f"正确答案：{q.get('answer') or ''}\n"
    if q.get("knowledge_point"):
        user_prompt += f"参考知识点：{q['knowledge_point']}\n"
    if "![" in (q.get("question") or ""):
        user_prompt += ("（题干中的 ![图N](...) 为题目配图标记，图片内容不可见；"
                        "请按该题型的通用解题方法进行分析）\n")
    out = _llm_call(
        [{"role": "system", "content": sys_prompt},
         {"role": "user", "content": user_prompt}],
        tcfg, max_tokens=2048,
        model=(eff or {}).get("model_id"),
        temperature=(eff or {}).get("temperature"),
        base_url=(eff or {}).get("base_url"),
        api_key=(eff or {}).get("api_key"),
    )
    text = (out or "").strip()
    if not text:
        raise ValueError("AI 未返回解析内容（模型输出为空）")
    return text


# ---------- #24 R1：LLM JSON 多策略容错解析 ----------

def _parse_llm_json_array(raw: str) -> tuple[list, str | None]:
    """LLM 原始输出 → (数组, 错误原因)。成功时错误为 None。
    策略：①剥 ``` 包裹取 [..] 直接解析 → ②修复尾逗号/行注释重试
    → ③截断/损坏抢救（扫描平衡 {} 逐对象解析）。"""
    s = (raw or "").strip()
    if not s:
        return [], "模型返回为空"
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    start = s.find("[")
    if start == -1:
        # 退化：模型只输出了单个对象 {...} → 包成数组
        ob, cb = s.find("{"), s.rfind("}")
        if ob != -1 and cb > ob:
            s = "[" + s[ob:cb + 1] + "]"
            start = 0
        else:
            return [], "输出中未找到 JSON 数组（模型未按要求的格式返回）"
    end = s.rfind("]")
    frag = s[start:end + 1] if end > start else None
    if frag is None:
        # 有 [ 无 ]：输出被截断 → 抢救已完整的对象
        objs = _salvage_json_objects(s[start:])
        if objs:
            return objs, None
        return [], "JSON 被截断（缺少结尾 ]，疑似超出模型单次输出长度）"
    # 策略 1：直接解析
    try:
        arr = json.loads(frag)
        if isinstance(arr, list):
            return arr, None
        return [], "顶层不是数组"
    except ValueError:
        pass
    # 策略 2：修复常见语法问题（尾逗号 / // 行注释 / 全角引号）
    fixed = re.sub(r",\s*([\]}])", r"\1", frag)
    fixed = re.sub(r"//[^\n\"]*\n", "\n", fixed)
    fixed = fixed.replace("“", '"').replace("”", '"')
    try:
        arr = json.loads(fixed)
        if isinstance(arr, list):
            return arr, None
    except ValueError:
        pass
    # 策略 3：逐对象抢救（截断在数组中间 / 个别对象损坏）
    objs = _salvage_json_objects(frag)
    if objs:
        return objs, None
    try:
        json.loads(frag)
    except ValueError as e:
        return [], f"JSON 语法错误: {str(e)[:100]}"
    return [], "无法解析"


def _salvage_json_objects(s: str | list | dict) -> list[dict]:
    """扫描平衡的顶层 {...} 对象逐个解析（容错截断与个别损坏对象）。
    字符串感知：引号内的 {}/, 不影响括号深度。
    仅从顶层 { 开始收集对象缓冲（忽略数组开头的 [ 与前导噪声），
    因此即便整个数组被截断、结尾缺 ]，完整对象也能逐个抢救出来。"""
    if isinstance(s, (list, dict)):  # 已是对象/数组，直接喂给调用方兜底
        return []
    out: list[dict] = []
    depth = 0
    buf: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            buf.append(ch)
        elif ch == "{":
            depth += 1
            buf.append(ch)
        elif ch == "}":
            depth -= 1
            buf.append(ch)
            if depth == 0:
                cand = "".join(buf)
                for attempt in (cand, re.sub(r",\s*([\]}])", r"\1", cand)):
                    try:
                        obj = json.loads(attempt)
                        if isinstance(obj, dict):
                            out.append(obj)
                            break
                    except ValueError:
                        continue
                buf = []
            elif depth < 0:
                break  # 结构异常，停止
        elif depth >= 1:
            buf.append(ch)
    return out


def _clean_img_marks(text: str) -> str:
    """#33 R2 题干图片标记清洗：只保留合法 ![图N](/api/qimg/...) 标记。

    LLM 可能改写/编造标记（外部 URL、伪造 fname），前端渲染白名单只认
    /api/qimg/{tid}/{sha1前12}.{ext}，这里同步清洗防止死链与脏数据入库。
    #35 R2 修复：正则补 alt 捕获组（原单组下 m.group(2) 触发 IndexError，
    图形推理等含图片标记的题干首次真正触发该路径）。
    """
    from .ingest import QIMG_URL_RE

    def _sub(m: re.Match) -> str:
        return m.group(0) if QIMG_URL_RE.match(m.group(2)) else ""

    return re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", _sub, text)


def _extract_img_urls(text: str) -> list[str]:
    """从题干提取合法图片 URL 列表（委托 ingest.extract_img_urls，#33 R2）。"""
    from .ingest import extract_img_urls

    return extract_img_urls(text)


def _normalize_questions(arr: list) -> list[dict]:
    """LLM 数组 → 规范化题目列表（字段校验与缺省兜底）。
    兼容模型常见输出变体：#28 实测 gpt-5.6-luna 常输出
    qtype="单选题"/"判断题"、options 为对象 {"A":...}、difficulty="中等"，
    原实现会丢题型/丢选项/int("中等") 崩溃，这里统一归一化。"""
    if not isinstance(arr, list):
        raise ValueError("AI 返回不是题目数组")
    qtype_map = {
        "choice": "choice", "选择": "choice", "单选题": "choice", "单选": "choice",
        "选择题": "choice", "多选": "choice", "多选题": "choice",
        "judge": "judge", "判断": "judge", "判断题": "judge",
        "essay": "essay", "简答": "essay", "简答题": "essay", "主观题": "essay",
        "论述": "essay", "论述题": "essay", "材料题": "essay", "综合": "essay",
    }
    diff_map = {"很简单": 1, "简单": 1, "易": 1, "较易": 1,
                "较简单": 2, "偏易": 2,
                "中等": 3, "中": 3, "一般": 3,
                "较难": 4, "偏难": 4, "困难": 4, "难": 4,
                "很难": 5, "极难": 5, "最难": 5,
                "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
    questions = []
    for it in arr:
        if not isinstance(it, dict) or not str(it.get("question") or "").strip():
            continue
        qtype = qtype_map.get(str(it.get("qtype", "")).strip().lower(), "essay")
        # options：兼容数组 ["A. x"] 与对象 {"A": "x"}；非选择题强制空
        raw_opts = it.get("options")
        opts: list = []
        if isinstance(raw_opts, dict):
            opts = [f"{k}. {v}" for k, v in raw_opts.items() if str(v).strip()]
        elif isinstance(raw_opts, list):
            opts = [str(o).strip() for o in raw_opts if str(o).strip()]
        if qtype != "choice":
            opts = []
        # difficulty：兼容数字与中文
        dv = str(it.get("difficulty", "")).strip().lower()
        try:
            diff = max(1, min(5, int(dv or 1)))
        except (TypeError, ValueError):
            diff = diff_map.get(dv, 3)
        q = {
            "qtype": qtype,
            "question": _clean_img_marks(str(it.get("question", "")).strip()),
            "options": opts,
            "answer": str(it.get("answer", "")).strip(),
            "analysis": str(it.get("analysis", "")).strip(),
            "difficulty": diff,
            "knowledge_point": str(it.get("knowledge_point", "")).strip(),
        }
        # #35 R1：课程分类归一化（别名映射→枚举校验→非法兜底"综合"）
        from .study import normalize_category
        q["category"] = normalize_category(it.get("category")) or "综合"
        # #60：题号剥离后写入 number（整数；供采集端拼来源"…第N题"，不入库）
        try:
            q["number"] = int(it.get("number"))
        except (TypeError, ValueError):
            q["number"] = None
        # #33 R2：题干合法图片 URL 提取入 images（入库 question_bank.images 列）
        q["images"] = _extract_img_urls(q["question"])
        questions.append(q)
    return questions


def _normalize_extracted(raw: str) -> list[dict]:
    """兼容入口（旧契约）：解析失败抛 ValueError（消息含具体原因）。"""
    arr, err = _parse_llm_json_array(raw)
    if err is not None:
        raise ValueError(f"AI 输出解析失败：{err}")
    return _normalize_questions(arr)


# ---------- 知识体系 AI 思维导图（#21：LLM 节点增强） ----------

def enhance_mindmap(teacher_id: str, root_id: str = "", save_back: bool = False) -> dict:
    """AI 思维导图：取知识树 → LLM 为 description 为空的节点生成一句话考点说明 →
    返回增强后的嵌套树 + Markdown + FreeMind 三格式。
    save_back=True 时把增强结果回写 knowledge_nodes.description（数据联动）。
    """
    from . import llm
    from .config import Config
    from .knowledge import get_knowledge_store

    ks = get_knowledge_store()
    cfg = Config()
    tcfg = cfg.get_teacher(teacher_id)
    subject = getattr(tcfg, "teacher_subject", "") if tcfg else ""

    tree = ks.to_nested(teacher_id, root_id)

    # 收集待增强节点（BFS，上限 60 个防 LLM 滥用/超时）
    pending: list[dict] = []
    queue = [tree]
    while queue:
        cur = queue.pop(0)
        for c in cur.get("children", []):
            if not (c.get("description") or "").strip():
                pending.append(c)
            queue.append(c)
    if len(pending) > 60:
        pending = pending[:60]

    title = tree.get("name") if tree.get("node_id") else f"{subject or '公考'}知识体系"

    if pending:
        # 一次性让 LLM 批量生成（node_id 定位，严格 JSON）
        items = [{"node_id": n["node_id"], "name": n["name"],
                  "path": _node_path(tree, n["node_id"])} for n in pending]
        sys_prompt = (
            "你是公考教研专家。为知识体系的每个节点生成一句话考点说明（≤40字，"
            "说明该知识点的核心考查内容或易错点）。输出严格 JSON 数组，不要输出 JSON 以外的文字："
            '[{"node_id": "与输入一致", "description": "一句话说明"}]'
            f"该科目方向：{subject or '公考'}。"
        )
        user_prompt = "知识节点列表：\n" + json.dumps(items, ensure_ascii=False)
        eff = _aim_eff()
        raw = _llm_call(
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": user_prompt}],
            tcfg,
            base_url=(eff or {}).get("base_url") if eff else None,
            api_key=(eff or {}).get("api_key") if eff else None,
        )
        descs = _parse_mindmap_enhance(raw)
        for n in pending:
            d = descs.get(n["node_id"])
            if d:
                n["description"] = d[:500]

    if save_back:
        for n in pending:
            if (n.get("description") or "").strip():
                try:
                    ks.update_node(n["node_id"], description=n["description"])
                except ValueError:
                    pass  # 回写失败不阻断导出

    return {
        "tree": tree,
        "title": title,
        # 注意：基于增强后的内存树生成（save_back=False 时重查库会丢增强结果）
        "markdown": ks.to_markdown(teacher_id, root_id, title=title, tree=tree),
        "freemind": ks.to_freemind(teacher_id, root_id, title=title, tree=tree),
        "enhanced": len(pending),
        "total_nodes": _count_nodes(tree),
    }


def _node_path(tree: dict, node_id: str) -> str:
    """从树根到目标节点的名称路径（给 LLM 上下文）。"""
    def walk(node: dict, trail: list[str]):
        if node.get("node_id") == node_id:
            return trail + [node.get("name", "")]
        for c in node.get("children", []):
            r = walk(c, trail + [node.get("name", "")])
            if r is not None:
                return r
        return None
    p = walk(tree, [])
    return " / ".join(x for x in (p or []) if x)


def _count_nodes(tree: dict) -> int:
    n = 0
    queue = [tree]
    while queue:
        cur = queue.pop(0)
        n += len(cur.get("children", []))
        queue.extend(cur.get("children", []))
    return n


def _parse_mindmap_enhance(raw: str) -> dict[str, str]:
    """LLM 增强 JSON → {node_id: description}。解析失败返回空 dict（导图仍可纯结构生成）。"""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return {}
    try:
        arr = json.loads(raw[start:end + 1])
    except ValueError:
        return {}
    out: dict[str, str] = {}
    if isinstance(arr, list):
        for it in arr:
            if isinstance(it, dict) and it.get("node_id") and (it.get("description") or "").strip():
                out[str(it["node_id"])] = str(it["description"]).strip()
    return out


# ---------- #24 R3：文档建树 / 联网搜索建树（知识体系自动编写） ----------

# LLM 输入资料截断上限（字符）：建树只需大纲级信息，过长反而稀释注意力
BUILD_TREE_MAX_CHARS = 12000
# 单次建树节点数上限与层级深度上限（防 LLM 失控输出 / 滥用）
_BUILD_TREE_MAX_NODES = 50
_BUILD_TREE_MAX_DEPTH = 4


def _llm_build_knowledge_tree(material: str, subject: str, source_hint: str) -> list[dict]:
    """把整理好的资料文本交给 LLM → 多级知识点树 JSON（复用 R1 容错解析器）。

    返回 [{"name","description","children":[...]}] 嵌套列表；失败抛 ValueError（带原因）。
    """
    from . import llm

    sys_prompt = (
        "你是公考教研专家。请把给定资料整理成多级知识点树，用于构建该科目方向的知识体系。"
        f"科目方向：{subject or '公考'}。资料来源：{source_hint}。\n"
        "要求：\n"
        "1. 顶层按大模块划分（如 判断推理/言语理解/数量关系…），每个大模块下细分小类，"
        "最多 4 层、总计不超过 40 个节点；\n"
        "2. 每个节点必须有简短名称（≤20字），并为每层节点写一句话考点说明（description，≤50字）；\n"
        "3. 只依据资料内容归纳，资料未覆盖的常见考点可补充但需通用准确；\n"
        "4. 输出严格 JSON 数组（顶层为各大模块），不要输出 JSON 以外的任何文字：\n"
        '[{"name": "模块名", "description": "一句话说明", "children": ['
        '{"name": "子类名", "description": "…", "children": []}]}]'
    )
    user_prompt = f"资料内容：\n{material[:BUILD_TREE_MAX_CHARS]}"
    raw = ""
    for attempt in range(3):  # 对齐 R1：解析失败带错误原因重试
        msgs = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt}]
        if attempt > 0:
            msgs.append({"role": "user", "content":
                         "你上一次的输出无法解析（结构必须是 JSON 数组且含 name 字段）。"
                         "请重新输出完整、合法、未截断的 JSON。"})
        eff = _aim_eff()
        raw = _llm_call(
            msgs, _build_tree_tcfg(),
            model=(eff or {}).get("model_id") if eff else None,
            temperature=(eff or {}).get("temperature") if eff else None,
            base_url=(eff or {}).get("base_url") if eff else None,
            api_key=(eff or {}).get("api_key") if eff else None,
        )
        arr, err = _parse_llm_json_array(raw)
        if err is None and isinstance(arr, list) and arr:
            return arr
        err = err or "输出为空数组"
    raise ValueError(
        f"AI 生成知识点树失败（已自动重试 2 次）：{err}。"
        f"模型原始输出片段：{(raw or '')[:120]!r}。可缩短资料后重试。")


def _build_tree_tcfg():
    """建树用全局默认老师配置（管理端操作，不绑定具体老师的模型偏好）。"""
    from .config import Config
    return Config().get_teacher(Config().teacher_id)


def _aim_eff() -> dict | None:
    """平台级 AI 生效配置（模型管理页默认模型；含模型自身通道 base_url/api_key）。
    DB 异常返回 None → 调用方回退全局 .env 配置。"""
    try:
        from .models import get_ai_model_store
        return get_ai_model_store().effective_default()
    except Exception:  # noqa: BLE001  DB 异常不阻断
        return None


def _create_nodes_recursive(ks, teacher_id: str, parent_id: str,
                            children: list, tree_type: str,
                            depth: int = 1, counter: list | None = None) -> int:
    """递归落库 LLM 生成的嵌套树 → knowledge_nodes。返回创建节点数。

    硬限制：深度 ≤ 4 层、总数 ≤ 50（超限截断，保证建树永远可控）。
    """
    counter = counter if counter is not None else [0]
    created = 0
    for it in children or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name or counter[0] >= _BUILD_TREE_MAX_NODES or depth > _BUILD_TREE_MAX_DEPTH:
            continue
        node = ks.create_node(
            teacher_id, name[:50], parent_id=parent_id,
            description=str(it.get("description") or "").strip()[:500],
            tree_type=tree_type)
        counter[0] += 1
        created += 1
        created += _create_nodes_recursive(
            ks, teacher_id, node["node_id"], it.get("children"),
            tree_type, depth + 1, counter)
    return created


def build_tree_from_document(teacher_id: str, doc_name: str, root_name: str = "",
                             parent_id: str = "", tree_type: str = "knowledge",
                             save: bool = True) -> dict:
    """#24 R3 文档建树：读取该老师已上传的文档全文 → LLM 整理知识点清单 → 落库成多级树。

    save=False 为预演模式：只返回 LLM 生成的树结构（children 嵌套），不落库。
    """
    from .config import Config
    from .ingest import extract_text
    from .knowledge import get_knowledge_store

    cfg = Config()
    tcfg = cfg.get_teacher(teacher_id)
    subject = getattr(tcfg, "teacher_subject", "") if tcfg else ""

    safe_name = (doc_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    path = cfg.upload_dir(teacher_id) / safe_name
    if not path.exists():
        raise ValueError(f"原始文件不存在: {safe_name}（请先在「文档管理」上传该文档）")
    try:
        text = extract_text(path)
    except ValueError as e:
        raise ValueError(str(e).replace("入库失败:", "解析失败:"))
    text = (text or "").strip()
    if not text:
        raise ValueError(f"未能从 {safe_name} 中解析出文本内容")

    arr = _llm_build_knowledge_tree(text, subject, f"老师课件《{safe_name}》")

    if not save:
        return {"doc_name": safe_name, "chars": len(text), "preview_tree": arr,
                "would_create": _count_preview_nodes(arr), "created": 0, "saved": False}

    ks = get_knowledge_store()
    root_id = (parent_id or "").strip()
    root_name = (root_name or "").strip()
    if root_id:
        ks.get_node(root_id)  # 校验目标父节点存在（不存在会抛错）
    elif root_name:
        root_id = ks.create_node(teacher_id, root_name[:50],
                                 tree_type=tree_type)["node_id"]
    created = _create_nodes_recursive(ks, teacher_id, root_id, arr, tree_type)
    if not root_id and not created:
        raise ValueError("AI 未生成任何有效知识点，未落库")
    return {
        "doc_name": safe_name, "chars": len(text),
        "root_id": root_id, "created": created, "saved": True,
        "preview_tree": arr if not created else None,
    }


def _count_preview_nodes(arr: list) -> int:
    n = 0
    stack = list(arr or [])
    while stack:
        it = stack.pop()
        if isinstance(it, dict) and (it.get("name") or "").strip():
            n += 1
            stack.extend(it.get("children") or [])
    return n


def enhance_tree_from_search(teacher_id: str, query: str, root_id: str = "",
                             root_name: str = "", tree_type: str = "knowledge",
                             save: bool = True, allow_fallback: bool = True) -> dict:
    """#24 R3 联网搜索建树：web_search 检索资料 → LLM 归纳成知识点树 → 落库。

    allow_fallback=True 时搜索失败不阻断：基于 LLM 已有知识继续生成，warning 透传前端。
    """
    from .config import Config
    from .knowledge import get_knowledge_store
    from .search import web_search, search_available

    cfg = Config()
    tcfg = cfg.get_teacher(teacher_id)
    subject = getattr(tcfg, "teacher_subject", "") if tcfg else ""

    q = (query or "").strip()
    if not q:
        raise ValueError("搜索关键词不能为空")
    if not search_available():
        raise ValueError("联网搜索未启用（服务器 SEARCH_PROVIDER=none），无法使用该功能")

    results, warning = web_search(q, max_results=6)
    material = ""
    source_hint = "联网搜索资料"
    if results:
        parts = [f"【{i + 1}】{r['title']}\n{r['snippet']}" for i, r in enumerate(results)]
        material = "\n\n".join(parts)
        source_hint = f"联网搜索「{q}」的 {len(results)} 条资料"
    elif not allow_fallback:
        raise ValueError(f"联网搜索失败，已终止：{warning}")

    arr = _llm_build_knowledge_tree(
        material or f"主题：{q}（无搜索资料，请基于你掌握的公考知识整理）",
        subject, source_hint if results else f"基于模型已有知识（搜索失败：{warning[:80]}）")

    if not save:
        return {"query": q, "results": len(results), "warning": warning,
                "preview_tree": arr, "would_create": _count_preview_nodes(arr),
                "created": 0, "saved": False}

    ks = get_knowledge_store()
    target = (root_id or "").strip()
    rid = target
    if not rid:
        rname = (root_name or "").strip() or q[:50]
        rid = ks.create_node(teacher_id, rname[:50], tree_type=tree_type)["node_id"]
    created = _create_nodes_recursive(ks, teacher_id, rid, arr, tree_type)
    if not created:
        raise ValueError("AI 未生成任何有效知识点，未落库")
    return {"query": q, "results": len(results), "warning": warning,
            "root_id": rid, "created": created, "saved": True}


# ---------- C4 数据备份 / 恢复（源文件级，安全边界明确） ----------

def list_backups_admin(limit: int = 50) -> list[dict]:
    """列出备份归档（data_dir/backups/backup_*.tar.gz，新的在前）。"""
    import time as _t
    bak_dir = Config.data_dir / "backups"
    if not bak_dir.exists():
        return []
    out = []
    for p in sorted(bak_dir.glob("backup_*.tar.gz"), key=lambda x: x.name, reverse=True)[:limit]:
        st = p.stat()
        out.append({
            "name": p.name,
            "size": st.st_size,
            "mtime": _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(st.st_mtime)),
        })
    return out


def create_backup_admin() -> dict:
    """打包数据目录：raw 源文件 + 全部 *.db（auth/teachers/questions/pay 等）。
    不打包向量库与模型缓存（可重建/可下载），控制归档体积。
    时间戳命名，并发创建不互相覆盖。"""
    import tarfile
    import time as _t
    data = Config.data_dir
    bak_dir = data / "backups"
    bak_dir.mkdir(parents=True, exist_ok=True)
    name = f"backup_{_t.strftime('%Y%m%d_%H%M%S')}.tar.gz"
    path = bak_dir / name
    with tarfile.open(str(path), "w:gz") as tf:
        raw = data / "raw"
        if raw.exists():
            tf.add(str(raw), arcname="raw")
        for db in sorted(data.glob("*.db")):
            try:
                tf.add(str(db), arcname=db.name)
            except Exception:  # noqa: BLE001 数据库被占用时跳过，不影响整体备份
                print(f"[backup] 跳过 {db.name}: 文件被占用")
    st = path.stat()
    return {
        "name": name,
        "size": st.st_size,
        "created_at": _t.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "已归档 raw 源文件与数据库；向量库/模型缓存不打包（可重建）",
    }


def restore_backup_admin(name: str) -> dict:
    """恢复归档中的 raw 源文件（解压合并进 data/raw，同名覆盖）。
    安全边界：只恢复源文件，不替换 db/向量库——避免在线热替换造成数据不一致。
    如需重建向量库，恢复后在文档管理页重新入库。"""
    import shutil
    import tarfile
    if not re.fullmatch(r"backup_\d{8}_\d{6}\.tar\.gz", name or ""):
        raise ValueError("备份名不合法")
    data = Config.data_dir
    path = data / "backups" / name
    if not path.exists():
        raise ValueError(f"备份不存在: {name}")
    tmp = data / ".restore_tmp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    restored, skipped = 0, 0
    with tarfile.open(str(path), "r:gz") as tf:
        for m in tf.getmembers():
            if not (m.name.startswith("raw/") or m.name == "raw"):
                continue
            m.name = m.name[len("raw/"):] if m.name.startswith("raw/") else ""
            if not m.isfile() or not m.name:  # 只解压普通文件
                continue
            m.name = m.name.lstrip("/\\")
            if ".." in m.name.split("/"):
                skipped += 1
                continue
            tf.extract(m, str(tmp))  # 先解压到临时目录，再合并
            restored += 1
    raw = data / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for f in tmp.rglob("*"):
        if f.is_file():
            rel = f.relative_to(tmp)
            dest = raw / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(f.read_bytes())
    shutil.rmtree(tmp, ignore_errors=True)
    return {
        "restored_files": restored,
        "skipped": skipped,
        "note": "已恢复 raw 源文件；向量库与数据库未改动（避免在线不一致）",
    }


def backup_disk_usage() -> dict:
    """备份目录磁盘占用概览（运维看板用）。"""
    import time as _t
    bak_dir = Config.data_dir / "backups"
    files = sorted(bak_dir.glob("backup_*.tar.gz")) if bak_dir.exists() else []
    total = sum(p.stat().st_size for p in files)
    return {
        "dir": str(bak_dir),
        "count": len(files),
        "total_bytes": total,
        "newest": files[-1].name if files else "",
        "oldest": files[0].name if files else "",
        "updated_at": _t.strftime("%Y-%m-%d %H:%M:%S"),
    }
