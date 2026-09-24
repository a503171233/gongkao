# -*- coding: utf-8 -*-
"""向量库封装：Chroma 持久化 + embedding 适配（local/api/selftest 三模式）。
多老师隔离：一个老师一个 Collection（说明书 §6.3），所有函数按 teacher_id 操作。

稳定契约（防接口漂移）：
    upsert_chunks(chunks, teacher_id) -> int
    retrieve(query, teacher_id, top_n=None) -> list[dict]     # 带 score 的召回，阈值过滤在 answer 层
    collection_count(teacher_id) -> int
    list_documents(teacher_id) -> list[dict]
    delete_document(teacher_id, doc_name) -> int
    clear_collection(teacher_id) -> None
    list_collections() -> list[str]

Embedding 模型 / API key / 路径由模块级 _GLOBAL(Config) 共享；teacher 只决定 collection 归属。
"""
import hashlib
import math
import re
import threading

import chromadb
from chromadb.config import Settings

from .cache import TTLCache, _safe_key
from .config import Config
from .ingest import Chunk

# 模块级全局配置：embedding / 路径 / 密钥共享（实例属性，可被测试覆盖）
_GLOBAL = Config()

# 检索缓存（TTL 5 分钟；知识库更新时按老师前缀失效）
retrieve_cache = TTLCache(ttl=300)

# ---------- embedding：local=fastembed本地ONNX(推荐) / api=中转站 / selftest=离线哈希 ----------
_fastembed_model = None  # 懒加载单例
_fastembed_lock = threading.Lock()  # 同步路由跑在线程池，防并发首载双重加载模型（2C 服务器扛不住）


def _get_fastembed():
    global _fastembed_model
    if _fastembed_model is None:
        with _fastembed_lock:
            if _fastembed_model is None:
                from fastembed import TextEmbedding
                _fastembed_model = TextEmbedding(
                    model_name=_GLOBAL.embed_model,
                    cache_dir=str(_GLOBAL.model_cache),
                    threads=2,  # 2核服务器
                )
    return _fastembed_model


def _embed_local(texts: list[str]) -> list[list[float]]:
    model = _get_fastembed()
    return [list(map(float, v)) for v in model.embed(texts)]


def _embed_via_api(texts: list[str]) -> list[list[float]]:
    from openai import OpenAI
    client = OpenAI(api_key=_GLOBAL.api_key, base_url=_GLOBAL.api_base_url)
    out: list[list[float]] = []
    BATCH = 32
    for i in range(0, len(texts), BATCH):
        resp = client.embeddings.create(model=_GLOBAL.embed_model, input=texts[i:i + BATCH])
        out.extend([d.embedding for d in resp.data])
    return out


def _embed_selftest(texts: list[str], dim: int = 256) -> list[list[float]]:
    """离线哈希向量：仅用于无 API key 时的端到端自测，不可用于真实问答。"""
    out: list[list[float]] = []
    for t in texts:
        vec = [0.0] * dim
        for i in range(len(t) - 1):
            h = int(hashlib.md5(t[i:i + 2].encode("utf-8")).hexdigest(), 16)
            vec[h % dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        out.append([v / norm for v in vec])
    return out


def embed_texts(texts: list[str]) -> list[list[float]]:
    p = _GLOBAL.embed_provider
    if p == "selftest":
        return _embed_selftest(texts)
    if p == "local":
        return _embed_local(texts)
    if not _GLOBAL.api_key or _GLOBAL.api_key.startswith("sk-请"):
        raise RuntimeError("未配置 API_KEY：.env 中请填入中转站 key，或临时把 EMBED_PROVIDER=selftest")
    return _embed_via_api(texts)


# ---------- Chroma 封装（全部按 teacher_id 操作） ----------
def get_client() -> chromadb.PersistentClient:
    _GLOBAL.db_path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(_GLOBAL.db_path), settings=Settings(anonymized_telemetry=False))


def _collection(teacher_id: str):
    """获取老师专属 collection；显式 cosine 空间。历史 L2 空间自动删除重建（需重新 ingest）。"""
    client = get_client()
    name = f"teacher_{teacher_id}"
    existing = client.get_collection(name) if name in [c.name for c in client.list_collections()] else None
    if existing is not None and (existing.metadata or {}).get("hnsw:space") != "cosine":
        print(f"[warn] {name} 距离空间={(existing.metadata or {}).get('hnsw:space')}，自动删除重建（需重新 ingest）")
        client.delete_collection(name)
        existing = None
    if existing is not None:
        return existing
    return client.get_or_create_collection(
        name=name,
        metadata={"teacher_id": teacher_id, "hnsw:space": "cosine"},
    )


def upsert_chunks(chunks: list[Chunk], teacher_id: str) -> int:
    """入库：分块 → 向量化 → 写入该老师专属 collection。同 id 幂等覆盖。
    知识库变更 → 失效该老师检索缓存（保证新知识立即可检索）。
    """
    coll = _collection(teacher_id)
    if not chunks:
        return 0
    texts = [c.content for c in chunks]
    vectors = embed_texts(texts)
    ids = [
        f"{c.doc_name}_{c.idx}_{hashlib.md5(c.content.encode('utf-8')).hexdigest()[:8]}"
        for c in chunks
    ]
    metadatas = [{"teacher_id": teacher_id, "doc_name": c.doc_name, "idx": c.idx} for c in chunks]
    coll.upsert(ids=ids, documents=texts, embeddings=vectors, metadatas=metadatas)
    # 知识库变更 → 失效该老师检索缓存
    n = retrieve_cache.invalidate_prefix(f"retr|{teacher_id}|")
    return len(chunks)


def _query_variants(query: str) -> list[str]:
    """查询变体扩充：缓解"整句embedding稀释关键短语信号"导致的漏召回。
    变体：原句 / 去引号主干 / 引号内短语 / 去疑问助词版。去重保序，上限 4 个。
    """
    vs = [query]
    # 1) 去引号内容后的主干（把 "很多人认为" 当作普通文本参与匹配）
    core = re.sub(r'[“"]([^”"]+)[”"]', r"\1", query)
    if core.strip() and core != query:
        vs.append(core.strip())
    # 2) 引号内短语作为独立查询（讲义含该短语的块应直接命中）
    for m in re.findall(r'[“"]([^”"]+)[”"]', query):
        if m.strip():
            vs.append(m.strip())
    # 3) 去疑问助词
    no_q = re.sub(r"(吗|呢|么|怎|什么|？|\?|的)", "", query).strip()
    if no_q and no_q != query:
        vs.append(no_q)
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for v in vs:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out[:4]


def get_collection(teacher_id: str):
    """公开集合访问（供练习兜底/工具类模块使用；会惰性创建，读路径请用 _collection_exists）。"""
    return _collection(teacher_id)


def _collection_exists(teacher_id: str) -> bool:
    """只读探测老师 collection 是否存在（不创建）。检索读路径避免写副作用。"""
    client = get_client()
    return f"teacher_{teacher_id}" in [c.name for c in client.list_collections()]


def _filter_disabled_docs(teacher_id: str, hits: list[dict]) -> list[dict]:
    """#24 R5 权限控制：过滤已下架（registry.enabled=0）文档的命中块。
    注册表无记录/无下架 → 原样返回（零开销路径）。懒加载避免模块环。"""
    if not hits:
        return hits
    try:
        from .docreg import get_doc_registry
        disabled = get_doc_registry().disabled_docs(teacher_id)
    except Exception:  # noqa: BLE001 — 注册表故障不阻断检索主链路
        return hits
    if not disabled:
        return hits
    return [h for h in hits if (h.get("meta") or {}).get("doc_name") not in disabled]


def retrieve(query: str, teacher_id: str, top_n: int | None = None) -> list[dict]:
    """语义检索：问题(含变体)向量化 → 老师 collection 内召回 → 合并去重取最高分。
    空库返回 []。多变体一次 query 多向量传入，仅多一次本地 embedding（成本可忽略）。
    带检索缓存（TTL 5 分钟，键含 teacher_id 天然隔离）。
    #24 R5：返回前过滤已下架文档（缓存存全量，出口统一过滤——上下架即时生效）。
    """
    # 键用解析后的 top：retrieve(q,t) 与 retrieve(q,t,top_n=默认值) 共享同一缓存条目
    top = top_n or _GLOBAL.top_n
    cache_key = _safe_key("retr", teacher_id, query, top)
    hit = retrieve_cache.get(cache_key)
    if hit is not None:
        return _filter_disabled_docs(teacher_id, hit)

    # 读路径不创建 collection（不存在的老师/已清库直接返回空，不留垃圾库）
    if not _collection_exists(teacher_id):
        return []
    coll = _collection(teacher_id)
    total = coll.count()
    if total == 0:
        return []
    variants = _query_variants(query)
    qvecs = embed_texts(variants)
    # 单次查询召回上限不得超过集合现有块数，否则 Chroma 抛 NotEnoughElementsException
    n_per = min(max(top, 5), total)
    res = coll.query(query_embeddings=qvecs, n_results=n_per)
    merged: dict[str, dict] = {}
    if not res or not res.get("ids"):
        return []
    for gi in range(len(qvecs)):
        ids = res["ids"][gi]
        docs = res["documents"][gi]
        metas = res["metadatas"][gi]
        dists = res["distances"][gi]
        for i, _id in enumerate(ids):
            sim = max(0.0, 1.0 - float(dists[i]))
            meta = metas[i] or {}
            if _id not in merged or sim > merged[_id]["score"]:
                merged[_id] = {"id": _id, "content": docs[i], "meta": meta, "score": sim}
    hits = sorted(merged.values(), key=lambda h: h["score"], reverse=True)[:top]
    if hits:
        retrieve_cache.set(cache_key, hits)
    return _filter_disabled_docs(teacher_id, hits)


def collection_count(teacher_id: str) -> int:
    return _collection(teacher_id).count()


def list_documents(teacher_id: str) -> list[dict]:
    """按来源文档聚合块数，返回 [{doc_name, chunks, teacher_id}]。"""
    coll = _collection(teacher_id)
    data = coll.get(include=["metadatas"])
    agg: dict[str, int] = {}
    for m in data.get("metadatas", []):
        doc = m.get("doc_name", "未知")
        agg[doc] = agg.get(doc, 0) + 1
    return [
        {"doc_name": k, "chunks": v, "teacher_id": teacher_id}
        for k, v in sorted(agg.items(), key=lambda x: -x[1])
    ]


def delete_document(teacher_id: str, doc_name: str) -> int:
    """删除指定文档全部块（doc_name 精确匹配元数据）。返回删除块数。知识库变更 → 失效缓存。"""
    coll = _collection(teacher_id)
    data = coll.get(include=["metadatas"])
    ids = [
        doc_id for doc_id, m in zip(data.get("ids", []), data.get("metadatas", []))
        if m.get("doc_name") == doc_name
    ]
    if not ids:
        return 0
    coll.delete(ids=ids)
    retrieve_cache.invalidate_prefix(f"retr|{teacher_id}|")
    return len(ids)


def clear_collection(teacher_id: str) -> None:
    """删除某老师整个 collection（重置/自测清理）。"""
    client = get_client()
    name = f"teacher_{teacher_id}"
    if name in [c.name for c in client.list_collections()]:
        client.delete_collection(name=name)
    retrieve_cache.invalidate_prefix(f"retr|{teacher_id}|")


def list_collections() -> list[str]:
    return [c.name for c in get_client().list_collections()]
