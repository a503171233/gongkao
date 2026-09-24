# -*- coding: utf-8 -*-
"""fetch 能力（§五表：fetch(url) -> ({"title","text"}, warning)）。

builtin = autocollect 现有抓取逻辑（httpx + _html_to_text 粗提正文）；
mcp     = MCP 工具（MCP_TOOL_FETCH，带 JS 渲染/正文抽取更稳）。
采集四层防线不变：能力层只负责"把网页变成文本"，门控在 autocollect 层2。
"""
from __future__ import annotations

import re

from . import registry
from .. import mcp_client


def _extract_title(html: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    return re.sub(r"\s+", " ", m.group(1)).strip()[:120] if m else ""


def _builtin(url: str, timeout: int = 20) -> tuple[dict | None, str]:
    """对齐 autocollect._fetch_text 原行为：httpx+UA+重定向；正文经
    _decode_resp_text 按响应真实编码解码（老牌中文站 gbk 嗅探，勿改）；
    额外返回 title 与解码后 html（供上层可选使用）。"""
    import httpx
    from ..autocollect import _UA, _html_to_text, _decode_resp_text

    try:
        with httpx.Client(timeout=timeout, headers={"User-Agent": _UA},
                          follow_redirects=True) as c:
            r = c.get(url)
            r.raise_for_status()
        html = _decode_resp_text(r)
    except Exception as e:  # noqa: BLE001  抓取失败归一 warning（不抛）
        return None, f"抓取失败（{type(e).__name__}: {str(e)[:120]}）"
    ctype = (r.headers.get("content-type") or "").lower()
    if "html" in ctype:
        return {"title": _extract_title(html), "text": _html_to_text(html),
                "html": html}, ""
    return {"title": "", "text": html.strip(), "html": ""}, ""


def _mcp(url: str, timeout: int = 20) -> tuple[dict | None, str]:
    tool = mcp_client.tool_bound("MCP_TOOL_FETCH")
    if not tool:
        return None, "MCP 抓取工具未绑定（MCP_TOOL_FETCH 为空）"
    res, warn = mcp_client.call_tool(tool, {"url": url, "timeout": int(timeout)})
    if warn:
        return None, warn
    if isinstance(res, dict):
        text = str(res.get("text") or res.get("content") or res.get("markdown") or "")
        title = str(res.get("title") or "")
    else:
        text, title = str(res or ""), ""
    if not text.strip():
        return None, "MCP fetch 返回空正文"
    return {"title": title[:120], "text": text, "html": ""}, ""


registry.register("fetch", "builtin", lambda url, timeout=20: _builtin(url, timeout))
registry.register("fetch", "mcp", lambda url, timeout=20: _mcp(url, timeout))
