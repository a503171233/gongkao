# -*- coding: utf-8 -*-
"""批次6-B：/me/logout-all token 版本化专项（清单 #6 补测试）。

运行：cd backend && python tests/test_logout_all.py
覆盖：
  1) 核心链路：登录 → revoke_all_tokens → 旧 token 失效 → 重登新 token 可用，版本递增；
  2) ver=0 存量四段 token：用户版本 0 时可校验；logout-all（ver≥1）后正确失效；
  3) 旧库缺 token_ver 列：_token_ver 平滑回退 0、登录不炸、版本校验跳过（向后兼容）；
  4) 同秒连续签发 token 不同（nonce，防登出后同秒重登拿到同一已吊销 token 回归）；
  5) 版本落后 token 防御：用旧版本密钥正确签名的 token（ver < 当前版本）仍被拒；
  6) API 层：POST /me/logout-all 返回新版本号，旧 token 后续请求 401。
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk_logout_all_")
os.environ["AUTH_DB"] = os.path.join(TMP, "auth.db")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["STUDY_DB"] = os.path.join(TMP, "study.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"

ok_count = 0
fail_count = 0


def check(name, cond, detail=""):
    global ok_count, fail_count
    if cond:
        print(f"  ✅ {name}")
        ok_count += 1
    else:
        print(f"  ❌ {name}" + (f"  | {detail}" if detail else ""))
        fail_count += 1


from poc.auth import AuthStore  # noqa: E402

a = AuthStore()

# ==================== 1) 核心链路 ====================
uid = a.register("吊销测试员", "pass123")
tok1 = a.login("吊销测试员", "pass123")
check("登录后 token 有效", a.user_from_token(tok1) is not None)
check("初始 token_ver=0", a._token_ver(uid) == 0, a._token_ver(uid))

new_ver = a.revoke_all_tokens(uid)
check("revoke_all_tokens 返回新版本 1", new_ver == 1, new_ver)
check("logout-all 后旧 token 失效", a.user_from_token(tok1) is None)

tok2 = a.login("吊销测试员", "pass123")
u2 = a.user_from_token(tok2)
check("重登后新 token 可用", u2 is not None and u2["user_id"] == uid)

tok3 = a.login("吊销测试员", "pass123")
a.revoke_all_tokens(uid)
check("二次 logout-all 后 token2/token3 均失效",
      a.user_from_token(tok2) is None and a.user_from_token(tok3) is None)
check("版本递增至 2", a._token_ver(uid) == 2, a._token_ver(uid))
tok4 = a.login("吊销测试员", "pass123")
check("版本 2 下重登仍可用", a.user_from_token(tok4) is not None)

# ==================== 2) ver=0 存量四段 token 兼容 ====================
uid_b = a.register("存量用户", "pass456")
legacy = a._sign(uid_b, ver=0)  # 显式签发历史四段格式
check("ver=0 存量四段 token 可校验", a.user_from_token(legacy) is not None)
a.revoke_all_tokens(uid_b)
check("logout-all 后 ver=0 存量 token 正确失效", a.user_from_token(legacy) is None)

# ==================== 3) 旧库缺 token_ver 列平滑兼容 ====================
old_db = os.path.join(TMP, "drop_col.db")
a_old = AuthStore(db_path=old_db)
uid_old = a_old.register("缺列用户", "pass789")
conn = sqlite3.connect(old_db)
conn.execute("ALTER TABLE users DROP COLUMN token_ver")  # 模拟升级前的老库
conn.commit()
conn.close()
check("模拟旧库：token_ver 列已删除",
      "token_ver" not in [r[1] for r in sqlite3.connect(old_db).execute("PRAGMA table_info(users)")])
check("缺列 _token_ver 回退 0 不炸", a_old._token_ver(uid_old) == 0)
tok_old = a_old.login("缺列用户", "pass789")
check("缺列库用户登录不炸且 token 有效", a_old.user_from_token(tok_old) is not None)

# ==================== 4) 同秒连续签发 nonce 不同 ====================
t1 = a._sign(uid)
t2 = a._sign(uid)
check("同秒两次签发 token 不同（nonce）", t1 != t2)
check("同秒两次签发均有效", a.user_from_token(t1) is not None and a.user_from_token(t2) is not None)

# ==================== 5) 版本落后 token 防御 ====================
import hashlib  # noqa: E402
import hmac  # noqa: E402
import time  # noqa: E402

# 当前 uid 已到 ver=2：用 ver=1 的密钥手工正确签名 → 版本落后必须被拒
payload = f"{uid}.{int(time.time())}.deadbeef.1"
sig = hmac.new(b"gk-auth:1", payload.encode(), hashlib.sha256).hexdigest()[:16]
forged = f"{payload}.{sig}"
check("版本落后(1<2)的正确签名 token 仍被拒", a.user_from_token(forged) is None)

# ==================== 6) API 层 ====================
from fastapi.testclient import TestClient  # noqa: E402

from poc import api  # noqa: E402

client = TestClient(api.app)
tok_api = api.auth_store.login("吊销测试员", "pass123")
check("API 侧登录成功（共享 AUTH_DB）", bool(tok_api))

r = client.post("/me/logout-all", headers={"Authorization": f"Bearer {tok_api}"})
check("POST /me/logout-all 200 且返回新版本号",
      r.status_code == 200 and r.json().get("logged_out_all") is True
      and isinstance(r.json().get("token_ver"), int), r.text[:150])
check("API logout-all 后旧 token 再调 401",
      client.post("/me/logout-all", headers={"Authorization": f"Bearer {tok_api}"}).status_code == 401)
tok_new = api.auth_store.login("吊销测试员", "pass123")
check("API 侧重登后可用", client.post(
    "/me/logout-all", headers={"Authorization": f"Bearer {tok_new}"}).status_code == 200)

# ==================== 汇总 ====================
print(f"\n结果：{ok_count} passed, {fail_count} failed")
sys.exit(1 if fail_count else 0)
