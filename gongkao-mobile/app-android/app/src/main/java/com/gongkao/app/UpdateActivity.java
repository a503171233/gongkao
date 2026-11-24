package com.gongkao.app;

import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.File;

/**
 * 检查更新页（批次H · 在线更新）。
 * - 应用内通道：检查 → 展示新版亮点 → DownloadManager 下载（通知栏进度）
 *   → sha256 校验 → 拉起系统安装器覆盖安装
 * - 网盘通道：复制链接+提取码 → 跳浏览器（适合大包/直连受限场景）
 * - 热更补丁：展示远程代码补丁状态，支持手动清除
 * 更新配置由服务端 /app-update.json 下发（nginx 静态文件，改配置零重启）。
 */
public class UpdateActivity extends BaseActivity {

    private TextView statusText;
    private LinearLayout resultBox;    // 检查结果区（动态填充）
    private TextView patchState;
    private LinearLayout patchCard;    // 热更补丁状态卡（下载后即时刷新）
    private Prefs prefs;
    private JSONObject cfg;            // /app-update.json 内容
    private long downloadId = -1;
    private BroadcastReceiver apkReceiver;
    private TextView installBtn;       // 批次 J：已下载包恢复安装入口
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable poll = new Runnable() {
        @Override public void run() {
            if (downloadId < 0) return;
            int[] p = UpdateManager.queryProgress(UpdateActivity.this, downloadId);
            if (p == null) { handler.postDelayed(this, 600); return; }
            switch (p[0]) {
                case DownloadManager.STATUS_RUNNING:
                case DownloadManager.STATUS_PAUSED:
                    statusText.setText("⬇️ 下载中 " + p[1] + "%"
                            + (p[0] == DownloadManager.STATUS_PAUSED ? "（等待网络）" : ""));
                    break;
                case DownloadManager.STATUS_SUCCESSFUL:
                    statusText.setText("✅ 下载完成，正在拉起安装…");
                    downloadId = -1;
                    return;                    // 广播接管安装
                case DownloadManager.STATUS_FAILED:
                    statusText.setText("❌ 下载失败，可检查网络后重试，或改用网盘通道");
                    downloadId = -1;
                    return;
                default:
                    break;
            }
            handler.postDelayed(this, 600);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);
        Ui.systemBars(this);

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(this, "bg"));

        page.addView(Ui.topBar(this, "🚀 检查更新", v -> finish()),
                new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(Ui.dp(this, 16), Ui.dp(this, 14), Ui.dp(this, 16), Ui.dp(this, 40));
        android.widget.ScrollView scroll = new android.widget.ScrollView(this);
        scroll.addView(body, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        page.addView(scroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        setContentView(page);

        // ---------------- 当前版本卡 ----------------
        LinearLayout verCard = new LinearLayout(this);
        verCard.setOrientation(LinearLayout.VERTICAL);
        verCard.setBackground(Ui.card(this, 14));
        int p14 = Ui.dp(this, 14);
        verCard.setPadding(p14, Ui.dp(this, 12), p14, Ui.dp(this, 12));
        TextView cur = new TextView(this);
        cur.setText("公考学习 · 当前版本");
        cur.setTextSize(13);
        cur.setTextColor(Ui.c(this, "faint"));
        verCard.addView(cur);
        TextView curV = new TextView(this);
        curV.setText("v" + UpdateManager.appVersion(this)
                + "（versionCode " + UpdateManager.appVc(this) + "）");
        curV.setTextSize(17);
        curV.setTextColor(Ui.c(this, "text"));
        curV.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
        curV.setPadding(0, Ui.dp(this, 3), 0, 0);
        verCard.addView(curV);
        body.addView(verCard);

        // ---------------- 检查按钮 ----------------
        TextView checkBtn = Ui.btnPrimary(this, "🔍 检查更新", 12);
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        clp.topMargin = Ui.dp(this, 12);
        checkBtn.setOnClickListener(v -> check());
        body.addView(checkBtn, clp);

        statusText = new TextView(this);
        statusText.setTextSize(13.5f);
        statusText.setTextColor(Ui.c(this, "sub"));
        statusText.setPadding(Ui.dp(this, 4), Ui.dp(this, 10), Ui.dp(this, 4), 0);
        body.addView(statusText);

        // 批次 J：已下载包的恢复安装入口（下载完成后从「未知来源授权页」返回时，
        // 广播已消费、无重装入口导致用户卡死——本按钮常驻，onResume 按需显隐）
        installBtn = Ui.btnPrimary(this, "📦 安装已下载的更新包", 12);
        LinearLayout.LayoutParams ilp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        ilp.topMargin = Ui.dp(this, 8);
        installBtn.setOnClickListener(v -> {
            File apk = UpdateManager.downloadedApk(this);
            if (apk == null) {
                Ui.toast(this, "本地没有已下载的更新包");
                refreshInstallBtn();
                return;
            }
            UpdateManager.installApk(this, apk);
        });
        installBtn.setVisibility(View.GONE);
        body.addView(installBtn, ilp);

        resultBox = new LinearLayout(this);
        resultBox.setOrientation(LinearLayout.VERTICAL);
        body.addView(resultBox);

        // ---------------- 热更补丁状态卡 ----------------
        body.addView(section("热更补丁（远程代码 · 本地加载）"));
        patchCard = new LinearLayout(this);
        patchCard.setOrientation(LinearLayout.VERTICAL);
        patchCard.setBackground(Ui.card(this, 14));
        patchCard.setPadding(p14, Ui.dp(this, 12), p14, Ui.dp(this, 12));
        patchState = new TextView(this);
        refreshPatchState(patchCard);
        patchCard.addView(patchState);

        TextView patchDesc = new TextView(this);
        patchDesc.setTextSize(12);
        patchDesc.setTextColor(Ui.c(this, "faint"));
        patchDesc.setLineSpacing(Ui.dp(this, 1), 1f);
        patchDesc.setText("远程下发的轻量代码包（dex），启动时本地加载执行：可紧急修复逻辑、\n"
                + "切换备用线路、下发公告。页面级新功能仍需上方整包覆盖安装。");
        patchDesc.setPadding(0, Ui.dp(this, 8), 0, 0);
        patchCard.addView(patchDesc);

        if (PatchRuntime.present(this)) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            TextView reload = Ui.btnSecondary(this, "重新加载", 10);
            reload.setOnClickListener(v -> {
                PatchRuntime.clear(this);
                boolean ok = PatchRuntime.tryApply(this);
                Ui.toast(this, ok ? "补丁已重新加载" : "补丁加载失败（已清除）");
                refreshPatchState(patchCard);
            });
            row.addView(reload);
            TextView clear = Ui.btnSecondary(this, "清除补丁", 10);
            LinearLayout.LayoutParams lp = (LinearLayout.LayoutParams) reload.getLayoutParams();
            lp.leftMargin = Ui.dp(this, 10);
            clear.setOnClickListener(v -> {
                PatchRuntime.clear(this);
                Ui.toast(this, "已清除，重启 App 后回到纯宿主版本");
                refreshPatchState(patchCard);
            });
            row.addView(clear);
            row.setPadding(0, Ui.dp(this, 10), 0, 0);
            patchCard.addView(row);
        }
        body.addView(patchCard);

        Ui.reveal(page);
        PatchRuntime.tryApply(this);   // 进入页面顺带执行一次补丁（幂等）
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        handler.removeCallbacks(poll);
        UpdateManager.unregisterApkReceiver(this, apkReceiver);
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshInstallBtn();
    }

    /** 批次 J：本地有已下载的更新包时显示安装入口（覆盖授权页返回等场景）。 */
    private void refreshInstallBtn() {
        if (installBtn == null) return;
        File apk = UpdateManager.downloadedApk(this);
        if (apk != null) {
            installBtn.setVisibility(View.VISIBLE);
            long mb = apk.length() / 1024 / 1024;
            installBtn.setText(mb > 0
                    ? "📦 安装已下载的更新包（约 " + mb + "MB）"
                    : "📦 安装已下载的更新包");
        } else {
            installBtn.setVisibility(View.GONE);
        }
    }

    // ------------------------------ 检查与渲染 ------------------------------

    private void check() {
        statusText.setText("正在检查…");
        resultBox.removeAllViews();
        Api.updateCheck(new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                runOnUiThread(() -> {
                    cfg = d;
                    render(d);
                });
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> statusText.setText("检查失败：" + m
                        + "\n（服务端未发布更新配置时属正常现象）"));
            }
        });
    }

    private void render(JSONObject d) {
        int curVc = UpdateManager.appVc(this);
        int latestVc = d.optInt("latest_vc", curVc);
        String latestVn = d.optString("latest_version", "?");
        boolean hasNew = latestVc > curVc;

        if (!hasNew) {
            statusText.setText("✓ 已是最新版本");
            return;
        }
        statusText.setText("");

        // 新版本卡片
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(Ui.card(this, 14));
        int p14 = Ui.dp(this, 14);
        card.setPadding(p14, Ui.dp(this, 13), p14, Ui.dp(this, 13));

        TextView head = new TextView(this);
        head.setText("🎁 发现新版本 v" + latestVn
                + (d.optBoolean("force") ? "（重要更新）" : ""));
        head.setTextSize(16);
        head.setTextColor(Ui.c(this, "brandDark"));
        head.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
        card.addView(head);

        org.json.JSONArray notes = d.optJSONArray("notes");
        if (notes != null) {
            for (int i = 0; i < notes.length() && i < 6; i++) {
                String line = notes.optString(i, "");
                if (line.isEmpty()) continue;
                TextView t = new TextView(this);
                t.setText("· " + line);
                t.setTextSize(13);
                t.setTextColor(Ui.c(this, "sub"));
                t.setPadding(0, Ui.dp(this, 4), 0, 0);
                card.addView(t);
            }
        }

        // 主通道：应用内下载并安装
        String apkUrl = d.optString("apk_url", "");
        TextView dl = Ui.btnPrimary(this, "⬇️ 应用内下载并安装", 12);
        LinearLayout.LayoutParams dlp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        dlp.topMargin = Ui.dp(this, 12);
        dl.setOnClickListener(v -> startDownload(latestVn));
        card.addView(dl, dlp);

        // 网盘通道（需求2）：配置了网盘源才显示
        org.json.JSONArray sources = d.optJSONArray("sources");
        if (sources != null) {
            for (int i = 0; i < sources.length(); i++) {
                JSONObject s = sources.optJSONObject(i);
                if (s == null || !"netdisk".equals(s.optString("type"))) continue;
                String url = s.optString("url", "");
                if (url.isEmpty()) continue;
                String label = s.optString("label", "网盘下载");
                String pwd = s.optString("pwd", "");
                TextView nd = Ui.btnSecondary(this, "🔗 " + label + "（自动复制链接+提取码）", 12);
                LinearLayout.LayoutParams nlp = new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                nlp.topMargin = Ui.dp(this, 8);
                nd.setOnClickListener(v -> UpdateManager.openNetdisk(this, url, pwd, label));
                card.addView(nd, nlp);
            }
        }
        resultBox.addView(card);

        // 热更补丁通道（需求1）：服务端 patch 字段非空且本地未装 → 自动下载落盘
        autoPatch(d);
    }

    private void autoPatch(JSONObject d) {
        JSONObject patch = d.isNull("patch") ? null : d.optJSONObject("patch");
        if (patch == null) return;
        String pUrl = patch.optString("url", "");
        int pVc = patch.optInt("vc", 0);
        if (pUrl.isEmpty() || pVc <= prefs.patchVc()) return;
        String sha = patch.isNull("sha256") ? null : patch.optString("sha256", "");
        if (sha != null && sha.isEmpty()) sha = null;
        statusText.setText("⬇️ 发现热更补丁（vc" + pVc + "），正在下载…");
        UpdateManager.downloadPatch(this, pUrl, sha, new Api.Cb() {
            @Override public void ok(JSONObject r) {
                prefs.setPatchVc(pVc);
                runOnUiThread(() -> {
                    statusText.setText("✅ 热更补丁 vc" + pVc + " 已就绪，重启 App 后生效");
                    refreshPatchState(patchCard);
                });
            }
            @Override public void err(String m) {
                runOnUiThread(() -> {
                    statusText.setText("补丁下载失败：" + m + "（不影响整包更新）");
                });
            }
        });
    }

    // ------------------------------ 下载 ------------------------------

    private void startDownload(String versionName) {
        if (cfg == null) return;
        String url = cfg.optString("apk_url", "");
        if (url.isEmpty()) {
            Ui.toast(this, "服务端未提供安装包地址");
            return;
        }
        String sha = cfg.isNull("sha256") ? null : cfg.optString("sha256", "");
        if (sha != null && sha.isEmpty()) sha = null;
        downloadId = UpdateManager.enqueueApkDownload(this, url, versionName);
        apkReceiver = UpdateManager.registerApkReceiver(this, downloadId, sha);
        statusText.setText("⬇️ 已加入下载队列…");
        handler.postDelayed(poll, 600);
    }

    // ------------------------------ 补丁区 ------------------------------

    private void refreshPatchState(LinearLayout patchCard) {
        String txt;
        if (PatchRuntime.loadedName() != null) {
            txt = "已加载补丁：" + PatchRuntime.loadedName()
                    + "（vc" + PatchRuntime.loadedVc() + "）✅";
        } else if (PatchRuntime.present(this)) {
            txt = "📦 本地存有补丁包，重启 App 后生效";
        } else {
            txt = "未启用（服务端未下发补丁）";
        }
        patchState.setText(txt);
        patchState.setTextSize(14);
        patchState.setTextColor(Ui.c(this, "text"));
    }

    private TextView section(String t) {
        TextView tv = new TextView(this);
        tv.setText(t);
        tv.setTextSize(13);
        tv.setTextColor(Ui.c(this, "faint"));
        tv.setPadding(Ui.dp(this, 4), Ui.dp(this, 16), Ui.dp(this, 4), Ui.dp(this, 8));
        return tv;
    }
}
