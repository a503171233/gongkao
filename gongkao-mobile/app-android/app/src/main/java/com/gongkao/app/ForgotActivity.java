package com.gongkao.app;

import android.app.Activity;
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
 * 找回密码页（批次E）：密保问题三步向导。
 * 第一步 POST /password/forgot/question {username} → {can_recover,security_question}
 * 第二步 POST /password/forgot/verify {username,answer} → {ticket}（答错限流 429）
 * 第三步 POST /password/forgot/reset {ticket,new_password} → {reset}（全量吊销旧 token）。
 * 成功后返回登录页用新密码登录。
 */
public class ForgotActivity extends BaseActivity {

    private LinearLayout body;
    private TextView status;
    private String username = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        getWindow().requestFeature(android.view.Window.FEATURE_NO_TITLE);
        if (android.os.Build.VERSION.SDK_INT >= 23) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        }
        getWindow().setStatusBarColor(Ui.c(ForgotActivity.this, "card"));

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(ForgotActivity.this, "bg"));

        // 顶栏
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Ui.c(ForgotActivity.this, "card"));
        bar.setPadding(dp(12), dp(10), dp(12), dp(10));
        TextView b = new TextView(this);
        b.setText("←");
        b.setTextSize(20);
        b.setTextColor(Ui.c(ForgotActivity.this, "sub"));
        b.setPadding(dp(4), dp(2), dp(14), dp(2));
        b.setOnClickListener(v -> finish());
        bar.addView(b);
        TextView t = new TextView(this);
        t.setText("🔑 找回密码");
        t.setTextSize(17);
        t.setTextColor(Ui.c(ForgotActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(t);
        page.addView(bar);

        status = new TextView(this);
        status.setTextSize(13);
        status.setTextColor(Ui.c(ForgotActivity.this, "faint"));
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
        step1();
    }

    private void step1() {
        status.setText("");
        body.removeAllViews();
        body.addView(tip("输入你的学号/用户名，系统会显示你注册时设置的密保问题。"));

        EditText userEt = input("用户名 / 学号");
        body.addView(field(userEt));
        Button next = submitBtn("下一步");
        next.setOnClickListener(v -> {
            username = userEt.getText().toString().trim();
            if (username.isEmpty()) {
                toast("请输入用户名");
                return;
            }
            status.setText("查询密保问题…");
            Api.forgotQuestion(username, new Api.Cb() {
                @Override
                public void ok(JSONObject d) {
                    runUi(() -> {
                        if (!d.optBoolean("can_recover")) {
                            status.setText("");
                            Ui.dialogBuilder(ForgotActivity.this)
                                    .setMessage("该账号未设置密保，无法自助找回。\n请联系老师重置密码。")
                                    .setPositiveButton("好的", null).show();
                            return;
                        }
                        step2(d.optString("security_question", ""));
                    });
                }

                @Override
                public void err(String m) {
                    runUi(() -> status.setText("查询失败：" + m));
                }
            });
        });
        body.addView(next);
    }

    private void step2(String question) {
        status.setText("");
        body.removeAllViews();
        body.addView(tip("请回答你的密保问题："));
        LinearLayout qCard = card();
        TextView q = new TextView(this);
        q.setText(question);
        q.setTextSize(15);
        q.setTextColor(Ui.c(ForgotActivity.this, "brandDark"));
        q.setTypeface(Typeface.DEFAULT_BOLD);
        qCard.addView(q);
        body.addView(wrap(qCard));

        EditText ansEt = input("密保答案");
        body.addView(field(ansEt));
        Button verify = submitBtn("验证答案");
        verify.setOnClickListener(v -> {
            String ans = ansEt.getText().toString().trim();
            if (ans.isEmpty()) {
                toast("请输入答案");
                return;
            }
            status.setText("校验中…");
            Api.forgotVerify(username, ans, new Api.Cb() {
                @Override
                public void ok(JSONObject d) {
                    runUi(() -> step3(d.optString("ticket", "")));
                }

                @Override
                public void err(String m) {
                    runUi(() -> {
                        status.setText("");
                        Ui.dialogBuilder(ForgotActivity.this)
                                .setMessage("答案不正确（" + m + "）")
                                .setPositiveButton("重试", null).show();
                    });
                }
            });
        });
        body.addView(verify);
    }

    private void step3(String ticket) {
        status.setText("");
        body.removeAllViews();
        body.addView(tip("密保验证通过（票据 10 分钟有效）。请设置新密码："));

        EditText pwdEt = input("新密码（至少 8 位）");
        pwdEt.setInputType(android.text.InputType.TYPE_CLASS_TEXT
                | android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        body.addView(field(pwdEt));
        EditText pwd2Et = input("再输入一次确认");
        pwd2Et.setInputType(android.text.InputType.TYPE_CLASS_TEXT
                | android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        body.addView(field(pwd2Et));
        Button reset = submitBtn("重置密码");
        reset.setOnClickListener(v -> {
            String p1 = pwdEt.getText().toString();
            String p2 = pwd2Et.getText().toString();
            if (p1.length() < 8) {
                toast("密码至少 8 位");
                return;
            }
            if (!p1.equals(p2)) {
                toast("两次输入不一致");
                return;
            }
            status.setText("重置中…");
            Api.forgotReset(ticket, p1, new Api.Cb() {
                @Override
                public void ok(JSONObject d) {
                    runUi(() -> {
                        Ui.dialogBuilder(ForgotActivity.this)
                                .setTitle("✅ 重置成功")
                                .setMessage("所有旧登录已失效，请用新密码重新登录。")
                                .setCancelable(false)
                                .setPositiveButton("去登录", (dg, wg) -> finish())
                                .show();
                    });
                }

                @Override
                public void err(String m) {
                    runUi(() -> status.setText("重置失败：" + m));
                }
            });
        });
        body.addView(reset);
    }

    // ------------------------------ 工具 ------------------------------

    private void runUi(Runnable r) {
        runOnUiThread(r);
    }

    private TextView tip(String s) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(13);
        t.setTextColor(Ui.c(ForgotActivity.this, "sub"));
        t.setLineSpacing(dp(3), 1f);
        t.setPadding(dp(4), dp(10), dp(4), dp(6));
        return t;
    }

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(ForgotActivity.this, "card"));
        bg.setStroke(dp(1), Ui.c(ForgotActivity.this, "line"));
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

    private LinearLayout field(EditText et) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, dp(8), 0, 0);
        LinearLayout f = new LinearLayout(this);
        f.addView(et, lp);
        return f;
    }

    private EditText input(String hint) {
        EditText et = new EditText(this);
        et.setHint(hint);
        et.setTextSize(13.5f);
        et.setTextColor(Ui.c(ForgotActivity.this, "text"));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(10));
        bg.setColor(Ui.c(ForgotActivity.this, "cardAlt"));
        bg.setStroke(dp(1), Ui.c(ForgotActivity.this, "line"));
        et.setBackground(bg);
        et.setPadding(dp(10), dp(9), dp(10), dp(9));
        return et;
    }

    private Button submitBtn(String text) {
        Button b = new Button(this);
        b.setText(text);
        b.setTextColor(Color.WHITE);
        b.setBackgroundColor(Ui.c(ForgotActivity.this, "brand"));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, dp(10), 0, 0);
        b.setLayoutParams(lp);
        return b;
    }

    private void toast(String m) {
        android.widget.Toast.makeText(this, m, android.widget.Toast.LENGTH_SHORT).show();
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
