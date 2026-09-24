# -*- coding: utf-8 -*-
"""B4 调度控制模块 · 编排单元测试（RED→GREEN）
运行: cd backend && python tests/test_answer_orchestration.py
覆盖（对仗派单五项验收标准）:
  A. 空 query 抛 ValueError
  B. 无匹配 → 拒答(rejected=true) 且不调 LLM（非流式 + 流式）
  C. 有匹配 → 正常回答；非流式缓存命中后不重复调 LLM
  D. 幻觉拦截结果不进入缓存（再次同样提问仍重新推理）
  E. 流式事件顺序 delta→refs→done；流式从不走缓存；流式守卫拦截 → guard_block 终止
只 patch 上游替身（B3 store.retrieve / B7 _call_llm·_stream_llm / B8 run_guard），
不改动任何被调用的具体实现。
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-b4-test-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "local"

from poc import store  # noqa: E402
from poc import answer  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


# ---------- 隔离桩：走非 selftest 分支（真实阈值 0.5），但 LLM/检索/守卫全替身 ----------
store._GLOBAL.embed_provider = "local"
store._GLOBAL.api_key = "test-stub-key-not-a-real-credential"

HITS = [
    {"content": "转折之后是重点，转折之前可略读。显性转折词有但是、然而、却。",
     "meta": {"doc_name": "讲义.md"}, "score": 0.8},
]

calls = {"llm": 0, "stream": 0}
guard_state = {"passed": True, "fails": []}


def fake_retrieve(query, teacher_id, top_n=None):
    return [dict(h) for h in HITS]


def fake_retrieve_empty(query, teacher_id, top_n=None):
    return []


def fake_llm(messages, tcfg):
    calls["llm"] += 1
    return "转折之后是重点，转折之前可略读。"


def fake_guard(answer_text, hits, tcfg, teacher_id, query=""):
    return (guard_state["passed"], {"fails": list(guard_state["fails"]),
                                    "level": "pass" if guard_state["passed"] else "block"})


def fake_stream_llm(messages, tcfg):
    calls["stream"] += 1
    yield {"type": "delta", "data": "转折之后"}
    yield {"type": "delta", "data": "是重点。"}


# 打桩：B4 编排层引用的上游全部替换为替身
answer.store.retrieve = fake_retrieve
answer.run_guard = fake_guard
answer._call_llm = fake_llm
answer._stream_llm = fake_stream_llm

# ---------- A. 空 query 抛 ValueError ----------
try:
    answer.ask("   ")
    check("A. 空 query 抛 ValueError", False)
except ValueError as e:
    check("A. 空 query 抛 ValueError", True, str(e))

# ---------- B. 无匹配 → 拒答且不调 LLM ----------
calls["llm"] = 0
answer.store.retrieve = fake_retrieve_empty
r = answer.ask("一个根本不存在的知识点", teacher_id="T001")
check("B. 无匹配: rejected=true", r.get("rejected") is True)
check("B. 无匹配: 不调 LLM", calls["llm"] == 0, f"calls.llm={calls['llm']}")

calls["stream"] = 0
ev = list(answer.ask("一个根本不存在的知识点", teacher_id="T001", stream=True))
types = [e["type"] for e in ev]
check("B. 流式无匹配: delta→refs→done", types == ["delta", "refs", "done"], str(types))
check("B. 流式无匹配: 不调 LLM", calls["stream"] == 0, f"calls.stream={calls['stream']}")

# ---------- C. 有匹配 → 正常回答 + 缓存命中 ----------
answer.store.retrieve = fake_retrieve
guard_state["passed"] = True
guard_state["fails"] = []
calls["llm"] = 0
answer.answer_cache.clear()

r1 = answer.ask("转折关系怎么做题", teacher_id="T001")
check("C. 有匹配: 不拒答", r1.get("rejected") is False)
check("C. 有匹配: 调用 LLM 一次", calls["llm"] == 1, f"calls.llm={calls['llm']}")
check("C. 有匹配: 回答正确", r1.get("answer") == "转折之后是重点，转折之前可略读。")
check("C. 有匹配: 返回命中片段", bool(r1.get("hits")))

calls["llm"] = 0
r2 = answer.ask("转折关系怎么做题", teacher_id="T001")
check("C. 缓存命中: 不重复调 LLM", calls["llm"] == 0, f"calls.llm={calls['llm']}")
check("C. 缓存命中: 答案一致", r2.get("answer") == r1.get("answer"))

# ---------- D. 幻觉拦截结果不进入缓存 ----------
guard_state["passed"] = False
guard_state["fails"] = ["fact"]
calls["llm"] = 0
answer.answer_cache.clear()
before = answer.guard_counter.stats().get("total", 0)

r3 = answer.ask("另一道题", teacher_id="T001")
check("D. 拦截: rejected=true", r3.get("rejected") is True)
check("D. 拦截: 返回 guard_fails", "fact" in (r3.get("guard_fails") or []), str(r3.get("guard_fails")))
check("D. 拦截: 调用过 LLM", calls["llm"] == 1, f"calls.llm={calls['llm']}")
after = answer.guard_counter.stats().get("total", 0)
check("D. 拦截: 计数 +1 (走 guard_counter.record)", after == before + 1, f"{before}->{after}")

guard_state["passed"] = True
guard_state["fails"] = []
r4 = answer.ask("另一道题", teacher_id="T001")
check("D. 拦截不入缓存 → 再次提问重新推理(又调 LLM)", calls["llm"] == 2, f"calls.llm={calls['llm']}")
check("D. 再次提问答非拒答", r4.get("rejected") is False)

# ---------- E. 流式事件序列 / 不读缓存 / 守卫拦截 guard_block ----------
# E1: 流式正常事件顺序 delta→refs→done
guard_state["passed"] = True
guard_state["fails"] = []
calls["stream"] = 0
answer.answer_cache.clear()
answer.answer_cache.set(answer._safe_key("ans", "T001", "流式探测", "hash"), {"answer": "缓存哨兵", "rejected": False})
ev1 = list(answer.ask("流式探测", teacher_id="T001", stream=True))
types1 = [e["type"] for e in ev1]
check("E. 流式顺序: delta→delta→refs→done", types1 == ["delta", "delta", "refs", "done"], str(types1))
check("E. 流式从不读缓存(仍调 stream LLM)", calls["stream"] == 1, f"calls.stream={calls['stream']}")
check("E. 流式非缓存哨兵返回", ev1[0]["data"] != "缓存哨兵")
refs = next(e for e in ev1 if e["type"] == "refs")["data"]
check("E. 流式 refs 含来源", any(x.get("doc_name") == "讲义.md" for x in refs), str(refs))

# E2: 流式守卫拦截 → 追加 guard_block 并终止(不发 refs/done)
guard_state["passed"] = False
guard_state["fails"] = ["reference"]
calls["stream"] = 0
ev2 = list(answer.ask("流式拦截测试", teacher_id="T001", stream=True))
types2 = [e["type"] for e in ev2]
check("E. 流式拦截: 末事件为 guard_block", types2[-1] == "guard_block", str(types2))
check("E. 流式拦截: 不发 refs/done", "refs" not in types2 and "done" not in types2, str(types2))
block = ev2[-1].get("data", {})
check("E. 流式拦截: guard_block 含 reason/fails",
      block.get("reason") == "reference" and "reference" in (block.get("fails") or []), str(block))

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)