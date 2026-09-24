# -*- coding: utf-8 -*-
"""P0-4 · 技能层框架单测（说明书 §四 / §九 P0 验收）。
运行: cd backend && python tests/test_skills_framework.py
覆盖: 模板装配 / 缺变量显式报错 / baseline 逐字节等价 / JSON 修复重试 /
      schema 归一 / 失败归一(kind) / 指标计数。全部 mock LLM，零网络零 token。
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
_TMP = Path(tempfile.mkdtemp(prefix="gk-skills-fw-"))
os.environ["CHAT_DB"] = str(_TMP / "chat.db")
os.environ["DB_PATH"] = str(_TMP / "vec")
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["MODEL_CACHE"] = str(_TMP / "data" / "models")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["STUDY_DB"] = str(_TMP / "study.db")

from poc import skills                              # noqa: E402
from poc.skills import base, q_extract              # noqa: E402

TCFG = SimpleNamespace(temperature=0.3, llm_model="mock")
ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(extra)[:200]) if (extra and not cond) else ""))
    if not cond:
        ok = False


# ---------- 注册表 ----------
check("q-extract 已注册", "q-extract" in base.registry())
try:
    base.get_skill("no-such")
    check("未注册技能报错", False)
except KeyError as e:
    check("未注册技能报错", "no-such" in str(e))

# ---------- 模板装配 / 占位符 ----------
spec = base.get_skill("q-extract")
inp = q_extract.render_inputs(material="题干示例", teacher_subject="行测",
                              allowed_types="choice,judge,essay", source_url="u")
sys_txt, embedded = base.render_prompt(spec, inp)
check("模板渲染含变量值（teacher_subject）", "行测" in sys_txt)
check("渲染无残留占位符", "{teacher_subject}" not in sys_txt
      and "{material}" not in sys_txt)
check("baseline 未内嵌 material（材料走 user 消息）", embedded is False)
# 缺变量显式报错（§七·8）
try:
    base.render_prompt(spec, {"material": "x"})   # 缺 teacher_subject
    check("缺变量抛 KeyError", False)
except KeyError as e:
    check("缺变量抛 KeyError", "teacher_subject" in str(e))


# ---------- baseline 逐字节 = 迁移前内联 prompt ----------
# 迁移前 admin 内联 prompt 已存备份（scripts/admin_py_p0_migrated.bak）；
# 此处从技能模板反查关键锚点（备份不存在则跳过，如线上镜像态）
BAK = BACKEND / "scripts" / "admin_py_p0_migrated.bak"
if not BAK.exists():
    BAK = BACKEND / "poc" / "admin.py.p0bak"
if BAK.exists():
    import ast
    old = BAK.read_text(encoding="utf-8")
    tree = ast.parse(old)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "extract_questions_ai")
    asg = next(n for n in ast.walk(fn) if isinstance(n, ast.Assign)
               and any(getattr(t, "id", "") == "sys_prompt" for t in n.targets))

    def _eval(n):
        if isinstance(n, ast.Constant):
            return str(n.value)
        if isinstance(n, ast.JoinedStr):
            return "".join(str(v.value) if isinstance(v, ast.Constant) else "行测"
                           for v in n.values)
        if isinstance(n, ast.BinOp):
            return _eval(n.left) + _eval(n.right)
        raise AssertionError

    old_text = _eval(asg.value)   # 动态位填「行测」
    check("baseline 与迁移前 prompt 逐字节等价", old_text == sys_txt,
          f"old={len(old_text)} new={len(sys_txt)}")
else:
    print("SKIP baseline 逐字节比对（无 .p0bak 备份）")


# ---------- JSON 修复重试（§4.0 / §七·4） ----------
seen = []


def mock_retry(messages, tcfg, **kw):
    seen.append(messages)
    if len(seen) == 1:
        return "抱歉，这不是JSON。"
    return '[{"qtype":"单选题","question":"关于甲乙，下列正确的是（ ）。",' \
           '"options":{"A":"甲","B":"乙","C":"丙","D":"丁"},"answer":"C",' \
           '"analysis":"","difficulty":"中等","knowledge_point":"逻辑",' \
           '"category":"判断推理"}]'


r = skills.run("q-extract", inp, tcfg=TCFG, llm=mock_retry)
check("坏输出重试后成功", r.ok and r.data and len(r.data) == 1)
check("重试消息带失败原因", len(seen) == 2 and "无法解析" in seen[1][-1]["content"])
check("qtype 变体归一 单选题→choice", r.ok and r.data[0]["qtype"] == "choice")
check("options 对象归一字符串数组", r.ok and r.data[0]["options"][0].startswith("A."))
check("difficulty 中文归一整数", r.ok and r.data[0]["difficulty"] == 3)
check("category 归一", r.ok and r.data[0]["category"] == "判断推理")


# ---------- 失败归一：恒坏输出 → kind=parse ----------
def mock_always_bad(messages, tcfg, **kw):
    return "非JSON非JSON非JSON"


r2 = skills.run("q-extract", inp, tcfg=TCFG, llm=mock_always_bad)
check("恒坏输出 ok=False", not r2.ok)
check("恒坏输出 kind=parse", r2.kind == "parse")
check("恒坏输出 error 非空", bool(r2.error))
# 重试次数 = spec.attempts（不超发）
calls = []
skills.run("q-extract", inp, tcfg=TCFG,
           llm=lambda m, t, **k: (calls.append(1), "x")[1])
check("重试次数上限 = attempts", len(calls) == spec.attempts)


# ---------- LLM 异常归一：kind=llm，原始 exc 可上抛 ----------
def mock_boom(messages, tcfg, **kw):
    raise RuntimeError("AI 服务暂不可用：connection reset")


r3 = skills.run("q-extract", inp, tcfg=TCFG, llm=mock_boom)
check("LLM 异常 ok=False kind=llm", (not r3.ok) and r3.kind == "llm")
check("LLM 异常携带原始 exc", isinstance(r3.exc, RuntimeError))


# ---------- 模板缺失归一 ----------
from poc.skills.base import SkillSpec  # noqa: E402
bad_spec = base.register(SkillSpec(skill_id="__no_tpl__", template_name="__none__.md"))
r4 = skills.run("__no_tpl__", inp, tcfg=TCFG, llm=mock_retry)
check("模板缺失归一 kind=config", (not r4.ok) and r4.kind == "config")
del base.registry()["__no_tpl__"]


# ---------- 指标 ----------
snap = base.metrics.snapshot()
check("成功指标计数", any(k.startswith("q-extract:ok") for k in snap["calls"]))
check("失败指标计数", any(k.startswith("q-extract:fail") for k in snap["calls"]))
lines = base.prom_lines()
check("prom_lines 含 gk_skill_calls_total", any("gk_skill_calls_total" in x for x in lines))
check("prom_lines 含 gk_skill_latency_ms", any("gk_skill_latency_ms" in x for x in lines))

print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
sys.exit(0 if ok else 1)
