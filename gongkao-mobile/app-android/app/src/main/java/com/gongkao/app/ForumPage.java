package com.gongkao.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 论坛页（批次E，Tab「论坛」实装）：版块 chips + 最新/最热排序 + 发帖 + 详情（点赞/回帖/举报）。
 * 契约：GET /forum/categories → {categories[{category,count}]}
 *      GET /forum/posts?category&sort(new/hot)&page&size → {items[{post_id,title,author,
 *          category,likes,views,excerpt,reply_count,created_at}],total}
 *      POST /forum/posts {title,category,content}（3-80 字 / 5-8000 字 / 15s 频控）
 *      GET /forum/posts/{id} → post+liked · GET/POST /forum/posts/{id}/replies
 *      POST /forum/posts/{id}/like · POST /forum/reports {target_kind,target_id,reason}。
 */
public class ForumPage {

    private final Activity act;
    private final Prefs prefs;
    private LinearLayout listView;
    private LinearLayout detailHost;
    private LinearLayout catChips;
    private TextView status;
    private String curCat = "";
    private String curSort = "new";
    private String currentPostId;

    public ForumPage(Activity act, Prefs prefs) {
        this.act = act;
        this.prefs = prefs;
    }

    public View build() {
        FrameLayout root = new FrameLayout(act);
        root.setBackgroundColor(Ui.c(act, "bg"));

        // 列表层
        ScrollView scroll = new ScrollView(act);
        LinearLayout page = new LinearLayout(act);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(14), dp(12), dp(14), dp(20) + dp(56));

        // 顶行：标题 + 发帖按钮
        LinearLayout head = new LinearLayout(act);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView title = new TextView(act);
        title.setText("🌐 学员论坛");
        title.setTextSize(18);
        title.setTextColor(Ui.c(act, "text"));
        title.setTypeface(Typeface.DEFAULT_BOLD);
        head.addView(title, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView newBtn = new TextView(act);
        newBtn.setText("✏️ 发帖");
        newBtn.setTextSize(13);
        newBtn.setTextColor(Color.WHITE);
        GradientDrawable nbg = new GradientDrawable();
        nbg.setCornerRadius(dp(16));
        nbg.setColor(Ui.c(act, "brand"));
        newBtn.setBackground(nbg);
        newBtn.setPadding(dp(14), dp(6), dp(14), dp(6));
        newBtn.setOnClickListener(v -> showNewPostDialog());
        head.addView(newBtn);
        page.addView(head);

        // 版块 chips
        catChips = new LinearLayout(act);
        catChips.setOrientation(LinearLayout.HORIZONTAL);
        catChips.setPadding(0, dp(10), 0, 0);
        HorizontalScrollView hs = new HorizontalScrollView(act);
        hs.setHorizontalScrollBarEnabled(false);
        hs.addView(catChips);
        page.addView(hs);

        // 排序切换
        LinearLayout sortRow = new LinearLayout(act);
        sortRow.setOrientation(LinearLayout.HORIZONTAL);
        sortRow.setPadding(0, dp(8), 0, 0);
        TextView newest = sortChip("🆕 最新", "new");
        TextView hot = sortChip("🔥 最热", "hot");
        sortRow.addView(newest);
        sortRow.addView(hot);
        page.addView(sortRow);

        status = new TextView(act);
        status.setTextSize(12.5f);
        status.setTextColor(Ui.c(act, "faint"));
        status.setPadding(dp(2), dp(10), 0, 0);
        page.addView(status);

        listView = new LinearLayout(act);
        listView.setOrientation(LinearLayout.VERTICAL);
        page.addView(listView);
        scroll.addView(page, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(scroll, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        // 详情层（覆盖列表）
        detailHost = new LinearLayout(act);
        detailHost.setOrientation(LinearLayout.VERTICAL);
        detailHost.setVisibility(View.GONE);
        detailHost.setBackgroundColor(Ui.c(act, "bg"));
        root.addView(detailHost, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        return root;
    }

    public void refresh() {
        if (detailHost != null && detailHost.getVisibility() == View.VISIBLE) return;
        loadCategories();
        loadPosts();
    }

    // ------------------------------ 列表 ------------------------------

    private TextView sortChip(String label, String key) {
        TextView c = new TextView(act);
        c.setText(label);
        c.setTextSize(12.5f);
        c.setPadding(dp(12), dp(5), dp(12), dp(5));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(14));
        boolean sel = curSort.equals(key);
        bg.setColor(sel ? Ui.c(act, "brand") : Ui.c(act, "card"));
        bg.setStroke(dp(1), sel ? Ui.c(act, "brand") : Ui.c(act, "line"));
        c.setBackground(bg);
        c.setTextColor(sel ? Color.WHITE : Ui.c(act, "sub"));
        c.setOnClickListener(v -> {
            curSort = key;
            loadPosts();
        });
        return c;
    }

    private void loadCategories() {
        Api.forumCategories(prefs.token(), new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                runUi(() -> {
                    catChips.removeAllViews();
                    catChips.addView(catChip("全部", ""));
                    JSONArray cs = d.optJSONArray("categories");
                    if (cs != null) {
                        for (int i = 0; i < cs.length(); i++) {
                            JSONObject c = cs.optJSONObject(i);
                            if (c != null)
                                catChips.addView(catChip(c.optString("category", ""),
                                        c.optString("category", "")));
                        }
                    }
                });
            }

            @Override
            public void err(String m) { /* 版块加载失败不阻塞列表 */ }
        });
    }

    private TextView catChip(String label, String key) {
        TextView c = new TextView(act);
        c.setText(label);
        c.setTextSize(12.5f);
        c.setPadding(dp(12), dp(5), dp(12), dp(5));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(14));
        boolean sel = curCat.equals(key);
        bg.setColor(sel ? Ui.c(act, "hero") : Ui.c(act, "card"));
        bg.setStroke(dp(1), sel ? Ui.c(act, "hero") : Ui.c(act, "line"));
        c.setBackground(bg);
        c.setTextColor(sel ? Ui.c(act, "heroGold") : Ui.c(act, "sub"));
        c.setOnClickListener(v -> {
            curCat = key;
            loadCategories();
            loadPosts();
        });
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.rightMargin = dp(8);
        c.setLayoutParams(lp);
        return c;
    }

    private void loadPosts() {
        // 骨架屏加载态（批次F）：先摆 3 张呼吸骨架卡，数据到达后原位替换
        status.setText("");
        listView.removeAllViews();
        listView.addView(Ui.skeleton(act, 3));
        Api.forumPosts(prefs.token(), curCat, curSort, 1, 20, new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                runUi(() -> {
                    status.setText("");
                    listView.removeAllViews();
                    JSONArray items = d.optJSONArray("items");
                    if (items == null || items.length() == 0) {
                        TextView empty = new TextView(act);
                        empty.setText("\n这个版块还没有帖子，点右上角「✏️ 发帖」抢占第一楼！");
                        empty.setTextSize(13);
                        empty.setTextColor(Ui.c(act, "faint"));
                        empty.setGravity(Gravity.CENTER);
                        empty.setPadding(0, dp(40), 0, dp(20));
                        listView.addView(empty);
                        return;
                    }
                    int shown = 0;
                    for (int i = 0; i < items.length(); i++) {
                        JSONObject p = items.optJSONObject(i);
                        if (p != null) {
                            View card = postCard(p);
                            listView.addView(card);
                            Ui.enter(card, shown * 45L);   // 逐卡错峰入场
                            shown++;
                        }
                    }
                });
            }

            @Override
            public void err(String m) {
                runUi(() -> {
                    listView.removeAllViews();
                    status.setText("加载失败：" + m);
                });
            }
        });
    }

    private View postCard(JSONObject p) {
        LinearLayout card = new LinearLayout(act);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(13), dp(11), dp(13), dp(11));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(act, "card"));
        bg.setStroke(dp(1), Ui.c(act, "line"));
        card.setBackground(bg);

        LinearLayout head = new LinearLayout(act);
        head.setOrientation(LinearLayout.HORIZONTAL);
        TextView cat = new TextView(act);
        cat.setText(p.optString("category", "闲聊"));
        cat.setTextSize(10.5f);
        cat.setTextColor(Ui.c(act, "brandDark"));
        GradientDrawable cbg = new GradientDrawable();
        cbg.setCornerRadius(dp(4));
        cbg.setColor(Ui.c(act, "brandSoft"));
        cat.setBackground(cbg);
        cat.setPadding(dp(6), dp(2), dp(6), dp(2));
        head.addView(cat, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        if (p.optBoolean("pinned")) {
            TextView pin = new TextView(act);
            pin.setText("📌 置顶");
            pin.setTextSize(10.5f);
            pin.setTextColor(Ui.c(act, "red"));
            head.addView(pin);
        }
        card.addView(head);
        TextView title = new TextView(act);
        title.setText(p.optString("title", ""));
        title.setTextSize(15);
        title.setTextColor(Ui.c(act, "text"));
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setMaxLines(2);
        title.setEllipsize(TextUtils.TruncateAt.END);
        title.setPadding(0, dp(7), 0, 0);
        card.addView(title);

        TextView excerpt = new TextView(act);
        excerpt.setText(p.optString("excerpt", ""));
        excerpt.setTextSize(12.5f);
        excerpt.setTextColor(Ui.c(act, "sub"));
        excerpt.setMaxLines(2);
        excerpt.setEllipsize(TextUtils.TruncateAt.END);
        excerpt.setPadding(0, dp(4), 0, 0);
        card.addView(excerpt);

        TextView meta = new TextView(act);
        meta.setText(p.optString("author", "同学") + " · 💬 " + p.optInt("reply_count")
                + " · 👍 " + p.optInt("likes") + " · 👁 " + p.optInt("views"));
        meta.setTextSize(11);
        meta.setTextColor(Ui.c(act, "faint"));
        meta.setPadding(0, dp(7), 0, 0);
        card.addView(meta);

        card.setOnClickListener(v -> openDetail(p.optString("post_id")));
        LinearLayout wrap = new LinearLayout(act);
        wrap.setPadding(dp(2), dp(5), dp(2), dp(5));
        wrap.addView(card, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return wrap;
    }

    // ------------------------------ 详情 ------------------------------

    private void openDetail(String postId) {
        currentPostId = postId;
        Api.forumPost(prefs.token(), postId, new Api.Cb() {
            @Override
            public void ok(JSONObject post) {
                Api.forumReplies(prefs.token(), postId, new Api.Cb() {
                    @Override
                    public void ok(JSONObject replies) {
                        runUi(() -> paintDetail(post, replies.optJSONArray("items")));
                    }

                    @Override
                    public void err(String m) {
                        runUi(() -> paintDetail(post, new JSONArray()));
                    }
                });
            }

            @Override
            public void err(String m) {
                toast("加载失败：" + m);
            }
        });
    }

    private void paintDetail(JSONObject post, JSONArray replies) {
        detailHost.removeAllViews();
        detailHost.setVisibility(View.VISIBLE);

        LinearLayout bar = new LinearLayout(act);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Ui.c(act, "card"));
        bar.setPadding(dp(12), dp(10), dp(12), dp(10));
        TextView back = new TextView(act);
        back.setText("←");
        back.setTextSize(20);
        back.setTextColor(Ui.c(act, "sub"));
        back.setPadding(dp(4), dp(2), dp(14), dp(2));
        back.setOnClickListener(v -> {
            detailHost.setVisibility(View.GONE);
            loadPosts();
        });
        bar.addView(back);
        TextView bt = new TextView(act);
        bt.setText(post.optString("category", "帖子详情"));
        bt.setTextSize(15);
        bt.setTextColor(Ui.c(act, "text"));
        bt.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(bt);
        detailHost.addView(bar);

        ScrollView sc = new ScrollView(act);
        LinearLayout body = new LinearLayout(act);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(14), dp(10), dp(14), dp(20));

        // 帖子卡
        LinearLayout card = new LinearLayout(act);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(act, "card"));
        bg.setStroke(dp(1), Ui.c(act, "line"));
        card.setBackground(bg);
        TextView title = new TextView(act);
        title.setText(post.optString("title", ""));
        title.setTextSize(17);
        title.setTextColor(Ui.c(act, "text"));
        title.setTypeface(Typeface.DEFAULT_BOLD);
        card.addView(title);
        TextView meta = new TextView(act);
        meta.setText(post.optString("author", "同学") + " · "
                + post.optString("created_at", "").substring(0, Math.min(16,
                post.optString("created_at", "").length())));
        meta.setTextSize(11.5f);
        meta.setTextColor(Ui.c(act, "faint"));
        meta.setPadding(0, dp(5), 0, dp(8));
        card.addView(meta);
        TextView content = new TextView(act);
        content.setText(post.optString("content", ""));
        content.setTextSize(14.5f);
        content.setTextColor(Ui.c(act, "text"));
        content.setLineSpacing(dp(4), 1f);
        card.addView(content);

        // 点赞 + 举报行
        LinearLayout actions = new LinearLayout(act);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        actions.setGravity(Gravity.CENTER_VERTICAL);
        actions.setPadding(0, dp(12), 0, 0);
        TextView like = new TextView(act);
        like.setText((post.optBoolean("liked") ? "❤️ " : "🤍 ") + post.optInt("likes") + " 赞");
        like.setTextSize(13);
        like.setTextColor(post.optBoolean("liked") ? Ui.c(act, "red") : Ui.c(act, "sub"));
        like.setPadding(dp(12), dp(6), dp(12), dp(6));
        GradientDrawable lbg = new GradientDrawable();
        lbg.setCornerRadius(dp(16));
        lbg.setColor(Ui.c(act, "cardAlt"));
        like.setBackground(lbg);
        like.setOnClickListener(v -> Api.forumLike(prefs.token(), currentPostId,
                new Api.Cb() {
                    @Override
                    public void ok(JSONObject r) {
                        runUi(() -> openDetail(currentPostId));   // 重拉刷新 liked/likes
                    }

                    @Override
                    public void err(String m) { toast(m); }
                }));
        actions.addView(like);
        TextView report = new TextView(act);
        report.setText("🚩 举报");
        report.setTextSize(12.5f);
        report.setTextColor(Ui.c(act, "faint"));
        report.setPadding(dp(12), dp(6), dp(6), dp(6));
        report.setOnClickListener(v -> showReportDialog());
        actions.addView(report);
        card.addView(actions);
        body.addView(card);

        // 回帖区
        TextView rt = new TextView(act);
        rt.setText("💬 回帖 " + (replies == null ? 0 : replies.length()));
        rt.setTextSize(14);
        rt.setTextColor(Ui.c(act, "text"));
        rt.setTypeface(Typeface.DEFAULT_BOLD);
        rt.setPadding(dp(4), dp(12), 0, dp(4));
        body.addView(rt);
        if (replies == null || replies.length() == 0) {
            TextView nr = new TextView(act);
            nr.setText("还没有回帖，来抢沙发～");
            nr.setTextSize(12.5f);
            nr.setTextColor(Ui.c(act, "faint"));
            nr.setPadding(dp(8), dp(6), 0, 0);
            body.addView(nr);
        } else {
            for (int i = 0; i < replies.length(); i++) {
                JSONObject r = replies.optJSONObject(i);
                if (r == null) continue;
                LinearLayout rc = new LinearLayout(act);
                rc.setOrientation(LinearLayout.VERTICAL);
                rc.setPadding(dp(12), dp(9), dp(12), dp(9));
                GradientDrawable rgb = new GradientDrawable();
                rgb.setCornerRadius(dp(10));
                rgb.setColor(Ui.c(act, "card"));
                rc.setBackground(rgb);
                TextView ru = new TextView(act);
                ru.setText((i + 1) + "楼 · " + r.optString("username", "同学"));
                ru.setTextSize(11.5f);
                ru.setTextColor(Ui.c(act, "brandDark"));
                rc.addView(ru);
                TextView rct = new TextView(act);
                rct.setText(r.optString("content", ""));
                rct.setTextSize(13);
                rct.setTextColor(Ui.c(act, "text"));
                rct.setPadding(0, dp(4), 0, 0);
                rc.addView(rct);
                LinearLayout rw = new LinearLayout(act);
                rw.setPadding(dp(2), dp(4), dp(2), dp(4));
                rw.addView(rc, new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
                body.addView(rw);
            }
        }

        // 回帖输入
        LinearLayout replyBar = new LinearLayout(act);
        replyBar.setOrientation(LinearLayout.HORIZONTAL);
        replyBar.setGravity(Gravity.CENTER_VERTICAL);
        replyBar.setPadding(0, dp(10), 0, 0);
        EditText replyEt = new EditText(act);
        replyEt.setHint("友善回帖，共建社区…");
        replyEt.setTextSize(13);
        replyEt.setBackgroundColor(Ui.c(act, "card"));
        replyEt.setPadding(dp(10), dp(8), dp(10), dp(8));
        GradientDrawable ebg = new GradientDrawable();
        ebg.setCornerRadius(dp(10));
        ebg.setColor(Ui.c(act, "card"));
        ebg.setStroke(dp(1), Ui.c(act, "line"));
        replyEt.setBackground(ebg);
        replyBar.addView(replyEt, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView send = new TextView(act);
        send.setText("发送");
        send.setTextSize(13.5f);
        send.setTextColor(Color.WHITE);
        GradientDrawable sbg = new GradientDrawable();
        sbg.setCornerRadius(dp(14));
        sbg.setColor(Ui.c(act, "brand"));
        send.setBackground(sbg);
        send.setPadding(dp(14), dp(7), dp(14), dp(7));
        LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        slp.setMargins(dp(8), 0, 0, 0);
        send.setLayoutParams(slp);
        send.setOnClickListener(v -> {
            String txt = replyEt.getText().toString().trim();
            if (txt.length() < 2) {
                toast("回帖至少 2 个字");
                return;
            }
            Api.forumReply(prefs.token(), currentPostId, txt, new Api.Cb() {
                @Override
                public void ok(JSONObject r) {
                    runUi(() -> openDetail(currentPostId));
                }

                @Override
                public void err(String m) { toast(m); }
            });
        });
        replyBar.addView(send);
        body.addView(replyBar);

        sc.addView(body, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        detailHost.addView(sc, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
    }

    // ------------------------------ 发帖 / 举报对话框 ------------------------------

    private void showNewPostDialog() {
        LinearLayout box = new LinearLayout(act);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(20), dp(10), dp(20), dp(4));

        final String[] cats = {"言语理解", "判断推理", "数量关系", "资料分析", "常识判断",
                "申论", "面试", "上岸经验", "备考求助", "闲聊交流"};
        final String[] picked = {cats[7]};
        HorizontalScrollView hs = new HorizontalScrollView(act);
        hs.setHorizontalScrollBarEnabled(false);
        LinearLayout chips = new LinearLayout(act);
        for (String c : cats) {
            TextView chip = new TextView(act);
            chip.setText(c);
            chip.setTextSize(12);
            chip.setPadding(dp(10), dp(5), dp(10), dp(5));
            chip.setOnClickListener(v -> {
                picked[0] = c;
                // 重画选中态
                for (int i = 0; i < chips.getChildCount(); i++) {
                    TextView t = (TextView) chips.getChildAt(i);
                    boolean sel = t.getText().toString().equals(c);
                    GradientDrawable bg = new GradientDrawable();
                    bg.setCornerRadius(dp(12));
                    bg.setColor(sel ? Ui.c(act, "brand") : Ui.c(act, "card"));
                    t.setBackground(bg);
                    t.setTextColor(sel ? Color.WHITE : Ui.c(act, "sub"));
                }
            });
            chips.addView(chip);
        }
        hs.addView(chips);
        box.addView(hs);
        // 触发一次默认选中
        chips.getChildAt(7).performClick();

        EditText titleEt = new EditText(act);
        titleEt.setHint("标题（3-80 字）");
        titleEt.setTextSize(13.5f);
        box.addView(titleEt);
        EditText contentEt = new EditText(act);
        contentEt.setHint("正文（5-8000 字），分享经验、提问求助…");
        contentEt.setTextSize(13.5f);
        contentEt.setMinLines(5);
        contentEt.setGravity(Gravity.TOP);
        box.addView(contentEt);

        Ui.dialogBuilder(act)
                .setTitle("✏️ 发新帖")
                .setView(box)
                .setPositiveButton("发布", (d, w) -> {
                    String t = titleEt.getText().toString().trim();
                    String c = contentEt.getText().toString().trim();
                    if (t.length() < 3 || c.length() < 5) {
                        toast("标题至少 3 字、正文至少 5 字");
                        return;
                    }
                    Api.forumCreate(prefs.token(), t, picked[0], c, new Api.Cb() {
                        @Override
                        public void ok(JSONObject r) {
                            runUi(() -> {
                                toast("发布成功");
                                loadPosts();
                            });
                        }

                        @Override
                        public void err(String m) { toast(m); }
                    });
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void showReportDialog() {
        final String[] reasons = {"垃圾广告", "人身攻击", "违法违规", "泄露隐私", "其他"};
        Ui.dialogBuilder(act)
                .setTitle("🚩 举报该帖")
                .setItems(reasons, (d, w) ->
                        Api.forumReport(prefs.token(), currentPostId,
                                reasons[w], new Api.Cb() {
                            @Override
                            public void ok(JSONObject r) {
                                toast("已举报，管理员会尽快处理");
                            }

                            @Override
                            public void err(String m) { toast(m); }
                        }))
                .setNegativeButton("取消", null)
                .show();
    }

    // ------------------------------ 工具 ------------------------------

    private void runUi(Runnable r) {
        act.runOnUiThread(r);
    }

    private void toast(String m) {
        android.widget.Toast.makeText(act, m, android.widget.Toast.LENGTH_SHORT).show();
    }

    private int dp(int v) {
        return Math.round(v * act.getResources().getDisplayMetrics().density);
    }
}
