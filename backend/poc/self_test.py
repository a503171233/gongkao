# -*- coding: utf-8 -*-
"""离线自测：不依赖外部 API，验证整条链路（多老师契约版）。
覆盖：分块 / 入库 / 检索 / 拒答 / 命中 / 文档管理 / 多老师隔离 / 空库保护 / 流式事件序列。
运行：python -m poc.cli selftest
"""
from .config import Config
from . import store


def run_selftest(cfg: Config) -> bool:
    # 强制离线 embedding + 禁 API key（作用于 store 模块级全局）
    store._GLOBAL.embed_provider = "selftest"
    store._GLOBAL.api_key = ""

    from .ingest import chunk_text, clean_text, Chunk
    from .answer import ask

    ok = True

    def check(name: str, cond: bool):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            ok = False

    try:
        # ---------- 1) 分块 / 清洗 ----------
        text = ("第一段内容：转折关系是言语理解高频考点。\n"
                "第二段内容：做题要先抓关联词。\n"
                "第三段内容：转折之后是重点。\n"
                "第四段内容：选项要忠于原文。")
        parts = chunk_text(text, chunk_size=30, overlap=5)
        check(f"分块>=2 且非空 ({len(parts)})", len(parts) >= 2 and all(p.strip() for p in parts))
        check("清洗去空行", clean_text("  a \n\n  b \n") == "a\nb")

        chunks = [Chunk(content=p, doc_name="自测讲义.md", idx=i) for i, p in enumerate(parts)]

        # ---------- 2) T001 入库 ----------
        store.clear_collection("T001")
        n = store.upsert_chunks(chunks, "T001")
        check(f"T001 入库 {n} 块", n == len(parts))
        check("T001 count 正确", store.collection_count("T001") == len(parts))

        # ---------- 3) 检索 ----------
        hits = store.retrieve("转折关系怎么做题", "T001", top_n=3)
        check("检索返回命中", len(hits) > 0)
        if hits:
            check("命中得分>0", hits[0]["score"] > 0)

        # ---------- 4) 命中问答（selftest mock 返回 Top1 片段） ----------
        r = ask("转折关系怎么做题", teacher_id="T001")
        check("命中 → 不拒答", not r.get("rejected", True))
        check("mock: 答案=Top1片段", (not r.get("rejected")) and r["answer"] == hits[0]["content"])
        check("返回引用片段", bool(r.get("hits")))

        # ---------- 5) 文档管理（list/delete） ----------
        docs = store.list_documents("T001")
        check("list_documents 含自测讲义", any(d["doc_name"] == "自测讲义.md" for d in docs))
        deleted = store.delete_document("T001", "自测讲义.md")
        check(f"delete_document 删除 {deleted} 块", deleted == len(parts))
        check("删除后 count==0", store.collection_count("T001") == 0)

        # ---------- 6) 空库拒答 ----------
        store.clear_collection("T001")
        check("空库检索返回空", store.retrieve("任何问题", "T001") == [])
        r2 = ask("任何问题", teacher_id="T001")
        check("空库 → 拒答且不调模型", r2.get("rejected", False))

        # ---------- 7) 多老师隔离（用数据归属验证，而非哈希相似度阈值） ----------
        store.clear_collection("T002")
        t2 = [Chunk(content=p, doc_name="逻辑判断讲义.md", idx=i)
              for i, p in enumerate(["假言命题是逻辑判断的核心考点。", "充分条件用箭头推理。", "肯前肯后，否后否前。"])]
        store.upsert_chunks(t2, "T002")
        check("T002 count==3", store.collection_count("T002") == 3)
        # 物理隔离铁证：各库 list_documents 只含自己的文档，不含对方的
        docs_t1 = store.list_documents("T001")
        docs_t2 = store.list_documents("T002")
        check("T001 库不含 T002 文档(物理隔离)",
              all(d["doc_name"] != "逻辑判断讲义.md" for d in docs_t1))
        check("T002 库不含 T001 文档(物理隔离)",
              all(d["doc_name"] != "自测讲义.md" for d in docs_t2))
        # 检索归属：T002 检索"假言命题"应命中自己的块，且命中块 doc_name 属 T002
        h2 = store.retrieve("假言命题怎么推理", "T002", top_n=2)
        check("T002 检索命中自己内容", len(h2) > 0)
        if h2:
            check("T002 命中块 doc_name=逻辑判断讲义.md",
                  h2[0]["meta"].get("doc_name") == "逻辑判断讲义.md")
        check("两老师 collection 独立存在",
              "teacher_T001" in store.list_collections() and "teacher_T002" in store.list_collections())

        # ---------- 8) 流式事件序列（selftest mock） ----------
        events = [item["type"] for item in ask("转折关系怎么做题", teacher_id="T001", stream=True)]
        check(f"流式事件序列首为 delta 末为 done ({events})",
              bool(events) and events[0] == "delta" and events[-1] == "done")
        check("流式含 refs 事件", "refs" in events)
    finally:
        # 清理测试数据，避免污染真实库
        store.clear_collection("T001")
        store.clear_collection("T002")

    print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
    return ok
