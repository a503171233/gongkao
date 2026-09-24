# -*- coding: utf-8 -*-
"""批次A：Scrapling fetch provider + auto 降级链动态化。

运行：cd backend && python tests/test_fetch_scrapling.py
覆盖（零网络，mock scrapling.fetchers.Fetcher 与假 provider）：
  1) _scrapling 契约：200 HTML → ({"title","text","html"}, "")；非 200 /
     Fetcher.get 异常 / scrapling 未安装 → (None, warning)；
  2) auto 链顺序：mcp(有配置)→scrapling→builtin；无 mcp 配置直接 scrapling；
     scrapling 失败降级 builtin；
  3) 显式 CAP_FETCH_PROVIDER=scrapling：只用 scrapling 不回退；
  4) status() 就绪面含 scrapling 键；auto 链对未注册环自动跳过。
"""
import os
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk_scrap_cap_")
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


from poc.capabilities import registry, fetch_cap  # noqa: E402

# ==================== 1) _scrapling 契约 ====================
_HTML = "<html><head><title>行测真题页</title></head><body><p>下列说法正确的是……</p></body></html>"
_page = SimpleNamespace(status=200, html_content=_HTML,
                        headers={"content-type": "text/html; charset=utf-8"})

with mock.patch("scrapling.fetchers.Fetcher") as fake_f:
    fake_f.get.return_value = _page
    res, warn = fetch_cap._scrapling("https://x.test/p/1", timeout=9)
check("200 HTML 契约", res == {"title": "行测真题页", "text": mock.ANY, "html": _HTML,
                              "final_url": mock.ANY}
      and "下列说法正确的是" in (res or {}).get("text", "") and warn == "", (res, warn))
check("参数透传", fake_f.get.call_args.kwargs.get("timeout") == 9
      and fake_f.get.call_args.kwargs.get("impersonate") == "chrome"
      and fake_f.get.call_args.kwargs.get("follow_redirects") is True)

with mock.patch("scrapling.fetchers.Fetcher") as fake_f:
    fake_f.get.return_value = SimpleNamespace(status=403, html_content="",
                                              headers={"content-type": "text/html"})
    res, warn = fetch_cap._scrapling("https://x.test/blocked")
check("非 200 归一 warning", res is None and "HTTP 403" in warn, warn)

with mock.patch("scrapling.fetchers.Fetcher") as fake_f:
    fake_f.get.side_effect = RuntimeError("TLS handshake timeout")
    res, warn = fetch_cap._scrapling("https://x.test/down")
check("网络异常归一 warning 不抛", res is None and "RuntimeError" in warn
      and "TLS handshake" in warn, warn)

# scrapling 未安装（import 失败）
with mock.patch.dict(sys.modules, {"scrapling": None, "scrapling.fetchers": None}):
    res, warn = fetch_cap._scrapling("https://x.test/p/1")
check("未安装归一 warning", res is None and "未安装" in warn, warn)

# 非 HTML 内容（.txt 纯文本）直通不清洗
_txt = SimpleNamespace(status=200, html_content="1、下列说法正确的是…… 答案：A",
                       headers={"content-type": "text/plain; charset=utf-8"})
with mock.patch("scrapling.fetchers.Fetcher") as fake_f:
    fake_f.get.return_value = _txt
    res, warn = fetch_cap._scrapling("https://x.test/a.txt")
check("纯文本直通", res is not None and res["text"].startswith("1、") and res["html"] == ""
      and warn == "", (res, warn))

# ==================== 2) auto 链顺序 ====================
_calls = []


def _mk(tag, ok):
    def _fn(**kw):
        _calls.append(tag)
        return ("R-" + tag, "") if ok else (None, f"{tag} failed")
    return _fn


os.environ["CAP_FETCH_PROVIDER"] = "auto"
_orig_fetch_provs = dict(registry._PROVIDERS.get("fetch", {}))  # 测试后恢复


def _use_fetch_provs(provs: dict) -> None:
    registry._PROVIDERS["fetch"] = provs


_provs_full = {"mcp": _mk("mcp", False), "scrapling": _mk("scrapling", True),
               "builtin": _mk("builtin", True)}

_use_fetch_provs(_provs_full)
with mock.patch.object(registry, "has_mcp_config", return_value=True):
    _calls.clear()
    res, warn = registry.call("fetch", url="u")
    check("auto: mcp 败→scrapling 接管", res == "R-scrapling" and _calls == ["mcp", "scrapling"], (res, _calls))

with mock.patch.object(registry, "has_mcp_config", return_value=False):
    _calls.clear()
    res, warn = registry.call("fetch", url="u")
    check("auto: 无 mcp 配置跳过", res == "R-scrapling" and _calls == ["scrapling"], (res, _calls))

_use_fetch_provs({"mcp": _mk("mcp", False), "scrapling": _mk("scrapling", False),
                  "builtin": _mk("builtin", True)})
with mock.patch.object(registry, "has_mcp_config", return_value=True):
    _calls.clear()
    res, warn = registry.call("fetch", url="u")
    check("auto: scrapling 败→builtin 兜底", res == "R-builtin"
          and _calls == ["mcp", "scrapling", "builtin"], (res, _calls, warn))

# 未注册 scrapling 的环境（历史行为）：auto 链跳过该环
_use_fetch_provs({"builtin": _mk("builtin", True)})
with mock.patch.object(registry, "has_mcp_config", return_value=False):
    _calls.clear()
    res, warn = registry.call("fetch", url="u")
    check("auto: 未装 scrapling 链退化不变", res == "R-builtin" and _calls == ["builtin"], (res, _calls))

# ==================== 3) 显式 scrapling 模式 ====================
os.environ["CAP_FETCH_PROVIDER"] = "scrapling"
_use_fetch_provs({"scrapling": _mk("scrapling", False), "builtin": _mk("builtin", True)})
with mock.patch.object(registry, "has_mcp_config", return_value=True):
    _calls.clear()
    res, warn = registry.call("fetch", url="u")
    check("显式 scrapling 不回退", res is None and _calls == ["scrapling"]
          and "scrapling failed" in warn, (res, _calls, warn))
os.environ["CAP_FETCH_PROVIDER"] = "auto"
_use_fetch_provs(_orig_fetch_provs)  # 恢复真实 provider 表

# ==================== 4) status() 与真实注册面 ====================
st = registry.status()
check("status 含 scrapling 就绪面", "fetch" in st and "scrapling" in st["fetch"], st.get("fetch"))
check("容器内 scrapling 已注册为 fetch provider", st["fetch"]["scrapling"] is True)

# 真实 _scrapling 注册链存在（未 mock 场景：scrapling 已安装 → provider 已注册）
check("_scrapling 可调用", callable(registry._PROVIDERS["fetch"].get("scrapling")))

print(f"\n总计: 通过 {ok_count} · 失败 {fail_count}")
sys.exit(0 if fail_count == 0 else 1)
