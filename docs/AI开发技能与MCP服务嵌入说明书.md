# AI 开发技能（Skills）与 MCP 服务嵌入说明书

> 版本：v2.0 · 2026-09-12（v1.0 初版为只读分析，未改代码；v2.0 合并「最高标准」扩充：S7~S12 技能 / M5~M9 MCP / §五工程决策边界 / 统一路线图四~七期）
> 依据：全量通读工作区（docs/ 11 份文档、backend/poc/ 36 模块、frontend/ 全部页面、tests/ + scripts/、.ai-memory / .workbuddy / docs/project_memory.md 三套记忆）
> 读者：项目统筹、后续接手开发的 AI（Agent）、运维

---

## 一、项目现状一页纸（分析基础）

### 1.1 架构与规模

| 维度 | 事实 |
|---|---|
| 定位 | 多老师垂直大模型公考平台：RAG 问答 + 题库刷题 + 模考组卷 + 论坛/消息/激励 + 运营后台 |
| 形态 | 双站点：`frontend/` 纯静态（nginx :3000 对外）+ `backend/` FastAPI（:9000 内网），Docker Compose 双容器 |
| 后端 | `backend/poc/` 36 个模块；`api.py` 约 180KB / 200+ 路由；SQLite 9 库（auth/chat/pay/study/forum/...）+ Chroma 向量库 |
| LLM | OpenAI 兼容中转站（ai.anyyds.cn/v1，当前仅 `gpt-5.6-luna` 稳定）；本地 fastembed bge-small-zh 做 embedding |
| 前端 | 9 个 HTML 页面 + `js/a1~a5` `js/c4~c9` 模块化原生 JS；`admin.js` 达 311KB，靠 `?v=YYYYMMDDx` 版本号防缓存 |
| 部署 | 腾讯云 YOUR_SERVER_IP（2C/3.6G，Ubuntu 24.04），本地 Windows 11 开发，paramiko SFTP 脚本上传 + `docker compose build && up -d` |
| 测试 | `backend/tests/` run_tests.py 21 套 + pytest 15 + 线上冒烟 38 项；另有 100+ 一次性 deploy/verify/probe 脚本（已归档部分） |
| 记忆 | 三套并行：`.ai-memory/`（日志）、`.workbuddy/memory/`（长期记忆）、`docs/project_memory.md` |

### 1.2 与 AI 开发直接相关的高频场景

1. **改后端 → 回归 → 部署 → 冒烟**：每轮迭代的固定链路，手工步骤 6+ 步，坑位 5 条（见 §五红线）。
2. **课件入库**：`data/raw/` 全是 PDF/xlsx 课件，历史上靠"xlsx 转 md + SFTP + CLI ingest"手工完成。
3. **AI 自动采集调试**：`autocollect.py` 158KB，本项目最复杂模块（爬虫 + Bing 解析 + LLM 提取 + 伪题四层防线），调试有特殊纪律（坑#38：勿触发在线 run 烧 token；登录限流 5 分钟）。
4. **前端页面验证**：9 页面无构建，改完只能靠结构检查脚本 + 线上肉眼验收，缺浏览器自动化。
5. **线上排障**：大量依赖"容器内 python + SQLite 检查 + 日志翻查"，当前全部写成一次性 paramiko 脚本。
6. **任务清单**（`docs/功能完善与新增任务清单.md`）：P0 技术债 #1 是 **git 基线未提交**（09-06 后 8+ 新模块未 commit，本地崩溃即丢）。

---

## 二、总体嵌入策略

三层嵌入，按性价比排序：

```
第 1 层  AGENTS.md 项目规则        ← 零成本，所有 Agent 每次自动加载，解决"坑位遗忘"
第 2 层  Skills（项目技能包）       ← 把"重复工作流"固化成可调用技能，解决"每次重讲一遍"
第 3 层  MCP 服务（能力扩展）       ← 补齐 Agent 触达不到的能力（远程服务器/浏览器/文档解析），解决"做不了"
```

原则：
- **已有能力不重复造**：当前环境已连接 `baizhi-toolkit` 与 `mc-browser` 两个 MCP，覆盖面很广（见 §四），优先把项目工作流"对准"它们。
- **内建 Skill 优先**：`/dev-expert`（编码总控）、`/code-review`（提交前审查）、`/verify`（验证闸门）已内建，项目只需补充"领域特化"技能。
- **只读优先、生产慎动**：MCP 对生产服务器的操作必须走确认制；密钥永不进 git / 技能文件。
- **分档推进**：一~三期 = **稳定交付标准**（默认必做）；四~七期 = **最高标准增量**（质量闸门 / 可观测 / AI 评测），见 §八路线与 §五决策边界。

---

## 三、Skills 技能规划

### 3.1 技能清单（12 个项目技能 + 内建复用；S1~S6 稳定交付档 · S7~S12 最高标准档）

| # | 技能名 | 类型 | 触发场景 | 价值 |
|---|---|---|---|---|
| S1 | `gongkao-release` | 部署发布链 | 改完代码要上线 | ★★★ 把 6 步手工链固化为一步，防 5 大坑 |
| S2 | `gongkao-regression` | 回归测试 | 改了 `poc/` 或前端 | ★★★ 一键跑全量回归并判读 ALL PASS |
| S3 | `gongkao-debug` | 排障手册 | 线上 500/403/黑屏 | ★★★ 把排障笔记变成交互式 checklist |
| S4 | `gongkao-courseware` | 课件入库 | 新课件 PDF/xlsx 需入库 | ★★ 对接 docparse MCP，替代手工转码 |
| S5 | `gongkao-autocollect` | 采集调试 | 采集模块出问题/调优 | ★★ 专属纪律（防烧 token、限流、回放验证） |
| S6 | `gongkao-api-contract` | 新端点检查 | 给 `api.py` 加接口 | ★★ 鉴权守卫/compose 同步/契约三件套 |
| S7 | `gongkao-eval` | AI 质量评测 | 改 prompt/守卫阈值/换模型 | ★★★ 变更有评测数据背书 |
| S8 | `gongkao-security` | 安全审计闸 | 每次发布前 | ★★★ 攻击面正规化扫描 |
| S9 | `gongkao-perf` | 性能与容量 | 大改动后/上线前 | ★★ 容量红线数字化 |
| S10 | `gongkao-e2e` | 端到端测试 | 每次发布前 | ★★★ 前端验收可回归 |
| S11 | `gongkao-incident` | 事故响应 | 告警触发/故障时 | ★★ 监控→闭环标准动作 |
| S12 | `gongkao-data-ops` | 数据迁移与演练 | 改表结构/季度演练 | ★★ 数据库治理正规化 |
| — | `/dev-expert`（内建） | 编码总控 | 日常开发默认入口 | 复用 |
| — | `/code-review`（内建） | 代码审查 | 提交/部署前 | 复用（最高标准：high effort 常态化） |
| — | `/verify`（内建） | 验证闸门 | 发布前综合验证 | 复用（对接 S2；四期后接 S10/S8） |

### 3.2 各技能内容要点

**S1 `gongkao-release`（部署发布链）**
- 步骤：本地改动清单确认 → SFTP 上传（`scripts/deploy.py`，新文件必须同步 FILES 清单）→ 服务器 `docker compose build backend frontend`（**缓存模式**）→ `up -d` → 冒烟（`/api/health`、`/api/metrics`、前端 200）→ git 提交（服务器侧）。
- 内置坑位断言：禁 `--no-cache`；改 `index.html`/`admin.js` 需版本号 `?v=YYYYMMDDx` 递增；nginx Host 必须 `$http_host`。
- 产出：发布记录（时间/文件/冒烟结果），追加到 `.ai-memory/当日/daily.md`。

**S2 `gongkao-regression`（回归测试）**
- 按改动范围自动选档：
  - 改 `poc/` → `python -m compileall -q poc` + `python tests/run_tests.py`（21 套）+ `python -m poc.cli selftest`；
  - 改 `index.html`/`admin.js`/其他页面 → `frontend/check_structure.py`（div 配对/id 唯一/关键锚点存在）；
  - 部署后 → `python tests/online_smoke.py`（38 项）。
- 判读标准：任何 FAIL 先修再上线（对齐 `docs/测试清单.md` §五回归纪律）。
- 注意：本地需 venv（chromadb/openai 已补装）；admin 登录限流 `LOGIN_LIMIT`，多次验证间隔 5 分钟。

**S3 `gongkao-debug`（排障 checklist）**
- 症状索引（源自 `docs/排障笔记.md` + V3.1 §七 + daily.md 坑位）：
  - Chroma 500 `hnsw segment reader` → `docker compose restart backend`（CLI ingest 后必踩）；
  - CSRF 403 → 查 nginx Host 透传；
  - 改动不生效 → 查是否漏 `build`；
  - LLM 503 → 查中转站模型状态（仅 gpt-5.6-luna 稳定）；
  - 前端三页面同页 → 查镜像内 `js/` 与 admin.html 是否缺失。
- 每条含：现象 → 定位命令 → 修复动作 → 验证。

**S4 `gongkao-courseware`（课件入库）**
- 链路：PDF/xlsx → **baizhi docparse MCP 转 Markdown**（≤200 页；扫描版走 image_analysis OCR）→ 人工校对 → `python -m poc.cli ingest` 或管理端上传 → `docker compose restart backend`（CLI 入库必做）→ 黄金集问答验证。
- 取代历史做法："xlsx 转 md + SFTP + CLI ingest"（2026-09-04 T003 入库实录）。

**S5 `gongkao-autocollect`（采集调试）**
- 铁律：不轻易触发在线 `/admin/autocollect/run`（坑#38 烧 token）；用回放验证（`_probe_*` 只读模式）；登录限流间隔 5 分钟。
- 四层防线地图（2026-09-12 部署）：层1 prompt 拒编造 → 层2 `_gongkao_real_question_shape` 正象识别 → 层3 域名黑名单 `_SEARCH_BLOCK_HOSTS` → 层4 质检标准；调试先判"伪题漏进/真题误杀"属哪一层。
- 验证脚本索引：`test_autocollect_offtopic.py`（22 项）/ `verify_ai_collect.py` / `verify_ai_stability.py`。

**S6 `gongkao-api-contract`（新端点三件套）**
- 加接口必查：① 鉴权守卫（学员端 `_user` / 管理端 `_admin_user`，管理端只认 admin Bearer）；② 新环境变量同步 `docker-compose.yml` environment 段（compose 无 env_file）；③ 同步 `docs/specs/02-数据契约.md`（契约唯一真源）+ `docs/对外API对接文档.md`（若属 v1 开放面）。

### 3.3 最高标准档技能（S7~S12）内容要点

**S7 `gongkao-eval`（AI 质量评测）**
- 现状缺口：黄金集仅 1 个文件（T001 转折关系）；guard 阈值 0.28"勿乱改"靠口口相传。
- 动作：每次改 prompt / 守卫阈值 / 换模型，自动跑评测对比——检索命中率 + LLM-as-judge 事实一致性评分 + guard 误杀率，产出回归报告。
- 目标：黄金集扩容 ≥30 题；阈值调整由评测数据背书。

**S8 `gongkao-security`（安全审计闸）**
- 发布前四件套：pip-audit（依赖 CVE）→ bandit/semgrep（SAST）→ gitleaks（密钥扫描）→ trivy（镜像扫描）。
- 执行器用已连接的 huskbox 沙箱隔离运行，不污染开发机。
- 现状证据：文档明文散落 admin/admin123、CHANGE_ME_ADMIN_SECRET、服务器 IP——一轮扫描即全暴露；会员支付 + 论坛 UGC + 上传面需要正规审计。

**S9 `gongkao-perf`（性能与容量）**
- k6/Locust 压 SSE 并发（300s 长连接是容量黑洞）；chromadb + fastembed + uvicorn 在 2C/3.6G 的内存水位基线；SQLite WAL 锁行为。
- 产出容量红线数字（"感觉还行"→ 数字），压测必须在 staging 跑。

**S10 `gongkao-e2e`（端到端测试）**
- Playwright 固化 9 页面关键用户流：注册 → 提问 → 收藏 → 错题 → 模考 → 充值码激活 → 后台 CRUD。
- 进 CI 作为发布门禁；附 axe 无障碍抽查。替代当前"人工截图验收、不可回归"。

**S11 `gongkao-incident`（事故响应）**
- P0/P1 分级标准、回滚手册（配套镜像 tag 化）、复盘模板。
- 补齐"alert.sh 告警响了之后做什么"的标准动作，形成监控 → 响应 → 复盘闭环。

**S12 `gongkao-data-ops`（数据迁移与演练）**
- 9 个 SQLite 库的 schema 变更统一走 `backend/migrations/`（禁止即兴 ALTER）；季度备份恢复演练（任务 #27）；备份校验和验证。

**内建能力升级与配套实践（最高标准下）**
- `/code-review` 以 high effort 常态化：每次提交必过，不再只在大改动时用。
- `/verify` 升级为发布闸门：对接 S10（E2E）与 S8（安全扫描）结果。
- 配套实践：FastAPI 原生 OpenAPI（`/api/docs`）直接暴露接口契约，杜绝"文档与实现漂移"（B3 曾上报契约脱节）。

### 3.4 SKILL.md 落盘规范与示例

项目技能放 `D:\xiangmu\gongkao\.ohmyagent\skills\<技能名>\SKILL.md`（Claude 兼容目录 `.claude\skills\`、Agent 兼容 `.agents\skills\` 会自动同步识别）。示例（S1）：

```markdown
---
name: gongkao-release
description: 公考平台发布链：SFTP 上传 → compose build(缓存模式) → up -d → 冒烟 → 记录。改完代码要上线时使用。
---

# 公考平台发布链

## 前置检查
1. 改动文件清单是否都属于本模块允许范围（docs/specs/01-模块分解.md §4）
2. 前端文件改动是否已递增版本号 ?v=YYYYMMDDx
3. 新增环境变量是否已同步 docker-compose.yml environment 段

## 步骤
1. python scripts/deploy.py   # 上传（新增文件先补 FILES 清单）
2. 服务器: docker compose build backend frontend   # 禁用 --no-cache！
3. 服务器: docker compose up -d backend frontend
4. 冒烟: curl localhost:3000/api/health 含 T001；python tests/online_smoke.py
5. 记录到 .ai-memory/当日/daily.md

## 红线
- 禁 --no-cache（pip 全量重装卡 20+ 分钟）
- CLI ingest 后必须 restart backend
- 密钥只存服务器 .env，永不进 git / 技能文件
```

其余 5 个技能按同构模板编写，内容取 §3.2 要点。

---

## 四、MCP 服务规划

### 4.1 已连接 MCP 的项目化用法（零新增成本）

**A. `baizhi-toolkit`**（当前会话已连接，能力最全）

| 工具组 | 映射到项目场景 | 说明 |
|---|---|---|
| `docparse_parse` / `docparse_get_doc_upload_url` | **课件入库预处理**（S4 技能核心）：`data/raw/T003/*.pdf`、xlsx → Markdown | 单文档 ≤200 页；本地文件先取 upload_url 再传 |
| `image_analysis_*` | 扫描版课件页 OCR；题目截图（`/qimg/` 存图）识别 | 异步任务，配合 docparse 兜底 |
| `websearch_search` / `web_extract` / `web_scrape` | **采集源调研**：验证 Bing 解析结果、探测新题库站点结构、调试 `_SEARCH_BLOCK_HOSTS` | 替代手写 `_probe_3sites.py` 类一次性脚本 |
| `huskbox_create_sandbox` / `huskbox_execute_task` | **隔离实验**：跑不可信爬虫/解析实验、批量文档转换，不污染本机与生产 | 镜像仅限 Docker Hub；结果经 workspace 导出 |
| `websearch_newssearch` / `aisearch` | 公考行业资讯（articles 模块内容源） | 按需 |
| `get_cutout_detail` / `imgsearch_search` | 首页 Banner 图素材处理 | 按需（Banner 管理 #23 已上线） |

**B. `mc-browser`**（真实浏览器，共享用户登录态）

| 场景 | 说明 |
|---|---|
| 前端三入口/九页面验收 | `browser_navigate` → `browser_snapshot`/`browser_take_screenshot` 逐页检查 index/admin/dashboard/forum/knowledge/articles/chat |
| 管理后台端到端 | admin 登录 → 老师管理/题库/采集页 tab 操作流实测（当前只能靠 curl + 肉眼） |
| 采集目标页 DOM 探测 | 采集站反爬结构观察，辅助写解析正则（比纯 httpx 抓 HTML 更直观） |
| 论坛/分享页 UGC 冒烟 | 登录态下发帖/点赞/审核流 |

### 4.2 建议补充的 MCP

**稳定交付档（M1~M4）**

| # | MCP | 解决什么 | 现状痛点证据 | 安全边界 |
|---|---|---|---|---|
| M1 | **SSH 远程执行 MCP**（如 mcp-ssh / openssh-mcp，指向 ubuntu@YOUR_SERVER_IP） | 部署、容器日志、`docker compose exec` 调试、服务器巡检 | `tests/deploy_*.py` paramiko 一次性脚本 20+ 个；每轮迭代重写；容器内调试靠 `docker cp`+临时脚本 | **白名单命令制**：只放行 compose/logs/curl/git 类；禁 rm/重启类直执；生产变更需人工确认 |
| M2 | **SQLite MCP（只读）** | 查 auth/study/forum/pay 等 9 库数据，验证功能与排查脏数据 | 现在靠"容器内 python -c"或下载 db；`.workbuddy` 记忆里大量"py_in + base64 管道"黑魔法 | **只读挂载**；线上库先 `scp` 副本到本地再查，或经 SSH MCP 执行 sqlite3 只读查询 |
| M3 | **Git MCP / 本地 git 工作流** | 任务清单 P0 #1：09-06 后 8+ 新模块未提交基线 | 本地目录当前非 git 仓库（备份裸仓库 D:\xiangmu\gongkao-backup.git 长期未同步） | 先 `git init` + 基线 commit + 关联 backup remote；这是纯命令行即可完成的事，MCP 可选 |
| M4 | （可选）Playwright/浏览器 MCP 独立实例 | 若 mc-browser 依赖用户浏览器不便无人值守跑批 | 前端验证需人工在场 | 指向测试环境，勿对生产 |

**最高标准档（M5~M9）**

| # | MCP | 用途 | 资源影响 |
|---|---|---|---|
| M5 | **代码托管 + CI**（Gitee Go / GitHub Actions；配套 Gitee 社区 MCP 或 GitHub 官方 MCP） | PR 流、分支保护、CI 门禁；Agent 经 MCP 查 CI 状态 / 建 PR / 读 issue。已有 Gitee OAuth → 托管 Gitee 最顺 | CI 跑云端免费 runner，服务器零负担 |
| M6 | **Sentry MCP**（官方） | FastAPI 接 SDK 后，Agent 经 MCP 直接查最近异常、按 release 定位，替代翻 docker logs | SaaS 免费档，服务器仅加一个 SDK |
| M7 | **Playwright MCP**（官方） | 无人值守浏览器自动化：驱动 S10 E2E 与采集站 DOM 探测；与 mc-browser 互补（后者依赖用户浏览器在场） | 本地/CI 运行，不占服务器 |
| M8 | **Grafana MCP** + Grafana Cloud 免费档 | `gk_*` Prometheus 指标早已输出但 `prometheus.yml` 闲置；remote-write 上云后 Agent 经 MCP 查看板/告警，SLO 从 JSON 接口变成真监控 | 采集端内存 <100MB |
| M9 | **Langfuse**（可选，P2） | AI 全链路 tracing：prompt / 检索 / 守卫各环节延迟与 token、评测数据集管理；autocollect 烧 token 的可观测性受益 | 云免费档；自托管太重不建议 |

> 已连接的 huskbox 沙箱在最高标准下的角色：**S8 的扫描执行器**——pip-audit/bandit/trivy 均在隔离沙箱运行，比再引一个安全类 MCP 更干净。

> 不建议引入：数据库写类 MCP（SQLite 写操作危险且项目以代码管数据结构）、Docker MCP（2C 服务器经 SSH 已足够）、通用文件系统 MCP（Agent 已内置读写）；Sentry / Grafana / Langfuse 均走云免费档，不做自托管（2C 服务器扛不住）。

---

## 五、工程决策边界（最高标准的前置共识）

以下属于工程决策而非"装个 MCP"能解决的，必须先拍板；相关工具是为其服务的：

| 事项 | 现状 | 最高标准动作 | 关联工具 |
|---|---|---|---|
| staging 环境 | online_smoke 等测试直打生产 YOUR_SERVER_IP | compose 起第二套环境（同机或低成本实例），压测/E2E/冒烟先打 staging | S9/S10、M7 |
| HTTPS | 等域名（任务 #26，受阻） | 域名就绪后 `scripts/setup-https.sh` 一键签发（脚本包已备） | 既有脚本包 |
| Redis 化 | 限流/缓存为单实例内存窗口 | 多副本部署前必须换 Redis（代码已留口，任务 #29） | — |
| 镜像版本化 | build 即 latest，回滚靠改代码重发 | 镜像 tag 化（git sha / 语义版本），回滚 = 换 tag 重启 | S11 |
| 契约漂移 | 对外接口文档手工维护（B3 曾上报契约脱节） | 启用 FastAPI 原生 OpenAPI（`/api/docs`）作为契约源，手工文档只留对接指引 | api.py 现成能力 |

---

## 六、AGENTS.md 项目规则（第 1 层嵌入，最优先）

位置：`D:\xiangmu\gongkao\.ohmyagent\AGENTS.md`（本环境约定路径，不落根目录 `AGENTS.md`）。内容直接沉淀既有纪律，所有后续 Agent 自动生效：

```markdown
# 公考平台 · 项目纪律（Agent 必读）

## 红线（违反即事故）
1. 改 api.py / index.html / admin.js 后必须 docker compose build（缓存模式）再 up -d；禁用 --no-cache。
2. CLI/脚本入库后必须 docker compose restart backend（chromadb 常驻进程不同步会 500）。
3. auth/chat 等 9 个 SQLite 库必须落 /data 挂载卷。
4. .env 新增变量必须同步 docker-compose.yml environment 段（compose 无 env_file）。
5. nginx 反代 Host 用 $http_host；/api/ 前缀被剥，后端路由不含 /api。
6. 契约唯一真源 docs/specs/02-数据契约.md；禁擅自引新重依赖（前端 Vue/React、后端 ORM 等）。
7. 守卫顺序：先幻觉守卫后缓存；多老师 collection 隔离不可破。
8. 密钥只存服务器 .env / 本地 .env（已 gitignore），永不写入代码、文档、技能、记忆。

## 固定工作流
- 改后端 → compileall + tests/run_tests.py + cli selftest（全 ALL PASS 才算完）
- 改前端 → check_structure.py + admin.js 版本号递增
- 上线 → scripts/deploy.py → build+up -d → online_smoke 38 项
- 采集调试 → 不触发在线 run（烧 token）；回放验证；登录限流间隔 5 分钟

## 环境速查
- 线上：ubuntu@YOUR_SERVER_IP:/home/ubuntu/gongkao（双容器）；主站 :3000
- LLM：中转站仅 gpt-5.6-luna 稳定；FALLBACK_MODELS 实测后再启用
- 任务台账：docs/功能完善与新增任务清单.md；交付记录：docs/specs/modules/*.md §8
```

---

## 七、嵌入后的目录布局（目标态）

```
D:\xiangmu\gongkao\
├─ .ohmyagent\
│  ├─ AGENTS.md                      ← 第 1 层：项目纪律（§六模板）
│  └─ skills\
│     ├─ gongkao-release\SKILL.md    ← S1
│     ├─ gongkao-regression\SKILL.md ← S2
│     ├─ gongkao-debug\SKILL.md      ← S3
│     ├─ gongkao-courseware\SKILL.md ← S4
│     ├─ gongkao-autocollect\SKILL.md← S5
│     ├─ gongkao-api-contract\SKILL.md←S6
│     ├─ gongkao-eval\SKILL.md       ← S7（四~七期）
│     ├─ gongkao-security\SKILL.md   ← S8
│     ├─ gongkao-perf\SKILL.md       ← S9
│     ├─ gongkao-e2e\SKILL.md        ← S10
│     ├─ gongkao-incident\SKILL.md   ← S11
│     └─ gongkao-data-ops\SKILL.md   ← S12
├─ backend\migrations\               ← S12：9 库 schema 变更唯一入口
├─ docs\AI开发技能与MCP服务嵌入说明书.md   ← 本文档
└─ （.claude\skills\ / .agents\skills\ 由宿主自动同步，无需手工维护两份）
```

MCP 配置在宿主（ohmyagent）设置中注册，项目不落密钥文件；SSH MCP 的私钥走系统凭据管理，不进工作区。

---

## 八、分阶段落地路线（标准版一~三期 + 最高标准四~七期）

| 阶段 | 动作 | 依赖 | 预期收益 |
|---|---|---|---|
| **一期（立即）** | ① 落 `AGENTS.md`（§六）；② 编写 S1/S2 两个技能（内容现成，纯搬运 docs/ 既有手册） | 无 | 消除"坑位遗忘"类事故；回归/发布链从 30 分钟手工变一键 |
| **二期（本周）** | ③ S3~S6 技能；④ git init + 基线提交（任务 #1，P0 风险项） | 一期 | 一次性脚本知识固化；代码资产安全 |
| **三期（按需）** | ⑤ 引入 SSH MCP（M1）+ SQLite 只读 MCP（M2）；⑥ S4 对接 baizhi docparse 跑通一篇 PDF 课件端到端入库 | 三期前完成 P0 #1 | 告别一次性 paramiko 脚本；T002 课件入库（任务 #30）打通 |
| **四期（最高标准 · 解锁一切）** | ⑦ git 托管 + CI（M5）+ 分支保护；⑧ S10 E2E 与 S8 安全扫描进流水线门禁 | 三期 | 手工作业 → 流水线；"绿灯才可合并"成为唯一门禁 |
| **五期（可观测 / 可回滚）** | ⑨ Sentry（M6）+ 镜像 tag 化（§五决策边界）+ S11 回滚手册 | 四期 | 出事可知（异常聚合）、可退（换 tag 回滚） |
| **六期（容量与演练）** | ⑩ Grafana 云端（M8）+ staging 环境（§五决策边界）+ S9 压测基线 + S12 备份恢复演练 | 四期 | 容量红线有数字；备份可信；生产不再被测试直打 |
| **七期（AI 质量长期建设）** | ⑪ S7 评测体系（黄金集扩容 ≥30 题）+ M9 Langfuse | 随 AI 功能演进 | prompt / 阈值 / 模型变更有评测数据背书 |
| **持续** | 每轮迭代结束：交付记录写入 specs 模块 §8、坑位回写 daily.md，由统筹择要升级进 AGENTS.md | — | 记忆体系从"三套并行"收敛为"规则+日志"两层 |

## 九、验收标准（嵌入是否成功）

- [ ] 新会话 Agent 无需人工提示即知 8 条红线（AGENTS.md 生效）
- [ ] `gongkao-release` 从改码到冒烟无人值守跑通一次真实发布
- [ ] `gongkao-regression` 能按改动范围自动选档并输出 ALL PASS 判定
- [ ] mc-browser 完成 admin.html 四 tab 全操作流截图验收 1 次
- [ ] baizhi docparse 完成 1 份 PDF 课件 → Markdown → ingest → 黄金集问答全链路
- [ ] （三期后）SSH MCP 白名单外命令被拒绝且留痕
- [ ] （四期后）CI 绿灯为合并唯一门禁，S8 四件套扫描零高危
- [ ] （五期后）Sentry 收敛首个生产异常并可按 release 过滤；回滚演练 = 换 tag 一次成功
- [ ] （六期后）压测报告给出 SSE 并发容量红线数字；一次完整备份恢复演练（RTO/RPO 实测）
- [ ] （七期后）黄金集评测 ≥30 题；prompt/守卫阈值/模型变更附评测对比报告

---

## 附：本轮分析中确认的事实边界

1. 本地工作区当前**不是 git 仓库**（环境探测 `Is a git repository: false`），与记忆中"git 已备份"不一致——印证任务清单 #1 的基线风险，属嵌入工作最高优先级。
2. `mc-browser` 与 `baizhi-toolkit` 已在本环境连接可用，无需重复注册。
3. 全文未修改任何业务代码；本文档为唯一新增文件。
4. 版本历史：v1.0（2026-09-12 初版分析）；v2.0（同日合并「最高标准」扩充——S7~S12 / M5~M9 / §五工程决策边界 / 路线图扩展为七期）。
