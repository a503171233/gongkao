# -*- coding: utf-8 -*-
"""P0-4 · 能力层注册表单测（说明书 §五 / §九 P0 验收）。
运行: cd backend && python tests/test_capabilities_registry.py
覆盖: resolve 默认策略 / auto 无网关直 builtin / mcp 失败回退 builtin /
      mcp 显式不回退 / off 显式禁用 / 失败归一 warning / provider 异常归一 /
      search & fetch builtin 直连现实现 / docparse builtin=ingest / 指标。
全离线 mock：不发真实网络（MCP 网关未配置；builtin 用 monkeypatch 注入假实现）。
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
_TMP = Path(tempfile.mkdtemp(prefix="gk-cap-test-"))
os.environ["CHAT_DB"] = str(_TMP / "chat.db")
os.environ["DB_PATH"] = str(_TMP / "vec")
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["MODEL_CACHE"] = str(_TMP / "data" / "models")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["STUDY_DB"] = str(_TMP / "study.db")
os.environ.pop("MCP_GATEWAY_URL", None)     # 默认无网关

from poc.capabilities import registry        # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(extra)[:200]) if (extra and not cond) else ""))
    if not cond:
        ok = False


# ---------- 注册面 ----------
st = registry.status()
for cap in ("search", "fetch", "docparse"):
    check(f"{cap} 已注册 builtin+mcp", st[cap]["builtin"] and st[cap]["mcp"] is False)
    # 无网关时 mcp_ready=False（status 里 mcp 表示「就绪」而非「已注册」）

# ---------- resolve 默认策略（§5.1 表） ----------
check("默认 search=auto", registry.resolve("search") == "auto")
check("默认 fetch=auto", registry.resolve("fetch") == "auto")
check("默认 docparse=builtin", registry.resolve("docparse") == "builtin")

# ---------- 注入替身 provider ----------
calls = []


def fake_builtin(**kw):
    calls.append("b")
    return {"data": "from-builtin", **kw}, ""


def fake_mcp_fail(**kw):
    calls.append("m-fail")
    return None, "MCP 超时（mock）"


def fake_mcp_ok(**kw):
    calls.append("m-ok")
    return {"data": "from-mcp", **kw}, ""


def fake_boom(**kw):
    calls.append("boom")
    raise ValueError("provider 内部炸了")


registry.register("__t__", "builtin", fake_builtin)
registry.register("__t__", "mcp", fake_mcp_fail)

# auto + 无网关：直接 builtin（不调 mcp）
os.environ.pop("CAP___T___PROVIDER", None)
res, warn = registry.call("__t__", x=1)
check("auto 无网关 → 直 builtin", res and res["data"] == "from-builtin" and not warn)

# auto + 有网关 + mcp 失败：回退 builtin（链：mcp→builtin）
os.environ["MCP_GATEWAY_URL"] = "https://mcp.invalid.test/"
registry.register("__t2__", "builtin", fake_builtin)
registry.register("__t2__", "mcp", fake_mcp_fail)
calls.clear()
res, warn = registry.call("__t2__", x=2)
check("auto 有网关 mcp 失败 → 回退 builtin",
      res and res["data"] == "from-builtin" and calls == ["m-fail", "b"])

# mcp 全链失败：warning 透出（含 mcp 原因），result=None
registry.register("__t3__", "builtin", fake_mcp_fail)
registry.register("__t3__", "mcp", fake_mcp_fail)
res, warn = registry.call("__t3__")
check("全 provider 失败 → warning 透出", res is None and "MCP 超时" in warn)

# 显式 mcp：不回退 builtin
registry.register("__t4__", "builtin", fake_builtin)
registry.register("__t4__", "mcp", fake_mcp_fail)
os.environ["CAP___T4___PROVIDER"] = "mcp"
res, warn = registry.call("__t4__")
check("mcp 显式失败不回退", res is None and "MCP 超时" in warn)

# mcp 成功：直接用 mcp 结果
registry.register("__t5__", "builtin", fake_builtin)
registry.register("__t5__", "mcp", fake_mcp_ok)
os.environ.pop("CAP___T5___PROVIDER", None)   # auto
calls.clear()
res, warn = registry.call("__t5__", x=5)
check("auto 有网关 mcp 成功 → 用 mcp", res and res["data"] == "from-mcp" and calls == ["m-ok"])

# off 显式禁用
os.environ["CAP___T5___PROVIDER"] = "off"
res, warn = registry.call("__t5__")
check("off 禁用 → warning", res is None and "禁用" in warn)

# provider 内部异常归一 warning，并回退
registry.register("__t6__", "builtin", fake_builtin)
registry.register("__t6__", "mcp", fake_boom)
os.environ.pop("CAP___T6___PROVIDER", None)
calls.clear()
res, warn = registry.call("__t6__")
check("异常 provider 归一后回退 builtin",
      res and res["data"] == "from-builtin" and calls == ["boom", "b"])

# 未注册能力：warning 提示调用方按现状降级
res, warn = registry.call("no-such-cap")
check("未注册能力归一 warning", res is None and "未注册" in warn)

# ---------- 真实 builtin 契约冒烟（不发网络） ----------
from poc.capabilities import search_cap, fetch_cap, docparse_cap  # noqa: E402,F401

# search builtin 即 search.web_search（stub 其内部源，验证包装层透传）
from poc import search as search_mod  # noqa: E402
_orig = search_mod.web_search
search_mod.web_search = lambda q, max_results=6: (
    [{"title": "T", "url": "https://a.test/1", "snippet": "s"}], "")
try:
    res, warn = search_cap._builtin("行测", 3)
    check("search builtin 透传现契约", res and res[0]["url"].startswith("https") and not warn)
finally:
    search_mod.web_search = _orig

# MCP 结果归一（dict/list 两种形状）
norm = search_cap._normalize_mcp_results(
    {"results": [{"title": "x", "url": "https://b.test/2", "snippet": "y"},
                 {"title": "bad", "url": "javascript:alert(1)"}]})
check("MCP 搜索结果归一+URL 安全过滤", len(norm) == 1 and norm[0]["url"] == "https://b.test/2")
norm2 = search_cap._normalize_mcp_results([{"title": "z", "url": "https://c.test/3"}])
check("MCP 结果裸数组兼容", len(norm2) == 1)

# fetch builtin：注入假 httpx 客户端不易，改为验证函数存在且契约正确（不发网络）
import inspect  # noqa: E402
sig = inspect.signature(fetch_cap._builtin)
check("fetch builtin 契约 (url, timeout)", list(sig.parameters) == ["url", "timeout"])

# docparse builtin：txt 文件走 ingest.extract_text（真函数，临时文件零网络）
p = _TMP / "t.txt"
p.write_text("第一章 总则\n第一条 ……", encoding="utf-8")
md, warn = docparse_cap._builtin(str(p))
check("docparse builtin 提取文本", "总则" in md and not warn)
md2, warn2 = docparse_cap._builtin(str(_TMP / "missing.pdf"))
check("docparse builtin 失败归一 warning", md2 == "" and bool(warn2))

# ---------- 指标 ----------
snap_c, snap_l = registry.metrics.snapshot()
check("cap 指标已计数", any("__t__" in k for k in snap_c))
lines = registry.prom_lines()
check("prom_lines 含 gk_cap_calls_total", any("gk_cap_calls_total" in x for x in lines))
check("prom_lines 含 provider 标签", any('provider="builtin"' in x for x in lines))

# ---------- mcp_client 网关未配置归一（零网络） ----------
from poc import mcp_client  # noqa: E402
os.environ.pop("MCP_GATEWAY_URL", None)
check("gateway_configured False", mcp_client.gateway_configured() is False)
res, warn = mcp_client.call_tool("any", {})
check("无网关 call_tool 归一 warning", res is None and "未配置" in warn)
res, warn = mcp_client.list_tools()
check("无网关 list_tools 归一 warning", warn != "")

print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
sys.exit(0 if ok else 1)
