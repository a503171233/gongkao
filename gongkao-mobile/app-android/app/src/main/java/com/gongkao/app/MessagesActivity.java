package com.gongkao.app;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ListView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * 消息中心（批次A，原生页）：公告/系统消息列表 + 未读红点 + 单条/全部已读。
 * 契约：GET /messages?page=&size=50 → {total, messages:[{id,title,content,target,created_at,read}], unread}
 *       POST /messages/{id}/read · POST /messages/read_all · GET /messages/unread
 */
public class MessagesActivity extends BaseActivity {


    private Prefs prefs;
    private android.widget.BaseAdapter adapter;
    private TextView unreadBadge;
    private TextView emptyTip;
    private android.widget.LinearLayout skeletonBox;
    private ListView list;

    /** 列表条目模型（复用气泡适配器太怪，这里用内部简版 adapter）。 */
    private static class Row {
        long id;
        String title;
        String content;
        String time;
        boolean read;
    }

    private final List<Row> rows = new ArrayList<>();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);

        getWindow().requestFeature(android.view.Window.FEATURE_NO_TITLE);
        if (android.os.Build.VERSION.SDK_INT >= 23) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        }
        getWindow().setStatusBarColor(Ui.c(MessagesActivity.this, "card"));

        android.widget.LinearLayout page = new android.widget.LinearLayout(this);
        page.setOrientation(android.widget.LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(MessagesActivity.this, "bg"));

        // 顶栏：返回 + 标题 + 未读徽标 + 全部已读
        android.widget.LinearLayout bar = new android.widget.LinearLayout(this);
        bar.setOrientation(android.widget.LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Ui.c(MessagesActivity.this, "card"));
        bar.setPadding(dp(12), dp(10), dp(12), dp(10));

        TextView back = new TextView(this);
        back.setText("←");
        back.setTextSize(22);
        back.setTextColor(Ui.c(MessagesActivity.this, "text"));
        back.setPadding(dp(4), dp(2), dp(14), dp(2));
        back.setOnClickListener(v -> finish());
        bar.addView(back);

        TextView title = new TextView(this);
        title.setText("消息中心");
        title.setTextSize(17);
        title.setTextColor(Ui.c(MessagesActivity.this, "text"));
        title.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(title, new android.widget.LinearLayout.LayoutParams(0,
                android.view.ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        unreadBadge = new TextView(this);
        unreadBadge.setTextSize(12);
        unreadBadge.setTextColor(Color.WHITE);
        unreadBadge.setGravity(Gravity.CENTER);
        unreadBadge.setPadding(dp(8), dp(2), dp(8), dp(2));
        bar.addView(unreadBadge);

        TextView readAll = new TextView(this);
        readAll.setText("全部已读");
        readAll.setTextSize(13);
        readAll.setTextColor(Ui.c(MessagesActivity.this, "brand"));
        readAll.setPadding(dp(12), dp(4), 0, dp(4));
        readAll.setOnClickListener(v -> {
            Api.messageReadAll(prefs.token(), new Api.Cb() {
                @Override
                public void ok(JSONObject o) {
                    runOnUiThread(() -> {
                        for (Row r : rows) r.read = true;
                        paintAdapter();
                        paintBadge(0);
                    });
                }

                @Override
                public void err(String m) {
                    runOnUiThread(() -> toast(m));
                }
            });
        });
        bar.addView(readAll);

        page.addView(bar, new android.widget.LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        // 列表
        adapter = new MsgListAdapter();
        list = new ListView(this);
        list.setDivider(null);
        list.setAdapter(adapter);
        page.addView(list, new android.widget.LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        skeletonBox = new android.widget.LinearLayout(this);
        skeletonBox.setOrientation(android.widget.LinearLayout.VERTICAL);
        skeletonBox.addView(Ui.skeleton(this, 3));
        skeletonBox.setVisibility(View.GONE);
        page.addView(skeletonBox, new android.widget.LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        emptyTip = new TextView(this);
        emptyTip.setText("暂无消息\n有新公告时会出现在这里");
        emptyTip.setTextSize(14);
        emptyTip.setTextColor(Ui.c(MessagesActivity.this, "faint"));
        emptyTip.setGravity(Gravity.CENTER);
        emptyTip.setVisibility(View.GONE);
        page.addView(emptyTip, new android.widget.LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(160)));

        setContentView(page);
        load();
    }

    private void load() {
        // 骨架屏加载态（批次G）
        runOnUiThread(() -> {
            skeletonBox.setVisibility(View.VISIBLE);
            list.setVisibility(View.GONE);
            emptyTip.setVisibility(View.GONE);
        });
        Api.messages(prefs.token(), 1, new Api.Cb() {
            @Override
            public void ok(JSONObject data) {
                rows.clear();
                JSONArray arr = data.optJSONArray("messages");
                if (arr != null) {
                    for (int i = 0; i < arr.length(); i++) {
                        JSONObject m = arr.optJSONObject(i);
                        if (m == null) continue;
                        Row r = new Row();
                        r.id = m.optLong("id");
                        r.title = m.optString("title", "（无标题）");
                        r.content = m.optString("content", "");
                        r.time = m.optString("created_at", "");
                        r.read = m.optInt("read", 0) == 1;
                        rows.add(r);
                    }
                }
                int unread = data.optInt("unread", -1);
                runOnUiThread(() -> {
                    skeletonBox.setVisibility(View.GONE);
                    list.setVisibility(View.VISIBLE);
                    paintAdapter();
                    paintBadge(unread);
                });
            }

            @Override
            public void err(String m) {
                runOnUiThread(() -> {
                    skeletonBox.setVisibility(View.GONE);
                    list.setVisibility(View.VISIBLE);
                    toast("加载失败：" + m);
                });
            }
        });
    }

    private void paintBadge(int unread) {
        if (unread > 0) {
            unreadBadge.setVisibility(View.VISIBLE);
            unreadBadge.setText(unread + " 条未读");
            GradientDrawable g = new GradientDrawable();
            g.setCornerRadius(dp(9));
            g.setColor(Ui.c(MessagesActivity.this, "red"));
            unreadBadge.setBackground(g);
        } else {
            unreadBadge.setVisibility(View.GONE);
        }
    }

    private void paintAdapter() {
        adapter.notifyDataSetChanged();
        emptyTip.setVisibility(rows.isEmpty() ? View.VISIBLE : View.GONE);
    }

    private void openRow(final Row r) {
        if (!r.read) {
            Api.messageRead(prefs.token(), r.id, new Api.Cb() {
                @Override
                public void ok(JSONObject o) {
                    r.read = true;
                    runOnUiThread(() -> paintAdapter());
                }

                @Override
                public void err(String m) { /* 已读失败不打断阅读 */ }
            });
        }
        Ui.dialogBuilder(this)
                .setTitle(r.title)
                .setMessage(r.content + (r.time.isEmpty() ? "" : "\n\n" + r.time.replace("T", " ").replace("+08:00", "")))
                .setPositiveButton("好", null)
                .show();
    }

    private void toast(String s) {
        android.widget.Toast.makeText(this, s, android.widget.Toast.LENGTH_SHORT).show();
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    /** 消息行适配器。 */
    private class MsgListAdapter extends android.widget.BaseAdapter {
        @Override
        public int getCount() { return rows.size(); }
        @Override
        public Object getItem(int position) { return rows.get(position); }
        @Override
        public long getItemId(int position) { return rows.get(position).id; }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            final Row r = rows.get(position);
            android.widget.LinearLayout card = new android.widget.LinearLayout(getContext());
            card.setOrientation(android.widget.LinearLayout.VERTICAL);
            card.setPadding(dp(14), dp(12), dp(14), dp(12));
            GradientDrawable bg = new GradientDrawable();
            bg.setCornerRadius(dp(12));
            bg.setColor(Ui.c(MessagesActivity.this, "card"));
            bg.setStroke(dp(1), r.read ? Ui.c(MessagesActivity.this, "line") : Ui.c(MessagesActivity.this, "gold"));
            card.setBackground(bg);
            card.setOnClickListener(v -> openRow(r));

            TextView t = new TextView(getContext());
            t.setTextSize(15);
            t.setTextColor(Ui.c(MessagesActivity.this, "text"));
            t.setTypeface(r.read ? Typeface.DEFAULT : Typeface.DEFAULT_BOLD);
            t.setText((r.read ? "" : "🔴 ") + r.title);
            t.setSingleLine(true);
            card.addView(t);

            TextView c = new TextView(getContext());
            c.setTextSize(13);
            c.setTextColor(Ui.c(MessagesActivity.this, "sub"));
            c.setText(r.content);
            c.setMaxLines(2);
            c.setPadding(0, dp(4), 0, 0);
            card.addView(c);

            TextView tm = new TextView(getContext());
            tm.setTextSize(11);
            tm.setTextColor(Ui.c(MessagesActivity.this, "faint"));
            tm.setText(r.time.replace("T", " ").replace("+08:00", ""));
            tm.setPadding(0, dp(4), 0, 0);
            card.addView(tm);

            android.widget.LinearLayout wrap = new android.widget.LinearLayout(getContext());
            wrap.setPadding(dp(12), dp(5), dp(12), dp(5));
            wrap.addView(card, new android.widget.LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
            return wrap;
        }

        private android.content.Context getContext() {
            return MessagesActivity.this;
        }
    }
}
