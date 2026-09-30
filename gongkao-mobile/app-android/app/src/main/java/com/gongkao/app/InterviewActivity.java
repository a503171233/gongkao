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

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 面试练习页（批次D）：五大模块出题 → 作答 → AI 点评（得分/维度/建议/参考提纲）。
 * 契约：POST /me/interview/question {module,difficulty:"standard"} →
 *   {id,module,module_name,question}
 *      POST /me/interview/answer {qid,answer} →
 *   {score(百分制),review[{dim,comment}],advice[],outline}
 * module 枚举：comprehensive/organize/emergency/relations/expression。
 */
public class InterviewActivity extends BaseActivity {

    private static final String[][] MODULES = {
            {"comprehensive", "🧭", "综合分析", "社会现象、政策观点类"},
            {"organize", "📋", "组织协调", "活动策划、调研组织类"},
            {"emergency", "🚨", "应急应变", "突发事件、紧急处置类"},
            {"relations", "🤝", "人际关系", "同事协作、上下级沟通类"},
            {"expression", "🗣", "言语表达", "情景模拟、现场讲话类"},
    };

    private Prefs prefs;
    private LinearLayout body;
    private TextView status;
    private String mode = "idle";            // idle / question / result
    private String curModule = "comprehensive";
    private String[] curModuleMeta = MODULES[0];
    private JSONObject curQuestion;
    private EditText answerEt;
    private Button submitBtn;
    private LinearLayout questionCard;       // 作答区容器（出题后填充）

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);

        getWindow().requestFeature(android.view.Window.FEATURE_NO_TITLE);
        if (android.os.Build.VERSION.SDK_INT >= 23) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        }
        getWindow().setStatusBarColor(Ui.c(InterviewActivity.this, "card"));

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(InterviewActivity.this, "bg"));

        page.addView(topBar("🎤 面试练习", v -> onBackPressed()));

        status = new TextView(this);
        status.setTextSize(13);
        status.setTextColor(Ui.c(InterviewActivity.this, "faint"));
        status.setGravity(Gravity.CENTER);
        status.setPadding(dp(16), dp(8), dp(16), dp(4));
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
        paintIdle();
    }

    @Override
    public void onBackPressed() {
        if ("idle".equals(mode)) finish();
        else paintIdle();
    }

    private View topBar(String title, View.OnClickListener back) {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Ui.c(InterviewActivity.this, "card"));
        bar.setPadding(dp(12), dp(10), dp(12), dp(10));
        TextView b = new TextView(this);
        b.setText("←");
        b.setTextSize(20);
        b.setTextColor(Ui.c(InterviewActivity.this, "sub"));
        b.setPadding(dp(4), dp(2), dp(14), dp(2));
        b.setOnClickListener(back);
        bar.addView(b);
        TextView t = new TextView(this);
        t.setText(title);
        t.setTextSize(17);
        t.setTextColor(Ui.c(InterviewActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(t);
        return bar;
    }

    // ------------------------------ 模块选择 ------------------------------

    private void paintIdle() {
        mode = "idle";
        status.setText("");
        body.removeAllViews();

        TextView intro = new TextView(this);
        intro.setText("选择一个模块，AI 老师出题并逐维度点评\n（综合分析 / 组织协调 / 应急应变 / 人际关系 / 言语表达）");
        intro.setTextSize(12.5f);
        intro.setTextColor(Ui.c(InterviewActivity.this, "faint"));
        intro.setLineSpacing(dp(3), 1f);
        body.addView(intro);

        for (String[] m : MODULES) {
            LinearLayout card = new LinearLayout(this);
            card.setOrientation(LinearLayout.HORIZONTAL);
            card.setGravity(Gravity.CENTER_VERTICAL);
            card.setPadding(dp(14), dp(13), dp(14), dp(13));
            GradientDrawable bg = new GradientDrawable();
            bg.setCornerRadius(dp(12));
            bg.setColor(Ui.c(InterviewActivity.this, "card"));
            bg.setStroke(dp(1), Ui.c(InterviewActivity.this, "line"));
            card.setBackground(bg);

            TextView icon = new TextView(this);
            icon.setText(m[1]);
            icon.setTextSize(24);
            card.addView(icon);
            LinearLayout mid = new LinearLayout(this);
            mid.setOrientation(LinearLayout.VERTICAL);
            LinearLayout.LayoutParams mlp = new LinearLayout.LayoutParams(0,
                    ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            mlp.setMargins(dp(12), 0, 0, 0);
            TextView name = new TextView(this);
            name.setText(m[2]);
            name.setTextSize(15);
            name.setTextColor(Ui.c(InterviewActivity.this, "text"));
            name.setTypeface(Typeface.DEFAULT_BOLD);
            mid.addView(name);
            TextView desc = new TextView(this);
            desc.setText(m[3]);
            desc.setTextSize(11.5f);
            desc.setTextColor(Ui.c(InterviewActivity.this, "faint"));
            desc.setPadding(0, dp(2), 0, 0);
            mid.addView(desc);
            card.addView(mid, mlp);
            TextView go = new TextView(this);
            go.setText("开始 ›");
            go.setTextSize(13);
            go.setTextColor(Ui.c(InterviewActivity.this, "brandDark"));
            card.addView(go);

            final String[] meta = m;
            card.setOnClickListener(v -> {
                curModule = meta[0];
                curModuleMeta = meta;
                fetchQuestion();
            });
            body.addView(wrap(card));
        }
    }

    // ------------------------------ 出题 + 作答 ------------------------------

    private void fetchQuestion() {
        mode = "question";
        status.setText("AI 老师正在出题…");
        body.removeAllViews();
        Api.interviewQuestion(prefs.token(), curModule, new Api.Cb() {
            @Override
            public void ok(JSONObject q) {
                runOnUiThread(() -> {
                    status.setText("");
                    curQuestion = q;
                    paintQuestion(q);
                });
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> {
                    status.setText("出题失败：" + m);
                    Button retry = new Button(InterviewActivity.this);
                    retry.setText("重试");
                    retry.setOnClickListener(v -> fetchQuestion());
                    body.addView(retry);
                });
            }
        });
    }

    private void paintQuestion(JSONObject q) {
        body.removeAllViews();

        // 模块标签
        TextView tag = new TextView(this);
        tag.setText(curModuleMeta[1] + " " + q.optString("module_name", curModuleMeta[2]));
        tag.setTextSize(12.5f);
        tag.setTextColor(Ui.c(InterviewActivity.this, "brandDark"));
        GradientDrawable tbg = new GradientDrawable();
        tbg.setCornerRadius(dp(6));
        tbg.setColor(Ui.c(InterviewActivity.this, "brandSoft"));
        tag.setBackground(tbg);
        tag.setPadding(dp(8), dp(3), dp(8), dp(3));
        body.addView(tag, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        // 题目卡
        LinearLayout qCard = new LinearLayout(this);
        qCard.setOrientation(LinearLayout.VERTICAL);
        qCard.setPadding(dp(14), dp(14), dp(14), dp(14));
        GradientDrawable qbg = new GradientDrawable();
        qbg.setCornerRadius(dp(12));
        qbg.setColor(Ui.c(InterviewActivity.this, "card"));
        qbg.setStroke(dp(1), Ui.c(InterviewActivity.this, "line"));
        qCard.setBackground(qbg);
        TextView qt = new TextView(this);
        qt.setText(q.optString("question", ""));
        qt.setTextSize(15.5f);
        qt.setTextColor(Ui.c(InterviewActivity.this, "text"));
        qt.setLineSpacing(dp(4), 1f);
        qCard.addView(qt);
        body.addView(wrap(qCard));

        // 作答区
        LinearLayout aCard = new LinearLayout(this);
        aCard.setOrientation(LinearLayout.VERTICAL);
        aCard.setPadding(dp(14), dp(12), dp(14), dp(12));
        aCard.setBackground(qbg);
        questionCard = aCard;

        TextView hint = new TextView(this);
        hint.setText("💬 请口头作答后把要点写下来，或直接写下你的答题思路：");
        hint.setTextSize(12);
        hint.setTextColor(Ui.c(InterviewActivity.this, "faint"));
        aCard.addView(hint);

        answerEt = new EditText(this);
        answerEt.setHint("从「表态 / 分析 / 对策」等层面组织你的回答…");
        answerEt.setTextSize(13.5f);
        answerEt.setTextColor(Ui.c(InterviewActivity.this, "text"));
        answerEt.setMinLines(6);
        answerEt.setGravity(Gravity.TOP);
        GradientDrawable ebg = new GradientDrawable();
        ebg.setCornerRadius(dp(10));
        ebg.setColor(Ui.c(InterviewActivity.this, "cardAlt"));
        ebg.setStroke(dp(1), Ui.c(InterviewActivity.this, "line"));
        answerEt.setBackground(ebg);
        LinearLayout.LayoutParams elp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        elp.setMargins(0, dp(8), 0, 0);
        aCard.addView(answerEt, elp);

        submitBtn = new Button(this);
        submitBtn.setText("📨 提交 AI 点评");
        submitBtn.setTextColor(Color.WHITE);
        submitBtn.setBackgroundColor(Ui.c(InterviewActivity.this, "brand"));
        submitBtn.setOnClickListener(v -> submitAnswer());
        LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        slp.setMargins(0, dp(10), 0, 0);
        aCard.addView(submitBtn, slp);
        body.addView(wrap(aCard));

        // 换一题 / 返回
        LinearLayout foot = new LinearLayout(this);
        foot.setOrientation(LinearLayout.HORIZONTAL);
        foot.setGravity(Gravity.CENTER);
        foot.setPadding(0, dp(12), 0, 0);
        TextView another = new TextView(this);
        another.setText("🔄 换一题");
        another.setTextSize(13.5f);
        another.setTextColor(Ui.c(InterviewActivity.this, "purple"));
        another.setPadding(dp(16), dp(6), dp(16), dp(6));
        another.setOnClickListener(v -> fetchQuestion());
        foot.addView(another);
        TextView back = new TextView(this);
        back.setText("返回模块");
        back.setTextSize(13.5f);
        back.setTextColor(Ui.c(InterviewActivity.this, "faint"));
        back.setPadding(dp(16), dp(6), dp(16), dp(6));
        back.setOnClickListener(v -> paintIdle());
        foot.addView(back);
        body.addView(foot);
    }

    private void submitAnswer() {
        String answer = answerEt.getText().toString().trim();
        if (answer.length() < 20) {
            android.widget.Toast.makeText(this,
                    "回答太短了，至少写 20 字把思路讲清楚", android.widget.Toast.LENGTH_SHORT).show();
            return;
        }
        long qid = curQuestion != null ? curQuestion.optLong("id") : 0;
        submitBtn.setEnabled(false);
        submitBtn.setText("AI 点评中，约需 30~60 秒…");
        status.setText("老师正在逐维度点评…");
        Api.interviewAnswer(prefs.token(), qid, answer, new Api.Cb() {
            @Override
            public void ok(JSONObject r) {
                runOnUiThread(() -> paintResult(r, answer));
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> {
                    submitBtn.setEnabled(true);
                    submitBtn.setText("📨 提交 AI 点评");
                    status.setText("点评失败：" + m);
                });
            }
        });
    }

    // ------------------------------ 点评结果 ------------------------------

    private void paintResult(JSONObject r, String myAnswer) {
        mode = "result";
        status.setText("");
        body.removeAllViews();

        // 我的作答回显
        LinearLayout my = card();
        TextView myT = section("我的作答");
        my.addView(myT);
        TextView myA = new TextView(this);
        myA.setText(myAnswer);
        myA.setTextSize(12.5f);
        myA.setTextColor(Ui.c(InterviewActivity.this, "sub"));
        myA.setLineSpacing(dp(2), 1f);
        myA.setPadding(0, dp(6), 0, 0);
        my.addView(myA);
        body.addView(wrap(my));

        // 得分卡
        LinearLayout hero = new LinearLayout(this);
        hero.setOrientation(LinearLayout.VERTICAL);
        hero.setGravity(Gravity.CENTER_HORIZONTAL);
        hero.setPadding(dp(16), dp(16), dp(16), dp(14));
        GradientDrawable hg = new GradientDrawable();
        hg.setCornerRadius(dp(14));
        hg.setColor(0xFF2B2620);
        hero.setBackground(hg);
        TextView score = new TextView(this);
        score.setText(trimNum(r.optDouble("score", 0)) + " 分");
        score.setTextSize(36);
        score.setTextColor(Ui.c(InterviewActivity.this, "heroGold"));
        score.setTypeface(Typeface.DEFAULT_BOLD);
        score.setGravity(Gravity.CENTER);
        hero.addView(score);
        TextView sL = new TextView(this);
        sL.setText("百分制 · 综合面试表现评定");
        sL.setTextSize(11);
        sL.setTextColor(0xFFBBAF9A);
        sL.setPadding(0, dp(3), 0, 0);
        hero.addView(sL);
        body.addView(wrap(hero));

        // 维度点评
        JSONArray review = r.optJSONArray("review");
        if (review != null && review.length() > 0) {
            LinearLayout rc = card();
            rc.addView(section("维度点评"));
            for (int i = 0; i < review.length(); i++) {
                JSONObject dim = review.optJSONObject(i);
                if (dim == null) continue;
                TextView d = new TextView(this);
                d.setText("【" + dim.optString("dim", "") + "】" + dim.optString("comment", ""));
                d.setTextSize(13);
                d.setTextColor(Ui.c(InterviewActivity.this, "text"));
                d.setPadding(0, dp(8), 0, 0);
                d.setLineSpacing(dp(2), 1f);
                rc.addView(d);
            }
            body.addView(wrap(rc));
        }

        // 提升建议
        JSONArray advice = r.optJSONArray("advice");
        if (advice != null && advice.length() > 0) {
            LinearLayout ac = card();
            ac.addView(section("提升建议"));
            for (int i = 0; i < advice.length(); i++) {
                TextView a = new TextView(this);
                a.setText((i + 1) + ". " + advice.optString(i));
                a.setTextSize(12.5f);
                a.setTextColor(Ui.c(InterviewActivity.this, "sub"));
                a.setPadding(0, dp(5), 0, 0);
                a.setLineSpacing(dp(2), 1f);
                ac.addView(a);
            }
            body.addView(wrap(ac));
        }

        // 参考提纲
        String outline = r.optString("outline");
        if (!outline.isEmpty()) {
            LinearLayout oc = card();
            oc.addView(section("参考提纲"));
            TextView o = new TextView(this);
            o.setText(outline);
            o.setTextSize(13);
            o.setTextColor(Ui.c(InterviewActivity.this, "green"));
            o.setLineSpacing(dp(3), 1f);
            o.setPadding(0, dp(6), 0, 0);
            oc.addView(o);
            body.addView(wrap(oc));
        }

        // 下一题
        Button next = new Button(this);
        next.setText("🎤 下一题");
        next.setTextColor(Ui.c(InterviewActivity.this, "sub"));
        next.setBackgroundColor(Ui.c(InterviewActivity.this, "line"));
        next.setOnClickListener(v -> fetchQuestion());
        LinearLayout.LayoutParams nlp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        nlp.setMargins(dp(2), dp(14), dp(2), 0);
        body.addView(next, nlp);
    }

    // ------------------------------ 通用小组件 ------------------------------

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(InterviewActivity.this, "card"));
        bg.setStroke(dp(1), Ui.c(InterviewActivity.this, "line"));
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

    private TextView section(String title) {
        TextView t = new TextView(this);
        t.setText(title);
        t.setTextSize(14.5f);
        t.setTextColor(Ui.c(InterviewActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        return t;
    }

    private String trimNum(double v) {
        if (v == Math.floor(v) && !Double.isInfinite(v)) return String.valueOf((long) v);
        return String.format(java.util.Locale.US, "%.1f", v);
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
