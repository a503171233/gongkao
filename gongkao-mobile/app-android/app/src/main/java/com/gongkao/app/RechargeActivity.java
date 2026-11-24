package com.gongkao.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

/**
 * 会员充值页（批次E）：会员状态 + 充值码激活（卡密）+ 网页支付指引（按用户决策跳网页）。
 * 契约：GET /me/membership → {role,is_member,member_expire_at,days_remaining}
 *      POST /orders/recharge-code/activate {code} → 订单激活（5 次/10 分钟频控，成败均计数）。
 */
public class RechargeActivity extends BaseActivity {

    private static final String WEB_HOME = "https://a2a0641667069c341.app.workbuddy.host";

    private Prefs prefs;
    private LinearLayout body;
    private TextView status;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);

        getWindow().requestFeature(android.view.Window.FEATURE_NO_TITLE);
        if (android.os.Build.VERSION.SDK_INT >= 23) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        }
        getWindow().setStatusBarColor(Ui.c(RechargeActivity.this, "card"));

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(RechargeActivity.this, "bg"));

        page.addView(topBar("💎 会员充值", v -> finish()));

        status = new TextView(this);
        status.setTextSize(13);
        status.setTextColor(Ui.c(RechargeActivity.this, "faint"));
        status.setGravity(Gravity.CENTER);
        status.setPadding(dp(16), dp(10), dp(16), dp(4));
        page.addView(status);

        ScrollView scroll = new ScrollView(this);
        body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(14), dp(4), dp(14), dp(30));
        scroll.addView(body, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        page.addView(scroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        setContentView(page);
        load();
    }

    private View topBar(String title, View.OnClickListener back) {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Ui.c(RechargeActivity.this, "card"));
        bar.setPadding(dp(12), dp(10), dp(12), dp(10));
        TextView b = new TextView(this);
        b.setText("←");
        b.setTextSize(20);
        b.setTextColor(Ui.c(RechargeActivity.this, "sub"));
        b.setPadding(dp(4), dp(2), dp(14), dp(2));
        b.setOnClickListener(back);
        bar.addView(b);
        TextView t = new TextView(this);
        t.setText(title);
        t.setTextSize(17);
        t.setTextColor(Ui.c(RechargeActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(t);
        return bar;
    }

    private void load() {
        status.setText("加载会员状态…");
        Api.membership(prefs.token(), new Api.Cb() {
            @Override
            public void ok(JSONObject m) {
                runUi(() -> {
                    status.setText("");
                    paint(m);
                });
            }

            @Override
            public void err(String e) {
                runUi(() -> {
                    status.setText("加载失败：" + e);
                    paint(new JSONObject());
                });
            }
        });
    }

    private void paint(JSONObject m) {
        body.removeAllViews();
        boolean isMember = m.optBoolean("is_member");

        // 会员状态卡
        LinearLayout hero = new LinearLayout(this);
        hero.setOrientation(LinearLayout.VERTICAL);
        hero.setGravity(Gravity.CENTER_HORIZONTAL);
        hero.setPadding(dp(16), dp(18), dp(16), dp(16));
        GradientDrawable hg = new GradientDrawable();
        hg.setCornerRadius(dp(14));
        hg.setColor(0xFF2B2620);
        hero.setBackground(hg);
        TextView icon = new TextView(this);
        icon.setText(isMember ? "👑" : "💎");
        icon.setTextSize(34);
        hero.addView(icon);
        TextView title = new TextView(this);
        title.setText(isMember ? "尊贵的会员" : "普通学员");
        title.setTextSize(17);
        title.setTextColor(Ui.c(RechargeActivity.this, "heroGold"));
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setPadding(0, dp(6), 0, 0);
        hero.addView(title);
        TextView sub = new TextView(this);
        if (isMember) {
            sub.setText("剩余 " + m.optInt("days_remaining") + " 天 · 至 "
                    + m.optString("member_expire_at", "").replace("T", " ")
                            .substring(0, Math.min(10,
                                    m.optString("member_expire_at", "").length())));
        } else {
            sub.setText("开通会员解锁全部题库与无限问答");
        }
        sub.setTextSize(12);
        sub.setTextColor(0xFFBBAF9A);
        sub.setPadding(0, dp(4), 0, 0);
        hero.addView(sub);
        body.addView(wrap(hero));

        // 权益说明
        LinearLayout perks = card();
        perks.addView(section("会员权益"));
        String[] lines = {
                "✅ 全模块题库无限练习与判分",
                "✅ AI 老师无限次追问与讲解",
                "✅ 申论批改 / 面试点评不限次",
                "✅ 智能组卷 · 在线模考全解锁",
                "✅ 错题本 · 收藏夹 · 艾宾浩斯复习",
        };
        for (String s : lines) {
            TextView p = new TextView(this);
            p.setText(s);
            p.setTextSize(13);
            p.setTextColor(Ui.c(RechargeActivity.this, "sub"));
            p.setPadding(0, dp(6), 0, 0);
            perks.addView(p);
        }
        body.addView(wrap(perks));

        // 充值码激活
        LinearLayout redeem = card();
        redeem.addView(section("🎟 充值码激活"));
        TextView hint = new TextView(this);
        hint.setText("向老师购买充值码后在此兑换会员：");
        hint.setTextSize(12.5f);
        hint.setTextColor(Ui.c(RechargeActivity.this, "sub"));
        hint.setPadding(0, dp(6), 0, 0);
        redeem.addView(hint);
        EditText codeEt = new EditText(this);
        codeEt.setHint("输入充值码");
        codeEt.setTextSize(14);
        codeEt.setTextColor(Ui.c(RechargeActivity.this, "text"));
        codeEt.setAllCaps(false);
        GradientDrawable ebg = new GradientDrawable();
        ebg.setCornerRadius(dp(10));
        ebg.setColor(Ui.c(RechargeActivity.this, "cardAlt"));
        ebg.setStroke(dp(1), Ui.c(RechargeActivity.this, "line"));
        codeEt.setBackground(ebg);
        codeEt.setPadding(dp(10), dp(9), dp(10), dp(9));
        LinearLayout.LayoutParams elp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        elp.setMargins(0, dp(8), 0, 0);
        redeem.addView(codeEt, elp);
        Button go = new Button(this);
        go.setText("兑换");
        go.setTextColor(Color.WHITE);
        go.setBackgroundColor(Ui.c(RechargeActivity.this, "brand"));
        LinearLayout.LayoutParams glp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        glp.setMargins(0, dp(10), 0, 0);
        redeem.addView(go, glp);
        go.setOnClickListener(v -> {
            String code = codeEt.getText().toString().trim();
            if (code.isEmpty()) {
                toast("请输入充值码");
                return;
            }
            go.setEnabled(false);
            go.setText("兑换中…");
            Api.redeemCode(prefs.token(), code, new Api.Cb() {
                @Override
                public void ok(JSONObject r) {
                    runUi(() -> {
                        go.setEnabled(true);
                        go.setText("兑换");
                        Ui.dialogBuilder(RechargeActivity.this)
                                .setTitle("🎉 兑换成功")
                                .setMessage("会员已开通/续期，快去刷题吧！")
                                .setPositiveButton("好的", (d, w) -> load())
                                .show();
                    });
                }

                @Override
                public void err(String e) {
                    runUi(() -> {
                        go.setEnabled(true);
                        go.setText("兑换");
                        Ui.dialogBuilder(RechargeActivity.this)
                                .setMessage("兑换失败：" + e
                                        + "\n\n（试错有限频：5 次/10 分钟，请核对后重试）")
                                .setPositiveButton("好的", null)
                                .show();
                    });
                }
            });
        });
        body.addView(wrap(redeem));

        // 网页支付指引
        TextView web = new TextView(this);
        web.setText("💳 想在线支付购买充值码？\n用浏览器打开网页版 → 「会员充值」：\n"
                + WEB_HOME + "\n（点按可复制网址）");
        web.setTextSize(12);
        web.setTextColor(Ui.c(RechargeActivity.this, "purple"));
        web.setGravity(Gravity.CENTER);
        web.setLineSpacing(dp(3), 1f);
        web.setPadding(0, dp(14), 0, 0);
        web.setOnClickListener(v -> {
            android.content.ClipboardManager cm =
                    (android.content.ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            cm.setPrimaryClip(android.content.ClipData.newPlainText("url", WEB_HOME));
            toast("网址已复制");
        });
        body.addView(web);
    }

    // ------------------------------ 工具 ------------------------------

    private void runUi(Runnable r) {
        runOnUiThread(r);
    }

    private TextView section(String s) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(14);
        t.setTextColor(Ui.c(RechargeActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        return t;
    }

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(RechargeActivity.this, "card"));
        bg.setStroke(dp(1), Ui.c(RechargeActivity.this, "line"));
        card.setBackground(bg);
        return card;
    }

    private LinearLayout wrap(View card) {
        LinearLayout wrap = new LinearLayout(this);
        wrap.setPadding(dp(2), dp(5), dp(2), dp(5));
        wrap.addView(card, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return wrap;
    }

    private void toast(String m) {
        android.widget.Toast.makeText(this, m, android.widget.Toast.LENGTH_SHORT).show();
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
