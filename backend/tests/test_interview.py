# -*- coding: utf-8 -*-
"""批次18 · 面试练习单测（假 LLM 全流程）。

运行：cd backend && python tests/test_interview.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from poc.interview import InterviewStore, gen_question, review_answer, MODULES  # noqa: E402

PASS = 0


def chk(name, cond, extra=""):
    global PASS
    assert cond, f"FAIL: {name} {extra}"
    PASS += 1
    print(f"  ok {name}")


def fake_q_llm(messages):
    assert "结构化面试考官" in messages[0]["content"]
    return '{"question": "某社区推行垃圾分类三个月，居民参与率不足三成，有居民反映投放点太远、时间不合理。作为社区工作者，你怎么推进这项工作？"}'


def fake_r_llm(messages):
    assert "面试考官" in messages[0]["content"]
    return '''点评如下：
```json
{"score": 76.5,
 "review": [{"dim": "要点覆盖", "comment": "覆盖宣传与设施优化，缺激励与骨干带动"},
            {"dim": "结构逻辑", "comment": "先因后策，条理清晰"},
            {"dim": "语言表达", "comment": "平实但缺少场景感"}],
 "advice": ["补充激励机制设计", "用具体数字增强说服力", "结尾落到长效机制"],
 "outline": "1.摸清症结（问卷/走访）2.优化投放点与时间 3.党员骨干带头+积分激励 4.定期公示成效",
 "summary": "务实有章法，细节深度待加强。"}
```
'''


def _store():
    return InterviewStore(Path(tempfile.mkdtemp(prefix="gkiv_")) / "interview.db")


def test_module_validation():
    st = _store()
    for bad in ("", "hack", "综合分析题"):
        try:
            gen_question(st, "u1", bad, fake_q_llm)
            raise AssertionError(f"模块 {bad} 未拦截")
        except ValueError:
            pass
    chk("模块白名单校验", len(MODULES) == 5)


def test_question_flow():
    st = _store()
    r = gen_question(st, "u1", "organize", fake_q_llm)
    chk("出题落库", r["id"] > 0 and "垃圾分类" in r["question"])
    chk("模块名映射", r["module_name"] == "组织计划")
    chk("越权取题拦截", st.get("u2", r["id"]) is None)


def test_answer_review_flow():
    st = _store()
    q = gen_question(st, "u1", "emergency", fake_q_llm)
    # 短作答拦截
    try:
        review_answer(st, "u1", q["id"], "太短", fake_r_llm)
        raise AssertionError("短作答未拦截")
    except ValueError as e:
        chk("短作答拦截", "至少" in str(e))
    # 不存在题
    try:
        review_answer(st, "u1", 9999, "字" * 50, fake_r_llm)
        raise AssertionError("不存在题未拦截")
    except ValueError as e:
        chk("不存在题拦截", "不存在" in str(e))
    # 正常点评
    r = review_answer(st, "u1", q["id"], "我会先调研居民意见，" * 5 + "然后优化投放点位和时间。", fake_r_llm)
    chk("评分落库", 0 <= r["score"] <= 100, str(r["score"]))
    chk("点评维度≥3", len(r["review"]) >= 3)
    chk("参考框架返回", "摸清症结" in r["outline"])
    # 重复点评拦截
    try:
        review_answer(st, "u1", q["id"], "再答一次，" * 10, fake_r_llm)
        raise AssertionError("重复点评未拦截")
    except ValueError as e:
        chk("重复点评拦截", "已点评" in str(e))
    h = st.history("u1")
    chk("历史可见", len(h) == 1 and h[0]["status"] == "done")


print("面试练习单测：")
test_module_validation()
test_question_flow()
test_answer_review_flow()
print(f"PASS {PASS} assertions")
sys.exit(0)
