# -*- coding: utf-8 -*-
"""自动采集：SEO / 备考资讯 / 问答页拦截 + 错误分类 + 题目形态启发式校验（纯规则，无状态）。"""
import re

# ============================================================
# SEO / 备考资讯 / 问答页拦截（问题4）：autonomous 搜索常命中
# 「XX考试时间、报名安排必看、最新公布」这类资讯/问答外围页，LLM 会
# 从这类文章里『编造』出形态完整的伪题（启发式形态校验拦不住）。
# 依据正文密集出现的资讯话术 + 题目骨架稀少做页面级拦截，不误杀真实题卷。
# ============================================================
_SEO_INFO_WORDS = (
    "什么时候", "最新安排", "必看", "报名时间", "报名通道", "报名", "报考",
    "考试时间", "笔试时间", "面试时间", "时间安排", "预计", "公布", "公告",
    "通知", "提醒", "备考", "上岸", "领取资料", "备考指导", "提分", "攻略",
    "经验", "咨询", "关注公众号", "加微信", "二维码", "查看详情", "更多内容",
)


def _is_seo_info_page(text: str) -> bool:
    """页面是否为「备考资讯/问答/SEO 外围」内容（非题卷）。

    判据：正文≥200 字、命中≥4 个资讯话术、且题目骨架稀少（没有成片题号+ABCD）。
    真实行测/申论卷虽有"备考""公告"等词，但题目骨架密集，不会被误判。"""
    t = (text or "").strip()
    if len(t) < 200:
        return False
    hits = sum(1 for w in _SEO_INFO_WORDS if w in t)
    if hits < 4:
        return False
    # 题目骨架数：段落行首序号 / 选项字母 / 作答要求
    qn = len(re.findall(r"(?m)^\s*(?:第)?\d{1,3}\s*[、.．:：]\s*\S", t))
    opt = len(re.findall(r"(?:^|\s)[A-E]\s*[、.．:：]", t))
    if qn >= 3 or opt >= 4:
        return False   # 有真实题目骨架 → 不判为资讯页
    return True


# #12 失败分类：把 errors 纯文本归入可观测类别，供管理端统计与「仅重跑失败」决策。
_ERROR_CATEGORIES = ("限速", "超时", "解析", "网络", "无题", "其他")


def _classify_error(msg: str) -> str:
    """按错误文本特征归入失败类别（限速/超时/解析/网络/无题/其他）。"""
    m = msg.lower()
    if any(k in m for k in ("429", "rate limit", "too many", "限流", "繁忙", "throttl", "overload")):
        return "限速"
    if any(k in m for k in ("timeout", "timed out", "超时", "read timed", "connect timed")):
        return "超时"
    if any(k in m for k in ("解析", "parse", "extract", "提取失败", "json", "无题", "题目", "识别", "拆题")):
        return "解析"
    if any(k in m for k in ("connection", "connect", "refused", "network", "网络", "dns", "ssl", "certificate", "unreachable", "name resolution", "resolve host", "getaddrinfo")):
        return "网络"
    if any(k in m for k in ("无题", "未获得", "有效页面", "过短", "empty", "no question")):
        return "无题"
    return "其他"


# ============================================================
# 公考相关性过滤（需求4：采集到的题目很多不属于公考）
# 三层防御：
#   ① LLM 提取 prompt 已加"公考相关性门控 + 伪题拒绝门控"（admin.extract_questions_ai），
#     掐死 LLM 把百科/资讯/法条/政务材料改写编造成题的行为（最有杀伤力）；
#   ② 入库前保守启发式兜底「非题目形态」正象识别（本函数），剔除"公考相关但非真题"
#     的伪题（百科词条改写、法条抄写型 judge、新闻/公告语体、机翻夹英文、残缺设问）；
#   ③ 页面/抽取门控（_worth_extract/_QUESTION_STRONG_SIGNALS/_SEARCH_BLOCK_HOSTS）
#     剔除公告/资讯/政务/百科等非题目来源整页。
# 原则：宁可少收，不可误吞"公考相关但非题目"的伪题。
# ============================================================
_OFF_DOMAIN_HARD = (
    # 外语 / 高等教育 / 专业领域强信号（英文小写匹配）
    "四六级", "六级", "雅思", "托福", "gre ", "考研数学", "考研政治",
    "微积分", "高等数学", "线性代数", "概率密度", "泊松", "傅里叶", "拉普拉斯",
    "化学反应方程式", "元素周期表",
    "时间复杂度", "空间复杂度", "python", "c++", "java", "linux", "sql",
    "操作系统", "数据库", "正则表达式", "编译器",
    "科目一", "科目二", "台球", "乒乓球比赛",
)

# 医学/驾驶等「真题会顺带提及」的词：仅当题干是短标题（非材料型长题干）时才判为
# 跨专业域。存量扫描实测（2026-09-13）：「还达不到临床上失眠的诊断标准」「患者通常
# 被诊断为其它疾病」「驾驶证、社会保障卡…属于可证明身份的证件」均为真实行测真题，
# 若在长材料题干上直接命中黑名单会被整题误杀。
_OFF_DOMAIN_INCIDENTAL = ("病毒", "病原体", "临床", "诊断", "病历", "驾驶证")


def _gongkao_cjk_ratio(s: str) -> float:
    """非空白字符中汉字占比（识别外语/纯公式片段）。"""
    if not s:
        return 0.0
    total = sum(1 for c in s if not c.isspace())
    if total == 0:
        return 0.0
    cjk = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
    return cjk / total


# 题目残缺时必需的设问/指示语（判断一段文本"像不像一道成题"）
_REAL_Q_ASK = ("下列", "下列关于", "下列说法", "正确的是", "错误的是", "由此可以推出",
               "由此推出", "填入", "依次填入", "最能", "旨在", "强调", "可以推出",
               "意味着", "体现", "包含", "正确的是", "作答题", "请根据", "请概括",
               "请结合", "请提出", "请围绕", "写一篇", "谈谈", "分析", "归纳",
               "?" , "？", "（ ）", "(", "）", ")")

# 「新闻 / 政府文件 / 公告 / 会议」语体强信号：命中说明该片段是资讯/政务材料而非题目
#（公考常识题可能以"关于X的说法正确的是"为题干，因此只把"纯陈述式资讯语体"作负向信号）
_NEWS_POLICY_SPEECH = ("会议指出", "会议表示", "会议强调", "记者获悉", "据新华社",
                       "人民日报评论", "本期导语", "特此公告", "现将有关事项通知",
                       "现通知如下", "办公厅", "印发《", "正式发布", "政策解读")

# 英文单词占比过高 → 大概率机翻/外文材料（真题题干罕见连续多个英文单词）
_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")

# 报考指南 / 备考攻略 / SEO-FAQ 营销话术：公考真题题干绝不可能出现这些词。
# 信息页或资讯段落被 LLM 改写成伪题时几乎必然命中，用于题目级伪题兜底拦截。
# 强信号：命中 1 个即拒收（如"必看/备考攻略/报名入口"）。
# 弱信号：命中 ≥2 个才拒收（如"什么时候/有哪些/入口"等单看可能误伤的词）。
# 注意：思路是「拒绝报考咨询类伪题」，与题型无关，choice/judge/essay 一律适用。
_SEO_FAQ_STRONG = (
    "必看", "一文读懂", "备考攻略", "报考指南", "官方回应", "报名入口",
    "入口在哪", "查询入口", "怎么报名", "如何报名", "在哪报名", "怎么报考",
    "考试科目有哪些", "报考条件有哪些", "职位表什么时候", "报名时间是什么时候",
    "报考时间是什么时候", "考试时间是什么时候", "预计11月下旬", "预计12月",
    "考录专题", "职位表", "报名网站", "网上报名", "报名时间", "笔试时间",
    "几月几号", "什么时候考", "考生必看", "看看你符合条件吗", "正式启动网上报名",
    # 报考资讯/培训广告类伪题信号（公告/调剂/补录/成绩/机构营销等，短句标题被伪题化为 essay）
    "调剂公告", "补录公告", "成绩公布时间", "成绩查询时间", "查询成绩", "查分",
    "总分是多少", "一年几次", "考试内容是什么", "培训班", "笔试辅导", "辅导机构",
    "哪个好一点", "哪个好", "上岸鸭", "备考资料", "报名人数", "多少人报名",
    "公告发布时间", "是何时", "你符合条件吗", "考试内容", "一样吗",
)
_SEO_FAQ_WEAK = (
    "什么时候", "有哪些", "需要准备什么", "用什么书", "参考书", "复习资料",
    "面试需要注意", "注意事项", "多少分", "能考吗", "学历要求", "专业要求",
    "年龄限制", "在哪", "入口", "官网", "公告什么时候", "出公告", "考试时间",
)

# 申论/简答/材料题的设问指令词：essay 类伪题（如"笔试时间是什么时候"）不含这些指令，
# 而真实申论题几乎都含（概括/请根据/结合材料/【给定资料】/要求）。用于"essay + 单弱信号"
# 场景下区分"报考咨询伪题"与"真实材料题"，避免对申论误杀。
_ESSAY_ASK_CMDS = ("请", "根据", "结合", "概括", "谈谈", "分析", "归纳", "围绕",
                   "提出", "简述", "说明当下", "谈谈你", "【", "给定资料", "要求",
                   "作答要求", "申述", "就", "针对", "从", "根据上述", "联系实际")


def _gongkao_real_question_shape(q: dict) -> tuple[bool, str]:
    """「非题目形态」正象识别：判断单题是否具备一道"成题"的必要结构。

    返回 (是否放行保留, 拒绝原因)。保守启发式兜底，仅拦截强伪题信号，
    避免误杀真实常识/申论题。宁可少收，不误吞伪题。
    """
    question = str(q.get("question") or "").strip()
    answer = str(q.get("answer") or "").strip()
    analysis = str(q.get("analysis") or "").strip()
    qtype = str(q.get("qtype") or "").strip()
    opts = (q.get("options") or []) or []
    txt = " ".join([question, answer, analysis])

    # 0) 空题干 → 不成题，拒收
    if not question:
        return False, "题干为空"

    # 1) 汉字占比极低 → 外语/纯公式/代码片段
    if _gongkao_cjk_ratio(txt or question) < 0.2:
        return False, "非中文/公式片段"

    # 1.5) 短标题形态：单句、≤45 字、无换行、非「给定资料」材料题开头。
    #      部分门控（跨专业域词、SEO 话术）只对短标题生效，避免误杀材料型长题干。
    _is_short_title = len(question) <= 45 and "\n" not in question and "【" not in question[:3]

    # 2) 非公考专业域黑名单（保留了历史防御）
    low = txt.lower()
    for kw in _OFF_DOMAIN_HARD:
        if kw in low:
            return False, f"非公考专业域:{kw}"
    if _is_short_title:
        for kw in _OFF_DOMAIN_INCIDENTAL:
            if kw in low:
                return False, f"非公考专业域:{kw}"

    # 3) 机翻/外文信号：题干里出现 ≥3 个「不同」英文单词（且非公考常用术语如 A/B/C选项）。
    #    按「不同单词」计数：真题题干里的 APOE4/Piezo2/IPN 等专有名词会重复出现多次，
    #    按出现次数计数会把单个术语的重复误判成「大量英文」（存量扫描 10 例误杀根因）。
    latin = {w.lower() for w in _LATIN_WORD.findall(question)}
    if len(latin - {"a", "b", "c", "d"}) >= 3:
        return False, "题干夹大量英文(疑似机翻/外文材料)"

    # 4) 资讯/政务材料语体：题干 + 解析命中新闻公告惯用语 → 是材料不是题目
    news_hits = [k for k in _NEWS_POLICY_SPEECH if k in txt]
    if len(news_hits) >= 2:
        return False, "资讯/政务材料语体"

    # 5) 报考咨询/备考攻略 SEO 伪题：题干命中报考-FAQ 营销话术 → 信息页被伪题化，与题型无关。
    #    拦截规则（分级，避免对申论误杀）：
    #       a. 强信号命中 1 个即拒；
    #       b. 弱信号命中 ≥2 个即拒；
    #       c. essay(无选项)类且命中 1 个弱信号、但题干不含申论设问指令词 → 报考咨询伪题拒收
    #          （如"笔试时间是什么时候"；真实申论题含"请/根据/结合/概括【给定资料】"故放行）。
    #    5·仅"短标题形态"生效（单句、≤45字、无换行）——SEO FAQ 伪题均为简短标题；
    #       真实申论/材料题题干为长文本或含【给定资料】，命中通用词（培训班/辅导/上岸等）
    #       不应误判为伪题，故长文本整体跳过本条拦截。
    if _is_short_title:
        _strong_hits = [k for k in _SEO_FAQ_STRONG if k in question]
        if _strong_hits:
            return False, f"报考咨询/备考营销伪题:{_strong_hits[0]}"
        _weak_hits = [k for k in _SEO_FAQ_WEAK if k in question]
        if _weak_hits:
            _no_ask = not any(c in question for c in _ESSAY_ASK_CMDS)
            if len(_weak_hits) >= 2 or (not opts and qtype == "essay" and _no_ask):
                return False, f"报考咨询/备考营销伪题:{'/'.join(_weak_hits[:2]) or _weak_hits[0]}"

    # 5.5) 法条抄写型 judge：题干是"根据《XX法/法典》第N条…"且无选项、无设问——批量转写，非真题
    #    （书名兼容"法/法典"，条号支持汉字数字"第六百九十八"与阿拉伯数字"698"）
    if qtype == "judge" and not opts and re.search(
            r"《[^》]{1,14}法(?:典)?》\s*第\s*[0-9零一二三四五六七八九十百千]{1,10}\s*条", question):
        return False, "法条抄写型判断题"

    # 6) 选择题但无任何设问/指示语（非残缺、非申论）→ 缺失设问的伪题
    if qtype == "choice":
        if not opts:
            return False, "选择题缺选项"
        # 类比推理题干天然是「A：B(:C)」词对，本身不含设问指令，不得按「过短无设问」误杀
        if (len(question) < 12 and not any(k in question for k in _REAL_Q_ASK)
                and not re.search(r"[：:]", question)):
            return False, "选择题题干过短且无设问"

    # 7) 判断题：题干过短、无设问、又非完整法条/常识陈述 → 疑似残缺伪题
    if qtype == "judge":
        if not answer:
            return False, "判断题缺答案"
        if len(question) < 10 and not re.search(r"[（(]", question):
            return False, "判断题题干过短"

    return True, ""


def _is_gongkao_relevant(q: dict) -> bool:
    """入口：判定单题是否属于公考范畴且具备成题形态；True 保留、False 丢弃。"""
    ok, _ = _gongkao_real_question_shape(q)
    return ok


# P3 内容门控：调 LLM 之前先判断文本"像不像题目"，明显是导航/资讯简介/词典
# 之类没有题目结构的页面直接跳过，省 token、提高命中率。命中任一强信号即放行。
_QUESTION_STRONG_SIGNALS = re.compile(
    r"(?m)^\s*\d{1,3}\s*[.、．．:：]\s*\S"          # 行首序号（1. / 1、）
    r"|^\s*[（(]\d{1,3}[)）]\s*\S"                 # （1）编号
    r"|[A-D]\s*[.、．:]\s*\S"                      # A. B. C. D.
    r"|最多可行驶|你能得出结论|作答要求|结合材料|给定资料|"
    r"[．。]{5,}|行测|行政职业能力测验|公务员录用考试", re.M)


def _looks_like_questions(text: str) -> bool:
    """启发式：网页/搜索结果文本是否含题目结构。过短或空返回 False。"""
    t = (text or "").strip()
    if len(t) < 60:
        return False
    sig = _QUESTION_STRONG_SIGNALS.findall(t)
    return len(sig) >= 2


def _detail_candidate(text: str) -> bool:
    """兜底解析门槛（saB）：_looks_like_questions 未达标、但内容足够长且含题目骨架特征
    （选项/序号题干/例题标记）的长文本，仍放行进 LLM 抽取，降低无定制规则题站的误杀。
    仅宽容「≥1 个弱信号 + 长度」这一档；无任何题目特征的长资讯/正文仍被拦截，防烧预算。"""
    t = (text or "").strip()
    if len(t) < 200:
        return False
    sig = _QUESTION_STRONG_SIGNALS.findall(t)
    if sig:
        return True
    # 长文含"作答要求/【例/例题/材料"等申论/套卷典型词也放行
    import re as _re
    return bool(_re.search(r"作答要求|给定资料|【例|例题|结合材料|行测|申论", t))


_PROSE_NO_ASK = ("会议指出", "会议表示", "会议强调", "记者获悉", "据新华社",
                 "人民日报", "特此公告", "现将有关事项通知", "通知如下",
                 "办公厅", "印发《", "本期导语", "政策解读", "出炉", "据悉")


def _looks_like_prose(text: str) -> bool:
    """长文是否为「资讯/公告/政务/政策解读」语体（非题目材料）。

    仅当命中 ≥2 个独立资讯惯用语时判定为散文/通知，避免误杀申论给定资料
    （申论材料会重复出现"会议/印发《》/记者"，但通常伴随编号题干、A.、作答要求等
    题目结构，此时由 _looks_like_questions 兜住放行）。"""
    t = (text or "").strip()
    hits = [k for k in _PROSE_NO_ASK if k in t]
    if len(hits) < 2:
        return False
    # 若同时具备题目骨架（序号题干/选项/作答要求），仍算可提取，不按散文拦截
    if _looks_like_questions(t):
        return False
    return True


def _worth_extract(text: str, rule_detail: bool) -> bool:
    """提取前内容门控（P3）。
    rule_detail=True：该页已命中定制详情页规则（URL 形态即为真实题目页），放宽门控，
      长段真实内容（如申论/材料题的给定资料、整卷文本）直接放行，仅拦截过短的占位页 /
      无题目结构的短页 —— 避免把真正的申论套卷整页误判成噪音丢弃。
      同时把「无任何题目骨架的长篇资讯/公告/政务文」拦下（如《人民日报》专栏、政府文件），
      不让这类"公考相关但非题目"的纯材料页被整页喂进 LLM 编造成伪题。
    rule_detail=False：通用/搜索页，维持严格启发式，拦截导航/资讯/列表等杂页。"""
    t = (text or "").strip()
    if len(t) < 60:
        return False
    if rule_detail:
        if len(t) >= 1000:
            if _looks_like_prose(t):
                return False
            return True
        return _looks_like_questions(t)
    return _looks_like_questions(t)


