# 实弹验证：滑块 AI 自动拖动（观察 need_drag 前的自动识别/拖动中间态）
import json
import os
import time
import urllib.error
import urllib.request

BASE = "http://localhost:9000"
PHONE = os.environ.get("FB_VERIFY_PHONE", "13812345678")  # 真号经环境变量注入


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

code, d = req("POST", "/me/fenbi/login/sms", tok, {"phone": PHONE})
print("发起:", code, d.get("state"), flush=True)
sid = d["sid"]

timeline = []
for i in range(60):  # 最多 2 分钟
    time.sleep(1)
    code, d = req("GET", f"/me/fenbi/login/status?sid={sid}", tok)
    st, hint = d.get("state"), d.get("hint", "")
    line = f"[{i}s] {st} | {hint[:44]}"
    if not timeline or timeline[-1].split(" | ")[0] != line.split(" | ")[0] \
            or (st == "submitting" and
                (not timeline or timeline[-1] != line)):
        timeline.append(line)
        print(line, flush=True)
    if st in ("failed", "need_drag", "need_code", "success"):
        print("\n终态:", st, "|", d.get("error", "")[:90], flush=True)
        break

print("\n===== 关键判定 =====")
submitted_auto = any("正在自动拖动" in t or "自动识别" in t for t in timeline)
if submitted_auto:
    print("✓ AI 自动识别+自动拖动已真实执行（时间线含自动尝试中间态）")
else:
    print("△ 未观察到自动尝试中间态（可能轮询太疏或未触发滑块）")
if any("need_drag" in t for t in timeline):
    print("→ 自动尝试后回落人工兜底（预期行为之一）")
if any("need_code" in t for t in timeline):
    print("✓✓ 滑块自动通过并成功发出验证码！")
req("POST", "/me/fenbi/login/cancel", tok, {"sid": sid})
print("[清理] cancel 已发送")
