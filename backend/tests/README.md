# 测试体系说明（#8 测试体系整理）

> 更新：2026-09-08

## 目录结构

```
tests/
├── test_*.py            # 正式测试（分两类，见下）
├── online_smoke.py      # 线上冒烟验收（公网 HTTP，可重复运行）
├── online_kernel_8mods.py  # 线上 RAG 内核 8 模块验收（B1~B8）
├── run_tests.py         # 统一 runner（脚本风格单测聚合）
└── archive/             # 历史一次性/运维脚本归档（~90 个，保留追溯）
```

## 两类测试

### 1. 脚本风格单测（21 个）

顶层执行 + `sys.exit`/`assert` 风格，**不被 pytest 收集**（import 阶段即触发 SystemExit）。

- 运行方式（统一入口）：
  ```bash
  cd backend
  python tests/run_tests.py                    # 全部
  python tests/run_tests.py test_auth.py       # 指定
  ```
- 特点：每个测试自隔离（临时目录 + 环境变量），互不干扰。
- 环境要求：需安装 `chromadb`/`openai` 等依赖（本地 venv 若缺失，脚本顶部 import 会失败）。

### 2. pytest 风格（2 个）

`test_36_practice.py` / `test_mockexam.py` 为真 pytest 风格（`def test_*` + `assert`）。

- 运行方式：
  ```bash
  cd backend
  python -m pytest                    # pytest.ini 已配置仅收集这两个文件
  ```

## 线上验收（2 个，默认不跑）

`online_smoke.py` / `online_kernel_8mods.py` 需真实服务器（http://YOUR_SERVER_IP:3000），
默认不纳入 runner，用 `--online` 显式包含：

```bash
python tests/run_tests.py --online
```

## 归档说明（archive/）

历史一次性/运维脚本统一归档，**移动未删除**，保留历史追溯：

| 类别 | 数量 | 说明 |
|---|---|---|
| `deploy_*.py` | 27 | 历史部署脚本（含已执行的生产服务器凭据，勿复用） |
| `verify_*.py` | 18 | 一次性验证脚本 |
| `diag_*.py` / `_diag_*.py` | 14 | 诊断排查脚本 |
| `_probe_*.py` / `_run_*.py` / `_url_*.py` | 10 | 探查/试跑脚本 |
| `seed_*.py` / `e2e_*.py` / 其他 | ~20 | 冷启动种子/端到端/清理 |

> ⚠️ 归档脚本内含生产服务器凭据（`deploy_*.py` 硬编码 SSH 密码），
> 属历史遗留，已随 git 历史入库。**请勿在归档脚本基础上复制凭据**，
> 新部署统一走 `scripts/deploy.py`（凭据仍应迁到环境变量，见遗留项）。

## 遗留项（未在本轮处理）

1. **脚本风格 → pytest 风格改写**：23 个单测仍为脚本风格，未改写为 `def test_*`。
   改写可让 pytest 统一收集 + 失败定位更细，但成本高、风险中等，建议按需分批。
2. **凭据外置**：`scripts/deploy.py` 与归档脚本硬编码服务器密码，应迁至环境变量/密钥文件。
3. **CI 集成**：`run_tests.py` / pytest 尚未接入 CI（无 CI 配置文件）。
