# -*- coding: utf-8 -*-
"""批次19 · 知识体系 → 练习联动单测。

覆盖：
  A. store 层
    1. _kp_terms 词根：链式剥后缀（问题/策略/题）、按「与」拆分、短碎片丢弃、空名
    2. L1 知识点级命中（knowledge_point LIKE，如「比重问题」→「现期比重」）
    3. L2 章节级回退（知识点不命中 → 章节名词根）
    4. L3 模块级兜底（模块名映射 category 大类，必有题）
    5. practice_scope 附 node_path / level / term / note
    6. 节点不存在 / teacher 不匹配 / qtype 树节点 → ValueError
  B. API 层（TestClient + STUDY_DB 隔离）
    7. 未登录 401
    8. 登录后 200 且带 practice_scope
    9. 节点不存在 404

运行：cd backend && python tests/test_knowledge_practice.py
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from poc.study import StudyStore, _kp_terms  # noqa: E402
from poc.knowledge import KnowledgeStore  # noqa: E402

PASS = 0


def chk(name: str, cond: bool, extra: str = ""):
    global PASS
    assert cond, f"FAIL: {name} {extra}"
    PASS += 1
    print(f"  ok {name}")


# ============ A. store 层 ============
TMP = tempfile.mkdtemp(prefix="gk-kp-")
DB = Path(TMP) / "study.db"
st = StudyStore(db_path=DB)
ks = KnowledgeStore(db_path=DB)

print("== A1 _kp_terms 词根 ==")
chk("剥「问题」", _kp_terms("比重问题") == ["比重问题", "比重"], str(_kp_terms("比重问题")))
chk("链式剥「策略」+「题」", _kp_terms("综合判断题策略") == ["综合判断题策略", "综合判断题", "综合判断"],
    str(_kp_terms("综合判断题策略")))
chk("按「与」拆分", _kp_terms("增长量与增长率") == ["增长量与增长率", "增长量", "增长率"],
    str(_kp_terms("增长量与增长率")))
chk("短碎片丢弃", _kp_terms("力与运动") == ["力与运动", "运动"], str(_kp_terms("力与运动")))
chk("无后缀无与", _kp_terms("图形推理") == ["图形推理"], str(_kp_terms("图形推理")))
chk("空名返回空", _kp_terms("") == [] and _kp_terms(None) == [])

print("== A2-A5 三级检索 ==")
root = ks.create_node("T999", "资料分析", category="模块")
ch = ks.create_node("T999", "核心公式", parent_id=root["node_id"], category="资料分析")
leaf = ks.create_node("T999", "比重问题", parent_id=ch["node_id"], category="核心公式")
# 章节级题目：知识点名与叶子字面无关，但 knowledge_point 含章节词根
st.add_question("T999", "choice", "现期比重计算：甲占乙的比重为？", "A",
                options=["A. 0.2", "B. 0.4", "C. 0.5", "D. 0.8"],
                analysis="比重=部分/整体", category="资料分析",
                knowledge_point="现期比重", difficulty=3)
resp = st.get_practice_by_knowledge("T999", leaf["node_id"], ks=ks)
chk("L1 命中（比重→现期比重）", resp["practice_scope"]["level"] == "knowledge",
    str(resp.get("practice_scope")))
chk("L1 词根为「比重」", resp["practice_scope"]["term"] == "比重")
chk("node_path 完整", resp["practice_scope"]["node_path"] == ["资料分析", "核心公式", "比重问题"],
    str(resp["practice_scope"]["node_path"]))
chk("payload 带题目", resp["question"].startswith("现期比重"), resp["question"][:20])
chk("note 知识点专项", resp["practice_scope"]["note"] == "🎯 知识点专项")

# L2 章节级：叶子无命中 → 章节词根命中
st.add_question("T999", "choice", "图形旋转：下一个图形是？", "B",
                options=["A. 甲", "B. 乙", "C. 丙", "D. 丁"],
                analysis="顺时针旋转90°", category="判断推理",
                knowledge_point="图形推理-位置规律", difficulty=3)
# 造一棵独立分支：模块 判断推理 / 章节 图形推理 / 叶子 无命中考点
root2 = ks.create_node("T999", "判断推理", category="模块")
ch2 = ks.create_node("T999", "图形推理", parent_id=root2["node_id"], category="判断推理")
leaf3 = ks.create_node("T999", "无命中考点", parent_id=ch2["node_id"], category="图形推理")
resp2 = st.get_practice_by_knowledge("T999", leaf3["node_id"], ks=ks)
chk("L2 章节回退命中", resp2["practice_scope"]["level"] == "chapter",
    str(resp2.get("practice_scope")))
chk("L2 词根为章节名", resp2["practice_scope"]["term"] == "图形推理")

# L3 模块兜底：知识点/章节词根全不命中 → 模块 category 大类
root3 = ks.create_node("T999", "数量关系", category="模块")
ch3 = ks.create_node("T999", "陌生章节", parent_id=root3["node_id"], category="数量关系")
leaf4 = ks.create_node("T999", "生僻考点", parent_id=ch3["node_id"], category="陌生章节")
st.add_question("T999", "choice", "行程问题：甲乙相遇需要多久？", "C",
                options=["A. 1h", "B. 2h", "C. 3h", "D. 4h"],
                analysis="路程和=速度和×时间", category="数量关系",
                knowledge_point="完全无关知识点", difficulty=4)
resp3 = st.get_practice_by_knowledge("T999", leaf4["node_id"], ks=ks)
chk("L3 模块兜底命中", resp3["practice_scope"]["level"] == "module",
    str(resp3.get("practice_scope")))
chk("L3 词根为映射大类", resp3["practice_scope"]["term"] == "数量关系")
chk("L3 note 模块练习", resp3["practice_scope"]["note"] == "📚 模块练习")

print("== A6 异常路径 ==")
try:
    st.get_practice_by_knowledge("T999", "node-not-exist", ks=ks)
    chk("节点不存在抛错", False)
except ValueError as e:
    chk("节点不存在抛错", "不存在" in str(e), str(e))
try:
    st.get_practice_by_knowledge("T001", leaf["node_id"], ks=ks)  # 节点属 T999
    chk("teacher 不匹配抛错", False)
except ValueError as e:
    chk("teacher 不匹配抛错", "不存在" in str(e), str(e))
qnode = ks.create_node("T999", "图形推理题型", tree_type="qtype")
try:
    st.get_practice_by_knowledge("T999", qnode["node_id"], ks=ks)
    chk("qtype 树拒绝", False)
except ValueError as e:
    chk("qtype 树拒绝", "知识体系" in str(e), str(e))

# ============ B. API 层 ============
API_TMP = tempfile.mkdtemp(prefix="gk-kp-api-")
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

print("== B API 层 ==")
r = client.post("/register", json={"username": "知识学生", "password": "secret123"})
tok = r.json().get("token", "")
chk("注册拿 token", bool(tok))

r1 = client.get("/practice/by-knowledge?teacher_id=T888&node_id=abc")
chk("未登录 401", r1.status_code == 401, str(r1.status_code))

# API 库内建树建题（poc.api.study_store 与 knowledge 单例均指向 API_TMP/study.db）
api_ks = poc.api.knowledge_store if hasattr(poc.api, "knowledge_store") else None
if api_ks is None:
    from poc.knowledge import get_knowledge_store
    api_ks = get_knowledge_store()
aroot = api_ks.create_node("T888", "言语理解与表达", category="模块")
aleaf = api_ks.create_node("T888", "主旨概括", parent_id=aroot["node_id"], category="言语理解与表达")
poc.api.study_store.add_question("T888", "choice", "片段阅读主旨：这段文字意在说明？", "A",
                                 options=["A. 意在强调B", "B. 其他", "C. 甲", "D. 乙"],
                                 analysis="主旨概括找中心句", category="言语理解",
                                 knowledge_point="主旨概括题", difficulty=2)

r2 = client.get(f"/practice/by-knowledge?teacher_id=T888&node_id={aleaf['node_id']}",
                headers={"Authorization": f"Bearer {tok}"})
chk("登录后 200", r2.status_code == 200, f"{r2.status_code} {r2.text[:120]}")
d2 = r2.json()
chk("响应带 practice_scope", d2.get("practice_scope", {}).get("level") == "knowledge",
    str(d2.get("practice_scope")))
chk("node_path 正确", d2["practice_scope"]["node_path"] == ["言语理解与表达", "主旨概括"])

r3 = client.get("/practice/by-knowledge?teacher_id=T888&node_id=nope",
                headers={"Authorization": f"Bearer {tok}"})
chk("节点不存在 404", r3.status_code == 404, str(r3.status_code))

print(f"\n===== 通过 {PASS} 项 =====")
if PASS < 25:
    print("断言数不足，检查是否有用例被跳过")
    sys.exit(1)
print("ALL PASS")
