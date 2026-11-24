package com.gongkao.app;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * 主容器（真原生版 v2.2）：底部导航切换 问答/刷题/题册/论坛/我的 五个原生页面，零 WebView。
 * 未登录自动进入 LoginActivity；ProfilePage 每次切回刷新额度；消息未读经「我的」页宫格入口。
 * 批次A：问答页新增图片/文件附件；刷题/题册/论坛由后续批次实装（当前为占位页）。
 */
public class MainActivity extends Activity {

    private static final String[][] TABS = {
            {"💬", "问答"}, {"📝", "刷题"}, {"📚", "题册"}, {"🌐", "论坛"}, {"👤", "我的"}};

    private Prefs prefs;
    private ChatPage chatPage;
    private ProfilePage profilePage;
    private PracticeHome practiceHome;
    private MistakesBook mistakesBook;
    private ForumPage forumPage;   // 批次E：学员论坛
    private View chatView;      // 批次29：缓存已构建视图，切 tab 不再重建（保留聊天记录）
    private View profileView;
    private View practiceView;
    private View bookView;
    private View forumView;
    private FrameLayout container;
    private TextView[] tabViews = new TextView[TABS.length];
    private int currentTab = 0;
    private int lastTheme = 0;   // 批次F：主题版本（设置页改动后 onResume 检测重建）
    private TextView announceView;   // 批次H：热更公告条

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);
        if (!prefs.loggedIn()) {
            startActivityForResult(new Intent(this, LoginActivity.class), 1);
        }

        // 批次F：全局夜间模式缓存注入（设置页可改，onResume 检测变更重建）
        Ui.setNightMode(prefs.nightMode());
        lastTheme = prefs.nightMode();

        getWindow().requestFeature(Window.FEATURE_NO_TITLE);
        Ui.systemBars(this);   // 状态栏颜色/图标亮暗按主题同步

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(this, "bg"));

        // 批次H：热更补丁——本地存有 patch.dex 则加载执行（child-first，异常不阻塞）。
        // 必须在公告条/页面构建之前，补丁钩子（公告/备用域名）才能对本次会话生效。
        PatchRuntime.tryApply(this);

        // 批次H：热更公告条（补丁经 PatchHooks.setAnnouncement 下发，GONE 常态）
        announceView = new TextView(this);
        announceView.setTextSize(12.5f);
        announceView.setTextColor(0xFFFFFFFF);
        announceView.setGravity(Gravity.CENTER);
        android.graphics.drawable.GradientDrawable annBg =
                new android.graphics.drawable.GradientDrawable();
        annBg.setColor(Ui.c(this, "brandDark"));
        announceView.setBackground(annBg);
        announceView.setPadding(dp(12), dp(6), dp(12), dp(6));
        String ann = PatchHooks.announcement();
        announceView.setVisibility(ann == null ? View.GONE : View.VISIBLE);
        announceView.setText(ann == null ? "" : ann);
        page.addView(announceView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        container = new FrameLayout(this);
        page.addView(container, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        page.addView(buildTabbar(), new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(56)));

        setContentView(page);
        switchTab(0);

        // 批次H：启动静默检查更新（延迟 2.5s 不抢启动资源；发现新版弹一次提示）
        Ui.post(2500, () -> silentUpdateCheck());
    }

    /** 启动时静默检查：有新版且未被用户略过 → 弹更新提示。 */
    private void silentUpdateCheck() {
        Api.updateCheck(new Api.Cb() {
            @Override
            public void ok(org.json.JSONObject d) {
                int cur = UpdateManager.appVc(MainActivity.this);
                int latest = d.optInt("latest_vc", cur);
                if (latest <= cur) return;
                if (prefs.updatePromptedVc() >= latest) return;   // 本版已提示过
                prefs.setUpdatePromptedVc(latest);
                String vn = d.optString("latest_version", "");
                org.json.JSONArray notes = d.optJSONArray("notes");
                String first = notes == null || notes.length() == 0
                        ? "" : notes.optString(0);
                runOnUiThread(() -> Ui.dialogBuilder(MainActivity.this)
                        .setTitle("🎁 发现新版本 v" + vn)
                        .setMessage(first.isEmpty()
                                ? "包含体验优化与问题修复，建议升级。"
                                : first)
                        .setPositiveButton("去更新", (dlg, w) -> startActivity(
                                new Intent(MainActivity.this, UpdateActivity.class)))
                        .setNegativeButton("下次再说", null)
                        .show());
            }

            @Override
            public void err(String ignored) { /* 静默失败 */ }
        });
    }

    private LinearLayout buildTabbar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setBackgroundColor(Ui.c(this, "card"));
        bar.setPadding(0, dp(6), 0, dp(6));

        for (int i = 0; i < TABS.length; i++) {
            final int idx = i;
            // 批次28：emoji 与文字拆分双 TextView——原「emoji\n文字」同 View
            // 且行距 0.5，emoji 彩色字形溢出行框压住文字（图片盖字根因）
            LinearLayout tab = new LinearLayout(this);
            tab.setOrientation(LinearLayout.VERTICAL);
            tab.setGravity(Gravity.CENTER);
            TextView icon = new TextView(this);
            icon.setText(TABS[i][0]);
            icon.setTextSize(16);
            icon.setGravity(Gravity.CENTER);
            icon.setIncludeFontPadding(false);
            TextView label = new TextView(this);
            label.setText(TABS[i][1]);
            label.setTextSize(10);
            label.setGravity(Gravity.CENTER);
            label.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
            label.setIncludeFontPadding(false);
            label.setPadding(0, dp(3), 0, 0);
            tab.addView(icon);
            tab.addView(label);
            tab.setOnClickListener(v -> switchTab(idx));
            tabViews[i] = label;
            bar.addView(tab, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f));
        }
        return bar;
    }

    private void switchTab(int idx) {
        currentTab = idx;
        for (int i = 0; i < tabViews.length; i++) {
            tabViews[i].setTextColor(i == idx ? Ui.c(this, "brand") : Ui.c(this, "faint"));
        }
        container.removeAllViews();
        View v;
        switch (idx) {
            case 0:
                if (chatPage == null) chatPage = new ChatPage(this, prefs);
                if (chatView == null) chatView = chatPage.build();
                chatPage.onShow();   // 同步老师名（可能刚在「我的」切换过）
                v = chatView;
                break;
            case 1:
                // 批次B：刷题页实装（今日练习/错题/分类专项/智能组卷/模考）
                if (practiceHome == null) practiceHome = new PracticeHome(this, prefs);
                if (practiceView == null) practiceView = practiceHome.build();
                practiceHome.refresh();
                v = practiceView;
                break;
            case 2:
                // 批次C：错题本/收藏夹实装
                if (mistakesBook == null) mistakesBook = new MistakesBook(this, prefs);
                if (bookView == null) bookView = mistakesBook.build();
                mistakesBook.refresh();
                v = bookView;
                break;
            case 3:
                // 批次E：学员论坛实装（版块/发帖/回帖/点赞/举报）
                if (forumPage == null) forumPage = new ForumPage(this, prefs);
                if (forumView == null) forumView = forumPage.build();
                forumPage.refresh();
                v = forumView;
                break;
            default:
                if (profilePage == null) profilePage = new ProfilePage(this, prefs);
                if (profileView == null) profileView = profilePage.build();
                profilePage.refresh();   // 每次切到「我的」都刷新额度/角色/未读
                v = profileView;
                break;
        }
        container.addView(v, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    @Override
    public void startActivityForResult(Intent intent, int requestCode) {
        super.startActivityForResult(intent, requestCode);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, android.content.Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        // 批次A：附件选图/拍照结果转发给问答页
        if (chatPage != null && requestCode >= Attach.REQ_CAMERA
                && requestCode <= Attach.REQ_FILE) {
            chatPage.onAttachResult(requestCode, resultCode, data);
            return;
        }
        if (requestCode == 1 && resultCode == RESULT_OK) {
            // 登录成功：两页全部重建（新 token/用户态），问答历史清零
            chatPage = null; chatView = null;
            profilePage = null; profileView = null;
            switchTab(0);
        } else if (!prefs.loggedIn()) {
            finish();   // 登录页被退出 → 结束 App
        }
    }

    /** 回到前台：主题版本变化 → 重建全部页面缓存（ChatPage 复用 adapter，聊天记录保留）。 */
    @Override
    protected void onResume() {
        super.onResume();
        if (prefs.nightMode() != lastTheme) {
            lastTheme = prefs.nightMode();
            Ui.setNightMode(lastTheme);
            Ui.systemBars(this);
            chatView = null; profileView = null; practiceView = null;
            bookView = null; forumView = null;
            practiceHome = null; mistakesBook = null; forumPage = null; profilePage = null;
            switchTab(currentTab);
            return;
        }
        if (profilePage != null && currentTab == 4) profilePage.refresh();
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
