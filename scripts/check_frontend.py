# -*- coding: utf-8 -*-
"""B 阶段静态复核：前端静态资源死链 + 前端 api() 调用 vs 后端路由 悬空比对。
用法：python scripts/check_frontend.py
输出：RESOURCE_MISS / API_MISS / API_ORPHAN 三类问题清单（对服务器实测与本地代码比对）。"""
import json
import os
import re
import sys
import urllib.request
import urllib.error

FRONT = r"d:\xiangmu\gongkao\frontend"
API_PY = r"d:\xiangmu\gongkao\backend\poc\api.py"
BASE = "http://YOUR_SERVER_IP:3000"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {str(detail)[:130]}" if detail else ""))
    if not cond:
        fails.append(name)


# ---------- 1. 前端 HTML 引用的静态资源 在线探测（301/200=OK） ----------
html_files = [f for f in os.listdir(FRONT) if f.endswith(".html")]
resource_refs = []
for hf in html_files:
    with open(os.path.join(FRONT, hf), encoding="utf-8") as f:
        content = f.read()
    # script src 与 css href（排除 JS 模板拼串：含 +、引号、控制字符、< > 的跳过）
    for m in re.finditer(r'(?:src|href)="(/[^"#<>+]+?)"', content):
        p = m.group(1)
        if p.startswith("/") and not p.startswith("/api") and "://" not in p and "{" not in p:
            resource_refs.append((hf, p))

issues = []
for hf, p in sorted(set(resource_refs)):
    url = BASE + p
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=15) as r:
            code = r.status
        if code >= 400:
            issues.append(f"{hf} → {p} HTTP {code}")
    except urllib.error.HTTPError as e:
        issues.append(f"{hf} → {p} HTTP {e.code}")
    except Exception as e:
        issues.append(f"{hf} → {p} ERR {e}")
check("静态资源引用在线检查", not issues, "; ".join(issues[:4]))

# ---------- 2. 后端路由全量 ----------
with open(API_PY, encoding="utf-8") as f:
    api_src = f.read()
backend_routes = set()
for m in re.finditer(r'@app\.(get|post|put|delete|patch)\("([^"]+)"', api_src):
    backend_routes.add(m.group(2))
# 把 {xxx} 转 * 通配用于匹配
backend_pat = [r.replace("{", "<").replace("}", ">") for r in backend_routes]

# ---------- 3. 前端 JS 引用的 API 路径 ----------
api_calls = set()
# 匹配：GK.api('/x') / admin(`/x`) / fetch('/x') / "/api/x" 字面量 / ...(  'x' /  `x`
API_CALL_RE = re.compile(r"""["'`]/(?:api/)?((?:admin|auth|me|health|teachers|info|ask|login|register|logout|practice|favorites|mistakes|feedback|knowledge|documents|sessions|messages|session|study|stats|upload|ws|recharge|payment|backups|q|question|random|search|feedback)[A-Za-z0-9_/{}.?=&%-]*)["'`]""")
for root, _, files in os.walk(FRONT):
    for f in files:
        if f.endswith((".js", ".html")):
            path = os.path.join(root, f)
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            rel = os.path.relpath(path, FRONT).replace("\\", "/")
            for m in re.finditer(r'["\'`](/api/[A-Za-z0-9_/{}.?=&%-]+)["\'`]', src):
                api_calls.add((rel, m.group(1)))
            for m in API_CALL_RE.finditer(src):
                api_calls.add((rel, "/" + m.group(1)))

def fold(x):
    """把具体 id/参数路径折叠为模板（<x> 占位）。"""
    segs = x.strip("/").split("/")
    folded = []
    for s in segs:
        if re.fullmatch(r"[0-9A-Za-z_.-]{1,64}", s) and s not in ("admin", "api"):
            folded.append("<x>")
        else:
            folded.append(s)
    return "/" + "/".join(folded)


def _match_route(call_path, route):
    """粗匹配：call 的折叠路径是否命中某路由。"""
    r = route.strip("/").split("/")
    c = call_path.strip("/").split("/")
    if len(r) != len(c):
        return False
    for rs, cs in zip(r, c):
        if rs.startswith("<") or cs == "<x>":
            continue
        if rs.startswith("{") or cs.startswith("{"):
            continue
        if rs != cs:
            return False
    return True


miss, ok_count = [], 0
seen = set()


for rel, p in sorted(api_calls):
    k = (rel, p)
    if k in seen:
        continue
    seen.add(k)
    path_part = p.split("?")[0].lstrip("/").replace("api/", "", 1) or p.lstrip("/")
    path_part = "/" + path_part.lstrip("/")
    folded = fold(path_part)
    hit = folded in backend_routes or any(_match_route(folded, br) for br in backend_routes)
    # 特判：fold 把固定词也折叠成 <x> 造成漏配——再试按段不折叠精确比对
    if not hit:
        hit = path_part in backend_routes
    if hit:
        ok_count += 1
    else:
        miss.append(f"{rel} → {p}")


def _match_route(call_path, route):
    """粗匹配：call 的折叠路径是否命中某路由。"""
    r = route.strip("/").split("/")
    c = call_path.strip("/").split("/")
    if len(r) != len(c):
        return False
    for rs, cs in zip(r, c):
        if rs.startswith("<") or cs == "<x>":
            continue
        if rs.startswith("{") or cs.startswith("{"):
            continue
        if rs != cs:
            return False
    return True


if miss:
    print("  -- 疑似悬空 API 调用（前端引用但后端无路由）--")
    for x in miss[:25]:
        print("   ", x)
check("前端 API 调用均在后端有路由", not miss, "; ".join(miss[:3]) if miss else f"{ok_count} 个调用已匹配")

# ---------- 4. 后端路由 vs 前端调用（后端存在但前端从不调用 = 服务端保留接口，仅提示） ----------
front_calls_raw = {p for _, p in seen}
unused_hint = []
for br in sorted(backend_routes):
    if br.startswith("/admin/backups"):
        continue
    bf = fold(br)
    if not any(fold(c.split("?")[0]) == bf or _match_route(fold(c.split("?")[0]), br)
               for c in front_calls_raw):
        unused_hint.append(br)
print(f"  -- 提示：后端路由未被前端直接调用 {len(unused_hint)} 个（可能被小程序/管理端/内部逻辑调用）--")
for x in unused_hint[:15]:
    print("   ", x)

print(f"\n== 复核 {2 - len(fails)}/2 PASS ==")
sys.exit(1 if fails else 0)