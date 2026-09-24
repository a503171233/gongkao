# -*- coding: utf-8 -*-
"""会话历史持久化 · API 契约测试（RED→GREEN）
运行: cd backend && python tests/test_api_sessions.py
覆盖: /ask 会话创建/复用/重建、/sessions 列表与删除
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# 隔离环境（必须在 import poc.api 之前）
TMP = tempfile.mkdtemp(prefix="gk-api-test-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"  # 离线 mock，不调外部 API

from fastapi.testclient import TestClient  # noqa: E402
from poc.api import app  # noqa: E402

client = TestClient(app)
ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


# 1) 不传 session_id → 自动创建并返回
r = client.post("/ask", json={"query": "转折关系怎么做题", "teacher_id": "T001", "stream": False})
d = r.json()
check("不传 session_id: 返回 200", r.status_code == 200, str(r.status_code))
check("不传 session_id: 响应含 session_id", "session_id" in d and d["session_id"], str(d.get("session_id"))[:20])
sid = d.get("session_id")

# 2) 同 session_id 再次提问 → 会话复用（消息累加）
r2 = client.post("/ask", json={"query": "隐性转折词有哪些", "teacher_id": "T001", "stream": False, "session_id": sid})
d2 = r2.json()
check("传 session_id: 返回 200", r2.status_code == 200, str(r2.status_code))
check("传 session_id: 复用同一会话", d2.get("session_id") == sid, str(d2.get("session_id"))[:20])

# 3) DB 中消息条数 = 4（两轮 user+assistant）
from poc.chat import ChatStore  # noqa: E402
store = ChatStore()
msgs = store.get_messages(sid)
check(f"会话消息 4 条 (实得 {len(msgs)})", len(msgs) == 4)
check("第1条是 user", msgs[0]["role"] == "user" and "转折" in msgs[0]["content"])
check("第2条是 assistant 非空", msgs[1]["role"] == "assistant" and msgs[1]["content"])

# 4) 无效 session_id → 自动重建而非 404
r3 = client.post("/ask", json={"query": "转折关系怎么做题", "teacher_id": "T001", "stream": False, "session_id": "不存在的会话"})
check("无效 session_id: 自动重建返回 200", r3.status_code == 200, str(r3.status_code))
check("无效 session_id: 返回新 session_id", r3.json().get("session_id") != "不存在的会话")

# 5) GET /sessions 列表
r4 = client.get("/sessions?user_id=anonymous")
d4 = r4.json()
check("GET /sessions: 200", r4.status_code == 200, str(r4.status_code))
check("GET /sessions: 会话数>=2", len(d4.get("sessions", [])) >= 2, str(len(d4.get("sessions", []))))

# 6) DELETE /sessions/{sid}
r5 = client.delete(f"/sessions/{sid}")
check("DELETE /sessions: 200", r5.status_code == 200, str(r5.status_code))
check("DELETE /sessions: 删除后消息清空", len(store.get_messages(sid)) == 0)

# 7) 健康检查不受影响
r6 = client.get("/health")
check("health 仍正常", r6.status_code == 200 and '"T001"' in r6.text)

# 8) 流式路径会话契约：事件携带 session_id + assistant 落库
import json as _json  # noqa: E402
sid_s = ""
with client.stream("POST", "/ask", json={"query": "转折关系怎么做题", "teacher_id": "T001",
                                          "stream": True}) as resp:
    check("流式: HTTP 200", resp.status_code == 200, str(resp.status_code))
    ev_types = []
    for line in resp.iter_lines():
        if line.startswith("event:"):
            ev_types.append(line[6:].strip())
        elif line.startswith("data:") and sid_s == "":
            try:
                d = _json.loads(line[5:].strip())
                sid_s = d.get("session_id", "")
            except Exception:
                pass
check("流式: 事件序列含 start/delta/done", "start" in ev_types and "delta" in ev_types and "done" in ev_types,
      str(ev_types))
check("流式: 事件携带 session_id", bool(sid_s))
if sid_s:
    msgs_s = store.get_messages(sid_s)
    check(f"流式: DB 落库 2 条消息 (实得 {len(msgs_s)})", len(msgs_s) == 2)
    check("流式: assistant 消息非空", msgs_s[-1]["role"] == "assistant" and msgs_s[-1]["content"])

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
