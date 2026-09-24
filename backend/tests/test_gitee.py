# -*- coding: utf-8 -*-
"""Gitee OAuth 客户端 · 单元测试（mock HTTP）
运行: cd backend && python tests/test_gitee.py
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-gitee-test-")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


from poc.gitee import GiteeOAuth, TOKEN_URL, USER_API  # noqa: E402

# ---------- mock HTTP ----------
calls = []


def fake_http(method, url, data=None):
    calls.append((method, url, data))
    if url == TOKEN_URL:
        return 200, {"access_token": "tok_123", "token_type": "bearer"}
    if url.startswith(USER_API):
        return 200, {"id": 888, "name": "gitee_user", "avatar_url": "https://gitee.com/a.png"}
    return 404, {}


# 1) 未配置 → enabled=False
g_disabled = GiteeOAuth("", "", "http://x/cb")
check("未配置 client 禁用", g_disabled.enabled is False)

# 2) 已配置 → enabled=True + 授权 URL
g = GiteeOAuth("cid123", "csecret", "http://YOUR_SERVER_IP:3000/api/auth/gitee/callback",
               _http=fake_http)
check("已配置启用", g.enabled is True)
url = g.authorize_url("state_abc")
check("授权 URL 含 client_id/redirect/state",
      "client_id=cid123" in url and "redirect_uri=" in url and "state=state_abc" in url
      and "response_type=code" in url and "scope=user_info" in url, url)

# 3) code 换 token
tok = g.exchange_code("code_xyz")
check("exchange_code 返回 access_token", tok.get("access_token") == "tok_123", str(tok))
check("exchange_code 请求 POST TOKEN_URL",
      calls[-1][0] == "POST" and calls[-1][1] == TOKEN_URL and calls[-1][2]["code"] == "code_xyz",
      str(calls[-1]))

# 4) 拉用户信息
user = g.fetch_user("tok_123")
check("fetch_user 返回 id/name", user.get("id") == 888 and user.get("name") == "gitee_user",
      str(user))
check("fetch_user 请求 GET USER_API", calls[-1][0] == "GET" and calls[-1][1].startswith(USER_API))

# 5) 完整 login_with_code
u2 = g.login_with_code("code_abc")
check("login_with_code 返回用户", u2.get("id") == 888)

# 6) 换取失败 → ValueError
def fake_fail(method, url, data=None):
    return 400, {"error": "invalid_grant", "error_description": "bad code"}


g2 = GiteeOAuth("cid", "sec", "http://x/cb", _http=fake_fail)
try:
    g2.exchange_code("bad")
    check("换取失败抛错", False)
except ValueError as e:
    check("换取失败抛错", "bad code" in str(e) or "invalid_grant" in str(e), str(e))

# 7) 拉用户失败 → ValueError
def fake_user_fail(method, url, data=None):
    if url == TOKEN_URL:
        return 200, {"access_token": "t"}
    return 401, {"message": "unauthorized"}


g3 = GiteeOAuth("cid", "sec", "http://x/cb", _http=fake_user_fail)
try:
    g3.login_with_code("c")
    check("拉用户失败抛错", False)
except ValueError:
    check("拉用户失败抛错", True)

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)