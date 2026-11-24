package com.gongkao.app;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

/** 登录页（原生表单，POST /api/login，登录即自动注册）。 */
public class LoginActivity extends BaseActivity {

    private EditText user;
    private EditText pass;
    private TextView btn;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setGravity(Gravity.CENTER_HORIZONTAL);
        page.setBackgroundColor(Ui.c(LoginActivity.this, "bg"));
        page.setPadding(dp(40), dp(80), dp(40), dp(40));

        TextView logo = new TextView(this);
        logo.setText("GK");
        logo.setTextColor(Color.WHITE);
        logo.setTextSize(34);
        logo.setGravity(Gravity.CENTER);
        GradientDrawable lg = new GradientDrawable();
        lg.setCornerRadius(dp(28));
        lg.setColor(Ui.c(LoginActivity.this, "brand"));
        logo.setBackground(lg);
        page.addView(logo, new LinearLayout.LayoutParams(dp(96), dp(96)));

        TextView title = new TextView(this);
        title.setText("公考学习");
        title.setTextSize(24);
        title.setTextColor(Ui.c(LoginActivity.this, "text"));
        title.setGravity(Gravity.CENTER);
        title.setPadding(0, dp(18), 0, dp(4));
        page.addView(title);

        TextView sub = new TextView(this);
        sub.setText("登录后开始向老师提问");
        sub.setTextSize(13);
        sub.setTextColor(Ui.c(LoginActivity.this, "faint"));
        sub.setGravity(Gravity.CENTER);
        sub.setPadding(0, 0, 0, dp(36));
        page.addView(sub);

        user = field("账号");
        page.addView(user, fieldLp());
        pass = field("密码");
        pass.setInputType(android.text.InputType.TYPE_CLASS_TEXT
                | android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        page.addView(pass, fieldLp());

        btn = new TextView(this);
        btn.setText("登  录");
        btn.setTextColor(Color.WHITE);
        btn.setTextSize(17);
        btn.setGravity(Gravity.CENTER);
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(14));
        bg.setColor(Ui.c(LoginActivity.this, "brand"));
        btn.setBackground(bg);
        btn.setPadding(0, dp(14), 0, dp(14));
        btn.setOnClickListener(v -> doLogin());
        LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        bp.setMargins(0, dp(12), 0, 0);
        page.addView(btn, bp);

        // 批次E：忘记密码（密保问题自助找回）
        TextView forgot = new TextView(this);
        forgot.setText("忘记密码？密保找回");
        forgot.setTextSize(13);
        forgot.setTextColor(Ui.c(LoginActivity.this, "purple"));
        forgot.setGravity(Gravity.CENTER);
        forgot.setPadding(0, dp(14), 0, 0);
        forgot.setOnClickListener(v ->
                startActivity(new android.content.Intent(this, ForgotActivity.class)));
        page.addView(forgot, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        setContentView(page);
    }

    private void doLogin() {
        final String u = user.getText() == null ? "" : user.getText().toString().trim();
        final String p = pass.getText() == null ? "" : pass.getText().toString().trim();
        if (u.isEmpty() || p.isEmpty()) {
            toast("请输入账号和密码");
            return;
        }
        btn.setText("登录中…");
        btn.setEnabled(false);
        Api.login(u, p, new Api.Cb() {
            @Override
            public void ok(JSONObject data) {
                Prefs prefs = new Prefs(LoginActivity.this);
                prefs.setToken(data.optString("token", ""));
                prefs.setUsername(data.optString("username", u));
                prefs.setRole(data.optString("role", "free"));
                prefs.setQuotaLeft(data.optInt("quota_left", -1));
                runOnUiThread(() -> {
                    toast("欢迎，" + data.optString("username", u));
                    setResult(RESULT_OK);
                    finish();
                });
            }

            @Override
            public void err(String message) {
                runOnUiThread(() -> {
                    btn.setText("登  录");
                    btn.setEnabled(true);
                    toast(message);
                });
            }
        });
    }

    private EditText field(String hint) {
        EditText e = new EditText(this);
        e.setHint(hint);
        e.setTextSize(15);
        e.setTextColor(Ui.c(LoginActivity.this, "text"));
        e.setHintTextColor(Ui.c(LoginActivity.this, "faint"));
        e.setSingleLine(true);
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(LoginActivity.this, "card"));
        bg.setStroke(dp(1), Ui.c(LoginActivity.this, "line"));
        e.setBackground(bg);
        e.setPadding(dp(16), dp(13), dp(16), dp(13));
        return e;
    }

    private LinearLayout.LayoutParams fieldLp() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, 0, 0, dp(14));
        return lp;
    }

    private void toast(String s) {
        android.widget.Toast.makeText(this, s, android.widget.Toast.LENGTH_SHORT).show();
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
