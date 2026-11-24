package com.gongkao.app;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 学习报告页（批次D）：本周学情 + 分类掌握 + 复习排程 + 资产盘点 + 模考成绩 五区块。
 * 契约：GET /me/learning-report →
 *   weekly{graded,correct,accuracy,by_category[{category,total,correct,accuracy}],weak[],
 *          recent_days[{d,n,ok}],essay{pending,avg_score}}
 *   review{due,in_queue,mastered,next_review_at}
 *   inventory{favorites,mistakes}
 *   exam{trend[{d,papers,avg_score,best_score}],summary{papers,avg_score,best_score}}
 */
public class LearningReportActivity extends BaseActivity {

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
        getWindow().setStatusBarColor(Ui.c(LearningReportActivity.this, "card"));

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(LearningReportActivity.this, "bg"));

        page.addView(topBar("📈 学习报告", v -> finish()));

        status = new TextView(this);
        status.setTextSize(13);
        status.setTextColor(Ui.c(LearningReportActivity.this, "faint"));
        status.setGravity(Gravity.CENTER);
        status.setPadding(dp(16), dp(30), dp(16), dp(10));
        page.addView(status);

        ScrollView scroll = new ScrollView(this);
        scroll.setVisibility(View.GONE);
        body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(14), dp(6), dp(14), dp(30));
        scroll.addView(body, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        page.addView(scroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        setContentView(page);
        load();
    }

    private Prefs prefs;
    private ScrollView scroll;

    private View topBar(String title, View.OnClickListener back) {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Ui.c(LearningReportActivity.this, "card"));
        bar.setPadding(dp(12), dp(10), dp(12), dp(10));
        TextView b = new TextView(this);
        b.setText("←");
        b.setTextSize(20);
        b.setTextColor(Ui.c(LearningReportActivity.this, "sub"));
        b.setPadding(dp(4), dp(2), dp(14), dp(2));
        b.setOnClickListener(back);
        bar.addView(b);
        TextView t = new TextView(this);
        t.setText(title);
        t.setTextSize(17);
        t.setTextColor(Ui.c(LearningReportActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(t);
        return bar;
    }

    private void load() {
        // 骨架屏加载态（批次G）
        status.setVisibility(View.GONE);
        scroll.setVisibility(View.VISIBLE);
        body.removeAllViews();
        body.addView(Ui.skeleton(this, 3));
        Api.learningReport(prefs.token(), new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                runOnUiThread(() -> {
                    status.setVisibility(View.GONE);
                    scroll.setVisibility(View.VISIBLE);
                    body.removeAllViews();
                    paint(d);
                });
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> {
                    body.removeAllViews();
                    status.setVisibility(View.VISIBLE);
                    status.setText("加载失败：" + m);
                });
            }
        });
    }

    private void paint(JSONObject d) {
        JSONObject weekly = d.optJSONObject("weekly");
        if (weekly != null) {
            // ---- 本周总览 ----
            LinearLayout hero = card();
            TextView heroTitle = section("本周学情");
            hero.addView(heroTitle);
            LinearLayout nums = new LinearLayout(this);
            nums.setOrientation(LinearLayout.HORIZONTAL);
            nums.setPadding(0, dp(10), 0, dp(4));
            nums.addView(stat(weekly.optInt("graded"), "已练题数", 0.55f));
            nums.addView(stat(weekly.optInt("correct"), "判对题数", 0.55f));
            nums.addView(stat(Math.round(weekly.optDouble("accuracy", 0) * 100) + "%",
                    "正确率", 0.55f));
            hero.addView(nums);

            // 最近 7 天练题量迷你条形
            JSONArray recent = weekly.optJSONArray("recent_days");
            if (recent != null && recent.length() > 0) {
                TextView rt = new TextView(this);
                rt.setText("近 7 天练题量");
                rt.setTextSize(12);
                rt.setTextColor(Ui.c(LearningReportActivity.this, "faint"));
                rt.setPadding(0, dp(10), 0, dp(4));
                hero.addView(rt);
                int max = 1;
                for (int i = 0; i < recent.length(); i++)
                    max = Math.max(max, recent.optJSONObject(i).optInt("n"));
                LinearLayout bars = new LinearLayout(this);
                bars.setOrientation(LinearLayout.HORIZONTAL);
                bars.setGravity(Gravity.BOTTOM);
                for (int i = 0; i < recent.length(); i++) {
                    JSONObject rd = recent.optJSONObject(i);
                    LinearLayout col = new LinearLayout(this);
                    col.setOrientation(LinearLayout.VERTICAL);
                    col.setGravity(Gravity.CENTER_HORIZONTAL);
                    LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(0,
                            ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
                    TextView bar = new TextView(this);
                    int h = Math.max(dp(3), (int) (dp(36) * rd.optInt("n") / (float) max));
                    bar.setBackgroundColor(rd.optInt("n") > 0 ? Ui.c(LearningReportActivity.this, "brand") : Ui.c(LearningReportActivity.this, "line"));
                    LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(dp(14), h);
                    blp.gravity = Gravity.CENTER_HORIZONTAL;
                    bar.setLayoutParams(blp);
                    col.addView(bar);
                    TextView day = new TextView(this);
                    String ds = rd.optString("d", "");
                    day.setText(ds.length() >= 10 ? ds.substring(8) + "日" : ds);
                    day.setTextSize(9);
                    day.setTextColor(Ui.c(LearningReportActivity.this, "faint"));
                    day.setPadding(0, dp(3), 0, 0);
                    col.addView(day);
                    bars.addView(col, bp);
                }
                hero.addView(bars);
            }

            // 申论状态
            JSONObject essay = weekly.optJSONObject("essay");
            if (essay != null && (essay.optInt("pending") > 0
                    || essay.opt("avg_score") != null)) {
                TextView et = new TextView(this);
                String s = "✍️ 申论：";
                if (essay.optInt("pending") > 0)
                    s += essay.optInt("pending") + " 篇待批改";
                if (essay.opt("avg_score") != null)
                    s += (s.endsWith("：") ? "" : " · ") + "平均 "
                            + trimNum(essay.optDouble("avg_score", 0)) + " 分";
                et.setText(s);
                et.setTextSize(12.5f);
                et.setTextColor(Ui.c(LearningReportActivity.this, "brandDark"));
                et.setPadding(0, dp(8), 0, 0);
                hero.addView(et);
            }
            body.addView(wrap(hero));

            // ---- 分类掌握 ----
            JSONArray cats = weekly.optJSONArray("by_category");
            if (cats != null && cats.length() > 0) {
                LinearLayout cc = card();
                cc.addView(section("分类掌握"));
                for (int i = 0; i < cats.length(); i++) {
                    JSONObject c = cats.optJSONObject(i);
                    paintCategoryRow(cc, c);
                }
                JSONArray weak = weekly.optJSONArray("weak");
                if (weak != null && weak.length() > 0) {
                    TextView wt = new TextView(this);
                    StringBuilder sb = new StringBuilder("⚠️ 薄弱：");
                    for (int i = 0; i < weak.length(); i++) {
                        if (i > 0) sb.append("、");
                        sb.append(weak.optString(i));
                    }
                    wt.setText(sb + "（建议分类专项突破）");
                    wt.setTextSize(12.5f);
                    wt.setTextColor(Ui.c(LearningReportActivity.this, "red"));
                    wt.setPadding(0, dp(8), 0, 0);
                    cc.addView(wt);
                }
                body.addView(wrap(cc));
            }
        }

        // ---- 复习排程 ----
        JSONObject review = d.optJSONObject("review");
        if (review != null) {
            LinearLayout rc = card();
            rc.addView(section("复习排程（艾宾浩斯）"));
            LinearLayout rn = new LinearLayout(this);
            rn.setOrientation(LinearLayout.HORIZONTAL);
            rn.setPadding(0, dp(8), 0, 0);
            rn.addView(stat(review.optInt("due"), "今日待复习", 0.55f));
            rn.addView(stat(review.optInt("in_queue"), "复习队列", 0.55f));
            rn.addView(stat(review.optInt("mastered"), "已掌握", 0.55f));
            rc.addView(rn);
            body.addView(wrap(rc));
        }

        // ---- 题册资产 ----
        JSONObject inv = d.optJSONObject("inventory");
        if (inv != null) {
            LinearLayout ic = card();
            ic.addView(section("题册资产"));
            LinearLayout in = new LinearLayout(this);
            in.setOrientation(LinearLayout.HORIZONTAL);
            in.setPadding(0, dp(8), 0, 0);
            in.addView(stat(inv.optInt("mistakes"), "错题本", 0.55f));
            in.addView(stat(inv.optInt("favorites"), "收藏夹", 0.55f));
            ic.addView(in);
            body.addView(wrap(ic));
        }

        // ---- 模考成绩 ----
        JSONObject exam = d.optJSONObject("exam");
        if (exam != null) {
            JSONObject sum = exam.optJSONObject("summary");
            JSONArray trend = exam.optJSONArray("trend");
            if (sum != null && sum.optInt("papers") > 0) {
                LinearLayout ec = card();
                ec.addView(section("模考成绩"));
                LinearLayout en = new LinearLayout(this);
                en.setOrientation(LinearLayout.HORIZONTAL);
                en.setPadding(0, dp(8), 0, 0);
                en.addView(stat(sum.optInt("papers"), "已考套数", 0.55f));
                en.addView(stat(trimNum(sum.optDouble("avg_score", 0)), "平均分", 0.55f));
                en.addView(stat(trimNum(sum.optDouble("best_score", 0)), "最佳分", 0.55f));
                ec.addView(en);
                if (trend != null && trend.length() > 0) {
                    JSONObject last = trend.optJSONObject(trend.length() - 1);
                    if (last != null) {
                        TextView lt = new TextView(this);
                        lt.setText("最近一次：" + last.optString("d", "") + " 得 "
                                + trimNum(last.optDouble("best_score", 0)) + " 分");
                        lt.setTextSize(12);
                        lt.setTextColor(Ui.c(LearningReportActivity.this, "faint"));
                        lt.setPadding(0, dp(8), 0, 0);
                        ec.addView(lt);
                    }
                }
                body.addView(wrap(ec));
            }
        }

        if (body.getChildCount() == 0) {
            status.setVisibility(View.VISIBLE);
            status.setText("暂无学习数据，先去「刷题」练一轮吧");
        }
    }

    private void paintCategoryRow(LinearLayout parent, JSONObject c) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(0, dp(8), 0, 0);

        TextView name = new TextView(this);
        name.setText(c.optString("category", "-"));
        name.setTextSize(13.5f);
        name.setTextColor(Ui.c(LearningReportActivity.this, "text"));
        name.setWidth(dp(72));
        row.addView(name);

        // 条形进度
        LinearLayout barWrap = new LinearLayout(this);
        barWrap.setPadding(0, 0, dp(8), 0);
        View bar = new View(this);
        double acc = c.optDouble("accuracy", 0);
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(3));
        bg.setColor(acc >= 0.7 ? Ui.c(LearningReportActivity.this, "green") : acc >= 0.4 ? Ui.c(LearningReportActivity.this, "brand") : Ui.c(LearningReportActivity.this, "red"));
        bar.setBackground(bg);
        barWrap.addView(bar, new LinearLayout.LayoutParams(
                Math.max(dp(2), (int) (dp(110) * acc)), dp(8)));
        row.addView(barWrap, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        TextView pct = new TextView(this);
        pct.setText(trimNum(acc * 100) + "% (" + c.optInt("correct") + "/"
                + c.optInt("total") + ")");
        pct.setTextSize(12);
        pct.setTextColor(Ui.c(LearningReportActivity.this, "sub"));
        row.addView(pct);
        parent.addView(row);
    }

    // ------------------------------ 通用小组件 ------------------------------

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(LearningReportActivity.this, "card"));
        bg.setStroke(dp(1), Ui.c(LearningReportActivity.this, "line"));
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
        t.setTextColor(Ui.c(LearningReportActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        return t;
    }

    /** 数字统计块（大数字 + 小标签），weight 均分。 */
    private LinearLayout stat(Object num, String label, float weight) {
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setGravity(Gravity.CENTER_HORIZONTAL);
        TextView n = new TextView(this);
        n.setText(String.valueOf(num));
        n.setTextSize(19);
        n.setTextColor(Ui.c(LearningReportActivity.this, "brandDark"));
        n.setTypeface(Typeface.DEFAULT_BOLD);
        n.setGravity(Gravity.CENTER);
        col.addView(n);
        TextView l = new TextView(this);
        l.setText(label);
        l.setTextSize(11);
        l.setTextColor(Ui.c(LearningReportActivity.this, "faint"));
        l.setGravity(Gravity.CENTER);
        col.addView(l);
        LinearLayout holder = new LinearLayout(this);
        holder.addView(col, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, weight);
        holder.setLayoutParams(lp);
        return holder;
    }

    private String trimNum(double v) {
        if (v == Math.floor(v) && !Double.isInfinite(v)) return String.valueOf((long) v);
        return String.format(java.util.Locale.US, "%.1f", v);
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
