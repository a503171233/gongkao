# -*- coding: utf-8 -*-
"""批次20 · 知识掌握度地图单测。

覆盖：
  1. 无练习记录 → level=none
  2. 3 题全对（≥80% 且 ≥3 题）→ strong
  3. 正确率 <60% → weak
  4. 正确率 60-79% → mid
  5. 高正确率但样本 <3 题 → mid（不给 strong）
  6. 词根跨字面命中（叶子「比重问题」→ 题库 knowledge_point「现期比重」）
  7. 同章节其他叶子的记录不串扰（叶子级独立匹配）
  8. API：未登录 401 / 登录 200 带 mastery+total_done

运行：cd backend && python tests/test_knowledge_mastery.py
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from poc.study import StudyStore  # noqa: E402
from poc.knowledge import KnowledgeStore  # noqa: E402

PASS = 0


def chk(name: str, cond: bool, extra: str = ""):
    global PASS
    assert cond, f"FAIL: {name} {extra}"
    PASS += 1
    print(f"  ok {name}")


TMP = tempfile.mkdtemp(prefix="gk-km-")
DB = Path(TMP) / "study.db"
st = StudyStore(db_path=DB)
ks = KnowledgeStore(db_path=DB)

# 建树：资料分析 / 核心公式 / [比重问题, 生疏考点, 无关考点]
root = ks.create_node("T999", "资料分析", category="模块")
ch = ks.create_node("T999", "核心公式", parent_id=root["node_id"], category="资料分析")
leaf_bz = ks.create_node("T999", "比重问题", parent_id=ch["node_id"], category="核心公式")
leaf_raw = ks.create_node("T999", "生疏考点", parent_id=ch["node_id"], category="核心公式")
leaf_none = ks.create_node("T999", "无关考点", parent_id=ch["node_id"], category="核心公式")

# 「比重」题集：knowledge_point 均含「比重」（跨字面：叶子名 vs kp 值不同）
bz_qids = []
for i, kp in enumerate(["现期比重", "现期比重比较", "基期比重"], start=1):
    q = st.add_question("T999", "choice", f"比重题{i}：甲占乙的比重？", "A",
                        options=["A. 0.2", "B. 0.4", "C. 0.5", "D. 0.8"],
                        analysis="比重=部分/整体", category="资料分析",
                        knowledge_point=kp, difficulty=3)
    bz_qids.append(q["id"])


def _submit(user, qid, right):
    st.submit_practice(user, "T999", f"qid-{qid}", "A" if right else "B",
                       question_id=qid)


print("== 1 无记录 → none ==")
m0 = st.knowledge_mastery("T999", "u_nobody", ks=ks)
chk("无记录叶子 none", m0["mastery"][leaf_bz["node_id"]]["level"] == "none")
chk("total_done=0", m0["total_done"] == 0)

print("== 2-6 分级与词根 ==")
# u_strong：3 题全对
for qid in bz_qids:
    _submit("u_strong", qid, True)
# u_weak：2 题对 0？→ 1 对 1 错（50%）
_submit("u_weak", bz_qids[0], True)
_submit("u_weak", bz_qids[1], False)
# u_mid：3 题 2 对 1 错（66.7%）
_submit("u_mid", bz_qids[0], True)
_submit("u_mid", bz_qids[1], True)
_submit("u_mid", bz_qids[2], False)
# u_few：2 题全对（100% 但样本<3）
_submit("u_few", bz_qids[0], True)
_submit("u_few", bz_qids[1], True)

m = st.knowledge_mastery("T999", "u_strong", ks=ks)["mastery"][leaf_bz["node_id"]]
chk("strong 全对", m["level"] == "strong" and m["done"] == 3 and m["accuracy"] == 1.0,
    str(m))
chk("词根跨字面（比重问题→现期比重）", m["done"] == 3, str(m))

m = st.knowledge_mastery("T999", "u_weak", ks=ks)["mastery"][leaf_bz["node_id"]]
chk("weak 50%", m["level"] == "weak" and m["accuracy"] == 0.5, str(m))

m = st.knowledge_mastery("T999", "u_mid", ks=ks)["mastery"][leaf_bz["node_id"]]
chk("mid 66.7%", m["level"] == "mid" and abs(m["accuracy"] - 0.6667) < 0.001, str(m))

m = st.knowledge_mastery("T999", "u_few", ks=ks)["mastery"][leaf_bz["node_id"]]
chk("样本<3 不给 strong", m["level"] == "mid" and m["accuracy"] == 1.0, str(m))

print("== 7 叶子级独立 ==")
m = st.knowledge_mastery("T999", "u_strong", ks=ks)["mastery"]
chk("同章节无记录叶子 none", m[leaf_none["node_id"]]["level"] == "none")
chk("生疏叶子 none", m[leaf_raw["node_id"]]["level"] == "none")

# ============ API 层 ============
API_TMP = tempfile.mkdtemp(prefix="gk-km-api-")
os.environ["STUDY_DB"] = os.path.join(API_TMP, "study.db")
os.environ["CHAT_DB"] = os.path.join(API_TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(API_TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(API_TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(API_TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["AUTH_DB"] = os.path.join(API_TMP, "auth.db")

import poc.api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(poc.api.app)

print("== 8 API 层 ==")
r = client.post("/register", json={"username": "掌握度学生", "password": "secret123"})
tok = r.json().get("token", "")
chk("注册拿 token", bool(tok))
uid = client.get("/me", headers={"Authorization": f"Bearer {tok}"}).json().get("user_id", "")
chk("拿到 user_id", bool(uid), str(uid))

r1 = client.get("/knowledge/mastery?teacher_id=T888")
chk("未登录 401", r1.status_code == 401, str(r1.status_code))

a_ks = poc.api.knowledge_store if hasattr(poc.api, "knowledge_store") else None
if a_ks is None:
    from poc.knowledge import get_knowledge_store
    a_ks = get_knowledge_store()
aroot = a_ks.create_node("T888", "言语理解与表达", category="模块")
aleaf = a_ks.create_node("T888", "主旨概括", parent_id=aroot["node_id"], category="言语理解与表达")
aq = poc.api.study_store.add_question("T888", "choice", "主旨题1：意在说明？", "A",
                                      options=["A. 甲", "B. 乙", "C. 丙", "D. 丁"],
                                      analysis="找中心句", category="言语理解",
                                      knowledge_point="主旨概括题", difficulty=2)
poc.api.study_store.submit_practice(uid, "T888", f"qid-{aq['id']}", "A",
                                    question_id=aq["id"])

r2 = client.get("/knowledge/mastery?teacher_id=T888",
                headers={"Authorization": f"Bearer {tok}"})
chk("登录后 200", r2.status_code == 200, f"{r2.status_code} {r2.text[:120]}")
d2 = r2.json()
chk("mastery 含叶子", aleaf["node_id"] in d2.get("mastery", {}), str(d2)[:200])
chk("total_done 统计", d2.get("total_done", 0) >= 1, str(d2.get("total_done")))

print(f"\n===== 通过 {PASS} 项 =====")
if PASS < 14:
    print("断言数不足，检查是否有用例被跳过")
    sys.exit(1)
print("ALL PASS")
