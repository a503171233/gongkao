package com.gongkao.app;

import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.database.Cursor;
import android.net.Uri;
import android.os.Environment;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;

/**
 * 在线更新管理器（批次H）：
 * - 应用内下载：系统 DownloadManager（通知栏进度，零依赖），完成后经 MiniFileProvider
 *   content:// Uri 拉起系统安装器，用户确认即覆盖安装
 * - 网盘通道：复制链接+提取码到剪贴板并跳浏览器（不经过 App 网络栈，规避平台风控）
 * - 热更补丁：下载 patch.dex → sha256 校验 → PatchRuntime 本地加载（远程代码本地执行）
 */
public final class UpdateManager {

    private UpdateManager() { }

    /** 本 App 的 versionCode / versionName（动态读取，无需编译期常量）。 */
    public static int appVc(Context ctx) {
        try {
            return ctx.getPackageManager()
                    .getPackageInfo(ctx.getPackageName(), 0).versionCode;
        } catch (Exception e) {
            return 0;
        }
    }

    public static String appVersion(Context ctx) {
        try {
            return ctx.getPackageManager()
                    .getPackageInfo(ctx.getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "?";
        }
    }

    // ------------------------------------------------------------------
    // 应用内下载 APK → 覆盖安装
    // ------------------------------------------------------------------

    /** 更新包下载目录（App 私有 external files，无需存储权限）。 */
    public static File updateDir(Context ctx) {
        File d = new File(ctx.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS),
                "updates");
        if (!d.exists()) d.mkdirs();
        return d;
    }

    /** 批次 J：已下载完成的更新包（存在且非占位）→ 供「安装已下载的包」恢复入口。 */
    public static File downloadedApk(Context ctx) {
        File f = new File(updateDir(ctx), "gongkao-update.apk");
        return f.exists() && f.length() > 1024 ? f : null;
    }

    /**
     * 发起应用内下载（重复调用会替换旧任务）。返回 DownloadManager 任务 id。
     * 下载完成广播由 {@link #registerApkReceiver} 注册的接收器处理。
     */
    public static long enqueueApkDownload(Context ctx, String url, String versionName) {
        File target = new File(updateDir(ctx), "gongkao-update.apk");
        if (target.exists()) target.delete();   // 覆盖旧包

        DownloadManager dm = (DownloadManager) ctx.getSystemService(Context.DOWNLOAD_SERVICE);
        DownloadManager.Request req = new DownloadManager.Request(Uri.parse(url));
        req.setTitle("公考学习 v" + versionName);
        req.setDescription("下载完成后点击安装即可升级");
        req.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
        req.setMimeType("application/vnd.android.package-archive");
        req.setDestinationUri(Uri.fromFile(target));
        req.setAllowedOverMetered(true);       // 允许流量下载（包小）
        req.setAllowedOverRoaming(false);
        return dm.enqueue(req);
    }

    /** 查询任务进度：返回 int[]{status, progress%}；任务不存在返回 null。 */
    public static int[] queryProgress(Context ctx, long downloadId) {
        DownloadManager dm = (DownloadManager) ctx.getSystemService(Context.DOWNLOAD_SERVICE);
        Cursor c = dm.query(new DownloadManager.Query().setFilterById(downloadId));
        if (c == null) return null;
        int status = -1, pct = 0;
        try {
            if (c.moveToFirst()) {
                status = c.getInt(c.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS));
                long done = c.getLong(c.getColumnIndexOrThrow(
                        DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR));
                long total = c.getLong(c.getColumnIndexOrThrow(
                        DownloadManager.COLUMN_TOTAL_SIZE_BYTES));
                if (total > 0) pct = (int) (done * 100 / total);
            }
        } finally {
            c.close();
        }
        return status < 0 ? null : new int[]{status, pct};
    }

    /** 注册下载完成广播：完成即校验 sha256（可选）并拉起安装器。返回接收器（注销用）。 */
    public static BroadcastReceiver registerApkReceiver(Context ctx, long downloadId,
                                                        String expectSha256) {
        BroadcastReceiver r = new BroadcastReceiver() {
            @Override
            public void onReceive(Context c, Intent intent) {
                long id = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1);
                if (id != downloadId) return;
                File apk = new File(updateDir(c), "gongkao-update.apk");
                if (!apk.exists() || apk.length() < 1024) return;   // 下载失败占位
                if (expectSha256 != null && !expectSha256.isEmpty()
                        && !sha256Of(apk).equalsIgnoreCase(expectSha256)) {
                    apk.delete();
                    Ui.toast(c, "安装包校验失败，已删除，请重试或改用网盘下载");
                    return;
                }
                installApk(c, apk);
            }
        };
        ctx.registerReceiver(r, new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE));
        return r;
    }

    public static void unregisterApkReceiver(Context ctx, BroadcastReceiver r) {
        if (r != null) {
            try { ctx.unregisterReceiver(r); } catch (Exception ignored) { }
        }
    }

    /** 拉起系统安装器（覆盖安装）。API 26+ 未授权「未知来源安装」时先跳开关页。 */
    public static void installApk(Context ctx, File apk) {
        if (android.os.Build.VERSION.SDK_INT >= 26 && !ctx.getPackageManager()
                .canRequestPackageInstalls()) {
            // 引导用户开「允许安装未知应用」（返回后用户重按安装即可）
            Intent s = new Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES);
            s.setData(Uri.parse("package:" + ctx.getPackageName()));
            s.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            ctx.startActivity(s);
            Ui.toast(ctx, "请先允许「安装未知应用」，返回后点「安装已下载的包」");
            return;
        }
        Uri uri = MiniFileProvider.uriForUpdateApk(ctx, apk.getName());
        Intent it = new Intent(Intent.ACTION_VIEW);
        it.setDataAndType(uri, "application/vnd.android.package-archive");
        it.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                | Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            ctx.startActivity(it);
        } catch (Exception e) {
            Ui.toast(ctx, "无法启动安装器：" + e.getMessage());
        }
    }

    // ------------------------------------------------------------------
    // 网盘通道（需求2）：复制链接+提取码 → 跳浏览器
    // ------------------------------------------------------------------

    /** 复制网盘链接（含提取码）到剪贴板并用系统浏览器打开。 */
    public static void openNetdisk(Context ctx, String url, String pwd, String label) {
        StringBuilder sb = new StringBuilder(url == null ? "" : url);
        if (pwd != null && !pwd.isEmpty()) sb.append("  提取码：").append(pwd);
        ClipboardManager cm = (ClipboardManager)
                ctx.getSystemService(Context.CLIPBOARD_SERVICE);
        if (cm != null) {
            cm.setPrimaryClip(android.content.ClipData.newPlainText("gk-update", sb.toString()));
        }
        Ui.toast(ctx, "链接已复制（含提取码），正在打开" + (label == null ? "网盘" : label) + "…");
        try {
            Intent it = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
            it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            ctx.startActivity(it);
        } catch (Exception e) {
            Ui.toast(ctx, "请手动在浏览器打开链接");
        }
    }

    // ------------------------------------------------------------------
    // 热更补丁下载（需求1）：远程代码 → 本地加载
    // ------------------------------------------------------------------

    /**
     * 下载补丁 dex → sha256 校验 → 落盘 files/patch/patch.dex（下次启动生效）。
     * 回调 ok(true)=已就绪。
     */
    public static void downloadPatch(Context ctx, String url, String sha256, Api.Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(60000);
                int code = conn.getResponseCode();
                if (code != 200) {
                    cb.err("补丁下载失败 HTTP " + code);
                    return;
                }
                InputStream in = conn.getInputStream();
                java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
                in.close();
                byte[] dexBytes = bos.toByteArray();

                if (sha256 != null && !sha256.isEmpty()
                        && !sha256Of(dexBytes).equalsIgnoreCase(sha256)) {
                    cb.err("补丁校验失败（sha256 不匹配），已丢弃");
                    return;
                }
                File target = PatchRuntime.patchFile(ctx);
                target.getParentFile().mkdirs();
                java.io.FileOutputStream fo = new java.io.FileOutputStream(target);
                fo.write(dexBytes);
                fo.close();
                cb.ok(new org.json.JSONObject().put("ok", true));
            } catch (Exception e) {
                cb.err("补丁下载异常：" + e.getMessage());
            }
        }).start();
    }

    // ------------------------------------------------------------------
    // 工具
    // ------------------------------------------------------------------

    public static String sha256Of(File f) {
        try (InputStream in = new FileInputStream(f)) {
            return sha256Of(in);
        } catch (Exception e) {
            return "";
        }
    }

    public static String sha256Of(byte[] data) {
        try {
            return sha256Of(new java.io.ByteArrayInputStream(data));
        } catch (Exception e) {
            return "";
        }
    }

    private static String sha256Of(InputStream in) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) md.update(buf, 0, n);
        StringBuilder sb = new StringBuilder();
        for (byte b : md.digest()) sb.append(String.format("%02x", b));
        return sb.toString();
    }
}
