# -*- coding: utf-8 -*-
"""联网搜索解析与降级 · 单元测试（批次2 测试加固）
运行: cd backend && python tests/test_search.py
覆盖:
  - _parse_bing_html 纯函数：正常页解析（title/url/snippet）/ 空页抛 RuntimeError
  - web_search 降级契约：禁用/空关键词/异常全部归一为 ([], warning) 不抛错
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-search-test-")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["EMBED_PROVIDER"] = "selftest"

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


from poc.search import _parse_bing_html, web_search  # noqa: E402

# 固定必应结果页样本（结构对齐 cn.bing.com 的 <li class="b_algo">）
BING_HTML = """
<ol id="b_results">
  <li class="b_algo"><h2><a href="https://example.com/a" h="ID=SERP,1.1">行测<b>言语理解</b>技巧</a></h2>
    <p class="b_lineclamp">转折关系是<strong>言语理解</strong>高频考点，口诀是……</p></li>
  <li class="b_algo"><h2><a href="https://example.com/b">&amp;逻辑判断&amp;入门</a></h2>
    <p>真假推理题三步走。</p></li>
  <li class="b_algo"><div>无 h2 链接的杂项应被跳过</div></li>
  <li class="b_algo"><h2><a href="javascript:void(0)">非 http 链接应被跳过</a></h2></li>
</ol>
"""

# 1) 正常解析
rs = _parse_bing_html(BING_HTML, max_results=6)
check("解析出 2 条有效结果 (实得 %d)" % len(rs), len(rs) == 2, str(rs))
check("title 去标签+实体解码", rs[0]["title"] == "行测言语理解技巧", repr(rs[0]["title"]))
check("url 保留", rs[0]["url"] == "https://example.com/a", rs[0]["url"])
check("snippet 含正文且去标签", "转折关系" in rs[1]["snippet"] or "真假推理" in rs[1]["snippet"],
      repr([r["snippet"] for r in rs]))
check("非 http 链接被跳过", all(r["url"].startswith("http") for r in rs))

# 2) max_results 截断
rs2 = _parse_bing_html(BING_HTML, max_results=1)
check("max_results=1 截断", len(rs2) == 1, str(len(rs2)))

# 3) 空页/结构变化 → RuntimeError（调用方归一 warning，绝不静默）
try:
    _parse_bing_html("<html><body>被反爬的验证页</body></html>")
    check("空结果页抛 RuntimeError", False)
except RuntimeError as e:
    check("空结果页抛 RuntimeError", "0 条" in str(e), str(e))

# 4) web_search 降级契约（不联网：用 provider=none / 空关键词路径 + 异常路径）
os.environ["SEARCH_PROVIDER"] = "none"
res, warn = web_search("任何问题")
check("provider=none: ([], warning)", res == [] and bool(warn), f"{res} {warn}")

os.environ["SEARCH_PROVIDER"] = "bing"
res, warn = web_search("   ")
check("空关键词: ([], warning)", res == [] and "关键词为空" in warn, f"{res} {warn}")

# 异常路径：必应解析对空页抛 RuntimeError → web_search 归一 ([], warning)
import poc.search as s  # noqa: E402
orig = s._search_bing
s._search_bing = lambda q, n: (_ for _ in ()).throw(RuntimeError("模拟反爬"))
try:
    res, warn = web_search("正常关键词")
    check("搜索源异常: 归一 ([], warning) 不抛错", res == [] and "模拟反爬" in warn, f"{res} {warn}")
finally:
    s._search_bing = orig

print()
print("PASS" if ok else "FAIL", "- test_search")
sys.exit(0 if ok else 1)
