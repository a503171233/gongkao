# -*- coding: utf-8 -*-
"""Gitee OAuth API 集成测试（未配置时降级路径）
运行: cd backend && python tests/test_api_gitee.py
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-api-gitee-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["AUTH_DB"] = os.path.join(TMP, "auth.db")
os.environ.pop("GITEE_CLIENT_ID", None)
os.environ.pop("GITEE_CLIENT_SECRET", None)
os.environ.pop("GITEE_REDIRECT_URI", None)

import poc.api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(poc.api.app)
ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


# 1) status：未配置 → enabled=False
r = client.get("/auth/gitee/status")
check("status 200 + enabled=False", r.status_code == 200 and r.json().get("enabled") is False,
      str(r.json()))

# 2) login：未配置 → 503
r2 = client.get("/auth/gitee/login", follow_redirects=False)
check("login 未配置 503", r2.status_code == 503, str(r2.status_code) + str(r2.text[:80]))

# 3) callback：无 state → 400
r3 = client.get("/auth/gitee/callback?code=abc", follow_redirects=False)
check("callback 无 state 400", r3.status_code == 400, str(r3.status_code))

# 4) callback：state 未登记 → 400
r4 = client.get("/auth/gitee/callback?code=abc&state=forged", follow_redirects=False)
check("callback 伪造 state 400", r4.status_code == 400, str(r4.status_code))

# 5) 既有注册/登录无回归
r5 = client.post("/register", json={"username": "gitee_api用户", "password": "secret123"})
check("register 正常", r5.status_code == 200 and bool(r5.json().get("token")), str(r5.json())[:80])
r6 = client.post("/login", json={"username": "gitee_api用户", "password": "secret123"})
check("login 正常", r6.status_code == 200 and bool(r6.json().get("token")), str(r6.json())[:80])

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)