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
 * 激励排行页（批次D）：我的积分/等级/连击 + 成就墙 + 学霸榜 Top N。
 * 契约：GET /me/incentive →
 *   {points, level, level_name, next_level, next_level_name, streak_days,
 *    achievements[{code,title,icon,desc,achieved_at}]}
 * GET /me/incentive/board → {board[{username,points,level,streak_days,total_answers}], my_rank}
 */
public class IncentiveBoardActivity extends BaseActivity {

    private Prefs prefs;
    private LinearLayout body;
    private TextView status;
    private ScrollView scroll;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);

        getWindow().requestFeature(android.view.Window.FEATURE_NO_TITLE);
        if (android.os.Build.VERSION.SDK_INT >= 23) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        }
        getWindow().setStatusBarColor(Ui.c(IncentiveBoardActivity.this, "card"));

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(IncentiveBoardActivity.this, "bg"));

        page.addView(topBar("🏆 激励排行", v -> finish()));

        status = new TextView(this);
        status.setTextSize(13);
        status.setTextColor(Ui.c(IncentiveBoardActivity.this, "faint"));
        status.setGravity(Gravity.CENTER);
        status.setPadding(dp(16), dp(30), dp(16), dp(10));
        page.addView(status);

        scroll = new ScrollView(this);
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

    private View topBar(String title, View.OnClickListener back) {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Ui.c(IncentiveBoardActivity.this, "card"));
        bar.setPadding(dp(12), dp(10), dp(12), dp(10));
        TextView b = new TextView(this);
        b.setText("←");
        b.setTextSize(20);
        b.setTextColor(Ui.c(IncentiveBoardActivity.this, "sub"));
        b.setPadding(dp(4), dp(2), dp(14), dp(2));
        b.setOnClickListener(back);
        bar.addView(b);
        TextView t = new TextView(this);
        t.setText(title);
        t.setTextSize(17);
        t.setTextColor(Ui.c(IncentiveBoardActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(t);
        return bar;
    }

    private void load() {
        status.setText("加载中…");
        Api.incentive(prefs.token(), new Api.Cb() {
            @Override
            public void ok(JSONObject inc) {
                Api.incentiveBoard(prefs.token(), new Api.Cb() {
                    @Override
                    public void ok(JSONObject bd) {
                        runOnUiThread(() -> {
                            status.setVisibility(View.GONE);
                            scroll.setVisibility(View.VISIBLE);
                            body.removeAllViews();
                            paint(inc, bd);
                        });
                    }

                    @Override
                    public void err(String m) {
                        // 排行失败不阻塞我的面板
                        runOnUiThread(() -> {
                            status.setVisibility(View.GONE);
                            scroll.setVisibility(View.VISIBLE);
                            body.removeAllViews();
                            paint(inc, new JSONObject());
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

    private void paint(JSONObject inc, JSONObject bd) {
        // ---- 我的积分卡 ----
        LinearLayout mine = new LinearLayout(this);
        mine.setOrientation(LinearLayout.VERTICAL);
        mine.setGravity(Gravity.CENTER_HORIZONTAL);
        mine.setPadding(dp(16), dp(16), dp(16), dp(14));
        GradientDrawable mg = new GradientDrawable();
        mg.setCornerRadius(dp(14));
        mg.setColor(0xFF2B2620);
        mine.setBackground(mg);

        TextView lv = new TextView(this);
        lv.setText(inc.optString("level_icon", "🎖") + " " + inc.optString("level_name", "学员")
                + " · Lv." + inc.optInt("level"));
        lv.setTextSize(16);
        lv.setTextColor(Ui.c(IncentiveBoardActivity.this, "heroGold"));
        lv.setTypeface(Typeface.DEFAULT_BOLD);
        mine.addView(lv);

        TextView pts = new TextView(this);
        pts.setText(String.valueOf(inc.optInt("points")));
        pts.setTextSize(40);
        pts.setTextColor(Color.WHITE);
        pts.setTypeface(Typeface.DEFAULT_BOLD);
        pts.setPadding(0, dp(4), 0, 0);
        mine.addView(pts);
        TextView ptsL = new TextView(this);
        ptsL.setText("累计积分");
        ptsL.setTextSize(11.5f);
        ptsL.setTextColor(0xFFBBAF9A);
        mine.addView(ptsL);

        // 升级进度条
        int next = inc.optInt("next_level", 0);
        if (next > 0) {
            int points = inc.optInt("points");
            int prevTh = next - 100;   // 后端等级阈值线性：下一级阈值-100 为本段起点
            float frac = Math.max(0f, Math.min(1f,
                    (points - prevTh) / (float) Math.max(1, next - prevTh)));
            LinearLayout barBg = new LinearLayout(this);
            barBg.setPadding(dp(24), dp(10), dp(24), 0);
            View bar = new View(this);
            GradientDrawable bg = new GradientDrawable();
            bg.setCornerRadius(dp(3));
            bg.setColor(0xFF4A4238);
            bar.setBackground(bg);
            barBg.addView(bar, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, dp(7)));
            View fill = new View(this);
            GradientDrawable fg = new GradientDrawable();
            fg.setCornerRadius(dp(3));
            fg.setColor(Ui.c(IncentiveBoardActivity.this, "heroGold"));
            LinearLayout fillWrap = new LinearLayout(this);
            fillWrap.addView(fill, new LinearLayout.LayoutParams(
                    Math.max(dp(3), (int) (dp(260) * frac)), dp(7)));
            barBg.addView(fillWrap, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, dp(7)));
            mine.addView(barBg);
            TextView nextT = new TextView(this);
            nextT.setText("距「" + inc.optString("next_level_name", "下一级")
                    + "」还差 " + Math.max(0, next - points) + " 分");
            nextT.setTextSize(11);
            nextT.setTextColor(0xFFBBAF9A);
            nextT.setPadding(0, dp(5), 0, 0);
            mine.addView(nextT);
        }

        TextView streak = new TextView(this);
        streak.setText("🔥 连续学习 " + inc.optInt("streak_days") + " 天");
        streak.setTextSize(12.5f);
        streak.setTextColor(Ui.c(IncentiveBoardActivity.this, "heroGold"));
        streak.setPadding(0, dp(8), 0, 0);
        mine.addView(streak);
        body.addView(wrapDark(mine));

        // ---- 成就墙 ----
        JSONArray ach = inc.optJSONArray("achievements");
        if (ach != null && ach.length() > 0) {
            body.addView(sectionTitle("🏅 成就墙"));
            LinearLayout grid = new LinearLayout(this);
            grid.setOrientation(LinearLayout.VERTICAL);
            LinearLayout row = null;
            for (int i = 0; i < ach.length(); i++) {
                JSONObject a = ach.optJSONObject(i);
                if (a == null) continue;
                if (i % 3 == 0) {
                    row = new LinearLayout(this);
                    grid.addView(row);
                }
                boolean earned = !a.optString("achieved_at").isEmpty();
                LinearLayout cell = new LinearLayout(this);
                cell.setOrientation(LinearLayout.VERTICAL);
                cell.setGravity(Gravity.CENTER);
                cell.setPadding(dp(4), dp(10), dp(4), dp(10));
                TextView icon = new TextView(this);
                icon.setText(a.optString("icon", "🏅"));
                icon.setTextSize(22);
                icon.setAlpha(earned ? 1f : 0.3f);
                cell.addView(icon);
                TextView tt = new TextView(this);
                tt.setText(a.optString("title", ""));
                tt.setTextSize(11.5f);
                tt.setTextColor(earned ? Ui.c(IncentiveBoardActivity.this, "brandDark") : Ui.c(IncentiveBoardActivity.this, "faint"));
                tt.setPadding(0, dp(4), 0, 0);
                cell.addView(tt);
                TextView ds = new TextView(this);
                ds.setText(a.optString("desc", ""));
                ds.setTextSize(9);
                ds.setTextColor(Ui.c(IncentiveBoardActivity.this, "faint"));
                ds.setGravity(Gravity.CENTER);
                ds.setMaxLines(1);
                ds.setPadding(0, dp(1), 0, 0);
                cell.addView(ds);
                row.addView(cell, new LinearLayout.LayoutParams(
                        0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            }
            LinearLayout gw = card();
            gw.addView(grid);
            body.addView(wrap(gw));
        }

        // ---- 学霸榜 ----
        body.addView(sectionTitle("🥇 学霸榜"));
        JSONArray board = bd.optJSONArray("board");
        if (board != null && board.length() > 0) {
            LinearLayout bc = card();
            int myRank = bd.optInt("my_rank", -1);
            int show = Math.min(board.length(), 20);
            String[] medals = {"🥇", "🥈", "🥉"};
            for (int i = 0; i < show; i++) {
                JSONObject u = board.optJSONObject(i);
                if (u == null) continue;
                LinearLayout row = new LinearLayout(this);
                row.setOrientation(LinearLayout.HORIZONTAL);
                row.setGravity(Gravity.CENTER_VERTICAL);
                row.setPadding(dp(4), dp(9), dp(4), dp(9));
                if (i % 2 == 1) row.setBackgroundColor(Ui.c(IncentiveBoardActivity.this, "cardAlt"));

                TextView rank = new TextView(this);
                rank.setText(i < 3 ? medals[i] : String.valueOf(i + 1));
                rank.setTextSize(i < 3 ? 16 : 13);
                rank.setTextColor(Ui.c(IncentiveBoardActivity.this, "faint"));
                rank.setWidth(dp(34));
                rank.setGravity(Gravity.CENTER);
                row.addView(rank);

                TextView name = new TextView(this);
                name.setText(u.optString("username", "-"));
                name.setTextSize(13.5f);
                name.setTextColor(Ui.c(IncentiveBoardActivity.this, "text"));
                name.setSingleLine(true);
                LinearLayout.LayoutParams nlp = new LinearLayout.LayoutParams(0,
                        ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
                nlp.setMargins(dp(4), 0, dp(4), 0);
                row.addView(name, nlp);

                TextView info = new TextView(this);
                info.setText("Lv." + u.optInt("level") + " · "
                        + u.optInt("total_answers") + " 题");
                info.setTextSize(11);
                info.setTextColor(Ui.c(IncentiveBoardActivity.this, "faint"));
                row.addView(info);

                TextView p = new TextView(this);
                p.setText(u.optInt("points") + " 分");
                p.setTextSize(13);
                p.setTextColor(Ui.c(IncentiveBoardActivity.this, "brandDark"));
                p.setTypeface(Typeface.DEFAULT_BOLD);
                p.setPadding(dp(8), 0, 0, 0);
                row.addView(p);
                bc.addView(row);
            }
            if (myRank > 20) {
                TextView more = new TextView(this);
                more.setText("我的排名：第 " + myRank + " 名（未入前 20）");
                more.setTextSize(12);
                more.setTextColor(Ui.c(IncentiveBoardActivity.this, "faint"));
                more.setGravity(Gravity.CENTER);
                more.setPadding(0, dp(8), 0, 0);
                bc.addView(more);
            }
            body.addView(wrap(bc));
        } else {
            TextView empty = new TextView(this);
            empty.setText("榜单暂无数据，练题上榜！");
            empty.setTextSize(13);
            empty.setTextColor(Ui.c(IncentiveBoardActivity.this, "faint"));
            empty.setGravity(Gravity.CENTER);
            body.addView(empty);
        }
    }

    // ------------------------------ 通用小组件 ------------------------------

    private LinearLayout sectionTitle(String title) {
        TextView t = new TextView(this);
        t.setText(title);
        t.setTextSize(14.5f);
        t.setTextColor(Ui.c(IncentiveBoardActivity.this, "text"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setPadding(dp(4), dp(12), 0, dp(6));
        LinearLayout wrap = new LinearLayout(this);
        wrap.addView(t);
        return wrap;
    }

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(10), dp(6), dp(10), dp(8));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(IncentiveBoardActivity.this, "card"));
        bg.setStroke(dp(1), Ui.c(IncentiveBoardActivity.this, "line"));
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

    private LinearLayout wrapDark(View card) {
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
