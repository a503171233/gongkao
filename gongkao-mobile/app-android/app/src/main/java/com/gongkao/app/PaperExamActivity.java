package com.gongkao.app;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.HashMap;
import java.util.Map;

/**
 * 组卷/模考共用答题器（批次B）。
 * extras: paperId（必填）、durationMins（>0 = 模考计时，到 0 自动交卷）。
 * 流程：GET /smartexam/papers/{id} 拉题（作答中剥答案）→ 逐题作答（答题卡导航）
 *      → 交卷 POST …/submit → 成绩单（客观正确率 + 分类正确率 + 主观待批改提示）。
 */
public class PaperExamActivity extends BaseActivity {

    private Prefs prefs;
    private long paperId;
    private int durationMins;
    private String title = "";

    private JSONArray questions = new JSONArray();
    private final Map<String, String> answers = new HashMap<>();   // qid → 作答
    private int idx = 0;
    private boolean submitting = false;

    private TextView headTitle;
    private TextView timerText;
    private TextView progress;
    private TextView qBadge;
    private TextView qText;
    private LinearLayout optsBox;
    private EditText essayInput;
    private LinearLayout dotsBox;
    private ScrollView bodyScroll;
    private LinearLayout answerArea;      // 答题视图
    private LinearLayout resultArea;      // 成绩视图

    private final Handler timer = new Handler(Looper.getMainLooper());
    private long deadlineAt = 0;
    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            long left = deadlineAt - System.currentTimeMillis();
            if (left <= 0) {
                timerText.setText("⏰ 时间到，自动交卷");
                submit();
                return;
            }
            long m = left / 60000, s = (left / 1000) % 60;
            timerText.setText(String.format("剩余 %02d:%02d", m, s));
            timer.postDelayed(this, 1000);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);
        paperId = getIntent().getLongExtra("paperId", 0);
        durationMins = getIntent().getIntExtra("durationMins", 0);
        title = getIntent().getStringExtra("title");
        if (title == null) title = "";

        getWindow().requestFeature(android.view.Window.FEATURE_NO_TITLE);
        if (android.os.Build.VERSION.SDK_INT >= 23) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        }
        getWindow().setStatusBarColor(Ui.c(PaperExamActivity.this, "card"));

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(PaperExamActivity.this, "bg"));
        page.setPadding(dp(14), dp(10), dp(14), dp(10));

        // 顶栏：返回 + 标题 + 计时
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        TextView back = new TextView(this);
        back.setText("←");
        back.setTextSize(20);
        back.setTextColor(Ui.c(PaperExamActivity.this, "sub"));
        back.setOnClickListener(v -> {
            if (!submitting && questions.length() > 0) {
                Ui.dialogBuilder(this)
                        .setMessage("试卷还未交卷，确定退出吗？（已作答内容不会保留）")
                        .setPositiveButton("继续答题", null)
                        .setNegativeButton("退出", (d, w) -> finish())
                        .show();
            } else {
                finish();
            }
        });
        bar.addView(back);
        headTitle = new TextView(this);
        headTitle.setText("  " + (title.isEmpty() ? "试卷" : title));
        headTitle.setTextSize(15);
        headTitle.setTextColor(Ui.c(PaperExamActivity.this, "text"));
        headTitle.setTypeface(Typeface.DEFAULT_BOLD);
        headTitle.setSingleLine(true);
        headTitle.setEllipsize(android.text.TextUtils.TruncateAt.END);
        bar.addView(headTitle, rowWeight());
        timerText = new TextView(this);
        timerText.setTextSize(13);
        timerText.setTextColor(Ui.c(PaperExamActivity.this, "red"));
        bar.addView(timerText);
        page.addView(bar);

        progress = new TextView(this);
        progress.setTextSize(12.5f);
        progress.setTextColor(Ui.c(PaperExamActivity.this, "sub"));
        progress.setPadding(dp(2), dp(8), 0, dp(4));
        page.addView(progress);

        // 答题卡圆点行
        dotsBox = new LinearLayout(this);
        dotsBox.setOrientation(LinearLayout.HORIZONTAL);
        page.addView(dotsBox);

        // 答题区
        answerArea = new LinearLayout(this);
        answerArea.setOrientation(LinearLayout.VERTICAL);

        qBadge = new TextView(this);
        qBadge.setTextSize(12);
        qBadge.setTextColor(Ui.c(PaperExamActivity.this, "brandDark"));
        qBadge.setPadding(dp(2), dp(10), 0, dp(6));
        answerArea.addView(qBadge);

        bodyScroll = new ScrollView(this);
        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);

        qText = new TextView(this);
        qText.setTextSize(15.5f);
        qText.setTextColor(Ui.c(PaperExamActivity.this, "text"));
        qText.setLineSpacing(dp(3), 1f);
        body.addView(qText);

        optsBox = new LinearLayout(this);
        optsBox.setOrientation(LinearLayout.VERTICAL);
        optsBox.setPadding(0, dp(12), 0, 0);
        body.addView(optsBox);

        essayInput = new EditText(this);
        essayInput.setHint("在这里输入你的答案…");
        essayInput.setTextSize(14.5f);
        essayInput.setTextColor(Ui.c(PaperExamActivity.this, "text"));
        essayInput.setMinLines(5);
        essayInput.setGravity(Gravity.TOP);
        GradientDrawable eg = new GradientDrawable();
        eg.setCornerRadius(dp(10));
        eg.setColor(Ui.c(PaperExamActivity.this, "card"));
        eg.setStroke(dp(1), Ui.c(PaperExamActivity.this, "gold"));
        essayInput.setBackground(eg);
        essayInput.setPadding(dp(12), dp(10), dp(12), dp(10));
        body.addView(essayInput, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        bodyScroll.addView(body, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        answerArea.addView(bodyScroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        // 底部：上一题 / 交卷 / 下一题（Ui 实底按钮，涟漪反馈）
        LinearLayout btnBar = new LinearLayout(this);
        btnBar.setOrientation(LinearLayout.HORIZONTAL);
        TextView prev = Ui.btnSolid(this, "← 上一题", "line", "sub", 12);
        prev.setOnClickListener(v -> { saveCurrent(); if (idx > 0) { idx--; paint(); } });
        btnBar.addView(prev, rowWeight());
        TextView submitBtn = Ui.btnSolid(this, "交 卷", "red", "btnText", 12);
        submitBtn.setOnClickListener(v -> confirmSubmit());
        btnBar.addView(submitBtn, rowWeight());
        TextView next = Ui.btnSolid(this, "下一题 →", "brand", "btnText", 12);
        next.setOnClickListener(v -> { saveCurrent(); if (idx < questions.length() - 1) { idx++; paint(); } else toast("已是最后一题，可交卷"); });
        btnBar.addView(next, rowWeight());
        answerArea.addView(btnBar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        page.addView(answerArea, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        // 成绩视图（交卷后切换显示）
        resultArea = new LinearLayout(this);
        resultArea.setOrientation(LinearLayout.VERTICAL);
        resultArea.setVisibility(View.GONE);
        page.addView(resultArea, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        setContentView(page);
        load();
    }

    private void load() {
        progress.setText("正在载入试卷…");
        Api.paperDetail(prefs.token(), paperId, new Api.Cb() {
            @Override
            public void ok(JSONObject p) {
                questions = p.optJSONArray("questions") == null
                        ? new JSONArray() : p.optJSONArray("questions");
                title = p.optString("title", title);
                runOnUiThread(() -> {
                    if (questions.length() == 0) {
                        progress.setText("试卷为空或已评分");
                        return;
                    }
                    if (durationMins > 0) {
                        deadlineAt = System.currentTimeMillis() + durationMins * 60000L;
                        timer.postDelayed(tick, 1000);
                    }
                    paintDots();
                    paint();
                });
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> progress.setText("载入失败：" + m));
            }
        });
    }

    private void paintDots() {
        dotsBox.removeAllViews();
        for (int i = 0; i < questions.length(); i++) {
            JSONObject q = questions.optJSONObject(i);
            if (q == null) continue;
            TextView d = new TextView(this);
            String ans = answers.get(String.valueOf(q.optLong("id")));
            d.setText(String.valueOf(i + 1));
            d.setTextSize(11);
            d.setGravity(Gravity.CENTER);
            boolean answered = ans != null && !ans.isEmpty();
            boolean current = i == idx;
            GradientDrawable g = new GradientDrawable();
            g.setCornerRadius(dp(6));
            g.setColor(current ? Ui.c(PaperExamActivity.this, "brand") : answered ? Ui.c(PaperExamActivity.this, "brandSoft") : Ui.c(PaperExamActivity.this, "card"));
            g.setStroke(dp(1), current ? Ui.c(PaperExamActivity.this, "brand") : Ui.c(PaperExamActivity.this, "line"));
            d.setBackground(g);
            d.setTextColor(current ? Color.WHITE : answered ? Ui.c(PaperExamActivity.this, "brandDark") : Ui.c(PaperExamActivity.this, "faint"));
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(26), dp(26));
            lp.setMargins(dp(2), 0, dp(2), 0);
            final int target = i;
            Ui.pressScale(d);
            d.setOnClickListener(v -> { saveCurrent(); idx = target; paint(); });
            dotsBox.addView(d, lp);
        }
    }

    private void saveCurrent() {
        if (idx >= questions.length()) return;
        JSONObject q = questions.optJSONObject(idx);
        if (q == null) return;
        String type = q.optString("qtype", q.optString("type", "choice"));
        if ("essay".equals(type)) {
            String v = essayInput.getText() == null ? "" : essayInput.getText().toString().trim();
            if (!v.isEmpty()) answers.put(String.valueOf(q.optLong("id")), v);
        }
        // choice/judge 在点击时已存
    }

    private void paint() {
        if (idx >= questions.length()) return;
        JSONObject q = questions.optJSONObject(idx);
        if (q == null) return;
        String type = q.optString("qtype", q.optString("type", "choice"));
        qText.setText(q.optString("question", "（题目为空）"));
        qBadge.setText("第 " + (idx + 1) + "/" + questions.length() + " 题 · "
                + q.optString("category", "综合") + " · " + typeLabel(type));
        progress.setText("已作答 " + answers.size() + " / " + questions.length());

        optsBox.removeAllViews();
        essayInput.setVisibility("essay".equals(type) ? View.VISIBLE : View.GONE);
        if ("essay".equals(type)) {
            String saved = answers.get(String.valueOf(q.optLong("id")));
            essayInput.setText(saved == null ? "" : saved);
            essayInput.setOnFocusChangeListener((v, f) -> { if (!f) saveCurrent(); });
        } else if ("choice".equals(type)) {
            JSONArray opts = q.optJSONArray("options");
            if (opts != null) {
                String[] letters = {"A", "B", "C", "D", "E", "F"};
                for (int i = 0; i < opts.length(); i++) {
                    final String letter = letters[i];
                    TextView opt = new TextView(this);
                    opt.setTextSize(14.5f);
                    opt.setTextColor(Ui.c(PaperExamActivity.this, "text"));
                    opt.setText(letter + ". " + opts.optString(i, ""));
                    opt.setPadding(dp(14), dp(12), dp(14), dp(12));
                    String pickedNow = answers.get(String.valueOf(q.optLong("id")));
                    GradientDrawable bg = new GradientDrawable();
                    bg.setCornerRadius(dp(10));
                    boolean sel = letter.equals(pickedNow);
                    bg.setColor(sel ? Ui.c(PaperExamActivity.this, "brandSoft") : Ui.c(PaperExamActivity.this, "card"));
                    bg.setStroke(dp(2), sel ? Ui.c(PaperExamActivity.this, "brand") : Ui.c(PaperExamActivity.this, "line"));
                    opt.setBackground(Ui.rippleOn(this, bg, 10));
                    Ui.pressScale(opt);
                    LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                    lp.setMargins(0, 0, 0, dp(8));
                    bindChoice(q, letter, opt);
                    optsBox.addView(opt, lp);
                    Ui.enter(opt, i * 40L);
                }
            }
        } else if ("judge".equals(type)) {
            String[] pair = {"对", "错"};
            int jIdx = 0;
            for (final String val : pair) {
                TextView opt = new TextView(this);
                opt.setTextSize(15);
                opt.setTextColor(Ui.c(PaperExamActivity.this, "text"));
                opt.setGravity(Gravity.CENTER);
                opt.setText(val);
                opt.setPadding(dp(14), dp(14), dp(14), dp(14));
                String pickedNow = answers.get(String.valueOf(q.optLong("id")));
                GradientDrawable bg = new GradientDrawable();
                bg.setCornerRadius(dp(10));
                boolean sel = val.equals(pickedNow);
                bg.setColor(sel ? Ui.c(PaperExamActivity.this, "brandSoft") : Ui.c(PaperExamActivity.this, "card"));
                bg.setStroke(dp(2), sel ? Ui.c(PaperExamActivity.this, "brand") : Ui.c(PaperExamActivity.this, "line"));
                opt.setBackground(Ui.rippleOn(this, bg, 10));
                Ui.pressScale(opt);
                LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                lp.setMargins(0, 0, 0, dp(8));
                opt.setOnClickListener(v -> {
                    answers.put(String.valueOf(q.optLong("id")), val);
                    for (int j = 0; j < optsBox.getChildCount(); j++) {
                        View c = optsBox.getChildAt(j);
                        GradientDrawable g2 = new GradientDrawable();
                        g2.setCornerRadius(dp(10));
                        g2.setColor(c == opt ? Ui.c(PaperExamActivity.this, "brandSoft") : Ui.c(PaperExamActivity.this, "card"));
                        g2.setStroke(dp(2), c == opt ? Ui.c(PaperExamActivity.this, "brand") : Ui.c(PaperExamActivity.this, "line"));
                        c.setBackground(Ui.rippleOn(this, g2, 10));
                    }
                    paintDots();
                });
                optsBox.addView(opt, lp);
                Ui.enter(opt, jIdx * 40L);
                jIdx++;
            }
        }
        paintDots();
    }

    /** choice/judge 选项点击（paint 时动态创建，需在 paint 内绑定保存逻辑）。 */
    private void bindChoice(JSONObject q, String letter, TextView opt) {
        opt.setOnClickListener(v -> {
            answers.put(String.valueOf(q.optLong("id")), letter);
            for (int j = 0; j < optsBox.getChildCount(); j++) {
                View c = optsBox.getChildAt(j);
                GradientDrawable g2 = new GradientDrawable();
                g2.setCornerRadius(dp(10));
                boolean sel = letter.equals(answers.get(String.valueOf(q.optLong("id")))) && samePos(c, opt);
                g2.setColor(sel ? Ui.c(PaperExamActivity.this, "brandSoft") : Ui.c(PaperExamActivity.this, "card"));
                g2.setStroke(dp(2), sel ? Ui.c(PaperExamActivity.this, "brand") : Ui.c(PaperExamActivity.this, "line"));
                c.setBackground(Ui.rippleOn(this, g2, 10));
            }
            paintDots();
        });
    }

    private boolean samePos(View a, View b) {
        return a.equals(b);
    }

    private String typeLabel(String t) {
        if ("choice".equals(t)) return "单选";
        if ("judge".equals(t)) return "判断";
        if ("essay".equals(t)) return "简答";
        return t;
    }

    private void confirmSubmit() {
        saveCurrent();
        Ui.dialogBuilder(this)
                .setMessage("已作答 " + answers.size() + " / " + questions.length()
                        + " 题，确定交卷吗？未答题目按错误计。")
                .setPositiveButton("交卷", (d, w) -> submit())
                .setNegativeButton("继续答题", null)
                .show();
    }

    private void submit() {
        if (submitting) return;
        submitting = true;
        timer.removeCallbacks(tick);
        JSONObject ansBody = new JSONObject(answers);
        progress.setText("正在交卷判分…");
        Api.paperSubmit(prefs.token(), paperId, ansBody, new Api.Cb() {
            @Override
            public void ok(JSONObject r) {
                runOnUiThread(() -> paintResult(r));
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> {
                    submitting = false;
                    progress.setText("交卷失败：" + m + "（可重试）");
                });
            }
        });
    }

    private void paintResult(JSONObject r) {
        answerArea.setVisibility(View.GONE);
        resultArea.setVisibility(View.VISIBLE);
        dotsBox.setVisibility(View.GONE);
        progress.setVisibility(View.GONE);
        timerText.setVisibility(View.GONE);

        double score = r.optDouble("score", 0);
        int total = r.optInt("total", 0);
        int correct = r.optInt("correct", 0);
        int objective = r.optInt("objective", 0);
        int pending = r.optInt("essay_pending", 0);

        TextView big = new TextView(this);
        big.setText(String.format("%.0f 分", score * 100));
        big.setTextSize(44);
        big.setTextColor(score >= 0.6 ? Ui.c(PaperExamActivity.this, "green") : Ui.c(PaperExamActivity.this, "red"));
        big.setGravity(Gravity.CENTER);
        big.setTypeface(Typeface.DEFAULT_BOLD);
        resultArea.addView(big);
        Ui.enter(big, 0);

        TextView sum = new TextView(this);
        sum.setText("客观题 " + correct + " / " + objective + " 正确"
                + (pending > 0 ? "\n主观题 " + pending + " 题已进入 AI 批改队列" : ""));
        sum.setTextSize(15);
        sum.setTextColor(Ui.c(PaperExamActivity.this, "text"));
        sum.setGravity(Gravity.CENTER);
        sum.setPadding(0, dp(8), 0, dp(16));
        resultArea.addView(sum);
        Ui.enter(sum, 80);

        JSONArray cats = r.optJSONArray("by_category");
        if (cats != null) {
            for (int i = 0; i < cats.length(); i++) {
                JSONObject c = cats.optJSONObject(i);
                if (c == null) continue;
                double acc = c.optInt("total", 0) == 0 ? 0
                        : (double) c.optInt("correct", 0) / c.optInt("total", 1);
                TextView row = new TextView(this);
                row.setText("· " + c.optString("category", "") + "："
                        + c.optInt("correct", 0) + "/" + c.optInt("total", 0));
                row.setTextSize(14);
                row.setTextColor(Ui.c(PaperExamActivity.this, "sub"));
                row.setPadding(dp(28), dp(4), 0, dp(4));
                resultArea.addView(row);
                Ui.enter(row, 140 + i * 40L);
            }
        }

        TextView tip = new TextView(this);
        tip.setText("\n💪 继续加油！可回到刷题页查看学情报告");
        tip.setTextSize(13);
        tip.setTextColor(Ui.c(PaperExamActivity.this, "faint"));
        tip.setGravity(Gravity.CENTER);
        resultArea.addView(tip);
        Ui.enter(tip, 260);

        TextView done = Ui.btnPrimary(this, "完 成", 12);
        done.setOnClickListener(v -> finish());
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(dp(20), dp(24), dp(20), 0);
        resultArea.addView(done, lp);
        Ui.enter(done, 340);
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        timer.removeCallbacks(tick);
    }

    private LinearLayout.LayoutParams rowWeight() {
        return new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
    }

    private void toast(String s) {
        android.widget.Toast.makeText(this, s, android.widget.Toast.LENGTH_SHORT).show();
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
