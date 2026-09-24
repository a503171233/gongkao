# -*- coding: utf-8 -*-
"""监控指标（零外部依赖，标准库实现）。说明书 §10.1 全链路追踪/§10.2 指标。
线程安全：Metrics 实例进程内共享，FastAPI 中间件/端点并发读写。

用法：
    from .metrics import metrics
    # 中间件：metrics.record(method, path, status, latency_ms=..., teacher_id=...)
    # ask 端点：metrics.ask_started(tid) / metrics.ask_failed()
    # 端点：metrics.snapshot()  → JSON；metrics.prometheus_text() → Prom 文本

SLO（C5 · 性能/SLO）：
    - P95 时延：对最近 N 条耗时排序取 95 分位（阈值 SLO_P95_MS 默认 30000ms，可环境变量覆盖）
    - 可用性：非 5xx 请求占比（阈值 SLO_AVAILABILITY 默认 99.0%，即错误率 ≤1%）
    - snapshot 输出 slo: {p95_ms, availability_pct, p95_ok, availability_ok, thresholds}
    - webhook 预留：配置 SLO_WEBHOOK_URL 后，slo_breach() 可推送告警（默认仅日志）
"""
import json
import os
import threading
import time
import urllib.request
from collections import Counter, deque

# ---------- SLO 阈值（环境变量可覆盖；默认值与项目实测匹配） ----------
SLO_P95_MS = float(os.environ.get("SLO_P95_MS", "30000"))          # ask P95 ≤ 30s（LLM 最慢 121s，P95 放宽）
SLO_AVAILABILITY = float(os.environ.get("SLO_AVAILABILITY", "99.0"))  # 非 5xx 占比 ≥ 99%
SLO_WEBHOOK_URL = os.environ.get("SLO_WEBHOOK_URL", "").strip()    # 可选：告警推送地址（空=仅日志）


class Metrics:
    def __init__(self, max_recent: int = 200, max_latencies: int = 500):
        self._lock = threading.Lock()
        self._start = time.time()
        self._requests: Counter = Counter()      # (method, path) -> count
        self._status: Counter = Counter()        # str(status) -> count
        self._by_teacher: Counter = Counter()    # teacher_id -> ask 次数
        self._latencies: list[float] = []        # 最近 N 条耗时(ms)
        self._max_latencies = max_latencies
        self._ask_total = 0
        self._ask_fail = 0
        self._token_prompt = 0          # 累计输入 token（A6 token 核算）
        self._token_completion = 0      # 累计输出 token（A6 token 核算）
        self._recent: deque = deque(maxlen=max_recent)
        self._slo_breach_total = 0       # 累计 SLO 违约事件（边沿触发：OK→BREACH 才 +1）
        self._slo_prev_ok = True         # 上次 SLO 状态（初始视为 OK，避免启动即计数）
        # C3 LLM 被动可用度窗口（最近 20 次 ask 结果，无需额外探活调用）
        self._ask_window: deque = deque(maxlen=20)
        self._ask_last_ok_at = 0.0
        self._ask_last_fail_at = 0.0
        self._ask_last_error = ""

    # ---------- 记录 ----------
    def record(self, method: str, path: str, status: int,
               teacher_id: str | None = None, latency_ms: float | None = None) -> None:
        with self._lock:
            self._requests[(method, path)] += 1
            self._status[str(status)] += 1
            if teacher_id:
                self._by_teacher[teacher_id] += 1
            if latency_ms is not None:
                self._latencies.append(latency_ms)
                if len(self._latencies) > self._max_latencies:
                    self._latencies = self._latencies[-self._max_latencies:]
            self._recent.append({
                "t": time.strftime("%H:%M:%S"),
                "method": method, "path": path, "status": status,
                "teacher_id": teacher_id or "", "ms": round(latency_ms or 0),
            })

    def ask_started(self, teacher_id: str) -> None:
        with self._lock:
            self._ask_total += 1
            self._by_teacher[teacher_id] += 1

    def ask_succeeded(self) -> None:
        """ask 端点整体成功：入可用度窗口。"""
        with self._lock:
            self._ask_window.append(("ok", time.time()))
            self._ask_last_ok_at = time.time()

    def ask_failed(self, err: str = "") -> None:
        with self._lock:
            self._ask_fail += 1
            self._ask_window.append(("fail", time.time()))
            self._ask_last_fail_at = time.time()
            if err:
                self._ask_last_error = str(err)[:200]

    def llm_health(self) -> dict:
        """LLM 可用度（C3）：基于最近 ask 窗口判定 ok/degraded/down/unknown。
        判定规则：最近 5 次内 ≥3 次失败 → down；窗口成功率 <80% → degraded；
        窗口为空 → unknown。纯被动统计，不增加探测调用。"""
        with self._lock:
            wins = list(self._ask_window)
            total = len(wins)
            fails = sum(1 for k, _ in wins if k == "fail")
            recent = wins[-5:]
            recent_fails = sum(1 for k, _ in recent if k == "fail") if recent else 0
            if total == 0:
                status, pct = "unknown", 100.0
            else:
                pct = round((total - fails) / total * 100, 1)
                if recent_fails >= 3:
                    status = "down"
                elif pct < 80:
                    status = "degraded"
                else:
                    status = "ok"
            return {
                "status": status,
                "window_total": total,
                "window_fails": fails,
                "success_rate": pct,
                "recent_fails": recent_fails,
                "last_ok_at": self._ask_last_ok_at or None,
                "last_fail_at": self._ask_last_fail_at or None,
                "last_error": self._ask_last_error or "",
                "ask_total": self._ask_total,
                "ask_fail": self._ask_fail,
            }

    def record_tokens(self, prompt: int = 0, completion: int = 0) -> None:
        """A6 token 核算：累计输入/输出 token（由 llm 调用层上报）。"""
        with self._lock:
            self._token_prompt += max(0, int(prompt or 0))
            self._token_completion += max(0, int(completion or 0))

    # ---------- token 核算（A6） ----------
    def token_usage(self) -> dict:
        """累计输入/输出 token 快照（线程安全）。"""
        with self._lock:
            return {
                "prompt_tokens": self._token_prompt,
                "completion_tokens": self._token_completion,
                "total_tokens": self._token_prompt + self._token_completion,
            }

    # ---------- SLO 派生指标（C5） ----------
    def _p95_unlocked(self) -> float:
        """最近 N 条耗时排序取 95 分位（调用方须已持锁/单线程场景）。"""
        if not self._latencies:
            return 0.0
        s = sorted(self._latencies)
        idx = max(0, min(len(s) - 1, int(len(s) * 0.95) - 1))
        return round(s[idx], 1)

    def _avail_unlocked(self) -> float:
        """非 5xx 请求占比（调用方须已持锁/单线程场景）。"""
        total = sum(self._status.values())
        if total == 0:
            return 100.0
        err5xx = sum(n for code, n in self._status.items() if code.startswith("5"))
        return round((total - err5xx) / total * 100, 3)

    def p95_latency_ms(self) -> float:
        """最近 N 条耗时排序取 95 分位；无数据返回 0。"""
        with self._lock:
            return self._p95_unlocked()

    def availability_pct(self) -> float:
        """非 5xx 请求占比（进程内累计）。无请求返回 100.0。"""
        with self._lock:
            return self._avail_unlocked()

    def _slo_check_unlocked(self) -> dict:
        """SLO 达标检查核心（调用方须已持锁/单线程场景）。"""
        p95 = self._p95_unlocked()
        avail = self._avail_unlocked()
        p95_ok = p95 <= SLO_P95_MS
        avail_ok = avail >= SLO_AVAILABILITY
        breached = not (p95_ok and avail_ok)
        if breached and self._slo_prev_ok:
            self._slo_breach_total += 1
        self._slo_prev_ok = not breached
        return {
            "p95_ms": p95,
            "availability_pct": avail,
            "p95_ok": p95_ok,
            "availability_ok": avail_ok,
            "breached": breached,
            "breach_total": self._slo_breach_total,
            "thresholds": {"p95_ms": SLO_P95_MS, "availability_pct": SLO_AVAILABILITY},
        }

    def slo_check(self) -> dict:
        """SLO 达标检查（线程安全入口）：P95/可用性/违约计数（边沿触发）。"""
        with self._lock:
            return self._slo_check_unlocked()

    def slo_breach_notify(self, check: dict) -> None:
        """SLO 违约推送（webhook 预留）：配置 SLO_WEBHOOK_URL 才外发；默认仅记录日志。"""
        if not check.get("breached"):
            return
        msg = (f"[SLO BREACH] p95={check['p95_ms']}ms(阈值{SLO_P95_MS}) "
               f"avail={check['availability_pct']}%(阈值{SLO_AVAILABILITY})")
        print(msg)
        if SLO_WEBHOOK_URL:
            try:
                req = urllib.request.Request(
                    SLO_WEBHOOK_URL,
                    data=json.dumps({"text": msg, "event": "slo_breach"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception as e:  # noqa: BLE001
                print(f"[SLO] webhook 推送失败: {e}")

    # ---------- 输出 ----------
    def snapshot(self) -> dict:
        with self._lock:
            avg = (sum(self._latencies) / len(self._latencies)) if self._latencies else 0.0
            reqs = [{"method": m, "path": p, "count": c}
                    for (m, p), c in sorted(self._requests.items())]
            return {
                "uptime_seconds": int(time.time() - self._start),
                "requests": reqs,
                "status": dict(self._status),
                "by_teacher": dict(self._by_teacher),
                "ask": {"total": self._ask_total, "fail": self._ask_fail},
                "tokens": {
                    "prompt_tokens": self._token_prompt,
                    "completion_tokens": self._token_completion,
                    "total_tokens": self._token_prompt + self._token_completion,
                },
                "avg_latency_ms": round(avg, 1),
                "p95_latency_ms": self._p95_unlocked(),
                "slo": self._slo_check_unlocked(),
                "recent": list(self._recent)[-50:],
            }

    def prometheus_text(self) -> str:
        """Prometheus 文本格式（供 /metrics/prometheus 与外部抓取）。"""
        s = self.snapshot()
        lines = [
            "# HELP gk_uptime_seconds 服务运行时长",
            "# TYPE gk_uptime_seconds gauge",
            f"gk_uptime_seconds {s['uptime_seconds']}",
            "# HELP gk_http_requests_total HTTP 请求计数",
            "# TYPE gk_http_requests_total counter",
        ]
        for r in s["requests"]:
            lines.append(
                f'gk_http_requests_total{{method="{r["method"]}",path="{r["path"]}"}} {r["count"]}')
        for code, n in s["status"].items():
            lines.append(f'gk_http_status_total{{code="{code}"}} {n}')
        lines += [
            "# HELP gk_ask_total 问答请求数",
            "# TYPE gk_ask_total counter",
            f"gk_ask_total {s['ask']['total']}",
            "# HELP gk_ask_fail_total 问答失败数",
            "# TYPE gk_ask_fail_total counter",
            f"gk_ask_fail_total {s['ask']['fail']}",
            "# HELP gk_token_prompt_total 累计输入 token",
            "# TYPE gk_token_prompt_total counter",
            f"gk_token_prompt_total {s['tokens']['prompt_tokens']}",
            "# HELP gk_token_completion_total 累计输出 token",
            "# TYPE gk_token_completion_total counter",
            f"gk_token_completion_total {s['tokens']['completion_tokens']}",
            "# HELP gk_token_total 累计 token（输入+输出）",
            "# TYPE gk_token_total counter",
            f"gk_token_total {s['tokens']['total_tokens']}",
            "# HELP gk_avg_latency_ms 平均耗时ms",
            "# TYPE gk_avg_latency_ms gauge",
            f"gk_avg_latency_ms {s['avg_latency_ms']}",
            "# HELP gk_p95_latency_ms P95 耗时ms（SLO）",
            "# TYPE gk_p95_latency_ms gauge",
            f"gk_p95_latency_ms {s['p95_latency_ms']}",
            "# HELP gk_availability_pct 可用性%（非5xx占比，SLO）",
            "# TYPE gk_availability_pct gauge",
            f"gk_availability_pct {s['slo']['availability_pct']}",
            "# HELP gk_slo_breach_total SLO 违约累计",
            "# TYPE gk_slo_breach_total counter",
            f"gk_slo_breach_total {s['slo']['breach_total']}",
        ]
        # 运行时 AI 技能层/能力层指标（§四/§五；模块不可用时跳过，不阻断 /metrics）
        for mod_name in ("poc.skills.base", "poc.capabilities.registry"):
            try:
                import importlib
                mod = importlib.import_module(mod_name)
                lines.extend(mod.prom_lines())
            except Exception:  # noqa: BLE001
                pass
        return "\n".join(lines) + "\n"


# 进程级单例
metrics = Metrics()
