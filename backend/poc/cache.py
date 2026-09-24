# -*- coding: utf-8 -*-
"""缓存层：TTLCache + 检索/回答两层缓存封装（内存实现，说明书 §8.1 多级缓存）。
设计：
  TTLCache       —— 线程安全 TTL 缓存原语（带惰性过期清理）
  cached_retrieve —— 检索缓存装饰器：键=(teacher_id, query)，省 embedding+向量检索
  cached_answer   —— LLM 回答缓存：键=(teacher_id, query)，非流式、非拒答才缓存
用法：
  from .cache import ttl_cache
  在 store.retrieve / answer.ask 内调用 ttl_cache.get/set，键含 teacher_id 天然隔离。
"""
import threading
import time


class TTLCache:
    """简单线程安全 TTL 缓存。key 任意可哈希；value 任意对象。"""

    def __init__(self, ttl: float = 300):
        self.ttl = ttl
        self._data: dict = {}
        self._expires: dict = {}
        self._lock = threading.Lock()

    def get(self, key) -> object | None:
        with self._lock:
            exp = self._expires.get(key)
            if exp is None:
                return None
            if time.monotonic() > exp:
                # 惰性过期清理
                del self._data[key]
                del self._expires[key]
                return None
            return self._data[key]

    def set(self, key, value) -> None:
        with self._lock:
            self._data[key] = value
            self._expires[key] = time.monotonic() + self.ttl

    def invalidate(self, key) -> None:
        with self._lock:
            self._data.pop(key, None)
            self._expires.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> int:
        """失效所有键 str(key) 以 prefix 开头的项（如按老师粒度清空）。返回失效数。"""
        with self._lock:
            keys = [k for k in self._data if str(k).startswith(prefix)]
            for k in keys:
                self._data.pop(k, None)
                self._expires.pop(k, None)
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._expires.clear()

    def __len__(self) -> int:
        # 先触发一次惰性清理，再计数
        now = time.monotonic()
        with self._lock:
            expired = [k for k, e in self._expires.items() if now > e]
            for k in expired:
                self._data.pop(k, None)
                self._expires.pop(k, None)
            return len(self._data)


# ---------- 两层缓存实例（模块级单例，全进程共享） ----------
# 检索缓存：TTL 5 分钟（老师更新知识库后手动 invalidate_prefix("retr:T001")）
retrieve_cache = TTLCache(ttl=300)
# 回答缓存：TTL 10 分钟（仅缓存非流式、非拒答）
answer_cache = TTLCache(ttl=600)


def _safe_key(*parts) -> str:
    """组装缓存键，防特殊字符注入/冲突。"""
    return "|".join(str(p) for p in parts)


def cached_retrieve(func, ttl: float = 300):
    """包装 retrieve 语义：fn(teacher_id, query, ...) → hits。
    返回兼容原签名的包装函数；命中缓存直接返回（浅拷贝防外部篡改）。
    """
    import copy

    cache = TTLCache(ttl=ttl)

    def wrapper(teacher_id: str, query: str, *args, **kwargs):
        key = _safe_key("retr", teacher_id, query)
        hit = cache.get(key)
        if hit is not None:
            return copy.deepcopy(hit)
        result = func(teacher_id, query, *args, **kwargs)
        if result:
            cache.set(key, copy.deepcopy(result))
        return result

    wrapper._cache = cache  # 暴露给外部做 invalidate（如更新知识库后清空）
    return wrapper


def cached_answer(func, ttl: float = 600):
    """包装 answer 语义：fn(query, teacher_id, stream=False) → dict{answer,...}。
    仅缓存非流式、非拒答、无 mock 的完整答案。
    """
    import copy

    cache = TTLCache(ttl=ttl)

    def wrapper(query: str, teacher_id: str = "T001", stream: bool = False, *args, **kwargs):
        if stream:
            return func(query, teacher_id=teacher_id, stream=True, *args, **kwargs)
        key = _safe_key("ans", teacher_id, query)
        hit = cache.get(key)
        if hit is not None:
            return copy.deepcopy(hit)
        result = func(query, teacher_id=teacher_id, stream=False, *args, **kwargs)
        # 只缓存正常回答（不缓存拒答/异常，保证更新资料后新知识立即可答）
        if result and not result.get("rejected") and not result.get("mock"):
            cache.set(key, copy.deepcopy(result))
        return result

    wrapper._cache = cache
    return wrapper
