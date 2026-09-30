package com.gongkao.app;

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
 * 粉笔同步数据明细页（批次 J）：错题本 / 收藏题 / 模考试卷 三类视图。
 * intent extra: type = "wrong" | "collect" | "mock"。
 * 契约：GET /me/fenbi/wrong?limit&offset · /me/fenbi/collects → {rows,total}
 *      GET /me/fenbi/mocks → {rows} · GET /me/fenbi/mocks/{id} → 详情+作答明细。
 * 错题/收藏点击弹窗看完整题干+选项+答案+解析；模考点击进整卷明细。
 */
public class FenbiDataActivity extends BaseActivity {

    private String type = "wrong";
    private String title = "粉笔数据";
    private int total = 0;
    private int loaded = 0;
    private LinearLayout body;
    private TextView status;

    public static void start(android.content.Context ctx, String type) {
        ctx.startActivity(new android.content.Intent(ctx, FenbiDataActivity.class)
                .putExtra("type", type));
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        type = getIntent().getStringExtra("type");
        if (type == null) type = "wrong";
        if ("collect".equals(type)) title = "⭐ 粉笔收藏题";
        else if ("mock".equals(type)) title = "📝 粉笔模考试卷";
        else title = "📕 粉笔错题本";

        getWindow().requestFeature(android.view.Window.FEATURE_NO_TITLE);
        if (android.os.Build.VERSION.SDK_INT >= 23) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        }
        getWindow().setStatusBarColor(Ui.c(this, "card"));

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(this, "bg"));
        page.addView(topBar(title, v -> finish()));

        status = new TextView(this);
        status.setTextSize(13);
        status.setTextColor(Ui.c(this, "faint"));
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
        if ("mock".equals(type)) loadMocks();
        else loadPage(0);
    }

    private View topBar(String t, View.OnClickListener back) {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Ui.c(this, "card"));
        bar.setPadding(dp(12), dp(10), dp(12), dp(10));
        TextView b = new TextView(this);
        b.setText("←");
        b.setTextSize(20);
        b.setTextColor(Ui.c(this, "sub"));
        b.setPadding(dp(4), dp(2), dp(14), dp(2));
        b.setOnClickListener(back);
        bar.addView(b);
        TextView tt = new TextView(this);
        tt.setText(t);
        tt.setTextSize(17);
        tt.setTextColor(Ui.c(this, "text"));
        tt.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(tt);
        return bar;
    }

    // ------------------------------ 错题 / 收藏 ------------------------------

    private void loadPage(int offset) {
        status.setText("加载中…");
        Api.Cb cb = new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                runOnUiThread(() -> {
                    total = d.optInt("total", 0);
                    JSONArray rows = d.optJSONArray("items");
                    if (offset == 0) body.removeAllViews();
                    status.setText("共 " + total + " 道，已显示 " + Math.min(loadedCount(), total));
                    if (rows == null || rows.length() == 0) {
                        if (offset == 0) body.addView(emptyTip());
                        return;
                    }
                    for (int i = 0; i < rows.length(); i++) {
                        JSONObject q = rows.optJSONObject(i);
                        if (q != null) body.addView(wrap(questionCard(q)));
                    }
                    if (loadedCount() < total) body.addView(moreBtn());
                });
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> status.setText("加载失败：" + m));
            }
        };
        if ("collect".equals(type)) Api.fenbiCollects(new Prefs(this).token(), 20, offset, cb);
        else Api.fenbiWrong(new Prefs(this).token(), 20, offset, cb);
    }

    private int loadedCount() {
        int n = 0;
        for (int i = 0; i < body.getChildCount(); i++) {
            Object tag = body.getChildAt(i).getTag();
            if ("q".equals(tag)) n++;
        }
        return n;
    }

    private View questionCard(JSONObject q) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(12), dp(11), dp(12), dp(11));
        card.setBackground(Ui.card(this, 12));
        card.setTag("q");

        // 标签行：模块 · 知识点 · 难度
        StringBuilder tag = new StringBuilder();
        String module = q.optString("module", "");
        String kp = q.optString("keypoint", "");
        if (!module.isEmpty()) tag.append(module);
        if (!kp.isEmpty()) tag.append(" · ").append(kp);
        int diff = q.optInt("difficulty", 0);
        if (diff > 0) tag.append("　难度 ").append(diff).append("/10");
        if (tag.length() > 0) {
            TextView t = new TextView(this);
            t.setText(tag.toString());
            t.setTextSize(11.5f);
            t.setTextColor(Ui.c(this, "brandDark"));
            card.addView(t);
        }

        TextView content = new TextView(this);
        String text = previewText(q.optString("content", ""));
        content.setText(text);
        content.setTextSize(14);
        content.setTextColor(Ui.c(this, "text"));
        content.setLineSpacing(dp(2), 1f);
        content.setPadding(0, dp(5), 0, 0);
        card.addView(content);

        card.setOnClickListener(v -> showQuestionDialog(q));
        return card;
    }

    /** 列表预览文本：图片标记归一为「🖼 含图」提示（批次28）。 */
    private String previewText(String raw) {
        String text = raw.replaceAll("!\\[图\\d*\\]\\([^)]*\\)", "🖼")
                .replaceAll("\\[图\\d*\\]", "🖼").trim();
        if (text.length() > 60) text = text.substring(0, 60) + "…";
        return text;
    }

    /** 资料（材料）区块：浅底圆角卡，资料分析题的图表/文字材料（批次28）。 */
    private View materialBlock(String material) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(10), dp(9), dp(10), dp(9));
        box.setBackground(Ui.round(this, "cardAlt", 10));
        TextView head = new TextView(this);
        head.setText("📋 资料");
        head.setTextSize(12.5f);
        head.setTypeface(Typeface.DEFAULT_BOLD);
        head.setTextColor(Ui.c(this, "brandDark"));
        box.addView(head);
        box.addView(Ui.richTextView(this, material, 13f, "sub"));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.bottomMargin = dp(10);
        box.setLayoutParams(lp);
        return box;
    }

    /** 错题/收藏完整内容弹窗（批次28：自建弹窗 + 资料区块 + 图文混排）。 */
    private void showQuestionDialog(JSONObject q) {
        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);

        String material = q.optString("material", "").trim();
        if (!material.isEmpty()) body.addView(materialBlock(material));

        body.addView(Ui.richTextView(this, q.optString("content", "").trim(), 14.5f, "text"));

        JSONArray opts = q.optJSONArray("options");
        if (opts != null && opts.length() > 0) {
            String[] letters = {"A", "B", "C", "D", "E", "F", "G", "H"};
            for (int i = 0; i < opts.length() && i < letters.length; i++) {
                TextView o = new TextView(this);
                o.setText(letters[i] + ". " + opts.optString(i, "").trim());
                o.setTextSize(14);
                o.setTextColor(Ui.c(this, "sub"));
                o.setLineSpacing(dp(2), 1f);
                o.setPadding(0, dp(4), 0, 0);
                body.addView(o);
            }
        }

        TextView ans = new TextView(this);
        String answer = q.optString("answer", "").trim();
        ans.setText(answer.isEmpty() ? "答案：—" : "✅ 答案：" + answer);
        ans.setTextSize(14);
        ans.setTypeface(Typeface.DEFAULT_BOLD);
        ans.setTextColor(Ui.c(this, "green"));
        ans.setPadding(0, dp(12), 0, 0);
        body.addView(ans);

        String analysis = q.optString("analysis", "").trim();
        if (!analysis.isEmpty()) {
            TextView ah = new TextView(this);
            ah.setText("📖 解析");
            ah.setTextSize(13.5f);
            ah.setTypeface(Typeface.DEFAULT_BOLD);
            ah.setTextColor(Ui.c(this, "brandDark"));
            ah.setPadding(0, dp(10), 0, 0);
            body.addView(ah);
            body.addView(Ui.richTextView(this, analysis, 13.5f, "sub"));
        }

        String title = (q.optString("module", "")
                + (q.optString("keypoint", "").isEmpty() ? ""
                : " · " + q.optString("keypoint"))).trim();
        Ui.centerDialog(this, title.isEmpty() ? "题目详情" : title, body,
                "关闭", null, null, null).show();
    }

    // ------------------------------ 模考试卷 ------------------------------

    private void loadMocks() {
        status.setText("加载中…");
        Api.fenbiMocks(new Prefs(this).token(), new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                runOnUiThread(() -> {
                    JSONArray rows = d.optJSONArray("exams");
                    body.removeAllViews();
                    status.setText(rows == null ? "" : "已同步 " + rows.length() + " 套试卷");
                    if (rows == null || rows.length() == 0) {
                        body.addView(emptyTip());
                        return;
                    }
                    for (int i = 0; i < rows.length(); i++) {
                        JSONObject m = rows.optJSONObject(i);
                        if (m != null) body.addView(wrap(mockCard(m)));
                    }
                });
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> status.setText("加载失败：" + m));
            }
        });
    }

    private View mockCard(JSONObject m) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(12), dp(11), dp(12), dp(11));
        card.setBackground(Ui.card(this, 12));
        Ui.pressScale(card);

        TextView name = new TextView(this);
        name.setText(m.optString("name", "未命名试卷"));
        name.setTextSize(14.5f);
        name.setTextColor(Ui.c(this, "text"));
        name.setTypeface(Typeface.DEFAULT_BOLD);
        card.addView(name);

        double fullmark = m.optDouble("fullmark", 0);
        double score = m.optDouble("score", 0);
        int qc = m.optInt("question_count", 0);
        int cc = m.optInt("correct_count", 0);
        int wc = m.optInt("wrong_count", 0);
        int ua = m.optInt("unanswered", 0);
        int dur = m.optInt("duration_sec", 0);

        TextView scoreTv = new TextView(this);
        scoreTv.setText(String.format("得分 %.1f / %.0f　用时 %d 分 %02d 秒",
                score, fullmark, dur / 60, dur % 60));
        scoreTv.setTextSize(13);
        scoreTv.setTextColor(fullmark > 0 && score / fullmark >= 0.6
                ? Ui.c(this, "green") : Ui.c(this, "red"));
        scoreTv.setPadding(0, dp(5), 0, 0);
        card.addView(scoreTv);

        TextView stat = new TextView(this);
        stat.setText("共 " + qc + " 题 · ✅ " + cc + " · ❌ " + wc
                + (ua > 0 ? " · 未答 " + ua : ""));
        stat.setTextSize(12);
        stat.setTextColor(Ui.c(this, "sub"));
        stat.setPadding(0, dp(3), 0, 0);
        card.addView(stat);

        String submit = m.optString("submit_time", "");
        if (!submit.isEmpty()) {
            TextView st = new TextView(this);
            st.setText("提交于 " + submit);
            st.setTextSize(11);
            st.setTextColor(Ui.c(this, "faint"));
            st.setPadding(0, dp(3), 0, 0);
            card.addView(st);
        }

        card.setOnClickListener(v -> showMockDetail(m.optLong("id")));
        return card;
    }

    /** 整卷明细弹窗（批次28：自建弹窗 + 错题展开解析 + 图文混排）。 */
    private void showMockDetail(long examId) {
        status.setText("加载试卷明细…");
        Api.fenbiMockDetail(new Prefs(this).token(), examId, new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                runOnUiThread(() -> {
                    status.setText("");
                    LinearLayout body = new LinearLayout(FenbiDataActivity.this);
                    body.setOrientation(LinearLayout.VERTICAL);
                    JSONArray answers = d.optJSONArray("answers");
                    int shown = 0;
                    if (answers != null) {
                        for (int i = 0; i < answers.length(); i++) {
                            JSONObject a = answers.optJSONObject(i);
                            if (a == null) continue;
                            body.addView(mockAnswerBlock(a, ++shown));
                        }
                        if (answers.length() == 0) {
                            TextView e = new TextView(FenbiDataActivity.this);
                            e.setText("暂无作答明细");
                            e.setTextSize(13);
                            e.setTextColor(Ui.c(FenbiDataActivity.this, "faint"));
                            body.addView(e);
                        }
                    }
                    Ui.centerDialog(FenbiDataActivity.this,
                            d.optString("name", "试卷明细"), body,
                            "关闭", null, null, null).show();
                });
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> status.setText("加载失败：" + m));
            }
        });
    }

    /** 模考单题块：对题一行摘要；错题/未答题展开答案与解析（批次28）。 */
    private View mockAnswerBlock(JSONObject a, int idx) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(0, dp(8), 0, dp(8));

        int st = a.optInt("status", 0);
        String mark = st == 1 ? "✅" : st == 2 ? "❌" : "⬜";
        String content = previewText(a.optString("content", ""));
        if (content.length() > 50) content = content.substring(0, 50) + "…";

        TextView head = new TextView(this);
        head.setText(mark + " " + idx + ". " + content);
        head.setTextSize(13);
        head.setTextColor(Ui.c(this, "text"));
        head.setLineSpacing(dp(2), 1f);
        box.addView(head);

        String ua = a.optString("user_answer", "");
        String ca = prettyAnswer(a.optString("correct_answer", ""));
        if (st != 1) {
            TextView row = new TextView(this);
            row.setText("   你的答案：" + (ua.isEmpty() || "[]".equals(ua)
                    ? "未答" : prettyAnswer(ua)) + "　正确：" + ca);
            row.setTextSize(12.5f);
            row.setTextColor(Ui.c(this, "red"));
            row.setPadding(0, dp(3), 0, 0);
            box.addView(row);
        }

        // 批次28：解析不再缺席（此前模考明细只有对错，无解析）
        String analysis = a.optString("analysis", "").trim();
        if (!analysis.isEmpty() && st != 1) {
            TextView ah = new TextView(this);
            ah.setText("   📖 解析");
            ah.setTextSize(12.5f);
            ah.setTypeface(Typeface.DEFAULT_BOLD);
            ah.setTextColor(Ui.c(this, "brandDark"));
            ah.setPadding(0, dp(5), 0, 0);
            box.addView(ah);
            box.addView(Ui.richTextView(this, analysis, 12.5f, "sub"));
        }
        View line = new View(this);
        line.setBackgroundColor(Ui.c(this, "line"));
        box.addView(line, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, Math.max(1, dp(1))));
        return box;
    }

    /** 粉笔答案 JSON（如 ["A"]）→ 可读文本（批次28）。 */
    private String prettyAnswer(String raw) {
        String s = raw == null ? "" : raw.trim();
        if (s.startsWith("[") && s.endsWith("]")) {
            try {
                JSONArray arr = new JSONArray(s);
                StringBuilder sb = new StringBuilder();
                for (int i = 0; i < arr.length(); i++) {
                    sb.append(arr.opt(i)).append(i < arr.length() - 1 ? "、" : "");
                }
                return sb.length() > 0 ? sb.toString() : "—";
            } catch (Exception ignored) { }
        }
        return s.isEmpty() ? "—" : s;
    }

    // ------------------------------ 通用 ------------------------------

    private View emptyTip() {
        TextView t = new TextView(this);
        t.setText("\n还没有同步到数据。\n\n去「粉笔绑定」页点「🚀 生成我的提升计划」，\n"
                + "会自动拉取粉笔错题/收藏/模考记录。");
        t.setTextSize(13);
        t.setTextColor(Ui.c(this, "faint"));
        t.setGravity(Gravity.CENTER);
        t.setPadding(dp(16), dp(30), dp(16), dp(10));
        return t;
    }

    private View moreBtn() {
        TextView more = Ui.btnSecondary(this, "加载更多", 10);
        more.setOnClickListener(v -> {
            ViewGroup parent = (ViewGroup) more.getParent();
            if (parent != null) parent.removeView(more);
            loadPage(loadedCount());
        });
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(dp(20), dp(10), dp(20), 0);
        more.setLayoutParams(lp);
        return more;
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
