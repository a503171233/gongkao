# -*- coding: utf-8 -*-
"""会话历史持久化 · 单元测试（项目资产）
运行: cd backend && python tests/test_chat.py
依赖: 无外部服务；隔离 SQLite（CHAT_DB 指向临时文件）
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# 隔离测试库（必须在 import poc.chat 之前设置）
TMP = tempfile.mkdtemp(prefix="gk-chat-test-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["EMBED_PROVIDER"] = "selftest"

from poc.chat import ChatStore, build_history_messages  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


# 1) 建会话
store = ChatStore()
sid = store.create_session("T001", "user_1")
check("创建会话返回 session_id", bool(sid) and isinstance(sid, str))
check("会话归属老师", store.get_session(sid)["teacher_id"] == "T001")

# 2) 存消息 + 取历史
store.add_message(sid, "user", "转折关系怎么做题")
store.add_message(sid, "assistant", "先找转折词，重点在转折之后。")
hist = store.get_messages(sid)
check("取回 2 条消息", len(hist) == 2 and hist[0]["role"] == "user")

# 3) 历史 → Prompt messages（含系统角色时结构正确）
msgs = build_history_messages(hist)
check("历史转 messages", len(msgs) == 2 and msgs[0]["role"] == "user")
check("历史内容正确", "转折" in msgs[0]["content"])

# 4) limit 截断（存 6 条，取最近 2 条）
for i in range(6):
    store.add_message(sid, "user" if i % 2 == 0 else "assistant", f"内容{i}")
hist6 = store.get_messages(sid, limit=2)
check("limit 取最近 2 条", len(hist6) == 2 and "内容5" in hist6[-1]["content"])

# 5) 会话隔离（T002 vs T001）
sid2 = store.create_session("T002", "user_1")
store.add_message(sid2, "user", "逻辑判断的假言命题")
h2 = store.get_messages(sid2)
check("T002 会话独立", len(h2) == 1 and h2[0]["content"].startswith("逻辑"))
h_all = store.get_messages(sid)
check("T001 会话不受影响", "内容5" in h_all[-1]["content"])

# 6) 列表会话（历史栏数据源）
sessions = store.list_sessions("user_1")
check("list_sessions 含 2 个会话", len(sessions) == 2)
check("会话含消息数与最后活跃时间",
      "message_count" in sessions[0] and "last_active_at" in sessions[0])

# 7) 非法参数防护
try:
    store.add_message("不存在的会话", "user", "x")
    check("不存在会话抛异常", False)
except ValueError:
    check("不存在会话抛异常", True)

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)