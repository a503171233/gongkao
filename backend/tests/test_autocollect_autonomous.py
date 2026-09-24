# -*- coding: utf-8 -*-
"""自主采集（零配置找题）+ 入库自动打标（知识节点关联）验证。

覆盖：零配置兜底判定、显式开关、词池生成（课程大类/老师学科/知识树叶子）、
游标轮转、知识节点索引与最长子串匹配、save_config 字段回写。
运行: cd backend && python tests/test_autocollect_autonomous.py
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-auto-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["STUDY_DB"] = os.path.join(TMP, "study.db")
os.environ["SEARCH_PROVIDER"] = "none"  # 测试不联网

from poc import autocollect as ac  # noqa: E402
from poc.knowledge import get_knowledge_store  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(extra)[:300]) if extra and not cond else ""))
    if not cond:
        ok = False


# ---------- 1. 零配置兜底 + 显式开关（enabled=False 避免起守护线程） ----------
r = ac.save_config({"enabled": False, "urls": [], "search_queries": [],
                    "autonomous": False, "autonomous_queries": 12})
check("零配置兜底: 无 URL/搜索词时 effective=True", r.get("autonomous_effective") is True)
check("config 回显: autonomous=False", r.get("autonomous") is False)
check("config 回显: 默认词数量 12", r.get("autonomous_queries") == 12)

r = ac.save_config({"enabled": False, "urls": [], "search_queries": [],
                    "autonomous": True, "autonomous_queries": 8})
check("显式开启: autonomous=True", r.get("autonomous") is True)
check("显式开启: 词数量 8", r.get("autonomous_queries") == 8)
check("显式开启: effective=True", r.get("autonomous_effective") is True)

r = ac.save_config({"enabled": False, "urls": ["https://example.com/q"],
                    "search_queries": [], "autonomous": False})
check("已配置 URL 且未开启: effective=False", r.get("autonomous_effective") is False)

# 清理 runtime，避免影响后续（写回无来源零配置态）
ac.save_config({"enabled": False, "urls": [], "search_queries": [],
                "autonomous": True, "autonomous_queries": 8})

# ---------- 2. 词池生成（知识树为空 → 大类 + 老师学科） ----------
pool = ac._autonomous_query_pool(["T001", "T002"])
check("词池含 8 个课程大类", all(f"公务员考试 {c} 真题及答案" in pool for c in ac._COURSE_CATEGORIES))
check("词池含老师学科", any("言语理解 公务员考试 真题" in pool for q in pool)
      and any("逻辑判断 公务员考试 真题" in pool for q in pool))
check("词池去重保序", len(pool) == len(set(pool)) and pool == list(dict.fromkeys(pool)))
check("词池稳定顺序", pool == ac._autonomous_query_pool(["T001", "T002"]))

# ---------- 3. 游标轮转 ----------
plan1 = ac._autonomous_plan({}, ["T001"], 3)
plan2 = ac._autonomous_plan({"autonomous_cursor": plan1["next_cursor"]}, ["T001"], 3)
check("游标轮转: 每轮取 3 条", len(plan1["queries"]) == 3 and len(plan2["queries"]) == 3)
check("游标轮转: 两轮不重叠", set(plan1["queries"]).isdisjoint(set(plan2["queries"])))
check("游标轮转: 推进正确", plan1["next_cursor"] == 3)
check("游标轮转: 可环绕", ac._autonomous_plan({"autonomous_cursor": 1}, ["T001"], 99)["queries"])

# ---------- 4. 知识节点索引 + 最长子串关联 ----------
ks = get_knowledge_store()
n1 = ks.create_node("T001", "主旨概括", tree_type="knowledge")
n2 = ks.create_node("T001", "概括", tree_type="knowledge")
idx = ac._knowledge_node_index(["T001"])
check("索引含叶子节点名", "主旨概括" in idx and "概括" in idx)
q = {"question": "下列哪项属于主旨概括的具体方法……", "knowledge_point": "", "category": "言语理解"}
cid = ac._associate_category_id(q, idx)
check("最长匹配命中「主旨概括」", cid == n1["node_id"], cid)
check("空索引返回空", ac._associate_category_id(q, {}) == "")
q2 = {"question": "普通句子无考点词……", "knowledge_point": "", "category": "综合"}
check("无命中返回空", ac._associate_category_id(q2, idx) == "")

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
