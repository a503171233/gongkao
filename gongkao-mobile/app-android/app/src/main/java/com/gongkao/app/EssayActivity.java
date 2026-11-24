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
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 申论批改页（批次D）：四题型选择 → 材料+作答提交 → AI 批改报告（总分/维度/批注/建议）+ 历史。
 * 契约：POST /me/essay/grade {essay_type,title?,prompt?,essay} →
 *   {total,dims[{name,score,full,comment}],notes[{quote,note}],advice[],summary,
 *    essay_type,type_name,full_score,word_count,id}
 *      GET /me/essay/history → {items[{id,essay_type,title,total,full_score,word_count,
 *          summary,created_at}]}
 * essay_type 枚举：summary(20) / countermeasure(20) / applied(40) / composition(40)，
 * 作答至少 50 字（后端校验，前端同步提示）。
 */
public class EssayActivity extends BaseActivity {

    private static final String[][] TYPES = {
            {"summary", "概括归纳", "20"},
            {"countermeasure", "对策题", "20"},
            {"applied", "应用文", "40"},
            {"composition", "大作文", "40"},
    };

    private Prefs prefs;
    private LinearLayout body;
    private TextView status;
    private String mode = "edit";           // edit / result / hist
    private String curType = "summary";
    private String[] typeLabels = TYPES[0];
    private JSONObject lastResult;          // 最近一次批改结果
    private EditText titleEt;
    private EditText promptEt;
    private EditText essayEt;
    private TextView[] typeChips;
    private Button submitBtn;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);

        getWindow().requestFeature(android.view.Window.FEATURE_NO_TITLE);
        if (android.os.Build.VERSION.SDK_INT >= 23) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        }
        getWindow().setStatusBarColor(Ui.c(EssayActivity.this, "card"));

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(EssayActivity.this, "bg"));

        page.addView(topBar("✍️ 申论批改", v -> onBackPressed()));

        status = new TextView(this);
        status.setTextSize(13);
        status.setTextColor(Ui.c(EssayActivity.this, "faint"));
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
        paintEdit();
    }

    @Override
    public void onBackPressed() {
        if ("edit".equals(mode)) finish();
        else paintEdit();
    }

    private View topBar(String title, View.OnClickListener back) {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Ui.c(EssayActivity.this, "card"));
        bar.setPadding(dp(12), dp(10), dp(12), dp(10));
        TextView b = new TextView(this);
        b.setText("←");
        b.setTextSize(20);
        b.setTextColor(Ui.c(EssayActivity.this, "sub"));
        b.setPadding(dp(4), dp(2), dp(14), dp(2));
        b.setOnClickListener(back);
        bar.addView(b);
        TextView t = new TextView(this);
        t.setText(title);
        t.setTextSize(17);
        t.setTextColor(Ui.c(EssayActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(t, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView hist = new TextView(this);
        hist.setText("📜 历史");
        hist.setTextSize(13);
        hist.setTextColor(Ui.c(EssayActivity.this, "brandDark"));
        hist.setPadding(dp(8), dp(4), dp(4), dp(4));
        hist.setOnClickListener(v -> paintHistory());
        bar.addView(hist);
        return bar;
    }

    // ------------------------------ 编辑视图 ------------------------------

    private void paintEdit() {
        mode = "edit";
        status.setText("");
        body.removeAllViews();

        // 题型 chips
        TextView typeLabel = new TextView(this);
        typeLabel.setText("题型（满分不同）");
        typeLabel.setTextSize(13);
        typeLabel.setTextColor(Ui.c(EssayActivity.this, "sub"));
        body.addView(typeLabel);
        HorizontalScrollView hs = new HorizontalScrollView(this);
        hs.setHorizontalScrollBarEnabled(false);
        LinearLayout chips = new LinearLayout(this);
        chips.setPadding(0, dp(8), 0, 0);
        typeChips = new TextView[TYPES.length];
        for (int i = 0; i < TYPES.length; i++) {
            final String[] t = TYPES[i];
            final int fi = i;   // lambda 捕获需 effectively final
            TextView chip = new TextView(this);
            chip.setText(t[1] + " " + t[2] + "分");
            chip.setTextSize(13);
            chip.setPadding(dp(14), dp(8), dp(14), dp(8));
            chip.setClickable(true);
            GradientDrawable bg = new GradientDrawable();
            bg.setCornerRadius(dp(18));
            chip.setBackground(bg);
            chip.setOnClickListener(v -> {
                curType = t[0];
                typeLabels = t;
                for (int j = 0; j < typeChips.length; j++) {
                    GradientDrawable b2 = new GradientDrawable();
                    b2.setCornerRadius(dp(18));
                    boolean sel = j == fi;
                    b2.setColor(sel ? Ui.c(EssayActivity.this, "brand") : Ui.c(EssayActivity.this, "card"));
                    b2.setStroke(dp(1), sel ? Ui.c(EssayActivity.this, "brand") : Ui.c(EssayActivity.this, "line"));
                    typeChips[j].setBackground(b2);
                    typeChips[j].setTextColor(sel ? Color.WHITE : Ui.c(EssayActivity.this, "sub"));
                }
            });
            typeChips[i] = chip;
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            lp.setMargins(0, 0, dp(8), 0);
            chips.addView(chip, lp);
        }
        hs.addView(chips);
        body.addView(hs);
        typeChips[0].performClick();   // 默认选中第一个

        // 标题（可选）
        body.addView(fieldLabel("题目（选填）"));
        titleEt = input("给这篇作答起个名字，如「2026 国考概括题」");
        body.addView(titleEt);

        // 材料（可选）
        body.addView(fieldLabel("题目材料 / 题干（选填，帮助 AI 更准批改）"));
        promptEt = input("粘贴题干、给定材料原文…");
        promptEt.setMinLines(3);
        body.addView(promptEt);

        // 作答
        body.addView(fieldLabel("你的作答（至少 50 字）"));
        essayEt = input("在此输入你的申论作答…");
        essayEt.setMinLines(8);
        essayEt.setGravity(Gravity.TOP);
        body.addView(essayEt);

        submitBtn = new Button(this);
        submitBtn.setText("📨 提交 AI 批改");
        submitBtn.setTextColor(Color.WHITE);
        submitBtn.setBackgroundColor(Ui.c(EssayActivity.this, "brand"));
        submitBtn.setOnClickListener(v -> submit());
        LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        slp.setMargins(dp(2), dp(12), dp(2), 0);
        body.addView(submitBtn, slp);

        TextView tip = new TextView(this);
        tip.setText("批改维度：要点覆盖 / 对策可行 / 条理逻辑 / 语言规范\n支持概括、对策、应用文、大作文四类，AI 逐句批注");
        tip.setTextSize(12);
        tip.setTextColor(Ui.c(EssayActivity.this, "faint"));
        tip.setGravity(Gravity.CENTER);
        tip.setPadding(0, dp(10), 0, 0);
        body.addView(tip);
    }

    private void submit() {
        String essay = essayEt.getText().toString().trim();
        if (essay.length() < 50) {
            android.widget.Toast.makeText(this,
                    "作答太短（至少 50 字），请写完整些", android.widget.Toast.LENGTH_SHORT).show();
            return;
        }
        submitBtn.setEnabled(false);
        submitBtn.setText("AI 批改中，约需 30~60 秒…");
        status.setText("老师正在逐句批注，请稍候…");
        Api.essayGrade(prefs.token(), curType,
                titleEt.getText().toString().trim(),
                promptEt.getText().toString().trim(),
                essay, new Api.Cb() {
                    @Override
                    public void ok(JSONObject d) {
                        runOnUiThread(() -> {
                            submitBtn.setEnabled(true);
                            submitBtn.setText("📨 提交 AI 批改");
                            lastResult = d;
                            paintResult(d);
                        });
                    }

                    @Override
                    public void err(String m) {
                        runOnUiThread(() -> {
                            submitBtn.setEnabled(true);
                            submitBtn.setText("📨 提交 AI 批改");
                            status.setText("批改失败：" + m);
                        });
                    }
                });
    }

    // ------------------------------ 结果视图 ------------------------------

    private void paintResult(JSONObject r) {
        mode = "result";
        status.setText("");
        body.removeAllViews();

        // 总分卡
        LinearLayout hero = new LinearLayout(this);
        hero.setOrientation(LinearLayout.VERTICAL);
        hero.setGravity(Gravity.CENTER_HORIZONTAL);
        hero.setPadding(dp(16), dp(16), dp(16), dp(14));
        GradientDrawable hg = new GradientDrawable();
        hg.setCornerRadius(dp(14));
        hg.setColor(0xFF2B2620);
        hero.setBackground(hg);
        TextView score = new TextView(this);
        score.setText(trimNum(r.optDouble("total", 0)) + " / " + r.optInt("full_score", 20) + " 分");
        score.setTextSize(32);
        score.setTextColor(Ui.c(EssayActivity.this, "heroGold"));
        score.setTypeface(Typeface.DEFAULT_BOLD);
        score.setGravity(Gravity.CENTER);
        hero.addView(score);
        TextView tn = new TextView(this);
        tn.setText(r.optString("type_name", "") + " · " + r.optInt("word_count") + " 字");
        tn.setTextSize(12);
        tn.setTextColor(0xFFBBAF9A);
        tn.setPadding(0, dp(4), 0, 0);
        hero.addView(tn);
        String summary = r.optString("summary");
        if (!summary.isEmpty()) {
            TextView st = new TextView(this);
            st.setText(summary);
            st.setTextSize(13);
            st.setTextColor(Ui.c(EssayActivity.this, "heroSub"));
            st.setGravity(Gravity.CENTER);
            st.setPadding(dp(8), dp(8), dp(8), 0);
            st.setLineSpacing(dp(2), 1f);
            hero.addView(st);
        }
        body.addView(wrap(hero));

        // 维度评分
        JSONArray dims = r.optJSONArray("dims");
        if (dims != null && dims.length() > 0) {
            LinearLayout dc = card();
            dc.addView(section("维度评分"));
            for (int i = 0; i < dims.length(); i++) {
                JSONObject dim = dims.optJSONObject(i);
                if (dim == null) continue;
                LinearLayout row = new LinearLayout(this);
                row.setOrientation(LinearLayout.VERTICAL);
                row.setPadding(0, dp(9), 0, 0);
                LinearLayout top = new LinearLayout(this);
                top.setOrientation(LinearLayout.HORIZONTAL);
                TextView nm = new TextView(this);
                nm.setText(dim.optString("name", ""));
                nm.setTextSize(13.5f);
                nm.setTextColor(Ui.c(EssayActivity.this, "text"));
                top.addView(nm, new LinearLayout.LayoutParams(0,
                        ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
                TextView sc = new TextView(this);
                sc.setText(trimNum(dim.optDouble("score", 0)) + "/" + dim.optInt("full", 0));
                sc.setTextSize(13);
                sc.setTextColor(Ui.c(EssayActivity.this, "brandDark"));
                sc.setTypeface(Typeface.DEFAULT_BOLD);
                top.addView(sc);
                row.addView(top);
                TextView cm = new TextView(this);
                cm.setText(dim.optString("comment", ""));
                cm.setTextSize(12);
                cm.setTextColor(Ui.c(EssayActivity.this, "sub"));
                cm.setPadding(0, dp(3), 0, 0);
                cm.setLineSpacing(dp(2), 1f);
                row.addView(cm);
                dc.addView(row);
            }
            body.addView(wrap(dc));
        }

        // 逐句批注
        JSONArray notes = r.optJSONArray("notes");
        if (notes != null && notes.length() > 0) {
            LinearLayout nc = card();
            nc.addView(section("逐句批注"));
            for (int i = 0; i < notes.length(); i++) {
                JSONObject n = notes.optJSONObject(i);
                if (n == null) continue;
                TextView quote = new TextView(this);
                quote.setText("「" + n.optString("quote", "") + "」");
                quote.setTextSize(12.5f);
                quote.setTextColor(Ui.c(EssayActivity.this, "sub"));
                quote.setTypeface(Typeface.DEFAULT_BOLD);
                quote.setPadding(0, dp(9), 0, 0);
                nc.addView(quote);
                TextView note = new TextView(this);
                note.setText("✍️ " + n.optString("note", ""));
                note.setTextSize(12.5f);
                note.setTextColor(Ui.c(EssayActivity.this, "green"));
                note.setPadding(dp(10), dp(2), 0, 0);
                note.setLineSpacing(dp(2), 1f);
                nc.addView(note);
            }
            body.addView(wrap(nc));
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
                a.setTextColor(Ui.c(EssayActivity.this, "sub"));
                a.setPadding(0, dp(5), 0, 0);
                a.setLineSpacing(dp(2), 1f);
                ac.addView(a);
            }
            body.addView(wrap(ac));
        }

        // 再写一篇
        Button again = new Button(this);
        again.setText("✍️ 再写一篇");
        again.setTextColor(Ui.c(EssayActivity.this, "sub"));
        again.setBackgroundColor(Ui.c(EssayActivity.this, "line"));
        again.setOnClickListener(v -> paintEdit());
        LinearLayout.LayoutParams alp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        alp.setMargins(dp(2), dp(14), dp(2), 0);
        body.addView(again, alp);
    }

    // ------------------------------ 历史视图 ------------------------------

    private void paintHistory() {
        mode = "hist";
        status.setText("加载历史…");
        body.removeAllViews();
        Api.essayHistory(prefs.token(), new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                runOnUiThread(() -> {
                    status.setText("");
                    JSONArray items = d.optJSONArray("items");
                    if (items == null || items.length() == 0) {
                        TextView empty = new TextView(EssayActivity.this);
                        empty.setText("\n还没有批改记录\n去写第一篇申论吧～");
                        empty.setTextSize(13.5f);
                        empty.setTextColor(Ui.c(EssayActivity.this, "faint"));
                        empty.setGravity(Gravity.CENTER);
                        empty.setPadding(0, dp(80), 0, 0);
                        body.addView(empty);
                        return;
                    }
                    for (int i = 0; i < items.length(); i++) {
                        JSONObject it = items.optJSONObject(i);
                        if (it == null) continue;
                        LinearLayout c = card();
                        LinearLayout top = new LinearLayout(EssayActivity.this);
                        top.setOrientation(LinearLayout.HORIZONTAL);
                        top.setGravity(Gravity.CENTER_VERTICAL);
                        TextView t = new TextView(EssayActivity.this);
                        t.setText(it.optString("title", it.optString("essay_type", "申论")));
                        t.setTextSize(14.5f);
                        t.setTextColor(Ui.c(EssayActivity.this, "text"));
                        t.setTypeface(Typeface.DEFAULT_BOLD);
                        t.setSingleLine(true);
                        top.addView(t, new LinearLayout.LayoutParams(0,
                                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
                        TextView sc = new TextView(EssayActivity.this);
                        sc.setText(trimNum(it.optDouble("total", 0)) + "/"
                                + it.optInt("full_score", 20) + "分");
                        sc.setTextSize(13.5f);
                        sc.setTextColor(Ui.c(EssayActivity.this, "brandDark"));
                        sc.setTypeface(Typeface.DEFAULT_BOLD);
                        top.addView(sc);
                        c.addView(top);
                        TextView sum = new TextView(EssayActivity.this);
                        sum.setText(it.optString("summary", ""));
                        sum.setTextSize(12);
                        sum.setTextColor(Ui.c(EssayActivity.this, "sub"));
                        sum.setMaxLines(2);
                        sum.setPadding(0, dp(5), 0, 0);
                        c.addView(sum);
                        TextView meta = new TextView(EssayActivity.this);
                        meta.setText(it.optString("created_at", "") + " · "
                                + it.optInt("word_count") + " 字");
                        meta.setTextSize(10.5f);
                        meta.setTextColor(Ui.c(EssayActivity.this, "faint"));
                        meta.setPadding(0, dp(4), 0, 0);
                        c.addView(meta);
                        body.addView(wrap(c));
                    }
                });
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> status.setText("加载失败：" + m));
            }
        });
    }

    // ------------------------------ 通用小组件 ------------------------------

    private TextView fieldLabel(String s) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(13);
        t.setTextColor(Ui.c(EssayActivity.this, "sub"));
        t.setPadding(dp(2), dp(12), 0, dp(4));
        return t;
    }

    private EditText input(String hint) {
        EditText et = new EditText(this);
        et.setHint(hint);
        et.setTextSize(13.5f);
        et.setTextColor(Ui.c(EssayActivity.this, "text"));
        et.setBackgroundColor(Ui.c(EssayActivity.this, "card"));
        et.setPadding(dp(10), dp(8), dp(10), dp(8));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(10));
        bg.setColor(Ui.c(EssayActivity.this, "card"));
        bg.setStroke(dp(1), Ui.c(EssayActivity.this, "line"));
        et.setBackground(bg);
        return et;
    }

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(EssayActivity.this, "card"));
        bg.setStroke(dp(1), Ui.c(EssayActivity.this, "line"));
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
        t.setTextColor(Ui.c(EssayActivity.this, "text"));
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
