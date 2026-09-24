# -*- coding: utf-8 -*-
"""用户体系 API 契约测试（隔离 tempfile）
运行: cd backend && python tests/test_api_auth.py
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-api-auth-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["AUTH_DB"] = os.path.join(TMP, "auth.db")

import poc.api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(poc.api.app)
ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


# 1) 注册
r = client.post("/register", json={"username": "API学生", "password": "secret123"})
d = r.json()
check("注册 200", r.status_code == 200, str(r.status_code))
check("注册返回 token", bool(d.get("token")))
check("注册返回 role=free", d.get("role") == "free", str(d))
tok = d["token"]

# 2) 重复注册 400
r2 = client.post("/register", json={"username": "API学生", "password": "secret123"})
check("重复注册 400", r2.status_code == 400, str(r2.status_code))

# 3) 登录成功/失败
r3 = client.post("/login", json={"username": "API学生", "password": "secret123"})
check("登录 200", r3.status_code == 200, str(r3.status_code))
check("登录 quota_left 为免费额度", r3.json().get("quota_left") == 20, str(r3.json()))
r4 = client.post("/login", json={"username": "API学生", "password": "wrong"})
check("错误密码 401", r4.status_code == 401, str(r4.status_code))

# 4) /me 认证与匿名
r5 = client.get("/me", headers={"Authorization": f"Bearer {tok}"})
check("带 token /me 返回用户", r5.json().get("anonymous") is False and r5.json()["role"] == "free", str(r5.json()))
r6 = client.get("/me")
check("无 token /me 返回匿名", r6.json().get("anonymous") is True, str(r6.json()))

# 5) /ask 匿名可用（不限额度）
r7 = client.post("/ask", json={"query": "转折关系怎么做题", "teacher_id": "T001", "stream": False})
check("匿名 /ask 200", r7.status_code == 200, str(r7.status_code))

# 6) 带 token 提问（消耗额度）
r8 = client.post("/ask", json={"query": "转折关系怎么做题", "teacher_id": "T001", "stream": False},
                 headers={"Authorization": f"Bearer {tok}"})
check("登录用户 /ask 200", r8.status_code == 200, str(r8.status_code))
r9 = client.get("/me", headers={"Authorization": f"Bearer {tok}"})
check("提问后 quota_left 减 1 (=19)", r9.json().get("quota_left") == 19, str(r9.json()))

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)