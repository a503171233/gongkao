package com.gongkao.app;

import android.app.Activity;
import android.app.AlertDialog;
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

import java.util.HashMap;
import java.util.Map;

/**
 * 知识体系页（批次D升级，原 v2.7 占位）：知识树浏览 + 掌握度着色 + 节点描述 + 一键练题。
 * 契约：GET /public/knowledge/tree?teacher_id → {nodes[{node_id,name,category,
 *          description,children[]}], total}（嵌套树，公开接口）
 *      GET /knowledge/mastery?teacher_id → {mastery{nodeId:{done,correct,accuracy,level}},
 *          total_done}（需登录；level ∈ none/learning/mastered）
 *      GET /practice/by-knowledge?node_id → 一道题（跳 PracticeActivity 指定 question_id）。
 */
public class KnowledgeActivity extends BaseActivity {

    private Prefs prefs;
    private LinearLayout body;
    private TextView status;
    private final Map<String, JSONObject> masteryMap = new HashMap<>();
    private JSONArray roots;
    private String firstModuleName = "";     // 树为空时提示用

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);

        getWindow().requestFeature(android.view.Window.FEATURE_NO_TITLE);
        if (android.os.Build.VERSION.SDK_INT >= 23) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        }
        getWindow().setStatusBarColor(Ui.c(KnowledgeActivity.this, "card"));

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(KnowledgeActivity.this, "bg"));

        page.addView(topBar("🧠 知识体系", v -> finish()));

        status = new TextView(this);
        status.setTextSize(13);
        status.setTextColor(Ui.c(KnowledgeActivity.this, "faint"));
        status.setGravity(Gravity.CENTER);
        status.setPadding(dp(16), dp(30), dp(16), dp(10));
        page.addView(status);

        ScrollView scroll = new ScrollView(this);
        scroll.setVisibility(View.GONE);
        body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(12), dp(6), dp(12), dp(30));
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
        bar.setBackgroundColor(Ui.c(KnowledgeActivity.this, "card"));
        bar.setPadding(dp(12), dp(10), dp(12), dp(10));
        TextView b = new TextView(this);
        b.setText("←");
        b.setTextSize(20);
        b.setTextColor(Ui.c(KnowledgeActivity.this, "sub"));
        b.setPadding(dp(4), dp(2), dp(14), dp(2));
        b.setOnClickListener(back);
        bar.addView(b);
        TextView t = new TextView(this);
        t.setText(title);
        t.setTextSize(17);
        t.setTextColor(Ui.c(KnowledgeActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(t);
        return bar;
    }

    private void load() {
        status.setText("加载知识树…");
        Api.knowledgeTree(prefs.token(), prefs.teacherId(), new Api.Cb() {
            @Override
            public void ok(JSONObject tree) {
                Api.knowledgeMastery(prefs.token(), prefs.teacherId(), new Api.Cb() {
                    @Override
                    public void ok(JSONObject m) {
                        runOnUiThread(() -> {
                            JSONObject map = m.optJSONObject("mastery");
                            masteryMap.clear();
                            if (map != null) {
                                java.util.Iterator<String> it = map.keys();
                                while (it.hasNext()) {
                                    String k = it.next();
                                    masteryMap.put(k, map.optJSONObject(k));
                                }
                            }
                            paint(tree.optJSONArray("nodes"), m.optInt("total_done"));
                        });
                    }

                    @Override
                    public void err(String e2) {
                        // 掌握度失败不阻塞树浏览
                        runOnUiThread(() -> {
                            masteryMap.clear();
                            paint(tree.optJSONArray("nodes"), 0);
                        });
                    }
                });
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> status.setText("加载失败：" + m));
            }
        });
    }

    private void paint(JSONArray nodes, int totalDone) {
        status.setVisibility(View.GONE);
        android.view.View sv = body.getParent() instanceof ViewGroup
                ? (View) body.getParent() : null;
        if (sv != null) sv.setVisibility(View.VISIBLE);
        body.removeAllViews();
        roots = nodes;

        TextView sum = new TextView(this);
        sum.setText("共 " + (nodes == null ? 0 : nodes.length()) + " 个一级模块"
                + (totalDone > 0 ? " · 已练 " + totalDone + " 题" : " · 从任意模块开始练起"));
        sum.setTextSize(12);
        sum.setTextColor(Ui.c(KnowledgeActivity.this, "faint"));
        sum.setPadding(dp(4), dp(2), 0, dp(8));
        body.addView(sum);

        if (nodes == null || nodes.length() == 0) {
            TextView empty = new TextView(this);
            empty.setText("知识树暂未配置\n请联系老师在小程序/管理后台搭建");
            empty.setTextSize(13.5f);
            empty.setTextColor(Ui.c(KnowledgeActivity.this, "faint"));
            empty.setGravity(Gravity.CENTER);
            empty.setPadding(0, dp(60), 0, 0);
            body.addView(empty);
            return;
        }

        for (int i = 0; i < nodes.length(); i++) {
            JSONObject n = nodes.optJSONObject(i);
            if (n == null) continue;
            body.addView(nodeRow(n, 0));
            JSONArray kids = n.optJSONArray("children");
            if (kids != null) {
                // 一级模块默认展开二级
                for (int j = 0; j < kids.length(); j++) {
                    JSONObject k = kids.optJSONObject(j);
                    if (k != null) body.addView(nodeRow(k, 1));
                }
            }
        }
    }

    /** 树节点行：缩进 + 名称 + 掌握度点。有子节点的点击展开/收起（就地插入）。 */
    private View nodeRow(JSONObject node, int depth) {
        boolean leaf = node.optJSONArray("children") == null
                || node.optJSONArray("children").length() == 0;
        JSONObject m = masteryMap.get(node.optString("node_id"));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(8 + depth * 18), dp(11), dp(8), dp(11));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(9));
        bg.setColor(depth == 0 ? Ui.c(KnowledgeActivity.this, "card") : Ui.c(KnowledgeActivity.this, "cardAlt"));
        bg.setStroke(dp(1), Ui.c(KnowledgeActivity.this, "line"));
        row.setBackground(bg);

        TextView arrow = new TextView(this);
        arrow.setText(leaf ? (depth == 0 ? "📂" : "·") : "📁");
        arrow.setTextSize(13);
        arrow.setTextColor(Ui.c(KnowledgeActivity.this, "brandDark"));
        arrow.setWidth(dp(24));
        row.addView(arrow);

        TextView name = new TextView(this);
        name.setText(node.optString("name", ""));
        name.setTextSize(depth == 0 ? 14.5f : 13.5f);
        name.setTextColor(depth == 0 ? Ui.c(KnowledgeActivity.this, "text") : Ui.c(KnowledgeActivity.this, "sub"));
        name.setTypeface(depth == 0 ? Typeface.DEFAULT_BOLD : Typeface.DEFAULT);
        LinearLayout.LayoutParams nlp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        nlp.setMargins(dp(2), 0, dp(6), 0);
        row.addView(name, nlp);

        if (m != null && leaf) {
            double acc = m.optDouble("accuracy", -1);
            String level = m.optString("level", "none");
            TextView badge = new TextView(this);
            if (acc >= 0) {
                badge.setText((level.equals("mastered") ? "✓" : "")
                        + Math.round(acc * 100) + "%");
            } else {
                badge.setText(m.optInt("done") > 0 ? "批改中" : "未练");
            }
            badge.setTextSize(11);
            badge.setTextColor("mastered".equals(level) ? Ui.c(KnowledgeActivity.this, "green")
                    : "learning".equals(level) ? Ui.c(KnowledgeActivity.this, "brandDark") : Ui.c(KnowledgeActivity.this, "faint"));
            row.addView(badge);
        }

        final JSONObject fNode = node;
        final int fDepth = depth;
        row.setOnClickListener(v -> {
            JSONArray kids = fNode.optJSONArray("children");
            if (kids != null && kids.length() > 0) {
                toggleChildren(row, fNode, fDepth);
            } else {
                showNodeDialog(fNode);
            }
        });
        return wrapRow(row);
    }

    /** 展开/收起：就地在该行下方插入/移除子节点行。 */
    private void toggleChildren(LinearLayout row, JSONObject node, int depth) {
        LinearLayout parent = (LinearLayout) row.getParent();   // wrapRow 外壳
        int idx = body.indexOfChild(parent);
        boolean expanded = idx >= 0 && idx + 1 < body.getChildCount()
                && body.getChildAt(idx + 1).getTag() != null
                && Boolean.TRUE.equals(body.getChildAt(idx + 1).getTag());
        if (expanded) {
            body.removeViewAt(idx + 1);
            return;
        }
        JSONArray kids = node.optJSONArray("children");
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setTag(Boolean.TRUE);
        for (int i = 0; i < kids.length(); i++) {
            JSONObject k = kids.optJSONObject(i);
            if (k != null) box.addView(nodeRow(k, depth + 1));
        }
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        body.addView(box, idx + 1, lp);
    }

    private LinearLayout wrapRow(LinearLayout row) {
        LinearLayout wrap = new LinearLayout(this);
        wrap.setPadding(dp(2), dp(3), dp(2), dp(3));
        wrap.addView(row, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return wrap;
    }

    /** 节点详情弹层：描述 + 掌握度 + 去练题。 */
    private void showNodeDialog(JSONObject node) {
        String nodeId = node.optString("node_id");
        JSONObject m = masteryMap.get(nodeId);
        StringBuilder msg = new StringBuilder();
        String desc = node.optString("description", "");
        if (!desc.isEmpty()) msg.append(desc.trim()).append("\n\n");
        if (m != null) {
            msg.append("已练 ").append(m.optInt("done")).append(" 题");
            double acc = m.optDouble("accuracy", -1);
            if (acc >= 0) msg.append(" · 正确率 ").append(Math.round(acc * 100)).append("%");
        }

        LinearLayout btnWrap = new LinearLayout(this);
        btnWrap.setOrientation(LinearLayout.HORIZONTAL);
        btnWrap.setGravity(Gravity.CENTER);
        btnWrap.setPadding(0, dp(8), 0, 0);
        TextView go = new TextView(this);
        go.setText("✏️ 去练这个知识点");
        go.setTextSize(14);
        go.setTextColor(Color.WHITE);
        GradientDrawable gbg = new GradientDrawable();
        gbg.setCornerRadius(dp(20));
        gbg.setColor(Ui.c(KnowledgeActivity.this, "brand"));
        go.setBackground(gbg);
        go.setPadding(dp(20), dp(9), dp(20), dp(9));
        btnWrap.addView(go);

        AlertDialog dlg = Ui.dialogBuilder(this)
                .setTitle(node.optString("name", ""))
                .setMessage(msg.length() == 0 ? "暂无节点说明，直接开练！" : msg.toString())
                .setNegativeButton("关闭", null)
                .create();
        dlg.show();
        go.setOnClickListener(v -> {
            dlg.dismiss();
            status.setVisibility(View.VISIBLE);
            status.setText("抽题中…");
            Api.practiceByKnowledge(prefs.token(), nodeId, new Api.Cb() {
                @Override
                public void ok(JSONObject q) {
                    runOnUiThread(() -> {
                        long qid = q.optLong("question_id", q.optLong("id"));
                        if (qid <= 0) {
                            status.setText("该知识点暂无题目");
                            return;
                        }
                        android.content.Intent it = new android.content.Intent(
                                KnowledgeActivity.this, PracticeActivity.class);
                        it.putExtra("question_id", qid);   // PracticeActivity 用 getLongExtra
                        startActivity(it);
                        status.setVisibility(View.GONE);
                    });
                }

                @Override
                public void err(String e2) {
                    runOnUiThread(() -> {
                        status.setText("抽题失败：" + e2);
                        new android.os.Handler(android.os.Looper.getMainLooper())
                                .postDelayed(() -> status.setVisibility(View.GONE), 2000);
                    });
                }
            });
        });
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
