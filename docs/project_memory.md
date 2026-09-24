# 公考项目记忆

## 项目状态
- 线上地址：http://YOUR_SERVER_IP:3000（ECS 双容器 docker compose）
- 部署命令：`docker compose build backend frontend && docker compose up -d`
- 管理后台：http://YOUR_SERVER_IP:3000/admin.html（admin/admin123）
- 源码：`D:\xiangmu\gongkao`

## AI 采集（#29 #30 回采迭代）

### 采集架构
- `backend/poc/admin.py` 中 `extract_questions_ai` 入口
- 长文档（>2500 字）按段落边界分批 → 分批提取 → `absorb` 题干去重合并
- `_count_question_floor()`：按"答案："行 + 编号行统计文档最小题数
- `_half_split()`：按段落边界拆半递归

### 回采逻辑（#29 R3）
- `extract_piece` 内嵌：解析成功但回收数 < 文档题数 × 0.75 时拆半重采
- 深度 ≤ 3 层，子批内深度 < 2 时再拆一次取优
- 阈值 0.75 折衷（0.85 导致重复膨胀，0.6 回采不足）
- 重复项由外层 `absorb` 按题干前 80 字 + 选项前 60 字去重

### 已知边界
- **同题干逐字相同题组**（6 道"题干相同、选项不同"选择题 + 4 道判断）：模型偶发合并，回采从 avg 2.3→5.3，但无法根治（模型语义去重固有行为）。真实题库文档题干通常各异，稳定 12±1/12。
- 判断题识别率略低于选择题（偶发漏 1 道或把解析句误当判断——13/12），入库端题干去重兜底。

### 验证脚本
- `verify_ai_collect.py`：全链路 9/9 PASS（parse-file → 短文本 → 长文档 12/12）
- `verify_ai_stability.py`：3 次连跑采样 12/13/12
- `verify_ai_dedup.py`：12/12 无重复
- `regression.py`：65/65 PASS（注意 admin 登录限流 `LOGIN_LIMIT`，多次验证需间隔 5 分钟）

## 前端修复
- `admin.js` 版本号 `?v=YYYYMMDDx` 递增，改动后需同步
- 入库按钮 `#imp-import-btn`：采集弹窗底部常驻，初始 disabled，未提取/提取 0 保持禁用
- 分类页 `qcCreateTemplate`：一键生成 7 个一级公考分类（幂等跳过已存在）

## 部署注意
- 必须 `compose build` 再 `up -d`，不能只 `up -d`
- `scripts/deploy.py` 的 FILES 清单手动维护，新增文件需同步加入
- 前端静态资源带版本号，避免浏览器缓存
- 容器内调试需 `docker cp` + `cd /app && python poc/xxx.py`（PYTHONPATH 需 sys.path.insert(0,"/app")）