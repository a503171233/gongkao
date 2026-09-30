package com.gongkao.app;

import android.content.Context;

/**
 * 热更补丁约定接口（批次H · 在线更新）。
 * 补丁 dex 由远程下发（/app-update.json 的 patch 字段），本地 DexClassLoader 加载。
 * 约定：补丁内必须包含一个 public 类 <b>com.gongkao.patch.PatchMain</b>，
 * 实现本接口（宿主接口在 com.gongkao.app 包，补丁入口类独立包名防同名冲突），
 * 且编译时以宿主 APK 的 classes（含本接口）为 classpath。
 * 能力边界（诚实说明）：补丁在宿主启动时执行 apply()，可通过 PatchHooks 覆盖
 * API 域名等运行时钩子、弹公告、写配置；无法替换 Manifest 预注册的 Activity——
 * 页面结构级更新仍需整包覆盖安装（更新页双通道已覆盖）。
 */
public interface PatchMain {

    /** 补丁显示名（更新页展示）。 */
    String name();

    /** 补丁版本号（单调递增，本地已加载版本小于它才会下载）。 */
    int vc();

    /** 补丁入口：宿主启动（或补丁下载后重启）时执行一次。 */
    void apply(Context ctx);
}
