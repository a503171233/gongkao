package com.gongkao.app;

import android.app.Activity;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * 设置页（批次F 新增）：
 * - 夜间模式三态：跟随系统 / 浅色 / 深色（Prefs 持久化 + Ui.setNightMode 同步缓存）
 * - 聊天字号三档：小 / 标准 / 大（MsgAdapter.fontSizeOff）
 * - 清除会话记忆：置空 session_id，下次提问开启全新会话
 * 修改即时生效：本页 recreate 换肤；返回主页后 MainActivity 检测主题版本重建全部页面。
 */
public class SettingsActivity extends BaseActivity {

    private Prefs prefs;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);
        Ui.systemBars(this);   // 按当前主题同步状态栏

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(this, "bg"));

        page.addView(Ui.topBar(this, "设置", v -> finish()), new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        ScrollViewish pad = new ScrollViewish(this);
        pad.addView(body);
        page.addView(pad, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        setContentView(page);

        int pad16 = Ui.dp(this, 16);
        body.setPadding(pad16, Ui.dp(this, 14), pad16, Ui.dp(this, 30));

        // ---------------- 外观 ----------------
        body.addView(sectionTitle("外观"));
        String[][] nightOpts = {{"0", "🌤 跟随系统", "白天浅色、夜晚深色自动切换"},
                {"1", "☀️ 浅色模式", "始终使用浅色主题"},
                {"2", "🌙 深色模式", "始终使用深色主题，夜间更护眼"}};
        LinearLayout nightBox = cardColumn();
        int curNight = prefs.nightMode();
        for (int i = 0; i < nightOpts.length; i++) {
            final int val = i;
            View row = optionRow(nightBox, nightOpts[i][1], nightOpts[i][2], curNight == val);
            row.setOnClickListener(v -> {
                if (prefs.nightMode() != val) {
                    prefs.setNightMode(val);
                    Ui.setNightMode(val);
                    recreate();   // 本页即时换肤；返回主页后重建全部页面
                }
            });
        }
        body.addView(nightBox);

        // ---------------- 聊天字号 ----------------
        body.addView(sectionTitle("聊天字号"));
        LinearLayout fontBox = cardColumn();
        String[] fontNames = {"小", "标准", "大"};
        float[] fontOff = {-1.5f, 0f, 1.5f};
        int curFont = 1;
        if (MsgAdapter.fontSizeOff < -0.7f) curFont = 0;
        else if (MsgAdapter.fontSizeOff > 0.7f) curFont = 2;
        for (int i = 0; i < fontNames.length; i++) {
            final int val = i;
            View row = optionRow(fontBox, fontNames[i], null, curFont == val);
            row.setOnClickListener(v -> {
                MsgAdapter.fontSizeOff = fontOff[val];
                recreate();
            });
        }
        body.addView(fontBox);
        TextView fontHint = new TextView(this);
        fontHint.setTextSize(12);
        fontHint.setTextColor(Ui.c(this, "faint"));
        fontHint.setText("影响问答页 AI 回答与消息文字大小");
        fontHint.setPadding(Ui.dp(this, 4), Ui.dp(this, 6), Ui.dp(this, 4), 0);
        body.addView(fontHint);

        // ---------------- 会话 ----------------
        body.addView(sectionTitle("会话"));
        LinearLayout sessBox = cardColumn();
        LinearLayout clear = optionRow(sessBox, "🧹 清除会话记忆", "下次提问将开启全新会话", false);
        clear.setOnClickListener(v -> {
            prefs.setSessionId("");
            Ui.toast(this, "已清除，下次提问开启新会话");
        });
        body.addView(sessBox);

        Ui.reveal(page);
    }

    // ------------------------------ 构建小件 ------------------------------

    private TextView sectionTitle(String t) {
        TextView tv = new TextView(this);
        tv.setText(t);
        tv.setTextSize(13);
        tv.setTextColor(Ui.c(this, "faint"));
        tv.setPadding(Ui.dp(this, 4), Ui.dp(this, 14), Ui.dp(this, 4), Ui.dp(this, 8));
        return tv;
    }

    private LinearLayout cardColumn() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setBackground(Ui.card(this, 14));
        return box;
    }

    /** 选项行：标题+副标题+选中勾。选中态金浅底。 */
    private LinearLayout optionRow(LinearLayout parent, String title,
                                   String sub, boolean selected) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.VERTICAL);
        row.setBackground(selected ? Ui.ripple(this, "brandSoft", 14)
                : Ui.rippleFlat(this, 14));
        int pad = Ui.dp(this, 14);
        row.setPadding(pad, Ui.dp(this, 11), pad, Ui.dp(this, 11));

        TextView t = new TextView(this);
        t.setText(title);
        t.setTextSize(15);
        t.setTextColor(selected ? Ui.c(this, "brandDark") : Ui.c(this, "text"));
        row.addView(t);

        if (sub != null && !sub.isEmpty()) {
            TextView s = new TextView(this);
            s.setText(sub);
            s.setTextSize(12);
            s.setTextColor(Ui.c(this, "faint"));
            s.setPadding(0, Ui.dp(this, 2), 0, 0);
            row.addView(s);
        }

        TextView check = new TextView(this);
        check.setText(selected ? "✓" : "");
        check.setTextSize(15);
        check.setTextColor(Ui.c(this, "brandDark"));
        check.setGravity(Gravity.END);
        check.setPadding(0, Ui.dp(this, 2), Ui.dp(this, 2), 0);
        row.addView(check);

        parent.addView(row, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return row;
    }

    /** 轻量滚动容器（避免额外 import ScrollView 命名冲突）。 */
    private static class ScrollViewish extends android.widget.ScrollView {
        ScrollViewish(android.content.Context c) {
            super(c);
            setVerticalScrollBarEnabled(false);
        }
    }
}
