package com.gongkao.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
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
 * 粉笔绑定页（批次E）：绑定状态（三同步计数）+ 账密/短信原生登录 + 会话轮询。
 * 滑块验证（need_drag）按用户决策跳网页处理：提示打开网页版「粉笔绑定」页完成。
 * 契约：GET /me/fenbi/binding → {binding{status,phone,check_ok,wrong_count,
 *          collect_count,mock_count},latest}
 *      POST /me/fenbi/login/password {account,password} / sms {phone} → {sid,state,hint}
 *      GET /me/fenbi/login/status?sid → {sid,state,shot,error,hint,phone,mode}
 *      POST /me/fenbi/login/code {sid,code} · cancel {sid} · POST /me/fenbi/unbind
 * state ∈ sending/need_drag/need_code/submitting/success/failed/cancelled。
 */
public class FenbiActivity extends BaseActivity {

    private static final String WEB_HOME = "https://a2a0641667069c341.app.workbuddy.host";

    private Prefs prefs;
    private LinearLayout body;
    private TextView status;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private String sid = "";
    private boolean polling = false;
    private LinearLayout loginArea;      // 登录表单区（need_code 时切换为输码）
    private EditText codeEt;
    private TextView codeBtn;
    private boolean analysisPolling = false;   // 批次 J：分析进度轮询
    private TextView progressText;             // 分析进度文案

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);

        getWindow().requestFeature(android.view.Window.FEATURE_NO_TITLE);
        if (android.os.Build.VERSION.SDK_INT >= 23) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        }
        getWindow().setStatusBarColor(Ui.c(FenbiActivity.this, "card"));

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(FenbiActivity.this, "bg"));

        page.addView(topBar("🪄 粉笔绑定", v -> {
            stopPolling();
            finish();
        }));

        status = new TextView(this);
        status.setTextSize(13);
        status.setTextColor(Ui.c(FenbiActivity.this, "faint"));
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
        loadBinding();
    }

    @Override
    protected void onDestroy() {
        stopPolling();
        super.onDestroy();
    }

    private View topBar(String title, View.OnClickListener back) {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Ui.c(FenbiActivity.this, "card"));
        bar.setPadding(dp(12), dp(10), dp(12), dp(10));
        TextView b = new TextView(this);
        b.setText("←");
        b.setTextSize(20);
        b.setTextColor(Ui.c(FenbiActivity.this, "sub"));
        b.setPadding(dp(4), dp(2), dp(14), dp(2));
        b.setOnClickListener(back);
        bar.addView(b);
        TextView t = new TextView(this);
        t.setText(title);
        t.setTextSize(17);
        t.setTextColor(Ui.c(FenbiActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(t);
        return bar;
    }

    // ------------------------------ 绑定状态 ------------------------------

    private void loadBinding() {
        status.setText("加载绑定状态…");
        Api.fenbiBinding(prefs.token(), new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                runUi(() -> {
                    status.setText("");
                    JSONObject b = d.optJSONObject("binding");
                    boolean bound = b != null && "bound".equals(b.optString("status"));
                    paint(bound, b, d.optJSONObject("latest"));
                });
            }

            @Override
            public void err(String m) {
                runUi(() -> {
                    status.setText("加载失败：" + m);
                    paint(false, null, null);
                });
            }
        });
    }

    private void paint(boolean bound, JSONObject b, JSONObject latest) {
        body.removeAllViews();

        // 状态卡
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(14), dp(14), dp(14));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(FenbiActivity.this, "card"));
        bg.setStroke(dp(1), Ui.c(FenbiActivity.this, "line"));
        card.setBackground(bg);

        TextView st = new TextView(this);
        st.setText(bound ? "✅ 已绑定粉笔账号"
                : (b != null && "expired".equals(b.optString("status"))
                        ? "⚠️ 绑定已失效，请重新绑定" : "粉笔账号未绑定"));
        st.setTextSize(15.5f);
        st.setTextColor(bound ? Ui.c(FenbiActivity.this, "green") : Ui.c(FenbiActivity.this, "brandDark"));
        st.setTypeface(Typeface.DEFAULT_BOLD);
        card.addView(st);
        if (bound && b != null && !b.optString("phone").isEmpty()) {
            TextView ph = new TextView(this);
            ph.setText("绑定手机：" + b.optString("phone"));
            ph.setTextSize(12.5f);
            ph.setTextColor(Ui.c(FenbiActivity.this, "sub"));
            ph.setPadding(0, dp(4), 0, 0);
            card.addView(ph);
        }
        if (bound && b != null) {
            // 批次 J：三同步数据从纯计数升级为可点开的明细入口
            card.addView(section("📊 已同步的粉笔数据"));
            card.addView(dataRow("📕 粉笔错题本", b.optInt("wrong_count") + " 道", "wrong"));
            card.addView(dataRow("⭐ 粉笔收藏题", b.optInt("collect_count") + " 道", "collect"));
            card.addView(dataRow("📝 粉笔模考试卷", b.optInt("mock_count") + " 套", "mock"));
            card.addView(paintReportCard(latest));
            // 批次28：Ui 实底按钮 + 自建确认弹窗（与系统设计统一）
            TextView unbind = Ui.btnSolid(FenbiActivity.this, "解除绑定",
                    "redSoft", "red", 12);
            unbind.setOnClickListener(v -> {
                LinearLayout msg = new LinearLayout(FenbiActivity.this);
                msg.setOrientation(LinearLayout.VERTICAL);
                TextView mt = new TextView(FenbiActivity.this);
                mt.setText("解绑后同步数据保留，确认解绑？");
                mt.setTextSize(14);
                mt.setTextColor(Ui.c(FenbiActivity.this, "text"));
                msg.addView(mt);
                Ui.centerDialog(FenbiActivity.this, "解除绑定", msg, "确认解绑", d2 -> {
                    Api.fenbiUnbind(prefs.token(), new Api.Cb() {
                        @Override
                        public void ok(JSONObject r) { runUi(() -> loadBinding()); }

                        @Override
                        public void err(String m) { toast(m); }
                    });
                }, "取消", null).show();
            });
            LinearLayout.LayoutParams ulp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            ulp.setMargins(0, dp(12), 0, 0);
            card.addView(unbind, ulp);
        } else {
            TextView intro = new TextView(this);
            intro.setText("绑定后可同步粉笔错题/收藏/模考记录，\n并由 AI 老师生成针对性提升计划。");
            intro.setTextSize(12.5f);
            intro.setTextColor(Ui.c(FenbiActivity.this, "faint"));
            intro.setLineSpacing(dp(3), 1f);
            intro.setPadding(0, dp(8), 0, 0);
            card.addView(intro);
        }
        body.addView(wrap(card));

        if (!bound) paintLoginArea();
    }

    // ------------------------------ 批次 J · 同步明细与任务书 ------------------------------

    /** 同步数据明细入口行（计数 + 右箭头，点击进 FenbiDataActivity）。 */
    private View dataRow(String title, String count, String type) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(10), dp(10), dp(10), dp(10));
        row.setBackground(Ui.rippleOn(this, Ui.round(this, "cardAlt", 10), 10));
        LinearLayout.LayoutParams rlp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rlp.setMargins(0, dp(8), 0, 0);
        row.setLayoutParams(rlp);
        Ui.pressScale(row);

        TextView t = new TextView(this);
        t.setText(title);
        t.setTextSize(14);
        t.setTextColor(Ui.c(FenbiActivity.this, "text"));
        row.addView(t, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        TextView c = new TextView(this);
        c.setText(count + "  ›");
        c.setTextSize(13);
        c.setTextColor(Ui.c(FenbiActivity.this, "brandDark"));
        row.addView(c);

        row.setOnClickListener(v -> startActivity(
                new android.content.Intent(this, FenbiDataActivity.class)
                        .putExtra("type", type)));
        return row;
    }

    /** AI 提升计划任务书卡：无→引导生成 / 进行中→进度 / 完成→完整渲染。 */
    private View paintReportCard(JSONObject latest) {
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        c.setPadding(dp(12), dp(12), dp(12), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(FenbiActivity.this, "cardAlt"));
        bg.setStroke(dp(1), Ui.c(FenbiActivity.this, "gold"));
        c.setBackground(bg);
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        clp.setMargins(0, dp(14), 0, 0);
        c.setLayoutParams(clp);

        c.addView(section("🎯 AI 提升计划任务书"));
        String status = latest == null ? "" : latest.optString("status");
        progressText = new TextView(this);
        progressText.setTextSize(12.5f);
        progressText.setTextColor(Ui.c(FenbiActivity.this, "sub"));
        progressText.setLineSpacing(dp(3), 1f);
        progressText.setPadding(0, dp(6), 0, 0);
        c.addView(progressText);

        if ("running".equals(status) || "queued".equals(status)) {
            progressText.setText("⏳ " + latest.optString("stage", "准备中") + " "
                    + latest.optInt("progress", 0) + "%（完成后自动同步到学习计划）");
            startAnalysisPolling();
        } else if ("failed".equals(status)) {
            progressText.setText("上次生成失败：" + latest.optString("error", "未知原因"));
            c.addView(analyzeBtn("重试生成"));
        } else if ("done".equals(status)) {
            JSONObject report = latest.optJSONObject("report");
            if (report == null || report.optString("summary").isEmpty()) {
                progressText.setText("任务书数据为空，请重新生成");
                c.addView(analyzeBtn("重新生成"));
            } else {
                progressText.setText("");
                renderReport(c, report, latest);
            }
        } else {
            progressText.setText("还没有生成过提升计划。基于你同步的粉笔错题，\n"
                    + "AI 老师会生成专属任务书（薄弱点分析 + 4 周任务）。\n"
                    + "生成完成后自动同步到「学习计划」。");
            c.addView(analyzeBtn("🚀 生成我的提升计划"));
        }
        return c;
    }

    /** 任务书完整渲染：诊断 / 薄弱点 / 建议 / 4 周任务 / 时间分配。 */
    private void renderReport(LinearLayout c, JSONObject report, JSONObject latest) {
        c.addView(note("📋 总体诊断", "text"));
        c.addView(note(report.optString("summary", ""), "sub"));

        org.json.JSONArray wps = report.optJSONArray("weak_points");
        if (wps != null && wps.length() > 0) {
            c.addView(note("🔻 薄弱知识点", "text"));
            int n = Math.min(wps.length(), 8);
            for (int i = 0; i < n; i++) {
                JSONObject w = wps.optJSONObject(i);
                if (w == null) continue;
                StringBuilder sb = new StringBuilder("· ").append(w.optString("name", ""))
                        .append("（").append(w.optString("module", "")).append("）");
                int lv = w.optInt("level", 0);
                if (lv > 0) sb.append(" 严重度 ").append(lv).append("/5");
                String reason = w.optString("reason", "");
                if (!reason.isEmpty()) sb.append("：").append(reason);
                c.addView(note(sb.toString(), "sub"));
            }
        }

        org.json.JSONArray adv = report.optJSONArray("advice");
        if (adv != null && adv.length() > 0) {
            c.addView(note("💡 学习建议", "text"));
            for (int i = 0; i < adv.length(); i++) {
                c.addView(note("· " + adv.optString(i), "sub"));
            }
        }

        org.json.JSONArray weeks = report.optJSONArray("weeks");
        if (weeks != null) {
            for (int i = 0; i < weeks.length(); i++) {
                JSONObject w = weeks.optJSONObject(i);
                if (w == null) continue;
                c.addView(note("📅 第" + (i + 1) + "周 · " + w.optString("theme", "")
                        + (w.optString("goal").isEmpty() ? "" : "（" + w.optString("goal") + "）"), "text"));
                org.json.JSONArray tasks = w.optJSONArray("tasks");
                if (tasks != null) {
                    for (int j = 0; j < tasks.length(); j++) {
                        JSONObject t = tasks.optJSONObject(j);
                        if (t == null) continue;
                        StringBuilder sb = new StringBuilder("  ✓ ").append(t.optString("slot", ""))
                                .append("｜").append(t.optString("title", ""));
                        String detail = t.optString("detail", "");
                        if (!detail.isEmpty()) sb.append("——").append(detail);
                        int mins = t.optInt("minutes", 0);
                        if (mins > 0) sb.append("（约").append(mins).append("分钟）");
                        c.addView(note(sb.toString(), "sub"));
                    }
                }
            }
        }

        org.json.JSONArray tp = report.optJSONArray("time_plan");
        if (tp != null && tp.length() > 0) {
            c.addView(note("⏱️ 模考时间分配建议", "text"));
            for (int i = 0; i < tp.length(); i++) {
                JSONObject t = tp.optJSONObject(i);
                if (t == null) continue;
                c.addView(note("· " + t.optString("module", "") + "："
                        + t.optString("issue", "") + " → " + t.optString("suggestion", ""), "sub"));
            }
        }

        // 操作按钮：同步到学习计划 + 重新生成
        LinearLayout btns = new LinearLayout(this);
        btns.setOrientation(LinearLayout.HORIZONTAL);
        TextView syncBtn = Ui.btnSecondary(this, "📅 同步到学习计划", 10);
        syncBtn.setOnClickListener(v -> Api.fenbiSyncPlan(prefs.token(), new Api.Cb() {
            @Override
            public void ok(JSONObject r) {
                runUi(() -> toast("✅ 已同步到学习计划，去「学习计划」页查看"));
            }

            @Override
            public void err(String m) { runUi(() -> toast(m)); }
        }));
        btns.addView(syncBtn, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView regen = Ui.btnSecondary(this, "🔄 重新生成", 10);
        regen.setOnClickListener(v -> confirmAnalyze());
        LinearLayout.LayoutParams glp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        glp.setMargins(dp(8), 0, 0, 0);
        btns.addView(regen, glp);
        LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        blp.setMargins(0, dp(10), 0, 0);
        c.addView(btns, blp);

        String created = latest.optString("created_at", "");
        if (!created.isEmpty()) {
            c.addView(note("（生成于 " + created.replace("T", " ").replace("Z", "")
                    + "，重新生成冷却 6 小时）", "faint"));
        }
    }

    private TextView note(String text, String colorKey) {
        TextView t = new TextView(this);
        t.setText(text);
        t.setTextSize(12.5f);
        t.setTextColor(Ui.c(FenbiActivity.this, colorKey));
        t.setLineSpacing(dp(2), 1f);
        t.setPadding(0, dp(3), 0, dp(3));
        return t;
    }

    private TextView analyzeBtn(String text) {
        // 批次28：原生 Button → Ui 设计系统主按钮（涟漪/圆角/双主题）
        TextView btn = Ui.btnPrimary(this, text, 12);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, dp(10), 0, 0);
        btn.setLayoutParams(lp);
        btn.setOnClickListener(v -> confirmAnalyze());
        return btn;
    }

    private void confirmAnalyze() {
        // 批次28：自建确认弹窗（与设计系统统一）
        LinearLayout msg = new LinearLayout(this);
        msg.setOrientation(LinearLayout.VERTICAL);
        TextView mt = new TextView(this);
        mt.setText("将重新拉取粉笔错题/收藏/模考数据，并生成 AI 提升计划任务书"
                + "（冷却 6 小时）。\n\n同步完成后，错题与收藏会自动写入系统错题本/收藏夹。");
        mt.setTextSize(14);
        mt.setTextColor(Ui.c(this, "text"));
        mt.setLineSpacing(dp(2), 1f);
        msg.addView(mt);
        Ui.centerDialog(this, "同步并生成提升计划", msg,
                "开始", d -> startAnalysis(), "取消", null).show();
    }

    private void startAnalysis() {
        status.setText("正在启动同步与生成…");
        Api.fenbiAnalyze(prefs.token(), new Api.Cb() {
            @Override
            public void ok(JSONObject r) {
                runUi(() -> {
                    status.setText("");
                    toast("已开始，生成完成后自动同步到学习计划");
                    loadBinding();
                });
            }

            @Override
            public void err(String m) {
                runUi(() -> status.setText("启动失败：" + m));
            }
        });
    }

    private void startAnalysisPolling() {
        if (analysisPolling) return;
        analysisPolling = true;
        handler.postDelayed(this::analysisPollTick, 3000);
    }

    private void analysisPollTick() {
        if (!analysisPolling) return;
        Api.fenbiAnalysisLatest(prefs.token(), new Api.Cb() {
            @Override
            public void ok(JSONObject r) {
                runUi(() -> {
                    JSONObject la = r.optJSONObject("latest");
                    if (la == null) return;
                    String st = la.optString("status");
                    if ("running".equals(st) || "queued".equals(st)) {
                        if (progressText != null) {
                            progressText.setText("⏳ " + la.optString("stage", "准备中")
                                    + " " + la.optInt("progress", 0)
                                    + "%（完成后自动同步到学习计划）");
                        }
                        handler.postDelayed(FenbiActivity.this::analysisPollTick, 3000);
                    } else if ("done".equals(st)) {
                        analysisPolling = false;
                        toast("🎉 任务书生成完成，已同步到学习计划");
                        loadBinding();
                    } else if ("failed".equals(st)) {
                        analysisPolling = false;
                        loadBinding();
                    } else {
                        handler.postDelayed(FenbiActivity.this::analysisPollTick, 3000);
                    }
                });
            }

            @Override
            public void err(String m) {
                runUi(() -> handler.postDelayed(FenbiActivity.this::analysisPollTick, 5000));
            }
        });
    }

    // ------------------------------ 登录表单 ------------------------------

    private void paintLoginArea() {
        loginArea = new LinearLayout(this);
        loginArea.setOrientation(LinearLayout.VERTICAL);

        TextView lt = new TextView(this);
        lt.setText("登录你的粉笔账号");
        lt.setTextSize(14);
        lt.setTextColor(Ui.c(FenbiActivity.this, "text"));
        lt.setTypeface(Typeface.DEFAULT_BOLD);
        lt.setPadding(dp(4), dp(14), 0, dp(8));
        loginArea.addView(lt);

        // 账密登录
        LinearLayout pCard = card();
        pCard.addView(section("🔑 账号密码登录"));
        EditText accEt = input("手机号或邮箱");
        pCard.addView(field(accEt));
        EditText pwdEt = input("粉笔登录密码");
        pwdEt.setInputType(android.text.InputType.TYPE_CLASS_TEXT
                | android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        pCard.addView(field(pwdEt));
        TextView pwdBtn = submitBtn("登录并绑定");
        pwdBtn.setOnClickListener(v -> {
            String acc = accEt.getText().toString().trim();
            String pwd = pwdEt.getText().toString();
            if (acc.isEmpty() || pwd.isEmpty()) {
                toast("请填写账号和密码");
                return;
            }
            pwdBtn.setEnabled(false);
            status.setText("正在打开粉笔登录…");
            Api.fenbiLoginPassword(prefs.token(), acc, pwd, new Api.Cb() {
                @Override
                public void ok(JSONObject s) {
                    runUi(() -> {
                        pwdBtn.setEnabled(true);
                        sid = s.optString("sid");
                        startPolling();
                    });
                }

                @Override
                public void err(String m) {
                    runUi(() -> {
                        pwdBtn.setEnabled(true);
                        status.setText("发起失败：" + m);
                    });
                }
            });
        });
        pCard.addView(pwdBtn);
        loginArea.addView(wrap(pCard));

        // 短信登录
        LinearLayout sCard = card();
        sCard.addView(section("📱 短信验证码登录"));
        EditText phoneEt = input("11 位手机号");
        sCard.addView(field(phoneEt));
        TextView smsBtn = submitBtn("发送验证码并登录");
        smsBtn.setOnClickListener(v -> {
            String ph = phoneEt.getText().toString().replaceAll("\\D", "");
            if (ph.length() != 11) {
                toast("手机号格式不正确");
                return;
            }
            smsBtn.setEnabled(false);
            status.setText("正在发送短信…");
            Api.fenbiLoginSms(prefs.token(), ph, new Api.Cb() {
                @Override
                public void ok(JSONObject s) {
                    runUi(() -> {
                        smsBtn.setEnabled(true);
                        sid = s.optString("sid");
                        startPolling();
                    });
                }

                @Override
                public void err(String m) {
                    runUi(() -> {
                        smsBtn.setEnabled(true);
                        status.setText("发起失败：" + m);
                    });
                }
            });
        });
        sCard.addView(smsBtn);
        loginArea.addView(wrap(sCard));

        TextView tip = new TextView(this);
        tip.setText("· 密码仅用于本次登录，不保存不落库\n· 触发滑块验证时会引导你到网页版完成\n· 也可以在网页版「粉笔绑定」用扫码/Cookie 方式");
        tip.setTextSize(11.5f);
        tip.setTextColor(Ui.c(FenbiActivity.this, "faint"));
        tip.setLineSpacing(dp(3), 1f);
        tip.setPadding(dp(6), dp(10), 0, 0);
        loginArea.addView(tip);

        body.addView(loginArea);
    }

    // ------------------------------ 轮询 ------------------------------

    private void startPolling() {
        stopPolling();
        polling = true;
        pollTick();
    }

    private void stopPolling() {
        polling = false;
        handler.removeCallbacksAndMessages(null);
    }

    private void pollTick() {
        if (!polling || sid.isEmpty()) return;
        Api.fenbiLoginStatus(prefs.token(), sid, new Api.Cb() {
            @Override
            public void ok(JSONObject s) {
                runUi(() -> handleState(s));
            }

            @Override
            public void err(String m) {
                runUi(() -> status.setText("状态查询失败：" + m));
            }
        });
        handler.postDelayed(this::pollTick, 2000);
    }

    private void handleState(JSONObject s) {
        String state = s.optString("state");
        String hint = s.optString("hint", "");
        switch (state) {
            case "sending":
            case "submitting":
                status.setText(hint.isEmpty() ? "处理中…" : hint);
                break;
            case "need_code":
                stopPolling();
                status.setText(hint.isEmpty() ? "验证码已发送，请填写" : hint);
                showCodeInput(s.optString("phone", ""));
                break;
            case "need_drag":
                stopPolling();
                showDragGuide();
                break;
            case "success":
                stopPolling();
                status.setText("");
                toast("🎉 绑定成功");
                loadBinding();
                break;
            case "failed":
                stopPolling();
                status.setText("登录失败：" + s.optString("error", "未知原因"));
                break;
            case "cancelled":
                stopPolling();
                status.setText("会话已取消");
                break;
            default:
                break;
        }
    }

    /** need_code：就地把登录区替换为验证码输入。 */
    private void showCodeInput(String phoneMasked) {
        if (loginArea != null) body.removeView(loginArea);
        LinearLayout c = card();
        c.addView(section("📩 输入短信验证码"
                + (phoneMasked.isEmpty() ? "" : "（" + phoneMasked + "）")));
        codeEt = input("6 位验证码");
        c.addView(field(codeEt));
        codeBtn = submitBtn("提交验证码");
        codeBtn.setOnClickListener(v -> {
            String code = codeEt.getText().toString().trim();
            if (code.length() < 4) {
                toast("请输入完整验证码");
                return;
            }
            codeBtn.setEnabled(false);
            Api.fenbiLoginCode(prefs.token(), sid, code, new Api.Cb() {
                @Override
                public void ok(JSONObject r) {
                    runUi(() -> {
                        codeBtn.setEnabled(true);
                        status.setText("正在登录…");
                        startPolling();
                    });
                }

                @Override
                public void err(String m) {
                    runUi(() -> {
                        codeBtn.setEnabled(true);
                        status.setText("提交失败：" + m);
                    });
                }
            });
        });
        c.addView(codeBtn);
        TextView cancel = Ui.btnSecondary(this, "取消本次登录", 12);   // 批次28
        cancel.setOnClickListener(v -> {
            Api.fenbiLoginCancel(prefs.token(), sid, new Api.Cb() {
                @Override
                public void ok(JSONObject r) { runUi(() -> loadBinding()); }

                @Override
                public void err(String m) { runUi(() -> loadBinding()); }
            });
        });
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        clp.setMargins(0, dp(6), 0, 0);
        c.addView(cancel, clp);
        loginArea = new LinearLayout(this);
        loginArea.setOrientation(LinearLayout.VERTICAL);
        loginArea.addView(wrap(c));
        body.addView(loginArea);
    }

    /** need_drag：按用户决策跳网页完成滑块（App 端不做截图拖动）。 */
    private void showDragGuide() {
        status.setText("");
        // 批次28：自建指引弹窗（主按钮复制网址，次按钮知道了）
        LinearLayout msg = new LinearLayout(this);
        msg.setOrientation(LinearLayout.VERTICAL);
        TextView mt = new TextView(this);
        mt.setText("粉笔要求真人滑块验证。\n\n请用浏览器打开网页版：\n" + WEB_HOME
                + "\n\n登录后进入「我的 → 粉笔绑定」，\n完成滑块即可自动绑定成功，然后回到本页刷新。");
        mt.setTextSize(14);
        mt.setTextColor(Ui.c(this, "text"));
        mt.setLineSpacing(dp(2), 1f);
        msg.addView(mt);
        Ui.centerDialog(this, "🛡 需要滑块验证", msg,
                "复制网址", d -> {
                    android.content.ClipboardManager cm =
                            (android.content.ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                    cm.setPrimaryClip(android.content.ClipData.newPlainText(
                            "url", WEB_HOME));
                    toast("网址已复制");
                }, "知道了", null).show();
        loadBinding();   // 回到初始态
    }

    // ------------------------------ 工具 ------------------------------

    private void runUi(Runnable r) {
        runOnUiThread(r);
    }

    private TextView section(String s) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(14);
        t.setTextColor(Ui.c(FenbiActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        return t;
    }

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(FenbiActivity.this, "card"));
        bg.setStroke(dp(1), Ui.c(FenbiActivity.this, "line"));
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
        et.setTextColor(Ui.c(FenbiActivity.this, "text"));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(10));
        bg.setColor(Ui.c(FenbiActivity.this, "cardAlt"));
        bg.setStroke(dp(1), Ui.c(FenbiActivity.this, "line"));
        et.setBackground(bg);
        et.setPadding(dp(10), dp(9), dp(10), dp(9));
        return et;
    }

    private TextView submitBtn(String text) {
        // 批次28：原生 Button → Ui 设计系统主按钮
        TextView b = Ui.btnPrimary(this, text, 12);
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
