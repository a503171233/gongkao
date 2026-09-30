package com.gongkao.app;

import android.content.Context;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.text.SpannableStringBuilder;
import android.text.Spanned;
import android.text.style.BackgroundColorSpan;
import android.text.style.ForegroundColorSpan;
import android.text.style.StyleSpan;
import android.text.style.TypefaceSpan;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 极简 Markdown 渲染（批次F）：AI 回答气泡排版升级。
 * 支持：#~### 标题 / **粗体** / `行内代码` / - 列表 / 1. 有序列表 / > 引用 / ```代码块 / --- 分割线。
 * 不认识的行按普通文本展示——保证任何回答都不会渲染失败。
 * 颜色全部走 Ui.c()，深色模式自动跟随。
 */
public final class Markdown {

    private Markdown() { }

    /** 行内元素：**粗体** 或 `行内代码`。 */
    private static final Pattern INLINE = Pattern.compile("(\\*\\*([^*]+)\\*\\*)|(`([^`]+)`)");

    /** 渲染为垂直容器；baseOffsetSp 字号偏移（设置页聊天字号：-1/0/+1 档映射 ±1.5sp）。 */
    public static LinearLayout render(Context ctx, String md, float offsetSp) {
        LinearLayout box = new LinearLayout(ctx);
        box.setOrientation(LinearLayout.VERTICAL);
        if (md == null || md.isEmpty()) return box;

        String[] lines = md.replace("\r\n", "\n").split("\n", -1);
        StringBuilder code = new StringBuilder();
        boolean inCode = false;
        for (String raw : lines) {
            String t = raw.trim();
            if (t.startsWith("```")) {          // 代码块开/闭
                if (inCode) {
                    box.addView(codeView(ctx, code.toString(), offsetSp));
                    code.setLength(0);
                }
                inCode = !inCode;
                continue;
            }
            if (inCode) { code.append(raw).append('\n'); continue; }

            if (t.isEmpty()) continue;          // 空行=块间距，靠 margin 体现
            if (t.matches("-{3,}|\\*{3,}|_{3,}")) {
                box.addView(divider(ctx));
                continue;
            }
            if (t.startsWith("# ") || t.startsWith("## ") || t.startsWith("### ")) {
                int lvl = t.startsWith("# ") ? 1 : (t.startsWith("## ") ? 2 : 3);
                box.addView(heading(ctx, t.substring(lvl + 1).trim(), lvl, offsetSp));
                continue;
            }
            if (t.startsWith("> ")) {
                box.addView(quote(ctx, t.substring(2).trim(), offsetSp));
                continue;
            }
            if (t.startsWith("- ") || t.startsWith("* ")) {
                box.addView(para(ctx, "•  " + t.substring(2).trim(), offsetSp, true));
                continue;
            }
            if (t.matches("\\d+\\.\\s.*")) {    // 有序列表保留原编号
                box.addView(para(ctx, t, offsetSp, true));
                continue;
            }
            box.addView(para(ctx, t, offsetSp, false));
        }
        if (inCode && code.length() > 0) box.addView(codeView(ctx, code.toString(), offsetSp));
        return box;
    }

    // ------------------------------ 块级元素 ------------------------------

    private static View heading(Context ctx, String text, int level, float off) {
        TextView tv = new TextView(ctx);
        float size = level == 1 ? 17f : level == 2 ? 16f : 15f;
        tv.setTextSize(size + off);
        tv.setTextColor(Ui.c(ctx, "text"));
        tv.setTypeface(Typeface.DEFAULT_BOLD);
        tv.setText(inline(ctx, text));
        return wrap(tv, ctx, dp(ctx, level == 1 ? 8 : 6), dp(ctx, 3));
    }

    private static View para(Context ctx, String text, float off, boolean list) {
        TextView tv = new TextView(ctx);
        tv.setTextSize(15f + off);
        tv.setTextColor(Ui.c(ctx, "text"));
        tv.setLineSpacing(dp(ctx, 2), 1f);
        tv.setText(inline(ctx, text));
        return wrap(tv, ctx, 0, dp(ctx, list ? 2 : 3));
    }

    /** 引用：左侧金色竖条 + 浅底文本。 */
    private static View quote(Context ctx, String text, float off) {
        LinearLayout row = new LinearLayout(ctx);
        row.setOrientation(LinearLayout.HORIZONTAL);
        View bar = new View(ctx);
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(ctx, 2));
        bg.setColor(Ui.c(ctx, "brand"));
        bar.setBackground(bg);
        row.addView(bar, new LinearLayout.LayoutParams(dp(ctx, 3),
                ViewGroup.LayoutParams.MATCH_PARENT));

        TextView tv = new TextView(ctx);
        tv.setTextSize(14f + off);
        tv.setTextColor(Ui.c(ctx, "sub"));
        tv.setLineSpacing(dp(ctx, 2), 1f);
        tv.setText(inline(ctx, text));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.leftMargin = dp(ctx, 8);
        row.addView(tv, lp);
        return wrap(row, ctx, dp(ctx, 2), dp(ctx, 3));
    }

    /** 代码块：等宽字体 + 深浅自适应底色。 */
    private static View codeView(Context ctx, String code, float off) {
        TextView tv = new TextView(ctx);
        tv.setTextSize(12.5f + off);
        tv.setTypeface(Typeface.MONOSPACE);
        tv.setTextColor(Ui.c(ctx, "text"));
        tv.setText(code.length() > 0 && code.charAt(code.length() - 1) == '\n'
                ? code.substring(0, code.length() - 1) : code);
        tv.setLineSpacing(dp(ctx, 1), 1f);
        tv.setBackground(Ui.round(ctx, "codeBg", 8));
        tv.setPadding(dp(ctx, 10), dp(ctx, 8), dp(ctx, 10), dp(ctx, 8));
        tv.setTextIsSelectable(true);
        return wrap(tv, ctx, dp(ctx, 2), dp(ctx, 4));
    }

    private static View divider(Context ctx) {
        View v = new View(ctx);
        v.setBackgroundColor(Ui.c(ctx, "line"));
        return wrap(v, ctx, dp(ctx, 4), dp(ctx, 4));
    }

    /** 外包一层带上下边距的容器（LinearLayout child 需要显式 LayoutParams 才生效 margin）。 */
    private static View wrap(View v, Context ctx, int top, int bottom) {
        LinearLayout box = new LinearLayout(ctx);
        box.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = top;
        lp.bottomMargin = bottom;
        box.addView(v, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return box;
    }

    // ------------------------------ 行内元素 ------------------------------

    /** **粗体**（品牌深色加粗）与 `行内代码`（等宽 + 底色）。 */
    private static CharSequence inline(Context ctx, String s) {
        SpannableStringBuilder sb = new SpannableStringBuilder();
        Matcher m = INLINE.matcher(s);
        int last = 0;
        while (m.find()) {
            sb.append(s, last, m.start());
            if (m.group(2) != null) {                       // **粗体**
                int st = sb.length();
                sb.append(m.group(2));
                sb.setSpan(new StyleSpan(Typeface.BOLD), st, sb.length(),
                        Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                sb.setSpan(new ForegroundColorSpan(Ui.c(ctx, "brandDark")),
                        st, sb.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            } else {                                        // `行内代码`
                int st = sb.length();
                sb.append(m.group(4));
                sb.setSpan(new TypefaceSpan("monospace"), st, sb.length(),
                        Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                sb.setSpan(new BackgroundColorSpan(Ui.c(ctx, "codeBg")),
                        st, sb.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            }
            last = m.end();
        }
        sb.append(s, last, s.length());
        return sb;
    }

    private static int dp(Context ctx, int v) {
        return Math.round(v * ctx.getResources().getDisplayMetrics().density);
    }
}
