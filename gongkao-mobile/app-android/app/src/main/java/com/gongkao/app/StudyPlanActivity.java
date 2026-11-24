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
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 学习计划页（批次D）：当前 7 天冲刺计划（掌握度驱动）+ 逐项打卡 + 重新生成。
 * 契约：GET /study/plan {plan:{plan_id,days:[{day,title,items:[{name,module,note,done}]}]}}
 *      POST /study/plan/generate · POST /study/plan/checkin {plan_id,day,idx}。
 */
public class StudyPlanActivity extends BaseActivity {

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
        getWindow().setStatusBarColor(Ui.c(StudyPlanActivity.this, "card"));

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(StudyPlanActivity.this, "bg"));

        page.addView(topBar("📅 学习计划", v -> finish()));

        status = new TextView(this);
        status.setTextSize(13);
        status.setTextColor(Ui.c(StudyPlanActivity.this, "faint"));
        status.setPadding(dp(16), dp(10), dp(16), dp(4));
        page.addView(status);

        ScrollView scroll = new ScrollView(this);
        body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(14), dp(4), dp(14), dp(24));
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
        bar.setBackgroundColor(Ui.c(StudyPlanActivity.this, "card"));
        bar.setPadding(dp(12), dp(10), dp(12), dp(10));
        TextView b = new TextView(this);
        b.setText("←");
        b.setTextSize(20);
        b.setTextColor(Ui.c(StudyPlanActivity.this, "sub"));
        b.setPadding(dp(4), dp(2), dp(14), dp(2));
        b.setOnClickListener(back);
        bar.addView(b);
        TextView t = new TextView(this);
        t.setText(title);
        t.setTextSize(17);
        t.setTextColor(Ui.c(StudyPlanActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(t);
        return bar;
    }

    private void load() {
        status.setText("加载中…");
        Api.studyPlanGet(prefs.token(), prefs.teacherId(), new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                JSONObject plan = d.optJSONObject("plan");
                runOnUiThread(() -> {
                    body.removeAllViews();
                    if (plan == null || plan.optJSONArray("days") == null
                            || plan.optJSONArray("days").length() == 0) {
                        status.setText("暂无生效计划");
                        Button gen = new Button(StudyPlanActivity.this);
                        gen.setText("🎯 生成 7 天冲刺计划");
                        gen.setTextColor(Color.WHITE);
                        gen.setBackgroundColor(Ui.c(StudyPlanActivity.this, "brand"));
                        gen.setOnClickListener(v -> generate());
                        body.addView(gen, new LinearLayout.LayoutParams(
                                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
                        TextView tip = new TextView(StudyPlanActivity.this);
                        tip.setText("\n计划由你的练习掌握度驱动：薄弱知识点优先、\n新知识点穿插，每天 3 项任务，打卡追踪。");
                        tip.setTextSize(13);
                        tip.setTextColor(Ui.c(StudyPlanActivity.this, "faint"));
                        tip.setGravity(Gravity.CENTER);
                        body.addView(tip);
                        return;
                    }
                    status.setText("计划进行中 · Day " + currentDay(plan));
                    paintPlan(plan);
                });
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> status.setText("加载失败：" + m));
            }
        });
    }

    private int currentDay(JSONObject plan) {
        JSONArray days = plan.optJSONArray("days");
        int cur = 1;
        outer:
        for (int i = 0; i < days.length(); i++) {
            JSONArray items = days.optJSONObject(i).optJSONArray("items");
            if (items == null) continue;
            for (int j = 0; j < items.length(); j++) {
                if (!items.optJSONObject(j).optBoolean("done")) {
                    cur = days.optJSONObject(i).optInt("day", i + 1);
                    break outer;
                }
            }
            cur = days.optJSONObject(i).optInt("day", i + 1);
        }
        return cur;
    }

    private void paintPlan(JSONObject plan) {
        long planId = plan.optLong("plan_id");
        JSONArray days = plan.optJSONArray("days");
        // 批次 J：粉笔 AI 提升计划任务书摘要卡（随计划 payload 下发）
        JSONObject fr = plan.optJSONObject("fenbi_report");
        if (fr != null && fr.optString("summary", "").isEmpty() == false) {
            body.addView(wrapFenbiCard(fr));
        }
        for (int i = 0; i < days.length(); i++) {
            JSONObject day = days.optJSONObject(i);
            if (day == null) continue;
            LinearLayout card = new LinearLayout(this);
            card.setOrientation(LinearLayout.VERTICAL);
            card.setPadding(dp(14), dp(12), dp(14), dp(12));
            GradientDrawable bg = new GradientDrawable();
            bg.setCornerRadius(dp(12));
            bg.setColor(Ui.c(StudyPlanActivity.this, "card"));
            bg.setStroke(dp(1), Ui.c(StudyPlanActivity.this, "line"));
            card.setBackground(bg);

            TextView t = new TextView(this);
            t.setText(day.optString("title", "Day " + day.optInt("day"))
                    + (day.optString("focus").isEmpty() ? "" : " · " + day.optString("focus")));
            t.setTextSize(14.5f);
            t.setTextColor(Ui.c(StudyPlanActivity.this, "text"));
            t.setTypeface(Typeface.DEFAULT_BOLD);
            card.addView(t);

            JSONArray items = day.optJSONArray("items");
            if (items != null) {
                for (int j = 0; j < items.length(); j++) {
                    JSONObject it = items.optJSONObject(j);
                    if (it == null) continue;
                    LinearLayout row = new LinearLayout(this);
                    row.setOrientation(LinearLayout.HORIZONTAL);
                    row.setGravity(Gravity.CENTER_VERTICAL);
                    row.setPadding(0, dp(8), 0, dp(2));

                    TextView check = new TextView(this);
                    boolean done = it.optBoolean("done");
                    check.setText(done ? "☑" : "☐");
                    check.setTextSize(19);
                    check.setTextColor(done ? Ui.c(StudyPlanActivity.this, "green") : Ui.c(StudyPlanActivity.this, "faint"));
                    check.setPadding(0, 0, dp(10), 0);
                    row.addView(check);

                    TextView name = new TextView(this);
                    name.setText(it.optString("name", "") + "  " + it.optString("note", ""));
                    name.setTextSize(14);
                    name.setTextColor(done ? Ui.c(StudyPlanActivity.this, "faint") : Ui.c(StudyPlanActivity.this, "text"));
                    name.setPadding(0, 0, 0, 0);
                    row.addView(name, new LinearLayout.LayoutParams(0,
                            ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

                    if (!done) {
                        final int d = day.optInt("day", i + 1);
                        final int idx = j;
                        check.setOnClickListener(v ->
                                Api.studyPlanCheckin(prefs.token(), planId, d, idx, new Api.Cb() {
                                    @Override
                                    public void ok(JSONObject r) { runOnUiThread(() -> load()); }

                                    @Override
                                    public void err(String m) {
                                        runOnUiThread(() -> android.widget.Toast
                                                .makeText(StudyPlanActivity.this,
                                                        "打卡失败：" + m,
                                                        android.widget.Toast.LENGTH_SHORT).show());
                                    }
                                }));
                        name.setOnClickListener(v -> {
                            android.widget.Toast.makeText(this,
                                    "点前面的 ☐ 完成打卡",
                                    android.widget.Toast.LENGTH_SHORT).show();
                        });
                    }
                    card.addView(row);
                }
            }
            LinearLayout wrap = new LinearLayout(this);
            wrap.setPadding(dp(2), dp(5), dp(2), dp(5));
            wrap.addView(card, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
            body.addView(wrap);
        }

        Button regen = new Button(this);
        regen.setText("🔄 重新生成计划（旧计划作废）");
        regen.setTextColor(Ui.c(StudyPlanActivity.this, "sub"));
        regen.setBackgroundColor(Ui.c(StudyPlanActivity.this, "line"));
        regen.setOnClickListener(v -> generate());
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(dp(6), dp(14), dp(6), 0);
        body.addView(regen, lp);
    }

    /** 批次 J：粉笔任务书摘要卡（总体诊断 + 薄弱点 Top3 + 去粉笔页看完整版）。 */
    private View wrapFenbiCard(JSONObject fr) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(StudyPlanActivity.this, "cardAlt"));
        bg.setStroke(dp(1), Ui.c(StudyPlanActivity.this, "gold"));
        card.setBackground(bg);

        TextView title = new TextView(this);
        title.setText("🎯 AI 提升计划（来自粉笔错题分析）");
        title.setTextSize(14.5f);
        title.setTextColor(Ui.c(StudyPlanActivity.this, "brandDark"));
        title.setTypeface(Typeface.DEFAULT_BOLD);
        card.addView(title);

        TextView summary = new TextView(this);
        summary.setText(fr.optString("summary", ""));
        summary.setTextSize(12.5f);
        summary.setTextColor(Ui.c(StudyPlanActivity.this, "text"));
        summary.setLineSpacing(dp(3), 1f);
        summary.setPadding(0, dp(6), 0, 0);
        card.addView(summary);

        JSONArray wps = fr.optJSONArray("weak_points");
        if (wps != null && wps.length() > 0) {
            StringBuilder sb = new StringBuilder("薄弱点：");
            int n = Math.min(wps.length(), 3);
            for (int i = 0; i < n; i++) {
                JSONObject w = wps.optJSONObject(i);
                if (w == null) continue;
                if (i > 0) sb.append(" / ");
                sb.append(w.optString("name", ""));
            }
            TextView weak = new TextView(this);
            weak.setText(sb.toString());
            weak.setTextSize(12);
            weak.setTextColor(Ui.c(StudyPlanActivity.this, "red"));
            weak.setPadding(0, dp(6), 0, 0);
            card.addView(weak);
        }

        TextView go = new TextView(this);
        go.setText("完整任务书与周任务见下方「第X周」卡片 ›");
        go.setTextSize(11.5f);
        go.setTextColor(Ui.c(StudyPlanActivity.this, "faint"));
        go.setPadding(0, dp(6), 0, 0);
        card.addView(go);

        LinearLayout wrap = new LinearLayout(this);
        wrap.setPadding(dp(2), dp(6), dp(2), dp(3));
        wrap.addView(card, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return wrap;
    }

    private void generate() {
        status.setText("正在根据你的掌握度生成计划…");
        Api.studyPlanGenerate(prefs.token(), prefs.teacherId(), new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                runOnUiThread(() -> load());
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> status.setText("生成失败：" + m));
            }
        });
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
