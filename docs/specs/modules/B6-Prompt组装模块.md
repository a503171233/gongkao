# B6 · Prompt 组装模块（网站二 内核）

## 1. 目标

把检索上下文 + 教师人设模板 + 会话历史 + 当前问题组装为 LLM `messages`，并做历史注入安全过滤。

## 2. 现状

`backend/poc/answer.py`：`build_context()`、`build_messages()`。

## 3. 函数契约

| 函数 | 返回 | 说明 |
|---|---|---|
| `build_context(hits)` | `str` | 片段格式化 `[片段i｜来源:doc]\ncontent` |
| `build_messages(tcfg, hits, query, history=None)` | `list[dict]` | `[{system}, ...history, {user:query}]` |

## 4. 功能点清单

1. context 组装：去重来源文档名，取首个作为 `doc_hint`；按命中顺序编号片段。
2. system：用 B5 模板 `format` 注入人设/科目/拒答/context/来源。
3. 历史注入：仅保留 `role in (user, assistant)` 且非空的消息，**排除 system，防提示注入**。
4. 当前问题：追加为最后一条 user 消息。

## 5. 允许修改文件

- `backend/poc/answer.py` 中 `build_context`/`build_messages`

## 6. 验收标准

- [ ] context 片段编号与来源正确
- [ ] `history` 中非 user/assistant 消息被剔除
- [ ] messages 首位是 system，末位是当前 user 问题
- [ ] 无 history 时也能正确组装

## 7. 坑与注意事项

- 历史消息内容来自 DB，必须过滤 role 和空内容，防止恶意注入伪造 system。
- `doc_hint` 在无来源时回退为「讲义」。
- 当前问题（`query`）始终追加为末位 user 消息，不得拼入 system 内容。

---

## 8. 交付记录（B6 独立开发者）

> 交付方式：B6 独立模块开发者，只改 `backend/poc/answer.py` 中 `build_context`/`build_messages` 两个函数。
> 交付物：`backend/poc/answer.py`（仅该两个函数内部实现，未改函数签名、未改 `SYSTEM_TEMPLATE`/`REJECT_TEXT` 常量）。

### 8.1 改动摘要

| 函数 | 改动 | 说明 |
|---|---|---|
| `build_context`（39-44 行） | `h['content']` → `h.get('content', '')` | 防御性改进：hits 条目缺 content 时不抛 KeyError，返回空字串 |
| `build_messages`（47-66 行） | `doc_hint` 从 `sorted({...})` 字典序改为按命中顺序去重取首个 | 任务书 §4.1 要求「来源文档名去重取首个」，按 hits 顺序找第一个有 doc_name 的片段，无来源回退「讲义」 |

**未动**：`SYSTEM_TEMPLATE`、`REJECT_TEXT`、所有函数签名、其他模块文件。

### 8.2 占位符契约核对

`build_messages` 传入 `SYSTEM_TEMPLATE.format()` 的实参：

| 占位符 | 实参来源 | 状态 |
|---|---|---|
| `{teacher_name}` | `tcfg.teacher_name` | ✅ |
| `{teacher_subject}` | `tcfg.teacher_subject` | ✅ |
| `{doc_hint}` | 按命中顺序取首个 doc_name / "讲义" | ✅ |
| `{reject}` | `REJECT_TEXT` 常量 | ✅ |
| `{context}` | `build_context(hits)` 返回值 | ✅ |

占位符与 `B5-提示词模板模块.md` 及 `build_messages` 实传入参**完全一致**，无缺失、无多余。

### 8.3 核心能力实现

1. **context 组装**：`[片段{i}｜来源:{doc}]\n{content}`，按 hits 命中顺序编号，来源取 `meta.doc_name`，无来源回退「讲义」
2. **system 构造**：调 B5 模板 `SYSTEM_TEMPLATE.format()` 注入人设/科目/拒答/context/来源
3. **历史注入过滤**：仅保留 `role in (user, assistant)` 且 `content` 非空的消息，**system 消息一律剔除**，防提示注入
4. **当前问题**：追加为末位 `{"role": "user", "content": query}`

### 8.4 自测证据

**Ad-hoc 验证**（20 项 checks，`hermes-verify-b6.py`，跑完已清理）：

```
PASS 1a. context 含 2 个片段
PASS 1b. 片段1 来源 B讲义
PASS 1c. 片段2 来源 A讲义
PASS 1d. 片段顺序按 hits 顺序
PASS 1e. 无来源回退 讲义
PASS 2a. 防注入: 无额外 system
PASS 2b. 防注入: 空 content 被过滤
PASS 2c. 防注入: 只保留 user/assistant
PASS 2d. 防注入: 正确保留 2 条历史（不含当前问题）
PASS 3a. 首位 system
PASS 3b. 末位 当前问题
PASS 3c. system 不含当前问题
PASS 4a. history=None 不抛错
PASS 4b. 首位 system
PASS 4c. 末位 当前问题
PASS 4d. None 与 [] 等价
PASS 5a. 正常 hits 无 KeyError
PASS 5b. doc_hint 按命中顺序取首个
PASS 5c. 无来源 hits 不缺 doc_hint
PASS 5d. 混合来源: doc_hint 取首个有来源的
RESULT: ALL PASS
```

**全量回归**（改 `poc/` 必跑）：

| 套件 | 结果 |
|---|---|
| `test_answer_history.py`（B6 专属回归，12 项） | ✅ ALL PASS |
| `test_chat.py` / `test_cache.py` / `test_guard.py` | ✅ ALL PASS |
| `test_auth.py` / `test_api_auth.py` / `test_api_sessions.py` | ✅ ALL PASS |
| `test_metrics.py` / `test_gitee.py` / `test_api_gitee.py` | ✅ ALL PASS |
| `python -m poc.cli selftest`（22 项） | ✅ ALL PASS |
| compileall 语法闸门 | ✅ OK |

**11/11 套件 + selftest 全绿，零回归。**

### 8.5 上报事项

**无。** B5 模板占位符与 `build_messages` 实参完全一致，无缺失、无多余；`02-数据契约.md` 中无额外约束需要处理。

### 8.6 遗留/后续

- 本模块仅处理 `build_context`/`build_messages` 的组装逻辑，不涉及 `HISTORY_LIMIT=10` 轮上限（`api.py` 职责）及 `ask()` 中的缓存键历史哈希计算
- 汇入批次后建议线上部署验证（`docker compose build backend && up -d backend`，缓存模式）