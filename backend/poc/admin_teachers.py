# -*- coding: utf-8 -*-
"""C2 后台业务逻辑：老师 CRUD（从 admin.py 拆分）。"""
import json
from datetime import datetime

from .config import Config
from .auth import _now
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
