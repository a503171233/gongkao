# -*- coding: utf-8 -*-
"""用户账号/会员额度 · 单元测试（RED→GREEN）
运行: cd backend && python tests/test_auth.py
覆盖: 注册/登录/密码哈希/token签发/额度区分(free vs member)/每日限额/超额拒绝
"""
import os
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-auth-test-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["AUTH_DB"] = os.path.join(TMP, "auth.db")

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


from poc.auth import AuthStore, FREE_DAILY, MEMBER_DAILY  # noqa: E402

a = AuthStore()

# 1) 注册
uid = a.register("星辰学生", "pass123")
check("注册返回 user_id", bool(uid), uid)
check("注册后能查到用户", a.get_user(uid)["username"] == "星辰学生")
check("免费用户默认 role=free", a.get_user(uid)["role"] == "free")

# 2) 重复用户名拒绝
try:
    a.register("星辰学生", "xxx")
    check("重复用户名被拒", False)
except ValueError:
    check("重复用户名被拒", True)

# 3) 登录（密码正确/错误）
tok = a.login("星辰学生", "pass123")
check("登录成功发 token", bool(tok))
try:
    a.login("星辰学生", "wrong")
    check("错误密码登录被拒", False)
except ValueError:
    check("错误密码登录被拒", True)

# 4) token 校验
check("token 有效解析出用户", a.user_from_token(tok)["username"] == "星辰学生")
check("伪造 token 无效", a.user_from_token("fake-token") is None)

# 5) 额度区分：free 每日限额 vs member
role = a.get_user(uid)["role"]
daily = FREE_DAILY if role == "free" else MEMBER_DAILY
check(f"free 每日额度 {FREE_DAILY} / member {MEMBER_DAILY}", FREE_DAILY < MEMBER_DAILY)

# 6) 额度消耗与超额
check("初始未超限", a.check_quota(uid, role) is True)
# 模拟耗尽（把 today_count 推到上限）
import sqlite3
conn = sqlite3.connect(os.environ["AUTH_DB"])
conn.execute("UPDATE users SET today_count=?, quota_date=? WHERE user_id=?",
             (daily, time.strftime("%Y-%m-%d"), uid))
conn.commit()
conn.close()
check("额度耗尽后超限", a.check_quota(uid, role) is False)
check("额度耗尽返回剩余 0", a.quota_left(uid, role) == 0)

# 7) 跨天重置（把 quota_date 改成昨天）
import sqlite3
conn = sqlite3.connect(os.environ["AUTH_DB"])
conn.execute("UPDATE users SET quota_date=? WHERE user_id=?",
             ("2000-01-01", uid))
conn.commit()
conn.close()
check("跨天自动重置额度", a.check_quota(uid, role) is True)
check("重置后剩余=每日额度", a.quota_left(uid, role) == daily)

# 8) 消耗一次
a.consume(uid)
check("消耗后剩余=每日额度-1", a.quota_left(uid, role) == daily - 1)

# 9) 密码哈希非明文
import sqlite3
conn = sqlite3.connect(os.environ["AUTH_DB"])
row = conn.execute("SELECT password_hash FROM users WHERE user_id=?", (uid,)).fetchone()
conn.close()
check("密码以哈希存储非明文", row and row[0] != "pass123" and len(row[0]) >= 32, str(row)[:60])

# 10) 角色升级（会员）
a.set_role(uid, "member")
check("升级会员后 role=member", a.get_user(uid)["role"] == "member")

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)