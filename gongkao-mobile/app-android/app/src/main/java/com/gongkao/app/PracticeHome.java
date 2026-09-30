package com.gongkao.app;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
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
 * 刷题页主视图（批次B，Tab「刷题」内嵌）：
 * 学情统计卡 + 今日智能练习 + 只刷错题 + 分类专项 chips + 智能组卷 + 在线模考。
 * 抽题/判分闭环在后端（答错自动进错题本 · 艾宾浩斯排程）。
 */
public class PracticeHome {

    private final Activity act;
    private final Prefs prefs;
    private TextView statLine;
    private LinearLayout catBox;

    public PracticeHome(Activity act, Prefs prefs) {
        this.act = act;
        this.prefs = prefs;
    }

    public View build() {
        ScrollView scroll = new ScrollView(act);
        scroll.setBackgroundColor(Ui.c(act, "bg"));
        LinearLayout page = new LinearLayout(act);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(14), dp(14), dp(14), dp(20) + dp(56));

        // 学情统计卡
        LinearLayout statCard = new LinearLayout(act);
        statCard.setOrientation(LinearLayout.VERTICAL);
        GradientDrawable sgb = new GradientDrawable();
        sgb.setCornerRadius(dp(14));
        sgb.setColor(Ui.c(act, "card"));
        sgb.setStroke(dp(1), Ui.c(act, "line"));
        statCard.setBackground(sgb);
        statCard.setPadding(dp(16), dp(14), dp(16), dp(14));

        TextView st = new TextView(act);
        st.setText("📊 我的学情");
        st.setTextSize(14);
        st.setTextColor(Ui.c(act, "sub"));
        statCard.addView(st);

        statLine = new TextView(act);
        statLine.setText("加载中…");
        statLine.setTextSize(15);
        statLine.setTextColor(Ui.c(act, "text"));
        statLine.setPadding(0, dp(6), 0, 0);
        statCard.addView(statLine);
        page.addView(statCard, rowLp());

        // 入口卡：今日智能练习 / 只刷错题
        page.addView(entry("✨", "今日智能练习", "到期错题 · 薄弱专项 · 智能新题", "v23today"));
        page.addView(entry("📕", "只刷错题", "做错过且从未做对的题，逐个击破", "v23wrong"));

        // 分类专项
        TextView catTitle = new TextView(act);
        catTitle.setText("分类专项");
        catTitle.setTextSize(13);
        catTitle.setTextColor(Ui.c(act, "sub"));
        catTitle.setTypeface(Typeface.DEFAULT_BOLD);
        catTitle.setPadding(dp(4), dp(14), 0, dp(8));
        page.addView(catTitle);

        catBox = new LinearLayout(act);
        catBox.setOrientation(LinearLayout.VERTICAL);
        TextView loading = new TextView(act);
        loading.setText("加载分类中…");
        loading.setTextSize(13);
        loading.setTextColor(Ui.c(act, "faint"));
        catBox.addView(loading);
        page.addView(catBox);
        loadCategories();

        // 智能组卷 / 在线模考
        page.addView(entry("🧩", "智能组卷", "按题型/数量自选组卷，即时判分出成绩", "v23smart"));
        page.addView(entry("⏱️", "在线模考", "计时整卷考试，到时自动交卷", "v23mock"));

        scroll.addView(page, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        refreshStats();
        return scroll;
    }

    /** onResume / 切回时刷新统计。 */
    public void refresh() {
        refreshStats();
    }

    private void refreshStats() {
        Api.practiceStats(prefs.token(), new Api.Cb() {
            @Override
            public void ok(JSONObject s) {
                int total = s.optInt("total", 0);
                int correct = s.optInt("correct", 0);
                String acc = s.isNull("accuracy") || !s.has("accuracy") ? "—"
                        : String.format("%.0f%%", s.optDouble("accuracy", 0) * 100);
                act.runOnUiThread(() -> statLine.setText(
                        "已练 " + total + " 题 · 判对 " + correct + " 题 · 正确率 " + acc
                                + (total == 0 ? "\n还没有练习记录，从「今日智能练习」开始吧" : "")));
            }

            @Override
            public void err(String m) {
                act.runOnUiThread(() -> statLine.setText("统计加载失败：" + m));
            }
        });
    }

    private void loadCategories() {
        Api.practiceCategories(prefs.token(), prefs.teacherId(), new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                JSONArray cats = d.optJSONArray("categories");
                act.runOnUiThread(() -> {
                    catBox.removeAllViews();
                    if (cats == null || cats.length() == 0) {
                        TextView t = new TextView(act);
                        t.setText("该老师题库暂无分类");
                        t.setTextSize(13);
                        t.setTextColor(Ui.c(act, "faint"));
                        catBox.addView(t);
                        return;
                    }
                    LinearLayout row = null;
                    for (int i = 0; i < cats.length(); i++) {
                        JSONObject c = cats.optJSONObject(i);
                        if (c == null) continue;
                        if (i % 2 == 0) {
                            row = new LinearLayout(act);
                            row.setOrientation(LinearLayout.HORIZONTAL);
                            catBox.addView(row, rowLp());
                        }
                        TextView chip = new TextView(act);
                        chip.setText("  " + c.optString("category", "") + " · " + c.optInt("count", 0) + " 题  ");
                        chip.setTextSize(14);
                        chip.setTextColor(Ui.c(act, "brandDark"));
                        chip.setGravity(Gravity.CENTER);
                        chip.setPadding(dp(10), dp(12), dp(10), dp(12));
                        GradientDrawable bg = new GradientDrawable();
                        bg.setCornerRadius(dp(10));
                        bg.setColor(0xFFF7EFD9);
                        chip.setBackground(bg);
                        final String cat = c.optString("category", "");
                        chip.setOnClickListener(v -> openPractice("", cat));
                        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0,
                                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
                        lp.setMargins(dp(3), dp(3), dp(3), dp(3));
                        row.addView(chip, lp);
                    }
                });
            }

            @Override
            public void err(String m) {
                act.runOnUiThread(() -> {
                    catBox.removeAllViews();
                    TextView t = new TextView(act);
                    t.setText("分类加载失败：" + m);
                    t.setTextSize(13);
                    t.setTextColor(Ui.c(act, "red"));
                    catBox.addView(t);
                });
            }
        });
    }

    /** 入口卡（大按钮）。kind: v23today/v23wrong/v23smart/v23mock。 */
    private View entry(String icon, String title, String desc, String kind) {
        LinearLayout card = new LinearLayout(act);
        card.setOrientation(LinearLayout.HORIZONTAL);
        card.setGravity(Gravity.CENTER_VERTICAL);
        card.setPadding(dp(14), dp(13), dp(14), dp(13));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(14));
        bg.setColor(Ui.c(act, "card"));
        bg.setStroke(dp(1), Ui.c(act, "line"));
        card.setBackground(bg);

        TextView ic = new TextView(act);
        ic.setText(icon);
        ic.setTextSize(22);
        card.addView(ic);

        LinearLayout mid = new LinearLayout(act);
        mid.setOrientation(LinearLayout.VERTICAL);
        TextView t = new TextView(act);
        t.setText("  " + title);
        t.setTextSize(15.5f);
        t.setTextColor(Ui.c(act, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        mid.addView(t);
        TextView d = new TextView(act);
        d.setText("  " + desc);
        d.setTextSize(12);
        d.setTextColor(Ui.c(act, "faint"));
        d.setPadding(0, dp(2), 0, 0);
        mid.addView(d);
        card.addView(mid, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        TextView go = new TextView(act);
        go.setText("›");
        go.setTextSize(20);
        go.setTextColor(Ui.c(act, "faint"));
        card.addView(go);

        card.setOnClickListener(v -> {
            switch (kind) {
                case "v23today": openPractice("today", ""); break;
                case "v23wrong": openPractice("wrong", ""); break;
                case "v23smart": smartExamDialog(); break;
                case "v23mock": mockExamDialog(); break;
            }
        });

        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, dp(6), 0, dp(6));
        return wrap(card, lp);
    }

    private LinearLayout wrap(LinearLayout child, LinearLayout.LayoutParams lp) {
        LinearLayout holder = new LinearLayout(act);
        holder.addView(child, lp);
        return holder;
    }

    private void openPractice(String mode, String category) {
        android.content.Intent i = new android.content.Intent(act, PracticeActivity.class);
        i.putExtra("mode", mode);
        i.putExtra("category", category);
        act.startActivity(i);
    }

    private void smartExamDialog() {
        final String[] qtypes = {"不限", "单选", "判断", "简答"};
        final String[] qtypeVals = {"", "choice", "judge", "essay"};
        final int[] pickedType = {0};
        final EditText countInput = new EditText(act);
        countInput.setText("10");
        countInput.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        countInput.setHint("题量 1~50");
        countInput.setPadding(dp(16), dp(10), dp(16), dp(10));

        Ui.dialogBuilder(act)
                .setTitle("智能组卷")
                .setSingleChoiceItems(qtypes, 0, (d, w) -> pickedType[0] = w)
                .setView(countInput)
                .setPositiveButton("生成试卷", (d, w) -> {
                    int count = 10;
                    try {
                        count = Integer.parseInt(countInput.getText().toString().trim());
                    } catch (Exception ignored) { }
                    count = Math.max(1, Math.min(50, count));
                    generateSmart(qtypeVals[pickedType[0]], count);
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void generateSmart(String qtype, int count) {
        toast("正在组卷…");
        JSONObject body = new JSONObject();
        try {
            body.put("teacher_id", prefs.teacherId());
            body.put("count", count);
            if (!qtype.isEmpty()) body.put("qtype", qtype);
        } catch (Exception ignored) { }
        Api.smartGenerate(prefs.token(), body, new Api.Cb() {
            @Override
            public void ok(JSONObject paper) {
                openPaper(paper.optLong("id"), 0, paper.optString("title", "智能组卷"));
            }

            @Override
            public void err(String m) {
                act.runOnUiThread(() -> toast("组卷失败：" + m));
            }
        });
    }

    private void mockExamDialog() {
        final String[] opts = {"60 分钟", "120 分钟"};
        final int[] picked = {0};
        Ui.dialogBuilder(act)
                .setTitle("在线模考（计时自动交卷）")
                .setSingleChoiceItems(opts, 0, (d, w) -> picked[0] = w)
                .setPositiveButton("开始考试", (d, w) -> {
                    int mins = picked[0] == 0 ? 60 : 120;
                    toast("正在组卷…");
                    Api.mockGenerate(prefs.token(), prefs.teacherId(), mins, new Api.Cb() {
                        @Override
                        public void ok(JSONObject paper) {
                            openPaper(paper.optLong("id"), mins,
                                    paper.optString("title", "在线模考"));
                        }

                        @Override
                        public void err(String m) {
                            act.runOnUiThread(() -> toast("模考组卷失败：" + m));
                        }
                    });
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void openPaper(long paperId, int mins, String title) {
        android.content.Intent i = new android.content.Intent(act, PaperExamActivity.class);
        i.putExtra("paperId", paperId);
        i.putExtra("durationMins", mins);
        i.putExtra("title", title);
        act.startActivity(i);
    }

    private void toast(String s) {
        android.widget.Toast.makeText(act, s, android.widget.Toast.LENGTH_SHORT).show();
    }

    private LinearLayout.LayoutParams rowLp() {
        return new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    private int dp(int v) {
        return Math.round(v * act.getResources().getDisplayMetrics().density);
    }
}
