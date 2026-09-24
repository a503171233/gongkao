# 对外 API 对接文档（v1）

> 面向第三方系统 / 外部脚本 / 无平台账号的对接方。
> 本文档聚合平台当前对外开放的全部 v1 接口，含鉴权、限流、统一信封、错误码与完整示例。
> 最后更新：2026-09-08

---

## 0. 总览

| 分组 | 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|---|
| 文档管理 | GET | `/api/v1/documents/health` | 能力探测（公开） | 无 |
| 文档管理 | POST | `/api/v1/documents/upload` | 上传文档入库 | `X-API-Key` |
| 知识体系 | GET | `/api/v1/knowledge/health` | 能力探测（公开） | 无 |
| 知识体系 | GET | `/api/v1/knowledge/teachers` | 可用老师列表 | `X-API-Key` |
| 知识体系 | POST | `/api/v1/knowledge/upload` | Markdown 上传建知识树 | `X-API-Key` |

**Base URL**：`http://<host>/`（对外经 nginx `/api/` 反代，故完整路径带 `/api` 前缀；后端内部路由为 `/v1/*`）。

---

## 1. 鉴权（X-API-Key）

- 所有需鉴权的端点，在请求头携带 `X-API-Key: <OPENAPI_KEY>`。
- 密钥由服务端 `.env` 的 `OPENAPI_KEY` 配置（32 位随机，前缀 `gk_open_`）。
- **未配置密钥时**，所有需鉴权端点返回 `503`（明确「未启用」，而非开放裸奔）。
- 密钥比对采用 `hmac.compare_digest` 常量时间比较，防时序攻击。
- 密钥错误/缺失 → `401`。

> 获取密钥：联系平台管理员索取 `OPENAPI_KEY`（与内部 `UPLOAD_SECRET` 独立，互不影响）。

---

## 2. 限流

| 分组 | 限额 | 超限响应 |
|---|---|---|
| documents | 10 次 / 分钟 / IP | `429` + `Retry-After: <秒>` |
| knowledge | 6 次 / 分钟 / IP | `429` + `Retry-After: <秒>` |

- 内存滑动窗口实现，服务重启后清零。
- 客户端 IP 取 `X-Forwarded-For` 首段（nginx 反代场景），无则取直连 IP。

---

## 3. 统一响应信封

所有端点统一返回：

```json
{"code": 0, "message": "ok", "data": { ... }}
```

- `code == 0` 成功；`code != 0` 失败，`code` 即 HTTP 状态码。
- `message` 人类可读说明；`data` 业务数据（部分错误场景携带结构化纠错信息，如可用老师列表）。

### 错误码一览

| code | 含义 |
|---|---|
| 0 | 成功 |
| 400 | 参数/格式/内容不合法（缺字段、扩展名不符、超节点上限、编码错误等） |
| 401 | 密钥缺失或错误 |
| 404 | teacher_id 不存在或已停用 |
| 413 | 文件超过大小上限 |
| 429 | 请求过于频繁（限流） |
| 503 | 开放 API 未启用（服务端未配置 OPENAPI_KEY） |

---

## 4. 文档管理 API

### 4.1 能力探测

```
GET /api/v1/documents/health
```

无需鉴权。返回平台文档上传能力与限制：

```json
{
  "enabled": true,
  "formats": [".md", ".txt", ".docx", ".pptx", ".pdf", ".xlsx"],
  "max_mb": 50,
  "rate_limit": "10/min per IP",
  "auth": "X-API-Key header"
}
```

- `enabled=false` 表示服务端尚未配置密钥，需鉴权端点均不可用。

### 4.2 上传文档入库

```
POST /api/v1/documents/upload
Content-Type: multipart/form-data
X-API-Key: <OPENAPI_KEY>
```

**表单字段**：

| 字段 | 必填 | 说明 |
|---|---|---|
| `file` | ✅ | 文件本体；白名单 `.md/.txt/.docx/.pptx/.pdf/.xlsx`；≤50MB；文件名自动清洗防路径穿越 |
| `teacher_id` | ✅ | 目标老师 ID（须为已启用老师，否则 404），如 `T001` |
| `category` | ❌ | 文档分类，默认 `API上传`；写入 docreg 登记 |
| `tags` | ❌ | 逗号分隔标签（仅新登记文档生效；重传幂等保留人工标签） |

**成功响应**（`code=0`）：

```json
{"code": 0, "message": "ok", "data": {
  "doc_name": "讲义.md", "teacher_id": "T001", "category": "API上传",
  "chunks": 12, "stored": 12, "total": 345}}
```

- `chunks` 切块数、`stored` 实际入库块数、`total` 原文分段总数。

**示例**：

```bash
curl -X POST http://<host>/api/v1/documents/upload \
  -H "X-API-Key: gk_open_xxxx" \
  -F "file=@讲义.md" \
  -F "teacher_id=T001" \
  -F "category=API上传" \
  -F "tags=申论,讲义"
```

---

## 5. 知识体系 API

### 5.1 能力探测

```
GET /api/v1/knowledge/health
```

无需鉴权。返回知识上传能力与限制：

```json
{
  "enabled": true,
  "formats": [".md", ".txt"],
  "max_mb": 5,
  "rate_limit": "6/min per IP",
  "limits": "300 节点/次，层级≤6",
  "auth": "X-API-Key header"
}
```

### 5.2 可用老师列表

```
GET /api/v1/knowledge/teachers
X-API-Key: <OPENAPI_KEY>
```

返回当前启用老师列表（供上传方自查 `teacher_id`；限流同 upload，6 次/分钟）：

```json
{"code": 0, "message": "ok", "data": {
  "teachers": [
    {"teacher_id": "T001", "name": "星辰老师"},
    {"teacher_id": "T003", "name": "谭老师"}
  ],
  "total": 2}}
```

### 5.3 Markdown 上传建知识树

```
POST /api/v1/knowledge/upload
Content-Type: multipart/form-data
X-API-Key: <OPENAPI_KEY>
```

**表单字段**：

| 字段 | 必填 | 说明 |
|---|---|---|
| `file` | ✅ | `.md` 或 `.txt`，UTF-8 编码，≤5MB |
| `teacher_id` | ✅ | 目标老师（已启用）；也兼容放 URL query（Form 优先） |

**Markdown 格式约定**：

- `#` ~ `######` 六级标题映射为树层级；标题下正文合并为节点描述（≤500 字）。
- 层级跳跃（如 `#` 后直接 `###`）自动降级挂载（树不允许跳级孤儿）。
- 无任何标题 → 单节点兜底（name=文件名去扩展名，description=全文截 500）。
- 幂等：同级同名节点跳过不重建（重传同文档安全）。

**限制**：单次 ≤300 节点、树深 ≤6 级、文件 ≤5MB。

**成功响应**（`code=0`）：

```json
{"code": 0, "message": "ok", "data": {
  "doc_name": "申论知识体系.md", "teacher_id": "T003",
  "nodes_total": 42, "tree_depth": 4,
  "created": 38, "skipped": 4,
  "hint": "已导入 T003 的知识体系树（38 新建 / 4 跳过）；登录管理后台 → 知识管理 → 切换到该老师即可查看"}}
```

**错误纠错**：teacher_id 无效时，`data.available_teachers` 附可用老师列表便于自查：

```json
{"code": 404, "message": "老师不存在或已停用: T999",
 "data": {"available_teachers": [{"teacher_id": "T001", "name": "星辰老师"}]}}
```

**示例**：

```bash
curl -X POST http://<host>/api/v1/knowledge/upload \
  -H "X-API-Key: gk_open_xxxx" \
  -F "file=@申论知识体系.md" \
  -F "teacher_id=T003"
```

---

## 6. 对接建议流程

1. 先 `GET /api/v1/documents/health`（或 `knowledge/health`）探测能力，确认 `enabled=true`；
2. 从管理员处取得 `OPENAPI_KEY`；
3. 首次上传前用 `GET /api/v1/knowledge/teachers` 自查可用 `teacher_id`；
4. 上传失败按错误码自查（400 查格式/编码/上限，404 查 teacher_id，429 等待 `Retry-After`）。

---

## 7. 说明

- 本平台对外开放能力当前限于**文档入库**与**知识体系建树**两类；问答 `/api/ask` 等为核心业务接口，需平台账号登录态，不在本开放 API 范围内。
- 各端点实现细节与内部逻辑参见 `backend/poc/api.py`（`Open API v1` 分节注释）与 `docs/后台模块拓展指南.md` 第二部分。
