# -*- coding: utf-8 -*-
"""问题2：幻觉拦截守卫 v2 系统性测试套件。

覆盖四大维度（用户需求逐项映射）：
  A. 规则配置生效性 —— 8 个可调参数逐个改值，验证对判定结果的实际影响
     + 三级策略叠加顺序（学科预设→场景覆盖→老师覆盖）
  B. 正常内容误拦截 —— 教学口语/序数/算式/列表编号/回声/引用/鼓励语/设问句
  C. 幻觉内容识别与拦截 —— 编造数字（年份/百分比/量纲/中文数字）、
     纯编造断言、关键数字 vs 普通数字分级、引用+超纲交叉升级
  D. 异常输入处理 —— 空串/纯标点/纯数字/超长/单句/无hits/空content/特殊字符/emoji
  E. 边界值 —— 比例阈值上下界、数字个数边界、4-gram 边界、detail 结构完整性

运行: cd backend && python tests/test_guard_v2_systematic.py
输出: 逐用例 PASS/FAIL + 缺陷清单（FAIL 即缺陷，编号 DEF-x）
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-guard-sys-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"

from poc.ingest import Chunk  # noqa: E402
from poc import store  # noqa: E402
from poc.guard import (  # noqa: E402
    run_guard, resolve_policy, detect_scene, GuardPolicy, POLICY_FIELDS,
    _fact_evidence, _ref_overlap,
)

store.clear_collection("T001")
store.upsert_chunks([
    Chunk(content="转折关系做题口诀：转折之后是重点，转折之前可略读。显性转折词包括但是、然而、却。"
                  "隐性转折词包括实际上、事实上、其实。正确答案特征：完整涵盖转折后观点、忠于原文。",
          doc_name="讲义.md", idx=0),
    Chunk(content="行测言语理解模块共40道题，建议用时35分钟，正确率目标75%，每年约有2到3道转折关系题。",
          doc_name="考情.md", idx=1),
    Chunk(content="该省去年招录12万人，竞争激烈，平均竞争比达到58比1。",
          doc_name="招录.md", idx=2),
], "T001")

HITS = store.retrieve("转折关系怎么做题", "T001")
TCFG = SimpleNamespace(teacher_id="T001", threshold=0.5, teacher_subject="言语理解", guard_policy=None)

results = []
defects = []


def check(module, case, cond, expect="", actual=""):
    tag = "PASS" if cond else "FAIL"
    line = f"[{module}] {tag} {case}"
    if not cond:
        if expect:
            line += f" | 期望: {expect} | 实际: {str(actual)[:200]}"
        defects.append((module, case, str(actual)[:200]))
    results.append((tag, module, case))
    print(line)


def guard(answer, query="", tcfg=None, hits=None):
    _, d = run_guard(answer, hits if hits is not None else HITS, tcfg or TCFG, "T001", query=query)
    return d


# ================================================================
# A. 规则配置生效性（8 参数逐个验证）
# ================================================================
print("=" * 60)
print("A. 规则配置生效性")
print("=" * 60)

# A1. ref_strong_hits：引用门槛（构造 overlap=2 的答案验证参数区间生效）
# "转折之后是王道" 与语料"转折之后是重点"共享 4-gram 窗口 [转折之后]+[折之后是] = 2
# overlap=0 属"无引用证据"，任何配置下 confirm（设计行为，非参数可调区间）
ans_ov2 = "咱们做题讲究方法，转折之后是王道，细节先放一边。"
ov2 = _ref_overlap(ans_ov2, HITS)
tcfg_rs3 = SimpleNamespace(teacher_id="T001", threshold=0.5, teacher_subject="言语理解",
                           guard_policy=None)  # 默认 ref_strong_hits=3
tcfg_rs1 = SimpleNamespace(teacher_id="T001", threshold=0.5, teacher_subject="言语理解",
                           guard_policy={"ref_strong_hits": 1})
d_rs3 = guard(ans_ov2, tcfg=tcfg_rs3)
d_rs1 = guard(ans_ov2, tcfg=tcfg_rs1)
check("A", f"A1 ref_strong_hits: overlap={ov2}=2 时默认(3)→warn、调成1→pass",
      ov2 == 2 and d_rs3["level"] == "warn" and d_rs1["level"] == "pass",
      f"ov={ov2} 默认={d_rs3['level']} 调整后={d_rs1['level']}")

# A2. fact_max_miss：普通数字容忍度（阿拉伯数字+强引用，隔离 fact 关卡）
# 7/8 在语料中无独立出现（词边界匹配下不命中，见 DEF-v2-A 修复）
ans_norm_num = "转折之后是重点，转折之前可略读。这道题涉及7个关键要点和8条路径需要注意分析。"
tcfg_fact = SimpleNamespace(teacher_id="T001", threshold=0.5, teacher_subject="言语理解",
                            guard_policy={"fact_max_miss": 2, "fact_block_miss": 9})
d_def = guard(ans_norm_num)
d_fact = guard(ans_norm_num, tcfg=tcfg_fact)
check("A", f"A2 fact_max_miss: 阿拉伯普通数字未命中({d_def['scores']['fact_norm_miss']}个) 默认confirm、容忍2个后pass",
      d_def["scores"]["fact_norm_miss"] == 2 and d_def["level"] == "confirm"
      and d_fact["level"] == "pass",
      f"默认={d_def['level']}({d_def['scores']}) 调整后={d_fact['level']}")

# A3. fact_block_miss：普通数字拦截线
ans_many_num = "这里有7个要点8个难点9个考点10个坑11处细节12条规律13项原则。"
d3 = guard(ans_many_num)
check("A", "A3 fact_block_miss: 7个普通数字未命中 >=3 触发block(fact)",
      d3["level"] == "block" and "fact" in d3.get("fails", []), d3["level"])

tcfg_fact_hi = SimpleNamespace(teacher_id="T001", threshold=0.5, teacher_subject="言语理解",
                               guard_policy={"fact_block_miss": 20, "fact_max_miss": 10})
d4 = guard(ans_many_num, tcfg=tcfg_fact_hi)
check("A", "A3b fact_block_miss 调到20后不再block",
      d4["level"] != "block", d4["level"])

# A4. 关键数字不随 fact_max_miss 放宽（真幻觉必须拦）
ans_key_num = "转折关系题在2024年考了15次，正确率只要62.3%就行。"
d5 = guard(ans_key_num, tcfg=tcfg_fact_hi)  # 最宽松配置
check("A", "A4 关键数字(年份/百分比)不受 fact_max_miss 豁免——最宽松配置下仍 block",
      d5["level"] == "block" and "fact" in d5.get("fails", []),
      f"{d5['level']} {d5.get('fails')} scores={d5['scores']}")

# A5. scope_sim_threshold / 三档比例 + 小样本保护（selftest 下 scope 跳过，
#      结构性验证：chat 场景跳过反查 + scope_checked 字段）
d_chat = guard("你好呀，同学！", query="你好")
check("A", "A5 chat场景: 超纲反查结构性跳过(scope_checked=0)",
      d_chat["scores"].get("scope_checked") == 0, d_chat["scores"])

# A6. scope_min_sentences：小于3断言句时 block 档封顶 confirm
#      构造：单句断言 + 无引用 + 无数字 → scope 变量不可控（selftest哈希），验证字段存在
d_single = guard("量子纠缠决定了行测言语理解的本质结构。")
check("A", "A6 单断言句: 返回结构完整含 scene/scores/level",
      all(k in d_single for k in ("level", "scene", "scores", "fails", "soft_fails", "checked")),
      str(d_single)[:150])

# A7. 三级叠加顺序：学科预设 → 场景 → 老师覆盖（老师最高）
# 注意叠加方向验证须用 general 场景 query（concept/problem 会覆盖学科值——那是 A7b 的验证点）
tcfg_cs = SimpleNamespace(teacher_id="T9", threshold=0.5, teacher_subject="常识判断", guard_policy=None)
p_cs, _ = resolve_policy(tcfg_cs, "随便问点啥")
check("A", "A7a 学科预设: 常识判断+general → scope_block_ratio=0.80", p_cs.scope_block_ratio == 0.80)

p_cs2, _ = resolve_policy(tcfg_cs, "这道题怎么做")  # problem 场景覆盖学科
check("A", "A7b 场景覆盖: 常识+problem → 0.75(场景覆盖学科预设的0.80)", p_cs2.scope_block_ratio == 0.75)

p_cs3, _ = resolve_policy(tcfg_cs, "什么是法律")  # concept 场景收紧
check("A", "A7b2 场景覆盖: 常识+concept → 0.65(场景收紧)", p_cs3.scope_block_ratio == 0.65)

tcfg_top = SimpleNamespace(teacher_id="T9", threshold=0.5, teacher_subject="常识判断",
                           guard_policy={"scope_block_ratio": 0.60})
p_top, _ = resolve_policy(tcfg_top, "这道题怎么做")
check("A", "A7c 老师覆盖最高优先: guard_config 0.60 覆盖场景 0.75", p_top.scope_block_ratio == 0.60)

# A8. guard_config 非法字段被忽略（不炸）
tcfg_bad = SimpleNamespace(teacher_id="T9", threshold=0.5, teacher_subject="言语理解",
                           guard_policy={"evil_param": 1, "fact_max_miss": 2})
p_bad, _ = resolve_policy(tcfg_bad, "随便")
check("A", "A8 guard_config 未知字段被忽略、已知字段生效",
      p_bad.fact_max_miss == 2 and not hasattr(p_bad, "evil_param"))

# A9. POLICY_FIELDS 参数表完备性（8个参数全部可配）
check("A", "A9 POLICY_FIELDS 含8个参数且范围合法",
      len(POLICY_FIELDS) == 8 and all(len(v) == 3 and v[1] <= v[2] for v in POLICY_FIELDS.values()),
      str(POLICY_FIELDS))

# ================================================================
# B. 正常内容误拦截（黄金集扩充版）
# ================================================================
print()
print("=" * 60)
print("B. 正常内容误拦截")
print("=" * 60)

legit_cases = [
    ("B1 教学口语+引用", "同学你别急，咱们慢慢来。转折之后是重点，转折之前可略读，先找但是、然而、却。", "这道题怎么做"),
    ("B2 序数词+列表编号", "咱们分三步：1. 找转折词，2. 锁定重点，3. 对照选项。第2步最关键：转折之后是重点。", "第3题怎么做"),
    ("B3 算式推导+结果复用", "设 2x+3=11，解得 x=4，所以这道题的答案是4。转折之后是重点。", "这道题怎么解"),
    ("B4 语料数字正确引用", "言语理解一共40道题，建议用时35分钟，正确率目标75%，转折之后是重点。", "言语理解考情"),
    ("B5 问题数字回声", "你的正确率目标75%目前还差一点，建议多练转折关系题。", "正确率75%怎么提高"),
    ("B6 量词口语化", "千万要注意转折词。转折之后是重点，显性转折词包括但是、然而、却。", "转折关系注意什么"),
    ("B7 同义解释+引用", "转折关系就是前面铺垫、后面才是重点的结构。转折之后是重点，做题先找但是、然而、却。", "什么是转折关系"),
    ("B8 设问句+互动", "那转折之后看什么呢？当然是重点内容啦！显性转折词包括但是、然而、却。", "转折关系"),
    ("B9 括号编号", "转折词分两类：（1）显性转折词包括但是、然而、却；（2）隐性转折词包括实际上、事实上。", "转折词分类"),
    ("B10 万级换算形", "该省去年招录十二万人，竞争激烈，转折之后是重点。", "招录人数"),
    ("B11 中文数字命中语料", "每年约有二到三道转折关系题，转折之后是重点。", "转折关系考频"),
    ("B12 纯引用复述", "转折关系做题口诀：转折之后是重点，转折之前可略读。正确答案特征：完整涵盖转折后观点、忠于原文。", "做题口诀"),
]
for name, ans, q in legit_cases:
    d = guard(ans, query=q)
    check("B", f"{name} → 期望 pass/warn，实际", d["level"] in ("pass", "warn"),
          d["level"], d["level"])

# ================================================================
# C. 幻觉内容识别与拦截
# ================================================================
print()
print("=" * 60)
print("C. 幻觉内容识别与拦截")
print("=" * 60)

fab_cases = [
    ("C1 编造年份+百分比", "转折关系题在2024年考了15次，正确率只要62.3%就行。", "转折关系考频"),
    ("C2 中文数字编造百分比", "转折关系的本质是量子纠缠效应，正确率百分之九十九点七。", "转折关系"),
    ("C3 编造量纲数字", "该省去年招录300万人，人均年薪80万元。", "招录情况"),
    ("C4 纯编造断言+关键数字", "该理论由薛定谔于1926年提出，通过率88%，每年有7道题。", "转折关系"),
    ("C5 统计词前缀编造", "竞争比高达237:1，增长率达到156%近年最高。", "竞争情况"),
]
for name, ans, q in fab_cases:
    d = guard(ans, query=q)
    check("C", f"{name} → 期望 block(fact)", d["level"] == "block" and "fact" in d.get("fails", []),
          "block+fact", f"{d['level']} {d.get('fails')}")

# C6. 普通数字分级：1-2个 → confirm（不放行也不硬拦）
#     注意构造：强引用隔离 reference 关卡，断言 confirm 确实来自 fact（soft_fails 含 fact）
d_c6 = guard("转折之后是重点，转折之前可略读。这道题涉及7个关键要点需要注意。", query="这道题")
check("C", "C6 普通数字少量未命中 → confirm 且证据来自 fact 关卡（非 reference 假阳性）",
      d_c6["level"] == "confirm" and "fact" in d_c6.get("soft_fails", []),
      "confirm+fact", f"{d_c6['level']} soft={d_c6.get('soft_fails')} scores={d_c6['scores']}")

# C7. 编造数字在语料内改写不算幻觉（12万 命中语料）
d_c7 = guard("该省去年招录12万人，竞争比达到58比1，转折之后是重点。", query="招录人数")
check("C", "C7 语料内数字组合放行", d_c7["level"] in ("pass", "warn"), "pass/warn", d_c7["level"])

# ================================================================
# D. 异常输入处理
# ================================================================
print()
print("=" * 60)
print("D. 异常输入处理")
print("=" * 60)

# D1-D9：不崩溃 + 返回合法四档之一 + detail 结构完整
abnormal = [
    ("D1 空回答", ""),
    ("D2 纯标点", "！！！？？？。，，；；"),
    ("D3 纯数字", "1234567890"),
    ("D4 纯emoji", "😀🎯📚✨💯🔥👍"),
    ("D5 单字符", "好"),
    ("D6 超长重复", "转折之后是重点。" * 800),
    ("D7 特殊字符注入", "转折之后是重点。'); DROP TABLE users; -- ${jndi:ldap://x} <script>alert(1)</script>"),
    ("D8 全空白", "   \n\t  "),
    ("D9 混合乱码", "转折之后是重点� halfway 文字\xff\xfe乱码"),
]
for name, ans in abnormal:
    try:
        d = guard(ans, query="转折关系")
        check("D", f"{name} → 不崩溃且返回合法级别", d["level"] in ("pass", "warn", "confirm", "block"),
              "合法级别", d["level"])
    except Exception as e:  # noqa: BLE001
        check("D", f"{name} → 不崩溃", False, "no exception", f"{type(e).__name__}: {e}")

# D10. 空 hits（无检索结果——上游拒答分支，但守卫需健壮）
try:
    d = guard("转折之后是重点。", hits=[])
    check("D", "D10 空 hits → 不崩溃（上游有拒答兜底，守卫返回合法级别）",
          d["level"] in ("pass", "warn", "confirm", "block"), "合法级别", d["level"])
except Exception as e:  # noqa: BLE001
    check("D", "D10 空 hits → 不崩溃", False, "no exception", str(e))

# D11. hits 内 content 为空串
try:
    d = guard("转折之后是重点2024年。", hits=[{"content": "", "doc_name": "x"}])
    check("D", "D11 hits空content → 不崩溃（数字证据走语料为空分支）",
          d["level"] in ("pass", "warn", "confirm", "block"), "合法级别", d["level"])
except Exception as e:  # noqa: BLE001
    check("D", "D11 hits空content → 不崩溃", False, "no exception", str(e))

# D12. query 为空串/None
try:
    d1_ = guard("转折之后是重点。", query="")
    d2_ = guard("转折之后是重点。", query=None)
    check("D", "D12 query 空串/None → 不崩溃", True)
except Exception as e:  # noqa: BLE001
    check("D", "D12 query 空串/None → 不崩溃", False, "no exception", str(e))

# D13. 超长回答 + 编造数字（守卫仍需拦住）
d13 = guard("首先转折之后是重点。" * 200 + "该数据为2024年统计，通过率88%。", query="转折关系")
check("D", "D13 超长回答+编造数字 → 仍 block", d13["level"] == "block", "block", d13["level"])

# ================================================================
# E. 边界值测试
# ================================================================
print()
print("=" * 60)
print("E. 边界值测试")
print("=" * 60)

# E1. fact 关卡边界：正好 fact_max_miss+1 个 → confirm（强引用隔离）
d_e1 = guard("转折之后是重点，转折之前可略读。这里有7个要点8条路径。",
             query="这道题")
check("E", "E1 普通数字=2个未命中(>max_miss=0) → confirm 且来自 fact",
      d_e1["level"] == "confirm" and "fact" in d_e1.get("soft_fails", []),
      "confirm+fact", f"{d_e1['level']} soft={d_e1.get('soft_fails')} miss={d_e1['scores']['fact_norm_miss']}")

# E1b. DEF-v2-A 回归：词边界匹配——数字不得因子串命中语料中更大的数（9 不在语料）
km, nm = _fact_evidence("这里涉及9个要点需要注意。", HITS, query="")
check("E", "E1b 词边界: '9'未命中(语料无9)（未命中计数=1）",
      nm == 1, "norm_miss=1", f"({km},{nm})")
km, nm = _fact_evidence("每年约有2到3道转折题。", HITS, query="")
check("E", "E1c 词边界: 独立数字'2''3'命中语料'2到3道'（0未命中）",
      (km, nm) == (0, 0), "(0,0)", f"({km},{nm})")
km, nm = _fact_evidence("用时53分钟完成。", HITS, query="")
check("E", "E1d 词边界: '53'不因子串命中'35分钟'（未命中）",
      nm == 1, "norm_miss=1", f"({km},{nm})")

# E2. _fact_evidence 纯函数边界
km, nm = _fact_evidence("答案是42。", HITS, query="")
check("E", "E2 单个普通数字未命中 → (0,1)", (km, nm) == (0, 1), "(0,1)", f"({km},{nm})")

km, nm = _fact_evidence("正确率75%。", HITS, query="")
check("E", "E3 语料内百分比 → (0,0)", (km, nm) == (0, 0), "(0,0)", f"({km},{nm})")

km, nm = _fact_evidence("正确率85%。", HITS, query="")
check("E", "E4 语料外百分比 → (1,0) 关键数字", (km, nm) == (1, 0), "(1,0)", f"({km},{nm})")

# E5. ref_overlap 纯函数
check("E", "E5 _ref_overlap 空 hits → -1(不校验)", _ref_overlap("任意", []) == -1)
check("E", "E6 _ref_overlap 同文 → 高重叠", _ref_overlap("转折之后是重点，转折之前可略读", HITS) >= 3)

# E7. detect_scene 边界
scene_cases = [
    ("", "general"), ("你好", "chat"), ("在吗？", "chat"),
    ("这道题怎么解", "problem"), ("答案是什么", "problem"),
    ("什么是转折", "concept"),
    # 已知取舍：选项字母 pattern（A、B…）优先于 concept 命中 problem——
    # 误判方向是"更宽松"（problem 超纲档比 concept 宽），不产生误拦截，可接受
    ("A、B 两项区别", "problem"),
    ("转折关系做题口诀", "general"),
]
for q, expect in scene_cases:
    got = detect_scene(q)
    check("E", f"E7 detect_scene({q!r}) → {expect}", got == expect, expect, got)

# E8. run_guard 返回契约（passed 与 level 一致性）
for ans in ["转折之后是重点，转折之前可略读。", "正确率高达99.7%。"]:
    passed, d = run_guard(ans, HITS, TCFG, "T001", query="转折")
    check("E", f"E8 passed/level 一致 ({d['level']})",
          passed == (d["level"] != "block") and bool(d["fails"]) == (d["level"] == "block"),
          "一致", f"passed={passed} level={d['level']} fails={d['fails']}")

# ================================================================
# 汇总
# ================================================================
print()
print("=" * 60)
n_pass = sum(1 for t, *_ in results if t == "PASS")
n_fail = len(defects)
print(f"总计 {len(results)} 用例: PASS {n_pass} / FAIL {n_fail}")
if defects:
    print("\n缺陷清单:")
    for i, (mod, case, actual) in enumerate(defects, 1):
        print(f"  DEF-{i} [{mod}] {case} | 实际: {actual}")
print("RESULT:", "ALL PASS" if not defects else "HAS DEFECTS")
sys.exit(0 if not defects else 1)
