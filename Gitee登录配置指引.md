# Gitee 登录配置指引

> 多老师专属垂直大模型平台 · 第三方登录（OAuth 2.0）
> 适用版本：2026-09-03 交付的 M2.5 + Gitee OAuth 扩展

## 一、功能说明

平台已内置 **Gitee 第三方登录**，实现"扫码/账号授权即登录，免注册"：

- 登录/注册模态框内新增 🦊 **「使用 Gitee 登录」** 按钮（密码注册/登录渠道保留，互不影响）
- 首次 Gitee 授权 → **自动创建账号**并绑定（用户名格式 `你的Gitee昵称@gitee`，重名自动加序号；随机密码，无法用密码登录，仅能用 Gitee 登录）
- 之后每次 Gitee 授权 → 直接登录原账号（同一 Gitee 账号始终对应同一平台账号，绑定持久化）
- 登录后额度体系照常生效（免费 20 次/天），左上角显示用户名 + 今日剩余额度

**未配置 CILENT_ID/SECRET 时**：系统自动降级——Gitee 按钮点击提示"暂未开放"，不影响其他功能。

## 二、启用步骤（一次性配置）

### 第 1 步：注册 Gitee 开放平台应用

1. 登录 [Gitee](https://gitee.com)，进入 **设置 → 安全设置 → 第三方应用**（或直接访问 https://gitee.com/oauth/applications）
2. 点击 **创建应用**，填写：

| 字段 | 填写内容 |
|---|---|
| 应用名称 | 如：多老师智能助教 |
| 应用主页 | `http://YOUR_SERVER_IP:3000` |
| **应用回调地址** | **必须**填：`http://YOUR_SERVER_IP:3000/api/auth/gitee/callback` |
| 权限（勾选） | `user_info`（读取基本信息） |

> ⚠️ **回调地址必须与上面完全一致**（含端口和路径），否则授权成功后会提示 redirect_uri 不匹配。

3. 创建成功后，页面会显示：
   - **Client ID**（公开）
   - **Client Secret**（机密，仅展示一次，务必复制保存）

### 第 2 步：把凭据配置到服务器

将以下三个环境变量写入服务器 `/home/ubuntu/gongkao/.env`（在原有内容后追加，把 `xxx` 替换为你的真实值）：

```bash
GITEE_CLIENT_ID=xxx
GITEE_CLIENT_SECRET=xxx
GITEE_REDIRECT_URI=http://YOUR_SERVER_IP:3000/api/auth/gitee/callback
```

然后重建并重启后端容器使配置生效：

```bash
cd /home/ubuntu/gongkao
docker compose build backend   # 缓存模式，几秒钟（代码已内置 OAuth 逻辑）
docker compose up -d backend
```

### 第 3 步：验收

1. 浏览器打开 `http://YOUR_SERVER_IP:3000`（建议无痕窗口）
2. 点右上角 **登录** → 弹窗内点 🦊 **使用 Gitee 登录**
3. 跳转 Gitee 授权页 → 同意授权
4. 自动跳回首页并显示已登录（顶部显示 Gitee 昵称 + 剩余额度）

## 三、常见问题

**Q1：点击 Gitee 登录提示"暂未开放"？**
未配置 `GITEE_CLIENT_ID/SECRET`。按上文第 1、2 步配置后重建容器即可。可先验证：访问 `http://YOUR_SERVER_IP:3000/api/auth/gitee/status`，应返回 `{"enabled": true}`。

**Q2：授权页提示"回调地址不合法"？**
Gitee 后台填写的回调地址与 `.env` 中 `GITEE_REDIRECT_URI` 不一致。两者必须都为 `http://YOUR_SERVER_IP:3000/api/auth/gitee/callback`。

**Q3：之前用密码注册的账号，怎么跟 Gitee 关联？**
当前版本机制：Gitee 授权自动新建独立账号。若已存在同名 `昵称@gitee` 则加序号（`昵称@gitee1`）。如需合并账号/把密码账号升级为 Gitee 登录，需管理员在后台操作（联系开发）。

**Q4：安全性如何？**
- OAuth 流程带 `state` 参数防 CSRF（10 分钟有效期，一次性）
- 平台 token 与 Gitee token 完全隔离：`access_token` 只用于换取用户信息，**不落库、不进返回给前端**
- Gitee Client Secret 只存服务器 `.env`，永不进前端代码

## 四、技术实现（供开发参考）

| 组件 | 说明 |
|---|---|
| `backend/poc/gitee.py` | Gitee OAuth 客户端：授权 URL / code 换 token / 拉用户信息；标准库实现零依赖；可注入 `_http` mock 测试 |
| `backend/poc/auth.py` | `oauth_links` 表（provider + provider_uid 联合主键绑定 user_id）；`register_oauth` 自动建号；`find_user_by_oauth` 查绑定 |
| `backend/poc/api.py` | `/auth/gitee/status`（探测配置）、`/auth/gitee/login`（跳授权页）、`/auth/gitee/callback`（回调处理，302 回首页带 `?token=`） |
| `frontend/index.html` | 模态框 🦊 按钮；启动时解析 URL `?token=` 存 localStorage 并清除地址栏；`giteeLogin()` 先探测再跳转 |

**路由前缀说明**：nginx 的 `/api/` 规则会剥掉 `/api` 前缀再转后端，故后端路由为 `/auth/gitee/*`，对外访问是一致 URL `/api/auth/gitee/*`。改路由时切勿搞混。

**测试**：`backend/tests/test_gitee.py`（客户端 mock 10 项）+ `test_api_gitee.py`（API 降级路径 6 项），`python tests/test_gitee.py && python tests/test_api_gitee.py` 应全部 PASS。