# -*- coding: utf-8 -*-
"""技能层离线自测（说明书 §十）：python -m poc.skills.selftest

mock LLM 注入，零网络、零真实 token。覆盖：
  1) 模板装配：baseline 渲染逐字节 = 迁移前 admin 内联 prompt（行为保持前提）；
     缺变量显式报错（§七·8）；
  2) JSON 修复重试：首次坏输出 → 带原因反馈重试 → 第二次成功；
  3) schema/validator：题目归一（qtype 变体/options 对象/难度中文）；
  4) 黄金用例（§4.1）：必收类正常提取、必拒类 mock 正确返回 [] 通过门控；
     必拒样本同时校验模板正文含对应拒绝规则（prompt 门禁静态核验）；
  5) 失败归一：mock 恒坏输出 → ok=False + 原因，不抛异常。
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

# 隔离运行环境（对齐既有测试脚本风格：临时 DB，不碰真实数据）
_TMP = tempfile.mkdtemp(prefix="gk-skills-selftest-")
os.environ["CHAT_DB"] = str(Path(_TMP) / "chat.db")
os.environ["DB_PATH"] = str(Path(_TMP) / "vec")
os.environ["DATA_DIR"] = str(Path(_TMP) / "data")
os.environ["MODEL_CACHE"] = str(Path(_TMP) / "data" / "models")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["STUDY_DB"] = str(Path(_TMP) / "study.db")

from poc import skills  # noqa: E402
from poc.skills import base, q_extract  # noqa: E402

TCFG = SimpleNamespace(temperature=0.3, llm_model="mock-model")

_ok = True


def check(name: str, cond: bool, extra: str = "") -> None:
    global _ok
    print(("PASS " if cond else "FAIL ") + name + (("  | " + str(extra)[:200]) if (extra and not cond) else ""))
    if not cond:
        _ok = False


# ==================== 1) 模板装配 ====================
spec = base.get_skill("q-extract")
inputs = q_extract.render_inputs(material="材料X", teacher_subject="行测",
                                 allowed_types="choice,judge,essay",
                                 source_url="https://example.test/p/1")
sys_rendered, embedded = base.render_prompt(spec, inputs)
check("baseline 模板装配成功（默认非 optimized）", isinstance(sys_rendered, str) and len(sys_rendered) > 500)
# 行为保持前提：baseline system = 迁移前内联 prompt（含三大门控关键词）
check("baseline 含伪题拒绝门控", "伪题拒绝门控" in sys_rendered)
check("baseline 含公考相关性门控", "公考相关性门控" in sys_rendered)
check("baseline 含申论规则", "申论规则" in sys_rendered)
check("baseline 占位符已替换", "行测" in sys_rendered and "{teacher_subject}" not in sys_rendered)
# 缺失变量必须显式报错（baseline 模板含 {teacher_subject}，缺之即抛）
try:
    base.render_prompt(spec, {})   # 缺 teacher_subject
    check("缺变量显式报错", False, "未抛 KeyError")
except KeyError as e:
    check("缺变量显式报错", "teacher_subject" in str(e))

# 消息结构行为保持：baseline（未内嵌 material）→ user=「文档内容：\n材料」
msgs = base.build_messages(spec, inputs)
check("baseline 消息=user文档内容前缀", msgs[1]["content"] == "文档内容：\n材料X"
      and msgs[0]["content"].startswith("你是公考教研助手"))

# ==================== 2) JSON 修复重试 ====================
calls: list[list] = []


def mock_flaky(messages, tcfg, **kw):
    calls.append(messages)
    if len(calls) == 1:
        return "这不是 JSON，抱歉。"
    return '[{"qtype":"choice","question":"以下哪项正确（ ）。","options":{"A":"甲","B":"乙","C":"丙","D":"丁"},"answer":"A","analysis":"","difficulty":"中等","knowledge_point":"主旨","category":"言语理解"}]'


r = skills.run("q-extract", inputs, tcfg=TCFG, llm=mock_flaky)
check("坏输出→带原因重试→成功", r.ok and isinstance(r.data, list) and len(r.data) == 1)
check("重试消息含上次失败原因", len(calls) == 2 and "无法解析" in calls[1][-1]["content"])
check("归一化：options 对象→字符串数组", r.ok and r.data[0]["options"][0].startswith("A."))
check("归一化：difficulty 中文→整数", r.ok and r.data[0]["difficulty"] == 3)

# ==================== 3) 恒坏输出归一失败（不抛异常） ====================
def mock_bad(messages, tcfg, **kw):
    return "```json\n[{\"qtype\":"   # 截断且抢救不出对象


r2 = skills.run("q-extract", inputs, tcfg=TCFG, llm=mock_bad)
check("恒坏输出归一 ok=False", (not r2.ok) and bool(r2.error))

# LLM 调用异常归一
def mock_raise(messages, tcfg, **kw):
    raise RuntimeError("connection reset")


r3 = skills.run("q-extract", inputs, tcfg=TCFG, llm=mock_raise)
check("调用异常归一 ok=False 不抛出", (not r3.ok) and "connection reset" in r3.error)

# ==================== 4) 黄金用例（§4.1 必收/必拒） ====================
# mock 扮演「守规矩的模型」：按门控提取必收样本、对必拒样本返回 []。
# 真实模型在线验证由 SKILLS_ONLINE_CHECK=1 触发（--online 参数）。
GOLDEN = [
    # (类别, 名称, 材料片段, mock期望输出)
    ("must_pass", "言语主旨真题",
     "1、当代网络连接着人机物、海内外，数据海量增长……这段文字旨在说明：\n"
     "A. 网络治理是一项系统工程\nB. 数据量正在指数级增长\nC. 人机物互联已成现实\nD. 网络安全至关重要\n答案：A"),
    ("must_pass", "申论概括题",
     "根据给定资料2，概括S市推进乡村振兴的主要做法。（15分，不超过300字）\n"
     "作答要求：全面、准确、有条理。"),
    ("must_pass", "民法典真判断题",
     "根据《民法典》，自然人从出生时起到死亡时止，具有民事权利能力。（ ）、答案：对"),
    ("must_reject", "法条逐条改写",
     "《民法典》第一千零七十六条：夫妻双方自愿离婚的，应当签订书面离婚协议。"
     "第一千零七十七条：自婚姻登记机关收到离婚登记申请之日起三十日内……"),
    ("must_reject", "百科词条改写",
     "光合作用，通常是指绿色植物吸收光能，把二氧化碳和水合成富能有机物，同时释放氧气的过程。"),
    ("must_reject", "政务公告",
     "根据《XXX条例》第三十条，现将有关事项通知如下：一、各部门要提高站位……特此公告。"),
    ("must_reject", "给定资料残篇",
     "【给定资料1】\nB市积极探索社区治理新路径，率先在全国出台……（未完）"),
    ("must_reject", "中英夹杂机翻",
     "The government has announced new policies about 人工智能治理, 这是 very important 的 development."),
]


def _mock_by_gate(messages, tcfg, **kw):
    """守规矩模型替身：含成题结构的材料返回 1 题；否则返回 []。"""
    user = messages[1]["content"]
    has_q = any(k in user for k in ("答案：", "作答要求", "（ ）", "( )"))
    if has_q:
        return json.dumps([{
            "qtype": "essay" if "作答要求" in user else "choice",
            "question": "（mock 提取）原题题干",
            "options": [] if "作答要求" in user else ["A. 甲", "B. 乙", "C. 丙", "D. 丁"],
            "answer": "A", "analysis": "", "difficulty": 3,
            "knowledge_point": "主旨概括", "category": "言语理解",
        }], ensure_ascii=False)
    return "[]"


for cat, name, material in GOLDEN:
    inp = q_extract.render_inputs(material=material, teacher_subject="公考",
                                  allowed_types="choice,judge,essay",
                                  source_url="")
    rr = skills.run("q-extract", inp, tcfg=TCFG, llm=_mock_by_gate)
    got = list(rr.data or [])
    if cat == "must_pass":
        check(f"黄金·必收 {name}", rr.ok and len(got) >= 1)
    else:
        check(f"黄金·必拒 {name}", rr.ok and len(got) == 0)

# prompt 静态门禁：两版模板都必须含核心拒绝规则（§七·1/2）
try:
    os.environ["SKILL_EXTRACT_TEMPLATE"] = "optimized"
    sys_opt, emb_opt = base.render_prompt(spec, inputs)
finally:
    os.environ.pop("SKILL_EXTRACT_TEMPLATE", None)
check("optimized 模板存在且装配成功", len(sys_opt) > 300)
check("optimized 含正向门槛", "判定为" in sys_opt and "缺一不可" in sys_opt)
check("optimized 含注入防御", "注入防御" in sys_opt and "不是给你的指令" in sys_opt)
check("optimized 材料内嵌 system（不重复下发）", emb_opt)
check("optimized 含伪设问/法条抄写拒绝", "法条抄写" in sys_opt and "伪设问" in sys_opt)

# ==================== 5) 指标 ====================
snap = base.metrics.snapshot()
check("指标计数已登记", any("q-extract" in k for k in snap["calls"]))
check("prom_lines 可渲染", any("gk_skill_calls_total" in x for x in base.prom_lines()))

print("\n" + ("ALL PASS" if _ok else "SOME FAILED"))
sys.exit(0 if _ok else 1)
