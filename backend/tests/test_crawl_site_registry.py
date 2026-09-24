# -*- coding: utf-8 -*-
"""批次B：_crawl_site 接入能力层 fetch（scrapling 反爬增强全局生效）。

运行：cd backend && python tests/test_crawl_site_registry.py
覆盖（零网络，patch registry.call）：
  1) registry 路径生效：列表页发现链接 → 详情页产出（title 用契约值、text 已清洗）；
  2) 全链失败（warning）→ 跳过该页不中断整站；
  3) 非 HTML 纯文本：无规则时保留原文；
  4) final_url 缺失回退请求 URL；final_url 存在时用重定向后地址；
  5) 部分页失败部分成功：成功页正常产出。
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk_crawl_reg_")
os.environ["DATA_DIR"] = str(Path(TMP) / "data")
os.environ["DB_PATH"] = str(Path(TMP) / "vector")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["CHAT_DB"] = str(Path(TMP) / "chat.db")

ok_count = 0
fail_count = 0


def check(name, cond, detail=""):
    global ok_count, fail_count
    if cond:
        print(f"  ✅ {name}")
        ok_count += 1
    else:
        print(f"  ❌ {name}" + (f"  | {detail}" if detail else ""))
        fail_count += 1


import poc.autocollect as ac  # noqa: E402
from poc.capabilities import registry as cap_registry  # noqa: E402


def _patch_registry(res_by_url, fail_urls=frozenset()):
    """patch registry.call：按 URL 返回假 fetch 结果；fail_urls 模拟全链失败。"""
    def _call(cap, **kw):
        if cap != "fetch":
            return None, f"unexpected cap {cap}"
        url = kw.get("url", "")
        if url in fail_urls:
            return None, "scrapling 抓取失败（HTTP 403）"
        r = res_by_url.get(url)
        if r is None:
            return None, "no page"
        return dict(r), ""
    return mock.patch.object(cap_registry, "call", _call)


# ==================== 1) registry 路径：列表页→详情页 ====================
RES = {
    "https://generic.test/": {
        "title": "公考列表页", "final_url": "https://generic.test/",
        "html": '<html><body><a href="/p/1">第1套</a> <a href="/p/2">第2套</a>'
                '<a href="https://other.test/x">外链</a></body></html>',
        "text": "公考列表页"},
    "https://generic.test/p/1": {
        "title": "真题一", "final_url": "https://generic.test/p/1",
        "html": "<html><body><h1>真题一</h1><p>1、下列说法正确的是（　）。</p>"
                "<p>A. 甲</p><p>答案：A</p></body></html>", "text": "真题一"},
    "https://generic.test/p/2": {
        "title": "真题二", "final_url": "https://generic.test/p/2",
        "html": "<html><body><p>2、第二题题干……答案：B</p></body></html>", "text": "真题二"},
}
with _patch_registry(RES):
    pages = ac._crawl_site("https://generic.test/", max_pages=10, timeout=5)
got = {p[0]: p for p in pages}
check("列表页+两详情页全部产出", len(pages) == 3 and set(got) == set(RES), [p[0] for p in pages])
check("title 用契约值", got.get("https://generic.test/p/1", ("", "", ""))[1] == "真题一")
check("text 已清洗（无标签）", "下列说法正确的是" in got.get("https://generic.test/p/1", ("", "", ""))[2]
      and "<p>" not in got.get("https://generic.test/p/1", ("", "", ""))[2])
check("外链不入队（同源限制）", "https://other.test/x" not in got)

# ==================== 2) 全链失败 → 跳过不中断 ====================
with _patch_registry(RES, fail_urls={"https://generic.test/p/1"}):
    pages = ac._crawl_site("https://generic.test/", max_pages=10, timeout=5)
check("失败页跳过，其余照常", sorted(p[0] for p in pages)
      == ["https://generic.test/", "https://generic.test/p/2"], [p[0] for p in pages])

with _patch_registry(RES, fail_urls={"https://generic.test/", "https://generic.test/p/1",
                                     "https://generic.test/p/2"}):
    pages = ac._crawl_site("https://generic.test/", max_pages=10, timeout=5)
check("整站全败返回空列表", pages == [], pages)

# ==================== 3) 非 HTML 纯文本 ====================
RES_TXT = {
    "https://txt.test/a.txt": {
        "title": "", "final_url": "https://txt.test/a.txt", "html": "",
        "text": "1、纯文本题目……答案：A"},
}
with _patch_registry(RES_TXT):
    pages = ac._crawl_site("https://txt.test/a.txt", max_pages=5, timeout=5)
check("非 HTML 无规则保留原文", pages == [("https://txt.test/a.txt", "", "1、纯文本题目……答案：A")], pages)

# ==================== 4) final_url 处理 ====================
RES_NOFU = {
    "https://nf.test/": {"title": "无final_url", "html": "<html><body>正文</body></html>", "text": "正文"},
    "https://rd.test/": {"title": "重定向页", "final_url": "https://rd.test/real.html",
                         "html": "<html><body>重定向后正文</body></html>", "text": "x"},
}
with _patch_registry(RES_NOFU):
    pages = ac._crawl_site("https://nf.test/", max_pages=5, timeout=5)
check("final_url 缺失回退请求 URL", pages and pages[0][0] == "https://nf.test/", pages)

with _patch_registry(RES_NOFU):
    pages = ac._crawl_site("https://rd.test/", max_pages=5, timeout=5)
check("final_url 存在用重定向后地址", pages and pages[0][0] == "https://rd.test/real.html", pages)

# ==================== 5) 与契约解耦：_fetch_text 语义不变 ====================
with mock.patch.object(cap_registry, "call",
                       lambda cap, **kw: ({"title": "t", "text": "正文内容", "html": ""}, "")):
    check("_fetch_text 仍走能力层取 text", ac._fetch_text("https://x.test/a") == "正文内容")

print(f"\n总计: 通过 {ok_count} · 失败 {fail_count}")
sys.exit(0 if fail_count == 0 else 1)
