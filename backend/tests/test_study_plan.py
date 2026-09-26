# -*- coding: utf-8 -*-
"""批次22 · 个性化学习计划单测。

覆盖：
  1. 计划结构：Day1-5 行测 + Day6 申论（含写作任务）+ Day7 面试+模考
  2. 排序规则：weak 按正确率升序排前 → mid → none；strong 不进计划
  3. 申论/面试叶子分流入对应日，不占行测日
  4. 打卡：翻转 done + 进度统计
  5. 重新生成：旧计划作废（active=0），get 返回新计划
  6. 过期懒清理：created_at 超过 7 天 → get 返回 None
  7. API：未登录 401 / generate/get/checkin 200 / 非法打卡 400

运行：cd backend && python tests/test_study_plan.py
"""
import os
import sqlite3
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


TMP = tempfile.mkdtemp(prefix="gk-plan-")
DB = Path(TMP) / "study.db"
st = StudyStore(db_path=DB)
ks = KnowledgeStore(db_path=DB)

# ============ 建树：资料分析(行测) / 申论 / 面试 ============
root_xc = ks.create_node("T999", "资料分析", category="模块")
ch_xc = ks.create_node("T999", "核心公式", parent_id=root_xc["node_id"], category="资料分析")
leaf_w1 = ks.create_node("T999", "比重问题", parent_id=ch_xc["node_id"])   # weak 33%
leaf_w2 = ks.create_node("T999", "平均数问题", parent_id=ch_xc["node_id"])  # weak 50%
leaf_mid = ks.create_node("T999", "增长率问题", parent_id=ch_xc["node_id"])  # mid 66.7%
leaf_strong = ks.create_node("T999", "倍数问题", parent_id=ch_xc["node_id"])  # strong
leaf_none = ks.create_node("T999", "隔年增长", parent_id=ch_xc["node_id"])  # none

root_sl = ks.create_node("T999", "申论", category="模块")
ch_sl = ks.create_node("T999", "归纳概括", parent_id=root_sl["node_id"], category="申论")
leaf_sl = ks.create_node("T999", "概括问题", parent_id=ch_sl["node_id"])

root_ms = ks.create_node("T999", "面试", category="模块")
ch_ms = ks.create_node("T999", "结构化答题", parent_id=root_ms["node_id"], category="面试")
leaf_ms = ks.create_node("T999", "综合分析题", parent_id=ch_ms["node_id"])

# 补充行测叶子（无记录 → none），凑足 Day1-5 每天 3 项的池子
for i in range(11):
    ks.create_node("T999", f"填充考点{i}", parent_id=ch_xc["node_id"])

# 题库 + 练习记录（只对行测叶子造分）
qids = {}
for name, kps in [("比重问题", ["现期比重", "基期比重", "比重变化"]),
                  ("平均数问题", ["现期平均数", "平均数增长率"]),
                  ("增长率问题", ["同比增长率", "环比增长率"]),
                  ("倍数问题", ["现期倍数", "基期倍数", "倍数比较"]),
                  ("隔年增长", ["隔年增长率"])]:
    qids[name] = []
    for kp in kps:
        q = st.add_question("T999", "choice", f"{name}题·{kp}", "A",
                            options=["A. 1", "B. 2", "C. 3", "D. 4"],
                            analysis="x", category="资料分析",
                            knowledge_point=kp, difficulty=3)
        qids[name].append(q["id"])


def _submit(user, name, pattern):
    """pattern: list[bool] 逐题提交"""
    for qid, right in zip(qids[name], pattern):
        st.submit_practice(user, "T999", f"qid-{qid}", "A" if right else "B",
                           question_id=qid)


# u_plan：比重 33%(weak) / 平均数 50%(weak) / 增长率 66.7%(mid) / 倍数 100% 3题(strong) / 隔年 none
_submit("u_plan", "比重问题", [True, False, False])
_submit("u_plan", "平均数问题", [True, False])
_submit("u_plan", "增长率问题", [True, True, False])
_submit("u_plan", "倍数问题", [True, True, True])

print("== 1 生成结构 ==")
plan = st.generate_study_plan("T999", "u_plan", ks=ks)
days = plan["days"]
chk("7 天计划", len(days) == 7, str(len(days)))
chk("Day1-5 行测", all(d["focus"] == "行测" for d in days[:5]))
chk("Day6 申论", days[5]["focus"] == "申论")
chk("Day7 综合", days[7 - 1]["focus"] == "综合")
chk("Day6 含写作任务", any(it["kind"] == "essay" for it in days[5]["items"]))
chk("Day7 含面试任务+模考任务",
    {it["kind"] for it in days[6]["items"]} >= {"interview", "mock"})

print("== 2 排序规则 ==")
xc_items = [it for d in days[:5] for it in d["items"]]
names = [it["name"] for it in xc_items]
chk("比重(33%) 排最前", names[0] == "比重问题", str(names))
chk("平均数(50%) 第二", names[1] == "平均数问题", str(names))
chk("增长率(mid) 在 weak 之后",
    names.index("增长率问题") > names.index("平均数问题"), str(names))
chk("strong(倍数问题) 不进计划", "倍数问题" not in names, str(names))
chk("none(隔年增长) 进计划", "隔年增长" in names, str(names))
chk("申论叶子入 Day6", any(it["name"] == "概括问题" for it in days[5]["items"]))
chk("面试叶子入 Day7", any(it["name"] == "综合分析题" for it in days[6]["items"]))
chk("行测日不含申论/面试叶子",
    not any(it["module"] in ("申论", "面试") for it in xc_items))

print("== 3 打卡 ==")
r = st.checkin_plan_item("u_plan", plan["plan_id"], 1, 0)
chk("打卡翻转为 done", r["done"] is True, str(r))
chk("进度 1/total", r["progress"]["done"] == 1, str(r))
total_all = r["progress"]["total"]
r2 = st.checkin_plan_item("u_plan", plan["plan_id"], 1, 0)
chk("再打卡取消", r2["done"] is False and r2["progress"]["done"] == 0, str(r2))
try:
    st.checkin_plan_item("u_plan", plan["plan_id"], 1, 999)
    chk("越界打卡报错", False)
except ValueError:
    chk("越界打卡报错", True)
try:
    st.checkin_plan_item("u_other", plan["plan_id"], 1, 0)
    chk("他人计划不可打卡", False)
except ValueError:
    chk("他人计划不可打卡", True)

print("== 4 查询与重建 ==")
active = st.get_active_plan("T999", "u_plan")
chk("查到当前计划", active and active["plan_id"] == plan["plan_id"])
chk("进度字段齐", active["progress"]["total"] == total_all, str(active["progress"]))
chk("新用户无计划", st.get_active_plan("T999", "u_nobody") is None)

plan2 = st.generate_study_plan("T999", "u_plan", ks=ks)
active2 = st.get_active_plan("T999", "u_plan")
chk("重新生成 → 新 plan_id", active2["plan_id"] != plan["plan_id"])

print("== 5 过期懒清理 ==")
with sqlite3.connect(str(DB)) as conn:
    conn.execute("UPDATE study_plans SET created_at='2020-01-01T00:00:00Z' WHERE id=?",
                 (plan2["plan_id"],))
chk("过期计划返回 None", st.get_active_plan("T999", "u_plan") is None)

# ============ API 层 ============
API_TMP = tempfile.mkdtemp(prefix="gk-plan-api-")
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

print("== 6 API 层 ==")
r = client.post("/register", json={"username": "计划学生", "password": "secret123"})
tok = r.json().get("token", "")
chk("注册拿 token", bool(tok))
hdr = {"Authorization": f"Bearer {tok}"}

chk("未登录 generate 401", client.post("/study/plan/generate").status_code == 401)
chk("未登录 get 401", client.get("/study/plan").status_code == 401)
chk("未登录 checkin 401",
    client.post("/study/plan/checkin", json={"plan_id": 1, "day": 1, "idx": 0}).status_code == 401)

# API 库建树（API 的 knowledge_store 用 STUDY_DB）
a_ks = getattr(poc.api, "knowledge_store", None)
if a_ks is None:
    from poc.knowledge import get_knowledge_store
    a_ks = get_knowledge_store()
rt = a_ks.create_node("T001", "资料分析", category="模块")
ct = a_ks.create_node("T001", "核心公式", parent_id=rt["node_id"], category="资料分析")
a_ks.create_node("T001", "比重问题", parent_id=ct["node_id"])
a_ks.create_node("T001", "增长率问题", parent_id=ct["node_id"])
for i in range(13):
    a_ks.create_node("T001", f"API填充考点{i}", parent_id=ct["node_id"])

r = client.post("/study/plan/generate", headers=hdr)
chk("generate 200", r.status_code == 200, r.text[:200])
g = r.json()
chk("生成 7 天", len(g["days"]) == 7)

r = client.get("/study/plan", headers=hdr)
chk("get 200 plan 非空", r.status_code == 200 and r.json()["plan"] is not None)
pid = r.json()["plan"]["plan_id"]

r = client.post("/study/plan/checkin", headers=hdr,
                json={"plan_id": pid, "day": 1, "idx": 0})
chk("checkin 200 done", r.status_code == 200 and r.json()["done"] is True, r.text[:200])
chk("进度联动", r.json()["progress"]["done"] == 1)

r = client.post("/study/plan/checkin", headers=hdr,
                json={"plan_id": pid, "day": 99, "idx": 0})
chk("非法打卡 400", r.status_code == 400, str(r.status_code))

r = client.get("/study/plan", headers=hdr)
chk("打卡后进度持久", r.json()["plan"]["progress"]["done"] == 1)

print(f"\n通过 {PASS} 断言")
