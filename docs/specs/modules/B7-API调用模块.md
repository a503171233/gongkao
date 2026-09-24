# B7 · API 调用模块（网站二 内核）

## 1. 目标

封装 OpenAI 兼容 LLM 客户端：非流式与流式调用、参数透传（model/temperature/max_tokens）、异常处理，**新增重试与降级能力**（模型 503 时自动换备用模型）。

## 2. 现状

`backend/poc/answer.py`：`_openai()`、`_call_llm()`、`_stream_llm()`。当前无重试降级（遗留项）。

## 3. 函数契约

| 函数 | 返回 |
|---|---|
| `_call_llm(messages, tcfg)` | `str`（完整回答） |
| `_stream_llm(messages, tcfg)` | 生成器 `{type:"delta", data:...}` |

参数来自 `tcfg`（`llm_model`/`temperature`），固定 `max_tokens=2000`。

## 4. 功能点清单（含新增）

1. 客户端：`OpenAI(api_key, base_url)`（读 `store._GLOBAL`）。
2. 非流式：`chat.completions.create`，返回 `choices[0].message.content or ""`。
3. 流式：`stream=True`，逐 chunk 取 `delta.content`，非空才 yield。
4. **重试降级（新增）**：对 503/超时等可重试错误，最多重试 N 次；重试仍失败时降级到备用模型（备用模型列表可在 `config.py` 声明，如 `FALLBACK_MODELS`），全部失败才抛错。
5. 异常转义：把底层异常转为可控错误信息，回撩给 B4 统一处理。

## 5. 允许修改文件

- `backend/poc/answer.py` 中 `_openai/_call_llm/_stream_llm`
- 可抽到 `backend/poc/llm.py`（需同步 B4 引用）

## 6. 验收标准

- [ ] 非流式返回完整文本，空内容兜底为 `""`
- [ ] 流式逐 delta 输出，无异常时顺序正确
- [ ] 模拟 503 时能自动重试并降级，最终可用或明确抛错
- [ ] 不影响 B4 缓存/守卫时序

## 7. 坑与注意事项

- 中转站 21 模型仅 `gpt-5.6-luna` 稳定，降级模型需实测可用再写入，不要拍脑袋填。
- 流式重试会导致重复 delta，需在 B4 层保证「重试只发生在未产出内容前」或丢弃脏前缀。
- 重试不能绕过 B8 守卫与缓存纪律（仍先守卫后缓存）。