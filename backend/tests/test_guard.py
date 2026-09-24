# -*- coding: utf-8 -*-
"""幻觉检测三道关卡 · 单元测试（RED→GREEN）
运行: cd backend && python tests/test_guard.py
覆盖: 引用一致性 / 关键事实(数字)校验 / 超纲反查 / 拦截计数 / 日志
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-guard-test-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


# ---------- 构造数据：往 T001 库塞一段讲义（超纲反查需要真实 KB） ----------
from poc.ingest import Chunk  # noqa: E402
from poc import store  # noqa: E402

store.clear_collection("T001")
store.clear_collection("T999")  # 空库对照
store.upsert_chunks([Chunk(
    content="转折关系做题口诀：转折之后是重点，转折之前可略读。显性转折词包括但是、然而、却。"
            "隐性转折词包括实际上、事实上、其实。正确答案特征：完整涵盖转折后观点、忠于原文。",
    doc_name="讲义.md", idx=0)], "T001")

tcfg = SimpleNamespace(teacher_id="T001", threshold=0.5)

from poc.guard import run_guard, GuardCounter  # noqa: E402

# 1) 正常回答（引用讲义原句）→ 三道关卡全过
good_answer = "转折之后是重点，转折之前可略读。做言语理解题第一步先找转折词。"
hits = store.retrieve("转折关系怎么做题", "T001")
passed, detail = run_guard(good_answer, hits, tcfg, "T001")
check("正常回答: 关卡全过", passed is True, str(detail))

# 2) 编造回答（无任何引用依据 + 数字不在讲义）→ 应拦截
bad_answer = "转折关系的本质是量子纠缠效应，正确率高达99.7%，需要背诵30个公式。"
passed2, detail2 = run_guard(bad_answer, hits, tcfg, "T001")
check("编造回答: 被拦截", passed2 is False, str(detail2))
if not passed2:
    check("拦截原因含引用一致性或数字校验",
          any(k in detail2.get("fails", []) for k in ("reference", "fact")), str(detail2))

# 3) 中文数字编造（"百分之九十九点七" 是中文数字，须被 fact 关卡拦截）
cn_answer = "转折关系的本质是量子纠缠效应，正确率百分之九十九点七。"
passed_cn, detail_cn = run_guard(cn_answer, hits, tcfg, "T001")
check("中文数字编造: 被拦截", passed_cn is False, str(detail_cn))
if not passed_cn:
    check("中文数字编造: 拦截原因含 fact", "fact" in detail_cn.get("fails", []), str(detail_cn))

# 3b) 中文数字转换函数直接验证（防"百分之九十九点七"漏网）
from poc.guard import _cn_to_arabic, _cn_nums  # noqa: E402
check("中文转阿拉伯: 九十九点七→99.7", _cn_to_arabic("九十九点七") == "99.7",
      _cn_to_arabic("九十九点七"))
check("中文转阿拉伯: 百分之三十→30", _cn_to_arabic("百分之三十") == "30",
      _cn_to_arabic("百分之三十"))
check("中文转阿拉伯: 一百→100", _cn_to_arabic("一百") == "100", _cn_to_arabic("一百"))
check("中文转阿拉伯: 十二→12", _cn_to_arabic("十二") == "12", _cn_to_arabic("十二"))
check("中文数字提取: 含单位", "九十九点七" in _cn_nums("正确率九十九点七"), str(_cn_nums("正确率九十九点七")))
check("中文数字提取: 忽略'一个'", "一个" not in _cn_nums("这是一个问题"), str(_cn_nums("这是一个问题")))

# 3) 部分引用但含超纲结论 → 线上 local embedding 下由超纲反查兜底拦截。
#    本地 selftest（哈希向量无语义）按契约跳过 out_of_scope → 放行（不误拦）。
edge_answer = "转折之后是重点。这个理论最早由量子物理学家薛定谔提出，是公考最重要的发现。"
passed3, detail3 = run_guard(edge_answer, hits, tcfg, "T001")
check("超纲结论: selftest 下 out_of_scope 跳过(线上 local 才拦截)",
      passed3 is True and "out_of_scope" not in detail3.get("fails", []), str(detail3))

# 4) 空知识库 → 不误拦（guard 跳过超纲反查）
empty_hits = []
passed4, _ = run_guard("任何回答内容", empty_hits, tcfg, "T999")
check("空库: 不误拦(引用校验空通过)", passed4 is True)

# 5) 拦截计数
counter = GuardCounter()
counter.record("T001", "test_query", "reference")
counter.record("T001", "test_query2", "out_of_scope")
counter.record("T002", "x", "fact")
stats = counter.stats()
check("计数: T001 拦截 2 次", stats["by_teacher"].get("T001", 0) == 2)
check("计数: 总拦截 3 次", stats["total"] == 3)
check("计数: 原因分布", stats["by_reason"]["reference"] == 1 and stats["by_reason"]["out_of_scope"] == 1)

# 6) 拦截日志（供调优/监控）
logs = counter.recent(limit=10)
check("日志: 记录 3 条含原因", len(logs) == 3 and all(l.get("reason") for l in logs))

# =====================================================================
# v2 分级拦截：区分真幻觉 vs 合理助教表达 / 分级处理 / 可配置策略
# =====================================================================
from poc.guard import detect_scene, resolve_policy, GuardPolicy  # noqa: E402

# v2 语料补充：带数字的考情片段（关键数字命中用例需要）
store.upsert_chunks([Chunk(
    content="行测言语理解模块共40道题，建议用时35分钟，正确率目标75%，每年约有2到3道转折关系题。",
    doc_name="考情.md", idx=0)], "T001")
hits2 = store.retrieve("言语理解考情", "T001")

# 7) 场景检测（问题类型 → 教学场景）
check("v2场景: 问候→chat", detect_scene("你好呀") == "chat")
check("v2场景: 求解→problem", detect_scene("这道题怎么做") == "problem")
check("v2场景: 概念→concept", detect_scene("什么是转折关系") == "concept")
check("v2场景: 默认→general", detect_scene("转折关系") == "general")

# 8) 合理助教表达（v1 一刀切误拦 → v2 放行）
# 8a. 序数 + 行首/冒号后列表编号 + 算式推导 + 结果复用（数字均不在语料）
math_answer = ("咱们分三步：1. 找转折词，2. 锁定重点——转折之后是重点，3. 对照选项。"
               "第2步最关键。设 2x+3=11，解得 x=4，所以这道题的答案是4。")
passed_m, detail_m = run_guard(math_answer, hits, tcfg, "T001", query="第3题怎么做")
check("v2合理表达: 序数/列表/算式全豁免不拦截", passed_m is True and detail_m["level"] == "pass",
      str(detail_m))

# 8b. 关键数字正确引用（40/35/75% 均在语料）→ 放行
stat_answer = "言语理解一共40道题，建议用时35分钟，正确率目标75%，转折之后是重点。"
passed_s, detail_s = run_guard(stat_answer, hits2, tcfg, "T001")
check("v2合理表达: 语料内数字全命中放行", passed_s is True and detail_s["level"] == "pass",
      str(detail_s))

# 8c. 问题原文数字回声（复述用户输入不算编造）
echo_answer = "你的正确率目标75%目前还差一点，建议多练转折关系题。"
passed_e, detail_e = run_guard(echo_answer, hits2, tcfg, "T001", query="正确率75%怎么提高")
check("v2合理表达: 问题数字回声豁免", passed_e is True and detail_e["level"] in ("pass", "warn"),
      str(detail_e))

# 8d. 中文万级换算形（答案"十二万" ↔ 语料"12万"）
store.upsert_chunks([Chunk(content="该省去年招录12万人，竞争激烈。", doc_name="考情2.md", idx=0)], "T001")
hits3 = store.retrieve("招录人数", "T001")
passed_w, detail_w = run_guard("该省去年招录十二万人，竞争非常激烈。", hits3, tcfg, "T001")
check("v2合理表达: 中文万级换算不误拦", passed_w is True and detail_w["level"] in ("pass", "warn", "confirm"),
      str(detail_w))

# 9) 分级正确性
# 9a. warn 档：仅 1 个公共 4-gram（引用偏少）+ 无数字问题
warn_answer = "记住，转折之后是要重点看的内容，其他都可以先放一放。"
passed_a, detail_a = run_guard(warn_answer, hits, tcfg, "T001")
check("v2分级: 弱引用→warn 放行+提醒", passed_a is True and detail_a["level"] == "warn", str(detail_a))

# 9b. confirm 档：纯同义改写零引用证据（不硬拦，请求二次确认）
para_answer = "做题的时候一定要抓住句子最核心的部分，其余的修饰成分都可以先略过去。"
passed_p, detail_p = run_guard(para_answer, hits, tcfg, "T001")
check("v2分级: 同义改写→confirm 放行+以讲义为准", passed_p is True and detail_p["level"] == "confirm",
      str(detail_p))

# 9c. block 保持：编造年份/百分比（真幻觉必须硬拦）
fab_answer = "转折关系题在2024年考了15次，正确率只要62.3%就行。"
passed_f, detail_f = run_guard(fab_answer, hits2, tcfg, "T001")
check("v2分级: 编造年份/百分比→block", passed_f is False and detail_f["level"] == "block"
      and "fact" in detail_f.get("fails", []), str(detail_f))

# 10) 策略可配置：学科预设 → 场景覆盖 → 老师级覆盖
tcfg_changshi = SimpleNamespace(teacher_id="T9", threshold=0.5, teacher_subject="常识判断",
                                guard_policy=None)
pol_cs, _ = resolve_policy(tcfg_changshi, "随便问点啥")
check("v2策略: 常识学科→超纲容忍放宽", pol_cs.scope_block_ratio == 0.80 and pol_cs.scope_pass_ratio == 0.40)

tcfg_math = SimpleNamespace(teacher_id="T8", threshold=0.5, teacher_subject="数量关系", guard_policy=None)
pol_math, _ = resolve_policy(tcfg_math, "随便问点啥")
check("v2策略: 数量关系→数字关最严", pol_math.fact_max_miss == 0)

_, scene_concept = resolve_policy(tcfg, "什么是转折关系")
check("v2策略: concept场景识别", scene_concept == "concept")
pol_concept, _ = resolve_policy(tcfg, "什么是转折关系")
check("v2策略: concept→超纲收紧", pol_concept.scope_block_ratio == 0.65)

tcfg_over = SimpleNamespace(teacher_id="T8", threshold=0.5, teacher_subject="数量关系",
                            guard_policy={"fact_max_miss": 2, "scope_block_ratio": 0.9})
pol_over, _ = resolve_policy(tcfg_over, "这道题怎么做")
check("v2策略: 老师级覆盖优先级最高", pol_over.fact_max_miss == 2 and pol_over.scope_block_ratio == 0.9)

pol_chat, scene_chat = resolve_policy(tcfg, "你好")
check("v2策略: chat场景最宽松", scene_chat == "chat" and pol_chat.ref_strong_hits == 1
      and pol_chat.fact_max_miss == 3)

# 11) GuardCounter 分级计数（block-only 兼容 + by_level 新增）
counter2 = GuardCounter()
counter2.record("T001", "q1", "reference", level="block")
counter2.record("T001", "q2", "reference", level="warn")
counter2.record("T001", "q3", "fact", level="confirm")
stats2 = counter2.stats()
check("v2计数: by_level 全级别", stats2["by_level"] == {"block": 1, "warn": 1, "confirm": 1},
      str(stats2))
check("v2计数: total/by_reason 仅统计block(看板兼容)",
      stats2["total"] == 1 and stats2["by_reason"].get("reference") == 1
      and "fact" not in stats2["by_reason"], str(stats2))
logs2 = counter2.recent(limit=10)
check("v2计数: 日志带level", all(l.get("level") in ("block", "warn", "confirm") for l in logs2))

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)