# -*- coding: utf-8 -*-
"""技能层统一契约（《运行时AI技能与MCP能力开发说明书》§4.0）。

设计纪律：
  - prompt 只存在于 prompts/*.md（模板版本化，改 prompt 不动 Python）；
  - 模板变量必须显式：占位符缺失直接报错（防"半截 prompt"事故）；
  - 校验在代码不在 prompt：输出必过 schema/validator；解析失败自动带原因
    重试（spec.attempts，含首次）；仍失败返回 ok=False + 原因，由调用方决定
    降级，不把异常抛到业务外；
  - LLM 唯一出口仍是调用方注入的 llm 回调（默认 poc.llm.call_llm），不改其
    重试/降级签名；技能层不感知老师（tcfg 由调用方传入）；
  - 指标：技能调用/失败计数与平均时延（零依赖，prom_lines() 供 /metrics 暴露）。

稳定契约：
    run(skill_id, inputs, *, tcfg, max_tokens=0, temperature=None, model=None,
        base_url=None, api_key=None, llm=None) -> SkillResult
    llm 回调签名与 llm.call_llm 一致：(messages, tcfg, max_tokens=, temperature=,
    model=, base_url=, api_key=) -> str。单测/自测可注入 mock。
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, TypeAdapter, ValidationError

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# 默认解析失败反馈话术（q-extract 迁移前 admin 循环原话，保持行为一致）
DEFAULT_RETRY_MSG = ("你上一次的输出无法解析（{err}）。请重新输出：必须是完整、合法、"
                     "未截断的 JSON 数组，不要包含 JSON 以外的任何文字。")


# ---------- 指标（沿用项目零依赖风格，对齐 metrics.py 精神） ----------
class _SkillMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: Counter = Counter()     # (skill, result) -> count
        self._latency: dict[str, list] = {}  # skill -> [ms,...]（保留最近 500）

    def record(self, skill: str, ok: bool, ms: float) -> None:
        with self._lock:
            self._calls[(skill, "ok" if ok else "fail")] += 1
            lst = self._latency.setdefault(skill, [])
            lst.append(round(ms))
            if len(lst) > 500:
                del lst[:len(lst) - 500]

    def snapshot(self) -> dict:
        with self._lock:
            calls = {f"{k[0]}:{k[1]}": v for k, v in self._calls.items()}
            lat = {k: {"avg_ms": round(sum(v) / len(v)), "n": len(v)}
                   for k, v in self._latency.items() if v}
            return {"calls": calls, "latency": lat}


metrics = _SkillMetrics()


@dataclass
class SkillResult:
    ok: bool          # False 时 data=None，error 说明原因
    data: Any = None  # 已通过 schema/validator 校验的结构化输出
    error: str = ""
    raw: str = ""     # 模型原始输出（排查用，写日志不返前端）
    kind: str = ""    # ""=正常 | config=模板/变量缺失 | llm=调用失败 | parse=解析校验耗尽
    exc: Any = None   # kind=llm 时携带底层原始异常对象（调用方 re-raise 保旧语义）


@dataclass
class SkillSpec:
    """一个技能的定义：模板文件 + 校验器 + 默认调用参数 + 解析重试策略。"""
    skill_id: str
    schema: TypeAdapter | None = None          # pydantic TypeAdapter（与 validator 二选一）
    validator: Callable[[Any], Any] | None = None  # payload -> data；校验失败抛 ValueError
    parser: Callable[[str], tuple[Any, str | None]] | None = None
    # 自定义解析器 (raw)->(payload, err)；None 用内置剥围栏 + json.loads
    default_max_tokens: int = 2000
    default_temperature: float | None = None   # None=透传调用方（tcfg 温度）
    template_name: str | None = None           # 默认 {skill_id}.md
    template_resolver: Callable[[], str] | None = None  # 运行期动态选模板（返回文件名）
    attempts: int = 2                          # 总尝试次数（含首次）；解析失败带原因重试
    retry_msg: str = DEFAULT_RETRY_MSG
    user_fmt: str = "文档内容：\n{material}"    # 模板未内嵌 {material} 时的 user 消息格式
    user_when_embedded: str = "请按上述规则输出题目 JSON 数组。"

    @property
    def prompt_path(self) -> Path:
        name = self.template_resolver() if self.template_resolver else \
            (self.template_name or f"{self.skill_id}.md")
        return _PROMPT_DIR / name


# ---------- 模板装配 ----------
def load_template(spec: SkillSpec) -> str:
    """读模板正文（剥离开头全部版本注释块）。缺失直接抛错，调用方归一为 ok=False。"""
    try:
        tpl = spec.prompt_path.read_text(encoding="utf-8")
    except OSError as e:
        raise KeyError(f"技能模板缺失: {spec.prompt_path.name}（{e}）") from e
    return re.sub(r"^(<!--.*?-->\s*)+", "", tpl, count=1, flags=re.S)


def render_prompt(spec: SkillSpec, inputs: dict) -> tuple[str, bool]:
    """渲染 system 部分；返回 (渲染结果, 模板是否内嵌 {material})。
    缺失占位符直接抛 KeyError（§七·8 变量显式）。"""
    tpl = load_template(spec)
    embedded = "{material}" in tpl

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in inputs:
            raise KeyError(f"技能 {spec.skill_id} 模板变量缺失: {key}")
        v = inputs[key]
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)

    return _PLACEHOLDER_RE.sub(_sub, tpl).strip(), embedded


def build_messages(spec: SkillSpec, inputs: dict) -> list[dict]:
    """system=模板渲染；材料位置由模板决定：
      - baseline 模板无 {material} → 材料走 user=「文档内容：\\n{material}」，
        与迁移前 admin 消息结构逐字节等价（行为保持比对前提）；
      - 优化模板 §4.1 在 system 内嵌 {material} → user 用固定指令，
        避免同一材料在 system+user 各发一份白烧 token。"""
    system, embedded = render_prompt(spec, inputs)
    material = inputs.get("material")
    if material is None:
        material = inputs.get("text")
    if not isinstance(material, str) or not material.strip():
        raise KeyError(f"技能 {spec.skill_id} 缺少非空输入 material/text")
    user = spec.user_when_embedded if embedded else spec.user_fmt.format(material=material)
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


# ---------- 内置解析/校验 ----------
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def parse_json_output(raw: str) -> tuple[Any, str | None]:
    """剥 ```json 围栏 → json.loads。返回 (payload, err)。"""
    s = (raw or "").strip()
    if not s:
        return None, "模型返回为空"
    m = _FENCE_RE.match(s)
    if m:
        s = m.group(1)
    try:
        return json.loads(s), None
    except ValueError as e:
        return None, f"JSON 解析失败: {str(e)[:80]}"


def schema_validator(spec: SkillSpec) -> Callable[[Any], Any]:
    """schema(TypeAdapter) 包成 validator：失败抛 ValueError（含逐字段原因）。"""
    def _v(payload: Any) -> Any:
        try:
            return spec.schema.validate_python(payload)  # type: ignore[union-attr]
        except ValidationError as e:
            errs = "; ".join(".".join(str(p) for p in x["loc"]) + ": " + x["msg"]
                             for x in e.errors()[:5])
            raise ValueError(f"schema 校验失败: {errs[:200]}") from e
    return _v


# ---------- 注册表 ----------
_REGISTRY: dict[str, SkillSpec] = {}


def registry() -> dict[str, SkillSpec]:
    return _REGISTRY


def get_skill(skill_id: str) -> SkillSpec:
    _autoload()
    s = _REGISTRY.get(skill_id)
    if s is None:
        raise KeyError(f"未注册的技能: {skill_id}（已注册: {', '.join(_REGISTRY) or '无'}）")
    return s


def register(spec: SkillSpec) -> SkillSpec:
    _REGISTRY[spec.skill_id] = spec
    return spec


_loaded = False


def _autoload() -> None:
    """导入即注册全部技能（q_extract 等模块在导入时自注册）。"""
    global _loaded
    if not _loaded:
        _loaded = True
        from . import q_extract  # noqa: F401  注册 q-extract（运行期模板选择）


# ---------- 统一入口 ----------
def run(skill_id: str, inputs: dict, *, tcfg, max_tokens: int = 0,
        temperature: float | None = None, model: str | None = None,
        base_url: str | None = None, api_key: str | None = None,
        llm: Callable | None = None) -> SkillResult:
    """执行技能。任何失败（模板缺失/调用异常/解析校验耗尽重试）归一为
    SkillResult(ok=False)，绝不抛异常到调用方（§三 设计原则 2/3）。
    kind 区分失败类别：config（模板/变量）| llm（调用失败，error=底层原文）|
    parse（解析校验重试耗尽）；调用方按 kind 恢复迁移前语义。"""
    try:
        spec = get_skill(skill_id)
    except (KeyError, Exception) as e:  # noqa: BLE001
        return SkillResult(ok=False, error=str(getattr(e, "args", [e])[0]) or str(e),
                           kind="config")
    t0 = time.monotonic()
    try:
        base_msgs = build_messages(spec, inputs)
    except KeyError as e:
        metrics.record(skill_id, False, (time.monotonic() - t0) * 1000)
        return SkillResult(ok=False, error=str(e.args[0] if e.args else e), kind="config")

    mt = max_tokens or spec.default_max_tokens
    temp = temperature if temperature is not None else spec.default_temperature
    parser = spec.parser or parse_json_output
    validator = spec.validator or (schema_validator(spec) if spec.schema else None)

    def _call(messages: list[dict]) -> str:
        if llm is not None:
            return llm(messages, tcfg, max_tokens=mt, temperature=temp,
                       model=model, base_url=base_url, api_key=api_key)
        from .. import llm as _llm   # 延迟导入：selftest/单测注入 mock 即可离线
        return _llm.call_llm(messages, tcfg, max_tokens=mt, temperature=temp,
                             model=model, base_url=base_url, api_key=api_key)

    last_err = ""
    raw = ""
    for attempt in range(max(1, spec.attempts)):
        messages = list(base_msgs)
        if attempt > 0:
            messages.append({"role": "user", "content": spec.retry_msg.format(err=last_err)})
        try:
            raw = _call(messages)
        except Exception as e:  # noqa: BLE001  调用失败（网络/鉴权…）不在此重试：
            # LLM 出口自有重试/降级纪律（llm.py），这里直接归一失败，交调用方处理。
            # exc 携带原始异常：调用方 re-raise 可恢复迁移前逐字节相同的抛错语义。
            metrics.record(skill_id, False, (time.monotonic() - t0) * 1000)
            return SkillResult(ok=False, error=str(e)[:300], raw=raw, kind="llm", exc=e)
        payload, perr = parser(raw)
        if perr is not None:
            last_err = perr
            continue
        try:
            data = validator(payload) if validator is not None else payload
        except ValueError as e:
            last_err = str(e)
            continue
        metrics.record(skill_id, True, (time.monotonic() - t0) * 1000)
        return SkillResult(ok=True, data=data, raw=raw)
    metrics.record(skill_id, False, (time.monotonic() - t0) * 1000)
    return SkillResult(ok=False, error=last_err, raw=raw[:500], kind="parse")


def prom_lines() -> list[str]:
    """gk_skill_calls_total / gk_skill_latency_ms（供 metrics.prometheus_text 追加）。"""
    snap = metrics.snapshot()
    lines = [
        "# HELP gk_skill_calls_total AI 技能调用计数（按技能/结果）",
        "# TYPE gk_skill_calls_total counter",
    ]
    for key, v in sorted(snap["calls"].items()):
        skill, result = key.split(":", 1)
        lines.append(f'gk_skill_calls_total{{skill="{skill}",result="{result}"}} {v}')
    lines += [
        "# HELP gk_skill_latency_ms AI 技能平均耗时ms",
        "# TYPE gk_skill_latency_ms gauge",
    ]
    for k, v in sorted(snap["latency"].items()):
        lines.append(f'gk_skill_latency_ms{{skill="{k}"}} {v["avg_ms"]}')
    return lines
