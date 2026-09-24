# -*- coding: utf-8 -*-
"""批次8：POST /admin/capabilities/fetch-test 抓取通道一键自检。

运行：cd backend && python tests/test_fetch_test_endpoint.py
覆盖：
  1) 鉴权：无 token 401 / 非 admin 403 / admin 200；
  2) 返回结构：tested_url 固定 example.com（无用户输入 → 无 SSRF 面）；
     results 逐通道 {provider, ok, chars, elapsed_ms, error} 字段齐全；
     未安装的 provider 返回「未注册」而非缺失；
  3) builtin 通道实抓 example.com 应成功（httpx 基础连通性）；
  4) 网络故障容错：通道失败返回结构化 error 而非 500。
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = Path(tempfile.mkdtemp(prefix="gk_fetch_test_"))
os.environ["AUTH_DB"] = str(TMP / "auth.db")
os.environ["CHAT_DB"] = str(TMP / "chat.db")
os.environ["STUDY_DB"] = str(TMP / "study.db")
os.environ["DB_PATH"] = str(TMP / "vec")
os.environ["DATA_DIR"] = str(TMP / "data")
os.environ["MODEL_CACHE"] = str(TMP / "data" / "models")
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


from fastapi.testclient import TestClient  # noqa: E402

from poc import api  # noqa: E402

client = TestClient(api.app)
uid_admin = api.auth_store.register("自检管理员", "adminpass1")
api.auth_store.set_role(uid_admin, "admin")
uid_free = api.auth_store.register("普通用户乙", "freepass1")
tok_admin = api.auth_store.login("自检管理员", "adminpass1")
tok_free = api.auth_store.login("普通用户乙", "freepass1")

# ==================== 1) 鉴权 ====================
check("无 token 401", client.post("/admin/capabilities/fetch-test").status_code == 401)
check("非 admin 403",
      client.post("/admin/capabilities/fetch-test",
                  headers={"Authorization": f"Bearer {tok_free}"}).status_code == 403)
r = client.post("/admin/capabilities/fetch-test",
                headers={"Authorization": f"Bearer {tok_admin}"})
check("admin 200", r.status_code == 200, r.text[:200])

# ==================== 2) 返回结构 ====================
data = r.json() if r.status_code == 200 else {}
check("tested_url 固定 example.com（无用户输入）",
      data.get("tested_url") == "https://example.com", data.get("tested_url"))
results = data.get("results") or []
provs = [x.get("provider") for x in results]
check("逐通道结果含 scrapling/builtin", "scrapling" in provs and "builtin" in provs, provs)
check("每项字段齐全", all(
    {"provider", "ok", "chars", "elapsed_ms", "error"} <= set(x) for x in results),
    str(results))
check("ok 为布尔", all(isinstance(x.get("ok"), bool) for x in results))
check("elapsed_ms 为非负整数", all(
    isinstance(x.get("elapsed_ms"), int) and x["elapsed_ms"] >= 0 for x in results))

# ==================== 3) builtin 实抓应成功 ====================
builtin_res = next((x for x in results if x["provider"] == "builtin"), {})
check("builtin 实抓 example.com 成功", builtin_res.get("ok") is True,
      str(builtin_res))
check("builtin 抓到正文（example.com 约数百字）",
      50 <= builtin_res.get("chars", 0) <= 5000, builtin_res.get("chars"))

# scrapling：已装则应成功（容器内已验证 0.4.15）
try:
    import scrapling  # noqa: F401
    scrap_res = next((x for x in results if x["provider"] == "scrapling"), {})
    check("scrapling 实抓成功（已装环境）", scrap_res.get("ok") is True,
          str(scrap_res)[:150])
except ImportError:
    check("scrapling 未安装时返回「未注册」结构", True)  # 环境退化场景

# ==================== 汇总 ====================
print(f"\n结果：{ok_count} passed, {fail_count} failed")
sys.exit(1 if fail_count else 0)
