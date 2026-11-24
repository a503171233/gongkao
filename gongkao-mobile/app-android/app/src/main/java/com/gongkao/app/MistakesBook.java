package com.gongkao.app;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 题册页（批次C，Tab「题册」内嵌）：错题本 / 收藏夹 双页签。
 * 错题：GET /mistakes → {mistakes:[{id,question,user_answer,correct_note,question_id,created_at}]}
 *       支持 重练（question_id → 练习页指定题）与删除。
 * 收藏：GET /favorites → {favorites:[{id,question,answer,fav_group,created_at}]}，支持删除。
 */
public class MistakesBook {

    private final Activity act;
    private final Prefs prefs;
    private int mode = 0;               // 0 错题本 / 1 收藏夹
    private LinearLayout listBox;
    private TextView tabMist;
    private TextView tabFav;
    private TextView emptyTip;

    public MistakesBook(Activity act, Prefs prefs) {
        this.act = act;
        this.prefs = prefs;
    }

    public View build() {
        ScrollView scroll = new ScrollView(act);
        scroll.setBackgroundColor(Ui.c(act, "bg"));
        LinearLayout page = new LinearLayout(act);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(14), dp(14), dp(14), dp(20) + dp(56));

        // 页签切换
        LinearLayout tabs = new LinearLayout(act);
        tabs.setOrientation(LinearLayout.HORIZONTAL);
        tabMist = tabBtn("❌ 错题本");
        tabFav = tabBtn("⭐ 收藏夹");
        tabs.addView(tabMist, halfLp());
        tabs.addView(tabFav, halfLp());
        page.addView(tabs);

        emptyTip = new TextView(act);
        emptyTip.setTextSize(13.5f);
        emptyTip.setTextColor(Ui.c(act, "faint"));
        emptyTip.setGravity(Gravity.CENTER);
        emptyTip.setPadding(0, dp(30), 0, 0);
        page.addView(emptyTip);

        listBox = new LinearLayout(act);
        listBox.setOrientation(LinearLayout.VERTICAL);
        page.addView(listBox);

        scroll.addView(page, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        paintTabs();
        return scroll;
    }

    public void refresh() {
        load();
    }

    private TextView tabBtn(String label) {
        TextView t = new TextView(act);
        t.setText(label);
        t.setTextSize(14.5f);
        t.setGravity(Gravity.CENTER);
        t.setPadding(dp(10), dp(12), dp(10), dp(12));
        t.setOnClickListener(v -> {
            mode = (t == tabMist) ? 0 : 1;
            paintTabs();
        });
        return t;
    }

    private void paintTabs() {
        GradientDrawable on = new GradientDrawable();
        on.setCornerRadius(dp(12));
        on.setColor(Ui.c(act, "brand"));
        GradientDrawable off = new GradientDrawable();
        off.setCornerRadius(dp(12));
        off.setColor(Ui.c(act, "card"));
        off.setStroke(dp(1), Ui.c(act, "line"));
        tabMist.setBackground(mode == 0 ? on : off);
        tabMist.setTextColor(mode == 0 ? Color.WHITE : Ui.c(act, "sub"));
        tabFav.setBackground(mode == 1 ? on : off);
        tabFav.setTextColor(mode == 1 ? Color.WHITE : Ui.c(act, "sub"));
        load();
    }

    private void load() {
        // 骨架屏加载态（批次G）：骨架卡原位占位，数据到达 paintList 时自动替换
        emptyTip.setVisibility(View.GONE);
        listBox.removeAllViews();
        listBox.addView(Ui.skeleton(act, 3));
        if (mode == 0) {
            Api.listMistakes(prefs.token(), new Api.Cb() {
                @Override
                public void ok(JSONObject d) {
                    JSONArray arr = d.optJSONArray("mistakes");
                    act.runOnUiThread(() -> paintList(arr, true));
                }

                @Override
                public void err(String m) {
                    act.runOnUiThread(() -> {
                        listBox.removeAllViews();
                        emptyTip.setText("加载失败：" + m);
                        emptyTip.setVisibility(View.VISIBLE);
                    });
                }
            });
        } else {
            Api.listFavorites(prefs.token(), new Api.Cb() {
                @Override
                public void ok(JSONObject d) {
                    JSONArray arr = d.optJSONArray("favorites");
                    act.runOnUiThread(() -> paintList(arr, false));
                }

                @Override
                public void err(String m) {
                    act.runOnUiThread(() -> {
                        listBox.removeAllViews();
                        emptyTip.setText("加载失败：" + m);
                        emptyTip.setVisibility(View.VISIBLE);
                    });
                }
            });
        }
    }

    private void paintList(JSONArray arr, boolean isMistake) {
        listBox.removeAllViews();
        if (arr == null || arr.length() == 0) {
            emptyTip.setVisibility(View.VISIBLE);
            emptyTip.setText(isMistake ? "错题本还是空的\n练习中答错的题会自动收进来" : "还没有收藏\n在问答结果里可以收藏优质内容");
            return;
        }
        emptyTip.setVisibility(View.GONE);
        for (int i = 0; i < arr.length(); i++) {
            JSONObject it = arr.optJSONObject(i);
            if (it == null) continue;
            listBox.addView(card(it, isMistake));
        }
    }

    private View card(JSONObject it, boolean isMistake) {
        long id = it.optLong("id");
        LinearLayout card = new LinearLayout(act);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(act, "card"));
        bg.setStroke(dp(1), Ui.c(act, "line"));
        card.setBackground(bg);

        TextView q = new TextView(act);
        q.setText(it.optString("question", ""));
        q.setTextSize(14.5f);
        q.setTextColor(Ui.c(act, "text"));
        q.setMaxLines(3);
        q.setEllipsize(android.text.TextUtils.TruncateAt.END);
        card.addView(q);

        if (isMistake) {
            TextView ua = new TextView(act);
            ua.setText("我的答案：" + it.optString("user_answer", "—"));
            ua.setTextSize(13);
            ua.setTextColor(Ui.c(act, "red"));
            ua.setPadding(0, dp(6), 0, 0);
            card.addView(ua);
            TextView ca = new TextView(act);
            ca.setText("✅ " + it.optString("correct_note", "—"));
            ca.setTextSize(13);
            ca.setTextColor(Ui.c(act, "green"));
            ca.setPadding(0, dp(3), 0, 0);
            card.addView(ca);
        } else {
            TextView a = new TextView(act);
            a.setText(it.optString("answer", ""));
            a.setTextSize(13);
            a.setTextColor(Ui.c(act, "sub"));
            a.setMaxLines(4);
            a.setEllipsize(android.text.TextUtils.TruncateAt.END);
            a.setPadding(0, dp(6), 0, 0);
            card.addView(a);
            String grp = it.optString("fav_group", "");
            if (!grp.isEmpty()) {
                TextView g = new TextView(act);
                g.setText("📁 " + grp);
                g.setTextSize(11.5f);
                g.setTextColor(Ui.c(act, "brandDark"));
                g.setPadding(0, dp(4), 0, 0);
                card.addView(g);
            }
        }

        // 操作行：重练（仅错题带 question_id）/ 删除
        LinearLayout ops = new LinearLayout(act);
        ops.setOrientation(LinearLayout.HORIZONTAL);
        ops.setGravity(Gravity.RIGHT);
        long qid = it.optLong("question_id", 0);
        if (isMistake && qid > 0) {
            TextView redo = new TextView(act);
            redo.setText("🔁 重练");
            redo.setTextSize(13);
            redo.setTextColor(Ui.c(act, "brandDark"));
            redo.setPadding(dp(10), dp(8), dp(10), dp(4));
            redo.setOnClickListener(v -> {
                android.content.Intent i = new android.content.Intent(act, PracticeActivity.class);
                i.putExtra("mode", "");
                i.putExtra("category", "");
                // 指定题重练：用 question_id 由后端 /practice/next 抽回原题
                i.putExtra("mode", "redo");
                i.putExtra("question_id", qid);
                act.startActivity(i);
            });
            ops.addView(redo);
        }
        TextView del = new TextView(act);
        del.setText(isMistake ? "🗑 移出错题本" : "🗑 取消收藏");
        del.setTextSize(13);
        del.setTextColor(Ui.c(act, "faint"));
        del.setPadding(dp(10), dp(8), 0, dp(4));
        del.setOnClickListener(v -> Ui.dialogBuilder(act)
                .setMessage(isMistake ? "确定移出错题本？" : "确定取消收藏？")
                .setPositiveButton("确定", (d, w) -> {
                    if (isMistake) Api.deleteMistake(prefs.token(), id, refreshCb());
                    else Api.deleteFavorite(prefs.token(), id, refreshCb());
                })
                .setNegativeButton("取消", null)
                .show());
        ops.addView(del);
        card.addView(ops);

        LinearLayout wrap = new LinearLayout(act);
        wrap.setPadding(dp(2), dp(5), dp(2), dp(5));
        wrap.addView(card, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return wrap;
    }

    private Api.Cb refreshCb() {
        return new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                act.runOnUiThread(() -> load());
            }

            @Override
            public void err(String m) {
                act.runOnUiThread(() -> {
                    emptyTip.setVisibility(View.VISIBLE);
                    emptyTip.setText("操作失败：" + m);
                });
            }
        };
    }

    private LinearLayout.LayoutParams halfLp() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        lp.setMargins(dp(3), 0, dp(3), dp(8));
        return lp;
    }

    private int dp(int v) {
        return Math.round(v * act.getResources().getDisplayMetrics().density);
    }
}
