# 多老师专属垂直大模型平台

面向**公考言语理解教学**的 RAG 问答平台：多位老师（星辰老师 T001 / 云舟老师 T002）各自挂载私有课件知识库，回答**严格基于已入库资料**（幻觉检测三道关卡兜底），零训练、零微调。当前里程碑 **M2.5 已全部交付并线上运行**。

**部署地址**：`http://<服务器IP>:3000`（支持密码注册/登录 + Gitee OAuth 登录）

---

## 📚 文档导航（后续开发必读）

| 文档 | 内容 |
|---|---|
| [`docs/项目状态总览.md`](docs/项目状态总览.md) | 当前进度 / 线上部署信息 / 已实现功能清单（含接口契约）/ 关键设计决策与坑 / 遗留事项 |
| [`docs/开发操作手册.md`](docs/开发操作手册.md) | 代码同步流程 / 环境变量表 / 日常运维命令 / 接口速查 / 多老师扩展 / 本地调试 |
| [`docs/测试清单.md`](docs/测试清单.md) | 11 套测试 + 前端结构检查 + 线上冒烟 + 回归纪律（改码必跑） |
| [`docs/排障笔记.md`](docs/排障笔记.md) | 14+ 条真实踩坑记录（部署/模型/数据/Gitee/前端/守卫/监控） |
| [`docs/specs/README.md`](docs/specs/README.md) | **模块化开发说明书体系**：总设计/模块分解/数据契约 + 18 个模块说明书（A1-A5/B1-B8/C1-C5），供分派独立开发与统筹验收 |
| [`Gitee登录配置指引.md`](Gitee登录配置指引.md) | Gitee OAuth 申请与启用指引（已配置上线） |
| [`多老师专属垂直大模型平台开发说明书_V3.0.docx`](多老师专属垂直大模型平台开发说明书_V3.0.docx) | 产品方案书（V3.0，含技术选型/幻觉防线/里程碑） |

> ⚠️ 说明书 docx 尚未同步 M2.5 新功能，以 `docs/项目状态总览.md` 为当前事实源。

## 架构一览

```text
用户浏览器 ──▶ frontend (nginx :3000) ──/api/──▶ backend (FastAPI :9000 内网)
                                                    │
                    ┌───────┬───────┬────────┬──────┴───────┐
                    │  RAG  │ 会话  │ 用户   │  Gitee OAuth │
                    │ 检索+ │ SQLite│ 额度   │  第三方登录   │
                    │ 幻觉  │ 持久化│ 鉴权   │              │
                    │ 守卫  │       │        │              │
                    └──┬────┴───┬───┴───┬────┴───────┬──────┘
                       └────────┴───────┴────────────┘
                              data/ 挂载卷（auth.db/chat.db/vector/models）
```

## 快速开始（本地开发）

```powershell
cd D:\xiangmu\gongkao\backend
Copy-Item .env.example .env          # 填 API_KEY（中转站）
python -m poc.cli ingest "..\data\raw\T001\言语理解-转折关系.md"   # 课件入库
python -m poc.cli ask "转折关系做题的核心口诀是什么"                # CLI 提问
python -m poc.cli selftest                                          # 离线自测（无 key 可用）
```

完整开发/部署/测试流程见 `docs/开发操作手册.md` 与 `docs/测试清单.md`。

## 目录结构（关键部分）

```text
gongkao/
├── docker-compose.yml        # 双站点编排（backend+frontend，数据卷 ./data:/data）
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── .env.example
│   ├── poc/                  # Python 内核
│   │   ├── api.py            # FastAPI 应用（全部 REST + SSE）
│   │   ├── config.py         # 老师注册表 + 配置加载
│   │   ├── ingest.py         # 课件解析入库
│   │   ├── store.py          # Chroma 向量库
│   │   ├── answer.py         # 检索→Prompt→LLM→拒答兜底
│   │   ├── chat.py           # 会话历史 SQLite
│   │   ├── cache.py          # 双层缓存
│   │   ├── guard.py          # 幻觉检测三道关卡
│   │   ├── auth.py           # 用户/额度/OAuth 绑定
│   │   ├── gitee.py          # Gitee OAuth 客户端
│   │   ├── metrics.py        # 监控指标
│   │   └── cli.py            # 命令行入口
│   └── tests/                # 11 个测试套件（见 docs/测试清单.md）
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf            # 反代 /api/ + SSE 300s + 限流
│   └── index.html            # 纯静态前端（无构建）
├── data/                     # 挂载卷（服务器侧）
│   ├── raw/T001/             # 课件原始文件
│   └── vector/               # Chroma 向量库
└── docs/                     # 开发文档（本文档体系）
```

## 常用命令

| 命令 | 说明 |
|---|---|
| `python -m poc.cli ingest <file...>` | 课件入库（md/txt/docx/pptx/pdf） |
| `python -m poc.cli ask "问题"` | CLI 提问 |
| `python -m poc.cli selftest` | 离线自测 |
| `python tests/test_xxx.py` | 跑某套测试（见测试清单） |
| `docker compose build backend frontend` | 重建镜像（缓存模式，改了代码必做） |

## 当前状态（2026-09-03）

- ✅ **M2.5 全部交付**：双站点上线、SSE 流式、会话持久化、双层缓存、幻觉检测三关卡、用户/额度、前端登录 UI、监控指标、Gitee OAuth
- ✅ **P0 修复**：用户/会话数据落卷持久化（重建容器不丢）
- ⛔ **视频转写**：死胡同（中转站无 whisper 模型 / 服务器无 ffmpeg / 资源不足）
- ⬜ **遗留**：HTTPS、LLM 重试降级、会员管理后台、说明书 docx 同步、真实课件全量入库

详细进度与决策见 `docs/项目状态总览.md`。