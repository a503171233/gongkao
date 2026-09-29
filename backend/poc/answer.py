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
import os
import re

from . import llm
from . import store
from .cache import answer_cache, _safe_key
from .guard import run_guard, guard_counter

REJECT_TEXT = "该知识点XX老师暂未录入资料库，请勿自行拓展。可以换个具体题型或知识点再问哦。"


def reject_text(tcfg) -> str:
    """#8 文案修复：把占位「XX老师」替换为真实老师名（tcfg 有 teacher_name）。"""
    return REJECT_TEXT.replace("XX老师", getattr(tcfg, "teacher_name", "") or "老师")


# 批次 J：非知识点型提问（问候 / 平台使用 / 学习方法咨询）→ 通用助教模式。
# 此前「你好」「怎么制定复习计划」这类咨询命中「检索零命中 → 拒答」链路，
# 回复驴唇不对马嘴的「知识点未录入」。修复原则：**白名单式保守判定**——
# 凡含题型/模块/知识点特征词的问题一律走原知识库链路（防幻觉拒答不放松），
# 只有明确的问候/使用/方法咨询才走通用模式（LLM 正常作答但不讲知识点）。
_GENERAL_RE = re.compile(
    r"^(你好|您好|老师好|老师您好|hi|hello|嗨|哈喽|在吗|在么|谢谢|感谢|辛苦了)"
    r"|^(你是谁|你叫什么|介绍一下你自己|你能做什么|你会什么)"
    r"|^(怎么|如何|怎样|怎么才能|如何才能).{0,16}(学|复习|备考|刷题|提高|提升|规划|安排|使用|制定|准备)"
    r"|^(帮我)?(制定|生成|安排|做).{0,8}(计划|规划|安排)"
    r"|(怎么用|如何使用|怎么下载|如何下载|怎么充值|如何充值|怎么绑定|如何绑定"
    r"|怎么登录|如何登录|怎么注册|怎么改密码|忘记密码|会员|价格|多少钱"
    r"|几点|今天星期|你是机器人吗|人工智能吗)")

# 知识点特征：命中任意一个即视为知识点问题，绝不走通用模式（契约：空库拒答测试
# 用「一个根本不存在的知识点」这类 query，必须保持 rejected=true）。
_KNOWLEDGE_HINT_RE = re.compile(
    r"[?？]|题|知识点|考点|答案|解析|选项|例题|真题|技巧|秒杀|公式|定义|概念"
    r"|资料分析|数量关系|言语理解|判断推理|常识判断|申论|面试|行测|片段阅读"
    r"|逻辑|图形推理|类比推理|定义判断|逻辑填空|计算|方程|比重|增长率|基期"
    r"|范文|写作|口语|词汇|语法")


def _is_general_query(q: str) -> bool:
    """批次 J：是否问候/平台使用/学习方法类咨询（非知识点）。

    判定顺序：先排除知识点特征（含题型/模块/标点提问），再做白名单匹配；
    长问题（>40 字）大概率是具体提问，不进通用模式。"""
    ql = (q or "").strip()
    if not ql or len(ql) > 40:
        return False
    if _KNOWLEDGE_HINT_RE.search(ql):
        return False
    return bool(_GENERAL_RE.search(ql))


# 通用助教模式 system：正常作答但**不讲解具体知识点**（防幻觉原则不变），
# 学员实际想问知识点时引导回知识库，而不是输出拒答句。
GENERAL_TEMPLATE = """【身份设定】
你是{teacher_name}，{teacher_subject}领域资深讲师。学员当前的提问属于问候、平台使用或备考方法咨询，不是具体知识点的讲解请求。

【核心规则】（必须逐条遵守）
1. 正常、亲切、口语化地回答：问候热情回应；方法咨询给具体可执行的步骤建议；平台使用问题给清晰指引。
2. 不要讲解任何具体知识点、题目解法、公式推导——这些必须依据教学资料。若学员实际想问知识点，热情引导：具体题目讲解需要依据教学资料，建议在「知识库」搜索该知识点，或换个具体题型再问我哦。
3. 禁止编造：不得虚构课程、资料、价格、功能细节；不确定的平台信息建议学员在「我的」页面查看。
4. 篇幅适中（一般不超过 200 字），语气与{teacher_name}课堂一致，善用"咱们"。"""

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
5. 学员问候、问平台使用或备考方法（与具体知识点无关）时正常自然回答；若【教学资料】中没有学员所问知识点的对应内容，直接原样输出下面这句话，不要解释、不要补充：{reject}
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
5. 学员问候、问平台使用或备考方法（与具体知识点无关）时正常自然回答；若【教学资料】中没有学员所问知识点的对应内容，直接原样输出下面这句话，不要解释、不要补充：{reject}
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
5. 学员问候、问平台使用或备考方法（与具体知识点无关）时正常自然回答；若【教学资料】中没有学员所问知识点的对应内容，直接原样输出下面这句话，不要解释、不要补充：{reject}
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


def build_messages(tcfg, hits: list[dict], query: str, history: list[dict] | None = None,
                   attachments: list[dict] | None = None) -> list[dict]:
    """组装 messages：system(人设+教学资料) + 历史对话(可选) + 当前问题。
    history: ChatStore.get_messages 返回的历史，仅保留 user/assistant 防注入。
    attachments（批次28）：[{kind,name,text,image_data_url}] ——
    文本附件拼入 user 消息前缀（每附件 ≤8000 字）；图片附件把 user 消息
    升级为多模态 content 数组（OpenAI image_url 格式，需视觉模型通道）。
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
    # 批次28：附件成为合法依据来源——否则 system 的"资料没有的内容不答"
    # 会压过附件内容，模型对附件问题照样拒答/描述为空白
    if any(a.get("kind") in ("file", "image") for a in (attachments or [])):
        system += ("\n\n【学员附件说明】学员本次提问上传了附件（资料文档和/或图片）。"
                   "附件中的文字与图片中清晰可见的内容（文字、数字、图形、题目等）"
                   "均视为可依据的教学材料，请直接读取并结合附件作答——"
                   "读取附件内容不算编造。附件与教学资料都没有的内容，不答、不编、不推测。")
    messages = [{"role": "system", "content": system}]
    for m in history or []:
        # 防脏输入：历史项来自 DB，支持缺字段、content 为 None；非 dict 条目直接跳过
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    # ── 批次28：附件注入 ──
    atts = [a for a in (attachments or []) if isinstance(a, dict)]
    texts = [a for a in atts if a.get("kind") == "file" and a.get("text")]
    images = [a for a in atts if a.get("kind") == "image" and a.get("image_data_url")]
    if not texts and not images:
        messages.append({"role": "user", "content": query})
        return messages
    parts = []
    for a in texts:
        parts.append("【学员上传资料《" + str(a.get("name") or "附件") + "》】\n"
                     + str(a.get("text") or "")[:8000])
    text_body = query if query.strip() else "请结合我上传的内容进行讲解。"
    if texts:
        parts.append("【学员提问】" + text_body)
        full_text = "\n\n".join(parts)
    else:
        full_text = "【学员提问】" + text_body + "（已附图片，请结合图片内容解答）"
    if images:
        content: list = [{"type": "text", "text": full_text}]
        for img in images[:3]:
            content.append({"type": "image_url",
                            "image_url": {"url": img["image_data_url"]}})
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": full_text})
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


def _call_llm(messages, tcfg, channels: list[dict] | None = None) -> str:
    """B7：非流式调用，含超时 + 网络/5xx 重试 + 主模型降级（实现在 llm.py）。
    channels（批次28）：视觉候选链 [{model, base_url, api_key}, ...]——
    带图请求按序尝试，某通道失败（超时/网关限制/非标准响应）自动切下一个；
    文本请求传 None（单通道默认模型）。非流式失败时无已产出内容，切换无副作用。"""
    channels = channels or [{}]
    last_err: Exception | None = None
    for ch in channels:
        try:
            return llm.call_llm(messages, tcfg, model=ch.get("model"),
                                base_url=ch.get("base_url"), api_key=ch.get("api_key"))
        except Exception as e:  # noqa: BLE001  换下一个视觉候选
            last_err = e
    raise last_err  # type: ignore[misc]


def _stream_llm(messages, tcfg, channels: list[dict] | None = None):
    """B7：流式调用，含断流干净收尾 + 未产出前重试/降级（实现在 llm.py）。
    异常直接抛给 _stream_chain → api.py event_stream 转 SSE error 事件（协议已对齐）。
    channels（批次28）：视觉候选链——仅当某通道在**产出首个 delta 之前**失败
    才切换下一候选（首包前失败无已发送内容，切换安全）；一旦已产出 delta，
    异常原样抛出交由上层断流处理（换模型重发会导致前端内容重复）。
    """
    channels = channels or [{}]
    last_err: Exception | None = None
    for ch in channels:
        produced = False
        try:
            for item in llm.stream_llm(messages, tcfg, model=ch.get("model"),
                                       base_url=ch.get("base_url"),
                                       api_key=ch.get("api_key")):
                produced = True
                yield item
            return
        except Exception as e:  # noqa: BLE001
            if produced:
                raise          # 已有输出 → 不换模型，交上层断流兜底
            last_err = e       # 首包前失败 → 换下一个视觉候选
    raise last_err  # type: ignore[misc]


def _vision_channels() -> list[dict]:
    """批次28：视觉模型候选链——按序返回可用视觉通道（全部启用且带原始 key）。
    VISION_MODEL 环境变量可覆盖候选顺序（逗号分隔），默认
    "deepseek-v4.1-flash,qwen3.8-flash,gpt-5.6-luna"——2026-09-28 用 2026
    测试图实测网关 7 模型：deepseek-v4.1-flash / qwen3.8-flash 识别正确
    （gpt-5.6-luna 60s 超时 + 偶发"仅限指定客户端"限制；deepseek-v4-pro
    对 system+多模态输出审核文本；glm-5.3 无视觉；grok/agnes 识别错），
    luna 留作路由恢复时的备选，候选链保证任一通道漂移自动切换。
    注意必须用 get_model() 取原始 key：list_models() 的 api_key 是脱敏掩码
    （sk-a****vLvb），拿掩码调网关会 401 Invalid token。
    返回空列表 → 上层给可读错误，不静默吞图。"""
    names = [n.strip() for n in
             os.getenv("VISION_MODEL",
                       "deepseek-v4.1-flash,qwen3.8-flash,gpt-5.6-luna").split(",")
             if n.strip()]
    out: list[dict] = []
    try:
        from .models import get_ai_model_store
        store_ = get_ai_model_store()
        for name in names:
            row = store_.get_model(name)
            if row and row.get("enabled", True) and row.get("api_key"):
                out.append({"model": row["model_id"], "base_url": row.get("base_url") or None,
                            "api_key": row["api_key"]})
    except Exception:  # noqa: BLE001  注册表不可用时按未配置处理
        pass
    return out


def _gen_reject(tcfg=None):
    yield {"type": "delta", "data": reject_text(tcfg) if tcfg else REJECT_TEXT}
    yield {"type": "refs", "data": []}
    yield {"type": "done"}


# ---------------- 批次 J · 通用助教模式（问候/平台/方法咨询） ----------------

_GENERAL_FALLBACK = ("你好呀！我是{t}。具体题目的讲解需要依据教学资料，"
                     "建议在「知识库」里搜索该知识点，或者换个具体题型再问我哦。")


def _general_messages(query: str, tcfg) -> list[dict]:
    return [
        {"role": "system", "content": GENERAL_TEMPLATE.format(
            teacher_name=tcfg.teacher_name, teacher_subject=tcfg.teacher_subject)},
        {"role": "user", "content": query},
    ]


def _general_answer(query: str, tcfg) -> dict:
    """通用模式非流式：LLM 正常作答（独立缓存命名空间 gans，与知识库答案隔离）。
    通用回答不做讲义守卫（无教学资料依据，守卫必然失配）；LLM 不可用时给
    固定引导文案，保证链路永不出现「知识点未录入」的驴唇不对马嘴。"""
    cache_key = _safe_key("gans", tcfg.teacher_id, query)
    hit = answer_cache.get(cache_key)
    if hit is not None:
        return hit
    try:
        answer = _call_llm(_general_messages(query, tcfg), tcfg)
    except Exception as e:  # noqa: BLE001  LLM 不可用 → 固定引导文案
        print(f"[answer] 通用模式 LLM 失败，用引导文案：{type(e).__name__}", flush=True)
        answer = _GENERAL_FALLBACK.format(t=tcfg.teacher_name or "老师")
    result = {"answer": answer, "hits": [], "rejected": False, "general": True}
    answer_cache.set(cache_key, result)
    return result


def _gen_general(query: str, tcfg):
    """通用模式流式：一次性取全文后按 ~48 字分片推送（前端流式渲染体验一致）。"""
    try:
        answer = _call_llm(_general_messages(query, tcfg), tcfg)
    except Exception as e:  # noqa: BLE001
        print(f"[answer] 通用模式 LLM 失败，用引导文案：{type(e).__name__}", flush=True)
        answer = _GENERAL_FALLBACK.format(t=tcfg.teacher_name or "老师")
    for i in range(0, len(answer), 48):
        yield {"type": "delta", "data": answer[i:i + 48]}
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
        resume: str = "", attachments: list[dict] | None = None):
    """完整问答链路。teacher_id 决定老师配置与知识库。stream=True 返回事件生成器。
    history: 可选会话历史（ChatStore.get_messages 结果），注入多轮上下文。
    resume: 断点续答（SSE 断线重连）——前端已收到的部分答案；仅 stream=True 时生效，
    非流式请求忽略该字段（行为与原契约一致）。resume 模式跳过 refs 事件（前端已持有）。
    attachments（批次28）：附件列表 [{kind,name,text,image_data_url}]——
    文本附件直接拼入 prompt；图片附件走视觉模型（VISION_MODEL 注册条目）。
    """
    query = query.strip()
    if not query:
        raise ValueError("问题不能为空")
    tcfg = store._GLOBAL.get_teacher(teacher_id)
    hits = _retrieve_hits(query, tcfg)
    # 批次28：附件提问不做检索空判——学员上传资料/图片问"图里是什么"这类
    # 问题本就不依赖知识库命中，检索无命中也应结合附件作答（依据来源=附件）
    if not hits and not attachments:
        # 批次 J：问候/平台使用/学习方法咨询 → 通用助教模式（不拒答、不讲知识点）
        if _is_general_query(query) and not resume:
            if stream:
                return _gen_general(query, tcfg)
            return _general_answer(query, tcfg)
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

    messages = build_messages(tcfg, hits, query, history=history, attachments=attachments)
    # 批次28：带图请求必须走视觉模型通道（文本模型收到 image_url 直接 4xx）
    _has_img = llm._has_image(messages)
    channels: list[dict] = []
    if _has_img:
        channels = _vision_channels()
        if not channels:
            raise ValueError("图片理解模型未配置，请联系管理员或改用文字提问")
    if stream:
        return _stream_chain(messages, tcfg, hits, teacher_id, resume=resume,
                             channels=channels, soft_guard=bool(attachments))
    # 问题级缓存（§8.1）：键含 history 哈希，多轮上下文不串；命中直接返回
    # 批次28：缓存键不含 attachments——带附件请求完全绕过缓存（读写均跳），
    # 防止"带图答案"污染后续无附件的同 query 查询
    hist_key = hashlib.md5(
        json.dumps(history or [], ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]
    cache_key = _safe_key("ans", teacher_id, query, hist_key)
    if not attachments:
        hit = answer_cache.get(cache_key)
        if hit is not None:
            return hit
    answer = _call_llm(messages, tcfg, channels=channels)
    # ── 幻觉检测分级守卫（§9.4 v2）：必须在写缓存前检查 ──
    # block → 拒答不入缓存；confirm → 放行+提示但不入缓存（重问重新生成）；
    # warn → 放行+提示并入缓存；pass → 原行为
    # 批次28：附件回答的依据=学员上传材料（不在讲义内），讲义比对必然失配——
    # block 降级为 warn（放行+提示，不拦回答）
    passed, guard_detail = run_guard(answer, hits, tcfg, teacher_id, query=query)
    level = guard_detail.get("level") or ("pass" if passed else "block")
    if level == "block" and attachments:
        level = "warn"
        guard_detail = {"level": "warn", "soft_fails": ["学员附件回答，未做讲义强校验"]}
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
    if level != "confirm" and not attachments:
        answer_cache.set(cache_key, result)
    return result


def _msg_text(content) -> str:
    """批次28：user 消息文本提取——content 可为 str 或多模态数组（text 块拼接）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


def _stream_chain(messages, tcfg, hits, teacher_id, resume: str = "",
                  channels: list[dict] | None = None, soft_guard: bool = False):
    """流式链：边推边累积；done 前对完整回答跑幻觉检测分级守卫。
    block → guard_block 事件（前端替换为拒答提示，链路终止）；
    warn/confirm → guard_warn 事件（内容照常，前端附提示条，refs/done 照发）。
    resume（断点续答）：预置前端已收到的部分答案进累积缓冲（守卫对全文跑）；
    LLM 上下文末尾内存追加续写指令（不入库、不入缓存键）；refs 事件跳过（前端已持有）。
    channels（批次28）：带图请求的视觉候选链透传（首包前失败自动换候选）。
    soft_guard（批次28）：附件回答（依据=学员上传材料，不在讲义）时 True——
    守卫 block 降级为 warn，不拦回答。
    """
    buf: list[str] = []
    query = _msg_text(messages[-1]["content"]) if messages else ""
    if resume:
        buf.append(resume)
        messages = messages + [
            {"role": "assistant", "content": resume},
            {"role": "user",
             "content": "上一条回复因网络中断未发送完整。请从中断处继续输出剩余内容，衔接自然，禁止重复已输出内容。"},
        ]
    for item in _stream_llm(messages, tcfg, channels=channels):
        if item["type"] == "delta":
            buf.append(item["data"])
        yield item
    answer = "".join(buf)
    if answer:
        passed, detail = run_guard(answer, hits, tcfg, teacher_id, query=query)
        level = detail.get("level") or ("pass" if passed else "block")
        if level == "block" and soft_guard:
            level = "warn"
            detail = {"level": "warn", "soft_fails": ["学员附件回答，未做讲义强校验"]}
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
