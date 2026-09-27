# 抓真实滑块截图：发起 SMS 会话触发滑块 → 从 status 拿 base64 截图存盘分析
import base64
import json
import time
import urllib.error
import urllib.request

BASE = "http://localhost:9000"


def req(method, path, token=None, body=None):
    r = urllib.request.Request(BASE + path, method=method)
    if token:
        r.add_header("Authorization", "Bearer " + token)
    if body is not None:
        r.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    else:
        data = None
    try:
        resp = urllib.request.urlopen(r, data=data, timeout=30)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


code, d = req("POST", "/login", body={"username": "smoke26g",
                                      "password": "Smk26g!x"})
tok = d["token"]

code, d = req("POST", "/me/fenbi/login/sms", tok, {"phone": "13812345678"})
print("发起:", code, d.get("state"))
sid = d["sid"]

shot_saved = False
for i in range(30):
    time.sleep(2)
    code, d = req("GET", f"/me/fenbi/login/status?sid={sid}", tok)
    st = d.get("state")
    print(f"[{i*2}s] {st} {d.get('hint','')[:36]}", flush=True)
    if st == "need_drag" and d.get("shot"):
        with open("/tmp/slider_shot.png", "wb") as f:
            f.write(base64.b64decode(d["shot"]))
        print("已保存 /tmp/slider_shot.png", len(d["shot"]), "b64 chars")
        shot_saved = True
        break
    if st in ("failed", "need_code", "success"):
        print("终态:", st, d.get("error", ""))
        break

if not shot_saved:
    print("未拿到滑块截图")
else:
    from PIL import Image
    im = Image.open("/tmp/slider_shot.png")
    print("截图尺寸:", im.size, im.mode)
