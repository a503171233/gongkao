# -*- coding: utf-8 -*-
"""联网搜索模块（#24 R3）：为「AI 联网搜索完善知识体系」提供资料检索能力。

搜索源由 SEARCH_PROVIDER 环境变量切换（config.get 读取）：
  - bocha: 博查 AI 搜索 API（需 SEARCH_API_KEY，国内稳定，推荐生产使用）
  - bing:  必应中国页解析（无需任何密钥，默认值，国内服务器可直连）
  - none:  关闭联网搜索（知识建树降级为纯 LLM 模式，调用方需给出明确提示）

稳定契约（admin.py 编排层只依赖这两个函数，不感知具体搜索源）：
    search_available() -> bool
    web_search(query, max_results) -> (results, warning)
        results:  list[{"title", "url", "snippet"}]
        warning: 失败原因（成功为空串）。搜索失败不抛异常——知识建树流程
                  允许降级（基于 LLM 已有知识继续），由 warning 透传给前端。
容错纪律（对齐 #24 R1 的明确报错要求）：
    - 任何失败（网络超时/反爬/密钥缺失/页面结构变化）都归一为 warning 字符串，
      带「异常类型: 具体原因」，绝不静默吞错。
"""
import re
from html import unescape

from .config import get

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _provider() -> str:
    return (get("SEARCH_PROVIDER", "bing") or "").strip().lower()


def search_available() -> bool:
    """联网搜索是否启用（SEARCH_PROVIDER=none 时 False，前端据此隐藏/禁用入口）。"""
    return _provider() not in ("", "none")


def web_search(query: str, max_results: int = 6) -> tuple[list[dict], str]:
    """执行搜索。返回 (结果列表, 失败原因)。

    成功: (非空列表, "")
    禁用/空关键词/异常: ([], 具体原因)
    """
    prov = _provider()
    if prov in ("", "none"):
        return [], "联网搜索未启用（SEARCH_PROVIDER=none）"
    q = (query or "").strip()
    if not q:
        return [], "搜索关键词为空"
    n = max(1, min(int(max_results or 6), 10))
    try:
        if prov == "bocha":
            return _search_bocha(q, n), ""
        return _search_bing(q, n), ""
    except Exception as e:  # noqa: BLE001  搜索失败不阻断知识建树（允许降级）
        return [], f"联网搜索失败（{type(e).__name__}: {e}）"


# ---------- 搜索源 1：博查 AI（https://open.bochaai.com，国内合规搜索 API） ----------
def _search_bocha(query: str, max_results: int) -> list[dict]:
    import httpx
    key = (get("SEARCH_API_KEY", "") or "").strip()
    if not key:
        raise RuntimeError("已选 bocha 搜索源但 SEARCH_API_KEY 未配置")
    with httpx.Client(timeout=15) as c:
        r = c.post(
            "https://api.bochaai.com/v1/web-search",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"query": query, "count": max_results, "summary": True},
        )
        r.raise_for_status()
    data = (r.json() or {}).get("data") or {}
    pages = ((data.get("webPages") or {}).get("value")) or []
    out: list[dict] = []
    for p in pages:
        t = _strip_html(p.get("name", ""))
        s = _strip_html(p.get("summary", "") or p.get("snippet", "") or "")
        u = (p.get("url") or "").strip()
        if t and u.startswith("http"):
            out.append({"title": t[:120], "url": u[:500], "snippet": s[:300]})
        if len(out) >= max_results:
            break
    if not out:
        raise RuntimeError("bocha 返回 0 条有效结果（检查密钥额度/关键词）")
    return out


# ---------- 搜索源 2：必应中国页解析（免密钥，默认） ----------
_BING_ITEM = re.compile(r'<li class="b_algo".*?</li>', re.S)
_BING_LINK = re.compile(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_BING_P = re.compile(r'<p[^>]*>(.*?)</p>', re.S)


def _search_bing(query: str, max_results: int) -> list[dict]:
    import httpx
    with httpx.Client(timeout=15, headers=_HEADERS, follow_redirects=True) as c:
        r = c.get("https://cn.bing.com/search",
                  params={"q": query, "count": max_results * 2, "setlang": "zh-hans"})
        r.raise_for_status()
    out: list[dict] = []
    for m in _BING_ITEM.finditer(r.text):
        frag = m.group(0)
        a = _BING_LINK.search(frag)
        if not a:
            continue
        url = unescape(a.group(1)).strip()
        title = _strip_html(a.group(2))
        ps = _BING_P.search(frag)
        snippet = _strip_html(ps.group(1)) if ps else ""
        if title and url.startswith("http"):
            out.append({"title": title[:120], "url": url[:500], "snippet": snippet[:300]})
        if len(out) >= max_results:
            break
    if not out:
        raise RuntimeError("必应返回 0 条可解析结果（页面结构可能变化或被反爬，"
                           "可配置 SEARCH_PROVIDER=bocha + SEARCH_API_KEY 切换为 API 搜索）")
    return out


def _strip_html(s: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", s or "")).strip()
