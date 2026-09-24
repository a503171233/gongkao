# -*- coding: utf-8 -*-
"""MCP 轻量客户端（说明书 §六，P0 采用 HTTP 直连 JSON-RPC，零新依赖）。

传输：MCP Streamable HTTP —— httpx POST JSON-RPC（initialize → tools/call）。
纪律（对齐 llm.py / search.py）：
  - 连接/超时/5xx：重试 MCP_MAX_RETRIES（默认 2）次，线性退避；
  - 4xx/鉴权类不重试；
  - 所有失败归一为 warning 字符串返回，绝不向能力层/调用方抛异常；
  - MCP 非强依赖：网关未配置（MCP_GATEWAY_URL 空）时 gateway_configured()=False，
    能力层 auto 模式直接走 builtin。

约定：
  - 一次 initialize 后缓存会话 id（Mcp-Session-Id 头，服务端要求时携带）；
  - tools/call 参数一律服务端构造（§六 安全：绝不拼用户输入之外的字段）；
  - 结果取 result.content 中首个 text 项（JSON 字符串由调用方解析）。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx

from .config import get


def gateway_configured() -> bool:
    return bool(get("MCP_GATEWAY_URL", ""))


def _gateway_url() -> str:
    return get("MCP_GATEWAY_URL", "").rstrip("/")


def _api_key() -> str:
    return get("MCP_API_KEY", "")


def _timeout_s() -> float:
    try:
        return max(1.0, float(get("MCP_TIMEOUT_SECONDS", "60") or "60"))
    except ValueError:
        return 60.0


def _max_retries() -> int:
    try:
        return max(0, int(get("MCP_MAX_RETRIES", "2") or "2"))
    except ValueError:
        return 2


_lock = threading.Lock()
_session_id: str | None = None
_initialized = False
_id_counter = 0


def _next_id() -> int:
    global _id_counter
    with _lock:
        _id_counter += 1
        return _id_counter


def _headers() -> dict:
    h = {"Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    key = _api_key()
    if key:
        h["Authorization"] = f"Bearer {key}"
    if _session_id:
        h["Mcp-Session-Id"] = _session_id
    return h


def _parse_sse_or_json(resp: httpx.Response) -> dict:
    """兼容两种应答：纯 JSON 或 SSE（取最后一个 data: 帧）。"""
    ctype = (resp.headers.get("content-type") or "").lower()
    body = resp.text or ""
    if "text/event-stream" in ctype:
        payload = None
        for line in body.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk and chunk != "[DONE]":
                    try:
                        payload = json.loads(chunk)
                    except ValueError:
                        continue
        if payload is None:
            raise ValueError("SSE 应答中无合法 JSON data 帧")
        return payload
    return resp.json()


def _post(method: str, params: dict, timeout: float) -> dict:
    """单次 JSON-RPC 请求。返回 result 或抛 RuntimeError（带 error 内容）。"""
    body = {"jsonrpc": "2.0", "id": _next_id(), "method": method, "params": params}
    with httpx.Client(timeout=timeout) as c:
        r = c.post(_gateway_url(), json=body, headers=_headers())
    if r.status_code in (401, 403):
        raise PermissionError(f"MCP 鉴权失败 HTTP {r.status_code}")
    if 400 <= r.status_code < 500:
        raise ValueError(f"MCP 请求被拒 HTTP {r.status_code}: {r.text[:120]}")
    r.raise_for_status()
    sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
    if sid:
        global _session_id
        _session_id = sid
    doc = _parse_sse_or_json(r)
    if isinstance(doc, dict) and doc.get("error"):
        raise RuntimeError(f"MCP 返回错误: {json.dumps(doc['error'], ensure_ascii=False)[:180]}")
    return (doc or {}).get("result") or {}


def _ensure_init() -> None:
    """惰性 initialize（协议握手）。失败抛异常（由 call_tool 归一 warning）。"""
    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        res = _post("initialize", {
            "protocolVersion": "2025-03-26",
            "clientInfo": {"name": "gongkao-poc", "version": "1.0"},
            "capabilities": {},
        }, _timeout_s())
        if not isinstance(res, dict) or not res.get("protocolVersion"):
            raise RuntimeError("MCP initialize 应答缺少 protocolVersion（网关可能不是 MCP 服务）")
        _initialized = True


def reset_session() -> None:
    """网关重启后清缓存会话（测试/运维可用）。"""
    global _session_id, _initialized
    with _lock:
        _session_id = None
        _initialized = False


def _transient(e: Exception) -> bool:
    return isinstance(e, (httpx.TimeoutException, httpx.TransportError)) or \
        (isinstance(e, httpx.HTTPStatusError) and e.response.status_code >= 500)


def call_tool(tool: str, args: dict, timeout: float | None = None) -> tuple[Any, str]:
    """调用 MCP 工具。返回 (结果, warning)；失败不抛异常（§六 归一纪律）。
    结果 = result.content 首个 text 项解析后的 JSON（非 JSON 时原样字符串）；
    isError 的 content 也解析进 warning。"""
    if not gateway_configured():
        return None, "MCP 网关未配置（MCP_GATEWAY_URL 为空）"
    if not tool:
        return None, "MCP 工具名未配置"
    t0 = time.monotonic()
    to = timeout or _timeout_s()
    last_err = ""
    for attempt in range(_max_retries() + 1):
        try:
            _ensure_init()
            res = _post("tools/call", {"name": tool, "arguments": args}, to)
            if res.get("isError"):
                txt = _content_text(res)
                return None, f"MCP 工具 {tool} 执行失败: {txt[:180] or '无错误详情'}"
            txt = _content_text(res)
            if txt is None:
                return None, f"MCP 工具 {tool} 应答无 text 内容（结构可能不符预期）"
            try:
                return json.loads(txt), ""
            except ValueError:
                return txt, ""     # 纯文本工具结果（如 fetch 正文）
        except (PermissionError, ValueError) as e:
            return None, f"MCP 不可重试错误: {str(e)[:180]}"   # 鉴权/4xx：立即失败
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {str(e)[:140]}"
            if not _transient(e) or attempt >= _max_retries():
                break
            time.sleep(min(0.5 * (attempt + 1), 2.0))          # 线性退避（对齐 llm.py）
    _ = t0
    return None, f"MCP tools/call {tool} 失败（{last_err}）"


def _content_text(res: dict) -> str | None:
    for item in res.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text" and "text" in item:
            return str(item["text"])
    return None


def list_tools() -> tuple[list[dict], str]:
    """tools/list：探测校验工具绑定是否存在（配置页/自检用）。"""
    if not gateway_configured():
        return [], "MCP 网关未配置（MCP_GATEWAY_URL 为空）"
    try:
        _ensure_init()
        res = _post("tools/list", {}, _timeout_s())
        return (res.get("tools") or []), ""
    except Exception as e:  # noqa: BLE001
        return [], f"MCP tools/list 失败（{type(e).__name__}: {str(e)[:140]}）"


def tool_bound(env_name: str) -> str:
    """读 MCP_TOOL_* 配置（去空白）。"""
    return get(env_name, "").strip()
