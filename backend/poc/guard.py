# -*- coding: utf-8 -*-
"""幻觉检测三道关卡 v2 —— 分级拦截 + 可配置策略（说明书 §9.4 升级版）。

v1 痛点（一刀切）：任一关卡失败 → 整体拒答。教学口语、序数（第N）、
行首列表编号、算式计算结果、同义改写等合理助教表达全部被误杀。

v2 分级处理：
    pass    放行（完全通过）
    warn    轻度提醒（回答照常返回，前端附提示条）
    confirm 二次确认（回答返回但附"请以讲义为准"提示，不入缓存）
    block   硬拦截（确认与资料矛盾/凭空编造 → 拒答文案）

关卡1 引用一致性：答案与片段的公共 4-gram 数分档
    ≥ ref_strong_hits(3)   → 强引用，直过
    1 ~ ref_strong_hits-1  → 弱引用 → warn（改写较多但确有引用）
    0                      → 无引用证据 → confirm（与超纲证据叠加时升级 block）
关卡2 关键事实校验（数字三分类）：
    关键数字（百分比/年份/带量纲/统计词前缀）未命中语料 → block
    普通数字未命中 > fact_max_miss → confirm；≥ fact_block_miss → block
    豁免：算式计算结果（等式子句/变量=值/结果值全文复用）、序数（第N）、
          行首或空格后列表编号（1. 2、 3）、问题原文数字（回声用户输入）
关卡3 超纲反查（断言句占比分档，v1 为单句即拦）：
    占比 > scope_block_ratio(0.70)   → block
    占比 > scope_confirm_ratio(0.50) → confirm
    占比 > scope_pass_ratio(0.30)    → warn
    设问句（？结尾）与 ≤40 字引导互动句不参与反查；
    断言句数 < scope_min_sentences 时比例不可靠：全超纲才 block，否则封顶 confirm

策略三级叠加（后写覆盖前写，均可配置）：
    ① 学科预设（teacher_subject 命中 _SUBJECT_PRESETS 关键词）
    ② 场景覆盖（detect_scene 按 query 识别 chat/problem/concept/general）
    ③ 老师级覆盖（teachers 表 guard_config JSON 列 → tcfg.guard_policy）

契约兼容：
    run_guard 返回 (passed, detail)；passed=False 仅当 level=block；
    detail.fails 仅 block 档填充（枚举 reference/fact/out_of_scope 不变）；
    detail 新增 level/soft_fails/scene/scores 字段。旧式调用方行为不变。
    GuardCounter：total/by_teacher/by_reason 仍只统计 block（看板"拦截数"
    语义不变）；新增 by_level 全级别计数；日志条目带 level。
"""
import re
import threading
from collections import deque
from dataclasses import dataclass

from . import store

# 通用 4-gram（含这些不算"引用"证据，跳过）
_SKIP_GRAMS = {"这个知识", "这样的内容", "也就是我们", "所以说这个", "相关的知识", "这个问题"}


# ---------- 策略配置（可调参数与取值范围） ----------
@dataclass
class GuardPolicy:
    """守卫策略。所有字段均可经 teachers.guard_config JSON 按老师覆盖。"""
    ref_strong_hits: int = 3          # [1,10] 公共4-gram ≥ 此值 → 强引用直过
    fact_max_miss: int = 0            # [0,5] 普通数字允许未命中数（超出→confirm）
    fact_block_miss: int = 3          # [1,10] 普通数字未命中 ≥ 此值 → block
    scope_sim_threshold: float = 0.28  # [0.10,0.50] 断言句与KB最高相似低于此→记超纲句
    scope_pass_ratio: float = 0.30    # [0,1] 超纲句占比 ≤ 此 → 通过
    scope_confirm_ratio: float = 0.50  # [0,1] 占比 > 此 → confirm
    scope_block_ratio: float = 0.70   # [0,1] 占比 > 此 → block
    scope_min_sentences: int = 3      # [1,10] 断言句少于该值：比例封顶 confirm


# admin 校验用：字段名 → (类型, 下限, 上限)
POLICY_FIELDS: dict[str, tuple] = {
    "ref_strong_hits": (int, 1, 10),
    "fact_max_miss": (int, 0, 5),
    "fact_block_miss": (int, 1, 10),
    "scope_sim_threshold": (float, 0.10, 0.50),
    "scope_pass_ratio": (float, 0.0, 1.0),
    "scope_confirm_ratio": (float, 0.0, 1.0),
    "scope_block_ratio": (float, 0.0, 1.0),
    "scope_min_sentences": (int, 1, 10),
}

# 学科预设：按 teacher_subject 关键词命中（先匹配先用）
_SUBJECT_PRESETS: dict[str, dict] = {
    # 数字敏感学科：一个数据都不能错，但解题推导句式发散 → 超纲档放宽
    "数量关系": {"fact_max_miss": 0, "scope_pass_ratio": 0.35,
                "scope_confirm_ratio": 0.55, "scope_block_ratio": 0.75},
    "资料分析": {"fact_max_miss": 0, "scope_pass_ratio": 0.35,
                "scope_confirm_ratio": 0.55, "scope_block_ratio": 0.75},
    # 知识外延大学科：讲义难全覆盖 → 超纲容忍度高
    "常识": {"scope_pass_ratio": 0.40, "scope_confirm_ratio": 0.60, "scope_block_ratio": 0.80},
    "时政": {"scope_pass_ratio": 0.40, "scope_confirm_ratio": 0.60, "scope_block_ratio": 0.80},
    "申论": {"scope_pass_ratio": 0.40, "scope_confirm_ratio": 0.60, "scope_block_ratio": 0.80},
}

# 场景覆盖：按问题类型（detect_scene）叠加
_SCENE_OVERRIDES: dict[str, dict] = {
    # 问候/闲聊：非知识问答（关键数字编造仍拦；超纲反查结构性跳过）
    "chat": {"ref_strong_hits": 1, "fact_max_miss": 3},
    # 题目求解：解题过程重推导，句式发散 → 超纲档放宽一档
    "problem": {"scope_pass_ratio": 0.35, "scope_confirm_ratio": 0.55, "scope_block_ratio": 0.75},
    # 概念问答：定义应紧贴讲义 → 超纲档收紧一档
    "concept": {"scope_pass_ratio": 0.25, "scope_confirm_ratio": 0.45, "scope_block_ratio": 0.65},
}


_SCENE_CHAT_RE = re.compile(
    r"^(你好|您好|哈喽|嗨|在吗|谢谢|感谢|辛苦|好的|明白|懂了|收到|加油|再见|拜拜|晚安|早安|哈哈|嘿嘿|hi|hello)",
    re.IGNORECASE,
)
_SCENE_PROBLEM_RE = re.compile(
    r"怎么做|怎么解|怎么算|怎么求|解析|解答|求解|这(道|个)题|此题|选(哪个|项|什么)"
    r"|正确答案|答案是什么|为什么选|[ABCD][、.．)]"
)
_SCENE_CONCEPT_RE = re.compile(
    r"什么是|是啥|定义|含义|意思|区别|辨析|异同|是指|为什么|如何理解|怎么理解|讲(一)?下|介绍下"
)


def detect_scene(query: str) -> str:
    """问题类型 → 教学场景：chat(问候闲聊)/problem(题目求解)/concept(概念问答)/general。"""
    q = (query or "").strip()
    if not q:
        return "general"
    if len(q) <= 12 and _SCENE_CHAT_RE.match(q):
        return "chat"
    if _SCENE_PROBLEM_RE.search(q):
        return "problem"
    if _SCENE_CONCEPT_RE.search(q):
        return "concept"
    return "general"


def resolve_policy(tcfg, query: str = "") -> tuple[GuardPolicy, str]:
    """策略三级叠加：学科预设 → 场景覆盖 → 老师级覆盖（guard_config 最高优先）。"""
    scene = detect_scene(query)
    subject = (getattr(tcfg, "teacher_subject", "") or "")
    merged: dict = {}
    for key, preset in _SUBJECT_PRESETS.items():
        if key in subject:
            merged.update(preset)
            break
    merged.update(_SCENE_OVERRIDES.get(scene, {}))
    manual = getattr(tcfg, "guard_policy", None)
    if isinstance(manual, dict):
        for k, v in manual.items():
            if k in POLICY_FIELDS:
                merged[k] = v
    policy = GuardPolicy()
    for k, v in merged.items():
        if hasattr(policy, k):
            setattr(policy, k, v)
    return policy, scene


def _grams(text: str, n: int = 4) -> set[str]:
    """中文 n-gram 集合（过滤含标点/空白/纯数字的）。"""
    out = set()
    for i in range(len(text) - n + 1):
        g = text[i:i + n]
        if re.search(r"[\s\dA-Za-z，。！？、；：""''（）《》]", g):
            continue
        if g in _SKIP_GRAMS:
            continue
        out.add(g)
    return out


# ---------- 中文数字识别（防"百分之九十九点七"绕过事实校验） ----------
_CN_DIGITS = {"零": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4",
              "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN_NUM_RE = re.compile(r"(?:百分之)?[零一二两三四五六七八九十百千万]+(?:点[零一二两三四五六七八九]+)?")


def _cn_nums(text: str) -> list[str]:
    """提取中文数字 token。只认长度>=2 或含单位(十/百/千)的（防把'一个''一般'当数字）。"""
    out = []
    for m in _CN_NUM_RE.findall(text):
        if len(m) >= 2 or any(u in m for u in _CN_UNITS):
            out.append(m)
    return out


def _cn_section(s: str) -> int | None:
    """万以内分段数字 → int（数字字 + 十/百/千）。含非数字字（量词等）返回 None。"""
    if not s:
        return 0
    if not all(c in _CN_DIGITS or c in ("十", "百", "千") for c in s):
        return None
    section = 0
    digit = 0
    for ch in s:
        if ch in _CN_DIGITS:
            digit = int(_CN_DIGITS[ch])
        else:
            section += (digit if digit else 1) * _CN_UNITS[ch]
            digit = 0
    return section + digit


def _cn_integer(s: str) -> int | None:
    """万级中文整数 → int（xxx万yyy 分段）。
    - "万"前无数字字（量词/副词"千万""百万""十万""万万"→ 歧义）返回 None，不计为数字证据；
    - 含非数字字返回 None。"""
    if not s:
        return None
    if "万" in s:
        head, _, tail = s.partition("万")
        if "万" in tail:
            return None  # "万万"等歧义
        # head 必须含数字字（如"一千""两百万""十二万"），纯单位头（"千万/百万/十万"）是量词/副词歧义
        if not head or not any(c in _CN_DIGITS for c in head):
            return None
        if not all(c in _CN_DIGITS or c in ("十", "百", "千") for c in head + tail):
            return None
        h = _cn_section(head)
        if h is None:
            return None
        if tail:
            t = _cn_section(tail)
            if t is None:
                return None
            return h * 10000 + t
        return h * 10000
    return _cn_section(s)


def _cn_to_arabic(num: str) -> str:
    """中文数字 → 阿拉伯数字字符串（支持 十/百/千/万 组合与点后小数）。失败原样返回。"""
    s = num.replace("百分之", "")
    if not s:
        return num
    int_part, dot, frac_part = s.partition("点")
    if dot:
        if not frac_part or not all(c in _CN_DIGITS for c in frac_part):
            return num
    v = _cn_integer(int_part) if int_part else 0
    if v is None:
        return num
    out = str(v)
    if dot:
        out += "." + "".join(_CN_DIGITS[c] for c in frac_part)
    return out


# ---------- 关卡1：引用一致性（分档） ----------
def _ref_overlap(answer: str, hits: list[dict]) -> int:
    """答案与最佳片段的公共 4-gram 数。无片段 → -1（不校验，由上游拒答兜底）。"""
    if not hits:
        return -1
    ans_grams = _grams(answer)
    if not ans_grams:
        return 0
    return max(len(ans_grams & _grams(h.get("content", ""))) for h in hits)


# ---------- 关卡2：关键事实校验（数字三分类 + 豁免） ----------
_YEAR_RE = re.compile(r"(19|20)\d{2}")
# 统计词前缀：数字前 6 字窗口内含这些 → 视为关键数字（数据引用）
_KEY_BEFORE_WORDS = ("率", "高达", "达到", "上涨", "下跌", "增长", "下降")
# 量纲后缀：数字后紧跟这些字 → 关键数字
_KEY_AFTER_CHARS = "年万亿元倍"


def _clause_spans(answer: str) -> list[tuple[int, int]]:
    """按中英文标点切子句，返回 (start, end) 位置列表。"""
    spans: list[tuple[int, int]] = []
    last = 0
    for m in re.finditer(r"[，,；;。\n！!？?]", answer):
        spans.append((last, m.start()))
        last = m.end()
    spans.append((last, len(answer)))
    return spans


def _fact_evidence(answer: str, hits: list[dict], query: str = "") -> tuple[int, int]:
    """数字证据分级。返回 (关键数字未命中数, 普通数字未命中数)。
    空语料 → (0, 0)（无片段可校验，由上游拒答分支兜底）。"""
    corpus = "".join(h.get("content", "") for h in hits)
    if not corpus:
        return 0, 0

    # 算式豁免值：等式子句（含 = 且 ≥2 个数字）或 变量=值 形态中的数字，
    # 及其全文复用（"x=4……答案是4"），均为计算/推导结果，非语料引用
    exempt_values: set[str] = set()
    for cs, ce in _clause_spans(answer):
        clause = answer[cs:ce]
        if "=" not in clause and "＝" not in clause:
            continue
        nums_in_clause = re.findall(r"\d+(?:\.\d+)?%?", clause)
        if len(nums_in_clause) >= 2:
            exempt_values.update(n.rstrip("%") for n in nums_in_clause)
        else:
            m = re.search(r"[A-Za-z]\s*[=＝]\s*(\d+(?:\.\d+)?)", clause)
            if m:
                exempt_values.add(m.group(1))

    # 问题原文数字回声：复述用户输入的数字不算编造
    q_nums: set[str] = set(re.findall(r"\d+(?:\.\d+)?%?", query or ""))
    for n in _cn_nums(query or ""):
        a = _cn_to_arabic(n)
        if re.fullmatch(r"\d+(?:\.\d+)?", a):
            q_nums.add(a)

    key_miss = 0
    norm_miss = 0
    seen: set[str] = set()

    def _hit(val: str, orig: str) -> bool:
        # 词边界匹配（#20 系统性测试修复）：数字前后不得紧跟其他数字/小数点字符，
        # 防"3"子串假命中"35分钟""40道题"（v1 遗留缺陷：单字符数字几乎总能子串命中）
        bare = val.rstrip("%")
        if bare and re.search(r"(?<![\d.])" + re.escape(bare) + r"(?![\d.])", corpus):
            return True
        # 中文形态原词命中（orig 非空时；空串 in corpus 恒 True 必须显式排除）
        if orig and orig in corpus:
            return True
        # 中文万级换算形：答案"十二万"(120000) ↔ 语料"12万"
        if bare.endswith("0000") and re.search(
                r"(?<![\d.])" + re.escape(bare[:-4]) + r"万(?![\d.])", corpus):
            return True
        return False

    def _judge(tok: str, start: int, end: int, orig: str = "") -> None:
        nonlocal key_miss, norm_miss
        if tok in seen:
            return
        seen.add(tok)
        bare = tok.rstrip("%")
        if bare in exempt_values or tok in exempt_values or bare in q_nums or tok in q_nums:
            return  # 计算结果 / 结果值复用 / 问题回声
        if _hit(tok, orig):
            return
        prev = answer[start - 1] if start > 0 else ""
        nxt = answer[end:end + 1]
        line_head = answer.rfind("\n", 0, start) + 1
        # 列表编号豁免：行首/空白/冒号/逗号句号后 + 编号符（1. 2、 3） / 括号编号（2）
        at_marker_pos = (not answer[line_head:start].strip()
                         or (start > 0 and answer[start - 1] in " \t：:，,。；;"))
        if at_marker_pos and nxt and nxt[0] in ".、）)．:：":
            return
        if prev in "（(" and nxt and nxt[0] in "）)":
            return
        # 序数豁免：第N
        if prev == "第":
            return
        # 关键数字：百分比 / 年份 / 量纲 / 统计词前缀 / 中文百分之·万级
        is_key = (
            tok.endswith("%")
            or _YEAR_RE.fullmatch(bare)
            or (nxt and nxt[0] in _KEY_AFTER_CHARS)
            or orig.startswith("百分之")
            or ("万" in orig or "亿" in orig)
            or any(w in answer[max(0, start - 6):start] for w in _KEY_BEFORE_WORDS)
        )
        if is_key:
            key_miss += 1
        else:
            norm_miss += 1

    for m in re.finditer(r"\d+(?:\.\d+)?%?", answer):
        _judge(m.group(), m.start(), m.end())
    for m in _CN_NUM_RE.finditer(answer):
        orig = m.group()
        # 与 _cn_nums 同口径：只认长度≥2 或含单位(十/百/千)的（防"一共""放一放"的"一"误判）
        if not (len(orig) >= 2 or any(u in orig for u in _CN_UNITS)):
            continue
        arab = _cn_to_arabic(orig)
        # 量词歧义（千万/百万…）转换失败 → 不作为数字证据
        if not re.fullmatch(r"\d+(?:\.\d+)?", arab):
            continue
        if arab in seen:
            continue
        # "百分之三十" 转 "30" 后按 30 校验（与 v1 行为一致）
        _judge(arab, m.start(), m.end(), orig=orig)
    return key_miss, norm_miss


# ---------- 关卡3：超纲反查（断言句占比分档） ----------
# 引导/互动句前缀（≤40 字的此类句子不参与反查）
_SCOPE_EXEMPT_PREFIXES = (
    "咱们", "我们", "你注意", "注意", "接下来", "那么", "所以说", "简单来说",
    "换句话说", "比如", "例如", "举个例子", "记住", "总结", "划重点", "敲黑板",
    "再强调", "加油", "别急", "慢慢来", "很好", "听懂", "好了", "这题", "这道题",
    "回到", "另外", "此外", "对了", "还有",
)


def _is_assertion(s: str) -> bool:
    """知识断言句判定：非设问、长度≥10、非引导互动句。"""
    if not s:
        return False
    if s.endswith("？") or s.endswith("?"):
        return False  # 设问句：课堂互动，非知识断言
    if len(s) < 10:
        return False  # 短引导/过渡语
    if len(s) <= 40 and s.startswith(_SCOPE_EXEMPT_PREFIXES):
        return False  # 引导/互动句（口语化教学表达）
    return True


def _assertion_sentences(answer: str) -> list[str]:
    """切分并筛选知识断言句（保留句尾标点以识别设问句）。"""
    parts = re.split(r"([。！？!?；;])", answer)
    out: list[str] = []
    buf = ""
    for p in parts:
        buf += p
        if p and p in "。！？!?；;":
            s = buf.strip()
            buf = ""
            if _is_assertion(s):
                out.append(s)
    tail = buf.strip()
    if _is_assertion(tail):
        out.append(tail)
    return out


def _scope_ratio(answer: str, tcfg, teacher_id: str, policy: GuardPolicy):
    """返回 (超纲句占比, 参与反查句数, 超纲句数)。不可判场景返回 (None, 0, 0)。
    selftest（哈希向量无语义）/空库/向量化失败 → 跳过（不误拦）。"""
    if store._GLOBAL.embed_provider == "selftest":
        return None, 0, 0
    coll = store._collection(teacher_id)
    if coll.count() == 0:
        return None, 0, 0
    sentences = _assertion_sentences(answer)
    if not sentences:
        return None, 0, 0
    try:
        qvecs = store.embed_texts(sentences)
        res = coll.query(query_embeddings=qvecs, n_results=1)
    except Exception:
        return None, 0, 0
    if not res or not res.get("distances"):
        return None, 0, 0
    oos = 0
    checked = 0
    for dists in res["distances"]:
        if not dists:
            continue
        sim = max(0.0, 1.0 - float(dists[0]))
        checked += 1
        if sim < policy.scope_sim_threshold:
            oos += 1
    if checked == 0:
        return None, 0, 0
    return oos / checked, checked, oos


# ---------- 综合判定 ----------
def run_guard(answer: str, hits: list[dict], tcfg, teacher_id: str,
              query: str = "") -> tuple[bool, dict]:
    """三道关卡全跑，综合分级。返回 (passed, detail)。
    passed=False 仅当 level=block；detail.level ∈ {pass, warn, confirm, block}。"""
    policy, scene = resolve_policy(tcfg, query)
    ev_block: list[str] = []
    ev_confirm: list[str] = []
    ev_warn: list[str] = []
    scores: dict = {}

    # ---- 关卡2 事实（数字） ----
    key_miss, norm_miss = _fact_evidence(answer, hits, query=query)
    scores["fact_key_miss"] = key_miss
    scores["fact_norm_miss"] = norm_miss
    if key_miss > 0:
        ev_block.append("fact")
    elif norm_miss >= policy.fact_block_miss:
        ev_block.append("fact")
    elif norm_miss > policy.fact_max_miss:
        ev_confirm.append("fact")

    # ---- 关卡1 引用 ----
    overlap = _ref_overlap(answer, hits)
    scores["ref_overlap"] = overlap
    if overlap == 0:
        ev_confirm.append("reference")
    elif 0 < overlap < policy.ref_strong_hits:
        ev_warn.append("reference")

    # ---- 关卡3 超纲（chat 场景结构性跳过） ----
    if scene == "chat":
        scores["scope_checked"] = 0
    else:
        ratio, checked, oos = _scope_ratio(answer, tcfg, teacher_id, policy)
        scores["scope_checked"] = checked
        scores["scope_oos"] = oos
        if ratio is not None:
            scores["scope_ratio"] = round(ratio, 3)
            # 小样本保护：断言句不足时比例不稳，全超纲才允许 block 档
            small = checked < policy.scope_min_sentences and oos < checked
            if ratio > policy.scope_block_ratio and not small:
                ev_block.append("out_of_scope")
            elif ratio > policy.scope_confirm_ratio:
                ev_confirm.append("out_of_scope")
            elif ratio > policy.scope_pass_ratio:
                ev_warn.append("out_of_scope")

    # ---- 交叉升级：无引用证据 + 出现超纲 → 编造实锤（升 block） ----
    if "reference" in ev_confirm and "out_of_scope" in (ev_confirm + ev_warn):
        ev_block.append("reference")
        ev_confirm.remove("reference")

    if ev_block:
        level = "block"
    elif ev_confirm:
        level = "confirm"
    elif ev_warn:
        level = "warn"
    else:
        level = "pass"

    return (level != "block"), {
        "level": level,
        "fails": list(dict.fromkeys(ev_block)),
        "soft_fails": list(dict.fromkeys(ev_confirm + ev_warn)),
        "scene": scene,
        "scores": scores,
        "checked": ["reference", "fact", "out_of_scope"],
    }


class GuardCounter:
    """分级拦截计数 + 最近日志（线程安全，供监控看板）。
    兼容约定：total/by_teacher/by_reason 只统计 block（真拦截）；
    by_level 统计全部级别（warn/confirm 供调优分析）。"""

    def __init__(self, max_log: int = 200):
        self._lock = threading.Lock()
        self._by_teacher: dict[str, int] = {}
        self._by_reason: dict[str, int] = {}
        self._by_level: dict[str, int] = {}
        self._logs: deque = deque(maxlen=max_log)

    def record(self, teacher_id: str, query: str, reason: str, level: str = "block") -> None:
        with self._lock:
            if level == "block":
                self._by_teacher[teacher_id] = self._by_teacher.get(teacher_id, 0) + 1
                self._by_reason[reason] = self._by_reason.get(reason, 0) + 1
            self._by_level[level] = self._by_level.get(level, 0) + 1
            self._logs.append({
                "teacher_id": teacher_id,
                "query": query[:100],
                "reason": reason,
                "level": level,
            })

    def stats(self) -> dict:
        with self._lock:
            return {
                "total": sum(self._by_teacher.values()),
                "by_teacher": dict(self._by_teacher),
                "by_reason": dict(self._by_reason),
                "by_level": dict(self._by_level),
            }

    def recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return list(self._logs)[-limit:]


# 全局实例（API 层引用）
guard_counter = GuardCounter()
