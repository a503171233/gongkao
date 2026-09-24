# -*- coding: utf-8 -*-
"""批次3：收藏分组 + DELETE /documents 注册表联动。

运行：cd backend && python tests/test_favorites_group.py
覆盖：
  1) 老库平滑迁移：无 fav_group 列的 favorites 表打开后自动补列、旧数据不丢；
  2) store 层：add_favorite(group) / list_favorites 筛选(None=全部、''=未分组、
     组名=精确筛选) / set_favorite_group / list_favorite_groups；
  3) API 层：POST /favorites(group)、GET /favorites?group、GET /favorites/groups、
     PUT /favorites/{id}/group（含 404 分支）；
  4) DELETE /documents/{doc_name} 联动清注册表（registry_deleted，故障不阻断）。
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk_fav_group_")
os.environ["STUDY_DB"] = str(Path(TMP) / "study.db")
os.environ["DATA_DIR"] = str(Path(TMP) / "data")
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


from poc.study import StudyStore  # noqa: E402

# ==================== 1) 老库平滑迁移 ====================
old_db = Path(TMP) / "old_study.db"
_conn = sqlite3.connect(str(old_db))
_conn.executescript("""
CREATE TABLE favorites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    teacher_id  TEXT NOT NULL,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    refs        TEXT DEFAULT '',
    session_id  TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);
INSERT INTO favorites(user_id, teacher_id, question, answer, refs, session_id, created_at)
VALUES ('u-old', 'T001', '老收藏题', '老答案', '', '', '2026-01-01T00:00:00Z');
""")
_conn.commit()
_conn.close()

study = StudyStore(db_path=old_db)  # 打开即触发平滑迁移
_cols = [r[1] for r in sqlite3.connect(str(old_db)).execute("PRAGMA table_info(favorites)")]
check("老库打开后自动补 fav_group 列", "fav_group" in _cols, _cols)
check("迁移后旧数据不丢", len(study.list_favorites("u-old")) == 1)

# 重复打开（列已存在）不报错
study2 = StudyStore(db_path=old_db)
check("重复打开幂等", len(study2.list_favorites("u-old")) == 1)

# ==================== 2) store 层 ====================
db = Path(TMP) / "study.db"
st = StudyStore(db_path=db)

f1 = st.add_favorite("u-api", "T001", "题1", "答1", group="行测重点")
f2 = st.add_favorite("u-api", "T001", "题2", "答2")  # 未分组
f3 = st.add_favorite("u-api", "T001", "题3", "答3", group="申论素材")
check("add_favorite 返回 fav_group", f1["fav_group"] == "行测重点" and f2["fav_group"] == "")

check("不筛选=全部（旧行为）", len(st.list_favorites("u-api")) == 3)
check("筛选组名=精确匹配", [x["question"] for x in st.list_favorites("u-api", group="行测重点")] == ["题1"])
check("筛选空串=仅未分组", [x["question"] for x in st.list_favorites("u-api", group="")] == ["题2"])

check("set_favorite_group 改组", st.set_favorite_group("u-api", f2["id"], "行测重点")
      and len(st.list_favorites("u-api", group="行测重点")) == 2)
check("set_favorite_group 清组", st.set_favorite_group("u-api", f2["id"], "")
      and st.list_favorites("u-api", group="") == [x for x in st.list_favorites("u-api") if x["id"] == f2["id"]])
check("set_favorite_group 越权无效", st.set_favorite_group("u-other", f1["id"], "x") is False)
check("组名超长截断50", (lambda: (st.set_favorite_group("u-api", f1["id"], "组" * 60),
                                 len(st.list_favorites("u-api", group="组" * 50)) == 1)
                         and st.list_favorites("u-api", group="组" * 50)[0]["fav_group"] == "组" * 50)())
st.set_favorite_group("u-api", f1["id"], "行测重点")  # 恢复组名（免污染后续断言）

check("list_favorite_groups 去重排序", st.list_favorite_groups("u-api") == ["申论素材", "行测重点"])
check("组名组内空白串不入列表", st.set_favorite_group("u-api", f3["id"], "  ") is True
      and "  " not in st.list_favorite_groups("u-api"))

# ==================== 3) API 层 ====================
import poc.api as api  # noqa: E402


def fake_resolve(authorization):
    t = (authorization or "").replace("Bearer ", "", 1).strip()
    if t == "tok-a":
        return {"user_id": "u-api", "username": "测试用户", "role": "free"}
    return None


_orig_resolve = api._resolve_user
api._resolve_user = fake_resolve
client = TestClient(api.app)
h = {"Authorization": "Bearer tok-a"}
try:
    check("GET /favorites 鉴权 401", client.get("/favorites").status_code == 401)

    r = client.post("/favorites", headers=h,
                    json={"question": "API题", "answer": "API答", "group": "行测重点"})
    check("POST /favorites 带 group", r.status_code == 200 and r.json()["fav_group"] == "行测重点", r.text[:120])
    fid_new = r.json()["id"]

    r = client.get("/favorites?group=__none__", headers=h)
    check("GET /favorites?group=__none__ 仅未分组",
          all(x["fav_group"] == "" for x in r.json()["favorites"]) and r.json()["favorites"], r.text[:120])

    r = client.get("/favorites?group=行测重点", headers=h)
    check("GET /favorites?group=组名", all(x["fav_group"] == "行测重点" for x in r.json()["favorites"])
          and len(r.json()["favorites"]) >= 2)

    r = client.get("/favorites/groups", headers=h)
    check("GET /favorites/groups", "行测重点" in r.json()["groups"], r.text[:120])

    r = client.put(f"/favorites/{fid_new}/group", headers=h, json={"group": ""})
    check("PUT group 清组成功", r.status_code == 200 and r.json()["success"] is True)

    r = client.put("/favorites/999999/group", headers=h, json={"group": "x"})
    check("PUT group 不存在 404", r.status_code == 404)

    r = client.put(f"/favorites/{fid_new}/group", headers=h)  # 缺 body
    check("PUT group 缺 body 422", r.status_code == 422)
finally:
    api._resolve_user = _orig_resolve

# ==================== 4) DELETE /documents 注册表联动 ====================
import poc.docreg as _docreg_mod  # noqa: E402


class _StubStore:
    def delete_document(self, teacher_id, doc_name):
        return 7

    def collection_count(self, teacher_id):
        return 0

    def list_documents(self, teacher_id):
        return []


class _StubRegistry:
    def __init__(self, boom=False):
        self.calls = []
        self.boom = boom

    def delete(self, teacher_id, doc_name):
        self.calls.append((teacher_id, doc_name))
        if self.boom:
            raise RuntimeError("registry down")
        return True


_orig_store, _orig_admin, _orig_gdr = api.store, api._admin_user, _docreg_mod.get_doc_registry
try:
    api.store = _StubStore()
    api._admin_user = lambda a: {"user_id": "admin-x"}

    reg = _StubRegistry()
    _docreg_mod.get_doc_registry = lambda: reg
    r = client.delete("/documents/old-doc.md?teacher_id=T001", headers=h)
    check("DELETE /documents 联动清注册表",
          r.status_code == 200 and r.json()["registry_deleted"] is True
          and reg.calls == [("T001", "old-doc.md")], r.text[:160])

    reg2 = _StubRegistry(boom=True)
    _docreg_mod.get_doc_registry = lambda: reg2
    r = client.delete("/documents/old-doc.md?teacher_id=T001", headers=h)
    check("注册表故障不阻断删除", r.status_code == 200
          and r.json()["registry_deleted"] is False and r.json()["deleted"] == 7, r.text[:160])
finally:
    api.store, api._admin_user, _docreg_mod.get_doc_registry = _orig_store, _orig_admin, _orig_gdr

print(f"\n总计: 通过 {ok_count} · 失败 {fail_count}")
sys.exit(0 if fail_count == 0 else 1)
