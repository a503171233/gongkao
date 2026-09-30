# -*- coding: utf-8 -*-
"""自动采集守候：在低负载窗口（CPU 与内存使用率均 < 阈值）自动扩充题库。

三个来源（按需求确认）：
  1. 公开题库网址 —— AUTOCOLLECT_URLS（逗号/换行分隔），抓取网页文本后 LLM 提取题目；
  2. 已有文档库轮询 —— 遍历启用老师的已上传文档，逐篇 LLM 提取题目；
  3. AI 联网搜索 —— AUTOCOLLECT_SEARCH_QUERIES（逗号/换行分隔），搜索→抓取→提取。

资源门槛：Linux 下用 /proc 采样 CPU / 内存使用率，二者均低于阈值才执行。
模型：默认复用「AI 模型管理」的默认模型；可用 AUTOCOLLECT_MODEL / _BASE_URL / _API_KEY
      指定免费模型通道。

所有配置默认关闭（AUTOCOLLECT_ENABLED=0），未显式开启不会产生任何 LLM 调用。
"""
import collections
import json
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .config import Config, get
from .autocollect_classify import (
    _SEO_INFO_WORDS,
    _is_seo_info_page,
    _ERROR_CATEGORIES,
    _classify_error,
    _OFF_DOMAIN_HARD,
    _OFF_DOMAIN_INCIDENTAL,
    _gongkao_cjk_ratio,
    _REAL_Q_ASK,
    _NEWS_POLICY_SPEECH,
    _LATIN_WORD,
    _SEO_FAQ_STRONG,
    _SEO_FAQ_WEAK,
    _ESSAY_ASK_CMDS,
    _gongkao_real_question_shape,
    _is_gongkao_relevant,
    _QUESTION_STRONG_SIGNALS,
    _looks_like_questions,
    _detail_candidate,
    _PROSE_NO_ASK,
    _looks_like_prose,
    _worth_extract,
)
from .autocollect_text import (
    _UA,
    _html_to_text,
    _TABLE_CELL,
    _clean_cell,
    _tables_md,
    _formula_md,
    _html_to_text_wimg,
    _page_source,
    _page_title,
    _decode_resp_text,
    _extract_site_links,
    _text_fp,
)
from .autocollect_autonomous import (
    _COURSE_CATEGORIES,
    _collect_tree_leaves,
    _knowledge_leaf_names,
    _autonomous_query_pool,
    _autonomous_plan,
    _knowledge_node_index,
    _associate_category_id,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================
# 实时任务日志（#40 需求4：模型做的每个采集任务都进滑动日志，供管理端轮询展示）
# 进程内存环形缓冲，最长保留 300 条，线程安全。
# ============================================================
_task_log_lock = threading.Lock()
_task_logs: collections.deque = collections.deque(maxlen=300)


def _log_task(model: str, base_url: str, action: str, ok: bool = True,
              detail: str = "") -> None:
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    except Exception:  # noqa: BLE001
        ts = ""
    rec = {
        "ts": ts,
        "model": (model or "").strip() or "默认模型",
        "base_url": ((base_url or "").strip().rstrip("/")) or "",
        "action": action,
        "ok": bool(ok),
        "detail": detail or "",
    }
    with _task_log_lock:
        _task_logs.append(rec)


def recent_task_logs(limit: int = 80) -> list[dict]:
    """取最近 limit 条任务日志（倒序：最新在前）。"""
    with _task_log_lock:
        items = list(_task_logs)
    return list(reversed(items[-int(limit):]))


# ============================================================
# 资源采样（无 psutil，标准库实现；Linux 精确采样，其余降级）
# ============================================================

def _linux_cpu_percent(sample_sec: float = 0.3) -> int | None:
    """读取 /proc/stat 两次求 CPU 使用率（含 iowait 计为忙）。"""
    try:
        def _read():
            with open("/proc/stat", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("cpu "):
                        return [int(x) for x in line.split()[1:]]
            return None

        a = _read()
        if not a:
            return None
        time.sleep(sample_sec)
        b = _read()
        if not b:
            return None
        total_a = sum(a)
        total_b = sum(b)
        idle_a = a[3] + (a[4] if len(a) > 4 else 0)
        idle_b = b[3] + (b[4] if len(b) > 4 else 0)
        dt = total_b - total_a
        di = idle_b - idle_a
        if dt <= 0:
            return 0
        return round((dt - di) * 100.0 / dt)
    except Exception:  # noqa: BLE001  采样失败按未知处理，不阻断主流程
        return None


def _linux_mem_percent() -> int | None:
    """读取 /proc/meminfo 求内存使用率（优先 MemAvailable）。"""
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    info[parts[0].strip()] = int(parts[1].strip().split()[0])
        total = info.get("MemTotal", 0)
        if not total:
            return None
        avail = info.get("MemAvailable")
        if avail is None:
            avail = (info.get("MemFree", 0) + info.get("Buffers", 0)
                     + info.get("Cached", 0))
        used = max(0, total - avail)
        return round(used * 100.0 / total)
    except Exception:  # noqa: BLE001
        return None


def _win_mem_percent() -> int | None:
    """Windows 内存使用率（ctypes GlobalMemoryStatusEx）。"""
    try:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return None
        return int(m.dwMemoryLoad)
    except Exception:  # noqa: BLE001
        return None


def _sample_resources() -> dict:
    if sys.platform.startswith("linux"):
        return {"cpu_percent": _linux_cpu_percent(),
                "mem_percent": _linux_mem_percent()}
    return {"cpu_percent": None, "mem_percent": _win_mem_percent()}


def resource_ok(max_cpu: int = 80, max_mem: int = 80) -> tuple[bool, dict]:
    """返回 (是否放行, 采样详情)。CPU 与内存均 < 阈值才是 True。"""
    r = _sample_resources()
    cpu, mem = r["cpu_percent"], r["mem_percent"]
    if cpu is None or mem is None:
        return True, {**r, "ok": True,
                      "reason": "资源采样不可用（非 Linux 或 /proc 缺失），放行执行"}
    ok = cpu < max_cpu and mem < max_mem
    reason = "" if ok else (f"CPU {cpu}%/内存 {mem}% 达到或超过阈值 "
                            f"{max_cpu}%/{max_mem}%，本轮跳过")
    return ok, {**r, "ok": ok, "reason": reason}


# ============================================================
# 配置读取（优先级：runtime 配置 data/autocollect_config.json > .env / 默认）
# ============================================================

_CFG_PATH = Config.data_dir / "autocollect_config.json"
_HIST_PATH = Config.data_dir / "autocollect_history.jsonl"


def _load_runtime() -> dict:
    """读取管理端 runtime 配置；文件缺失/损坏返回空 dict（回落 .env 默认）。"""
    try:
        d = json.loads(_CFG_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_runtime(d: dict) -> None:
    try:
        _CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CFG_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"写入自动采集配置失败：{e}") from e


def _bool_cfg(rt: dict, key: str, env: str) -> bool:
    v = rt.get(key)
    if isinstance(v, bool):
        return v
    return get(env, "0").strip().lower() in ("1", "true", "yes", "on")


def _int_cfg(rt: dict, key: str, env: str, default: int,
             low: int | None = None) -> int:
    v = rt.get(key)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        try:
            iv = int(v)
            return iv if low is None else max(low, iv)
        except Exception:  # noqa: BLE001
            pass
    try:
        iv = int(get(env, str(default)) or default)
    except ValueError:
        iv = default
    return iv if low is None else max(low, iv)


def _float_cfg(rt: dict, key: str, env: str, default: float) -> float:
    v = rt.get(key)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    try:
        return float(get(env, str(default)) or default)
    except (TypeError, ValueError):
        return float(default)


def _list_cfg(rt: dict, key: str, env: str) -> list[str]:
    v = rt.get(key)
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return _comma_list(env)


def _enabled() -> bool:
    return _bool_cfg(_load_runtime(), "enabled", "AUTOCOLLECT_ENABLED")


def _interval_secs() -> int:
    return _int_cfg(_load_runtime(), "interval_secs", "AUTOCOLLECT_INTERVAL",
                    3600, low=60)


# ---- 采集调度增强：时间窗口 + 自动补采失败来源 ----

def _schedule_window() -> tuple[bool, int, int]:
    """(是否启用时间窗口, 起始小时, 结束小时)。结束小时 < 起始小时表示跨夜窗口。"""
    rt = _load_runtime()
    return (_bool_cfg(rt, "schedule_enabled", "AUTOCOLLECT_SCHEDULE_ENABLED"),
            _int_cfg(rt, "schedule_start", "AUTOCOLLECT_SCHEDULE_START", 2, low=0),
            _int_cfg(rt, "schedule_end", "AUTOCOLLECT_SCHEDULE_END", 6, low=0))


def _in_schedule_window() -> bool:
    """是否处于采集时间窗口内（守护轮询在窗口外跳过，实现低峰自动采集）。"""
    on, start, end = _schedule_window()
    if not on:
        return True
    h = time.localtime().tm_hour
    if start <= end:
        return start <= h <= end
    return h >= start or h <= end   # 跨夜窗口（如 22:00 → 06:00）


def _retry_failed_auto() -> bool:
    """启用后，每轮常规采集完成后自动对上一轮失败来源做一次补采。"""
    return _bool_cfg(_load_runtime(), "retry_failed_auto",
                     "AUTOCOLLECT_RETRY_AUTO")


# ---- 采集质量增强 1：来源优先级（管理端可配 high/medium/low） + 指数重试退避 ----

_PRIO_SCORE = {"high": 0, "medium": 1, "low": 2}   # 越小越优先


def _priorities() -> dict[str, str]:
    """来源优先级表 {url: "high"|"medium"|"low"}；未知/非法回落 medium。"""
    rt = _load_runtime()
    v = rt.get("priorities")
    if not isinstance(v, dict):
        return {}
    out = {}
    for k, val in v.items():
        norm = str(val or "").strip().lower()
        if norm not in _PRIO_SCORE:
            norm = "medium"
        out[str(k).strip()] = norm
    return out


def _priority_score(url: str) -> int:
    return _PRIO_SCORE.get(_priorities().get(url, "medium"), 1)


def _normalize_priorities(v) -> dict[str, str]:
    """归一化优先级表：仅保留 {url: high|medium|low}，未知值回落 medium。"""
    if not isinstance(v, dict):
        return {}
    out = {}
    for k, val in v.items():
        norm = str(val or "").strip().lower()
        if norm not in _PRIO_SCORE:
            norm = "medium"
        if str(k).strip():
            out[str(k).strip()] = norm
    return out


def _ordered_url_targets(urls: list[str]) -> list[str]:
    """按优先级（high→medium→low）稳定排序；相同优先级保持原有顺序。"""
    return sorted(urls, key=lambda u: _priority_score(u))


# 失败来源指数退避：基础 1 小时 * 2^(失败次数-1)，上限 24 小时
_BACKOFF_BASE = 3600          # 1 小时
_BACKOFF_CAP = 24 * 3600      # 24 小时
_BACKOFF_MAX_FAILS = 8        # 失败次数计入退避的上限（防溢出）


def _backoff_window(fails: int) -> float:
    """指数退避窗口（秒）：fails=1 → 1h, 2 → 2h, 3 → 4h … 上限 24h。"""
    n = max(1, min(int(fails), _BACKOFF_MAX_FAILS))
    return min(_BACKOFF_BASE * (2 ** (n - 1)), _BACKOFF_CAP)


def _fail_meta(state: dict):
    """失败来源元数据 {source_key: {"fails":int, "ts":float, "msg":str}}。"""
    m = state.setdefault("fail_meta", {})
    return m


def _mark_source_fail(state: dict, key: str, msg: str = "") -> dict:
    """记录一次来源失败：失败次数 +1、记时间戳（供指数退避与告警）。"""
    m = _fail_meta(state)
    cur = m.get(key) or {"fails": 0, "ts": 0.0, "msg": ""}
    cur["fails"] = int(cur.get("fails", 0)) + 1
    cur["ts"] = time.time()
    if msg:
        cur["msg"] = str(msg)[:200]
    m[key] = cur
    return cur


def _mark_source_ok(state: dict, key: str) -> None:
    """来源处理成功：清除其失败计数（退避复位）。"""
    m = _fail_meta(state)
    if key in m:
        m.pop(key, None)


def _source_in_backoff(state: dict, key: str, now: float | None = None) -> bool:
    """是否处于指数退避窗口内（自动轮次用于跳过，避免反复踩坑）。"""
    m = _fail_meta(state)
    cur = m.get(key)
    if not cur:
        return False
    fails = int(cur.get("fails", 0))
    return (now if now is not None else time.time()) - float(cur.get("ts", 0)) < _backoff_window(fails)


def _source_fail_count(state: dict, key: str) -> int:
    return int((_fail_meta(state).get(key) or {}).get("fails", 0))


def _should_skip_backoff(state: dict, key: str, force: bool = False) -> bool:
    """本轮是否应因退避窗口而跳过该来源（退避策略的唯一判定点）。

    策略：自动轮次（含自动补采 retry_failed）一律尊重退避；仅手动 force 轮绕过。
    历史 bug：自动补采曾绕过退避，导致每轮固定重试永久失败源（如 IP 级 403 死站），
    持续污染错误统计与连续失败告警。"""
    if force:
        return False
    return _source_in_backoff(state, key)


def _prune_stale_sources(state: dict) -> int:
    """清理指向「已不再配置的来源」的残留记录（fail_meta 退避计数 + failed_urls 补采队列）。

    只处理 URL 形态的键：其数值只在重试同一 URL 时才有意义。来源从 config.urls
    移除后，退避计数永不复位，而 failed_urls 里的死链还会被每轮补采反复重试
    （补采目标就是 failed_urls），持续产生固定错误（历史遗留：
    https://aipta.com/、https://gkzhenti.cn/ 与当前配置的 https://www.aipta.com/ 不同）。
    非 URL 键（q:搜索词、tid|文档名）与套卷详情页不在清理范围，避免误删在用来源。
    返回清理条数。"""
    live = set(_url_list()) | set(_whitelist_start_targets())

    def _stale(k) -> bool:
        return (isinstance(k, str) and k.startswith(("http://", "https://"))
                and k not in live)

    n = 0
    m = _fail_meta(state)
    for k in [k for k in m if _stale(k)]:
        m.pop(k, None)
        n += 1
    fu = state.get("failed_urls")
    if isinstance(fu, set):
        for k in [k for k in fu if _stale(k)]:
            fu.discard(k)
            n += 1
    return n


# ---- 采集质量增强 2：采集质量 AI 自评抽检（低成本抽样，来源含健康分） ----

def _quality_sample_rate() -> float:
    """AI 自评抽样比例（0~1）；0 = 关闭抽检。"""
    return _float_cfg(_load_runtime(), "quality_sample_rate",
                      "AUTOCOLLECT_QUALITY_SAMPLE", 0.10)


def _quality_rules() -> str:
    return str(_load_runtime().get("quality_rules") or "").strip() or \
        "必须是真正的公考成题（有明确题干+设问+确定答案）；" \
        "禁止把百科/新闻/政府文件/公告/备考素材改写成题目、禁止编造答案；" \
        "题干完整、选项与题干对应、答案/解析清晰、不答非所问"


# ---- 采集质量增强 3：结果摘要 / 失败告警 Webhook 通知 ----

def _notify_config() -> dict:
    """通知配置：webhook_url、通知事件（summary/fail_alert）、连续失败阈值。"""
    rt = _load_runtime()
    on = rt.get("notify_on") or []
    if not isinstance(on, list):
        on = [x.strip() for x in str(on).replace(",", "\n").splitlines() if x.strip()]
    return {
        "webhook_url": str(rt.get("webhook_url") or "").strip(),
        "notify_on": [x for x in on if x in ("summary", "fail_alert")],
        "fail_alert_threshold": _int_cfg(rt, "fail_alert_threshold",
                                         "AUTOCOLLECT_NOTIFY_FAIL_THRESHOLD", 3, low=1),
    }


# ---- 采集质量增强 2 实现：AI 自评抽检 ----
_QUALITY_MAX_Q = 4


def _parse_qc(raw: str) -> tuple[int, bool, list]:
    """解析质检 JSON；失败回落默认（score=100, 无异常），不阻断主链路。"""
    try:
        m = re.search(r"\{.*\}", raw or "", re.S)
        data = json.loads(m.group(0)) if m else {}
    except Exception:  # noqa: BLE001
        data = {}
    try:
        score = max(0, min(100, int(data.get("score", 100))))
    except (TypeError, ValueError):
        score = 100
    has_error = bool(data.get("has_error", False))
    issues = data.get("issues") if isinstance(data.get("issues"), list) else []
    return score, has_error, [str(x)[:100] for x in issues][:5]


def _quality_check(state: dict, source_key: str, questions: list[dict]) -> None:
    """AI 自评抽检：低概率抽样 ≤4 题交给 LLM 评分，记录来源健康分。

    评分低/含错误的来源会在 failed_sources/status 中体现，便于管理端定位劣质源。
    复用采集专用通道（或平台默认模型）做小成本评估；任何异常静默忽略，绝不阻断入库。
    """
    try:
        rate = _quality_sample_rate()
        if rate <= 0 or not questions:
            return
        if random.random() > rate:
            return
        sample = questions[:_QUALITY_MAX_Q]
        lines = []
        for q in sample:
            qt = (q.get("question") or "").strip()[:120]
            ans = (q.get("answer") or "").strip()[:40]
            lines.append(f"- 题干：{qt} | 答案：{ans}")
        text = "\n".join(lines)
        if len(text) < 20:
            return
        from . import llm
        from .config import Config as _Cfg
        tcfg = _Cfg().get_teacher(_Cfg.teacher_id)
        channels = _extraction_channels()
        c = channels[0] if channels else {}
        rules = _quality_rules()
        prompt = (
            f"你是题库质检员。以下是自动采集抽样的待入库题目，请按标准评估质量。\n"
            f"标准：{rules}\n\n{text}\n\n"
            f"仅返回一行 JSON，不得输出其他内容："
            f'{{"score":0-100整数,"has_error":true或false,'
            f'"issues":["简短问题描述"]}}'
        )
        _add_llm_usage(len(prompt))
        raw = llm.call_llm(
            [{"role": "system", "content": "你是严格的题库质检员，只输出JSON。"},
             {"role": "user", "content": prompt}],
            tcfg, max_tokens=300, temperature=0,
            model=c.get("model"), base_url=c.get("base_url"), api_key=c.get("api_key"))
        score, has_error, issues = _parse_qc(raw)
        qs_meta = state.setdefault("quality", {})
        bucket = qs_meta.setdefault(str(source_key)[:120], [])
        bucket.append({"score": score, "has_error": bool(has_error),
                       "issues": issues, "checked": len(sample), "ts": _snapshot_iso()})
        if len(bucket) > 50:
            del bucket[:-50]
        _log_task((c.get("model") if c else ""), (c.get("base_url") if c else ""),
                  "AI自评抽检", ok=True,
                  detail=f"评分 {score}/100，抽查 {len(sample)} 题"
                         f"{'，存在异常' if has_error else '，质量正常'}")
    except Exception:  # noqa: BLE001  抽检失败不影响主链路
        pass


# ---- 采集质量增强 3 实现：结果摘要 / 失败告警 Webhook 通知 ----

def _post_json(url: str, payload: dict, timeout: float = 8.0) -> bool:
    """发送 JSON 到 webhook；超时/网络异常静默返回 False，绝不阻断采集。"""
    try:
        import urllib.request
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            resp.read()
        return True
    except Exception:  # noqa: BLE001
        return False


def _recent_low_quality(state: dict) -> list[dict]:
    """最近抽检中评分 <60 或标记异常的来源（供告警附上劣质源）。"""
    out = []
    try:
        for k, bucket in (state.get("quality") or {}).items():
            recs = bucket if isinstance(bucket, list) else []
            recent = [r for r in recs
                      if r.get("has_error") or int(r.get("score", 100)) < 60]
            if recent:
                out.append({"source": k, "latest": recent[-1]})
    except Exception:  # noqa: BLE001
        pass
    return out[:10]


def _notify_result(state: dict, result: dict) -> None:
    """按通知配置外发结果摘要 / 连续失败告警；并维护连续失败计数到 state。

    连续失败计数与 webhook 是否配置无关：未配置 webhook 时也要照常写入 state
    （经 status.failed_sources.consecutive_failures 暴露），否则「未配置 webhook
    则仅记录」退化为「什么都不记」，告警链路永远无法被验证。
    """
    try:
        nc = _notify_config()
        url = nc.get("webhook_url") or ""
        on = nc.get("notify_on") or []
        added = int((result or {}).get("added", 0))
        errs = len((result or {}).get("errors") or [])
        col = int(state.get("consecutive_failures", 0))
        col = col + 1 if (added == 0 and errs > 0) else 0
        state["consecutive_failures"] = col
        if not url:
            return
        low_q = _recent_low_quality(state)
        base = {
            "type": "autocollect.notify",
            "ts": _now_iso(),
            "added": added,
            "errors": errs,
            "error_stats": (result or {}).get("error_stats") or {},
            "consecutive_failures": col,
            "low_quality_sources": low_q,
        }
        if "summary" in on:
            _post_json(url, {**base, "event": "summary",
                             "detail": (result or {}).get("detail") or {}})
        threshold = int(nc.get("fail_alert_threshold", 3))
        if "fail_alert" in on and col >= threshold:
            _post_json(url, {**base, "event": "fail_alert",
                             "message": f"自动采集连续 {col} 轮入库失败，请检查站点/模型/通道配置"})
    except Exception:  # noqa: BLE001
        pass


def test_notify() -> dict:
    """向已配置 webhook 发送一条测试消息，供管理端验证连通性。"""
    nc = _notify_config()
    url = nc.get("webhook_url") or ""
    if not url:
        return {"ok": False, "error": "未配置 webhook_url"}
    ok = _post_json(url, {"type": "autocollect.notify", "event": "test",
                          "ts": _now_iso(), "message": "自动采集 Webhook 通知连接测试"})
    return {"ok": ok, "error": "" if ok else "请求失败（超时或网络异常）"}


# ---- LLM 周期字数配额（预算墙）：每轮累计下发 LLM 的输入字符数达到上限即停 ----

def _llm_budget_chars() -> int:
    """每轮 LLM 输入字符数配额；0 = 不限。"""
    return _int_cfg(_load_runtime(), "llm_budget_chars",
                    "AUTOCOLLECT_LLM_BUDGET_CHARS", 0, low=0)


def _token_price() -> float:
    """每百万 token 估算价格（元），用于成本展示。"""
    return _float_cfg(_load_runtime(), "llm_price_per_1m",
                      "AUTOCOLLECT_LLM_PRICE", 2.0)


def _llm_budget_exceeded() -> bool:
    b = _llm_budget_chars()
    return b > 0 and _llm_usage_get("chars") >= b


# ============================================================
# 轮次级墙钟预算（问题2/3 收口）：_LLM_CALL_TIMEOUT_S 只约束「单次 LLM 调用」
# 不挂死，约束不了「一轮内正常调用累积过多」。实测某轮跑满 6.5 小时仍未结束，
# 期间 running 恒为 True，把 interval_secs=3600 的每小时调度全部 skip，
# 且 state/history 只在轮末落盘 → 整轮不可观测。
# 这里给整轮加一道墙钟上限：到点即正常收尾（落盘 state + 出 history +
# 释放 running），已入库题目全部保留，下一轮续采未处理来源。
# ============================================================
_ROUND_BUDGET_DEFAULT_S = 2700      # 默认 45 分钟；0 = 不限
_round_started_at: float = 0.0      # 本轮 monotonic 起点；<=0 表示无进行中轮次


def _round_budget_secs() -> int:
    """每轮墙钟上限（秒）；0 = 不限。"""
    return _int_cfg(_load_runtime(), "round_budget_secs",
                    "AUTOCOLLECT_ROUND_BUDGET_SECS", _ROUND_BUDGET_DEFAULT_S, low=0)


def _round_expired() -> bool:
    """本轮是否已达墙钟预算（未开始计时时恒为 False，便于单测直接调用）。"""
    b = _round_budget_secs()
    if b <= 0 or _round_started_at <= 0:
        return False
    return (time.monotonic() - _round_started_at) >= b


def _round_elapsed() -> float:
    """本轮已耗时（秒）；无进行中轮次返回 0。"""
    if _round_started_at <= 0:
        return 0.0
    return time.monotonic() - _round_started_at


def _round_should_stop(added_total: int) -> bool:
    """轮次收尾判定：入库达安全网 / LLM 配额耗尽 / 墙钟预算到点。"""
    return (added_total >= _RUN_ADD_LIMIT or _llm_budget_exceeded()
            or _round_expired())


def _thresholds() -> tuple[int, int]:
    rt = _load_runtime()
    return (_int_cfg(rt, "cpu_max", "AUTOCOLLECT_CPU_MAX", 80, low=1),
            _int_cfg(rt, "mem_max", "AUTOCOLLECT_MEM_MAX", 80, low=1))


def _max_per_cycle() -> int:
    return _int_cfg(_load_runtime(), "max_per_cycle",
                    "AUTOCOLLECT_MAX_PER_CYCLE", 100, low=1)


# #58 整卷不掐断：max_per_cycle 只作「每轮最低目标」，不再在单轮内掐断一套试卷的后半部分。
# 此处为纯安全网（默认足够容纳整套试卷），避免极端来源导致单轮无限灌库；
# 真正的自然上限来自 _crawl_site 单站页数预算 + extract_questions_ai 的 MAX_BATCHES。
_RUN_ADD_LIMIT = 2000


def _comma_list(name: str) -> list[str]:
    raw = get(name, "")
    return [x.strip() for x in raw.replace("\n", ",").split(",") if x.strip()]


def _teacher_ids() -> list[str]:
    return _list_cfg(_load_runtime(), "teachers", "AUTOCOLLECT_TEACHERS")


def _url_list() -> list[str]:
    return _list_cfg(_load_runtime(), "urls", "AUTOCOLLECT_URLS")


def _search_queries() -> list[str]:
    return _list_cfg(_load_runtime(), "search_queries",
                     "AUTOCOLLECT_SEARCH_QUERIES")


def _whitelist_list() -> list[str]:
    """站点白名单域名：信任直采站。搜索命中该域不阻断、直接进详情解析；
    每轮起点还自动追加 `https://{host}/` 做整站深采，收敛全网搜索噪音。"""
    return _list_cfg(_load_runtime(), "site_whitelist",
                     "AUTOCOLLECT_SITE_WHITELIST")


def _whitelist_hosts() -> set[str]:
    return {h.strip().lstrip("*.").lower() for h in _whitelist_list() if h.strip()}


def _whitelist_start_targets() -> list[str]:
    """白名单站每轮的起始采集入口：URL 形式追加进来源1，走整站 BFS 深采详情页。"""
    return [f"https://{h}/" for h in sorted(_whitelist_hosts()) if h]


def _autonomous() -> bool:
    """自主采集开关（零配置找题）：显式开启后，即使不配置 URL/搜索词也能自动找题。"""
    return _bool_cfg(_load_runtime(), "autonomous", "AUTOCOLLECT_AUTONOMOUS")


def _autonomous_queries_cap() -> int:
    """自主采集每轮自动生成搜索词数量上限（游标轮转探索词池）。"""
    return _int_cfg(_load_runtime(), "autonomous_queries",
                    "AUTOCOLLECT_AUTONOMOUS_QUERIES", 12, low=1)


def _effective_autonomous() -> bool:
    """自主采集是否实际生效：显式 autonomous=true，或启用后未配置任何 URL/搜索词
    （零配置兜底——启用即开始自动找题，无需额外配置来源）。"""
    if _autonomous():
        return True
    return not _url_list() and not _search_queries()


def _fallback_models() -> list[str]:
    return _list_cfg(_load_runtime(), "fallback_models",
                     "AUTOCOLLECT_FALLBACK_MODELS")


def _site_pages_map() -> dict[str, int]:
    """逐站页数额度表：{url或域名: 页数}。键做部分包含匹配（域名级优先于全局）。"""
    rt = _load_runtime()
    v = rt.get("site_pages_map")
    if not isinstance(v, dict):
        return {}
    out = {}
    for k, val in v.items():
        kk = str(k).strip()
        if not kk:
            continue
        try:
            out[kk] = max(1, int(val))
        except (TypeError, ValueError):
            continue
    return out


def _per_site_pages(url: str) -> int:
    """来源整站爬取页数额度：优先命中 site_pages_map（逐站差异化），
    否则回退全局 max_site_pages；最后叠加质量健康惩罚（saC 折算）。"""
    from urllib.parse import urlparse
    base = _max_site_pages()
    host = (urlparse(url).netloc or "").lower()
    for k, v in _site_pages_map().items():
        kk = k.lstrip("*.").lower()
        if kk and (kk in host or kk in url):
            base = v
            break
    return _health_pages(url, base)


def _max_site_pages() -> int:
    """新增采集源整站爬取页数上限（防止失控；默认 30 页）。"""
    return _int_cfg(_load_runtime(), "max_site_pages",
                    "AUTOCOLLECT_MAX_SITE_PAGES", 30, low=1)


def _health_pages(url: str, base: int) -> int:
    """质量健康闭环（saC）：按来源最近 AI 自评健康分折算整站爬取额度。
    评分 <60 减半、<40 降至 1/3，抑制劣质源灌库；无评分或高分维持 base。"""
    try:
        st = _load_state()
        for key, bucket in (st.get("quality") or {}).items():
            if key not in url and url not in key:
                continue
            recs = bucket if isinstance(bucket, list) else []
            recent = [r for r in recs if r.get("score") is not None]
            if not recent:
                continue
            score = int(recent[-1].get("score", 100))
            if score < 40:
                return max(5, base // 3)
            if score < 60:
                return max(8, base // 2)
    except Exception:  # noqa: BLE001
        pass
    return base


_LLM_PATH = Config.data_dir / "autocollect_llm.json"


def _mask_api_key(key: str) -> str:
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 8:
        return "****"
    return k[:3] + "****" + k[-4:]


def _norm_channel(model: str, base_url: str, api_key: str) -> dict:
    """规范化单个通道：去空白、去尾部 /models 与斜杠，返回 {model, base_url, api_key}。"""
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/models"):
        base = base[: -len("/models")].rstrip("/")
    return {"model": (model or "").strip(), "base_url": base,
            "api_key": (api_key or "").strip()}


def load_llm_channels() -> list[dict]:
    """多平台专用通道列表（AI 模型管理页可配置，多模型共存）。

    持久化 data/autocollect_llm.json 为 {"channels": [{model, base_url, api_key}, ...]}；
    兼容旧单通道存储（{model, base_url, api_key} → 单元素列表）。
    """
    try:
        d = json.loads(_LLM_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if isinstance(d, dict) and isinstance(d.get("channels"), list):
        out = []
        for x in d["channels"]:
            if isinstance(x, dict):
                c = _norm_channel(x.get("model"), x.get("base_url"), x.get("api_key"))
                if c["base_url"] and c["model"]:
                    out.append(c)
        return out
    if isinstance(d, dict) and d.get("base_url"):
        c = _norm_channel(d.get("model"), d.get("base_url"), d.get("api_key"))
        return [c] if c["base_url"] and c["model"] else []
    return []


def save_llm_channels(channels: list) -> list[dict]:
    """持久化多平台通道列表（自动规范化并丢弃空项）。"""
    clean = [_norm_channel(c.get("model"), c.get("base_url"), c.get("api_key"))
             for c in channels if isinstance(c, dict)]
    clean = [c for c in clean if c["base_url"] and c["model"]]
    try:
        _LLM_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LLM_PATH.write_text(
            json.dumps({"channels": clean}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"写入自动采集专用通道失败：{e}") from e
    return clean


def _resolve_registry_key(base_url: str, model: str = "") -> str:
    """选择注册表已有平台时，按 base_url（或 model）自动解析该平台保存的 API Key，
    使「选择已有平台→拉取模型→保存」无需重新手填密钥。找不到返回空串。"""
    try:
        from .models import get_ai_model_store
        st = get_ai_model_store()
        base = (base_url or "").strip().rstrip("/")
        if model:
            m = st.get_model(model)
            if m and (m.get("base_url") or "").strip().rstrip("/") == base:
                k = (m.get("api_key") or "").strip()
                if k:
                    return k
        reg = st.find_by_base_url(base)
        k = (reg.get("api_key") or "").strip() if reg else ""
        return k
    except Exception:  # noqa: BLE001  注册表不可用不阻断
        return ""


def add_llm_channel(model: str, base_url: str, api_key: str = "") -> list[dict]:
    """追加一个专用通道；api_key 空则解析注册表同名平台密钥，再回落全局密钥。
    返回保存后的通道列表。"""
    c = _norm_channel(model, base_url, api_key)
    if not c["base_url"]:
        raise ValueError("Base URL 不能为空")
    if not c["model"]:
        raise ValueError("模型不能为空")
    if not c["api_key"]:
        c["api_key"] = _resolve_registry_key(c["base_url"], c["model"])
    chs = load_llm_channels()
    if any(x["base_url"] == c["base_url"] and x["model"] == c["model"] for x in chs):
        raise ValueError(f"通道已存在：{c['model']} @ {c['base_url']}")
    chs.append(c)
    return save_llm_channels(chs)


def update_llm_channel(idx: int, model: str = "", base_url: str = "",
                       api_key: str = "") -> list[dict]:
    """更新第 idx 个专用通道；字段留空保持原值（api_key 留空保持原密钥）。
    返回保存后的通道列表。"""
    chs = load_llm_channels()
    if not (0 <= int(idx) < len(chs)):
        raise ValueError("通道索引越界")
    cur = chs[int(idx)]
    m = (model or "").strip() or cur["model"]
    b = _norm_channel(m, "", "").get("base_url", "")
    base = _norm_channel(m, base_url or cur["base_url"], "").get("base_url", "")
    if not base:
        raise ValueError("Base URL 不能为空")
    key = (api_key or "").strip() or cur["api_key"]
    if not key:
        key = _resolve_registry_key(base, m)
    chs[int(idx)] = {"model": m, "base_url": base, "api_key": key}
    return save_llm_channels(chs)


def delete_llm_channel(idx: int) -> list[dict]:
    """删除第 idx 个专用通道。返回保存后的通道列表。"""
    chs = load_llm_channels()
    if not (0 <= int(idx) < len(chs)):
        raise ValueError("通道索引越界")
    chs.pop(int(idx))
    return save_llm_channels(chs)


def load_llm_channel() -> dict:
    """取首通道（向后兼容旧调用方：模型设置/拉取远端模型等）。"""
    chs = load_llm_channels()
    return chs[0] if chs else {}


def save_llm_channel(model: str, base_url: str, api_key: str = "") -> dict:
    """覆盖/新增首通道（向后兼容旧 POST /llm 契约：api_key 留空则保留旧值）。"""
    c = _norm_channel(model, base_url, api_key)
    if not c["base_url"]:
        raise ValueError("Base URL 不能为空")
    if not c["model"]:
        raise ValueError("模型不能为空")
    chs = load_llm_channels()
    if chs:
        c["api_key"] = (api_key or "").strip() or chs[0].get("api_key", "")
        chs[0] = c
    else:
        chs = [c]
    save_llm_channels(chs)
    return {"model": c["model"], "base_url": c["base_url"],
            "api_key_masked": _mask_api_key(c["api_key"])}


def clear_llm_channels() -> None:
    """清除全部专用通道（回落 AUTOCOLLECT_* / 平台默认模型）。"""
    try:
        _LLM_PATH.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def clear_llm_channel() -> None:
    """向后兼容：清除全部专用通道。"""
    clear_llm_channels()


def llm_channel_info() -> dict:
    """首通道只读视图（API_KEY 脱敏）。"""
    chs = load_llm_channels()
    if not chs:
        return {"configured": False, "base_url": "", "model": "", "api_key_masked": ""}
    c = chs[0]
    return {"configured": True, "base_url": c["base_url"], "model": c["model"],
            "api_key_masked": _mask_api_key(c["api_key"])}


def llm_channels_info() -> dict:
    """管理端多通道只读视图（每条脱敏，供配置列表）。"""
    chs = load_llm_channels()
    return {
        "configured": bool(chs),
        "count": len(chs),
        "channels": [{"base_url": c["base_url"], "model": c["model"],
                      "api_key_masked": _mask_api_key(c["api_key"])} for c in chs],
    }


def _test_one_channel(c: dict, timeout: float = 30.0) -> dict:
    """对单条通道发一次最小 chat 请求（max_tokens=8），验证连通性与密钥有效性。

    api_key 为空的通道按采集时同样的解析链补齐（注册表同平台密钥 → 全局密钥），
    保证测活结果与真实采集行为一致；HTTP 错误把服务商响应体一并带出，便于
    当场发现 401（密钥失效）/429（限流）等问题。
    """
    import urllib.request as _uq
    key = (c.get("api_key") or "").strip()
    key_source = "channel"
    if not key:
        key = _resolve_registry_key(c["base_url"], c["model"])
        key_source = "registry" if key else ""
    if not key:
        key = get("API_KEY", "")
        key_source = "global" if key else ""
    url = (c["base_url"] or "").rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": c["model"],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "temperature": 0,
    }).encode("utf-8")
    head = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        head["Authorization"] = "Bearer " + key
    t0 = time.monotonic()
    base_view = {"model": c["model"], "base_url": c["base_url"],
                 "api_key_masked": _mask_api_key(key), "key_source": key_source or "none"}
    try:
        req = _uq.Request(url, data=body, headers=head, method="POST")
        with _uq.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ms = int((time.monotonic() - t0) * 1000)
        content = ""
        try:
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        except Exception:  # noqa: BLE001  响应结构差异不影响测活结论
            pass
        return {"ok": True, "latency_ms": ms, "error": "", "sample": content[:60], **base_view}
    except Exception as e:  # noqa: BLE001  测活的目的就是把真实错误亮出来
        ms = int((time.monotonic() - t0) * 1000)
        detail = ""
        try:  # HTTPError 响应体常带服务商的 json 错误说明（如 insufficient quota）
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        err = f"{type(e).__name__}: {e}"
        if detail:
            err += f" | {detail}"
        return {"ok": False, "latency_ms": ms, "error": err[:300], "sample": "", **base_view}


def test_llm_channel(idx: int, timeout: float = 30.0) -> dict:
    """一键测活第 idx 条通道。通道越界抛 ValueError。"""
    chs = load_llm_channels()
    if not (0 <= int(idx) < len(chs)):
        raise ValueError("通道索引越界")
    return _test_one_channel(chs[int(idx)], timeout=timeout)


def test_llm_channels_all(timeout: float = 30.0) -> dict:
    """一键测活全部通道，返回逐条结果与汇总（ok_count / total）。"""
    chs = load_llm_channels()
    results = [_test_one_channel(c, timeout=timeout) for c in chs]
    return {"ok": bool(results) and all(r["ok"] for r in results),
            "total": len(results),
            "ok_count": sum(1 for r in results if r["ok"]),
            "results": results}


def _model_overrides() -> dict:
    """LLM 通道优先级：自动采集专用通道(首条) > AUTOCOLLECT_* 环境变量 > 平台默认模型。"""
    ch = load_llm_channel()
    if ch.get("base_url") and ch.get("model"):
        return {
            "model": ch.get("model") or None,
            "base_url": ch.get("base_url") or None,
            "api_key": ch.get("api_key") or None,
        }
    return {
        "model": get("AUTOCOLLECT_MODEL", "") or None,
        "base_url": get("AUTOCOLLECT_BASE_URL", "") or None,
        "api_key": get("AUTOCOLLECT_API_KEY", "") or None,
    }


# ============================================================
# 状态 / 持久化（避免重复处理同一来源，控制 LLM 成本）
# ============================================================

_STATE_PATH = Config.data_dir / "autocollect_state.json"

_DOC_FAIL_RETRY = 6 * 3600  # 文档提取失败后的重试窗口（6 小时）

_lock = threading.Lock()
_status = {
    "enabled": False,
    "running": False,
    "last_run": None,
    "last_result": None,
}


def _load_state() -> dict:
    try:
        state = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        # #12：failed_* 集合落盘为 list，读回转 set（运行时用 set 去重）
        for k in ("failed_urls", "failed_queries"):
            if k in state and isinstance(state[k], list):
                state[k] = set(state[k])
        # 套卷标记升级迁移：旧版只有页面指纹 url_fp，首次加载时把存量指纹页
        # 标为 complete（保持旧「内容未变不重采」语义，避免升级后手动 force 轮
        # 把历史页全量重提）；新采页面从本轮起按真实完整性写标记。
        if "collected_sets" not in state and isinstance(state.get("url_fp"), dict):
            seeded = {u: {"fp": fp, "status": "complete", "added": 0, "n": 0,
                          "ts": "2026-09-12T00:00:00Z", "migrated": True}
                      for u, fp in state["url_fp"].items()}
            state["collected_sets"] = seeded
            _save_state(state)
        # 试卷作业升级迁移（关键）：采集范式从「逐页各自入库」改为「整卷作业串行」
        # 后，新代码只认 paper_jobs。若不迁移，升级后第一轮会把历史上 151 张已采
        # 完整的卷全当新卷重采（一次升级烧光整轮 LLM 预算）。
        #   complete/gated → done（带 migrated 标记；指纹为空时首次遇到内容再补基线）
        #   blocked        → blocked（自动轮继续跳过）
        #   failed/incomplete → 不建作业，作为新卷重排（未采完的本就该续采）
        if "paper_jobs" not in state and isinstance(state.get("collected_sets"), dict):
            seeded_jobs = {}
            for _k, _v in state["collected_sets"].items():
                if not isinstance(_v, dict):
                    continue
                _st = str(_v.get("status") or "")
                if _st not in ("complete", "gated", "blocked"):
                    continue
                seeded_jobs[str(_k)] = {
                    "status": "blocked" if _st == "blocked" else "done",
                    "cursor": 0, "total": 0, "buf": [],
                    "added": int(_v.get("added") or 0), "stall": 0,
                    "fp": str(_v.get("fp") or ""),
                    "ts": _v.get("ts") or _now_iso(),
                    "ts_epoch": float(_v.get("ts_epoch") or 0),
                    "migrated": True,
                }
            state["paper_jobs"] = seeded_jobs
            _save_state(state)
        return state
    except Exception:  # noqa: BLE001
        return {"docs": [], "urls": [], "search_urls": []}


def _save_state(state: dict) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # #12：状态里不允许 set（JSON 不可序列化），统一 list 落盘
        out = {}
        for k, v in state.items():
            out[k] = sorted(v) if isinstance(v, set) else v
        _STATE_PATH.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _state_add(state: dict, key: str, value: str, cap: int = 5000) -> None:
    seq = state.setdefault(key, [])
    if value not in seq:
        seq.append(value)
    if len(seq) > cap:
        del seq[: len(seq) - cap]


def _state_add_set(state: dict, key: str, value: str, cap: int = 5000) -> None:
    """#12 失败来源集合（去重追加 + 容量上限）；运行时为 set，落盘前转 list。"""
    seq = state.setdefault(key, set())
    seq.add(value)
    if len(seq) > cap:
        # 超出容量移除最早项（set 无序，保守截断，容量上限仅防失控）
        seq = set(list(seq)[-cap:])
        state[key] = seq


def _state_discard_set(state: dict, key: str, value: str) -> None:
    """#12 补采成功后从失败集合移除。"""
    seq = state.get(key)
    if isinstance(seq, set) and value in seq:
        seq.discard(value)


def _set_mark(collected: dict, page_url: str, fp: str, extract_result: dict,
              added: int) -> None:
    """记录一套题目（来源页链接）的采集状态标记。

    complete 判定：提取过程跑完全部片段（r["complete"]），与本题入库数无关——
    一套卷全部命中库内查重（added=0）也算采完，不应反复重提。
    incomplete = 被预算/熔断打断未跑完，下轮续采。added/n 仅供观测。"""
    try:
        complete = bool(extract_result.get("complete", True))
        prev = collected.get(page_url) or {}
        n_total = int(prev.get("added", 0)) + int(added)
        collected[page_url] = {
            "fp": fp,
            "status": "complete" if complete else "incomplete",
            "added": n_total,
            "n": len(extract_result.get("questions") or []),
            "ts": _now_iso(), "ts_epoch": time.time(),
        }
        _cap_collected(collected)
    except Exception:  # noqa: BLE001  标记失败不阻断采集主流程
        pass


def _set_fail(collected: dict, key: str, msg: str) -> None:
    """来源页提取失败（超时/解析等）落标记：status=failed + 时间戳。

    失败页在 _SET_FAIL_RETRY 窗口内直接跳过，避免同一坏页每轮重复耗尽
    5 通道 × 60s 的 LLM 预算（实测：搜索命中专题页反复全通道超时）。"""
    try:
        collected[key] = {"fp": "", "status": "failed", "added": 0, "n": 0,
                          "ts": _now_iso(), "ts_epoch": time.time(),
                          "msg": str(msg)[:160]}
        _cap_collected(collected)
    except Exception:  # noqa: BLE001
        pass


def _set_should_skip(collected: dict, key: str, fp: str, force: bool) -> str:
    """套卷跳过判定：返回 "" 表示应提取，否则返回跳过原因（计入观测）。

    complete/gated：永久跳过（含 force 手动轮，需先 reset 才重采）；
    blocked：自动轮跳过，但手动 force 轮放行重试（连续多轮无进展的试卷）；
    failed：重试窗口内跳过，窗口外放行续采；
    incomplete：不因标记跳过，下轮续采（直到完整）。"""
    mark = collected.get(key)
    if not mark:
        return ""
    st = mark.get("status")
    if st in ("complete", "gated"):
        # 内容变了才允许重新评估；未变则永久跳过（这就是需求1的核心）
        return st if (fp and mark.get("fp") == fp) else ""
    if st == "blocked":
        # 已封顶的难产卷：自动轮不再消耗预算，人工 force 轮仍可再试
        return "" if force else "blocked"
    if st == "failed":
        if "ts_epoch" not in mark:
            _set_epoch(mark)
        try:
            aged = time.time() - float(mark.get("ts_epoch", 0))
        except Exception:  # noqa: BLE001
            aged = _SET_FAIL_RETRY + 1
        return "failed" if aged < _SET_FAIL_RETRY else ""
    # incomplete / 其它：不跳过，交给提取流程续采
    return ""


def _cap_collected(collected: dict) -> None:
    """防失控：标记表超容量时丢弃最早条目（完整套卷被误丢只会重采一次，代价可接受）。"""
    if len(collected) > 20000:
        oldest = sorted(collected,
                        key=lambda x: (collected[x] or {}).get("ts_epoch", 0))[:2000]
        for k in oldest:
            collected.pop(k, None)


def _set_epoch(mark: dict) -> None:
    """给标记补 epoch 时间戳（供重试窗口计算，兼容旧数据）。"""
    try:
        import datetime as _dt
        ts = mark.get("ts") or ""
        mark["ts_epoch"] = _dt.datetime.fromisoformat(
            ts.replace("Z", "+00:00")).timestamp() if ts else time.time()
    except Exception:  # noqa: BLE001
        mark["ts_epoch"] = time.time()


_SET_FAIL_RETRY = 2 * 3600   # 提取失败套卷的重试窗口（2 小时）


def _set_status(collected: dict, page_url: str) -> str:
    try:
        return str((collected.get(page_url) or {}).get("status") or "")
    except Exception:  # noqa: BLE001
        return ""


# ============================================================
# 试卷作业：整卷为单位串行采集（一张采完再采下一张，整卷采完才入库）
# ============================================================

_PAPER_MAX_STALL = 3        # 连续多少轮零进展即标 blocked，避免一张坏卷永久堵死采集链
_PAPER_TEXT_MAX = 200000    # 单张试卷源文本上限（超出说明文档过大，拒绝而非静默截断）
_PAPER_BUF_MAX = 3000       # 单张卷待提交缓冲上限（防御：作业表在单个 state.json 里）
_PAPER_CKPT_EVERY = 20.0    # 整卷进度落盘节流（秒）
_paper_ckpt_at: float = 0.0


def _paper_checkpoint(state: dict) -> None:
    """把在途作业进度落盘（节流）。

    整卷可能横跨很久，而 state 原本只在轮末落盘：进程重启 / 轮次被中断后
    游标与缓冲全丢，已跑完的片段要重新烧一遍 LLM。这里做进度检查点，
    保证重启后从 cursor 续采（原子提交语义不变，未采完的卷不会半入库）。"""
    global _paper_ckpt_at
    now = time.monotonic()
    if now - _paper_ckpt_at < _PAPER_CKPT_EVERY:
        return
    _paper_ckpt_at = now
    _save_state(state)


def _paper_env_stop(round_expired) -> bool:
    """环境级停止（与试卷自身无关，不计入 stall）：
    轮次墙钟到点 / LLM 字符配额耗尽 / 模型集体熔断。

    历史坑：只判 round_expired，会把「配额用尽」当成「这张卷采不动」，
    连续三轮就把好卷标成 blocked。"""
    try:
        if callable(round_expired) and round_expired():
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        return bool(_model_down()) or bool(_llm_budget_exceeded())
    except Exception:  # noqa: BLE001
        return False


def _q_key(q: dict) -> str:
    """题目去重键（与 _extract_enhanced 内 _absorb 同口径）。"""
    qt = (q.get("question") or "").strip()
    return qt[:80] + "|" + "".join(q.get("options") or [])[:60] + "|" + qt[-60:]


def _paper_jobs(state: dict) -> dict:
    """试卷作业表：key → 作业记录。

    key 与 collected_sets 同口径（网址试卷 = 详情页 URL；文档试卷 = tid|文档名），
    两套标记可互相印证。作业字段：
      status  pending / active / done / blocked
      cursor  已完整跑完的片段数（下轮从这里续采，不再从头重跑）
      total   片段总数（首次切片后写入）
      buf     已提取、待整卷提交的题目（整卷采完才入库）
      added   提交入库累计条数
      stall   连续零进展轮数，达 _PAPER_MAX_STALL → blocked
      fp      源文本指纹；变了就作废重来
      text    在采网址试卷的正文（仅未完成作业持有，done/blocked 时清空防膨胀）
    """
    return state.setdefault("paper_jobs", {})


def _cap_paper_jobs(jobs: dict, cap: int = 2000) -> None:
    """作业表容量上限：只淘汰已完成/已阻塞的旧作业，未完成的绝不淘汰（否则丢进度）。"""
    if len(jobs) <= cap:
        return
    cand = sorted([(k, v) for k, v in jobs.items()
                   if str((v or {}).get("status")) in ("done", "blocked")],
                  key=lambda kv: float((kv[1] or {}).get("ts_epoch") or 0))
    for k, _ in cand[: max(0, len(jobs) - cap)]:
        jobs.pop(k, None)


def _paper_next(state: dict):
    """取当前该采的那张试卷：未完成作业中入队最早的一张（没则 None, None）。

    串行语义的关键——只要还有没采完的试卷，就不去开新试卷。"""
    jobs = _paper_jobs(state)
    pend = [(k, v) for k, v in jobs.items()
            if str((v or {}).get("status")) in ("pending", "active")]
    if not pend:
        return None, None
    pend.sort(key=lambda kv: float((kv[1] or {}).get("ts_epoch") or 0))
    return pend[0]


def _paper_enqueue(state: dict, key: str, kind: str, meta: dict,
                   text: str = "") -> dict:
    """把一张试卷登记为待采作业（已存在则原样返回，不打断进行中的进度）。"""
    jobs = _paper_jobs(state)
    j = jobs.get(key)
    if j is None:
        j = {"status": "pending", "cursor": 0, "total": 0, "buf": [], "added": 0,
             "stall": 0, "fp": "", "ts": _now_iso(), "ts_epoch": time.time()}
        jobs[key] = j
        _cap_paper_jobs(jobs)
    j["kind"] = kind
    j["meta"] = meta or {}
    if text:
        # 始终用本次爬到的正文：作业上若还残留旧正文（内容已变），不清会拿旧内容采
        j["text"] = text
    return j


def _paper_clear(j: dict) -> None:
    """释放作业持有的大对象（源正文 + 待提交缓冲）。"""
    j.pop("text", None)
    j["buf"] = []


def _paper_to_done(state: dict, key: str, j: dict, added: int, n_questions: int,
                   fp: str, collected: dict) -> None:
    """整卷采完且已入库：落 done + 同步写 collected_sets 标记（兼容旧视图与跳过判定）。"""
    j["status"] = "done"
    j["cursor"] = int(j.get("total") or j.get("cursor") or 0)
    j["added"] = int(j.get("added") or 0) + int(added)
    j["stall"] = 0
    j["ts"] = _now_iso()
    j["ts_epoch"] = time.time()
    j.pop("msg", None)
    try:
        prev = collected.get(key) or {}
        collected[key] = {
            "fp": fp, "status": "complete",
            "added": int(prev.get("added", 0)) + int(added),
            "n": int(n_questions), "ts": _now_iso(), "ts_epoch": time.time(),
        }
        _cap_collected(collected)
    except Exception:  # noqa: BLE001  标记失败不阻断采集主流程
        pass
    _paper_clear(j)
    _cap_paper_jobs(_paper_jobs(state))


def _paper_to_blocked(state: dict, key: str, j: dict, msg: str, collected: dict) -> None:
    """连续零进展达上限：标 blocked 并释放缓冲，让后续试卷能继续采。

    「整卷采完才入库」语义下，未采完的缓冲不部分入库；进度（cursor/total）
    保留供管理端查看，人工 reset 后可重排。"""
    j["status"] = "blocked"
    j["msg"] = str(msg)[:160]
    j["ts"] = _now_iso()
    j["ts_epoch"] = time.time()
    _set_fail(collected, key, "已阻塞：" + str(msg)[:140])
    try:
        collected[key]["status"] = "blocked"   # 覆盖 _set_fail 的 failed，语义更准确
    except Exception:  # noqa: BLE001
        pass
    _paper_clear(j)
    _cap_paper_jobs(_paper_jobs(state))


def _paper_stall(state: dict, key: str, j: dict, msg: str, collected: dict) -> str:
    """本轮零进展：stall+1，达上限转 blocked。返回本轮结果状态。"""
    j["stall"] = int(j.get("stall") or 0) + 1
    j["ts"] = _now_iso()
    j["ts_epoch"] = time.time()
    j["msg"] = str(msg)[:160]
    if j["stall"] >= _PAPER_MAX_STALL:
        _paper_to_blocked(state, key, j, msg, collected)
        return "blocked"
    j["status"] = "pending"
    return "stalled"


def paper_jobs_admin(state: dict | None = None, limit: int = 100) -> dict:
    """管理端：试卷作业队列视图（当前在采卷 / 待采明细 / 阻塞明细）。"""
    st = state if state is not None else _load_state()
    jobs = _paper_jobs(st)
    cur_key, cur = _paper_next(st)
    summary = {"total": len(jobs), "pending": 0, "active": 0, "done": 0, "blocked": 0}
    for v in jobs.values():
        s = str((v or {}).get("status"))
        if s in summary:
            summary[s] += 1
    def _row(k, v):
        v = v or {}
        return {"key": k, "kind": v.get("kind"), "status": v.get("status"),
                "cursor": v.get("cursor"), "total": v.get("total"),
                "stall": v.get("stall"), "added": v.get("added"),
                "pending_questions": len(v.get("buf") or []),
                "msg": v.get("msg"), "ts": v.get("ts")}
    blocked = sorted([_row(k, v) for k, v in jobs.items()
                      if str((v or {}).get("status")) == "blocked"],
                     key=lambda x: x.get("ts") or "", reverse=True)[:limit]
    incomplete = sorted([_row(k, v) for k, v in jobs.items()
                         if str((v or {}).get("status")) in ("pending", "active")],
                        key=lambda x: x.get("ts") or "")[:limit]
    cur_row = _row(cur_key, cur) if cur_key else None
    return {"summary": summary, "current": cur_key, "current_detail": cur_row,
            "incomplete": incomplete, "blocked": blocked}


def _paper_commit(state: dict, key: str, j: dict, commit_fn, collected: dict,
                  fp: str) -> dict:
    """整卷片段已跑完 → 一次性提交入库（原子）。

    本轮额度不足则返回 None 的提交结果：保留缓冲、下轮再提交，绝不截断丢题。"""
    buf = j.get("buf") or []
    if not buf:
        _paper_to_done(state, key, j, 0, 0, fp, collected)
        return {"key": key, "status": "done", "added": 0,
                "note": "整卷无题（库内查重命中或非题目页）"}
    n = commit_fn(buf, j)
    if n is None:
        j["status"] = "pending"
        j["msg"] = "整卷已提取，等待下轮额度提交（%d 题）" % len(buf)
        return {"key": key, "status": "incomplete", "added": 0,
                "note": "整卷已提取，本轮额度不足，推迟提交（%d 题）" % len(buf)}
    _paper_to_done(state, key, j, n, len(buf), fp, collected)
    return {"key": key, "status": "done", "added": n,
            "note": "整卷提交 %d 题（提取 %d 题）" % (n, len(buf))}


def _paper_work(state: dict, key: str, j: dict, text: str, subject: str,
                commit_fn, record_error, round_expired, collected: dict) -> dict:
    """把一张试卷从游标处采到完整，再一次性入库（整卷原子提交）。

    返回 {key, status, added, note}；status ∈ done/incomplete/stalled/blocked。
    语义（需求「以每张试卷为单位，确保这张全部采完再采别的」）：
      · 源文本指纹变了 → 作废已缓存结果，从片段 0 重来
      · 片段未跑完 → 存游标与缓冲，下轮续采；不标完成、不入库
      · 片段全跑完 → 尝试整卷提交；本轮额度不足则保留缓冲（绝不截断丢题）
    """
    start = int(j.get("cursor") or 0)
    total_prev = int(j.get("total") or 0)
    if total_prev and start >= total_prev:
        # 上轮已把整卷片段跑完，仅因额度不足未提交：本轮直接提交，不再重复提取
        return _paper_commit(state, key, j, commit_fn, collected, j.get("fp") or "")

    fp = _text_fp(text)
    if j.get("fp") and j.get("fp") != fp:
        j["cursor"] = 0
        j["total"] = 0
        j["buf"] = []
        j["stall"] = 0
        start = 0
    j["fp"] = fp
    j["status"] = "active"

    r, att = _extract_enhanced(
        text, subject, start_piece=start, stop_on_piece_fail=True,
        should_stop=lambda: bool(round_expired()) or _model_down())
    total = int(r.get("total_pieces") or 0)
    cursor = int(r.get("next_piece") or 0)
    j["total"] = total

    # 缓冲去重追加：同一片段下轮重采时不会重复堆积
    buf = j.setdefault("buf", [])
    have = {_q_key(x) for x in buf}
    for q in (r.get("questions") or []):
        k = _q_key(q)
        if k not in have:
            have.add(k)
            buf.append(q)
    if len(buf) > _PAPER_BUF_MAX:
        # 防御：单张卷缓冲异常膨胀（正常一套卷远达不到）→ 封顶为 blocked，不拖垮 state
        record_error(f"试卷 {key}", f"待提交缓冲超上限（{len(buf)} 题）")
        _paper_to_blocked(state, key, j, f"待提交缓冲超上限 {_PAPER_BUF_MAX} 题", collected)
        return {"key": key, "status": "blocked", "added": 0,
                "note": "待提交缓冲超上限，已阻塞"}

    if att:
        j["cursor"] = start          # 停在失败片段，绝不把残缺当进度
        record_error(f"试卷 {key}", "片段提取失败：" + "; ".join(att)[:120])
        st = _paper_stall(state, key, j, "; ".join(att)[:160], collected)
        return {"key": key, "status": st, "added": 0,
                "note": "片段 %d/%d 提取失败，停在原地等重试" % (start, total)}

    if cursor <= start:
        j["cursor"] = start
        if _paper_env_stop(round_expired):
            # 本轮到点/配额耗尽/熔断才没跑动，不是这张卷的问题：不计 stall，直接续采
            j["status"] = "pending"
            return {"key": key, "status": "incomplete", "added": 0,
                    "note": "本轮环境预算到点，下轮继续"}
        st = _paper_stall(state, key, j, "本轮无进展（提取通道无产出）", collected)
        return {"key": key, "status": st, "added": 0, "note": "本轮无进展"}

    j["stall"] = 0
    j["cursor"] = cursor
    j["ts"] = _now_iso()
    j["ts_epoch"] = time.time()

    if cursor < total:
        j["status"] = "pending"
        j["msg"] = "片段 %d/%d" % (cursor, total)
        _paper_checkpoint(state)   # 进度落盘：重启后从 cursor 续采
        return {"key": key, "status": "incomplete", "added": 0,
                "note": "片段 %d/%d，下轮续采" % (cursor, total)}

    return _paper_commit(state, key, j, commit_fn, collected, fp)


def _paper_drive(state: dict, *, acquire, commit_fn, record_error, collected: dict,
                 round_expired, pump=None, max_papers: int = 0) -> list[dict]:
    """串行驱动试卷作业：一张一张采到完整（或阻塞）为止。

    串行语义：每轮只拿队首那未完成的一张，采完（done/blocked）才取下一张；
    incomplete/stalled 表示本轮推不动了，直接收工留给下轮，不跳过去开新卷。
    pump 在队列清空后被调用以发现新试卷（返回 True 表示又入队了）。"""
    out: list[dict] = []
    guard = 0
    while True:
        if _paper_env_stop(round_expired):
            break
        key, j = _paper_next(state)
        if not key:
            if callable(pump) and pump():
                continue
            break
        guard += 1
        if guard > 100000:      # 防御：作业表异常时不死循环
            break
        print(f"[AC-DIAG] paper start kind={j.get('kind')} "
              f"cursor={j.get('cursor')}/{j.get('total')} key={str(key)[:110]}", flush=True)
        if max_papers and len([x for x in out if x.get("status") == "done"]) >= max_papers:
            break
        try:
            text, subject = acquire(key, j)
        except Exception as e:  # noqa: BLE001
            text, subject = None, ""
            record_error(f"试卷 {key} 取材失败", str(e))
        if not text:
            st = _paper_stall(state, key, j, "无法获取试卷源文本", collected)
            out.append({"key": key, "status": st, "added": 0, "note": "取材失败"})
            if st == "blocked":
                continue        # 转下一张，坏卷不堵队列
            break
        res = _paper_work(state, key, j, text, subject, commit_fn,
                          record_error, round_expired, collected)
        out.append(res)
        st = res.get("status")
        print(f"[AC-DIAG] paper {st} key={str(key)[:110]} "
              f"added={res.get('added')} note={res.get('note')}", flush=True)
        if st == "done":
            continue
        if st == "blocked":
            continue            # 连续无进展已封顶，换下一张
        break                   # incomplete / stalled：本轮推不动，留给下轮
    return out


def collected_sets_admin(url: str = "", limit: int = 200) -> dict:
    """管理端：套卷采集标记视图（可按来源页 URL 精确查，或返回最近 limit 条）。"""
    state = _load_state()
    collected = state.get("collected_sets") or {}
    if url:
        mark = collected.get(url)
        return {"total": len(collected), "url": url,
                "mark": mark, "questions": []}
    items = sorted(collected.items(),
                   key=lambda kv: kv[1].get("ts", ""), reverse=True)[:limit]
    return {"total": len(collected),
            "summary": _sets_summary(collected),
            "items": [{"url": k, **v} for k, v in items]}


def _sets_summary(collected: dict) -> dict:
    s = {"complete": 0, "incomplete": 0, "failed": 0, "gated": 0, "blocked": 0,
         "questions": 0}
    for v in collected.values():
        st = v.get("status")
        if st in s:
            s[st] += 1
        s["questions"] += int(v.get("added", 0))
    return s


def reset_collected_sets(urls: list[str] | None = None) -> dict:
    """管理端：清除套卷「已采集完整」标记，使对应链接下次采集重新提取。

    urls=None 或空列表 = 清空全部标记；传具体 URL 列表只清除对应项。
    同时移除页面指纹 url_fp，避免仅重置标记但指纹拦截双重卡住。"""
    state = _load_state()
    collected = state.get("collected_sets") or {}
    url_fp = state.get("url_fp") or {}
    if urls:
        removed = 0
        jobs = state.get("paper_jobs") or {}
        for u in urls:
            # 注意：不能用 or 短路——两个 dict 都要 pop（历史 bug：标记存在时
            # 短路跳过 url_fp.pop，页面指纹残留导致重置不彻底）
            hit = collected.pop(u, None) is not None
            hit = url_fp.pop(u, None) is not None or hit
            # 试卷作业同步作废：作业表里的 done/blocked 仍会挡住重采（新增）
            hit = jobs.pop(u, None) is not None or hit
            if hit:
                removed += 1
    else:
        removed = len(collected)
        collected.clear()
        url_fp.clear()
        # 作业表一并清空（含在采试卷的游标与缓冲），否则 done/blocked 作业挡住重采
        jobs = state.get("paper_jobs")
        if isinstance(jobs, dict):
            jobs.clear()
        state["urls"] = []   # 重置全部时同时回到「新源」语义，下轮重新盯站
    _save_state(state)
    return {"ok": True, "removed": removed}


# ============================================================
# LLM 提取通道（多通道容错降级）
# ============================================================

def _extraction_channels() -> list[dict]:
    """返回提取通道列表 {model, base_url, api_key}（多平台多模型共存）。

    优先级：所有已配置的专用通道（含对首通道网关追加的备用模型）> AUTOCOLLECT_*
    环境变量通道 > 仅平台默认模型（model/base_url/api_key 均为 None）。
    """
    chs = load_llm_channels()
    fb = _fallback_models()
    if chs:
        out = list(chs)
        # 对首通道网关追加同一网关的备用模型（保持旧单通道降级语义）
        base, key = out[0]["base_url"], out[0]["api_key"]
        for m in fb:
            if m and all(m != c["model"] for c in out):
                out.append({"model": m, "base_url": base, "api_key": key})
        return out
    env_model = get("AUTOCOLLECT_MODEL", "")
    if env_model:
        base, key = get("AUTOCOLLECT_BASE_URL", ""), get("AUTOCOLLECT_API_KEY", "")
        models = [env_model] + [m for m in fb if m and m != env_model]
        return [{"model": m, "base_url": base, "api_key": key}
                for m in models]
    return [{"model": None, "base_url": None, "api_key": None}]


def _healthy_order_channels(chs: list[dict]) -> list[dict]:
    """按近期健康度排序提取通道（问题2 效率主因修复）。

    背景：多线程 primary 用 `channels[pi % len]` 分配，总抓着列表头那个通道，
    即使它长期失败（如历史 deepseek-v4-pro fail 206>success 161）仍每次都先打它、
    白耗预算与时间才降级。这里把冷却通道排最末、失败率高且久无成功的通道降权，让
    健康通道担任 primary，显著提高吞吐。平台默认（base_url=None）恒作最后兜底。
    """
    if not chs:
        return []
    stats = _load_channel_stats().get("channels", {})
    now = time.time()

    def _score(c: dict) -> float:
        if _channel_blocked(c):
            return 1_000_000.0
        if not c.get("base_url"):
            return 500_000.0       # 平台默认通道排最末（primary 不启用它，走 _extract_via）
        st = stats.get(_channel_key(c))
        if not st:
            return 0.0
        suc = int(st.get("success_count", 0) or 0)
        fail = int(st.get("fail_count", 0) or 0)
        rate = fail / max(1, suc + fail)
        s = rate * 100.0
        last_ok = _ts_parse(st.get("last_success_ts", "") or "")
        last_at = _ts_parse(st.get("last_attempt_ts", "") or "")
        if last_ok and (now - last_ok) < 2 * 3600:
            s -= 15.0              # 近 2h 有成功 → 可信，优先
        elif last_at and (now - last_at) < 6 * 3600:
            s += 20.0              # 近 6h 一直在试但无成功 → 降权
        return s

    return sorted(chs, key=_score)


def _threads() -> int:
    """多线程并发提取的 worker 数（默认 4，上限 6；>=2 才启用并行）。"""
    return _int_cfg(_load_runtime(), "threads", "AUTOCOLLECT_THREADS", 4, low=1)


def _is_transient_llm_error(e: Exception) -> bool:
    """429 限流 / 5xx / 网络超时抖动 → 短暂重试；业务 4xx / 鉴权 → 不重试。"""
    status = getattr(e, "status_code", None)
    if status is None:
        status = getattr(e, "status", None)
    if status in (429, 500, 502, 503, 504):
        return True
    m = str(e).lower()
    return any(k in m for k in (
        "429", "500", "502", "503", "504",
        "rate limit", "too many", "busy", "overload", "throttl",
        "timeout", "timed out", "connection",
        "限流", "繁忙", "超时", "服务暂不可用", "上游"))


# ============================================================
# 调用级墙钟超时熔断（问题2/3）：LLM 提取必须受 wall-clock 上限约束，
# 杜绝「某通道网络 hang → 单轮 run 数小时不返回 → daemon 静默卡死」。
# 超时视为该通道不可用：记失败并降级下一通道，绝不无限等待。
# ============================================================
_LLM_CALL_TIMEOUT_S = 120.0     # 单次 LLM 提取最长期限（秒）。免费通道响应 9~66s 不稳定，
                                # 60s 墙钟会砍掉大量「即将成功」的长文本请求，调高到 120s。
_LLM_EXEC = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ac-llm-tmo")


def _ai_call(fn: Callable) -> Any:
    """在独立线程执行 LLM 调用并施加上限超时；超时抛 concurrent.futures.TimeoutError。"""
    f = _LLM_EXEC.submit(fn)
    return f.result(timeout=_LLM_CALL_TIMEOUT_S)

def _extract_via(text: str, subject: str) -> tuple[dict, list[str]]:
    """逐个通道尝试 LLM 提取；429/5xx/网络抖动同通道短暂退避重试（最多 3 次尝试）。
    P2 熔断：硬错误（非瞬态，如 401/402/403/404）立即熔断该通道到冷却期，不再反复重试，
    并把「平台默认模型」作为最后兜底通道，保证专用通道全挂时仍能出题。
    返回 (结果, 失败尝试记录)。"""
    from . import admin as admin_biz

    if _model_down():
        # P2 整体熔断生效中：不再逐页调 LLM，直接快速失败（省预算 + 快速返回结果）
        raise ValueError("LLM 通道集体熔断中（模型不可用），请稍后或检查模型配置")

    attempts: list[str] = []
    had_hard = False
    channels = _extraction_channels()
    # P2 熔断：跳过处于冷却期的坏通道
    channels = [c for c in channels if not _channel_blocked(c)]
    # 问题2：按近期健康度排序——长期失败/久无成功的坏通道排最末，健康通道优先承担
    # 提取，避免每次都先耗在失败通道上（历史 deepseek-v4-pro fail 高于 success）。
    channels = _healthy_order_channels(channels)
    # P2 兜底：若全是专用/环境通道（无平台默认），末尾追加平台默认模型作为最后手段。
    # 平台默认由管理侧生效默认模型承担（base_url=None → extract_questions_ai 走默认通道）。
    if channels and not any(c.get("base_url") is None for c in channels):
        channels = channels + [{"model": None, "base_url": None, "api_key": None}]
    for i, c in enumerate(channels):
        hard_fail = False
        for round_ in range(3):
            t0_m = time.monotonic()
            try:
                if round_ == 0:
                    _log_task(c["model"], c.get("base_url"),
                              "片段下发采集", ok=True,
                              detail=f"通道 #{i + 1} 提取（片段 {len(text):,} 字）")
                _add_llm_usage(len(text))   # 预算墙计量：下发即计字符
                r = _ai_call(lambda: admin_biz.extract_questions_ai(
                    text, subject, model=c["model"],
                    base_url=c["base_url"], api_key=c["api_key"]))
                qn = len(r.get("questions") or [])
                if qn:
                    _log_task(c["model"], c.get("base_url"),
                              f"提取成功 {qn} 题", ok=True, detail="")
                    _record_channel_use(c, True, int((time.monotonic() - t0_m) * 1000),
                                        chars=len(text))
                else:
                    _log_task(c["model"], c.get("base_url"),
                              "提取无题目", ok=False, detail="模型返回 0 题")
                    _record_channel_use(c, False, int((time.monotonic() - t0_m) * 1000),
                                        error="模型返回 0 题", chars=len(text))
                _record_model_success()  # 提取成功，重置整体熔断连败计数
                return r, attempts
            except Exception as e:  # noqa: BLE001
                from concurrent.futures import TimeoutError as _TmoErr
                if isinstance(e, _TmoErr):
                    # 墙钟超时：记失败并降级下一通道，不重试（杜绝单通道把整轮拖垮）
                    attempts.append(f"{c['model'] or '默认模型'}：调用超时({_LLM_CALL_TIMEOUT_S:.0f}s)")
                    _log_task(c["model"], c.get("base_url"), "通道超时", ok=False,
                              detail=f"> {_LLM_CALL_TIMEOUT_S:.0f}s 未返回")
                    _record_channel_use(c, False, int((time.monotonic() - t0_m) * 1000),
                                        error=f"调用超时 {_LLM_CALL_TIMEOUT_S:.0f}s", chars=len(text))
                    break
                transient = _is_transient_llm_error(e)
                if round_ < 2 and transient:
                    time.sleep(1.5 * (round_ + 1))  # 1.5s / 3s 退避
                    continue
                attempts.append(f"{c['model'] or '默认模型'}：{e}")
                _log_task(c["model"], c.get("base_url"),
                          "通道失败", ok=False, detail=str(e)[:120])
                _record_channel_use(c, False, int((time.monotonic() - t0_m) * 1000),
                                    error=str(e)[:200], chars=len(text))
                if not transient:
                    hard_fail = True
                break
        # P2 熔断：硬错误（非瞬态）记冷却，本轮后续/后续轮次跳过该通道
        if hard_fail and c.get("base_url"):
            _mark_channel_blocked(c)
        if hard_fail:
            had_hard = True
        if i >= len(channels) - 1:
            break
    _record_model_failure(had_hard)  # P2 整体熔断：连续全通道失败累计，最后进入冷却
    raise ValueError("；".join(attempts) or "LLM 提取失败")


# P2 LLM 通道熔断：硬错误通道冷却期内直接跳过，避免空耗重试。
# 冷却 key = base_url|model；base_url 为空（平台默认）永不被熔断。
_channel_cooldown: dict[str, float] = {}
_channel_cooldown_lock = threading.Lock()


def _channel_key(c: dict) -> str:
    return f"{(c.get('base_url') or '')}|{(c.get('model') or '')}"


def _channel_blocked(c: dict) -> bool:
    if not c.get("base_url"):
        return False
    expire = _channel_cooldown.get(_channel_key(c), 0)
    return expire > time.time()


def _mark_channel_blocked(c: dict, cooldown: int = 600) -> None:
    """硬错误（401/402/403/404 等非瞬态）熔断冷却 cooldown 秒。"""
    if not c.get("base_url"):
        return
    with _channel_cooldown_lock:
        _channel_cooldown[_channel_key(c)] = time.time() + cooldown


# P2 模型整体熔断：当连续多次整轮 LLM 提取都失败（全部通道不可用），
# 说明模型账号/通道集体失效，继续逐页调 LLM 只会空耗预算。
# 统计连续失败次数，≥阈值即进入冷却，run 主流程据此提前停止，等冷却后再试。
_model_fail_lock = threading.Lock()
_model_fail = {"streak": 0, "blocked_until": 0.0}
_MODEL_FAIL_THRESHOLD = 3      # 连续 N 片全通道失败 → 判定模型集体不可用
_MODEL_FAIL_COOLDOWN = 180     # 熔断冷却 180 秒


def _model_down() -> bool:
    return time.time() < _model_fail.get("blocked_until", 0.0)


def _record_model_failure(had_hard: bool) -> None:
    """记录一次整轮提取全部通道失败。had_hard=True（非瞬态，如 402）才累计熔断，
    纯网络/限流抖动不轻易熔断。"""
    with _model_fail_lock:
        if not had_hard:
            return
        st = _model_fail
        st["streak"] = st.get("streak", 0) + 1
        if st["streak"] >= _MODEL_FAIL_THRESHOLD:
            st["blocked_until"] = time.time() + _MODEL_FAIL_COOLDOWN
    if _model_down():
        _log_task("", "", "模型集体熔断", ok=False,
                  detail=f"连续 {_MODEL_FAIL_THRESHOLD} 片全通道失败，暂停采集 "
                         f"{_MODEL_FAIL_COOLDOWN // 60} 分钟，请检查模型账号/通道")


def _record_model_success() -> None:
    with _model_fail_lock:
        _model_fail["streak"] = 0


# ============================================================
# 通道使用统计（持久化；管理端据此识别长期失活 / 高失败通道）
# 独立于 LLM 通道文件，按 base_url|model 聚合每次调用结果。
# ============================================================

_CH_STATS_PATH = Config.data_dir / "autocollect_channel_stats.json"
_CH_INACTIVE_DAYS = 14      # 超过此天数无成功提取 → 视为失活（标红提示清理）


def _load_channel_stats() -> dict:
    try:
        d = json.loads(_CH_STATS_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"channels": {}}
    except Exception:  # noqa: BLE001
        return {"channels": {}}


def _save_channel_stats(data: dict) -> None:
    try:
        _CH_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CH_STATS_PATH.write_text(json.dumps(data, ensure_ascii=False),
                                  encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _ts_parse(s: str) -> float:
    """'%Y-%m-%d %H:%M:%S' 字符串 → 时间戳；解析失败返回 0。"""
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except Exception:  # noqa: BLE001
        return 0.0


def _snapshot_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _record_channel_use(channel: dict, ok: bool, ms: int,
                        error: str = "", chars: int = 0) -> None:
    """记录一次通道调用（成功/失败 + 耗时至毫秒 + 输入字符），供统计与失活判定。

    按 base_url|model 聚合并累计字符/折算 token 与成本；
    base_url 为空（平台默认兜底通道）不统计。
    """
    if not isinstance(channel, dict):
        return
    base = (channel.get("base_url") or "").strip().rstrip("/")
    if not base:
        return
    key = _channel_key(channel)
    n = int(chars or 0)
    tok = int(n * _CJK_TOKEN_FACTOR)
    try:
        data = _load_channel_stats()
        ch = data.setdefault("channels", {}).setdefault(key, {
            "model": channel.get("model") or "",
            "base_url": base,
            "success_count": 0, "fail_count": 0,
            "total_ms": 0, "success_ms": 0, "success_pieces": 0,
            "last_attempt_ts": "", "last_success_ts": "",
            "last_error": "",
            "chars": 0, "tokens": 0, "cost": 0.0,
        })
        now_s = _snapshot_iso()
        ch["last_attempt_ts"] = now_s
        ch["total_ms"] = int(ch.get("total_ms", 0)) + int(ms)
        if n:
            ch["chars"] = int(ch.get("chars", 0)) + n
            ch["tokens"] = int(ch.get("tokens", 0)) + tok
            ch["cost"] = round(float(ch.get("cost", 0.0))
                               + tok / 1_000_000 * _token_price(), 4)
        if ok:
            ch["success_count"] = int(ch.get("success_count", 0)) + 1
            ch["success_ms"] = int(ch.get("success_ms", 0)) + int(ms)
            ch["last_success_ts"] = now_s
        else:
            ch["fail_count"] = int(ch.get("fail_count", 0)) + 1
            if error:
                ch["last_error"] = str(error)[:300]
        _save_channel_stats(data)
    except Exception:  # noqa: BLE001
        pass


def channel_stats() -> dict:
    """每条通道的聚合使用统计 + 失活/高失败标记，供管理端通道表展示。

    失活(inactive)：从未成功且最近尝试已过阈值，或最近一次成功距今超过阈值；
    high_fail：一直在尝试但从未成功；none：从未使用。
    """
    now = time.time()
    data = _load_channel_stats()
    stats = data.get("channels", {})
    rows = []
    for i, c in enumerate(load_llm_channels()):
        key = _channel_key(c)
        st = stats.get(key, {})
        succ = int(st.get("success_count", 0))
        fail = int(st.get("fail_count", 0))
        succ_ms = int(st.get("success_ms", 0))
        last_ok = _ts_parse(st.get("last_success_ts", ""))
        age_ok = now - last_ok if last_ok else None
        last_at = _ts_parse(st.get("last_attempt_ts", ""))
        age_at = now - last_at if last_at else None
        avg_ms = round(succ_ms / succ, 1) if succ else 0
        if succ == 0 and fail == 0:
            flag = "none"                              # 从未使用
        elif succ == 0 and (age_at is None or age_at > _CH_INACTIVE_DAYS * 86400):
            flag = "inactive"                          # 从未成功且许久未试
        elif succ == 0:
            flag = "high_fail"                         # 一直在试但从未成功
        elif last_ok == 0 or age_ok > _CH_INACTIVE_DAYS * 86400:
            flag = "inactive"                          # 长期无成功
        else:
            flag = "ok"
        rows.append({
            "idx": i,
            "model": c.get("model") or "",
            "base_url": (c.get("base_url") or "").strip().rstrip("/"),
            "success_count": succ,
            "fail_count": fail,
            "avg_ms": avg_ms,
            "last_success_ts": st.get("last_success_ts", ""),
            "last_attempt_ts": st.get("last_attempt_ts", ""),
            "last_error": st.get("last_error", ""),
            "flag": flag,
            "chars": int(st.get("chars", 0)),
            "tokens": int(st.get("tokens", 0)),
            "cost": round(float(st.get("cost", 0.0)), 4),
        })
    return {"rows": rows, "inactive_days": _CH_INACTIVE_DAYS}


# ============================================================
# LLM 用量（字符/次数/token/成本）按周期累计 + 每通道成本估算
# ============================================================

_llm_usage_lock = threading.Lock()
_llm_usage = {"chars": 0, "jobs": 0, "tokens": 0, "cost": 0.0}

_CJK_TOKEN_FACTOR = 0.7   # 中文粗估 1 字符 ≈ 0.7 token


def _reset_llm_usage() -> None:
    with _llm_usage_lock:
        for k in _llm_usage:
            _llm_usage[k] = 0


def _llm_usage_get(key: str) -> int:
    with _llm_usage_lock:
        return int(_llm_usage.get(key, 0))


def _add_llm_usage(chars: int) -> None:
    """累计一次下发 LLM 的输入字符数，并折算 token 与估算成本。"""
    n = int(chars or 0)
    if n <= 0:
        return
    tok = int(n * _CJK_TOKEN_FACTOR)
    with _llm_usage_lock:
        u = _llm_usage
        u["chars"] += n
        u["jobs"] += 1
        u["tokens"] += tok
        u["cost"] = round(u.get("cost", 0.0) + tok / 1_000_000 * _token_price(), 4)


def _llm_usage_snapshot() -> dict:
    """当前周期的 LLM 用量快照（含预算墙剩余与成本/价格/折算系数）。"""
    with _llm_usage_lock:
        u = dict(_llm_usage)
    b = _llm_budget_chars()
    u["price_per_1m"] = _token_price()
    u["budget_chars"] = b
    u["tokens_left"] = max(0, (b - u["chars"])) if b > 0 else None
    u["cjk_token_factor"] = _CJK_TOKEN_FACTOR
    return u


def _split_paper_units(text: str) -> list[str] | None:
    """识别「序号 + A、…D、选项」风格的整卷文本并切成独立题目单元。

    返回单元列表；不足 3 个有效题号视为普通文档，返回 None（走原有分批逻辑）。

    #问题1-资料分析：题组前通常有一段共享材料/题干（如“根据以下资料，回答131~135题”
    之后的大段文字/图表），原实现把该材料头丢掉，导致题只有问句没有题干。
    这里把紧跟题组的材料块附加到题组首题单元，使 LLM 在同一片段内看到“材料+整组小题”，
    按其资料分析规则把材料回填进每道小题的题干。首次材料前若存在较长正文也一并带上。
    """
    cands = [(m.start(), int(m.group(1)))
             for m in re.finditer(
                 r"(?<![\dA-Za-z])(\d{1,3})(?:\s+|[.、．])\s*(?=[\u4e00-\u9fa5])", text)]
    valid: list[tuple[int, int]] = []
    for pos, num in cands:
        if num <= 0 or num > 999:
            continue
        tail = text[pos + len(str(num)): pos + len(str(num)) + 601]
        if not re.search(r"[A-D]\s*[、.．:]", tail):
            continue
        valid.append((pos, num))
    # 取「最长合法题号链」（相邻差 -3..200 视为连续，断链则重启新链）。
    # 旧实现只顺延不重启：页头杂数（如「50 爱真题」「646 一、政治理论」）会被当作
    # 首个题号，导致其后半段真实题序被整体丢弃（aipta 行测卷 1~46 题漏采根因）。
    starts: list[int] = []
    best: list[int] = []
    last_num = -1000
    for pos, num in valid:
        if starts and not (-3 <= num - last_num <= 200):
            if len(starts) > len(best):
                best = starts
            starts = []
        starts.append(pos)
        last_num = num
    if len(starts) > len(best):
        best = starts
    starts = best
    if len(starts) < 3:
        return None

    # 材料块起点：默认就是题号本行；命中经典的“根据以下资料，回答第131~135题”这类
    # 资料分析/一拖多题组头时，把标记后到该题前的整段作为题干材料一起附加。
    # 仅匹配“…资料…回答第N题”（带题目编号目标），避免误命中题干里的“根据上述材料回答”；
    # 若出现多个标记，依序附加到紧随其后的第一道题号。
    mat_start = list(starts)
    mat_pat = re.compile(
        r"(?:根据|阅读|依据|基于)(?:以下|下面|上述)?(?:资料|材料|文字|图表)"
        r"[^第]{0,6}?回答\s*第?\s*\d+\s*(?:~|至|—|－)?\s*\d*题?")
    for m in mat_pat.finditer(text):
        nxt = None
        for i, s in enumerate(starts):
            if s >= m.end():
                nxt = i
                break
        if nxt is not None and mat_start[nxt] > m.end():
            seg = text[m.end():starts[nxt]]
            if len(seg.strip()) >= 40:
                mat_start[nxt] = m.end()
    # 兜底：无标记时用“上一题答案后的大段正文”视作下一题组材料
    ans_pat = re.compile(r"(?:答案|参考答案|正确答案)\s*[:：]?")
    for i in range(1, len(starts)):
        if mat_start[i] != starts[i]:
            continue
        prev_seg = text[starts[i - 1]:starts[i]]
        am = list(ans_pat.finditer(prev_seg))
        if not am:
            continue
        body_end = starts[i - 1] + am[-1].end()
        gap = text[body_end:starts[i]]
        if len(gap.strip()) >= 60 and not re.search(r"[A-E]\s*[、.．:)](?:$|\s|[A-E])", gap):
            mat_start[i] = body_end
    # 卷首材料
    if starts and len(text[:starts[0]].strip()) >= 40:
        mat_start[0] = 0

    units = []
    for i, s in enumerate(starts):
        lo = mat_start[i]
        e = starts[i + 1] if i + 1 < len(starts) else len(text)
        units.append(text[lo:e].strip())
    return units


def _extract_enhanced(text: str, subject: str,
                      threads: int | None = None,
                      should_stop=None,
                      start_piece: int = 0,
                      stop_on_piece_fail: bool = False) -> tuple[dict, list[str]]:
    """整卷识别 → 按题目单元成组 → 逐组提取合并；普通文档走原逻辑。

    返回 (结果, 尝试失败记录)。结果的 `complete` 字段（仅整卷路径）标记是否
    跑完了全部提取片段：套卷采集完整性判定依据——中途被预算/熔断提前停止则
    complete=False，该套卷下轮续采而非永久跳过。

    修复：原 2500 字符硬切会把题从中切断导致大面积解析失败/残缺，
    行测整卷（如 120 题）只能回收少量题目。这里保证每个提取片段
    恰好包含完整题目单元，提高召回与命中率。

    #40 多线程：threads>1 且片段数>1 时，用线程池并发提取——片段按轮询分配到
    不同通道（不同模型），实现「多个模型并行采集」。任一通道失败由 _extract_via
    逐通道降级兜底，不影响其余片段；结果按片段序去重合并。

    #50 预算提前停止：should_stop 为可调用对象（返回 True 表示本轮已凑够目标题数），
    在每批片段产出后检查，命中即立即停止后续片段的 LLM 调用——避免"预算只需 20 题，
    却把整卷 120 题的 LLM 全提一遍"的巨额浪费（这是整轮采集卡死/超慢的主因）。

    断点续采（整卷作业）：start_piece 指定从第几个片段开始，返回值里的
    next_piece = 已完整跑完的片段数（下轮从这里续），total_pieces = 片段总数。
    complete 等价于 next_piece >= total_pieces。历史问题：没有游标，被预算打断后
    下轮仍从片段 0 重跑，配额永远不够就永远采不完，且每轮重复烧同样的钱。

    stop_on_piece_fail：片段提取失败时立即停在该片段（游标不前移），供整卷作业
    把致命失败当成「这张没采完」而非「跳过继续」。旧行为（默认 False）会把失败
    片段当空结果跳过，导致整卷静默缺题却照样标完成。
    """
    units = _split_paper_units(text)
    if not units:
        r, att = _extract_via(text, subject)
        if isinstance(r, dict):
            r["complete"] = True   # 非整卷单页：一次性提取，跑完即完整
            r["next_piece"] = 1    # 非整卷无片段概念：单次调用即全部
            r["total_pieces"] = 1
        return r, att
    pieces: list[str] = []
    acc: list[str] = []
    acc_len = 0
    max_len = 1200   # 单片提取文本上限：免费通道出题速度与片长正相关，小片不易撞墙钟超时，
                     # 失败损失也小（行测单元普遍 150~400 字，一片约 3~5 题）
    for u in units:
        if acc and acc_len + len(u) > max_len:
            pieces.append("\n".join(acc))
            acc, acc_len = [], 0
        acc.append(u)
        acc_len += len(u)
    if acc:
        pieces.append("\n".join(acc))

    n_threads = int(threads or _threads())
    channels = _extraction_channels()
    # 问题2：primary 通道按健康度排序并跳过冷却通道——让健康通道担任多线程 primary，
    # 失败通道交回 _extract_via 最后兜底，避免线程一直抓到列表头的坏通道白耗预算。
    channels = _healthy_order_channels([c for c in channels
                                       if not _channel_blocked(c)])

    def _hit_stop() -> bool:
        try:
            if callable(should_stop) and should_stop():
                return True
        except Exception:  # noqa: BLE001  回调异常不阻断采集
            pass
        return _llm_budget_exceeded()   # 预算墙：周期字符配额用尽即停止

    def _absorb(piece_questions):
        for q in piece_questions:
            qt = (q.get("question") or "").strip()
            if not qt:
                continue
            key = qt[:80] + "|" + "".join(q.get("options") or [])[:60] + "|" + qt[-60:]
            if key in seen:
                continue
            seen.add(key)
            questions.append(q)

    def _extract_one(piece: str, primary: dict) -> list[dict]:
        """用指定通道优先提取；失败时由 _extract_via 逐通道降级兜底。"""
        try:
            if primary.get("base_url") and primary.get("model"):
                _log_task(primary["model"], primary.get("base_url"),
                          "多线程并行提取", ok=True,
                          detail=f"分配线程（片段 {len(piece):,} 字）")
                from . import admin as admin_biz
                t0_m = time.monotonic()
                _add_llm_usage(len(piece))   # 预算墙计量：主通道下发计字符
                r = _ai_call(lambda: admin_biz.extract_questions_ai(
                    piece, subject, model=primary["model"],
                    base_url=primary["base_url"], api_key=primary["api_key"]))
                _record_channel_use(primary, True,
                                    int((time.monotonic() - t0_m) * 1000),
                                    chars=len(piece))
                return r.get("questions") or []
        except Exception:  # noqa: BLE001  主通道失败回落 _extract_via 全通道降级
            pass
        try:
            r, _ = _extract_via(piece, subject)
            return r.get("questions") or []
        except ValueError as e:
            attempts.append(str(e))
            return []

    questions: list[dict] = []
    seen: set[str] = set()
    attempts: list[str] = []
    # 断点游标：next_piece = 已完整跑完的片段数（下轮从这里续采）
    next_piece = max(0, min(int(start_piece or 0), len(pieces)))
    total_pieces = len(pieces)

    # 进入即已触发停止条件（如本轮墙钟已到点）：不做任何 LLM 调用，直接回未完成，
    # 由下轮续采。避免了「本轮白烧一次 LLM 却零进展」的浪费。
    if next_piece < total_pieces and _hit_stop():
        return {"questions": [], "complete": False,
                "next_piece": next_piece, "total_pieces": total_pieces}, attempts

    if n_threads > 1 and len(pieces) > 1 and channels:
        from concurrent.futures import ThreadPoolExecutor
        window = max(1, int(n_threads))

        def _idx(idx_piece):
            pi, piece = idx_piece
            primary = channels[pi % len(channels)]
            return _extract_one(piece, primary)

        # 分窗口提交并逐窗吸收结果，窗口间检查 should_stop → 预算满足即停止，
        # 避免把整卷全部片段的 LLM 都跑完才返回（整轮采集超慢的根源）。
        with ThreadPoolExecutor(max_workers=window) as ex:
            while next_piece < total_pieces:
                w = pieces[next_piece: next_piece + window]
                base = next_piece
                n_att0 = len(attempts)
                for qs in ex.map(_idx, enumerate(w, start=base)):
                    _absorb(qs if isinstance(qs, list) else [])
                if stop_on_piece_fail and len(attempts) > n_att0:
                    # 窗口内有片段失败：整窗重来（游标退回 base），不把残缺窗口算作进度
                    next_piece = base
                    break
                next_piece = base + len(w)   # 整窗跑完才算进度
                if _hit_stop():
                    break
    else:
        while next_piece < total_pieces:
            n_att0 = len(attempts)
            _absorb(_extract_one(pieces[next_piece], {}))
            if stop_on_piece_fail and len(attempts) > n_att0:
                break                    # 停在该片段：游标不前移，下轮从它续
            next_piece += 1
            if _hit_stop():
                break
    complete = next_piece >= total_pieces
    return {"questions": questions, "complete": complete,
            "next_piece": next_piece, "total_pieces": total_pieces}, attempts


# ============================================================
# 运行历史（data/autocollect_history.jsonl，仅记录真实执行轮次）
# ============================================================

def _history_append(result: dict) -> None:
    try:
        _HIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        if _HIST_PATH.exists():
            rows = [x for x in _HIST_PATH.read_text(encoding="utf-8").splitlines()
                    if x.strip()]
        rows.append(json.dumps({"ts": _now_iso(), **result}, ensure_ascii=False))
        _HIST_PATH.write_text("\n".join(rows[-500:]) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def history(limit: int = 50) -> list[dict]:
    """最近 limit 条运行历史（时间倒序）。"""
    try:
        rows = [json.loads(x) for x in
                _HIST_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]
    except Exception:  # noqa: BLE001
        rows = []
    return rows[-max(1, int(limit)):][::-1]


# ============================================================
# 管理端：runtime 配置读写
# ============================================================

def config_admin() -> dict:
    """管理端配置视图：生效值 + LLM 通道脱敏信息。"""
    return {
        "enabled": _enabled(),
        "interval_secs": _interval_secs(),
        "cpu_max": _thresholds()[0],
        "mem_max": _thresholds()[1],
        "max_per_cycle": _max_per_cycle(),
        "max_site_pages": _max_site_pages(),
        "threads": _threads(),
        "urls": _url_list(),
        "search_queries": _search_queries(),
        "site_whitelist": _whitelist_list(),
        "site_pages_map": _site_pages_map(),   # saE 逐站页数额度差异化
        "fallback_models": _fallback_models(),
        "autonomous": _autonomous(),
        "autonomous_effective": _effective_autonomous(),
        "autonomous_queries": _autonomous_queries_cap(),
        "schedule_enabled": _schedule_window()[0],
        "schedule_start": _schedule_window()[1],
        "schedule_end": _schedule_window()[2],
        "retry_failed_auto": _retry_failed_auto(),
        # 采集质量增强：来源优先级 + 退避参数 + AI 自评 + 通知
        "priorities": _priorities(),
        "backoff_base_secs": _BACKOFF_BASE,
        "backoff_cap_secs": _BACKOFF_CAP,
        "quality_sample_rate": _quality_sample_rate(),
        "quality_rules": _quality_rules(),
        "notify": _notify_config(),
        # 手动站点规则 + LLM 周期字数配额 / 成本参数
        "site_rules": _site_rules_config(),
        "llm_budget_chars": _llm_budget_chars(),
        "llm_price_per_1m": _token_price(),
        "round_budget_secs": _round_budget_secs(),   # 轮次墙钟上限（防单轮跑数小时）
        "round_budget_default_secs": _ROUND_BUDGET_DEFAULT_S,
        "llm_usage": _llm_usage_snapshot(),   # 当前周期用量（含预算墙剩余）
        "llm": llm_channel_info(),
        "llm_channels": llm_channels_info(),  # 多通道脱敏视图（前端线程面板使用）
    }


def save_config(p: dict) -> dict:
    """保存 runtime 配置（全量覆盖；越界/类型非法抛 ValueError）。"""
    def _to_int(v, default: int, low: int | None = None) -> int:
        try:
            iv = int(v) if v is not None else default
        except (TypeError, ValueError):
            raise ValueError(f"数值非法：{v}") from None
        return iv if low is None else max(low, iv)

    def _to_list(v) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return [x.strip() for x in str(v).replace("\n", ",").split(",")
                if x.strip()]

    def _to_float(v, default: float, low: float | None = None) -> float:
        try:
            fv = float(v) if v is not None else default
        except (TypeError, ValueError):
            raise ValueError(f"数值非法：{v}") from None
        return fv if low is None else max(low, fv)

    def _site_pages_from(v) -> dict:
        """归一化逐站页数额度表：{url或域名: 页数}；键去空、值限 1~500。"""
        src = v.get("site_pages_map") if isinstance(v, dict) else None
        out = {}
        if isinstance(src, dict):
            for k, val in src.items():
                kk = str(k).strip()
                if not kk:
                    continue
                try:
                    out[kk] = max(1, min(500, int(val)))
                except (TypeError, ValueError):
                    continue
        return out

    def _site_rules_from(v) -> list[dict]:
        """归一化自定义站点规则：仅保留合法字段，丢弃非法正则条目标记。"""
        src = v.get("site_rules") if isinstance(v, dict) else None
        out = []
        if isinstance(src, list):
            for r in src:
                if not isinstance(r, dict):
                    continue
                out.append({
                    "domain": str(r.get("domain") or "").strip(),
                    "name": str(r.get("name") or "").strip(),
                    "detail_regex": str(r.get("detail_regex") or "").strip(),
                    "max_pages": _to_int(r.get("max_pages"), 30, low=1),
                    "enabled": bool(r.get("enabled", True)),
                })
        return out

    enabled = bool(p.get("enabled", _enabled()))
    interval = _to_int(p.get("interval_secs"), _interval_secs(), low=60)
    if interval > 86400:
        raise ValueError("采集周期不能超过 86400 秒（24 小时）")
    cpu = _to_int(p.get("cpu_max"), _thresholds()[0], low=1)
    mem = _to_int(p.get("mem_max"), _thresholds()[1], low=1)
    if cpu > 99 or mem > 99:
        raise ValueError("资源阈值需在 1~99 之间")
    budget = _to_int(p.get("max_per_cycle"), _max_per_cycle(), low=1)
    if budget > _RUN_ADD_LIMIT:
        raise ValueError(f"每轮预算不能超过 {_RUN_ADD_LIMIT} 题")
    site_pages = _to_int(p.get("max_site_pages"), _max_site_pages(), low=1)
    if site_pages > 500:
        raise ValueError("整站爬取页数上限不能超过 500 页")
    threads = _to_int(p.get("threads"), _threads(), low=1)
    if threads > 6:
        raise ValueError("并发线程数不能超过 6")
    autonomous = bool(p.get("autonomous", _autonomous()))
    auto_q = _to_int(p.get("autonomous_queries"), _autonomous_queries_cap(), low=1)
    if auto_q > 100:
        raise ValueError("自主词池每轮数量不能超过 100 条")
    # 采集调度增强：时间窗口 + 自动补采
    schedule_enabled = bool(p.get("schedule_enabled", _schedule_window()[0]))
    def _hour(v, default: int) -> int:
        try:
            iv = int(v) if v is not None else default
        except (TypeError, ValueError):
            raise ValueError(f"小时数值非法：{v}") from None
        if not (0 <= iv <= 23):
            raise ValueError("时间窗口小时需在 0~23 之间")
        return iv
    start_h = _hour(p.get("schedule_start"), _schedule_window()[1])
    end_h = _hour(p.get("schedule_end"), _schedule_window()[2])
    retry_auto = bool(p.get("retry_failed_auto", _retry_failed_auto()))
    _save_runtime({
        "enabled": enabled,
        "interval_secs": interval,
        "cpu_max": cpu,
        "mem_max": mem,
        "max_per_cycle": budget,
        "max_site_pages": site_pages,
        "threads": threads,
        "teachers": [],
        "urls": _to_list(p.get("urls")),
        "search_queries": _to_list(p.get("search_queries")),
        "site_whitelist": _to_list(p.get("site_whitelist")),
        "fallback_models": _to_list(p.get("fallback_models")),
        "autonomous": autonomous,
        "autonomous_queries": auto_q,
        "schedule_enabled": schedule_enabled,
        "schedule_start": start_h,
        "schedule_end": end_h,
        "retry_failed_auto": retry_auto,
        # 采集质量增强：来源优先级 + AI 自评抽样 + Webhook 通知
        "priorities": _normalize_priorities(p.get("priorities")),
        "quality_sample_rate": max(0.0, min(1.0, _to_float(
            p.get("quality_sample_rate"), _quality_sample_rate(), low=0.0))),
        "quality_rules": str(p.get("quality_rules") or _quality_rules())[:500],
        "webhook_url": str(p.get("webhook_url") or "").strip()[:1000],
        "notify_on": [s for s in _to_list(p.get("notify_on"))
                      if s in ("summary", "fail_alert")],
        "fail_alert_threshold": _to_int(p.get("fail_alert_threshold"),
                                        _notify_config()["fail_alert_threshold"], low=1),
        # 手动站点规则（站点详情页正则可配置）+ LLM 配额/成本参数
        "site_rules": _site_rules_from(p),
        "site_pages_map": _site_pages_from(p),   # saE 逐站页数额度差异化
        "llm_budget_chars": _to_int(p.get("llm_budget_chars"),
                                    _llm_budget_chars(), low=0),
        "llm_price_per_1m": _to_float(p.get("llm_price_per_1m"),
                                      _token_price(), low=0),
        "round_budget_secs": _to_int(p.get("round_budget_secs"),
                                     _round_budget_secs(), low=0),
    })
    with _lock:
        _status["enabled"] = _enabled()
    ensure_daemon()
    return config_admin()

def _search_available() -> bool:
    """搜索是否可用：内置 search.py 契约 或 能力层 mcp（auto/mcp 且网关已配置）。
    默认（无 MCP 网关）时与旧 search.search_available() 结果一致（行为保持）。"""
    from . import search as _search
    from .capabilities import registry
    if _search.search_available():
        return True
    return registry.resolve("search") in ("mcp", "auto") and registry.has_mcp_config()


def _search_cap_web(query: str, max_results: int = 5) -> tuple[list[dict], str]:
    """联网搜索能力调度（说明书 §五）：(results, warning)，对齐 search.py 现契约。
    默认/builtin 直调 search.web_search（行为不变）；CAP_SEARCH_PROVIDER=mcp/auto+
    网关配置时先走 MCP 搜索工具，失败自动回退 builtin（warning 透出）。"""
    from .capabilities import registry

    return registry.call("search", query=query, max_results=max_results)


def _fetch_text(url: str, timeout: int = 20) -> str:
    """抓取 URL 并粗提正文文本；HTML 去除脚本/标签，非 HTML 原文返回。
    能力层接入（说明书 §五）：经 capabilities.registry 调度 fetch 能力。
    默认/builtin 行为与原实现逐字节一致（httpx+UA+重定向+粗提正文）；
    CAP_FETCH_PROVIDER=mcp/auto 且网关可用时走 MCP（JS 渲染/正文抽取更稳）。"""
    from .capabilities import registry

    result, warning = registry.call("fetch", url=url, timeout=timeout)
    if warning:
        # 保持旧语义：调用方以异常感知失败（搜索结果回退 snippet）
        raise ValueError(warning)
    return (result or {}).get("text", "")


# ============================================================
# 按题库平台定制采集规则（需求：不同公开题库 → 不同精准采集策略）
# 每套规则声明该平台真正承载题目的「详情页路径模式」，爬虫只把命中该模式的
# 页面交给 LLM 提取，其余（导航/列表/搜索/资讯简介）只用于发现链接、不出题，
# 从而避免把 SEO 简介页、板块导航、后台杂页当成题目收进题库。
#
#   gwy.gkzhenti.cn /paper/{id}                  整卷页（行测/申论/面试整卷）
#   www.aipta.com  /article/{id}.html            真题套卷文章（含行测/申论）
#   kaogong.suanzan.com /paper/{province}/{no}   官方行测/申论真题套题页
#   www.51test.net  /show/{id}.html              申论/行测试题文章
#   www.gwy.com     /gjgwy/{id}.html             国家公务员题库单题文章
# ============================================================
_SITE_RULES = {
    # 注意：键用域名，匹配用"部分包含"，避免 www. 前缀差异
    "gkzhenti.cn": {
        "detail": re.compile(r"/paper/\d+$"),
        "name": "公开真题库(整卷)",
        "max_pages": 40,
    },
    "aipta.com": {
        "detail": re.compile(r"/article/\d+\.html$"),
        "name": "爱刷题(真题套卷)",
        "max_pages": 40,
    },
    "suanzan.com": {
        "detail": re.compile(r"/paper/[a-z0-9]+/[\w-]+$"),
        "name": "公考考试真题(套题)",
        "max_pages": 40,
    },
    "51test.net": {
        "detail": re.compile(r"/show/\d+\.html$"),
        "name": "无忧考网(试题文章)",
        "max_pages": 40,
    },
    "gwy.com": {
        # 详情页同时存在两种形态：/gjgwy/{id}.html 与 /gjgwy/kstk/{id}.html，
        # 起始页 https://www.gwy.com/gjgwy/kstk/ 的列表链接正是 kstk 子路径。
        "detail": re.compile(r"/gjgwy(?:/kstk)?/\d+\.html$"),
        "name": "人事考试网(题库文章)",
        "max_pages": 30,
    },
}

# 明显不含题目的高频噪音路径，列为忽略，避免爬虫浪费预算/收录杂页
_NOISE_PATH = re.compile(r"(?i)(?:/login|/register|/signin|/signup|/logout|"
                         r"/search|/so\.|/tag|/tags|/help|/about|/contact|"
                         r"/(?:feedback|fankui)|/zt/|/tiku/|/category/|/list/?$|"
                         r"/?[?&].{0,4}page=)")

# P4 搜索降噪：联网搜索常命中百科/词典/资讯等低价值非题库页面，直接跳过，省预算保纯度。
# key 为 host（子串匹配，如 "baike" 命中 baike.baidu.com）。
_SEARCH_BLOCK_HOSTS = ("baike", "zhuanlan", "elsevier", "iciba", "hanyuguoxue",
                       "hgcha", "nasachina", "wuli.ac.cn", "zhihu.com/question",
                       "renrendoc", "doc88", "book118", "jd.com", "tmall",
                       "diaoyu", "qihoo", "sohu", "sina", "qq.com/news",
                       # 低价值资讯/政务/百科/时评源：与公考相关但非题目，整站阻断
                       "court.gov.cn", "jcy.gov.cn", "cyol.com", "people.com.cn",
                       "xinhuanet.com", "chinanews.com", "huanqiu.com", "gmw.cn",)


def _search_url_allowed(url: str) -> bool:
    """搜索结果页是否值得抓取：host 未命中阻断名单则放行（内容像不像由抽取前门控把关）；
    saA：命中站点白名单的信任域无论与否直接放行（不进 blocked）。"""
    from urllib.parse import urlparse
    host = (urlparse(url).netloc or "").lower()
    if not host:
        return False
    if any(h and h in host for h in _whitelist_hosts()):
        return True
    return not any(b in host for b in _SEARCH_BLOCK_HOSTS)


def _url_allowed_for_extract(url: str, text: str) -> bool:
    """来源页面抽取前的综合门控：噪音域名跳过 + 内容先判像题目（P3/P4）。"""
    if not _search_url_allowed(url):
        return False
    return _looks_like_questions(text)


def _site_rules_config() -> list[dict]:
    """用户在管理端自定义的站点详情页规则（优先级高于内建 _SITE_RULES）。"""
    rt = _load_runtime()
    v = rt.get("site_rules")
    if not isinstance(v, list):
        return []
    return [r for r in v if isinstance(r, dict)]


def _compile_site_rule(r: dict) -> dict | None:
    """把自定义条目标为运行态规则：detail 编译为 re；非法正则返回 None 跳过。"""
    domain = str(r.get("domain") or "").strip().lower()
    pat = str(r.get("detail_regex") or "").strip()
    if not domain or not pat or r.get("enabled", True) is False:
        return None
    try:
        rex = re.compile(pat)
    except re.error:
        return None
    return {"detail": rex,
            "name": str(r.get("name") or domain),
            "max_pages": int(r.get("max_pages") or 0) or 30}


def _site_rule(start_url: str) -> dict | None:
    """按起始 URL 命中采集规则；优先自定义规则，其次内建 _SITE_RULES；未命中返回 None。"""
    from urllib.parse import urlparse

    host = (urlparse(start_url).netloc or "").lower()
    for r in _site_rules_config():          # 自定义优先
        rule = _compile_site_rule(r)
        if rule and str(r.get("domain") or "").lower().strip() in host:
            return rule
    for domain, rule in _SITE_RULES.items():  # 内建兜底
        if domain in host:
            return rule
    return None


def _is_detail_page(path: str, rule: dict | None, host: str) -> bool:
    """判断某路径是否为可产出题目的详情页（无规则=任何页面都算，通用态）。"""
    if not rule:
        return True
    return bool(rule["detail"].match(path))


def _crawl_site(start_url: str, max_pages: int = 30, timeout: int = 15) -> list[tuple[str, str]]:
    """R2 采集源整站爬取：BFS 同源遍历至多 max_pages 页，返回 [(url, 页面标题, 纯文本), ...]。
    ≥C9：命中定制规则（_SITE_RULES）时，只把命中详情页模式的内容返回给 LLM 提取，
    其余页面（列表/导航/简介）只参与链接发现；无规则则保持通用全页提取。
    起始页优先在前；单页失败跳过不中断整站；URL 归一化去重防环形爬取。"""
    import httpx
    from urllib.parse import urlparse

    rule = _site_rule(start_url)
    host = (urlparse(start_url).netloc or "").lower()
    cap = rule["max_pages"] if (rule and rule.get("max_pages")) else max_pages
    # R2 单站整体时长预算：防止站点无响应时靠"每页 15s 超时"无限拖慢整轮采集
    # （症状即"采集一直 running 但 CPU≈0、日志为空"——看似卡死实为慢速空等网络）
    wall_start = time.time()
    wall_budget = max(60.0, cap * 8.0)
    pages: list[tuple[str, str, str]] = []
    visited: set[str] = set()
    queue: list[str] = [start_url]
    with httpx.Client(timeout=timeout, headers={"User-Agent": _UA},
                      follow_redirects=True) as c:
        while queue and len(pages) < cap:
            if time.time() - wall_start > wall_budget:
                break
            u = queue.pop(0)
            if u in visited:
                continue
            visited.add(u)
            try:
                r = c.get(u)
                r.raise_for_status()
            except Exception:  # noqa: BLE001  单页抓取失败跳过
                continue
            ctype = (r.headers.get("content-type") or "").lower()
            if "html" in ctype:
                is_detail = _is_detail_page(urlparse(str(r.url)).path, rule, host)
                decoded = _decode_resp_text(r)
                # #60：详情页文本保留原位 <img>（图形推理/图表下载托管 /api/qimg），
                # 列表/导航页只需链接发现，用普通文本提即可，省下载与预算。
                # 兼容旧数据：_save_img 落在 data/qimg/{tid}，与文档配图同目录被 /api/qimg 提供。
                if is_detail:
                    try:
                        from .study import SHARED_TEACHER_ID
                        from .config import Config
                        img_dir = Config().data_dir / "qimg" / SHARED_TEACHER_ID
                        text = _html_to_text_wimg(decoded, str(r.url), img_dir, c)
                    except Exception:  # noqa: BLE001  图片托管异常不阻断整页文本
                        text = _html_to_text(decoded)
                else:
                    text = _html_to_text(decoded)
                # 精准规则：只收集详情页；导航/列表页仅用于发现更多链接
                if is_detail:
                    pages.append((str(r.url), _page_title(r.text), text))
                    # 命中详情页即进入页面级预算计数，控制总提取页数
                    if len(pages) >= cap:
                        break
                # 发现新链接入队（预算保护：候选量不超上限 3 倍，防海量反爬参数 URL）
                # #57 修复：有定制规则时优先入队详情页（list 页导航链往往排在题目链前面，
                # 若按原始顺序在小预算下入队，导航链会抢先填满预算把详情链饿死，导致该站 0 题）。
                extracted = _extract_site_links(r.text, str(r.url))
                if rule:
                    detail_l = [l for l in extracted
                                if _is_detail_page(urlparse(l).path, rule, host)]
                    rest_l = [l for l in extracted if l not in detail_l]
                else:
                    detail_l, rest_l = [], extracted
                for link in detail_l + rest_l:
                    if len(visited) + len(queue) >= max_pages * 3:
                        break
                    lpath = urlparse(link).path
                    # 命中定制规则时，跳过明显噪音路径，省预算
                    if rule and _NOISE_PATH.search(lpath):
                        continue
                    if link not in visited and link not in queue:
                        queue.append(link)
            else:
                # 非 HTML（如 PDF/网盘）：有规则时文献类链接非题目，跳过；无规则保留原文
                if not rule:
                    pages.append((u, "", r.text.strip()))
    return pages



# ============================================================
# 单轮采集主流程
# ============================================================

def run_once(force: bool = False, retry_failed: bool = False) -> dict:
    """执行一轮自动采集。返回 {skipped?, added, urls, docs, search, errors, error_stats}。

    retry_failed=True（#12 一键补采）：仅重跑上一轮记录失败的来源
    （失败 URL / 失败文档 / 失败搜索词），跳过正常增量，用于失败后补采。"""
    from . import admin as admin_biz
    from .docreg import get_doc_registry
    from .study import get_study_store, SHARED_TEACHER_ID

    if not _enabled() and not force:
        return {"skipped": True, "reason": "AUTOCOLLECT_ENABLED 未启用", "added": 0}

    # #12 一键补采早退：失败队列全空时直接返回，避免空跑全量采集
    if retry_failed:
        fs = failed_sources()
        if fs["count"] == 0:
            return {"skipped": True, "reason": "无可补采的失败来源", "added": 0,
                    "failed_sources": fs}

    cpu_max, mem_max = _thresholds()
    ok, res = resource_ok(cpu_max, mem_max)
    if not ok and not force:
        return {"skipped": True, "reason": res["reason"], **res, "added": 0}

    global _round_started_at
    with _lock:
        if _status.get("running"):
            return {"skipped": True, "reason": "上一轮采集尚未结束", "added": 0}
        _status["running"] = True
    _round_started_at = time.monotonic()   # 轮次墙钟预算起点

    _reset_llm_usage()   # 每轮独立清零 LLM 用量，配额按轮计算

    print("[AC-DIAG] run_once entered try (force=%s)" % force, flush=True)
    try:
        st = get_study_store()
        reg = get_doc_registry()
        cfg = Config()
        teacher_ids = _teacher_ids() or [
            t["teacher_id"] for t in cfg.list_teachers_all() if t.get("enabled")]
        if not teacher_ids:
            teacher_ids = [cfg.teacher_id]

        # 入库自动打标：知识树叶子节点名→node_id 索引（跨启用老师取并集），
        # 采集题 category_id 为空时按最长子串匹配自动关联（零 LLM 成本）。
        node_index = _knowledge_node_index(teacher_ids)

        state = _load_state()
        _pruned = _prune_stale_sources(state)   # 清理已从 config.urls 移出的残留来源记录
        if _pruned:
            print(f"[AC-DIAG] pruned stale source records={_pruned}", flush=True)
        added_total = 0
        dup_total = 0
        invalid_total = 0
        filtered_total = 0
        offdomain_total = 0
        errors: list[str] = []
        error_stats: dict[str, int] = {c: 0 for c in _ERROR_CATEGORIES}
        detail = {"urls": {"attempted": 0, "fetched": 0, "added": 0},
                  "docs": {"scanned": 0, "processed": 0, "added": 0},
                  "search": {"attempted": 0, "added": 0},
                  # 试卷作业观测：整卷完成/续采/受阻/阻塞各多少张
                  "papers": {"done": 0, "incomplete": 0, "stalled": 0, "blocked": 0},
                  # saD 命中漏斗：分阶段累计，反映「候选→抓到→门控→抽取→入题」每一级过滤
                  "funnel": {"search": 0, "site": 0, "detail": 0,
                             "qualify": 0, "parse": 0, "added": 0}}

        def _record_error(source: str, msg: str) -> None:
            """#12 结构化错误：追加纯文本 + 分类计数（限速/超时/解析/网络/无题/其他）。

            同时把「来源 → 最近错误明文 + 时间」写入 state.source_errors，
            供 failed_sources() 返回每条失败来源的错误详情明细。"""
            errors.append(f"{source}：{msg}")
            cat = _classify_error(msg)
            error_stats[cat] = error_stats.get(cat, 0) + 1
            try:
                src_errors = state.setdefault("source_errors", {})
                src_errors[str(source)[:200]] = {
                    "msg": str(msg)[:300],
                    "ts": _snapshot_iso(),
                    "cat": cat,
                }
            except Exception:  # noqa: BLE001
                pass

        def _add(teacher_id: str, questions: list[dict],
                 source_type: str, source: str) -> int:
            nonlocal added_total, dup_total, invalid_total, offdomain_total, filtered_total
            if not questions:
                return 0
            remaining = _RUN_ADD_LIMIT - added_total
            if remaining <= 0:
                return 0
            chunk = questions[:remaining]
            # 需求4 · 公考相关性过滤：入库前剔除「非题目形态」伪题（保守启发式兜底）
            keep = []
            for q in chunk:
                q["source_type"] = source_type
                # #60：已由 _page_source 按"页面标题+第N题"标注的来源优先保留；否则用调用方默认
                q.setdefault("source", source)
                # 溯源：采集来源页链接写入 source_url，供「套卷回查/修复」按页定位题目
                q.setdefault("source_url", source if str(source).startswith(("http://", "https://")) else "")
                ok, reason = _gongkao_real_question_shape(q)
                if ok:
                    keep.append(q)
                else:
                    offdomain_total += 1
                    # 按拒绝原因聚合（跨轮累计），便于管理端定位劣质伪题来源
                    try:
                        _rd = state.setdefault("offdomain_reasons", {})
                        _rd[reason] = int(_rd.get(reason, 0)) + 1
                    except Exception:  # noqa: BLE001
                        pass
            if not keep:
                return 0
            for q in keep:
                # 自动打标：category_id 空则按知识树叶子节点名最长子串匹配关联
                if node_index and not (q.get("category_id") or "").strip():
                    q["category_id"] = _associate_category_id(q, node_index)
            # 采集质量增强2 · AI 自评抽检：低概率抽样评分来源健康度（异常静默）
            _quality_check(state, source, keep)
            # #10 入库敏感词守卫：采集题一律过 check_sensitive（题干/答案/解析），
            # 命中整题拒绝入库，计数进 filtered（不入 added/skipped/invalid）
            r = st.add_questions_batch(teacher_id, keep, sensitive_check=True)
            added_total += int(r.get("added", 0))
            dup_total += int(r.get("skipped", 0))
            invalid_total += int(r.get("invalid", 0))
            filtered_total += int(r.get("filtered", 0))
            # 同源整卷跳过：命中重复且来源一致则整卷不入库，计入累计漏斗
            _ft = state.setdefault("funnel_total", {})
            _ft["dupe_paper"] = int(_ft.get("dupe_paper", 0)) + int(r.get("dupe_source", 0))
            return int(r.get("added", 0))

        # ---- 来源 1+2：试卷作业（整卷为单位串行：一张采完再采下一张，整卷采完才入库） ----
        # 与旧实现的关键差别：旧代码边爬边逐页提取、每页各自入库，一张卷被预算打断
        # 也照样入库半截并可能标 complete；现改为试卷作业队列——先把未完成的试卷采完
        # （跨轮断点续采），整卷片段全跑完才一次性入库。
        url_fp = state.setdefault("url_fp", {})
        # 套卷采集标记：每套题目链接 → {fp, status, added, n, ts}。
        # status: complete=全套采集完整（永久跳过，重采需先重置）；
        #         blocked=连续多轮无进展（自动轮跳过，手动 force 可重试）；
        #         incomplete=被预算/熔断打断，下轮续采（由 paper_jobs 游标驱动）。
        collected_sets = state.setdefault("collected_sets", {})
        known_urls = set(state.get("urls", []))  # 已处理过的采集源（新增源判定）
        failed_urls = state.setdefault("failed_urls", set())  # #12 失败 URL 记录
        # #12 一键补采：仅重跑失败 URL，跳过正常 URL 列表；列表按优先级稳定排序
        # saA：normal 轮在公开题库网址后自动追加白名单站的起始入口，做整站深采
        base_targets = (sorted(failed_urls) if retry_failed
                        else _url_list() + _whitelist_start_targets())
        url_targets = _ordered_url_targets(base_targets)
        print(f"[AC-DIAG] url_targets={url_targets}", flush=True)

        def _paper_acquire(key, j):
            """为作业取源文本与科目；取不到返回 (None, "")。"""
            md = j.get("meta") or {}
            if j.get("kind") == "doc":
                tid, dn = md.get("tid") or "", md.get("doc_name") or ""
                if not tid or not dn:
                    return None, ""
                try:
                    pv = admin_biz.preview_document(tid, dn, max_chars=_PAPER_TEXT_MAX)
                except Exception as e:  # noqa: BLE001
                    _record_error(f"文档 {key} 读取失败", str(e))
                    return None, ""
                if pv.get("truncated"):
                    # 整卷语义下宁可拒收也不能静默采半张（历史 bug：截断后照样标 complete）
                    _record_error(
                        f"文档 {key} 超出单卷文本上限",
                        f"全文 {pv.get('chars')} 字 > {_PAPER_TEXT_MAX} 字，请拆分为单卷后重传")
                    return None, ""
                t = pv.get("preview") or ""
                if len(t) < 20:
                    _record_error(f"文档 {key} 提取失败", "文档文本过短")
                    return None, ""
                try:
                    subj = (cfg.get_teacher(tid)).teacher_subject
                except Exception:  # noqa: BLE001
                    subj = ""
                return t, subj
            t = j.get("text") or ""
            return (t, "") if len(t) >= 20 else (None, "")

        def _paper_commit(buf, j):
            """整卷原子提交：本轮入库额度不足返回 None（下轮再提交，绝不截断丢题）。"""
            if not buf:
                return 0
            if added_total + len(buf) > _RUN_ADD_LIMIT:
                return None
            md = j.get("meta") or {}
            if j.get("kind") == "doc":
                tid = md.get("tid") or SHARED_TEACHER_ID
                n = _add(tid, buf, "文档库", md.get("doc_name") or "")
                detail["docs"]["processed"] += 1
                detail["docs"]["added"] += n
                return n
            page_url = md.get("url") or ""
            # #60：题号写入来源出处（如"2025年行测资料分析第120题"），题干不代题号
            qs = _page_source(page_url, md.get("title") or "", buf)
            n = _add(SHARED_TEACHER_ID, qs, "公开题库", page_url)
            detail["urls"]["added"] += n
            detail["funnel"]["added"] += n   # saD 入题
            return n

        def _paper_obs(res):
            """把作业结果计入观测（详情计数 + 任务日志）。"""
            s = str(res.get("status") or "")
            detail["papers"][s] = detail["papers"].get(s, 0) + 1
            if s == "done":
                _log_task("", "", "整卷采集完成", ok=True,
                          detail=f"{res.get('key', '')} {res.get('note', '')}")
            elif s in ("blocked", "stalled"):
                _log_task("", "", "整卷采集受阻", ok=False,
                          detail=f"{res.get('key', '')} {res.get('note', '')}")

        _pump_state = {"docs_done": False}
        _url_cur = {"site": 0, "pages": [], "i": 0, "rule": None}

        def _enqueue_docs() -> bool:
            """文档试卷入队：只认 doc_registry（不扫目录）。每轮读文件取材，不缓存正文。"""
            added = 0
            for tid in teacher_ids:
                if _round_should_stop(added_total) or _model_down():
                    break
                try:
                    metas = [m for m in reg.list_meta(tid) if m.get("enabled", 1)]
                except Exception as e:  # noqa: BLE001
                    _record_error(f"读取老师 {tid} 文档列表失败", str(e))
                    continue
                for m in metas:
                    key = f"{tid}|{m['doc_name']}"
                    if _should_skip_backoff(state, key, force):
                        detail["docs"]["backoff"] = detail["docs"].get("backoff", 0) + 1
                        continue
                    detail["docs"]["scanned"] += 1
                    j = _paper_jobs(state).get(key)
                    st = str((j or {}).get("status") or "")
                    if retry_failed and st not in ("pending", "active", "blocked") \
                            and _set_status(collected_sets, key) not in ("failed", "blocked"):
                        # #12 一键补采：只重跑失败/受阻的文档，不扰动正常文档
                        continue
                    if st == "done":
                        cur_u = (j.get("meta") or {}).get("updated_at")
                        if cur_u is None and j.get("migrated"):
                            # 迁移来的历史 complete 标记：以当前注册表状态为基线，不重采
                            j["meta"] = {"tid": tid, "doc_name": m["doc_name"],
                                         "updated_at": m.get("updated_at") or ""}
                            continue
                        # 仅当注册表 updated_at 变化（文档被重传/更新）才重采
                        if (m.get("updated_at") or "") == (cur_u or ""):
                            continue
                        j["status"] = "pending"
                        j["cursor"] = 0
                        j["total"] = 0
                        j["buf"] = []
                        j["stall"] = 0
                        j.pop("msg", None)
                    elif st == "blocked":
                        if not force:
                            continue
                        # force 轮：人工要求重试，把阻塞作业放回队列（否则仍是 blocked，
                        # _paper_next 只取 pending/active，等于放行了却没真重试）
                        j["status"] = "pending"
                        j["cursor"] = 0
                        j["total"] = 0
                        j["buf"] = []
                        j["stall"] = 0
                        j.pop("msg", None)
                    _paper_enqueue(state, key, "doc",
                                   {"tid": tid, "doc_name": m["doc_name"],
                                    "updated_at": m.get("updated_at") or ""})
                    added += 1
            return added > 0

        def _pump_url_next() -> bool:
            """网址试卷：按站点顺序爬取，把下一张合格详情页入队（一次一张，防 state 膨胀）。"""
            while not _round_should_stop(added_total) and not _model_down():
                if _url_cur["i"] < len(_url_cur["pages"]):
                    page_url, page_title, text = _url_cur["pages"][_url_cur["i"]]
                    _url_cur["i"] += 1
                    if len(text) < 40:
                        continue
                    # P3 内容门控：rule_detail 页放宽（防误杀申论/材料整卷），通用页严格拦截杂页
                    if not _worth_extract(text, _url_cur["rule"] is not None):
                        detail["urls"]["gated"] = detail["urls"].get("gated", 0) + 1
                        continue
                    # 问题4：资讯/备考软文页即便命中详情规则也可能被 LLM 改写成伪题
                    if _is_seo_info_page(text):
                        detail["urls"]["gated"] = detail["urls"].get("gated", 0) + 1
                        continue
                    fp = _text_fp(text)
                    j = _paper_jobs(state).get(page_url)
                    jst = str((j or {}).get("status") or "")
                    if jst == "done":
                        jfp = str((j or {}).get("fp") or "")
                        if jfp == fp or (not jfp and (j or {}).get("migrated")):
                            # 已采完整且内容未变。迁移来的历史标记未带指纹时，以本次
                            # 爬到的内容为基线（否则升级后 151 张旧卷会被当成新卷重采）
                            if not jfp:
                                j["fp"] = fp
                            detail["urls"]["sets_complete"] = \
                                detail["urls"].get("sets_complete", 0) + 1
                            continue
                    if jst == "blocked":
                        if not force:
                            detail["urls"]["sets_blocked"] = \
                                detail["urls"].get("sets_blocked", 0) + 1
                            continue
                        # force 轮：把阻塞作业放回队列，否则依旧是 blocked，不会被采
                        j["status"] = "pending"
                        j["cursor"] = 0
                        j["total"] = 0
                        j["buf"] = []
                        j["stall"] = 0
                        j.pop("msg", None)
                    # 失败页重试窗口（原 _set_should_skip 语义）：窗口内不重提，
                    # 防坏页每轮重烧 5 通道 × 60s 的 LLM 预算；force 轮放行。
                    if not force and not j:
                        _why = _set_should_skip(collected_sets, page_url, fp, False)
                        if _why in ("complete", "gated", "failed", "blocked"):
                            detail["urls"][f"sets_{_why}"] = \
                                detail["urls"].get(f"sets_{_why}", 0) + 1
                            continue
                    if j and j.get("fp") and j.get("fp") != fp:
                        # 内容变了：作废已缓存结果，重采这张
                        j["cursor"] = 0
                        j["total"] = 0
                        j["buf"] = []
                        j["stall"] = 0
                        j["status"] = "pending"
                    _paper_enqueue(state, page_url, "url",
                                   {"url": page_url, "title": page_title}, text=text)
                    detail["urls"]["fetched"] += 1
                    detail["funnel"]["detail"] += 1   # saD 真拉到可解析内容
                    return True
                if _url_cur["site"] >= len(url_targets):
                    return False
                url = url_targets[_url_cur["site"]]
                _url_cur["site"] += 1
                # 采集质量增强1 · 指数退避：自动轮次（含自动补采）在退避窗口内一律跳过
                if _should_skip_backoff(state, url, force):
                    detail["urls"]["backoff"] = detail["urls"].get("backoff", 0) + 1
                    _log_task("", "", "退避中跳过", ok=True,
                              detail=f"{url}（第 {_source_fail_count(state, url)} 次失败，尚未到重试窗口）")
                    continue
                detail["urls"]["attempted"] += 1
                detail["funnel"]["site"] += 1   # saD 站点候选
                is_new = url not in known_urls
                print(f"[AC-DIAG] processing url={url} is_new={is_new}", flush=True)
                _log_task("", "", "开始爬取站点", ok=True, detail=f"{url}（新源={is_new}）")
                try:
                    t0_crawl = time.time()
                    # saC 健康把关：低分来源按健康分折算页数额度，抑制劣质源（saE 逐站差异化优先）
                    pages = _crawl_site(url, max_pages=_per_site_pages(url))
                    print(f"[AC-DIAG] crawled {url} -> {len(pages)} pages in {round(time.time()-t0_crawl,1)}s", flush=True)
                    _log_task("", "", "整站爬取完成", ok=bool(pages),
                              detail=f"{url} → {len(pages)} 页, 耗时 {round(time.time()-t0_crawl,1)}s")
                    if not pages:
                        raise ValueError("整站爬取未获得有效页面")
                    _url_cur["pages"] = list(pages)
                    _url_cur["i"] = 0
                    _url_cur["rule"] = _site_rule(url)   # None=通用态；命中=URL 即确认为题目详情页
                    if is_new:
                        detail["urls"]["sites"] = detail["urls"].get("sites", 0) + 1
                    _state_add(state, "urls", url)
                    _state_discard_set(state, "failed_urls", url)  # 补采成功，移出失败队列
                    _mark_source_ok(state, url)   # 成功：退避计数复位
                except Exception as e:  # noqa: BLE001
                    _record_error(f"公开题库网址 {url} 处理失败", str(e))
                    _state_add_set(state, "failed_urls", url)  # #12 记录失败 URL 供补采
                    _mark_source_fail(state, url, str(e))      # 指数退避：失败次数+1
            return False

        def _pump_new_papers() -> bool:
            """发现新试卷：文档库廉价先行，网址爬取昂贵放后。队列未空时不会被调用。"""
            if _round_should_stop(added_total) or _model_down():
                return False
            if not _pump_state["docs_done"]:
                _pump_state["docs_done"] = True
                if _enqueue_docs():
                    return True
            return _pump_url_next()

        # 串行驱动：一张一张采到完整（或阻塞），队列清了才去发现新试卷。
        for _res in _paper_drive(state, acquire=_paper_acquire, commit_fn=_paper_commit,
                                 record_error=_record_error, collected=collected_sets,
                                 round_expired=_round_expired,
                                 pump=_pump_new_papers):
            _paper_obs(_res)

        # ---- 来源 2：已有文档库轮询 —— 已并入上方试卷作业（_enqueue_docs 入队 +
        # _paper_drive 串行采集），与网址试卷共用同一套游标/原子提交/阻塞策略：
        #   · 只认 doc_registry（不扫磁盘目录，故未登记的文档不会入队）
        #   · 单文档超过 _PAPER_TEXT_MAX 字直接拒收并报错，不再静默截断后标 complete
        #   · 文档更新（注册表 updated_at 变化）→ 自动重新排队重采

        # ---- 来源 3：AI 联网搜索（结果页同样增量重扫） ----
        queries = _search_queries()
        # 自主采集（零配置找题）：非补采且生效时，按游标从稳定词池取词追加探索。
        # 已显式配置的搜索词优先，自主词仅追加去重，游标跨轮次推进实现持续换词。
        if not retry_failed and _effective_autonomous():
            plan = _autonomous_plan(state, teacher_ids, _autonomous_queries_cap())
            configured = set(queries)
            for q in plan["queries"]:
                if q not in configured:
                    queries.append(q)
            state["autonomous_cursor"] = plan["next_cursor"]
            detail["search"]["autonomous"] = len(plan["queries"])
        search_fp = state.setdefault("search_fp", {})
        failed_queries = state.setdefault("failed_queries", set())  # #12 失败搜索词
        # #12 一键补采：仅重跑失败搜索词
        if retry_failed:
            queries = sorted(failed_queries)
        if queries and _search_available():
            for q in queries:
                if _round_should_stop(added_total):
                    break
                # 采集质量增强1：搜索词指数退避（自动轮次含补采一律跳过退避窗口内失败的搜索词）
                if _should_skip_backoff(state, f"q:{q}", force):
                    detail["search"]["backoff"] = detail["search"].get("backoff", 0) + 1
                    continue
                # 能力层接入（§五）：默认/builtin 直调 search.py 现契约，行为不变；
                # CAP_SEARCH_PROVIDER=mcp/auto+网关配置时走 MCP 搜索（失败自动回退 builtin）
                results, warning = _search_cap_web(q, max_results=5)
                if warning:
                    _record_error(f"搜索「{q}」失败", str(warning))
                    _state_add_set(state, "failed_queries", q)  # #12 记录失败搜索词
                    _mark_source_fail(state, f"q:{q}", str(warning))
                    continue
                _state_discard_set(state, "failed_queries", q)  # 补采成功移出
                _mark_source_ok(state, f"q:{q}")
                for item in results:
                    if _round_should_stop(added_total):
                        break
                    if _model_down():   # P2 模型集体熔断：提前停止搜索提取
                        break
                    u = (item.get("url") or "").strip()
                    if not u:
                        continue
                    detail["search"]["attempted"] += 1
                    detail["funnel"]["search"] += 1   # saD 搜索候选
                    # P4 降噪：百科/词典/资讯等低价值域名先行跳过（saA 白名单信任域豁免）
                    if not _search_url_allowed(u):
                        detail["search"]["blocked"] = detail["search"].get("blocked", 0) + 1
                        detail["funnel"]["qualify"] += 1   # 白名单以外的噪音域计入"被拦"
                        continue
                    text = ""
                    try:
                        text = _fetch_text(u)
                    except Exception:  # noqa: BLE001
                        text = (item.get("snippet") or "").strip()
                    if len(text) < 40:
                        _state_add(state, "search_urls", u)
                        continue
                    # P3 内容门控：抓回来的内容看不出题目结构则不调 LLM（saB 兜底放行长文本弱信号）
                    if not _looks_like_questions(text) and not _detail_candidate(text):
                        detail["search"]["gated"] = detail["search"].get("gated", 0) + 1
                        detail["funnel"]["qualify"] += 1
                        _state_add(state, "search_urls", u)
                        continue
                    # 问题4：搜索命中资讯/备考软文页（关键词密集、题目骨架稀少）先于 LLM 拦截，
                    # 防止 LLM 把"XX考试时间最新安排必看"类文章改写成伪题（如历史 ID2218）。
                    if _is_seo_info_page(text):
                        detail["search"]["gated"] = detail["search"].get("gated", 0) + 1
                        detail["funnel"]["qualify"] += 1
                        _state_add(state, "search_urls", u)
                        continue
                    detail["funnel"]["detail"] += 1   # saD 真抓到可解析内容
                    fp = _text_fp(text)
                    # 套卷标记（需求1统一口径）：搜索结果页同样标记，完整/失败窗口内
                    # 不再重复提取（历史问题：坏页每轮重烧 5 通道×60s 超时）
                    _ss = _set_should_skip(collected_sets, u, fp, force)
                    if _ss:
                        detail["search"][f"sets_{_ss}"] = \
                            detail["search"].get(f"sets_{_ss}", 0) + 1
                        _state_add(state, "search_urls", u)
                        continue
                    if not force and search_fp.get(u) == fp:
                        continue
                    try:
                        r, att = _extract_enhanced(text, "")
                        detail["funnel"]["parse"] += 1   # saD 进 LLM 抽取
                        _s3 = _add(SHARED_TEACHER_ID, r.get("questions") or [], "互联网搜索", u)
                        detail["search"]["added"] += _s3
                        detail["funnel"]["added"] += _s3   # saD 入题
                        if att:
                            _set_fail(collected_sets, u, "; ".join(att)[:160])
                        else:
                            _set_mark(collected_sets, u, fp, r, _s3)
                            search_fp[u] = fp
                        _state_add(state, "search_urls", u)
                    except Exception as e:  # noqa: BLE001
                        _record_error(f"搜索结果 {u} 提取失败", str(e))
                        _set_fail(collected_sets, u, str(e))
                        _state_add(state, "search_urls", u)
        else:
            detail["search"]["note"] = "未配置搜索词或搜索源未启用"

        # saD：跨轮累计命中漏斗（供前端长期看趋势；本轮见 detail["funnel"]）
        ft = state.setdefault("funnel_total", {})
        for _k, _v in detail.get("funnel", {}).items():
            ft[_k] = ft.get(_k, 0) + int(_v)
        _save_state(state)
        result = {"skipped": False, "added": added_total,
                  "resources": res, **detail, "errors": errors,
                  "error_stats": error_stats,
                  "round_budget": {   # 轮次墙钟预算可观测（到点提前收尾的原因）
                      "budget_secs": _round_budget_secs(),
                      "elapsed_secs": round(_round_elapsed(), 1),
                      "expired": _round_expired()},
                  "llm_usage": _llm_usage_snapshot(),   # 本轮 LLM 字数/token/成本/预算墙
                  "filtered": {"duplicates": dup_total, "invalid": invalid_total,
                               "sensitive": filtered_total,
                               "offdomain": offdomain_total,
                               # 需求4：本轮「非题目形态」伪题拒绝原因分布
                               "offdomain_reasons": dict(
                                   state.get("offdomain_reasons") or {})}}
        # 采集质量增强3：按配置外发结果摘要 / 连续失败告警（更新连续失败计数）
        _notify_result(state, result)
        _save_state(state)   # 持久化 consecutive_failures 等新增状态
        with _lock:
            _status["last_run"] = _now_iso()
            _status["last_result"] = result
        _history_append(result)
        return result
    finally:
        _round_started_at = 0.0   # 轮次结束：墙钟计时复位，避免泄漏到下一轮
        with _lock:
            _status["running"] = False


# ============================================================
# 异步触发（P0：/run、/retry-failed 改为后台执行，避免同步长任务
# 被 nginx 600s 读超时掐断 / 拿不到结果。调用方立即拿到 started，
# 再轮询 status()（running → last_result）获取最终结果。）
# ============================================================

_manual_lock = threading.Lock()


def trigger_run(retry_failed: bool = False) -> dict:
    """异步触发一轮手动采集：立即返回，真实执行放后台线程。

    retry_failed=True（#12 一键补采）：仅重跑上一轮失败来源。
    若已有一轮在运行则返回 started=False 与原因（并发锁保护）。

    #53 修复：不再在触发端预先 _status["running"]=True 占位——
    run_once 自己入口会检查并占用 running。若触发端先占位，run_once
    一进来就判定"上一轮尚未结束"而直接 skip，导致采集"触发即空跑"。
    并发防重入由 run_once 内的 running 检查保证（_manual_lock 仅保护触发竞态）。"""
    with _manual_lock:
        threading.Thread(target=_run_manual_once,
                         args=(retry_failed,), name="ac-manual",
                         daemon=True).start()
        return {"started": True}


def _run_manual_once(retry_failed: bool) -> None:
    """后台执行一轮手动采集；异常兜底写入状态，保证 running 复位、结果可见。"""
    import traceback
    try:
        print("[AC-DIAG] _run_manual_once start", flush=True)
        run_once(force=True, retry_failed=retry_failed)
        print("[AC-DIAG] _run_manual_once done", flush=True)
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        print("[AC-DIAG] _run_manual_once EXC:", repr(e), "\n", tb, flush=True)
        with _lock:
            _status["running"] = False
            _status["last_run"] = _now_iso()
            _status["last_result"] = {
                "skipped": False, "added": 0, "errors": [f"手动采集异常：{e}"]}


def _config_summary() -> dict:
    """管理端只读配置摘要（不回显 API_KEY）。"""
    chs = load_llm_channels()
    ch = chs[0] if chs else {}
    overrides = _model_overrides()
    if ch.get("base_url") and ch.get("model"):
        channel = "ac"
        host = (ch.get("base_url") or "").rstrip("/")
    elif get("AUTOCOLLECT_MODEL", ""):
        channel = "env"
        host = (get("AUTOCOLLECT_BASE_URL", "") or "").rstrip("/")
    else:
        channel = "platform"
        host = ""
    return {
        "interval_secs": _interval_secs(),
        "cpu_max": _thresholds()[0],
        "mem_max": _thresholds()[1],
        "max_per_cycle": _max_per_cycle(),
        "threads": _threads(),
        "urls": _url_list(),
        "teachers": _teacher_ids(),
        "search_queries": _search_queries(),
        "fallback_models": _fallback_models(),
        "autonomous": _autonomous(),
        "autonomous_effective": _effective_autonomous(),
        "autonomous_queries": _autonomous_queries_cap(),
        "schedule_enabled": _schedule_window()[0],
        "schedule_start": _schedule_window()[1],
        "schedule_end": _schedule_window()[2],
        "retry_failed_auto": _retry_failed_auto(),
        "model": overrides.get("model") or "（默认模型）",
        "custom_model": bool(overrides.get("model")),
        "llm_channels": llm_channels_info(),
        "llm_channel": channel,
        "llm_host": host,
    }


def status() -> dict:
    with _lock:
        st = dict(_status)
    st["enabled"] = _enabled()
    st["config"] = _config_summary()
    st["failed_sources"] = failed_sources()
    try:  # saD：跨轮累计命中漏斗（供前端长期趋势）
        st["funnel_total"] = _load_state().get("funnel_total", {})
    except Exception:  # noqa: BLE001
        st["funnel_total"] = {}
    try:  # 需求4：跨轮累计「非题目形态」伪题拒绝原因分布（供管理端定位劣质来源）
        st["offdomain_reasons"] = _load_state().get("offdomain_reasons", {})
    except Exception:  # noqa: BLE001
        st["offdomain_reasons"] = {}
    try:  # 套卷采集标记汇总（complete/incomplete/累计入库）
        _cs = (_load_state().get("collected_sets") or {})
        st["collected_sets"] = {"total": len(_cs), **_sets_summary(_cs)}
    except Exception:  # noqa: BLE001
        st["collected_sets"] = {}
    try:  # 试卷作业队列摘要（当前在采卷 / 待采 / 阻塞），整卷串行采集的主视图
        _pj = paper_jobs_admin()
        st["papers"] = {"summary": _pj.get("summary") or {},
                        "current": _pj.get("current") or ""}
    except Exception:  # noqa: BLE001
        st["papers"] = {}
    return st


def _quality_summary(state: dict) -> dict:
    """来源健康分汇总：{source: {last_score, checks, low_ratio}}。"""
    out = {}
    try:
        for k, bucket in (state.get("quality") or {}).items():
            recs = bucket if isinstance(bucket, list) else []
            if not recs:
                continue
            last = recs[-1]
            low = sum(1 for r in recs
                      if r.get("has_error") or int(r.get("score", 100)) < 60)
            out[str(k)] = {
                "last_score": int(last.get("score", 100)),
                "checks": len(recs),
                "low_ratio": round(low / len(recs), 2) if recs else 0,
                "last_issue": (last.get("issues") or [])[:2],
            }
    except Exception:  # noqa: BLE001
        pass
    return out


def failed_sources() -> dict:
    """#12 可观测：上一轮失败的采集来源（URL/文档/搜索词），供管理端展示与一键补采。

    附加 errors_detail（来源 → {msg, ts, cat}）、backoff（指数退避剩余）、
    quality（AI 自评来源健康分）。"""
    state = _load_state()
    docs_failed = state.get("docs_failed", {})
    # 试卷作业被阻塞（连续多轮无进展）也算失败来源：旧的 docs_failed/失败窗口
    # 已由作业阻塞机制接管，此处并入，保证管理端「失败来源」视图不丢信息。
    try:
        _blocked = [r.get("key") for r in (paper_jobs_admin(state).get("blocked") or [])
                    if r.get("key")]
    except Exception:  # noqa: BLE001
        _blocked = []
    docs_all = sorted({k for k in list(docs_failed.keys()) + _blocked if k})
    src_errors = state.get("source_errors", {}) or {}
    now = time.time()
    backoff = []
    for key, cur in (state.get("fail_meta") or {}).items():
        fails = int(cur.get("fails", 0))
        if fails <= 0:
            continue
        win = _backoff_window(fails)
        remain = win - (now - float(cur.get("ts", 0)))
        backoff.append({"key": key, "fails": fails,
                        "window_secs": int(win),
                        "remaining_secs": max(0, int(remain)),
                        "msg": cur.get("msg", "")[:120]})
    backoff.sort(key=lambda x: x["remaining_secs"])
    return {
        "urls": sorted(state.get("failed_urls", [])),
        "queries": sorted(state.get("failed_queries", [])),
        "docs": docs_all,
        "count": len(state.get("failed_urls", []))
                 + len(state.get("failed_queries", []))
                 + len(docs_all),
        "blocked_papers": _blocked,   # 整卷采集连续无进展被阻塞的试卷（人工 reset 可重试）
        "errors_detail": {k: v for k, v in src_errors.items()},
        "backoff": backoff,               # 采集质量增强1：指数退避剩余
        "quality": _quality_summary(state),  # 采集质量增强2：来源健康分
        "consecutive_failures": int(state.get("consecutive_failures", 0)),
    }


_daemon_th: threading.Thread | None = None


def _daemon_loop() -> None:
    """守护线程主循环：永不退出（问题3 自愈）。

    外层 try 兜住「调度判断/补采判断」等调度语句自身的异常（历史上这些语句在
    try 之外，一旦抛错线程直接退出，表现为『突然停止』）；内层只负责单轮采集。
    finally 保证每轮后必然按周期休眠，绝不因异常跳过休眠导致 CPU 空转或线程退出。"""
    import traceback
    time.sleep(min(_interval_secs(), 60))
    while True:
        try:
            try:
                if _enabled() and _in_schedule_window():
                    run_once()
                    # 调度增强：每轮常规采集后，对上一轮失败来源自动补采一次
                    if _retry_failed_auto():
                        fs = failed_sources()
                        if fs["count"]:
                            run_once(force=False, retry_failed=True)
            except Exception as e:  # noqa: BLE001  单轮异常不影响线程存活
                with _lock:
                    _status["last_result"] = {"skipped": False, "added": 0,
                                              "errors": [f"自动采集异常：{e}"]}
                    _status["last_run"] = _now_iso()
        except Exception as e:  # noqa: BLE001  最外层兜底：守护线程永不因任何异常退出
            print(f"[autocollect] daemon outer exception: {e!r}\n"
                  + traceback.format_exc(), flush=True)
        finally:
            time.sleep(_interval_secs())


def ensure_daemon() -> None:
    """按运行时 enabled 保证守护线程存活（启动时 / 配置热更新后均可调用）。"""
    global _daemon_th
    if not _enabled():
        with _lock:
            _status["enabled"] = False
        return
    with _lock:
        _status["enabled"] = True
    if _daemon_th is not None and _daemon_th.is_alive():
        return
    _daemon_th = threading.Thread(target=_daemon_loop, name="autocollect",
                                  daemon=True)
    _daemon_th.start()


def start() -> None:
    """挂到 FastAPI startup：启用后启动后台守候线程。"""
    ensure_daemon()