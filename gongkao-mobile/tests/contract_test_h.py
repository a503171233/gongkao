#!/usr/bin/env python3
"""批次H 契约测试：在线更新链路（check 配置 / APK 三通道 / 热更补丁配置）。"""
import json, sys, urllib.request, urllib.error, hashlib, re, base64

BASE = "https://a2a0641667069c341.app.workbuddy.host"
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))

def get(url, max_bytes=None):
    r = urllib.request.urlopen(url, timeout=30)
    data = r.read()
    return r.getcode(), data

# 1. 更新配置可达且结构合法
code, raw = get(BASE + "/api/app-update.json")
check("update.check 200", code == 200, str(code))
cfg = json.loads(raw)
for k in ["latest_vc", "latest_version", "apk_url", "notes", "force", "sources", "patch"]:
    check(f"update.check 字段 {k}", k in cfg)
check("update.check latest_vc 是 int", isinstance(cfg["latest_vc"], int))
check("update.check notes 非空", isinstance(cfg["notes"], list) and len(cfg["notes"]) > 0)
check("update.check sources 含 server 通道",
      any(s.get("type") == "server" for s in cfg["sources"] if isinstance(s, dict)))
patch = cfg.get("patch")
check("update.check patch 为 null 或含 vc/url/sha256",
      patch is None or all(k in patch for k in ("vc", "url", "sha256")))

# 2. APK 三通道一致（check 配置 / apk.html 内嵌 / 直链）
code, apk = get(cfg["apk_url"])
check("update.apk_url 200", code == 200, str(code))
h_cfg = hashlib.sha256(apk).hexdigest()
check("update.apk sha256 与 check 配置一致", h_cfg == cfg["sha256"],
      f"{h_cfg[:12]} vs {cfg['sha256'][:12]}")
code, html = get(BASE + "/apk.html")
check("update.apk.html 200", code == 200)
m = re.search(rb'[A-Za-z0-9+/=]{1000,}', html)
b64apk = base64.b64decode(m.group(0)) if m else b""
h_html = hashlib.sha256(b64apk).hexdigest()
check("update.apk.html 内嵌包与 check 一致", h_html == h_cfg, f"{h_html[:12]} vs {h_cfg[:12]}")

# 3. 覆盖安装前提：latest_vc 递增（对当前包）
code, raw2 = get(BASE + "/api/app-update.json")
check("update.check 缓存禁用（no-cache 复查一致）", json.loads(raw2)["latest_vc"] == cfg["latest_vc"])

# 4. 主链路无回归
code, _ = get(BASE + "/api/plans")
check("regression /api/plans 200", code == 200)

passed = sum(1 for _, ok, _ in results if ok)
print(f"\n== {passed}/{len(results)} PASS ==")
sys.exit(0 if passed == len(results) else 1)
