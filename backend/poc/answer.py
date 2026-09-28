# -*- coding: utf-8 -*-
"""问答链路：检索 → Prompt 组装 → LLM 推理 → 拒答兜底 → 可选 SSE 流式。
对应说明书 §4.1/§4.5/§7.1。

稳定契约：
    ask(query, teacher_id="T001", stream=False)
      非流式返回 dict{answer, hits, rejected}
      流式返回生成器，产出事件 dict：
        {"type":"delta", "data": 增量文本}
        {"type":"refs",  "data": [{"doc_name","score"}, ...]}
        {"type":"done"}
    teacher_id 决定用哪套老师配置（人设/模型/阈值）与哪个知识库。
"""
import hashlib
import json

from . import llm
from . import store
from .cache import answer_cache, _safe_key
from .guard import run_guard, guard_counter

REJECT_TEXT = "该知识点XX老师暂未录入资料库，请勿自行拓展。可以换个具体题型或知识点再问哦。"


def reject_text(tcfg) -> str:
    """#8 文案修复：把占位「XX老师」替换为真实老师名（tcfg 有 teacher_name）。"""
    return REJECT_TEXT.replace("XX老师", getattr(tcfg, "teacher_name", "") or "老师")

# B8 v2 分级守卫提示文案（warn=轻度提醒 / confirm=二次确认；block 走 REJECT_TEXT）
GUARD_NOTICES = {
    "warn": "💡 核心内容已通过教学资料校验，部分表述为助教补充讲解，仅供参考。",
    "confirm": "⚠️ 本回答与讲义匹配度一般，可能包含助教个人理解，请以讲义为准。",
}

SYSTEM_TEMPLATE = """【身份设定】
你是{teacher_name}，专注{teacher_subject}教学与解题方法研究，是{teacher_subject}领域的资深讲师，常年带公考学员刷题，熟悉考生常见误区与高频失分点。

【核心规则】（回答必须逐条遵守）
1. 只依据下方【教学资料】作答：所有知识点、解题方法、结论均须能在资料中找到依据；资料没有的内容，不答、不编、不推测。
2. 讲解题目必须按固定步骤展开，步骤齐全、逐步说明、每步交代依据：
   做题思路 → 题干拆解 → 选项分析 → 易错点提醒
3. 用{teacher_name}上课的口吻作答：像课堂上带学生讲题一样通俗、亲切、有互动，善用“咱们”“你注意”“这里要看清”等课堂用语；禁止书面腔、机器话术、条款式罗列。
4. 引用资料内容时，顺口自然带出来源：如“咱们讲义《{doc_hint}》里讲过”，不要生硬标注页码，更不得凭空编造出处。
5. 若【教学资料】中没有对应内容，直接原样输出下面这句话，不要解释、不要补充：{reject}
6. 篇幅适中：把题讲透、把概念说清；用自己的话讲解，不要大段照抄资料原文。

【教学资料】
{context}"""

# A4 模板变体：同一老师可选不同人设/风格（teacher 注册表 prompt_style 字段切换）。
# 所有变体保留核心规则（只依据资料/步骤讲解/拒答原句），仅风格措辞不同。
SYSTEM_TEMPLATES: dict[str, str] = {
    # 默认：课堂带讲风（亲切互动、口语化）
    "classroom": SYSTEM_TEMPLATE,
    # 简洁风：言简意赅、少废话
    "concise": """【身份设定】
你是{teacher_name}，专注{teacher_subject}教学与解题方法研究，是{teacher_subject}领域的资深讲师。

【核心规则】（回答必须逐条遵守）
1. 只依据下方【教学资料】作答：资料没有的内容，不答、不编、不推测。
2. 讲解题目必须按固定步骤展开：做题思路 → 题干拆解 → 选项分析 → 易错点提醒。
3. 表达简洁克制，直接给结论与依据，禁止寒暄、禁止冗余铺陈。
4. 引用资料内容时顺口带出来源（如《{doc_hint}》），不得凭空编造出处。
5. 若【教学资料】中没有对应内容，直接原样输出下面这句话，不要解释、不要补充：{reject}
6. 篇幅适中：把题讲透即可，不重复。

【教学资料】
{context}""",
    # 应试风：强调考点/答题技巧/分值意识
    "exam": """【身份设定】
你是{teacher_name}，{teacher_subject}领域资深讲师，常年带公考学员冲刺，熟悉高频考点与命题规律。

【核心规则】（回答必须逐条遵守）
1. 只依据下方【教学资料】作答：所有知识点、结论均须能在资料中找到依据；资料没有的内容，不答、不编、不推测。
2. 讲解必须按固定步骤展开：做题思路 → 题干拆解 → 选项分析 → 易错点提醒，并标注考点与易错陷阱。
3. 以应试导向讲解：点出这是哪类高频考点、命题人常见挖坑点、考场如何快速定位答案。
4. 引用资料内容时顺口带出来源（如《{doc_hint}》），不得凭空编造出处。
5. 若【教学资料】中没有对应内容，直接原样输出下面这句话，不要解释、不要补充：{reject}
6. 篇幅适中：把题讲透、把考点点清。

【教学资料】
{context}""",
}


def _system_template(style: str) -> str:
    """按老师配置选择 prompt 模板变体；未知风格回退默认 classroom。"""
    return SYSTEM_TEMPLATES.get(style, SYSTEM_TEMPLATE)


def build_context(hits: list[dict]) -> str:
    parts = []
    for i, h in enumerate(hits, 1):
        doc = h.get("meta", {}).get("doc_name", "讲义")
        parts.append(f"[片段{i}｜来源:{doc}]\n{h.get('content', '')}")
    return "\n\n".join(parts)


def build_messages(tcfg, hits: list[dict], query: str, history: list[dict] | None = None) -> list[dict]:
    """组装 messages：system(人设+教学资料) + 历史对话(可选) + 当前问题。
    history: ChatStore.get_messages 返回的历史，仅保留 user/assistant 防注入。
    """
    # 按命中顺序去重取首个 doc_name（任务书：来源文档名去重取首个）
    doc_hint = "讲义"
    for h in hits:
        doc = h.get("meta", {}).get("doc_name", "")
        if doc:
            doc_hint = doc
            break
    system = _system_template(tcfg.prompt_style).format(
        teacher_name=tcfg.teacher_name,
        teacher_subject=tcfg.teacher_subject,
        doc_hint=doc_hint,
        reject=reject_text(tcfg),
        context=build_context(hits),
    )
    messages = [{"role": "system", "content": system}]
    for m in history or []:
        # 防脏输入：历史项来自 DB，支持缺字段、content 为 None；非 dict 条目直接跳过
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": query})
    return messages


def _threshold(tcfg) -> float:
    # selftest 哈希向量相似度整体偏低，用 0.1；真实 embedding 用老师配置阈值
    return 0.1 if store._GLOBAL.embed_provider == "selftest" else tcfg.threshold


def _refs(hits: list[dict]) -> list[dict]:
    return [
        {"doc_name": h.get("meta", {}).get("doc_name", ""), "score": round(h["score"], 3)}
        for h in hits
    ]


def _retrieve_hits(query: str, tcfg) -> list[dict]:
    hits = store.retrieve(query, tcfg.teacher_id)
    return [h for h in hits if h["score"] >= _threshold(tcfg)]


def _openai():
    """B7：客户端构造逻辑已移入 llm.py（密钥/端点/超时全从 config 读取）。"""
    return llm._openai()


def _call_llm(messages, tcfg) -> str:
    """B7：非流式调用，含超时 + 网络/5xx 重试 + 主模型降级（实现在 llm.py）。"""
    return llm.call_llm(messages, tcfg)


def _stream_llm(messages, tcfg):
    """B7：流式调用，含断流干净收尾 + 未产出前重试/降级（实现在 llm.py）。
    异常直接抛给 _stream_chain → api.py event_stream 转 SSE error 事件（协议已对齐）。
    """
    yield from llm.stream_llm(messages, tcfg)


def _gen_reject(tcfg=None):
    yield {"type": "delta", "data": reject_text(tcfg) if tcfg else REJECT_TEXT}
    yield {"type": "refs", "data": []}
    yield {"type": "done"}


def _gen_mock(answer: str, hits: list[dict], resume: str = ""):
    """selftest 模式 mock 流。resume（断点续答）时只发剩余部分，避免前端内容重复。"""
    text = answer[len(resume):] if resume and answer.startswith(resume) else answer
    yield {"type": "delta", "data": text}
    if not resume:
        yield {"type": "refs", "data": _refs(hits)}
    yield {"type": "done"}


def _gen_silent_done():
    """断点续答场景专用：重连后检索已无命中（如知识库被清空），
    不覆盖前端已收到的部分答案，静默发 done 收尾。"""
    yield {"type": "done"}


def ask(query: str, teacher_id: str = "T001", stream: bool = False, history: list[dict] | None = None,
        resume: str = ""):
    """完整问答链路。teacher_id 决定老师配置与知识库。stream=True 返回事件生成器。
    history: 可选会话历史（ChatStore.get_messages 结果），注入多轮上下文。
    resume: 断点续答（SSE 断线重连）——前端已收到的部分答案；仅 stream=True 时生效，
    非流式请求忽略该字段（行为与原契约一致）。resume 模式跳过 refs 事件（前端已持有）。
    """
    query = query.strip()
    if not query:
        raise ValueError("问题不能为空")
    tcfg = store._GLOBAL.get_teacher(teacher_id)
    hits = _retrieve_hits(query, tcfg)
    if not hits:
        # 无匹配 → 拒答，不调大模型（说明书 §4.2 2001 分支）
        if stream:
            # 断点续答时检索无命中（如知识库被清空）：不覆盖前端已有内容，静默收尾
            if resume:
                return _gen_silent_done()
            return _gen_reject(tcfg)
        return {"answer": reject_text(tcfg), "hits": [], "rejected": True}

    if store._GLOBAL.embed_provider == "selftest":
        # 离线自测：返回 Top1 片段作为"答案"，验证链路
        answer = hits[0]["content"]
        if stream:
            return _gen_mock(answer, hits, resume=resume)
        return {"answer": answer, "hits": hits, "rejected": False, "mock": True}

    # 27-L：模型注册表默认模型自带通道密钥（base_url/api_key 独立），
    # 全局 .env key 仅在注册表不可用时兜底必需——两路任一有 key 即放行。
    _eff = None
    try:
        from .models import get_ai_model_store
        _eff = get_ai_model_store().effective_default()
    except Exception:  # noqa: BLE001
        pass
    _api_key_now = (_eff or {}).get("api_key") or store._GLOBAL.api_key or ""
    if not _api_key_now or _api_key_now.startswith("sk-请"):
        raise RuntimeError("未配置 API_KEY：请在「AI 模型管理」为默认模型填写密钥，或在 .env 填入中转站 key")

    messages = build_messages(tcfg, hits, query, history=history)
    if stream:
        return _stream_chain(messages, tcfg, hits, teacher_id, resume=resume)
    # 问题级缓存（§8.1）：键含 history 哈希，多轮上下文不串；命中直接返回
    hist_key = hashlib.md5(
        json.dumps(history or [], ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]
    cache_key = _safe_key("ans", teacher_id, query, hist_key)
    hit = answer_cache.get(cache_key)
    if hit is not None:
        return hit
    answer = _call_llm(messages, tcfg)
    # ── 幻觉检测分级守卫（§9.4 v2）：必须在写缓存前检查 ──
    # block → 拒答不入缓存；confirm → 放行+提示但不入缓存（重问重新生成）；
    # warn → 放行+提示并入缓存；pass → 原行为
    passed, guard_detail = run_guard(answer, hits, tcfg, teacher_id, query=query)
    level = guard_detail.get("level") or ("pass" if passed else "block")
    if level == "block":
        reason = (guard_detail.get("fails") or ["unknown"])[0]
        guard_counter.record(teacher_id, query, reason, level="block")
        return {
            "answer": REJECT_TEXT, "hits": hits, "rejected": True,
            "guard_fails": guard_detail.get("fails", []),
        }
    if level in GUARD_NOTICES:
        soft = guard_detail.get("soft_fails") or ["unknown"]
        guard_counter.record(teacher_id, query, soft[0], level=level)
    result = {
        "answer": answer, "hits": hits, "rejected": False, "guard_fails": [],
        "guard_level": level, "guard_notice": GUARD_NOTICES.get(level, ""),
    }
    if level != "confirm":
        answer_cache.set(cache_key, result)
    return result


def _stream_chain(messages, tcfg, hits, teacher_id, resume: str = ""):
    """流式链：边推边累积；done 前对完整回答跑幻觉检测分级守卫。
    block → guard_block 事件（前端替换为拒答提示，链路终止）；
    warn/confirm → guard_warn 事件（内容照常，前端附提示条，refs/done 照发）。
    resume（断点续答）：预置前端已收到的部分答案进累积缓冲（守卫对全文跑）；
    LLM 上下文末尾内存追加续写指令（不入库、不入缓存键）；refs 事件跳过（前端已持有）。
    """
    buf: list[str] = []
    query = messages[-1]["content"] if messages else ""
    if resume:
        buf.append(resume)
        messages = messages + [
            {"role": "assistant", "content": resume},
            {"role": "user",
             "content": "上一条回复因网络中断未发送完整。请从中断处继续输出剩余内容，衔接自然，禁止重复已输出内容。"},
        ]
    for item in _stream_llm(messages, tcfg):
        if item["type"] == "delta":
            buf.append(item["data"])
        yield item
    answer = "".join(buf)
    if answer:
        passed, detail = run_guard(answer, hits, tcfg, teacher_id, query=query)
        level = detail.get("level") or ("pass" if passed else "block")
        if level == "block":
            reason = (detail.get("fails") or ["unknown"])[0]
            guard_counter.record(teacher_id, query, reason, level="block")
            yield {"type": "guard_block",
                   "data": {"fails": detail.get("fails", []), "reason": reason}}
            return  # 终止生成器：不再发 refs/done，前端收到 guard_block 后结束
        if level in GUARD_NOTICES:
            soft = detail.get("soft_fails") or ["unknown"]
            guard_counter.record(teacher_id, query, soft[0], level=level)
            yield {"type": "guard_warn",
                   "data": {"level": level, "notice": GUARD_NOTICES[level],
                            "reasons": soft, "scene": detail.get("scene", "")}}
    if not resume:
        # 续答模式前端已持有引用，跳过 refs 事件
        yield {"type": "refs", "data": _refs(hits)}
    yield {"type": "done"}
