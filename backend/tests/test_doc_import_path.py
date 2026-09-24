# -*- coding: utf-8 -*-
"""#36 文档按路径导入契约测试：浏览白名单根/防穿越/导入入库/非法类型/非管理员。

运行：cd backend && python tests/test_doc_import_path.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TMP = Path(tempfile.mkdtemp(prefix="gk-docimport-"))
os.environ["DATA_DIR"] = str(TMP)          # 必须在 import poc.api 之前设置（Config 读 env）
os.environ["CHAT_DB"] = str(TMP / "chat.db")
os.environ["DB_PATH"] = str(TMP / "vector")
os.environ["MODEL_CACHE"] = str(TMP / "models")
os.environ["EMBED_PROVIDER"] = "selftest"   # 离线哈希嵌入，零网络

import poc.api as api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def fake_admin_user(authorization):
    """patch _admin_user（管理端鉴权点）：tok-admin → admin 用户。"""
    from fastapi import HTTPException
    t = (authorization or "").replace("Bearer ", "", 1).strip()
    if t == "tok-admin":
        return {"user_id": "u-x", "username": "管理员", "role": "admin"}
    if not t:
        raise HTTPException(status_code=401, detail="请先登录")
    raise HTTPException(status_code=401, detail="无效 Token")


api._admin_user = fake_admin_user
client = TestClient(api.app)
H = {"Authorization": "Bearer tok-admin"}
ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


# 默认根 = DATA_DIR/import
imp = api._import_roots()[0]

# 1) 非管理员拒绝
check("browse 未登录 401/403", client.get("/admin/documents/browse").status_code in (401, 403))
check("import 未登录 401/403",
      client.post("/admin/documents/import-path", json={"path": "x"}).status_code in (401, 403))

# 2) 根浏览（自动创建 /data/import）
r = client.get("/admin/documents/browse", headers=H)
check("browse 根 200", r.status_code == 200, str(r.status_code))
d = r.json()
check("browse at_root=True 且列出根", d.get("at_root") is True and str(imp) in d.get("roots", []), str(d)[:120])
check("browse 默认根已自动 mkdir", imp.is_dir())

# 3) 放一个合法文件 + 一个非法文件 + 子目录
sub = imp / "真题库"
sub.mkdir()
f_ok = sub / "宪法题.md"
f_ok.write_text("# 宪法题\n根据《宪法》规定，行使国家立法权的机关是全国人民代表大会。（解析略）\n"
                "A. 全国人大 B. 国务院 C. 中央军委 D. 监察委\n答案：A", encoding="utf-8")
f_bad = imp / "readme.exe"
f_bad.write_bytes(b"MZ123")

r = client.get(f"/admin/documents/browse?path={imp}", headers=H)
check("browse 子目录 200", r.status_code == 200, str(r.status_code))
d = r.json()
names = [e["name"] for e in d["entries"]]
check("browse 列出子目录", "真题库" in names, str(names))
check("browse 过滤非白名单扩展名", "readme.exe" not in names, str(names))

r = client.get(f"/admin/documents/browse?path={sub}", headers=H)
check("browse 进子目录见文件", "宪法题.md" in [e["name"] for e in r.json()["entries"]])
check("browse parent 可回溯根", str(imp) in (r.json()["parent"] or ""))

# 4) 防穿越：根外绝对路径 → 403；拼接 .. → 403
check("browse 根外 403",
      client.get("/admin/documents/browse?path=/etc", headers=H).status_code == 403)
check("browse .. 穿越 403",
      client.get(f"/admin/documents/browse?path={imp}/../../etc", headers=H).status_code == 403)

# 5) 导入路径文件（真实分块+入库）
r = client.post("/admin/documents/import-path",
                json={"teacher_id": "T001", "path": str(f_ok), "category": "行测"}, headers=H)
check("import 200", r.status_code == 200, str(r.status_code) + " " + str(r.json())[:150])
j = r.json()
check("import 返回块数>0", j.get("chunks", 0) > 0, str(j)[:150])
check("import filename 落库名", j.get("filename") == "宪法题.md", str(j)[:150])

# 6) 非白名单扩展名 → 400
check("import .exe 400",
      client.post("/admin/documents/import-path",
                  json={"teacher_id": "T001", "path": str(f_bad)}, headers=H).status_code == 400)
# 7) 不存在的文件 → 404；空 path → 400
check("import 不存在 404",
      client.post("/admin/documents/import-path",
                  json={"teacher_id": "T001", "path": str(imp / "无.pdf")}, headers=H).status_code == 404)
check("import 空path 400",
      client.post("/admin/documents/import-path", json={"teacher_id": "T001"}, headers=H).status_code == 400)
# 8) 穿越攻击 path → 403
check("import 穿越 403",
      client.post("/admin/documents/import-path",
                  json={"teacher_id": "T001", "path": "/etc/passwd"}, headers=H).status_code == 403)

print("\nRESULT:", "ALL PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
