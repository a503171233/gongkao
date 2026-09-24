# -*- coding: utf-8 -*-
"""POC 命令行入口（多老师契约）：
  python -m poc.cli ingest <课件文件...> [--teacher T001]  # 入库（支持多文件/通配）
  python -m poc.cli ask "问题" [--teacher T001] [--stream]   # 提问
  python -m poc.cli selftest                                 # 离线自测（无需 API key）
  python -m poc.cli reset [--teacher T001]                   # 清空指定老师向量库
  python -m poc.cli docs [--teacher T001]                    # 文档列表
  python -m poc.cli reset-pass <用户名> <新密码>             # 重置用户密码（无邮箱方案）
  python -m poc.cli question add --teacher T001 --qtype choice \
      --question "..." --answer A --options "A..,B..,C.." --analysis "..."  # 加题库题
  python -m poc.cli question list --teacher T001              # 题库列表
"""
import argparse
import sys
from pathlib import Path

from .config import Config
from .ingest import ingest_file
from . import store

cfg = Config()


def cmd_ingest(files: list[str], teacher_id: str) -> int:
    total = 0
    for f in files:
        p = Path(f)
        if not p.exists():
            print(f"[skip] 文件不存在: {f}")
            continue
        tcfg = cfg.get_teacher(teacher_id)
        chunks = ingest_file(p, tcfg)
        n = store.upsert_chunks(chunks, teacher_id)
        total += n
        print(f"[ok] {p.name}: 分块 {len(chunks)} → 入库 {n}")
    tname = cfg.get_teacher(teacher_id).teacher_name
    print(f"完成。{tname}（{teacher_id}）知识库总块数: {store.collection_count(teacher_id)}")
    return 0


def cmd_ask(query: str, teacher_id: str, stream: bool) -> int:
    tcfg = cfg.get_teacher(teacher_id)
    if not cfg.api_key and cfg.embed_provider != "selftest":
        print("未配置 API_KEY。两种选择：\n  1) 在 .env 填入中转站 key（推荐）\n  2) 临时 export EMBED_PROVIDER=selftest 仅验证链路")
        return 1
    print(f"提问 [{tcfg.teacher_name}]: {query}\n" + "-" * 40)

    if stream:
        from .answer import ask as rag_ask
        for item in rag_ask(query, teacher_id=teacher_id, stream=True):
            if item["type"] == "delta":
                print(item["data"], end="", flush=True)
            elif item["type"] == "refs":
                print("\n" + "-" * 40)
                print(f"引用片段（{len(item['data'])} 条）:")
                for r in item["data"]:
                    print(f"  · {r['doc_name']} (score={r['score']})")
            elif item["type"] == "done":
                print()
        return 0

    from .answer import ask as rag_ask
    result = rag_ask(query, teacher_id=teacher_id)
    print(result["answer"])
    if result.get("hits"):
        print("-" * 40)
        print(f"引用片段 {len(result['hits'])} 条（最高相似度 {result['hits'][0]['score']:.3f}）：")
        for h in result["hits"]:
            doc = h.get("meta", {}).get("doc_name", "?")
            print(f"  · {doc} (score={h['score']:.3f})")
    return 0


def cmd_selftest() -> int:
    from .self_test import run_selftest
    return 0 if run_selftest(cfg) else 1


def cmd_reset(teacher_id: str) -> int:
    store.clear_collection(teacher_id)
    print(f"已清空 {cfg.get_teacher(teacher_id).teacher_name}（{teacher_id}）向量库")
    return 0


def cmd_docs(teacher_id: str) -> int:
    docs = store.list_documents(teacher_id)
    total = store.collection_count(teacher_id)
    print(f"{cfg.get_teacher(teacher_id).teacher_name}（{teacher_id}）知识库：{total} 块，{len(docs)} 个文档")
    for d in docs:
        print(f"  · {d['doc_name']}（{d['chunks']} 块）")
    return 0


def cmd_reset_pass(username: str, new_password: str) -> int:
    """重置用户密码（无邮箱方案：管理员/运维直改）。"""
    from .auth import AuthStore
    auth = AuthStore()
    user = auth.get_user_by_username(username.strip())
    if not user:
        print(f"[err] 用户不存在: {username}")
        return 1
    if len(new_password) < 6:
        print("[err] 新密码至少 6 位")
        return 1
    auth.set_password(user["user_id"], new_password)
    print(f"[ok] 已重置用户 {username}（{user['user_id']}）的密码")
    return 0


def cmd_question_add(args) -> int:
    from .study import get_study_store
    s = get_study_store()
    opts = [o.strip() for o in (args.options or "").split(",")] if args.options else []
    opts = [o for o in opts if o]
    try:
        q = s.add_question(args.teacher, args.qtype, args.question, args.answer,
                           opts, args.analysis or "", args.difficulty or 1)
        print(f"[ok] 已加题 #{q['id']}（{args.qtype}）")
    except ValueError as e:
        print(f"[err] {e}")
        return 1
    return 0


def cmd_question_list(args) -> int:
    from .study import get_study_store
    s = get_study_store()
    qs = s.list_questions(args.teacher, args.qtype or None)
    print(f"题库（{args.teacher}）：{len(qs)} 题")
    for q in qs:
        print(f"  #{q['id']} [{q['qtype']}] {q['question'][:40]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="poc", description="多老师 RAG 平台 M1/M2 POC")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="课件入库")
    p_ingest.add_argument("files", nargs="+", help="课件文件路径（md/txt/docx/pptx/pdf）")
    p_ingest.add_argument("--teacher", default="T001")

    p_ask = sub.add_parser("ask", help="提问")
    p_ask.add_argument("query", help="问题文本")
    p_ask.add_argument("--teacher", default="T001")
    p_ask.add_argument("--stream", action="store_true", help="SSE 流式输出")

    sub.add_parser("selftest", help="离线自测（无需 API key）")

    p_reset = sub.add_parser("reset", help="清空指定老师知识库")
    p_reset.add_argument("--teacher", default="T001")

    p_docs = sub.add_parser("docs", help="查看知识库文档列表")
    p_docs.add_argument("--teacher", default="T001")

    p_rp = sub.add_parser("reset-pass", help="重置用户密码（无邮箱方案）")
    p_rp.add_argument("username", help="用户名")
    p_rp.add_argument("new_password", help="新密码（≥6位）")

    p_q = sub.add_parser("question", help="题库管理")
    q_sub = p_q.add_subparsers(dest="qcmd", required=True)
    p_qa = q_sub.add_parser("add", help="新增题库题")
    p_qa.add_argument("--teacher", default="T001")
    p_qa.add_argument("--qtype", default="choice", help="choice/judge/essay")
    p_qa.add_argument("--question", required=True)
    p_qa.add_argument("--answer", required=True)
    p_qa.add_argument("--options", default="", help="逗号分隔（choice 用）")
    p_qa.add_argument("--analysis", default="")
    p_qa.add_argument("--difficulty", type=int, default=1)
    p_ql = q_sub.add_parser("list", help="题库列表")
    p_ql.add_argument("--teacher", default="T001")
    p_ql.add_argument("--qtype", default="")

    args = parser.parse_args(argv)

    if args.cmd == "ingest":
        return cmd_ingest(args.files, args.teacher)
    if args.cmd == "ask":
        return cmd_ask(args.query, args.teacher, args.stream)
    if args.cmd == "selftest":
        return cmd_selftest()
    if args.cmd == "reset":
        return cmd_reset(args.teacher)
    if args.cmd == "docs":
        return cmd_docs(args.teacher)
    if args.cmd == "reset-pass":
        return cmd_reset_pass(args.username, args.new_password)
    if args.cmd == "question":
        if args.qcmd == "add":
            return cmd_question_add(args)
        if args.qcmd == "list":
            return cmd_question_list(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())