# -*- coding: utf-8 -*-
"""gongkao 线上冒烟 + C1/C3/C4 功能测试（公网 HTTP，无需 SSH）
目标：http://YOUR_SERVER_IP:3000/api
"""
import json
import time
import urllib.request
import urllib.error
import sys
import http.client

BASE = "http://YOUR_SERVER_IP:3000/api"
RESULTS = []

def req(method, path, body=None, headers=None, timeout=30, raw=False, no_redirect=False):
    url = BASE + path
    data = None
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    r = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        opener = urllib.request.build_opener()
        if no_redirect:
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None
            opener = urllib.request.build_opener(NoRedirect())
        with opener.open(r, timeout=timeout) as resp:
            code = resp.status
            ct = resp.headers.get("content-type", "")
            if raw:
                text = resp.read().decode("utf-8", "replace")
            elif "json" in ct:
                text = resp.read().decode("utf-8", "replace")
                try:
                    text = json.loads(text)
                except Exception:
                    pass
            else:
                text = resp.read().decode("utf-8", "replace")[:500]
            return code, text
    except urllib.error.HTTPError as e:
        body_txt = ""
        try:
            raw_b = e.read().decode("utf-8", "replace")
            try:
                j = json.loads(raw_b)
                body_txt = j.get("detail", raw_b) if isinstance(j, dict) else raw_b
            except Exception:
                body_txt = raw_b
        except Exception:
            pass
        return e.code, body_txt
    except Exception as e:
        return -1, str(e)[:200]

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    RESULTS.append((status, name, detail))
    mark = "✅" if cond else "❌"
    print(f"{mark}[{status}] {name}" + (f"\n      {detail}" if detail and status == "FAIL" else ""))

def main():
    # ========== A. 基础设施（匿名） ==========
    print("===== A. 基础设施 =====")
    code, r = req("GET", "/health")
    check("A1. /health 200 且含多老师",
          code == 200 and isinstance(r, dict) and len(r.get("teachers", [])) >= 2, r)
    code, r = req("GET", "/teachers")
    check("A2. /teachers 返回老师列表",
          code == 200 and isinstance(r, dict) and len(r.get("teachers", [])) >= 1, r)
    code, r = req("GET", "/info?teacher_id=T001")
    chunk = r.get("chunk_count", 0) if isinstance(r, dict) else 0
    check("A3. /info T001 知识库有块", code == 200 and chunk > 0, f"chunk_count={chunk}")
    code, r = req("GET", "/guard/stats")
    check("A4. /guard/stats 200", code == 200, r)
    code, r = req("GET", "/metrics")
    check("A5. /metrics 含 uptime", code == 200 and "uptime" in str(r), str(r)[:120])
    code, r = req("GET", "/metrics/prometheus")
    check("A6. /metrics/prometheus 有 gk_ 指标",
          code == 200 and "gk_" in str(r), str(r)[:120])
    code, r = req("GET", "/audio/transcriptions/status")
    check("A7. 转写桩 enabled=false",
          code == 200 and isinstance(r, dict) and r.get("enabled") is False, r)
    code, r = req("GET", "/plans")
    plans = r.get("plans", []) if isinstance(r, dict) else []
    check("A8. /plans 三档定价",
          code == 200 and isinstance(plans, list) and len(plans) == 3,
          r if code != 200 else f"{len(plans)} 档")
    code, r = req("GET", "/auth/gitee/status")
    check("A9. Gitee status enabled", code == 200 and "enabled" in str(r), r)
    code, _ = req("GET", "/auth/gitee/login", no_redirect=True)
    check("A10. Gitee login 307 跳转", code == 307, f"code={code}")

    # ========== B. 用户链路 ==========
    print("===== B. 用户链路 =====")
    uname = f"stest_{int(time.time())}"
    code, r = req("POST", "/register", {"username": uname, "password": "Test@1234"})
    check("B1. 注册成功返回 token",
          code == 200 and isinstance(r, dict) and r.get("token"), r if code != 200 else "")
    token = r.get("token", "") if isinstance(r, dict) else ""
    auth = {"Authorization": f"Bearer {token}"}
    code, r = req("POST", "/login", {"username": uname, "password": "Test@1234"})
    check("B2. 登录成功", code == 200 and isinstance(r, dict) and r.get("token"), r if code != 200 else "")
    code, r = req("GET", "/me", headers=auth)
    check("B3. /me 非匿名且含额度",
          code == 200 and isinstance(r, dict) and r.get("anonymous") is False
          and "quota_left" in r, str(r)[:200])
    code, r = req("GET", "/me/membership", headers=auth)
    check("B4. /me/membership 会员状态",
          code == 200 and isinstance(r, dict), str(r)[:200])

    # ========== C. C4 学习闭环 ==========
    print("===== C. C4 学员增值 =====")
    code, r = req("POST", "/favorites", {"question": "转折关系口诀？", "answer": "看关联词", "teacher_id": "T001"}, headers=auth)
    fav_id = r.get("fav_id") or r.get("id") if isinstance(r, dict) else None
    check("C1. 收藏回答成功", code == 200 and isinstance(r, dict), str(r)[:200])
    code, r = req("GET", "/favorites", headers=auth)
    favs = r.get("favorites", []) if isinstance(r, dict) else []
    check("C2. 收藏列表含刚收藏项", code == 200 and len(favs) >= 1, f"{len(favs)} 条")
    if fav_id:
        code, r = req("DELETE", f"/favorites/{fav_id}", headers=auth)
        check("C3. 取消收藏", code == 200, r)
    code, r = req("POST", "/mistakes", {"question": "错题示例", "correct_note": "注意转折词"}, headers=auth)
    mist_id = r.get("mist_id") or r.get("id") if isinstance(r, dict) else None
    check("C4. 记录错题成功", code == 200, str(r)[:200])
    code, r = req("GET", "/mistakes", headers=auth)
    mists = r.get("mistakes", []) if isinstance(r, dict) else []
    check("C5. 错题本非空", code == 200 and len(mists) >= 1, f"{len(mists)} 条")
    if mist_id:
        code, r = req("DELETE", f"/mistakes/{mist_id}", headers=auth)
        check("C6. 删除错题", code == 200, r)
    code, r = req("GET", "/practice/next?teacher_id=T001", headers=auth)
    check("C7. 抽练习题（匿名可读）", code == 200, str(r)[:150])
    code, r = req("POST", "/practice/submit", {"question": "练习题", "answer": "A", "score": 80, "feedback": "不错", "teacher_id": "T001"}, headers=auth)
    check("C8. 提交练习", code == 200, str(r)[:150])
    code, r = req("GET", "/practice/logs", headers=auth)
    logs = r.get("logs", []) if isinstance(r, dict) else []
    check("C9. 练习记录非空", code == 200 and len(logs) >= 1, f"{len(logs)} 条")

    # ========== D. C1 会员订单 ==========
    print("===== D. C1 会员付费 =====")
    code, r = req("POST", "/orders", {"plan": "month"}, headers=auth)
    order_id = r.get("order_id") if isinstance(r, dict) else None
    check("D1. 建单成功", code == 200 and order_id, str(r)[:200])
    if order_id:
        code, r = req("GET", f"/orders/{order_id}", headers=auth)
        check("D2. 查单状态", code == 200 and isinstance(r, dict) and r.get("status"), str(r)[:200])
    code, r = req("GET", "/orders/user", headers=auth)
    olist = r.get("orders", []) if isinstance(r, dict) else []
    check("D3. 用户订单列表", code == 200 and isinstance(olist, list), f"{len(olist)} 单")
    code, r = req("POST", "/orders/recharge-code/activate", {"code": "INVALID-CODE-XXXX"}, headers=auth)
    check("D4. 无效充值码被拒(404/400)", code in (400, 404), f"code={code} {str(r)[:100]}")

    # ========== E. Admin 鉴权负路径（无凭据） ==========
    print("===== E. Admin 鉴权（无凭据应拒） =====")
    code, r = req("GET", "/admin/stats/overview")
    check("E1. 无凭据 overview → 401/403", code in (401, 403), f"code={code}")
    code, r = req("GET", "/admin/stats/overview", headers={"X-Admin-Secret": "wrong-secret"})
    check("E2. 错误 X-Admin-Secret → 401/403", code in (401, 403), f"code={code}")
    code, r = req("GET", "/admin/teachers")
    check("E3. 无凭据老师列表 → 401/403", code in (401, 403), f"code={code}")
    code, r = req("GET", "/admin/users")
    check("E4. 无凭据用户列表 → 401/403", code in (401, 403), f"code={code}")
    code, r = req("POST", "/admin/teachers", {"teacher_id": "T099"})
    check("E5. 无凭据创建老师 → 401/403", code in (401, 403), f"code={code}")
    code, r = req("GET", "/admin/stats/export")
    check("E6. 无凭据导出 → 401/403", code in (401, 403), f"code={code}")

    # ========== F. 安全合规 ==========
    print("===== F. 安全合规 =====")
    # F1: multipart 上传，错误 X-Upload-Secret → 403
    boundary = "----gkTestBoundary" + str(int(time.time()))
    body_b = (f"--{boundary}\r\n"
              f"Content-Disposition: form-data; name=\"file\"; filename=\"t.txt\"\r\n"
              f"Content-Type: text/plain\r\n\r\n"
              f"测试内容\r\n"
              f"--{boundary}--\r\n").encode("utf-8")
    f1_url = BASE + "/upload?teacher_id=T001"
    f1_req = urllib.request.Request(f1_url, data=body_b, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Upload-Secret": "wrong-secret",
    })
    try:
        with urllib.request.urlopen(f1_req, timeout=15) as resp:
            f1_code = resp.status
    except urllib.error.HTTPError as e:
        f1_code = e.code
    except Exception as e:
        f1_code = -1
    check("F1. 错误上传密钥 → 403", f1_code == 403, f"code={f1_code}")
    code, r = req("POST", "/login", {"username": "x", "password": "y"}, headers={"Origin": "https://evil.example.com"})
    check("F2. 跨站 Origin POST → 403(CSRF)", code == 403, f"code={code} {str(r)[:80]}")
    code, r = req("POST", "/me/logout", headers=auth)
    check("F3. 登出成功", code == 200 and r.get("logged_out") is True, str(r)[:120])
    code, r = req("GET", "/me", headers=auth)
    check("F4. 登出后旧 token 失效(匿名)",
          code == 200 and isinstance(r, dict) and r.get("anonymous") is True, str(r)[:150])
    code, r = req("GET", "/documents?teacher_id=T001")
    docs = r.get("documents", []) if isinstance(r, dict) else []
    check("F5. 文档列表可见", code == 200 and isinstance(docs, list), f"{len(docs)} 文档")

    # ========== 汇总 ==========
    print("\n===== 汇总 =====")
    passed = sum(1 for s, _, _ in RESULTS if s == "PASS")
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    print(f"总计 {len(RESULTS)} 项：PASS {passed} / FAIL {failed}")
    for s, n, d in RESULTS:
        if s == "FAIL":
            print(f"  ❌ {n}  {d}")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())