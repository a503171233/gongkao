# -*- coding: utf-8 -*-
"""q-extract 真题提取技能（说明书 §4.1，P0 最高优先）。

服务两处层1提取（共用本技能，只技能化 prompt，采集四层防线中仅层1）：
  - autocollect 层1（_extract_via → 本模块）
  - admin.extract_questions_ai（文档导题）

行为保持纪律（§九 P0）：
  - 内部题目结构（qtype/question/options:字符串数组/answer/analysis/
    difficulty/knowledge_point/category/number）与迁移前 _normalize_questions
    产出一致，下游（层2/3/4、题库入库、题源拼接）零改动；
  - §4.1 理想化输出形状（type/stem/options:[{key,text}]/confidence）由
    normalize_payload() 归一到内部形状，两种模型输出形态都能落库；
  - baseline 技能（q-extract-baseline）：模板=现行 admin prompt 逐字节摘录，
    解析=现行 _parse_llm_json_array + _normalize_questions 原样，
    用于离线行为比对与 P0 默认兜底路径。

温度纪律：抽取类 0.1（§七·6）。max_tokens：4000（§4.1）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter

from .base import SkillSpec, register


# ---------- §4.1 理想化输出形状（schema 文档化 + 新模板产出） ----------
class QOption(BaseModel):
    key: str = ""
    text: str = ""


class QItem(BaseModel):
    type: Literal["choice", "judge", "essay"] = "choice"
    stem: str = ""
    options: list[QOption | str] = Field(default_factory=list)
    answer: str = ""
    analysis: str = ""
    category: str = ""
    confidence: float = 0.0


QExtractOut = TypeAdapter(list[QItem])


# ---------- 形状归一（内部题目 dict 与迁移前 _normalize_questions 对齐） ----------
_QTYPE_MAP = {
    "choice": "choice", "选择": "choice", "单选题": "choice", "单选": "choice",
    "选择题": "choice", "多选": "choice", "多选题": "choice",
    "judge": "judge", "判断": "judge", "判断题": "judge",
    "essay": "essay", "简答": "essay", "简答题": "essay", "主观题": "essay",
    "论述": "essay", "论述题": "essay", "材料题": "essay", "综合": "essay",
}
_DIFF_MAP = {"很简单": 1, "简单": 1, "易": 1, "较易": 1, "较简单": 2, "偏易": 2,
             "中等": 3, "中": 3, "一般": 3, "较难": 4, "偏难": 4, "困难": 4,
             "难": 4, "很难": 5, "极难": 5, "最难": 5, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}


def _opts_to_strings(raw) -> list[str]:
    """options 兼容：数组['A. x'] / 对象{'A':x} / [{key,text}]；过滤空项。"""
    out: list[str] = []
    if isinstance(raw, dict):
        out = [f"{k}. {v}" for k, v in raw.items() if str(v).strip()]
    elif isinstance(raw, list):
        for o in raw:
            if isinstance(o, dict):
                k = str(o.get("key", "")).strip()
                t = str(o.get("text", "")).strip()
                out.append(f"{k}. {t}" if k else t)
            elif str(o).strip():
                out.append(str(o).strip())
    return [x for x in out if x]


def normalize_payload(payload) -> list[dict]:
    """模型输出数组（§4.1 理想化形状或现行内部形状皆可）→ 内部题目 dict 列表。
    逐条宽松归一（兼容模型混用字段名：stem/question、type/qtype），
    无题干条目丢弃；空数组合法（无题可提）。"""
    if not isinstance(payload, list):
        raise ValueError("输出顶层不是 JSON 数组")
    out: list[dict] = []
    for it in payload:
        if not isinstance(it, dict):
            continue
        stem = str(it.get("stem") or it.get("question") or "").strip()
        if not stem:
            continue
        qtype = _QTYPE_MAP.get(
            str(it.get("type") or it.get("qtype") or "").strip().lower(), "essay")
        opts = _opts_to_strings(it.get("options")) if qtype == "choice" else []
        dv = str(it.get("difficulty", "")).strip().lower()
        try:
            diff = max(1, min(5, int(dv or 3)))
        except (TypeError, ValueError):
            diff = _DIFF_MAP.get(dv, 3)
        try:
            number = int(it.get("number"))
        except (TypeError, ValueError):
            number = None
        item = {
            "qtype": qtype,
            "question": stem,
            "options": opts,
            "answer": str(it.get("answer", "")).strip(),
            "analysis": str(it.get("analysis", "")).strip(),
            "difficulty": diff,
            "knowledge_point": str(it.get("knowledge_point", "")).strip(),
            "category": str(it.get("category", "")).strip() or "综合",
            "number": number,
        }
        if "confidence" in it:
            try:
                item["confidence"] = max(0.0, min(1.0, float(it.get("confidence") or 0)))
            except (TypeError, ValueError):
                pass
        out.append(item)
    return out


def qitem_to_internal(it: QItem) -> dict:
    """§4.1 QItem（pydantic 模型）→ 内部 dict。"""
    qtype = _QTYPE_MAP.get(str(it.type).strip().lower(), "essay")
    opts = _opts_to_strings(it.options) if qtype == "choice" else []
    return {
        "qtype": qtype,
        "question": (it.stem or "").strip(),
        "options": opts,
        "answer": (it.answer or "").strip(),
        "analysis": (it.analysis or "").strip(),
        "difficulty": 3,
        "knowledge_point": "",
        "category": (it.category or "").strip() or "综合",
        "number": None,
        "confidence": it.confidence,   # 仅参考；硬门槛在 autocollect 层2 代码
    }


# ---------- 解析/归一分发（延迟 import admin，避免与业务模块循环） ----------
def _admin_array_parser(raw: str):
    """复用现行解析器（剥围栏+修复+截断抢救）。返回 (payload, err)。"""
    from ..admin import _parse_llm_json_array
    return _parse_llm_json_array(raw)


def _legacy_normalize(payload):
    """现行归一化原样（_normalize_questions：清洗图片标记/分类校验/字段兜底）。"""
    from ..admin import _normalize_questions
    return _normalize_questions(payload)


def pipeline_normalize(payload) -> list[dict]:
    """optimized 模板专用后处理：先形状归一（normalize_payload：stem→question、
    options 对象→字符串、type→qtype），再过现行 _normalize_questions，
    保证图片标记清洗、category 枚举校验、number 剥离、images 提取与迁移前一致。
    （baseline 输出已是现行字段名，直接走 _legacy_normalize，避免 difficulty
    缺省值等细节漂移。）"""
    return _legacy_normalize(normalize_payload(payload))


def _dispatch_normalize(payload):
    """运行期按模板分发 validator：baseline=现行归一原样（行为保持），
    optimized=形状归一+现行归一。改模板不动注册代码。"""
    return pipeline_normalize(payload) if is_optimized() else _legacy_normalize(payload)


# ---------- 模板变量装配 ----------
def render_inputs(*, material: str, teacher_subject: str = "公考",
                  allowed_types: str = "choice,judge,essay",
                  source_url: str = "") -> dict:
    # teacher_subject 不做 strip()/改写：与迁移前 f-string
    # 「该科目方向：{teacher_subject or '公考'}」逐字节等价（含纯空白取值原样保留）
    return {
        "material": material,
        "teacher_subject": teacher_subject or "公考",
        "allowed_types": allowed_types or "choice,judge,essay",
        "source_url": source_url or "（未提供）",
    }


# ---------- 注册（单一技能 + 运行期模板选择：§九 P0-2 门禁前默认 baseline） ----------
OPTIMIZED_TEMPERATURE = 0.1             # §七·6 抽取类温度档（optimized 生效）
LEGACY_MAX_TOKENS = 4096                # 现行抽取 max_tokens（调用方总是显式传，此处仅文档）


def _current_template() -> str:
    """SKILL_EXTRACT_TEMPLATE=optimized → §4.1 新模板；默认 baseline（迁移前逐字节等价）。
    baseline 切换 optimized 的前置门禁：python -m poc.skills.selftest --compare 必收全过+必拒全拒。"""
    from ..config import get
    t = (get("SKILL_EXTRACT_TEMPLATE", "baseline") or "baseline").strip().lower()
    return "q-extract.md" if t == "optimized" else "_baseline-q-extract.md"


def is_optimized() -> bool:
    return _current_template() == "q-extract.md"


def subject_for_template(teacher_subject: str) -> str:
    """teacher_subject → 模板占位符取值。
    baseline 必须复刻迁移前 f-string 语义 `teacher_subject or '公考'`
    （空白串是"真值"，原样输出）；verify_baseline_equiv.py 做逐字节门禁。
    optimized 模板无历史包袱，空白归一为 '公考'。"""
    if is_optimized():
        return (teacher_subject or "").strip() or "公考"
    return teacher_subject or "公考"


def render_inputs(*, material: str, teacher_subject: str = "公考",
                  allowed_types: str = "choice,judge,essay",
                  source_url: str = "") -> dict:
    return {
        "material": material,
        "teacher_subject": subject_for_template(teacher_subject),
        "allowed_types": allowed_types or "choice,judge,essay",
        "source_url": source_url or "（未提供）",
    }


SPEC = register(SkillSpec(
    skill_id="q-extract",
    validator=_dispatch_normalize,       # 运行期按模板分发（baseline 逐字节等价路径）
    parser=_admin_array_parser,          # 沿用现行解析器（剥围栏+修复+截断抢救）
    default_max_tokens=LEGACY_MAX_TOKENS,
    default_temperature=None,            # baseline 透传老师温度；optimized 由调用方传 0.1
    template_resolver=_current_template,
    attempts=3,                          # 对齐现行"3 次带原因重试"
))


if __name__ == "__main__":  # pragma: no cover
    print("q-extract registered:", SPEC.skill_id)
