package com.gongkao.app;

import android.app.Activity;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 单题练习页（批次B）：抽题 → 作答 → 自动判分 → 解析 → 下一题循环。
 * intent extras: mode("" 普通 / today 今日智能 / wrong 只刷错题), category 分类专项。
 * 契约：GET /practice/next · POST /practice/submit（答错自动进错题本，后端闭环）。
 */
public class PracticeActivity extends BaseActivity {

    private Prefs prefs;
    private String mode = "";
    private String category = "";
    private long redoQuestionId = 0;   // 错题重练：指定题库题
    private int doneCount = 0;

    private TextView headBadge;
    private TextView qText;
    private LinearLayout optsBox;
    private EditText essayInput;
    private TextView resultCard;
    private TextView submitBtn;
    private TextView nextBtn;
    private ScrollView scroll;

    private JSONObject current;          // /practice/next 响应
    private String picked = "";          // 已选选项
    private boolean submitting = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);
        mode = getIntent().getStringExtra("mode");
        category = getIntent().getStringExtra("category");
        redoQuestionId = getIntent().getLongExtra("question_id", 0);
        if (mode == null) mode = "";
        if (category == null) category = "";

        getWindow().requestFeature(android.view.Window.FEATURE_NO_TITLE);
        if (android.os.Build.VERSION.SDK_INT >= 23) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        }
        getWindow().setStatusBarColor(Ui.c(PracticeActivity.this, "card"));

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(PracticeActivity.this, "bg"));
        page.setPadding(dp(14), dp(10), dp(14), dp(10));

        // 顶栏：返回 + 标题
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        TextView back = new TextView(this);
        back.setText("← 返回");
        back.setTextSize(14);
        back.setTextColor(Ui.c(PracticeActivity.this, "sub"));
        back.setOnClickListener(v -> finish());
        bar.addView(back);
        TextView title = new TextView(this);
        title.setText("  " + (mode.equals("wrong") ? "只刷错题" : mode.equals("today") ? "今日智能练习"
                : category.isEmpty() ? "随机练习" : "专项 · " + category));
        title.setTextSize(15);
        title.setTextColor(Ui.c(PracticeActivity.this, "text"));
        title.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(title);
        page.addView(bar);

        // 分类/难度徽标行
        headBadge = new TextView(this);
        headBadge.setTextSize(12);
        headBadge.setTextColor(Ui.c(PracticeActivity.this, "brandDark"));
        headBadge.setPadding(dp(2), dp(6), 0, dp(6));
        page.addView(headBadge);

        // 题干（可滚动区）
        scroll = new ScrollView(this);
        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);

        qText = new TextView(this);
        qText.setTextSize(15.5f);
        qText.setTextColor(Ui.c(PracticeActivity.this, "text"));
        qText.setLineSpacing(dp(3), 1f);
        body.addView(qText);

        optsBox = new LinearLayout(this);
        optsBox.setOrientation(LinearLayout.VERTICAL);
        optsBox.setPadding(0, dp(12), 0, 0);
        body.addView(optsBox);

        // 简答题输入（essay/knowledge_point 用）
        essayInput = new EditText(this);
        essayInput.setHint("在这里输入你的答案…");
        essayInput.setTextSize(14.5f);
        essayInput.setTextColor(Ui.c(PracticeActivity.this, "text"));
        essayInput.setMinLines(4);
        essayInput.setGravity(Gravity.TOP);
        GradientDrawable eg = new GradientDrawable();
        eg.setCornerRadius(dp(10));
        eg.setColor(Ui.c(PracticeActivity.this, "card"));
        eg.setStroke(dp(1), Ui.c(PracticeActivity.this, "gold"));
        essayInput.setBackground(eg);
        essayInput.setPadding(dp(12), dp(10), dp(12), dp(10));
        essayInput.setVisibility(View.GONE);
        body.addView(essayInput, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        // 结果卡（判分后展示）
        resultCard = new TextView(this);
        resultCard.setTextSize(14);
        resultCard.setTextColor(Ui.c(PracticeActivity.this, "text"));
        resultCard.setLineSpacing(dp(3), 1f);
        resultCard.setPadding(dp(12), dp(10), dp(12), dp(10));
        resultCard.setVisibility(View.GONE);
        body.addView(resultCard, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        scroll.addView(body, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        page.addView(scroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        // 底部按钮行：提交 / 下一题（Ui 实底按钮，涟漪反馈）
        LinearLayout btnBar = new LinearLayout(this);
        btnBar.setOrientation(LinearLayout.HORIZONTAL);
        submitBtn = Ui.btnSolid(this, "提 交", "brand", "btnText", 12);
        submitBtn.setOnClickListener(v -> submit());
        btnBar.addView(submitBtn, rowWeight());

        nextBtn = Ui.btnSolid(this, "下一题 →", "hero", "onHero", 12);
        nextBtn.setVisibility(View.GONE);
        nextBtn.setOnClickListener(v -> loadNext());
        btnBar.addView(nextBtn, rowWeight());
        page.addView(btnBar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        setContentView(page);
        loadNext();
    }

    private void loadNext() {
        picked = "";
        resultCard.setVisibility(View.GONE);
        optsBox.removeAllViews();
        essayInput.setVisibility(View.GONE);
        essayInput.setText("");
        nextBtn.setVisibility(View.GONE);
        submitBtn.setVisibility(View.VISIBLE);
        qText.setText("正在抽题…");
        headBadge.setText(category.isEmpty() ? "第 " + (doneCount + 1) + " 题" : "第 " + (doneCount + 1) + " 题 · " + category);

        Api.practiceNext(prefs.token(), prefs.teacherId(), category, mode, redoQuestionId, new Api.Cb() {
            @Override
            public void ok(JSONObject q) {
                current = q;
                runOnUiThread(() -> paint(q));
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> {
                    qText.setText("抽题失败：" + m);
                    headBadge.setText("");
                });
            }
        });
    }

    private void paint(JSONObject q) {
        String type = q.optString("type", "choice");
        qText.setText(q.optString("question", "（题目为空）"));
        String badge = "第 " + (doneCount + 1) + " 题 · " + q.optString("category", "综合")
                + " · " + qtypeLabel(type) + " · 难度 " + q.optInt("difficulty", 3);
        if (q.has("today_note")) badge = q.optString("today_note", "");
        headBadge.setText(badge);

        if ("choice".equals(type)) {
            JSONArray opts = q.optJSONArray("options");
            if (opts != null) {
                String[] letters = {"A", "B", "C", "D", "E", "F"};
                for (int i = 0; i < opts.length(); i++) {
                    final String letter = letters[i];
                    String text = opts.optString(i, "");
                    TextView opt = new TextView(this);
                    opt.setTextSize(14.5f);
                    opt.setTextColor(Ui.c(PracticeActivity.this, "text"));
                    opt.setText(letter + ". " + text);
                    opt.setPadding(dp(14), dp(12), dp(14), dp(12));
                    GradientDrawable bg = new GradientDrawable();
                    bg.setCornerRadius(dp(10));
                    bg.setColor(Ui.c(PracticeActivity.this, "card"));
                    bg.setStroke(dp(1), Ui.c(PracticeActivity.this, "line"));
                    opt.setBackground(Ui.rippleOn(this, bg, 10));
                    Ui.pressScale(opt);
                    LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                    lp.setMargins(0, 0, 0, dp(8));
                    opt.setOnClickListener(v -> {
                        picked = letter;
                        for (int j = 0; j < optsBox.getChildCount(); j++) {
                            View c = optsBox.getChildAt(j);
                            GradientDrawable g2 = new GradientDrawable();
                            g2.setCornerRadius(dp(10));
                            g2.setColor(c == opt ? Ui.c(PracticeActivity.this, "brandSoft") : Ui.c(PracticeActivity.this, "card"));
                            g2.setStroke(dp(2), c == opt ? Ui.c(PracticeActivity.this, "brand") : Ui.c(PracticeActivity.this, "line"));
                            c.setBackground(Ui.rippleOn(this, g2, 10));
                        }
                    });
                    optsBox.addView(opt, lp);
                    Ui.enter(opt, i * 40L);
                }
            }
        } else if ("judge".equals(type)) {
            String[] pair = {"对", "错"};
            int jIdx = 0;
            for (String s : pair) {
                final String val = s;
                TextView opt = new TextView(this);
                opt.setTextSize(15);
                opt.setTextColor(Ui.c(PracticeActivity.this, "text"));
                opt.setGravity(Gravity.CENTER);
                opt.setText(val);
                opt.setPadding(dp(14), dp(14), dp(14), dp(14));
                GradientDrawable bg = new GradientDrawable();
                bg.setCornerRadius(dp(10));
                bg.setColor(Ui.c(PracticeActivity.this, "card"));
                bg.setStroke(dp(1), Ui.c(PracticeActivity.this, "line"));
                opt.setBackground(Ui.rippleOn(this, bg, 10));
                Ui.pressScale(opt);
                LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                lp.setMargins(0, 0, 0, dp(8));
                opt.setOnClickListener(v -> {
                    picked = val;
                    for (int j = 0; j < optsBox.getChildCount(); j++) {
                        View c = optsBox.getChildAt(j);
                        GradientDrawable g2 = new GradientDrawable();
                        g2.setCornerRadius(dp(10));
                        g2.setColor(c == opt ? Ui.c(PracticeActivity.this, "brandSoft") : Ui.c(PracticeActivity.this, "card"));
                        g2.setStroke(dp(2), c == opt ? Ui.c(PracticeActivity.this, "brand") : Ui.c(PracticeActivity.this, "line"));
                        c.setBackground(Ui.rippleOn(this, g2, 10));
                    }
                });
                optsBox.addView(opt, lp);
                Ui.enter(opt, jIdx * 40L);
                jIdx++;
            }
        } else {
            essayInput.setVisibility(View.VISIBLE);   // essay / knowledge_point
        }

        if (!q.optBoolean("has_answer", true)) {
            resultCard.setVisibility(View.VISIBLE);
            resultCard.setTextColor(Ui.c(PracticeActivity.this, "faint"));
            resultCard.setText("ℹ️ 该题暂无标准答案，作答后将进入待评分队列");
        }
    }

    private void submit() {
        if (submitting || current == null) return;
        String type = current.optString("type", "choice");
        String answer = picked;
        if ("essay".equals(type) || "knowledge_point".equals(type)) {
            answer = essayInput.getText() == null ? "" : essayInput.getText().toString().trim();
        }
        if (answer.isEmpty()) {
            toast("先作答再提交哦");
            return;
        }
        submitting = true;
        submitBtn.setText("判分中…");
        Api.practiceSubmit(prefs.token(), current.optString("question", ""), answer,
                prefs.teacherId(), current.optLong("id"), new Api.Cb() {
                    @Override
                    public void ok(JSONObject s) {
                        runOnUiThread(() -> paintResult(s));
                    }

                    @Override
                    public void err(String m) {
                        runOnUiThread(() -> {
                            submitting = false;
                            submitBtn.setText("提 交");
                            toast("提交失败：" + m);
                        });
                    }
                });
    }

    private void paintResult(JSONObject s) {
        submitting = false;
        doneCount++;
        submitBtn.setVisibility(View.GONE);
        nextBtn.setVisibility(View.VISIBLE);
        nextBtn.setText(doneCount >= 10 ? "再来一轮 →" : "下一题 →");

        double score = s.optDouble("score", -1);
        boolean auto = s.optBoolean("auto_graded", false);
        StringBuilder sb = new StringBuilder();
        if (auto && score >= 0.99) {
            sb.append("✅ 回答正确！");
            resultCard.setTextColor(Ui.c(PracticeActivity.this, "green"));
        } else if (auto) {
            sb.append("❌ 回答错误");
            resultCard.setTextColor(Ui.c(PracticeActivity.this, "red"));
        } else if (score == -1) {
            sb.append("📥 已提交，进入 AI 批改队列");
            resultCard.setTextColor(Ui.c(PracticeActivity.this, "brandDark"));
        } else {
            sb.append("✅ 已记录本次作答");
            resultCard.setTextColor(Ui.c(PracticeActivity.this, "text"));
        }
        String fb = s.optString("feedback", "");
        String an = s.optString("analysis", "");
        if (!fb.isEmpty()) sb.append("\n").append(fb);
        if (!an.isEmpty()) sb.append("\n\n📖 解析：").append(an);
        sb.append("\n\n分类：").append(s.optString("category", "综合"));
        resultCard.setVisibility(View.VISIBLE);
        resultCard.setText(sb.toString());
        scroll.post(() -> scroll.smoothScrollTo(0, resultCard.getTop()));
    }

    private String qtypeLabel(String t) {
        if ("choice".equals(t)) return "单选";
        if ("judge".equals(t)) return "判断";
        if ("essay".equals(t)) return "简答";
        return "知识点";
    }

    private void toast(String s) {
        android.widget.Toast.makeText(this, s, android.widget.Toast.LENGTH_SHORT).show();
    }

    private LinearLayout.LayoutParams rowWeight() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        lp.setMargins(dp(4), 0, dp(4), 0);
        return lp;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
