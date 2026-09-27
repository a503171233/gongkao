# 批次26-L 实弹验证：①qrcode 会话顶替 ②SMS 锁定快速失败
import json
import os
import time
import urllib.error
import urllib.request

BASE = "http://localhost:9000"
PHONE = os.environ.get("FB_VERIFY_PHONE", "13812345678")


def req(method, path, token=None, body=None):
    r = urllib.request.Request(BASE + path, method=method)
    if token:
        r.add_header("Authorization", "Bearer " + token)
    data = None
    if body is not None:
        r.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    try:
        resp = urllib.request.urlopen(r, data=data, timeout=30)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


code, d = req("POST", "/login", body={"username": "smoke26g",
                                      "password": "Smk26g!x"})
tok = d["token"]

ok = []


def chk(name, cond):
    ok.append(bool(cond))
    print(("✓" if cond else "✗"), name, flush=True)


# ① qrcode 会话顶替
c, d = req("POST", "/me/fenbi/login/qrcode", tok)
sid_a = d.get("sid", "")
print("会话A:", c, d.get("state"), sid_a[:12])
c, d = req("POST", "/me/fenbi/login/qrcode", tok)
sid_b = d.get("sid", "")
print("会话B:", c, d.get("state"), sid_b[:12], "(应为新 sid)")
chk("B 为新会话", sid_b and sid_b != sid_a)
time.sleep(1.5)
c, d = req("GET", f"/me/fenbi/login/status?sid={sid_a}", tok)
print("A status:", c, d.get("state"), "|", (d.get("hint") or "")[:24])
# 顶替设计：旧会话立即从注册表移除（HTTP 层 404），worker 线程自行退出
chk("A 被顶替移除(HTTP 404)", c == 404)
c, d = req("GET", f"/me/fenbi/login/status?sid={sid_b}", tok)
print("B status:", c, d.get("state"), "|", (d.get("hint") or "")[:24])
chk("B 仍活动(非 cancelled)", d.get("state") not in ("cancelled",))
time.sleep(3)
c, d = req("GET", f"/me/fenbi/login/status?sid={sid_b}", tok)
chk("B 3s 后不被 A 的迟到过程态覆盖", d.get("state") != "cancelled")
req("POST", "/me/fenbi/login/cancel", tok, {"sid": sid_b})
print("[清理] B 已取消")

# ② SMS 锁定快速失败
c, d = req("POST", "/me/fenbi/login/sms", tok, {"phone": PHONE})
sid_c = d.get("sid", "")
print("SMS 会话:", c, d.get("state"))
t0 = time.time()
fast = False
for i in range(40):
    time.sleep(1)
    c, d = req("GET", f"/me/fenbi/login/status?sid={sid_c}", tok)
    st = d.get("state")
    if st == "failed":
        dt = time.time() - t0
        print(f"failed @ {dt:.0f}s | {(d.get('error') or '')[:60]}")
        fast = dt < 25 and "锁定" in (d.get("error") or "")
        break
    if i % 5 == 0:
        print(f"[{i}s] {st} | {(d.get('hint') or '')[:30]}", flush=True)
chk("SMS 风控锁定快速失败(<25s 且文案含「锁定」)", fast)

print(f"\n===== 结果 {sum(ok)}/{len(ok)} =====")
