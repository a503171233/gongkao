# -*- coding: utf-8 -*-
"""能力层 provider 注册表（说明书 §5.0）。

每个 capability 一个注册表；provider 统一返回 (result, warning)，失败不抛异常。
  - resolve(cap): 读 CAP_{CAP}_PROVIDER（auto|builtin|mcp|off），默认 auto；
  - call(cap, **kwargs): 按解析结果调度。auto = 有 MCP 配置先用 MCP，
    MCP 失败记录原因并回退 builtin；无配置直接 builtin。
  - 失败链纪律（对齐 search.py）：任何失败归一为 warning 字符串，绝不静默吞错。
  - 指标：cap_calls_total{cap,provider,result} + 平均时长（prom_lines 供 /metrics）。

P0 注册：search / fetch / docparse。vision/asr/rerank 在 P1/P2 接入
（未注册的能力 call() 返回明确 warning，调用方按现状降级）。
"""
from __future__ import annotations

import threading
import time
from collections import Counter
from typing import Any, Callable

from ..config import get


class _CapMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: Counter = Counter()          # (cap, provider, result) -> n
        self._ms: dict[tuple, list] = {}          # (cap, provider) -> [ms...]

    def record(self, cap: str, provider: str, result: str, ms: float) -> None:
        with self._lock:
            self._calls[(cap, provider, result)] += 1
            lst = self._ms.setdefault((cap, provider), [])
            lst.append(round(ms))
            if len(lst) > 500:
                del lst[:len(lst) - 500]

    def snapshot(self) -> tuple[dict, dict]:
        with self._lock:
            calls = {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in self._calls.items()}
            lat = {f"{k[0]}|{k[1]}": round(sum(v) / len(v)) for k, v in self._ms.items() if v}
            return calls, lat


metrics = _CapMetrics()

# cap -> {"builtin": fn, "mcp": fn}；fn(**kwargs) -> (result, warning)
_PROVIDERS: dict[str, dict[str, Callable]] = {}


def register(cap: str, provider: str, fn: Callable) -> None:
    _PROVIDERS.setdefault(cap, {})[provider] = fn


# 各能力默认 provider 策略（§5.1 表；docparse=mcp 含 OCR 风险高，默认 builtin）
_DEFAULT_MODE = {"search": "auto", "fetch": "auto", "docparse": "builtin"}


def resolve(cap: str) -> str:
    """CAP_{CAP}_PROVIDER：auto（默认，见 §5.1 表）| builtin | mcp | off。"""
    return (get(f"CAP_{cap.upper()}_PROVIDER", _DEFAULT_MODE.get(cap, "auto"))
            or "auto").strip().lower()


def has_mcp_config() -> bool:
    """MCP 网关是否配置（空=MCP provider 全部不可用）。"""
    from ..mcp_client import gateway_configured
    return gateway_configured()


def call(cap: str, **kwargs) -> tuple[Any, str]:
    """调度一个能力调用。返回 (result, warning)；result=None 且 warning 非空 = 失败。"""
    mode = resolve(cap)
    provs = _PROVIDERS.get(cap)
    if provs is None:
        return None, f"能力未注册: {cap}（调用方按现状降级）"
    if mode == "off":
        return None, f"能力 {cap} 已显式禁用（CAP_{cap.upper()}_PROVIDER=off）"

    order: list[str] = []
    if mode == "builtin":
        order = ["builtin"]
    elif mode == "mcp":
        order = ["mcp"]                    # 显式 mcp：不回退（失败 warning 透出）
    else:  # auto
        order = (["mcp", "builtin"] if has_mcp_config() and "mcp" in provs else ["builtin"])

    last_warn = ""
    for p in order:
        fn = provs.get(p)
        if fn is None:
            last_warn = f"能力 {cap} 缺少 {p} provider"
            continue
        t0 = time.monotonic()
        try:
            result, warning = fn(**kwargs)
        except Exception as e:  # noqa: BLE001  provider 内异常归一 warning，不外抛
            result, warning = None, f"{p} 异常（{type(e).__name__}: {str(e)[:120]}）"
        ms = (time.monotonic() - t0) * 1000
        if warning:
            metrics.record(cap, p, "fail", ms)
            last_warn = f"[{p}] {warning}"
            continue                        # auto 模式下继续回退下一个 provider
        metrics.record(cap, p, "ok", ms)
        return result, ""
    return None, last_warn or f"能力 {cap} 全部 provider 失败"


def status() -> dict:
    """各能力当前模式与 provider 就绪面（供管理端只读接口）。"""
    out = {}
    for cap, provs in _PROVIDERS.items():
        mcp_ready = "mcp" in provs and has_mcp_config()
        out[cap] = {"mode": resolve(cap), "builtin": "builtin" in provs,
                    "mcp": mcp_ready}
    return out


def prom_lines() -> list[str]:
    calls, lat = metrics.snapshot()
    lines = [
        "# HELP gk_cap_calls_total 能力调用计数（能力/provider/结果）",
        "# TYPE gk_cap_calls_total counter",
    ]
    for key, v in sorted(calls.items()):
        cap, prov, res = key.split("|")
        lines.append(f'gk_cap_calls_total{{cap="{cap}",provider="{prov}",result="{res}"}} {v}')
    lines += [
        "# HELP gk_cap_latency_ms 能力平均耗时ms",
        "# TYPE gk_cap_latency_ms gauge",
    ]
    for key, v in sorted(lat.items()):
        cap, prov = key.split("|")
        lines.append(f'gk_cap_latency_ms{{cap="{cap}",provider="{prov}"}} {v}')
    return lines
