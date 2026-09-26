# -*- coding: utf-8 -*-
"""批次18 · 申论批改单测（假 LLM 全流程）。

运行：cd backend && python tests/test_essay_grade.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from poc.essay import EssayStore, grade_essay, ESSAY_TYPES  # noqa: E402

PASS = 0


def chk(name, cond, extra=""):
    global PASS
    assert cond, f"FAIL: {name} {extra}"
    PASS += 1
    print(f"  ok {name}")


FAKE_LLM_OUT = '''
阅卷中……以下是批改结果：
```json
{"total": 32, "dims": [
  {"name": "立意", "score": 9, "full": 10, "comment": "立意准确"},
  {"name": "结构", "score": 8, "full": 10, "comment": "完整但段间衔接弱"},
  {"name": "论证", "score": 8, "full": 10, "comment": "例证单薄"},
  {"name": "语言", "score": 7, "full": 10, "comment": "偶有口语化"}
],
"notes": [{"quote": "基层治理需要多元共治", "note": "观点句有力"},
          {"quote": "大家都要参与", "note": "表述口语化，建议改为“多元主体协同参与”"}],
"advice": ["每段补充1个具体案例", "结尾升华避免口号化", "规范书面语"],
"summary": "整体二类文，立意准但论证深度不足。"}
```
'''


def fake_llm(messages):
    assert "阅卷组长" in messages[0]["content"]
    return FAKE_LLM_OUT


def _store():
    return EssayStore(Path(tempfile.mkdtemp(prefix="gkess_")) / "essay.db")


def test_type_validation():
    st = _store()
    for bad in ("", "hack", "大作文"):
        try:
            grade_essay(st, "u1", {"essay_type": bad, "essay": "字" * 60}, fake_llm)
            raise AssertionError(f"题型 {bad} 未拦截")
        except ValueError:
            pass
    chk("题型白名单校验", True)


def test_length_validation():
    st = _store()
    try:
        grade_essay(st, "u1", {"essay_type": "summary", "essay": "太短"}, fake_llm)
        raise AssertionError("短文未拦截")
    except ValueError as e:
        chk("短文拦截", "至少" in str(e))
    try:
        grade_essay(st, "u1", {"essay_type": "summary", "essay": "字" * 9000}, fake_llm)
        raise AssertionError("超长未拦截")
    except ValueError as e:
        chk("超长拦截", "上限" in str(e))


def test_grade_full_flow():
    st = _store()
    r = grade_essay(st, "u1", {
        "essay_type": "composition", "title": "基层治理",
        "prompt": "围绕基层治理现代化写议论文",
        "essay": "基层治理是国家治理的基石。" + "基层治理需要多元共治，" + "大家都要参与，" + "同心协力共建美好家园，形成共建共治共享格局。" * 3,
    }, fake_llm)
    chk("总分落库并夹到满分内", 0 <= r["total"] <= 40, str(r["total"]))
    chk("维度≥4", len(r["dims"]) >= 4)
    chk("批注引用保留", r["notes"][0]["quote"].startswith("基层治理"))
    chk("建议≥3条", len(r["advice"]) >= 3)
    chk("记录 id 返回", isinstance(r["id"], int) and r["id"] > 0)

    h = st.history("u1")
    chk("历史可见", len(h) == 1 and h[0]["essay_type"] == "composition")
    d = st.get("u1", r["id"])
    chk("详情解析 dims/notes/advice", isinstance(d["dims"], list) and isinstance(d["advice"], list))
    chk("越权不可见", st.get("u2", r["id"]) is None)
    try:
        grade_essay(st, "u1", {"essay_type": "composition",
                               "essay": "字" * 100}, lambda m: (_ for _ in ()).throw(ValueError("AI 服务暂不可用")))
        raise AssertionError("LLM 异常未透出")
    except ValueError as e:
        chk("LLM 异常透出", "AI" in str(e))


def test_llm_malformed_json():
    st = _store()
    try:
        grade_essay(st, "u1", {"essay_type": "summary", "essay": "字" * 80},
                    lambda m: "抱歉我无法以JSON输出")
        raise AssertionError("坏 JSON 未拦截")
    except ValueError as e:
        chk("坏 JSON 拦截", "无法解析" in str(e))


print("申论批改单测：")
test_type_validation()
test_length_validation()
test_grade_full_flow()
test_llm_malformed_json()
print(f"PASS {PASS} assertions")
sys.exit(0)
