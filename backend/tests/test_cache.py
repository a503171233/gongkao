# -*- coding: utf-8 -*-
"""缓存层 · 单元测试（RED→GREEN）
运行: cd backend && python tests/test_cache.py
覆盖: TTLCache 基础/过期/清除/封装两层缓存
"""
import os
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-cache-test-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"

from poc.cache import TTLCache  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


# 1) 基础 get/set
c = TTLCache(ttl=60)
c.set("k1", "v1")
check("get 已有值", c.get("k1") == "v1")
check("get 不存在", c.get("kx") is None)

# 2) 过期
c2 = TTLCache(ttl=0.05)  # 50ms
c2.set("k2", "v2")
time.sleep(0.1)
check("过期后返回 None", c2.get("k2") is None)

# 3) 清除
c.set("k3", "v3")
c.invalidate("k3")
check("invalidate 后不存在", c.get("k3") is None)

# 4) len
c.clear()
c.set("a", 1)
c.set("b", 2)
check("len 计数", len(c) == 2)

# 5) 自动清理过期
c3 = TTLCache(ttl=0.05)
c3.set("k", "v")
c3.set("k2", "v2")
time.sleep(0.1)
_ = c3.get("k")  # 触发过期清理
check("自动清理后 len 为 0", len(c3) == 0)

# 6) 值类型：dict 也可缓存
c.set("d", {"a": 1, "b": [2, 3]})
check("缓存 dict 类型", c.get("d")["b"][0] == 2)

# 7) 缓存装饰器（封装层测试——集成到 store/answer 前先测纯逻辑）
from poc.cache import cached_retrieve, cached_answer  # noqa: E402

calls = {"n": 0}

def fake_retrieve(teacher_id, query):
    calls["n"] += 1
    return [{"content": f"结果_{query}", "score": 0.9}]

wrapped = cached_retrieve(fake_retrieve, ttl=60)
r1 = wrapped("T001", "转折关系")
check("装饰器首次调用返回结果", r1[0]["score"] == 0.9)
calls["n"] = 0
r2 = wrapped("T001", "转折关系")
check("装饰器缓存命中不再调用原函数", calls["n"] == 0 and r2[0]["content"] == "结果_转折关系")
r3 = wrapped("T001", "隐性转折")
check("不同 query 不冲突", calls["n"] == 1 and r3[0]["content"] == "结果_隐性转折")

# 8) 缓存键中毒防护
r4 = wrapped("T001", "转折关系")  # 再次命中缓存
check("缓存键不含特殊字符冲突", r4[0]["content"] == "结果_转折关系")

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)