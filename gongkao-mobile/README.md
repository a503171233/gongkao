# gongkao-mobile · 公考学习移动端

一套后端（FastAPI，多端复用核心资产）+ 两个客户端：

```
gongkao-mobile/
├── app-android/            # 安卓原生工程（WebView 承载学习平台全功能）
│   ├── app/src/main/
│   │   ├── java/com/gongkao/app/MainActivity.java   # 核心活动
│   │   ├── AndroidManifest.xml
│   │   └── res/            # 图标(5档)/主题/文案
│   ├── build.gradle        # 本地 Android Studio 维护用（阿里云镜像）
│   └── settings.gradle
├── gongkao-release.keystore # 发布签名（30年有效期，妥善保管，丢了无法覆盖安装）
└── miniprogram/            # 微信小程序骨架（原生，为后续上线准备）
    ├── app.js / app.json
    ├── utils/api.js        # API 封装 + SSE 流式问答（chunked 分块解析）
    └── pages/
        ├── login/          # 登录（契约与 Web 端一致）
        └── chat/           # 问答（SSE 流式渲染）
```

## 一、安卓 App

**产物**：`gongkao.apk`（**v2.8.2 在线更新版**，versionCode 13，v2+v3 签名，minSdk 24 即 Android 7+）

**架构**：独立原生客户端——**零 WebView**，所有界面原生渲染，直连 FastAPI 后端 API（与 Web 端并行）。

**功能矩阵**（v2.2.0 → v2.7.0 六批次全功能实装，契约测试 A~E 全绿）：

| 版本 | 批次 | 功能 |
|------|------|------|
| v2.2.0 | A | 问答附件（拍照/相册/文件 ≤3 个，自动压缩）· 消息中心（未读红点/全文/全部已读）· 我的页九宫格骨架 |
| v2.3.0 | B | 刷题线：今日智能练习/只刷错题/分类专项/单题练习循环（判分→错题本→艾宾浩斯）/智能组卷/在线模考（计时+答题卡+成绩单） |
| v2.4.0 | C | 题册线：错题本（我的答案 vs 正确答案+重练）/收藏夹（分组+删除） |
| v2.5.0 | D | 学习线：学习计划（生成/打卡/重生成）/学习报告（五区块）/激励排行（成就墙+Top20）/备考文章（AI 要点提炼）/申论批改（四题型+逐句批注）/面试练习（五模块+AI 点评）/知识体系（树形+掌握度+一键练题） |
| v2.6.0 | E | 社区账号线：学员论坛（版块/发帖/回帖/点赞/举报）/粉笔绑定（账密+短信原生，滑块跳网页指引）/会员充值（充值码激活+网页支付指引）/找回密码（密保三步）/设置密保/意见反馈 |
| v2.7.0 | F | UI 质感全面升级：Ui 设计系统（日/夜双主题 21 色语义板+涟漪+动效，约 580 处色值语义化）/AI 回答 Markdown 排版（标题/粗体/列表/代码块/引用）/停止生成（SSE 断流+保留已生成内容）/气泡长按（复制全文/重新生成）/设置页（夜间模式三态+聊天字号三档+清除会话）/论坛与文章骨架屏加载态/页面错峰入场动画 |
| v2.7.1 | G | 全局打磨：BaseActivity 统一 15 页转场动画与状态栏同步/弹窗深色自适配（21 处 Material 双主题）/骨架屏推广（错题本·消息中心·学习报告） |
| v2.8.0 | H | 在线更新体系：检查更新页（版本卡+更新日志）/应用内下载覆盖安装（DownloadManager+sha256 校验+未知来源引导）/网盘下载通道（复制链接+提取码跳浏览器）/热更补丁（远程 dex 本地 child-first 加载，PatchHooks 钩子：备用 API 域名灾备+公告下发）/启动静默检查+一次性升级弹窗 |
| v2.8.1 | I | 下载页固化（tools/build-download-page.sh 一键生成内嵌 APK 的单文件下载页）/粉笔提升计划任务书全文展示+一键同步到学习计划/作答页交互升级（Ui.btnSolid 实底按钮+涟漪反馈+深色选中态修复）/热更补丁 v2 演练（运营公告下发） |
| v2.8.2 | J | 粉笔同步明细：错题/收藏/模考计数升级为可点开明细页（FenbiDataActivity，7 个新 API）/AI 提升计划分析触发+进度轮询。**在线更新紧急修复**：更新包读取路径对齐（外部 Downloads 优先+内部回退，修复安装器 FileNotFoundException）+「安装已下载的更新包」常驻恢复入口（授权页返回不再卡死） |

**底部 5 Tab**：💬 问答 · 📝 刷题 · 📚 题册 · 🌐 论坛 · 👤 我的（消息中心 + 功能九宫格）

**关键实现注记**：
- 认证双头冗余：`Authorization: Bearer` + `X-Auth-Token`（公网反代可能剥离 Authorization，与 Web 端 a4-request.js 对齐）
- SSE 事件协议（start/delta/refs/guard/error/done）与 Web 端一致；错误后 onDone 跳过不覆盖错误提示
- 粉笔滑块（need_drag）按决策跳网页完成：提示打开网页版「我的 → 粉笔绑定」+ 一键复制网址
- 语音提问：后端无 whisper 通道（/audio/transcriptions 501），未上 App（后端开启后可补）

**源码结构**（纯 android.* framework，零第三方依赖，沙箱手工链可构建）：
```
java/com/gongkao/app/
├── Ui.java                   # 设计系统（批次F）：日/夜双主题色板/涟漪/卡片/按钮/顶栏/骨架屏/动效
├── Markdown.java             # AI 回答排版（批次F）：标题/粗体/列表/代码块/引用
├── SettingsActivity.java     # 设置（批次F）：夜间模式三态/聊天字号/清除会话
├── Api.java                  # HTTP+SSE 客户端（60 方法 + SSE 取消机制）
├── Prefs.java                # token/老师/会话/夜间模式 存储
├── Attach.java + MiniFileProvider.java   # 问答附件（拍照/相册/文件，压缩+EXIF 转正）
├── ChatPage.java / MsgAdapter.java       # 问答页（SSE 流式 + Markdown + 停止/长按菜单）
├── PracticeHome.java / PracticeActivity.java / PaperExamActivity.java  # 刷题线
├── MistakesBook.java         # 错题本/收藏夹
├── StudyPlanActivity.java / LearningReportActivity.java / IncentiveBoardActivity.java
├── ArticlesActivity.java / EssayActivity.java / InterviewActivity.java / KnowledgeActivity.java
├── ForumPage.java            # 学员论坛（骨架屏加载态）
├── FenbiActivity.java / RechargeActivity.java / ForgotActivity.java
├── MessagesActivity.java     # 消息中心
├── ProfilePage.java / LoginActivity.java / MainActivity.java
└── PlaceholderPage.java      # 备用占位页
```

**换部署地址**：改 `Api.java` 的 `BASE` 常量（粉笔滑块/支付的网页指引地址在
`FenbiActivity.WEB_HOME` / `RechargeActivity.WEB_HOME` 同步替换）。

**安装**：传输 APK 到安卓手机 → 点击安装（需允许「安装未知应用」）。
**在线下载页**（v2.6.0 起，部署于 Web 应用 nginx，与 API 同域 HTTPS）：
- 下载页：`https://a2a0641667069c341.app.workbuddy.host/apk.html`
- APK 直链：`https://a2a0641667069c341.app.workbuddy.host/gongkao.apk`
- 更新方式：`docker cp` 覆盖 `gongkao-frontend:/usr/share/nginx/html/{apk.html,gongkao.apk}`
  （apk.html 内嵌 APK base64 + 直链双通道；勿用「发布为应用」——会顶掉本域 API 反代）

**在线更新运维**（v2.8.0 起，配置文件 `app-update.json` 同目录，改完 docker cp 即生效零重启）：
```bash
# 发新版流程：构建 → 算哈希 → 改配置 → 三件套上线
./build-apk.sh <新versionCode> <新版本名> /workspace/apk-dist/gongkao.apk
APK_SHA=$(sha256sum /workspace/apk-dist/gongkao.apk | cut -d' ' -f1)
# 编辑 /tmp/app-update.json：latest_vc↑ / latest_version / sha256=$APK_SHA / notes 更新日志
docker cp /tmp/app-update.json gongkao-frontend:/usr/share/nginx/html/app-update.json
docker cp /workspace/apk-dist/gongkao.apk gongkao-frontend:/usr/share/nginx/html/gongkao.apk
# 老版本 App 启动即收到更新提示（检查接口 /api/app-update.json，nginx 精确路由静态直出）
```
- **网盘通道**：在 sources 里 type=netdisk 的 url/pwd 填上网盘分享链接与提取码（留空则 App 自动隐藏该按钮）
- **热更补丁**：`./tools/build-patch.sh tools/patch-demo /tmp/patch-demo.dex`
  → 补丁 dex 传 HTTPS → patch 字段填 {vc↑, url, sha256}；补丁入口类约定 `com.gongkao.patch.PatchMain`
  实现 `com.gongkao.app.PatchMain` 接口；能力=PatchHooks 钩子（备用 API 域名/公告）+ 自定义逻辑，
  页面结构级更新仍走整包覆盖安装
- 契约测试：`python3 tests/contract_test_h.py`（check 结构/APK 三通道 sha256 一致性/主链路回归）

**本地二次构建**（两种方式任选）：
1. Android Studio 打开 `app-android/` → Build APK（首次自动下 gradle/SDK，仓库已配阿里云镜像）
2. **一键脚本**（v2.0.1 起内置，无 Gradle 手工链）：配好 `SDK_DIR`/`ANDROID_JAR` 环境变量后执行
   ```bash
   ./build-apk.sh <版本号> <版本名> <输出路径>   # 例: ./build-apk.sh 5 2.0.2 ./gongkao.apk
   ```
   脚本流程：aapt2 compile/link → javac → d8 → zipalign → apksigner，任一步失败即中止。

**签名密钥**：`gongkao-release.keystore`（alias `gongkao`，口令 `gongkao2026`）。
⚠️ 务必备份此文件：覆盖安装必须同一签名；正式上架前建议更换强口令。

## 二、微信小程序（骨架，为上线准备）

**资质现状**：个人主体 → 不能用 web-view 嵌网页 → 必须原生小程序（本骨架即原生方案）。

**复用策略**：后端零改动——小程序直接调 FastAPI 的 REST/SSE 接口（`utils/api.js` 已封装，
SSE 用 `wx.request enableChunked` 分块解析，与 Web 端 a4 同一契约）。

**上线步骤**：
1. 注册小程序（mp.weixin.qq.com）拿 AppID → 替换 `project.config.json` 的 `appid`
2. 微信开发者工具导入 `miniprogram/` 目录
3. 开发者工具「详情-本地设置」勾选「不校验合法域名」先调试
4. 正式发布前：小程序后台「开发管理-服务器域名」把 `a2a0641667069c341.app.workbuddy.host`
   加入 **request 合法域名**（HTTPS ✅ 已满足要求）
5. 补齐后续页面（错题/粉笔/报告等）——按需渐进，API 均现成

**已知边界**（小程序平台限制，规划时注意）：
- 申论导出 DOCX 下载：`wx.downloadFile` 可替代，但 blob 类导出需后端提供直链
- SSE 断线重连：骨架版未实现（Web 端 a4 逻辑可移植）
- 附件提问：可用 `wx.uploadFile` 对接 `/api/ask/upload`，骨架版未含
