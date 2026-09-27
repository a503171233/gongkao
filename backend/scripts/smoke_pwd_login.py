# 批次26-K 冒烟：账密登录链路实弹（假凭据 → 期待「账号或密码不正确」类错误）
# 同时验证：端点可用、worker 真实打开粉笔页、切账密视图、填表提交、结果识别
import json
import time
import urllib.error
import urllib.request

BASE = "http://localhost:9000"


def req(method, path, token=None, body=None, timeout=30):
    r = urllib.request.Request(BASE + path, method=method)
    if token:
        r.add_header("Authorization", "Bearer " + token)
    data = None
    if body is not None:
        r.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    try:
        resp = urllib.request.urlopen(r, data=data, timeout=timeout)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


code, d = req("POST", "/login", body={"username": "smoke26g",
                                      "password": "Smk26g!x"})
assert code == 200, d
tok = d["token"]
print("[登录] ok", flush=True)

# 发起账密会话（假凭据：真实格式手机号 + 错误密码）
code, d = req("POST", "/me/fenbi/login/password", token=tok,
              body={"account": "13800009999", "password": "WrongPwd#123"})
print(f"[发起] {code}: mode={d.get('mode')} state={d.get('state')}", flush=True)
assert code == 200, d
sid = d["sid"]

final = None
for i in range(40):  # 最多 2 分钟
    time.sleep(3)
    code, d = req("GET", f"/me/fenbi/login/status?sid={sid}", token=tok)
    st = d.get("state")
    if i % 4 == 0 or st in ("failed", "success", "need_drag"):
        print(f"[{i*3}s] state={st} hint={d.get('hint','')[:40]} "
              f"err={d.get('error','')[:80]}", flush=True)
    if st in ("failed", "success", "need_drag"):
        final = d
        break

print("\n===== 冒烟结论 =====", flush=True)
if final is None:
    print("超时未到终态", flush=True)
elif final["state"] == "need_drag":
    print("→ 触发滑块验证（链路通到风控层，符合预期分支之一）", flush=True)
elif final["state"] == "failed":
    print(f"→ failed: {final['error']}", flush=True)
    ok_keywords = ("密码", "账号", "错误", "失败", "不正确")
    hit = any(k in final["error"] for k in ok_keywords)
    print("→ 错误语义识别:", "✓ 精准提示" if hit else "△ 通用兜底提示", flush=True)
elif final["state"] == "success":
    print("→ 意外成功？检查假凭据", flush=True)
# 无论哪个分支，会话都会被 TTL reaper 或 cancel 清理
req("POST", "/me/fenbi/login/cancel", token=tok, body={"sid": sid})
print("[清理] cancel 已发送", flush=True)
