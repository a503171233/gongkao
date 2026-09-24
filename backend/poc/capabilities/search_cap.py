# -*- coding: utf-8 -*-
"""search 能力（§五表：web_search(query, max_results) -> (results, warning)）。

builtin = 现有 poc/search.py（其内部再按 SEARCH_PROVIDER 分 bocha/bing，
既有契约与行为原样保留，不改动——能力层只是包它）；
mcp     = MCP 网关工具（MCP_TOOL_SEARCH 绑定，tools/list 可探测）。
"""
from __future__ import annotations

from typing import Any

from . import registry
from .. import mcp_client


def _builtin(query: str, max_results: int = 6) -> tuple[list[dict], str]:
    from ..search import web_search   # 稳定契约，内部已归一 warning
    return web_search(query, max_results)


def _mcp(query: str, max_results: int = 6) -> tuple[list[dict], str]:
    tool = mcp_client.tool_bound("MCP_TOOL_SEARCH")
    if not tool:
        return [], "MCP 搜索工具未绑定（MCP_TOOL_SEARCH 为空）"
    res, warn = mcp_client.call_tool(tool, {"query": query, "count": int(max_results)})
    if warn:
        return [], warn
    return _normalize_mcp_results(res), ""


def _normalize_mcp_results(res: Any) -> list[dict]:
    """MCP 搜索结果归一：兼容 {"results":[...]} / [...] / {"items":[...]}。
    每项缺 title/snippet 时给空串（对齐 search.py 输出形状）。"""
    items = res
    if isinstance(res, dict):
        items = res.get("results") or res.get("items") or res.get("webPages") or []
    out: list[dict] = []
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            url = str(it.get("url") or "").strip()[:500]
            title = str(it.get("title") or "").strip()[:120]
            if not url.startswith("http"):
                continue
            out.append({"title": title, "url": url,
                        "snippet": str(it.get("snippet") or it.get("summary") or "")[:300]})
    return out[:10]


registry.register("search", "builtin", lambda query, max_results=6: _builtin(query, max_results))
registry.register("search", "mcp", lambda query, max_results=6: _mcp(query, max_results))
