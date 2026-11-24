package com.gongkao.patch;

import android.content.Context;
import android.widget.Toast;

/**
 * 示例热更补丁（批次I 演练版 v2）：演示「远程获取代码 → 本地加载执行」全链路。
 *
 * 构建（宿主编译产物 /tmp/gk-apk-build/classes 存在时）：
 *   ./tools/build-patch.sh tools/patch-demo /tmp/patch-2.dex
 *
 * 下发：把 /tmp/patch-2.dex 放到任意 HTTPS 地址，更新配置 app-update.json 写入：
 *   "patch": {"vc": 2, "url": "https://…/patches/patch-2.dex", "sha256": "<文件哈希>"}
 * 客户端检查更新时自动下载校验落盘，重启 App 即执行本 apply()。
 *
 * 本示例能力：下发公告条（PatchHooks.setAnnouncement）。
 * 真实运维中可用于：切换备用 API 域名（setApiBase）、下发开关配置、紧急文案修正。
 */
public class PatchMain implements com.gongkao.app.PatchMain {

    @Override
    public String name() {
        return "运营公告补丁";
    }

    @Override
    public int vc() {
        return 2;
    }

    @Override
    public void apply(Context ctx) {
        com.gongkao.app.PatchHooks.setAnnouncement(
                "📣 v2.8.1 已发布：作答页交互升级（选项涟漪反馈 · 深色模式修复），"
                        + "去「我的 → 检查更新」升级体验");
        android.util.Log.i("GkPatch", "patch v2 applied: announcement hook set");
    }
}
