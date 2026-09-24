# -*- coding: utf-8 -*-
"""LLM 中继端点调用（B7）：OpenAI 兼容客户端封装 + 超时 + 重试 + 主模型降级。

稳定契约（与 answer.py 薄委托对齐，签名不得变）：
    call_llm(messages, tcfg) -> str                          # 非流式，完整回答
    stream_llm(messages, tcfg) -> 生成器 {type:"delta", data:...}

重试策略（B7 说明书 §4.4 + 派单硬性约束）：
    - 网络类/超时（APIConnectionError，含 APITimeoutError 子类）与 5xx 最多重试
      LLM_MAX_RETRIES 次（默认 2 次）；
    - 4xx（含 429 限流）、鉴权失败、参数错误一律不重试，直接抛错上报；
    - 重试退避：线性 0.5s * attempt，上限 2s。

降级策略：
    - 主模型重试耗尽后，按 FALLBACK_MODELS 列表依次尝试备用模型（每个仅 1 次，不重试）；
    - 仅降级一次（主 → 备1 → 备2 … 有限列表），全部失败抛最后一个错误；
    - FALLBACK_MODELS 默认空：需实测可用后再启用（见 config.py 注释），未实测前不兜底。

流式纪律（B7 说明书 §7 坑「流式重试会产生重复 delta」）：
    - 重试/降级只发生在「尚未产出任何 delta 前」；一旦产出过内容再断流，
      不重试不降级，直接抛错 → 由 api.py event_stream 转 SSE error 事件（协议已对齐）；
    - 单次流式调用用 try/finally 关闭底层连接，不悬挂。

密钥/端点/模型名一律从 config（store._GLOBAL = Config 实例）读取，禁止硬编码。
SDK 自带重试关闭（max_retries=0），统一由本模块控制次数，避免双重叠加。
"""
import time

from openai import OpenAI, APIConnectionError, APIStatusError

from . import store
from .metrics import metrics

# 可重试的 5xx；429 属 4xx，按派单「4xx 不重试」直接抛（RateLimitError 也走 APIStatusError 分支）
_RETRYABLE_5XX = frozenset({500, 502, 503, 504})


def _openai(base_url: str | None = None, api_key: str | None = None):
    """构造 OpenAI 兼容客户端：密钥/端点/超时默认取 config；可被 AI 模型管理页的
    注册模型覆盖（base_url/api_key 显式传入时用模型自身通道），SDK 重试关闭。"""
    g = store._GLOBAL
    return OpenAI(
        api_key=api_key or g.api_key,
        base_url=base_url or g.api_base_url,
        timeout=g.llm_timeout,
        max_retries=0,
    )


def _is_retryable(err: Exception) -> bool:
    """网络类/超时/5xx → 可重试；4xx（含429）、鉴权、参数错误及其他 → 不可重试。"""
    if isinstance(err, APIConnectionError):  # 含 APITimeoutError（其子类）
        return True
    if isinstance(err, APIStatusError):
        return err.status_code in _RETRYABLE_5XX
    return False


def _backoff(attempt: int) -> None:
    """线性退避：0.5s * (attempt+1)，上限 2s。attempt 从 0 开始（首次重试前等 0.5s）。"""
    time.sleep(min(0.5 * (attempt + 1), 2.0))


def _max_retries() -> int:
    return max(0, int(getattr(store._GLOBAL, "llm_max_retries", 2)))


def _fallback_models() -> list[str]:
    return list(getattr(store._GLOBAL, "fallback_models", None) or [])


def _chat_once(messages, model, tcfg, max_tokens: int = 2000,
               temperature: float | None = None,
               base_url: str | None = None, api_key: str | None = None) -> str:
    """单次非流式调用；成功返回文本，失败抛异常（由 call_llm 决定是否重试）。
    成功后采集 usage 上报 metrics（A6 token 核算）。
    max_tokens/temperature 可调：问答默认取 tcfg；题目抽取等大 JSON 输出场景显式传更大值/温度。
    base_url/api_key 可选：AI 模型管理页注册模型各自的通道配置。"""
    client = _openai(base_url=base_url, api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=tcfg.temperature if temperature is None else temperature,
        max_tokens=max_tokens,
    )
    # A6：token 用量核算（resp.usage 可能为 None——部分代理不返回）
    try:
        usage = getattr(resp, "usage", None)
        if usage is not None:
            metrics.record_tokens(
                getattr(usage, "prompt_tokens", 0),
                getattr(usage, "completion_tokens", 0),
            )
    except Exception:  # noqa: BLE001  上报失败不影响回答
        pass
    return resp.choices[0].message.content or ""


def call_llm(messages, tcfg, max_tokens: int = 2000, model: str | None = None,
             temperature: float | None = None,
             base_url: str | None = None, api_key: str | None = None) -> str:
    """非流式：主模型重试（LLM_MAX_RETRIES 次）→ 备用模型（每个 1 次）→ 全败抛错。
    max_tokens 默认 2000（问答）；抽取等大输出调用方按需传更大值。
    model/temperature 显式给出时覆盖 tcfg 对应值（AI模型管理页默认模型由此接入）；
    base_url/api_key 显式给出时用该模型自身通道（默认模型页配置），否则走全局 .env。"""
    last_err: Exception | None = None
    for attempt in range(_max_retries() + 1):  # 首次尝试 + 重试次数
        try:
            return _chat_once(messages, model or tcfg.llm_model, tcfg, max_tokens,
                              temperature, base_url, api_key)
        except Exception as e:  # noqa: BLE001
            if not _is_retryable(e):
                raise  # 4xx/鉴权/参数错误：直接抛错上报，不重试不降级
            last_err = e
            if attempt >= _max_retries():
                break
            _backoff(attempt)

    # 主模型耗尽 → 降级备用（每个仅 1 次，不重试；全部失败抛最后错误）
    for fb in _fallback_models():
        try:
            return _chat_once(messages, fb, tcfg, max_tokens, temperature, base_url, api_key)
        except Exception as e:  # noqa: BLE001
            last_err = e
    if last_err is not None:
        raise last_err
    raise RuntimeError("LLM 调用失败（无可用模型）")


def _stream_once(messages, model, tcfg, produced: dict,
                 base_url: str | None = None, api_key: str | None = None):
    """单次流式调用：逐 chunk 产 delta；finally 关闭连接（不悬挂）。
    produced["v"] 记录是否已产出过 delta（供重试/降级决策）。
    base_url/api_key：模型管理页注册模型自身通道（问答默认仍走全局）。
    A6 token 采集：不传 stream_options（部分中转站不支持会 400，生产不能冒此险），
    逐 chunk 尽力读取 chunk.usage（部分兼容代理末 chunk 携带）；采不到则记 0，不影响回答。
    """
    client = _openai(base_url=base_url, api_key=api_key)
    stream = None
    usage = None
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=tcfg.temperature,
            max_tokens=2000,
            stream=True,
        )
        for chunk in stream:
            try:
                if getattr(chunk, "usage", None) is not None:
                    usage = chunk.usage
            except Exception:
                pass
            try:
                delta = chunk.choices[0].delta.content
            except Exception:
                delta = None
            if delta:
                produced["v"] = True
                yield {"type": "delta", "data": delta}
    finally:
        if usage is not None:
            try:
                metrics.record_tokens(
                    getattr(usage, "prompt_tokens", 0),
                    getattr(usage, "completion_tokens", 0),
                )
            except Exception:  # noqa: BLE001
                pass
        if stream is not None and hasattr(stream, "close"):
            try:
                stream.close()
            except Exception:
                pass


def stream_llm(messages, tcfg):
    """流式：重试/降级仅发生在未产出前；产出后断流直接抛错（对齐 SSE error 协议）。"""
    produced = {"v": False}
    last_err: Exception | None = None
    for attempt in range(_max_retries() + 1):
        try:
            yield from _stream_once(messages, tcfg.llm_model, tcfg, produced)
            return
        except Exception as e:  # noqa: BLE001
            if produced["v"] or not _is_retryable(e) or attempt >= _max_retries():
                # 已产出 → 不重试（防重复 delta）；不可重试/次数耗尽 → 直接抛
                raise
            last_err = e
            _backoff(attempt)

    for fb in _fallback_models():
        try:
            yield from _stream_once(messages, fb, tcfg, produced)
            return
        except Exception as e:  # noqa: BLE001
            if produced["v"]:
                raise  # 产出后断流不降级
            last_err = e
    if last_err is not None:
        raise last_err
    raise RuntimeError("LLM 流式调用失败（无可用模型）")