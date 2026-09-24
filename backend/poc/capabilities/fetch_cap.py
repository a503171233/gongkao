# -*- coding: utf-8 -*-
"""fetch 能力（§五表：fetch(url) -> ({"title","text"}, warning)）。

builtin   = autocollect 现有抓取逻辑（httpx + _html_to_text 粗提正文）；
mcp       = MCP 工具（MCP_TOOL_FETCH，带 JS 渲染/正文抽取更稳）；
scrapling = Scrapling Fetcher（curl_cffi 后端 TLS 指纹模拟，反基础反爬；
            仅装 scrapling[fetchers]、不装浏览器，StealthyFetcher/DynamicFetcher
            浏览器系档位在 2C 服务器不可承受，明确不用）。
采集四层防线不变：能力层只负责"把网页变成文本"，门控在 autocollect 层2。
scrapling 未安装时本 provider 不注册，auto 链自动退化为 mcp→builtin（零行为变化）。
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
    final_url = str(r.url)  # 重定向后的最终地址（_crawl_site 链接归一化用）
    if "html" in ctype:
        return {"title": _extract_title(html), "text": _html_to_text(html),
                "html": html, "final_url": final_url}, ""
    return {"title": "", "text": html.strip(), "html": "", "final_url": final_url}, ""


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


def _scrapling(url: str, timeout: int = 20) -> tuple[dict | None, str]:
    """Scrapling Fetcher（curl_cffi 后端）：Chrome TLS 指纹模拟 + 自动
    stealthy_headers（真实浏览器头），对 TLS/JA3 指纹类基础反爬的增强通道。
    纪律：
      - 只用静态档 Fetcher，不触碰浏览器系 fetcher（StealthyFetcher/DynamicFetcher
        需 chromium 二进制，2C/3.6G 服务器不可承受）；
      - 失败归一 warning（不抛），由 registry 继续降级下一 provider；
      - 清洗复用 _html_to_text（与 builtin 同一正文口径，层2 门控语义不变）；
      - retries=1：curl_cffi 单次内部重试；来源级退避在 autocollect 层统一管。
    """
    try:
        from scrapling.fetchers import Fetcher  # 延迟 import：未安装则不注册本 provider
    except Exception as e:  # noqa: BLE001
        return None, f"scrapling 未安装（{type(e).__name__}）"
    try:
        page = Fetcher.get(url, timeout=int(timeout), impersonate="chrome",
                           follow_redirects=True, retries=1)
        if page.status != 200:
            return None, f"scrapling 抓取失败（HTTP {page.status}）"
        html = page.html_content or ""
        ctype = ""
        try:
            ctype = (page.headers.get("content-type") or "").lower()
        except Exception:  # noqa: BLE001  headers 缺失不影响正文产出
            pass
    except Exception as e:  # noqa: BLE001  网络失败归一 warning（不抛）
        return None, f"scrapling 抓取失败（{type(e).__name__}: {str(e)[:120]}）"
    from ..autocollect import _html_to_text  # 延迟 import（防循环，与 _builtin 一致）
    final_url = ""
    try:
        final_url = str(getattr(page, "url", "") or "")
    except Exception:  # noqa: BLE001
        pass
    if html and ("html" in ctype or not ctype):
        return {"title": _extract_title(html), "text": _html_to_text(html),
                "html": html, "final_url": final_url}, ""
    return {"title": "", "text": html.strip(), "html": "", "final_url": final_url}, ""


registry.register("fetch", "builtin", lambda url, timeout=20: _builtin(url, timeout))
registry.register("fetch", "mcp", lambda url, timeout=20: _mcp(url, timeout))
try:  # scrapling 可导入才注册（未安装环境 fetch 链与历史完全一致）
    import scrapling  # noqa: F401
    registry.register("fetch", "scrapling",
                      lambda url, timeout=20: _scrapling(url, timeout))
except Exception:  # noqa: BLE001  未安装 scrapling：不注册，auto 链自动退化
    pass
