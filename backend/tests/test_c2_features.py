# -*- coding: utf-8 -*-
"""C2 自测脚本（不启动 FastAPI，直接测业务逻辑层）。
运行：cd backend && python tests/test_c2_features.py
"""
import os, sys, sqlite3, tempfile, shutil
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-c2-test-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["AUTH_DB"] = os.path.join(TMP, "auth.db")
os.environ["DATA_DIR"] = TMP
os.environ["EMBED_PROVIDER"] = "selftest"

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        print(f"  ✅ {name}")
        PASS += 1
    else:
        print(f"  ❌ {name}")
        if detail:
            print(f"     {detail}")
        FAIL += 1

def section(title):
    print(f"\n{'='*50}")
    print(f"  {title}")
    print("=" * 50)

# ============================================================
# 1. Config 老师注册表运行时化
# ============================================================
section("1. 老师注册表运行时化（config.py）")

from poc.config import Config, _BUILTIN_TEACHERS

cfg = Config()

# 1.1 内存缓存已加载（内置 T001/T002）
check("内存缓存已加载", list(Config._teachers_cache.keys()) == ['T001', 'T002'])

# 1.2 list_teachers() 前台返回结构兼容（仅 teacher_id/name/subject）
t_list = cfg.list_teachers()
check("list_teachers() 返回 list", isinstance(t_list, list))
check("前台老师数量 = 2（T001+T002 都在线）", len(t_list) == 2, f"实际 {len(t_list)}")
check("每个条目含 teacher_id", all("teacher_id" in t for t in t_list))
check("每个条目含 teacher_name", all("teacher_name" in t for t in t_list))
check("每个条目含 teacher_subject", all("teacher_subject" in t for t in t_list))
check("无 enabled 字段（前台结构不变）", all("enabled" not in t for t in t_list))
check("无 llm_model 字段（前台结构不变）", all("llm_model" not in t for t in t_list))

# 1.3 list_teachers_all() 后台返回全量
t_all = cfg.list_teachers_all()
check("后台全量含 enabled", all("enabled" in t for t in t_all))
check("后台全量含 llm_model", all("llm_model" in t for t in t_all))
check("enabled 字段类型为 bool", all(isinstance(t["enabled"], bool) for t in t_all))

# 1.4 get_teacher() 返回 TeacherCfg，字段兼容
t1 = cfg.get_teacher("T001")
check("get_teacher() 返回 TeacherCfg（namedtuple）", type(t1).__name__ == "TeacherCfg")
check("teacher_id", t1.teacher_id == "T001")
check("teacher_name", t1.teacher_name == "星辰老师")
check("teacher_subject", t1.teacher_subject == "言语理解")
check("temperature", t1.temperature == 0.3)
check("top_n", t1.top_n == 6)
check("threshold", t1.threshold == 0.5)
check("chunk_size（全局默认值）", t1.chunk_size == Config.chunk_size)
check("chunk_overlap（全局默认值）", t1.chunk_overlap == Config.chunk_overlap)

# 1.5 enabled=false 老师不在前台列表，但 get_teacher 可取
# 模拟下线
import datetime
with Config._teachers_connect() as conn:
    conn.execute(
        "UPDATE teachers SET enabled=0, updated_at=? WHERE teacher_id='T001'",
        (datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),)
    )
Config.reload_teachers()

t_list_after_disable = cfg.list_teachers()
check("前台过滤：T001 下线后不在列表", all(t["teacher_id"] != "T001" for t in t_list_after_disable))
check("前台过滤：T002 仍在列表", any(t["teacher_id"] == "T002" for t in t_list_after_disable))

try:
    t1_disabled = cfg.get_teacher("T001")
    check("get_teacher 仍可取已下线老师（B3/B4 兼容）", t1_disabled.teacher_id == "T001")
except Exception as e:
    check("get_teacher 仍可取已下线老师", False, str(e))

# 恢复 T001
with Config._teachers_connect() as conn:
    conn.execute(
        "UPDATE teachers SET enabled=1, updated_at=? WHERE teacher_id='T001'",
        (datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),)
    )
Config.reload_teachers()

# 1.6 不存在的老师抛 ValueError
try:
    cfg.get_teacher("T999")
    check("不存在的老师抛 ValueError", False)
except ValueError as e:
    check("不存在的老师抛 ValueError", "T999" in str(e))

# ============================================================
# 2. auth.py admin 角色 + member_expire_at
# ============================================================
section("2. admin 角色（auth.py）")

from poc.auth import AuthStore, AuthStore as _Auth

auth = AuthStore()

# 2.1 member_expire_at 列存在
with auth._connect() as conn:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
check("users 表含 member_expire_at 列", "member_expire_at" in cols)
check("users 表含 role 列", "role" in cols)

# 2.2 ensure_admin() 行为（无环境变量时返回 None）
uid = auth.ensure_admin()
check("无 ADMIN_USERNAME 时 ensure_admin 返回 None", uid is None)

# 2.3 register() 默认 role=free
u1_id = auth.register("c2_test_user1", "pass123")
u1 = auth.get_user(u1_id)
check("新用户默认 role=free", u1["role"] == "free")
check("新用户 member_expire_at 为空", not u1.get("member_expire_at"))

# 2.4 set_role → member
auth.set_role(u1_id, "member")
u2 = auth.get_user(u1_id)
check("set_role('member') 写入 role", u2["role"] == "member")
check("set_role('member') 写入 member_expire_at", bool(u2.get("member_expire_at")))

# 2.5 set_role → free
auth.set_role(u1_id, "free")
u3 = auth.get_user(u1_id)
check("set_role('free') 写回 free", u3["role"] == "free")

# 2.6 get_membership_info
info = auth.get_membership_info(u1_id)
check("get_membership_info 返回 is_member", "is_member" in info)
check("get_membership_info 返回 days_remaining", "days_remaining" in info)
check("free 用户 days_remaining = 0", info["days_remaining"] == 0)

# ============================================================
# 3. admin.py 业务逻辑
# ============================================================
section("3. 老师 CRUD（admin.py）")

from poc import admin as admin_biz

# 3.1 新增老师
new_t = admin_biz.create_teacher({
    "teacher_id": "T003",
    "teacher_name": "测试老师",
    "teacher_subject": "资料分析",
    "llm_model": "gpt-5.6-luna",
    "temperature": 0.4,
    "top_n": 8,
    "threshold": 0.45,
})
check("create_teacher 返回 teacher_id", new_t["teacher_id"] == "T003")
check("create_teacher 返回 teacher_name", new_t["teacher_name"] == "测试老师")
check("create_teacher 返回 enabled=True", new_t["enabled"] == True)
check("create_teacher 写入 DB", "T003" in Config._teachers_cache)

# 3.2 前台列表含 T003
t_list_with_t3 = cfg.list_teachers()
check("前台新增后 T003 出现", any(t["teacher_id"] == "T003" for t in t_list_with_t3))

# 3.3 编辑老师
updated = admin_biz.update_teacher("T003", {
    "teacher_name": "测试老师_v2",
    "temperature": 0.7,
    "top_n": 5,
})
check("update_teacher 返回更新后字段", updated["teacher_name"] == "测试老师_v2")
check("update_teacher 写入 DB", Config._teachers_cache["T003"]["teacher_name"] == "测试老师_v2")

# 3.4 字段校验
for bad in [
    ({}, "空 payload"),
    ({"teacher_id": ""}, "空 teacher_id"),
    ({"teacher_id": "T001", "teacher_name": "", "teacher_subject": "x"}, "空 name"),
    ({"teacher_id": "T001", "teacher_name": "n", "teacher_subject": "x", "temperature": 5.0}, "temperature 超范围"),
    ({"teacher_id": "T001", "teacher_name": "n", "teacher_subject": "x", "top_n": 0}, "top_n < 1"),
    ({"teacher_id": "T001", "teacher_name": "n", "teacher_subject": "x", "threshold": 2.0}, "threshold 超范围"),
]:
    try:
        admin_biz._validate_teacher_payload(bad[0])
        check(f"校验拒绝: {bad[1]}", False)
    except ValueError:
        check(f"校验拒绝: {bad[1]}", True)

# 3.5 重复 teacher_id 拒绝
try:
    admin_biz.create_teacher({"teacher_id": "T003", "teacher_name": "重复", "teacher_subject": "x"})
    check("重复 teacher_id 拒绝", False)
except ValueError as e:
    check("重复 teacher_id 拒绝", "已存在" in str(e))

# 3.6 软下线
disabled = admin_biz.disable_teacher("T003")
check("disable_teacher 返回 enabled=False", disabled["enabled"] == False)
check("软下线后 T003 不在前台", all(t["teacher_id"] != "T003" for t in cfg.list_teachers()))
check("软下线后 get_teacher 仍可取", cfg.get_teacher("T003").teacher_id == "T003")

# 3.7 恢复上线
enabled = admin_biz.enable_teacher("T003")
check("enable_teacher 返回 enabled=True", enabled["enabled"] == True)
check("恢复后 T003 重现前台", any(t["teacher_id"] == "T003" for t in cfg.list_teachers()))

# 3.8 不存在的老师操作抛 ValueError
for fn, args in [
    (admin_biz.update_teacher, ("T999", {})),
    (admin_biz.disable_teacher, ("T999",)),
    (admin_biz.enable_teacher, ("T999",)),
]:
    try:
        fn(*args)
        check(f"不存在老师操作抛异常: {fn.__name__}", False)
    except ValueError:
        check(f"不存在老师操作抛异常: {fn.__name__}", True)

# ============================================================
# 4. 用户管理
# ============================================================
section("4. 用户管理（admin.py）")

u_test_id = auth.register("c2_user_admin_test", "pass123")
u_test = auth.get_user(u_test_id)

# 4.1 list_users_admin
users = admin_biz.list_users_admin()
check("list_users_admin 返回 list", isinstance(users, list))
check("含刚注册用户", any(u["username"] == "c2_user_admin_test" for u in users))

# 4.2 get_user_admin
u_detail = admin_biz.get_user_admin(u_test_id)
check("get_user_admin 返回 dict", isinstance(u_detail, dict))
check("含 role 字段", "role" in u_detail)
check("含 created_at", "created_at" in u_detail)

# 4.3 set_role → member（30天）
m = admin_biz.set_user_role(u_test_id, "member", days=30)
check("set_role('member') role=member", m["role"] == "member")
check("set_role('member') 写 member_expire_at", bool(m.get("member_expire_at")))
info2 = auth.get_membership_info(u_test_id)
check("会员 days_remaining > 0", info2["days_remaining"] > 0)

# 4.4 set_role → free
f = admin_biz.set_user_role(u_test_id, "free")
check("set_role('free') role=free", f["role"] == "free")

# 4.5 grant days
g = admin_biz.grant_user(u_test_id, "days", 7)
check("grant_user('days') 写 member_expire_at", bool(g.get("member_expire_at")))
info3 = auth.get_membership_info(u_test_id)
check("grant 7 天后 days_remaining >= 7", info3["days_remaining"] >= 7)

# 4.6 grant reset_today
with auth._connect() as conn:
    conn.execute("UPDATE users SET today_count=5 WHERE user_id=?", (u_test_id,))
    conn.commit()
r = admin_biz.grant_user(u_test_id, "reset_today")
u4 = auth.get_user(u_test_id)
check("grant reset_today 清零已用次数", u4.get("today_count", 0) == 0)

# 4.7 非法 role
for illegal in ["superadmin", "vip", ""]:
    try:
        admin_biz.set_user_role(u_test_id, illegal)
        check(f"非法 role '{illegal}' 拒绝", False)
    except ValueError:
        check(f"非法 role '{illegal}' 拒绝", True)

# ============================================================
# 5. admin_stats
# ============================================================
section("5. 统计（admin_stats）")

stats = admin_biz.admin_stats()
check("stats 含 users.total", "total" in stats.get("users", {}))
check("stats 含 teachers.total", "total" in stats.get("teachers", {}))
check("stats 含 teachers.enabled", "enabled" in stats.get("teachers", {}))
check("stats 含 knowledge.total_chunks", "total_chunks" in stats.get("knowledge", {}))
check("stats.users.total >= 1", stats["users"]["total"] >= 1)

# ============================================================
# 6. B3/B4 兼容：get_teacher 结构不变
# ============================================================
section("6. B3/B4 兼容：get_teacher 结构")

tcfg = cfg.get_teacher("T001")
# 验证所有原有字段都存在
compat_fields = [
    "teacher_id", "teacher_name", "teacher_subject",
    "llm_model", "temperature", "top_n", "threshold",
    "chunk_size", "chunk_overlap",
]
for f in compat_fields:
    check(f"TeacherCfg.{f} 存在", hasattr(tcfg, f))

# ============================================================
# 清理 + 汇总
# ============================================================
shutil.rmtree(TMP, ignore_errors=True)

section("自测汇总")
print(f"\n  ✅ PASS: {PASS}")
print(f"  ❌ FAIL: {FAIL}")
print(f"  总计:   {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
