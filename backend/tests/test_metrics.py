# -*- coding: utf-8 -*-
"""监控指标 · 单元测试（RED→GREEN）
运行: cd backend && python tests/test_metrics.py
覆盖: 请求计数/状态分布/ask统计/平均耗时/老师分布/快照键/API /metrics 契约
"""
import os
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-metrics-test-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["AUTH_DB"] = os.path.join(TMP, "auth.db")

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


# ---------- 1) Metrics 类纯逻辑 ----------
from poc.metrics import Metrics  # noqa: E402

m = Metrics()

# 记录若干请求
m.record("GET", "/health", 200)
m.record("GET", "/health", 200)
m.record("POST", "/ask", 200, teacher_id="T001", latency_ms=123.4)
m.record("POST", "/ask", 500, latency_ms=50.0)

s = m.snapshot()
check("快照含 uptime_seconds", "uptime_seconds" in s and s["uptime_seconds"] >= 0, str(list(s.keys())))
check("请求计数: /health x2", any(r["path"] == "/health" and r["count"] == 2 for r in s["requests"]), str(s["requests"]))
check("请求计数: /ask x2", any(r["path"] == "/ask" and r["count"] == 2 for r in s["requests"]))
check("状态分布 200/500", s["status"].get("200") == 3 and s["status"].get("500") == 1, str(s["status"]))
check("老师分布 T001 x1", s["by_teacher"].get("T001") == 1, str(s["by_teacher"]))
check("ask 统计 total=0(需 ask_started)", s["ask"]["total"] == 0, str(s["ask"]))

# ask 生命周期统计
m.ask_started("T001")
m.ask_started("T002")
m.ask_failed()
s2 = m.snapshot()
check("ask total=2", s2["ask"]["total"] == 2, str(s2["ask"]))
check("ask fail=1", s2["ask"]["fail"] == 1, str(s2["ask"]))
check("平均耗时 ~86.7ms", abs(s2["avg_latency_ms"] - 86.7) < 1.0, str(s2["avg_latency_ms"]))

# 最近事件日志
check("recent 记录 4 条", len(s2["recent"]) == 4, str(len(s2["recent"])))
check("recent 条目含字段", all({"t", "method", "path", "status"} <= set(r) for r in s2["recent"]))

# 线程安全冒烟（并发记录不炸）
import threading

thr = []
for _ in range(8):
    t = threading.Thread(target=lambda: [m.record("GET", "/x", 200) for _ in range(100)])
    thr.append(t)
    t.start()
for t in thr:
    t.join()
s3 = m.snapshot()
check("并发记录 800 次 /x", any(r["path"] == "/x" and r["count"] == 800 for r in s3["requests"]), str(s3["requests"][:3]))

# ---------- 2) API /metrics 契约 ----------
import poc.api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(poc.api.app)
r = client.get("/health")
check("health 200", r.status_code == 200)
r2 = client.get("/metrics")
check("/metrics 200", r2.status_code == 200, str(r2.status_code))
d = r2.json()
check("/metrics JSON 含 uptime/requests/ask", "uptime_seconds" in d and "requests" in d and "ask" in d, str(list(d.keys())))
check("/metrics 记录到 /health 请求", any(x["path"] == "/health" for x in d["requests"]), str(d["requests"]))
check("/metrics 含 guard 统计", "guard" in d and "total" in d["guard"], str(list(d.keys())))
# /ask 一次（selftest 离线 mock）
r3 = client.post("/ask", json={"query": "转折关系怎么做题", "teacher_id": "T001", "stream": False})
check("带 ask 后 /ask 200", r3.status_code == 200)
d2 = client.get("/metrics").json()
check("ask total 递增(selftest 走 ask)", d2["ask"]["total"] >= 1, str(d2["ask"]))
check("by_teacher 含 T001", d2["by_teacher"].get("T001", 0) >= 1, str(d2["by_teacher"]))

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)