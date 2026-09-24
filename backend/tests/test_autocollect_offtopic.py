# -*- coding: utf-8 -*-
"""需求4修复：自动采集不再收录「与公考相关但非公考题目」的伪题。

三层防线单测：
  层① LLM 提取 prompt 含「伪题拒绝门控」（admin.py sys_prompt 中文字核验）
  层② 入库前 `_gongkao_real_question_shape` 正象识别：
       - 合法公考成题放行（保留历史防御、不误杀）
       - 法条抄写型 judge 拒绝
       - 机翻/夹英文 拒绝
       - 资讯/政务/会议语体 拒绝
       - 缺选项选择题 / 题干过短无设问 拒绝
  层③ 页面/抽取门控：
       - `_SEARCH_BLOCK_HOSTS` 含低价值资讯/政务/百科源
       - `_QUESTION_STRONG_SIGNALS` 不再把「特此公告」当题目信号
       - `_looks_like_prose` 拦截长篇资讯/公告，但不误杀申论套卷

运行: cd backend && python tests/test_autocollect_offtopic.py
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-ac-ot-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["STUDY_DB"] = os.path.join(TMP, "study.db")
os.environ["SEARCH_PROVIDER"] = "none"

from poc import autocollect as ac  # noqa: E402
from poc import admin as admin_biz  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(extra)[:300]) if extra and not cond else ""))
    if not cond:
        ok = False


def shape(q):
    """返回 (通过?, 拒绝原因)"""
    return ac._gongkao_real_question_shape(q)


# ==================== 层② 入库前正象识别 ====================

# ---- 合法公考成题：必须放行（不误杀）----
check("合法 choice 放行",
      shape({"qtype": "choice", "question": "下列关于行政诉讼的说法，正确的是（ ）。",
             "answer": "A",
             "options": ["A. 原告对行政行为不服提起的诉讼", "B. 行政机关可以反诉",
                         "C. 行政诉讼不适用调解", "D. 一审一律公开审理"],
             "analysis": "考查行政诉讼制度。"})[0])
check("申论作答要求 essay 放行",
      shape({"qtype": "essay",
             "question": "【作答要求】请结合给定资料，概括当前基层治理存在的主要问题并提出对策。（30分）",
             "answer": "一、存在问题：……（略）", "options": [],
             "analysis": ""})[0])
check("常识判断 choice 放行",
      shape({"qtype": "choice",
             "question": "我国第一部诗歌总集是（ ）。",
             "answer": "A",
             "options": ["A. 《诗经》", "B. 《楚辞》", "C. 《乐府诗集》", "D. 《文选》"],
             "analysis": "《诗经》是我国第一部诗歌总集。"})[0])
check("完整法条判断题(真真题)放行",
      shape({"qtype": "judge",
             "question": "根据《治安管理处罚法》的规定，违反治安管理行为在六个月内没有被公安机关发现的，不再处罚。",
             "answer": "对", "options": [], "analysis": "考查治安处罚时效。"})[0])

# ---- 存量扫描回归（2026-09-13）：以下均为库中真实真题，曾被门控误杀，必须放行 ----
# 根因一：英文按「出现次数」计数，单个专有名词重复 3 次即被误判为「夹大量英文」。
check("真题-专有名词重复(APOE4×3) 放行",
      shape({"qtype": "choice", "category": "言语理解",
             "question": "约有20%的人出生时至少携带一个名为APOE4的基因变异拷贝。该变异使人年老时更易患心脏病，"
                        "但这种变异却没有被淘汰。研究发现，APOE4基因可能具有抵消其他负面影响的____好处，"
                        "如携带APOE4的女性会生育更多的孩子。这种优势可能使该致病基因在人类进化过程中得以____。"
                        "依次填入画横线部分最恰当的一项是：",
             "answer": "A", "options": ["A、潜在 延续", "B、特殊 发展", "C、显性 保存", "D、隐性 入选"],
             "analysis": "APOE4基因的好处是研究之前未知的，用'潜在'恰当。"})[0])
check("真题-专有名词重复(SEA×3) 放行",
      shape({"qtype": "choice", "category": "判断推理",
             "question": "研究人员发现，与摄入盐水的对照组小鼠相比，摄入葡萄球菌肠毒素SEA的小鼠会出现明显干呕现象。"
                        "研究显示，小鼠肠道内的SEA毒素激活了肠内壁的肠嗜铬细胞。C、SEA毒素与肠道的感觉神经元受体"
                        "结合会引发动物的干呕。由此可以推出：",
             "answer": "B", "options": ["A. 不摄入就不干呕", "B. 开发药物或能帮助化疗病人减少呕吐",
                                        "C. SEA毒素与受体结合引发干呕", "D. 切断信号传递或能引发干呕"],
             "analysis": "化疗病人呕吐机制与小鼠类似，B项可推出。"})[0])
check("真题-化学式专有名词(Zn/b-ZnO) 放行",
      shape({"qtype": "choice", "category": "言语理解",
             "question": "微波辅助催化技术是一项实现填埋场混杂废塑料高值利用的新技术，特制的Zn/b-ZnO催化剂和微波的"
                        "独特作用，是该技术为塑料垃圾带来重生的关键。b-ZnO能吸收微波能量并转化为局部热量；原位生成的"
                        "Zn团簇则负责切断塑料分子中顽固的C-C键。最适合做这段文字标题的是：",
             "answer": "D", "options": ["A. 原子簇-氧化物的协同效应", "B. 微波选择性加热技术",
                                        "C. 微波与催化剂的烹饪二重奏", "D. 微波助力塑料垃圾实现高值利用"],
             "analysis": "文段主要介绍微波辅助催化技术实现塑料垃圾高值利用。"})[0])

# 根因二：医学/驾驶等词被硬黑名单命中，但真题只是顺带提及（长材料题干）。
check("真题-长材料含'诊断' 放行",
      shape({"qtype": "choice", "category": "判断推理",
             "question": "一项关于离子通道Piezo2的介导性慢性疼痛超敏反应功能的研究表明，Piezo2是一个机械敏感通道，"
                        "会在压力的刺激下激活伤害性感受器，当Piezo2发生功能丧失型突变时，患者对轻微的触摸或振动不再"
                        "敏感；当它发生功能获得型突变时，患者通常被诊断为其它疾病。以下哪项如果为真，最能削弱上述结论？",
             "answer": "D", "options": ["A. 有的患者在无机械刺激时仍然活跃", "B. 有的患者强烈挤压时激活了感受器",
                                        "C. 有的患者被诊断出复杂发育障碍", "D. 有的患者对机械刺激更敏感"],
             "analysis": "D项指出功能丧失型突变患者对机械刺激更敏感，直接削弱结论。"})[0])
check("真题-长材料含'临床'+'诊断' 放行",
      shape({"qtype": "choice", "category": "言语理解",
             "question": "晚安牛奶的最大卖点在于“高褪黑素含量”。有品牌标明“褪黑素含量：12500pg/盒”，1pg等于万亿分之一克。"
                        "而褪黑素的有效剂量在0.1~0.3毫克，因此晚安牛奶助眠更依赖于其安慰剂效应。如果某人有轻微的失眠困扰，"
                        "还达不到临床上失眠的诊断标准，又能够排除器质性或精神疾病因素导致的失眠，不妨食用。这段文字意在强调：",
             "answer": "A", "options": ["A. 助眠效果主要源自心理作用", "B. 能助眠是商家的虚假宣传",
                                        "C. 多喝才能起到助眠作用", "D. 有较好的助眠效果"],
             "analysis": "文段指出褪黑素含量极低，助眠更依赖安慰剂效应。"})[0])
check("真题-常识含'驾驶证' 放行",
      shape({"qtype": "choice", "category": "常识判断",
             "question": "在我国，驾驶证、社会保障卡、居民身份证和护照等属于依法可以证明身份的证件。"
                        "下列与之相关说法正确的是（ ）。",
             "answer": "B", "options": ["A. B1驾驶证的准驾车型不包括家用小型汽车",
                                        "B. 社会保障卡具备办理工伤和失业事务的功能",
                                        "C. 居民身份证号码的最后一位数字用来表示性别",
                                        "D. 中华人民共和国普通护照的签发机关为外交部"],
             "analysis": "社会保障卡可用于办理工伤、失业等社会保险事务。"})[0])
# 但「短标题」形态的跨专业域词仍必须拦截（保留原防御强度）
check("短标题含'临床' 仍拒绝",
      shape({"qtype": "choice", "question": "临床诊断学基础", "answer": "A",
             "options": ["A. x", "B. y", "C. z", "D. w"], "analysis": ""})[0] is False)

# 根因三：类比推理题干是「A：B」词对，天然无设问指令，曾被「过短无设问」误杀。
for _stem in ("杏仁：腰果：坚果", "蛋白质：有机物", "鸡犬不宁：安居乐业",
              "随波逐流：盲从", "树枝：秸秆：柴火"):
    check("真题-类比推理词对放行(%s)" % _stem,
          shape({"qtype": "choice", "category": "判断推理", "question": _stem,
                 "answer": "D", "options": ["A. a", "B. b", "C. c", "D. d"],
                 "analysis": "考查词项间逻辑关系。"})[0])
check("短题干但无冒号 仍拒绝",
      shape({"qtype": "choice", "question": "选择最恰当的一组", "answer": "A",
             "options": ["A. x", "B. y", "C. z", "D. w"], "analysis": ""})[0] is False)

# ---- 历史专业域黑名单仍生效 ----
check("专业域黑名单拒绝(四六级)",
      shape({"qtype": "choice", "question": "下列四六级词汇中意思相同的一项是（ ）。",
             "answer": "A", "options": ["A. x", "B. y", "C. z", "D. w"]})[0] is False)
check("专业域黑名单拒绝(编程)",
      shape({"qtype": "choice", "question": "以下关于 python 列表的说法正确的是（ ）。",
             "answer": "A", "options": ["A. x", "B. y", "C. z", "D. w"]})[0] is False)

# ---- 伪题形态：必须拒绝 ----
check("法条抄写型 judge 拒绝",
      shape({"qtype": "judge", "question": "根据《民法典》第七百条，保证人承担保证责任后，"
             "除当事人另有约定外，有权在其承担保证责任的范围内向债务人追偿。",
             "answer": "对", "options": [],
             "analysis": "根据《民法典》第七百条…"})[0] is False)
check("机翻夹大量英文 拒绝",
      shape({"qtype": "choice",
             "question": "下面是一段关于China traditional culture的摘录：Confucianism和Taoism"
             "等思想文化遗存，它在thought、literature、art方面都有丰富的product。",
             "answer": "D", "options": ["A. x", "B. y", "C. z", "D. w"]})[0] is False)
check("资讯/政务语体 拒绝",
      shape({"qtype": "essay",
             "question": "会议指出，本次会议表示，会议强调要将改革进行到底。",
             "answer": "贯彻会议精神。", "options": [], "analysis": "会议指出…会议强调…"})[0] is False)
check("选择题缺选项 拒绝",
      shape({"qtype": "choice", "question": "下列关于民法调整范围的说法正确的是（ ）",
             "answer": "B", "options": [], "analysis": ""})[0] is False)
check("选择题题干过短且无设问 拒绝",
      shape({"qtype": "choice", "question": "请选择搭配最恰当的一组", "answer": "A",
             "options": ["A. x", "B. y", "C. z", "D. w"], "analysis": ""})[0] is False)
check("判断题缺答案 拒绝",
      shape({"qtype": "judge", "question": "这是一段完整的法理陈述对不对？", "answer": "",
             "options": [], "analysis": ""})[0] is False)
check("题干为空 拒绝",
      shape({"qtype": "choice", "question": "", "answer": "A",
             "options": ["A. x", "B. y", "C. z", "D. w"], "analysis": ""})[0] is False)

# ==================== 层① LLM prompt 含伪题拒绝门控 ====================
# 变更说明（《运行时AI技能与MCP能力开发说明书》§九 P0-3）：提取 prompt 已从
# admin.extract_questions_ai 内联字符串迁移至 poc/skills/prompts/ 模板（文本
# 逐字节等价，_baseline-q-extract.md）。本层断言不变（仍核验四大门控关键词），
# 仅核验目标从「函数源码」改为「技能层渲染出的 system prompt」——这才是真正
# 下发给模型的文本，防护语义等价。
from poc import skills as _skills  # noqa: E402
from poc.skills import q_extract as _qe  # noqa: E402
_src, _embedded = _skills.base.render_prompt(
    _skills.base.get_skill("q-extract"),
    _qe.render_inputs(material="x", teacher_subject="行测",
                      allowed_types="choice,judge,essay", source_url=""))
check("prompt 含伪题拒绝门控", "伪题拒绝门控" in _src)
check("prompt 禁止把材料改写成题目", "禁止把材料改写" in _src)
check("prompt 明确'原本就存在的题目'", "原本就存在" in _src)
check("prompt 覆盖百科/新闻/政府文件", ("百科" in _src and "新闻" in _src and "政府文件" in _src))

# ==================== 层③ 页面/抽取门控 ====================
check("SEARCH_BLOCK 含 court.gov.cn",
      "court.gov.cn" in ac._SEARCH_BLOCK_HOSTS)
check("SEARCH_BLOCK 含 cyol.com / jcy.gov.cn",
      "cyol.com" in ac._SEARCH_BLOCK_HOSTS and "jcy.gov.cn" in ac._SEARCH_BLOCK_HOSTS)
check("信号不再把'特此公告'当题目信号",
      "特此公告" not in ac._QUESTION_STRONG_SIGNALS.pattern)

# 长篇资讯/公告语体：拦截
news_long = ("会议指出，本次会议表示，会议强调……" + "特此公告。现将有关事项通知如下。" * 3)
check("长文资讯语体 拦截",
      ac._looks_like_prose(news_long + (" " * 200)) is True)
# 但带题目骨架的长文（申论套卷）仍放行提取
shenlun = ("1、根据给定资料1，概括S市在乡村振兴中的主要做法。\n"
           "A. xxx B. xxx C. xxx D. xxx\n作答要求：不超过300字。" * 2)
check("申论套卷(带骨架) 不按散文拦截", ac._looks_like_prose(shenlun) is False)

# ==================== 连续失败计数（与 webhook 配置解耦） ====================
# 原实现把计数放在 `if not url: return` 之后 → 未配置 webhook 时计数恒为 0，
# 「未配置 webhook 则仅记录」实际是「什么都不记」。此处锁定修复后的语义。
_st = {}
ac._notify_result(_st, {"added": 0, "errors": ["站点超时"], "error_stats": {}})
ac._notify_result(_st, {"added": 0, "errors": ["模型 503"], "error_stats": {}})
check("无 webhook 时连续失败计数仍递增", _st.get("consecutive_failures") == 2, _st)
ac._notify_result(_st, {"added": 3, "errors": [], "error_stats": {}})
check("入库成功时连续失败计数归零", _st.get("consecutive_failures") == 0, _st)

print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
sys.exit(0 if ok else 1)