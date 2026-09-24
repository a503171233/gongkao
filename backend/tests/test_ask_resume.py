# -*- coding: utf-8 -*-
"""SSE 断点续答 · API/链路契约测试（批次1 问答体验加固）
运行: cd backend && python tests/test_ask_resume.py
覆盖:
  - 首连 stream 流：start/delta/refs/done 事件序列 + user 消息入库 1 条
  - resume 续答流：user 消息不重复入库、无 refs 事件、delta 仅含剩余部分
  - resume 且检索空（无课件老师）：静默 done 收尾（不覆盖前端已有内容）
  - 非流式 + resume 字段：被忽略（照常入库，契约不变）
  - answer 层：_stream_chain resume 预置缓冲（守卫对全文跑）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# 隔离环境（必须在 import poc.api 之前）
TMP = tempfile.mkdtemp(prefix="gk-resume-test-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"  # 离线 mock，不调外部 API
os.environ["AUTH_DB"] = os.path.join(TMP, "auth.db")

from fastapi.testclient import TestClient  # noqa: E402
from poc.api import app  # noqa: E402
from poc.chat import ChatStore  # noqa: E402

client = TestClient(app)
ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """原始 SSE 文本 → [(event, data_dict)]；data 非 JSON 时存 {'_raw': str}。"""
    events = []
    for block in text.split("\n\n"):
        ev, data_lines = "", []
        for line in block.splitlines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not ev or not data_lines:
            continue
        try:
            payload = json.loads("\n".join(data_lines))
        except ValueError:
            payload = {"_raw": "\n".join(data_lines)}
        events.append((ev, payload))
    return events


# ── 1) 首连 stream：完整事件序列 + user 消息入库 ──────────────────
Q = "转折关系怎么做题"
with client.stream("POST", "/ask",
                   json={"query": Q, "teacher_id": "T001", "stream": True}) as r:
    first_text = "".join(r.iter_text())
first_events = parse_sse(first_text)
names = [e for e, _ in first_events]
check("首连: 事件含 start/delta/refs/done",
      all(x in names for x in ("start", "delta", "refs", "done")), str(names))
check("首连: 无 guard_block 终态", "guard_block" not in names, str(names))
first_delta = "".join(d.get("text", "") for e, d in first_events if e == "delta")
check("首连: delta 非空", bool(first_delta))

cs = ChatStore()
sid = next(d["session_id"] for e, d in first_events if e == "start")
msgs = cs.get_messages(sid)
check("首连: user+assistant 各 1 条 (实得 %d)" % len(msgs), len(msgs) == 2)

# ── 2) 断线重连：resume 续答（模拟前端已收到前半段） ──────────────
seen = first_delta[: max(1, len(first_delta) // 2)]
with client.stream("POST", "/ask",
                   json={"query": Q, "teacher_id": "T001", "stream": True,
                         "session_id": sid, "resume": seen}) as r:
    resume_text = "".join(r.iter_text())
resume_events = parse_sse(resume_text)
rnames = [e for e, _ in resume_events]
check("续答: 无 refs 事件（前端已持有）", "refs" not in rnames, str(rnames))
check("续答: 含 done 收尾", "done" in rnames, str(rnames))
resume_delta = "".join(d.get("text", "") for e, d in resume_events if e == "delta")
check("续答: delta 仅含剩余部分（无重复前缀）",
      not (seen and resume_delta.startswith(seen[:5]) and seen in resume_delta),
      repr(resume_delta[:40]))
msgs2 = cs.get_messages(sid)
user_msgs = [m for m in msgs2 if m["role"] == "user"]
check("续答: user 消息不重复入库 (仍 %d 条)" % len(user_msgs), len(user_msgs) == 1)

# ── 3) resume 且检索空（T002 无课件）：静默 done，不覆盖前端内容 ──
with client.stream("POST", "/ask",
                   json={"query": "任意问题", "teacher_id": "T002", "stream": True,
                         "resume": "这是前端已收到的部分答案"}) as r:
    silent_text = "".join(r.iter_text())
silent_events = parse_sse(silent_text)
snames = [e for e, _ in silent_events]
check("续答+空库: 仅 start+done（静默收尾，无内容覆盖）", snames == ["start", "done"], str(snames))

# ── 4) 非流式 + resume：字段被忽略，照常入库（契约不变） ──────────
r = client.post("/ask", json={"query": "非流式也带resume字段", "teacher_id": "T001",
                              "stream": False, "resume": "假的已有内容"})
check("非流式+resume: 200", r.status_code == 200, str(r.status_code))
d = r.json()
sid3 = d.get("session_id")
msgs3 = cs.get_messages(sid3)
check("非流式+resume: user 消息照常入库",
      any(m["role"] == "user" and "非流式" in m["content"] for m in msgs3))
check("非流式+resume: assistant 消息存在", any(m["role"] == "assistant" for m in msgs3))

# ── 5) answer 层：_stream_chain resume 预置（守卫对全文跑） ────────
from poc import answer as ans  # noqa: E402

# 协议语义测试：stub 掉 LLM 流与守卫（各自有专属测试），聚焦 resume 事件契约
ans._stream_llm = lambda messages, tcfg: iter(
    [{"type": "delta", "data": "A"}, {"type": "delta", "data": "B"}])
ans.run_guard = lambda answer, hits, tcfg, teacher_id, query="": (True, {"level": "pass"})

collected = list(ans._stream_chain(
    [{"role": "user", "content": "q"}],
    type("T", (), {"teacher_id": "T001", "llm_model": "m", "temperature": 0.1,
                   "top_n": 1, "threshold": 0.1, "prompt_style": "classroom",
                   "guard_policy": None})(),
    [{"doc_name": "d", "content": "c", "score": 0.9}],
    "T001", resume="RESUME_PART"))
cnames = [i["type"] for i in collected]
check("链路层: resume 模式无 refs", "refs" not in cnames, str(cnames))
check("链路层: 含 done", "done" in cnames, str(cnames))
chain_deltas = "".join(i["data"] for i in collected if i["type"] == "delta")
check("链路层: delta 仅新内容（不含 resume 前缀）",
      chain_deltas == "AB" and "RESUME_PART" not in chain_deltas, repr(chain_deltas))

print()
print("PASS" if ok else "FAIL", "- test_ask_resume")
sys.exit(0 if ok else 1)
