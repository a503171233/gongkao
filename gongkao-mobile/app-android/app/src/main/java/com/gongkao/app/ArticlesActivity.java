package com.gongkao.app;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.TextUtils;
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
 * 备考文章页（批次D）：热帖精选列表 → 详情（全文+热评+AI 要点提炼）。
 * 契约：GET /articles/home → {items[{post_id,title,category,author,likes,views,
 *          excerpt,analyzed,analysis_summary}], total}
 *      GET /articles/{post_id} → post + replies[] + analysis
 *      POST /articles/analyze {post_id} → {analysis, cached}（20s 冷却，429 友好提示）。
 */
public class ArticlesActivity extends BaseActivity {

    private Prefs prefs;
    private LinearLayout pageRoot;
    private LinearLayout listView;
    private LinearLayout detailView;
    private TextView status;
    private String currentPostId;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);

        getWindow().requestFeature(android.view.Window.FEATURE_NO_TITLE);
        if (android.os.Build.VERSION.SDK_INT >= 23) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        }
        getWindow().setStatusBarColor(Ui.c(ArticlesActivity.this, "card"));

        pageRoot = new LinearLayout(this);
        pageRoot.setOrientation(LinearLayout.VERTICAL);
        pageRoot.setBackgroundColor(Ui.c(ArticlesActivity.this, "bg"));

        pageRoot.addView(topBar("📖 备考文章", v -> onBackPressed()));

        status = new TextView(this);
        status.setTextSize(13);
        status.setTextColor(Ui.c(ArticlesActivity.this, "faint"));
        status.setGravity(Gravity.CENTER);
        status.setPadding(dp(16), dp(30), dp(16), dp(10));
        pageRoot.addView(status);

        // 列表视图
        listView = new LinearLayout(this);
        listView.setOrientation(LinearLayout.VERTICAL);
        ScrollView listScroll = new ScrollView(this);
        listScroll.addView(listView, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        pageRoot.addView(listScroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        // 详情视图（默认隐藏）
        detailView = new LinearLayout(this);
        detailView.setOrientation(LinearLayout.VERTICAL);
        detailView.setVisibility(View.GONE);
        pageRoot.addView(detailView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        setContentView(pageRoot);
        loadList();
    }

    @Override
    public void onBackPressed() {
        if (detailView.getVisibility() == View.VISIBLE) {
            detailView.setVisibility(View.GONE);
            loadList();   // 回列表时刷新（点赞/浏览数可能变化）
        } else {
            finish();
        }
    }

    private View topBar(String title, View.OnClickListener back) {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Ui.c(ArticlesActivity.this, "card"));
        bar.setPadding(dp(12), dp(10), dp(12), dp(10));
        TextView b = new TextView(this);
        b.setText("←");
        b.setTextSize(20);
        b.setTextColor(Ui.c(ArticlesActivity.this, "sub"));
        b.setPadding(dp(4), dp(2), dp(14), dp(2));
        b.setOnClickListener(back);
        bar.addView(b);
        TextView t = new TextView(this);
        t.setText(title);
        t.setTextSize(17);
        t.setTextColor(Ui.c(ArticlesActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(t);
        return bar;
    }

    // ------------------------------ 列表 ------------------------------

    private void loadList() {
        // 骨架屏加载态（批次F）
        status.setVisibility(View.GONE);
        listView.removeAllViews();
        listView.addView(Ui.skeleton(this, 2));
        Api.articlesHome(prefs.token(), new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                runOnUiThread(() -> {
                    status.setVisibility(View.GONE);
                    listView.removeAllViews();
                    JSONArray items = d.optJSONArray("items");
                    if (items == null || items.length() == 0) {
                        TextView empty = new TextView(ArticlesActivity.this);
                        empty.setText("\n暂无文章\n\n论坛里的优质经验帖会精选到这里，\n去「论坛」发一篇你的备考心得吧～");
                        empty.setTextSize(13.5f);
                        empty.setTextColor(Ui.c(ArticlesActivity.this, "faint"));
                        empty.setGravity(Gravity.CENTER);
                        empty.setPadding(0, dp(80), 0, 0);
                        listView.addView(empty);
                        return;
                    }
                    int shown = 0;
                    for (int i = 0; i < items.length(); i++) {
                        JSONObject it = items.optJSONObject(i);
                        if (it != null) {
                            View card = wrap(paintItem(it));
                            listView.addView(card);
                            Ui.enter(card, shown * 45L);
                            shown++;
                        }
                    }
                });
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> {
                    listView.removeAllViews();
                    status.setVisibility(View.VISIBLE);
                    status.setText("加载失败：" + m);
                });
            }
        });
    }

    private LinearLayout paintItem(JSONObject it) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(ArticlesActivity.this, "card"));
        bg.setStroke(dp(1), Ui.c(ArticlesActivity.this, "line"));
        card.setBackground(bg);

        LinearLayout head = new LinearLayout(this);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView cat = new TextView(this);
        cat.setText(it.optString("category", "经验"));
        cat.setTextSize(10.5f);
        cat.setTextColor(Ui.c(ArticlesActivity.this, "brandDark"));
        GradientDrawable cbg = new GradientDrawable();
        cbg.setCornerRadius(dp(4));
        cbg.setColor(Ui.c(ArticlesActivity.this, "brandSoft"));
        cat.setBackground(cbg);
        cat.setPadding(dp(6), dp(2), dp(6), dp(2));
        head.addView(cat);
        if (it.optBoolean("analyzed")) {
            TextView ai = new TextView(this);
            ai.setText("✨ 已有 AI 要点");
            ai.setTextSize(10);
            ai.setTextColor(Ui.c(ArticlesActivity.this, "purple"));
            ai.setPadding(dp(8), 0, 0, 0);
            head.addView(ai);
        }
        card.addView(head);

        TextView title = new TextView(this);
        title.setText(it.optString("title", ""));
        title.setTextSize(15);
        title.setTextColor(Ui.c(ArticlesActivity.this, "text"));
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setMaxLines(2);
        title.setPadding(0, dp(7), 0, 0);
        card.addView(title);

        TextView excerpt = new TextView(this);
        excerpt.setText(it.optString("excerpt", ""));
        excerpt.setTextSize(12.5f);
        excerpt.setTextColor(Ui.c(ArticlesActivity.this, "sub"));
        excerpt.setMaxLines(2);
        excerpt.setEllipsize(TextUtils.TruncateAt.END);
        excerpt.setPadding(0, dp(4), 0, 0);
        card.addView(excerpt);

        LinearLayout foot = new LinearLayout(this);
        foot.setOrientation(LinearLayout.HORIZONTAL);
        foot.setPadding(0, dp(7), 0, 0);
        TextView meta = new TextView(this);
        meta.setText(it.optString("author", "同学") + " · 👍 " + it.optInt("likes")
                + " · 👁 " + it.optInt("views"));
        meta.setTextSize(11);
        meta.setTextColor(Ui.c(ArticlesActivity.this, "faint"));
        foot.addView(meta, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView go = new TextView(this);
        go.setText("阅读全文 ›");
        go.setTextSize(11.5f);
        go.setTextColor(Ui.c(ArticlesActivity.this, "brandDark"));
        foot.addView(go);
        card.addView(foot);

        card.setOnClickListener(v -> openDetail(it.optString("post_id")));
        return card;
    }

    // ------------------------------ 详情 ------------------------------

    private void openDetail(String postId) {
        currentPostId = postId;
        listView.setVisibility(View.GONE);
        status.setVisibility(View.VISIBLE);
        status.setText("加载中…");
        Api.articlesDetail(prefs.token(), postId, new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                runOnUiThread(() -> {
                    status.setVisibility(View.GONE);
                    detailView.removeAllViews();
                    detailView.setVisibility(View.VISIBLE);
                    ScrollView sc = new ScrollView(ArticlesActivity.this);
                    LinearLayout body = new LinearLayout(ArticlesActivity.this);
                    body.setOrientation(LinearLayout.VERTICAL);
                    body.setPadding(dp(14), dp(8), dp(14), dp(30));
                    paintDetail(body, d);
                    sc.addView(body, new ViewGroup.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT,
                            ViewGroup.LayoutParams.WRAP_CONTENT));
                    detailView.addView(sc, new LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
                });
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> {
                    status.setText("加载失败：" + m);
                    listView.setVisibility(View.VISIBLE);
                });
            }
        });
    }

    private void paintDetail(LinearLayout body, JSONObject d) {
        LinearLayout card = card();
        TextView title = new TextView(this);
        title.setText(d.optString("title", ""));
        title.setTextSize(17);
        title.setTextColor(Ui.c(ArticlesActivity.this, "text"));
        title.setTypeface(Typeface.DEFAULT_BOLD);
        card.addView(title);

        TextView meta = new TextView(this);
        meta.setText(d.optString("category", "") + " · " + d.optString("author", "同学")
                + " · 👍 " + d.optInt("likes", 0) + " · 👁 " + d.optInt("views", 0));
        meta.setTextSize(11.5f);
        meta.setTextColor(Ui.c(ArticlesActivity.this, "faint"));
        meta.setPadding(0, dp(6), 0, dp(8));
        card.addView(meta);

        // AI 要点提炼区（先展示缓存，无缓存给按钮）
        JSONObject analysis = d.optJSONObject("analysis");
        LinearLayout aiBox = new LinearLayout(this);
        aiBox.setOrientation(LinearLayout.VERTICAL);
        aiBox.setPadding(dp(10), dp(10), dp(10), dp(10));
        GradientDrawable aiBg = new GradientDrawable();
        aiBg.setCornerRadius(dp(10));
        aiBg.setColor(Ui.c(ArticlesActivity.this, "cardAlt"));
        aiBox.setBackground(aiBg);
        paintAiBox(aiBox, analysis);
        card.addView(aiBox);

        // 正文
        TextView content = new TextView(this);
        content.setText(d.optString("content", ""));
        content.setTextSize(14.5f);
        content.setTextColor(Ui.c(ArticlesActivity.this, "text"));
        content.setLineSpacing(dp(4), 1f);
        content.setPadding(0, dp(12), 0, 0);
        card.addView(content);

        // 热评
        JSONArray replies = d.optJSONArray("replies");
        if (replies != null && replies.length() > 0) {
            TextView rt = new TextView(this);
            rt.setText("💬 热门回帖");
            rt.setTextSize(13.5f);
            rt.setTextColor(Ui.c(ArticlesActivity.this, "text"));
            rt.setTypeface(Typeface.DEFAULT_BOLD);
            rt.setPadding(0, dp(16), 0, dp(4));
            card.addView(rt);
            for (int i = 0; i < replies.length(); i++) {
                JSONObject r = replies.optJSONObject(i);
                if (r == null) continue;
                TextView reply = new TextView(this);
                reply.setText(r.optString("username", "同学") + "：" + r.optString("content", ""));
                reply.setTextSize(12.5f);
                reply.setTextColor(Ui.c(ArticlesActivity.this, "sub"));
                reply.setPadding(0, dp(4), 0, 0);
                card.addView(reply);
            }
        }
        body.addView(wrap(card));
    }

    /** AI 要点框：有分析展示摘要；无分析放按钮（点击调 analyze）。 */
    private void paintAiBox(LinearLayout aiBox, JSONObject analysis) {
        aiBox.removeAllViews();
        TextView head = new TextView(this);
        head.setText("✨ AI 要点提炼");
        head.setTextSize(13);
        head.setTextColor(Ui.c(ArticlesActivity.this, "purple"));
        head.setTypeface(Typeface.DEFAULT_BOLD);
        aiBox.addView(head);

        if (analysis != null && !analysis.optString("summary").isEmpty()) {
            TextView sum = new TextView(this);
            sum.setText(analysis.optString("summary", ""));
            sum.setTextSize(12.5f);
            sum.setTextColor(Ui.c(ArticlesActivity.this, "sub"));
            sum.setLineSpacing(dp(3), 1f);
            sum.setPadding(0, dp(6), 0, 0);
            aiBox.addView(sum);
            JSONArray kps = analysis.optJSONArray("key_points");
            if (kps != null) {
                for (int i = 0; i < kps.length(); i++) {
                    TextView kp = new TextView(this);
                    kp.setText("· " + kps.optString(i));
                    kp.setTextSize(12.5f);
                    kp.setTextColor(Ui.c(ArticlesActivity.this, "sub"));
                    kp.setPadding(0, dp(3), 0, 0);
                    aiBox.addView(kp);
                }
            }
            String advice = analysis.optString("advice");
            if (!advice.isEmpty()) {
                TextView ad = new TextView(this);
                ad.setText("👉 " + advice);
                ad.setTextSize(12.5f);
                ad.setTextColor(Ui.c(ArticlesActivity.this, "purple"));
                ad.setPadding(0, dp(6), 0, 0);
                aiBox.addView(ad);
            }
        } else {
            TextView hint = new TextView(this);
            hint.setText("让 AI 帮你提炼这篇帖子的备考干货");
            hint.setTextSize(12);
            hint.setTextColor(Ui.c(ArticlesActivity.this, "faint"));
            hint.setPadding(0, dp(6), 0, 0);
            aiBox.addView(hint);
            Button btn = new Button(this);
            btn.setText("生成要点提炼");
            btn.setTextSize(12.5f);
            btn.setTextColor(Color.WHITE);
            btn.setBackgroundColor(Ui.c(ArticlesActivity.this, "purple"));
            btn.setOnClickListener(v -> {
                btn.setEnabled(false);
                btn.setText("提炼中…");
                Api.articlesAnalyze(prefs.token(), currentPostId, new Api.Cb() {
                    @Override
                    public void ok(JSONObject r) {
                        runOnUiThread(() -> paintAiBox(aiBox, r.optJSONObject("analysis")));
                    }

                    @Override
                    public void err(String m) {
                        runOnUiThread(() -> {
                            btn.setEnabled(true);
                            btn.setText("生成要点提炼");
                            android.widget.Toast.makeText(ArticlesActivity.this,
                                    m, android.widget.Toast.LENGTH_SHORT).show();
                        });
                    }
                });
            });
            aiBox.addView(btn, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        }
    }

    // ------------------------------ 通用小组件 ------------------------------

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(ArticlesActivity.this, "card"));
        bg.setStroke(dp(1), Ui.c(ArticlesActivity.this, "line"));
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

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
