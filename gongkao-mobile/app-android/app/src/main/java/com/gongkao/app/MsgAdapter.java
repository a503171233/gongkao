package com.gongkao.app;

import android.content.Context;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.LinearLayout;

import java.util.ArrayList;
import java.util.List;

/**
 * 问答消息列表（批次F 重写）：
 * - user 气泡：纯文本金底右对齐
 * - bot 气泡：Markdown.render 排版（标题/粗体/列表/代码块/引用），卡片底左对齐
 * - 长按气泡弹菜单（复制全文 / 重新生成），经 Cb 回调给 ChatPage
 * - 颜色全部走 Ui.c()，夜间模式自动跟随；fontSizeOff 支持设置页字号档位
 */
public class MsgAdapter extends BaseAdapter {

    /** 气泡长按回调。 */
    public interface Cb { void onBubbleLongPress(Msg m); }

    public static class Msg {
        public String role;   // "user" | "bot"
        public String text;
        public String notice; // 附加提示（守卫 warn / 引用数 / 停止标记），可空

        public Msg(String role, String text) {
            this.role = role;
            this.text = text;
        }
    }

    /** 聊天字号偏移（sp）：设置页三档 小=-1.5 / 标准=0 / 大=+1.5。 */
    public static volatile float fontSizeOff = 0f;

    private final Context ctx;
    private final Cb cb;
    public final List<Msg> items = new ArrayList<>();

    public MsgAdapter(Context ctx, Cb cb) {
        this.ctx = ctx;
        this.cb = cb;
    }

    public void add(Msg m) {
        items.add(m);
        notifyDataSetChanged();
    }

    /** 更新最后一条（流式追加用）。 */
    public void updateLast(String text, String notice) {
        if (items.isEmpty()) return;
        Msg last = items.get(items.size() - 1);
        last.text = text;
        if (notice != null) last.notice = notice;
        notifyDataSetChanged();
    }

    /** 删除最后一条（重新生成前移除旧回答）。 */
    public void removeLast() {
        if (items.isEmpty()) return;
        items.remove(items.size() - 1);
        notifyDataSetChanged();
    }

    @Override
    public int getCount() { return items.size(); }

    @Override
    public Object getItem(int position) { return items.get(position); }

    @Override
    public long getItemId(int position) { return position; }

    @Override
    public View getView(int position, View convertView, ViewGroup parent) {
        Msg m = items.get(position);
        boolean isUser = "user".equals(m.role);

        LinearLayout row = new LinearLayout(ctx);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(dp(12), dp(6), dp(12), dp(6));

        LinearLayout bubble = new LinearLayout(ctx);
        bubble.setOrientation(LinearLayout.VERTICAL);
        bubble.setPadding(dp(14), dp(10), dp(14), dp(10));
        bubble.setClickable(true);
        bubble.setLongClickable(true);
        bubble.setOnLongClickListener(v -> {
            if (cb != null) cb.onBubbleLongPress(m);
            return true;
        });

        if (isUser) {
            bubble.setBackground(Ui.ripple(ctx, "brand", 14));
            row.setGravity(Gravity.END);
            android.widget.TextView tv = new android.widget.TextView(ctx);
            tv.setTextSize(15.5f);
            tv.setTextColor(0xFFFFFFFF);
            tv.setLineSpacing(dp(2), 1f);
            tv.setText(m.text == null ? "" : m.text);
            bubble.addView(tv, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        } else {
            bubble.setBackground(Ui.card(ctx, 14));
            row.setGravity(Gravity.START);
            LinearLayout md = Markdown.render(ctx, m.text == null ? "" : m.text, fontSizeOff);
            bubble.addView(md, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        }

        if (m.notice != null && !m.notice.isEmpty()) {
            android.widget.TextView note = new android.widget.TextView(ctx);
            note.setTextSize(11.5f);
            note.setTextColor(isUser ? 0xFFF3E7BE : Ui.c(ctx, "brandDark"));
            note.setText(m.notice);
            note.setPadding(0, dp(6), 0, 0);
            bubble.addView(note, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        }

        // 气泡最大宽度 ~78%
        int max = (int) (parent.getWidth() * 0.78);
        LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        bp.width = Math.min(Math.max(max, dp(48)),
                Math.max(dp(48), measure(bubble, max)));
        row.addView(bubble, bp);
        return row;
    }

    /** 整个气泡容器按最大宽测量，取实际内容宽度。 */
    private int measure(View bubble, int max) {
        int wSpec = View.MeasureSpec.makeMeasureSpec(max, View.MeasureSpec.AT_MOST);
        int hSpec = View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED);
        bubble.measure(wSpec, hSpec);
        return bubble.getMeasuredWidth();
    }

    private int dp(int v) {
        return Math.round(v * ctx.getResources().getDisplayMetrics().density);
    }
}
