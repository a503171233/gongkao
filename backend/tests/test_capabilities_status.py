# -*- coding: utf-8 -*-
"""批次6-B：GET /admin/capabilities/status 能力层只读状态端点。

运行：cd backend && python tests/test_capabilities_status.py
覆盖：
  1) 鉴权：无 token 401 / 无效 token 401 / 非 admin 403 / admin 200；
  2) 返回结构：capabilities.fetch 含 mode/builtin/mcp/scrapling 就绪面；
     fetch_provider_env 与环境变量一致（缺省回 auto）；
  3) scrapling 就绪面与环境一致：已安装 → True（本容器已装）。
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk_cap_status_")
os.environ["AUTH_DB"] = os.path.join(TMP, "auth.db")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["STUDY_DB"] = os.path.join(TMP, "study.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["CAP_FETCH_PROVIDER"] = "auto"

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


from fastapi.testclient import TestClient  # noqa: E402

from poc import api  # noqa: E402

client = TestClient(api.app)

# 准备 admin + 普通用户（真 token，共享 AUTH_DB）
uid_admin = api.auth_store.register("状态管理员", "adminpass1")
api.auth_store.set_role(uid_admin, "admin")
uid_free = api.auth_store.register("普通用户甲", "freepass1")
tok_admin = api.auth_store.login("状态管理员", "adminpass1")
tok_free = api.auth_store.login("普通用户甲", "freepass1")

# ==================== 1) 鉴权 ====================
check("无 token 401", client.get("/admin/capabilities/status").status_code == 401)
check("无效 token 401",
      client.get("/admin/capabilities/status",
                 headers={"Authorization": "Bearer fake-token"}).status_code == 401)
check("非 admin 403",
      client.get("/admin/capabilities/status",
                 headers={"Authorization": f"Bearer {tok_free}"}).status_code == 403)
r = client.get("/admin/capabilities/status",
               headers={"Authorization": f"Bearer {tok_admin}"})
check("admin 200", r.status_code == 200, r.text[:150])

# ==================== 2) 返回结构 ====================
data = r.json() if r.status_code == 200 else {}
fetch = (data.get("capabilities") or {}).get("fetch") or {}
check("fetch.mode 存在", "mode" in fetch, fetch)
check("fetch.builtin 就绪面为布尔", isinstance(fetch.get("builtin"), bool), fetch)
check("fetch.mcp 就绪面为布尔", isinstance(fetch.get("mcp"), bool), fetch)
check("fetch.scrapling 就绪面为布尔", isinstance(fetch.get("scrapling"), bool), fetch)
check("fetch_provider_env 与 env 一致", data.get("fetch_provider_env") == "auto",
      data.get("fetch_provider_env"))
check("builtin 就绪（恒注册）", fetch.get("builtin") is True, fetch)

# ==================== 3) scrapling 就绪面与环境一致 ====================
try:
    import scrapling  # noqa: F401
    _installed = True
except Exception:
    _installed = False
check("scrapling 就绪面与实际安装一致", fetch.get("scrapling") is _installed,
      f"status={fetch.get('scrapling')} installed={_installed}")

# ==================== 汇总 ====================
print(f"\n结果：{ok_count} passed, {fail_count} failed")
sys.exit(1 if fail_count else 0)
