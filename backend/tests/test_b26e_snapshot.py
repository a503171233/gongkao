#!/usr/bin/env python3
"""批次26-E · 抓取快照层测试（下载与提取解耦，防题源站点风控）"""
import sys, os, json, time, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from poc import autocollect as ac

PASS, FAIL = 0, 0
def chk(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")

# ---- 测试环境：快照目录切到临时目录 ----
tmp = tempfile.mkdtemp(prefix="snap_test_")
ac._SNAP_DIR = __import__("pathlib").Path(tmp)
ac._host_last_fetch.clear()

# ---- mock 能力层 registry：计数 + 假抓取 ----
calls = {"fetch": 0}
class FakeRegistry:
    def call(self, cap, url="", timeout=20, **kw):
        if cap == "fetch":
            calls["fetch"] += 1
            return ({"text": f"正文-{url}"[:80] + "题干？选项A 选项B 答案A",
                     "html": f"<html><a href='/p/1'>1</a></html>",
                     "final_url": url, "title": f"标题-{url}"}, None)
        raise ValueError(f"unknown cap {cap}")

class FakeCapMod:
    registry = FakeRegistry()

import types
fake_mod = types.ModuleType("poc.capabilities")
fake_mod.registry = FakeRegistry()
sys.modules["poc.capabilities"] = fake_mod
import poc
poc.capabilities = fake_mod

print("== 1. 快照命中：同一 URL 只真实抓取一次 ==")
r1 = ac._fetch_cached("https://example.com/paper/1")
chk("首次抓取走网络", calls["fetch"] == 1, f"calls={calls['fetch']}")
chk("首次 from_cache=False", r1.get("from_cache") is False)
chk("首次有正文", "题干" in r1.get("text", ""))
chk("快照文件已落盘", ac._snap_path("https://example.com/paper/1").exists())

r2 = ac._fetch_cached("https://example.com/paper/1")
chk("第二次命中快照 from_cache=True", r2.get("from_cache") is True)
chk("网络调用数仍为 1（零重复抓取）", calls["fetch"] == 1, f"calls={calls['fetch']}")
chk("快照正文与首次一致", r2.get("text") == r1.get("text"))
chk("快照保留 title", r2.get("title") == r1.get("title"))
chk("快照保留 html（供链接发现）", "<a href" in (r2.get("html") or ""))

print("== 2. _fetch_text 兼容层自动获得快照 ==")
t1 = ac._fetch_text("https://example.com/paper/2")
t2 = ac._fetch_text("https://example.com/paper/2")
chk("fetch_text 两次结果一致", t1 == t2 and t1)
chk("网络调用仅 +1", calls["fetch"] == 2, f"calls={calls['fetch']}")

print("== 3. 快照清除后重新抓取 ==")
ac.snapshots_clear(urls=["https://example.com/paper/1"])
chk("单 URL 清除", ac._snap_path("https://example.com/paper/1").exists() is False)
ac._fetch_cached("https://example.com/paper/1")
chk("清除后重新走网络", calls["fetch"] == 3, f"calls={calls['fetch']}")

print("== 4. 清空快照库 ==")
n = ac.snapshots_clear(urls=None)
chk("清空返回移除数", n.get("removed", 0) >= 2, f"removed={n}")
chk("目录已空", len(list(ac._SNAP_DIR.glob('*.json'))) == 0)

print("== 5. 同域限速：真实抓取间隔 ≥ delay*0.5 ==")
ac._host_last_fetch.clear()
ac._SNAP_DIR = __import__("pathlib").Path(tmp)  # 已清空
t0 = time.monotonic()
ac._fetch_cached("https://site-a.com/paper/9")
ac._fetch_cached("https://site-a.com/paper/10")
elapsed = time.monotonic() - t0
chk(f"同域两次真实抓取被限速 (耗时 {elapsed:.2f}s ≥ 1.5s)",
    elapsed >= 1.5, f"elapsed={elapsed:.2f}")
chk("限速下网络调用正常", calls["fetch"] == 5, f"calls={calls['fetch']}")

print("== 6. 不同域不限速 ==")
ac._host_last_fetch.clear()
t0 = time.monotonic()
ac._fetch_cached("https://site-b.com/paper/1")
ac._fetch_cached("https://site-c.com/paper/1")
e2 = time.monotonic() - t0
chk(f"异域抓取不限速 (耗时 {e2:.2f}s < 1s)", e2 < 1.0, f"elapsed={e2:.2f}")

print("== 7. TTL 过期重新抓取 ==")
p = ac._snap_path("https://site-b.com/paper/1")
d = json.loads(p.read_text(encoding="utf-8"))
d["fetched_at"] = "2020-01-01T00:00:00Z"   # 伪造成 6 年前
p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
r = ac._fetch_cached("https://site-b.com/paper/1", )  # 默认 TTL=0 永久 → 仍命中
chk("TTL=0 永久快照不受旧时间戳影响", r.get("from_cache") is True)
before = calls["fetch"]
r = ac._fetch_cached("https://site-b.com/paper/1")  # 正常
# 手动启用 TTL=1 小时
orig = ac._snapshot_cfg
ac._snapshot_cfg = lambda: (True, 1.0, 0.0)
r = ac._fetch_cached("https://site-b.com/paper/1")
chk("TTL=1h 下 2020 年快照过期→重新抓取",
    r.get("from_cache") is False and calls["fetch"] == before + 1)
ac._snapshot_cfg = orig

print("== 8. 站点线 crawl 集成：第二次整站爬取零网络 ==")
before = calls["fetch"]
pages1 = ac._crawl_site("https://example.com/crawl-start", max_pages=5, timeout=5)
first = calls["fetch"] - before
before = calls["fetch"]
pages2 = ac._crawl_site("https://example.com/crawl-start", max_pages=5, timeout=5)
second = calls["fetch"] - before
chk(f"首次爬取有网络请求 ({first} 次)", first >= 1)
chk(f"第二次爬取零网络 ({second} 次)", second == 0, f"second={second}")
chk("两次爬取产出详情页一致",
    [x[2] for x in pages1] == [x[2] for x in pages2] and pages1,
    f"pages1={len(pages1)} pages2={len(pages2)}")

print("== 9. 管理端视图 ==")
v = ac.snapshots_admin(limit=5)
chk("统计总数正确", v.get("total", 0) >= 3, f"total={v.get('total')}")
chk("磁盘占用>0", v.get("disk_bytes", 0) > 0)
chk("列表含 URL", all(x.get("url") for x in v.get("items", [])))
v0 = ac.snapshots_admin(limit=0)
chk("limit=0 只统计不列明细", v0.get("total", 0) >= 3 and v0.get("items") == [])

print("== 10. 网络失败语义保持：抛 ValueError ==")
def boom(*a, **kw):
    return (None, "connection refused")
fake_mod.registry.call = boom
try:
    ac._fetch_cached("https://broken.com/x")
    chk("失败抛 ValueError", False, "未抛异常")
except ValueError:
    chk("失败抛 ValueError", True)
except Exception as e:
    chk("失败抛 ValueError", False, f"抛了 {type(e).__name__}")
fake_mod.registry.call = FakeRegistry().call

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n通过 {PASS} · 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
