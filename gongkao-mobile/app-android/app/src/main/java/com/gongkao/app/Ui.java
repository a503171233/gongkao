package com.gongkao.app;

import android.app.Activity;
import android.content.Context;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.graphics.drawable.StateListDrawable;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.animation.DecelerateInterpolator;
import android.view.animation.OvershootInterpolator;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * UI 设计系统（批次F）：统一色板（日/夜双套）、间距、圆角、涟漪按压、卡片/按钮/芯片工厂、
 * 顶栏、骨架屏、入场动画、页面转场。所有页面色值一律经 Ui.c() 取用，深色模式集中切换。
 * 深浅模式三态：跟随系统 / 浅色 / 深色（Prefs.nightMode，设置页可改）。
 */
public final class Ui {

    private Ui() { }

    // ------------------------------ 夜间模式判定 ------------------------------

    /** 全局夜间模式缓存：0 跟随系统 / 1 浅色 / 2 深色。
     *  MainActivity 启动时从 Prefs 注入，设置页更改后调用 setNightMode 同步。 */
    private static volatile int sNightMode = 0;

    public static void setNightMode(int mode) {
        sNightMode = mode;
    }

    /** 深色模式判定：先看用户设置（1/2），未设置时跟随系统。 */
    public static boolean isDark(Context ctx) {
        if (sNightMode == 1) return false;
        if (sNightMode == 2) return true;
        int ui = ctx.getResources().getConfiguration().uiMode
                & Configuration.UI_MODE_NIGHT_MASK;
        return ui == Configuration.UI_MODE_NIGHT_YES;
    }

    // ------------------------------ 色板 ------------------------------

    /** 语义色：brand 主金 / brandDark 深金 / gold 亮金 / bg 页面底 / card 卡片 / line 分割
     *  / text 主文字 / sub 次要 / faint 弱化 / green / red / purple / brandSoft 金浅底。 */
    public static int c(Context ctx, String key) {
        boolean dark = isDark(ctx);
        switch (key) {
            case "brand":      return 0xFFC9A227;
            case "brandDark":  return dark ? 0xFFE0C468 : 0xFFB08514;
            case "gold":       return 0xFFF0D48A;
            case "brandSoft":  return dark ? 0x26C9A227 : 0xFFFBF3DC;
            case "bg":         return dark ? 0xFF14161A : 0xFFF6F6F4;
            case "card":       return dark ? 0xFF1E2126 : 0xFFFFFFFF;
            case "cardAlt":    return dark ? 0xFF262A31 : 0xFFFDFBF5;
            case "line":       return dark ? 0xFF2A2D33 : 0xFFECECEC;
            case "text":       return dark ? 0xFFE8E6E1 : 0xFF1F1F1F;
            case "sub":        return dark ? 0xFF9A9A93 : 0xFF777777;
            case "faint":      return dark ? 0xFF6B6B66 : 0xFFAAAAAA;
            case "green":      return dark ? 0xFF6FCF8E : 0xFF1E7E34;
            case "red":        return dark ? 0xFFE57373 : 0xFFD64545;
            case "redSoft":    return dark ? 0x33E57373 : 0xFFFBECEC;
            case "purple":     return dark ? 0xFFA99BD6 : 0xFF8A7AB5;
            case "ripple":     return dark ? 0x2AC9A227 : 0x1FC9A227;
            case "shadow":     return dark ? 0xFF000000 : 0xFFD9D4C8;
            case "hero":       return 0xFF2B2620;
            case "heroGold":   return 0xFFF0D48A;
            case "heroSub":    return 0xFFBBAF9A;
            case "codeBg":     return dark ? 0xFF12140F : 0xFFF4F1E8;
            case "btnText":    return 0xFFFFFFFF;   // 实底按钮默认文字（白）
            case "onHero":     return 0xFFF0D48A;   // hero 深底上的文字（heroGold 同色）
            default:           return dark ? 0xFFE8E6E1 : 0xFF1F1F1F;
        }
    }

    // ------------------------------ 形状工厂 ------------------------------

    /** 圆角底色块。 */
    public static GradientDrawable round(Context ctx, String colorKey, int radiusDp) {
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(ctx, radiusDp));
        g.setColor(c(ctx, colorKey));
        return g;
    }

    /** 卡片：圆角底 + 细描边（夜间描边同色系）。 */
    public static GradientDrawable card(Context ctx, int radiusDp) {
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(ctx, radiusDp));
        g.setColor(c(ctx, "card"));
        g.setStroke(dp(ctx, 1), c(ctx, "line"));
        return g;
    }

    /** 涟漪按压背景（带底色 + 圆角 mask）。API 21+，minSdk 24 全覆盖。 */
    public static Drawable ripple(Context ctx, String baseKey, int radiusDp) {
        GradientDrawable content = round(ctx, baseKey, radiusDp);
        GradientDrawable mask = new GradientDrawable();
        mask.setCornerRadius(dp(ctx, radiusDp));
        mask.setColor(Color.WHITE);
        return new RippleDrawable(android.content.res.ColorStateList.valueOf(
                c(ctx, "ripple")), content, mask);
    }

    /** 无底色涟漪（列表行按压）。 */
    public static Drawable rippleFlat(Context ctx, int radiusDp) {
        ColorDrawable content = new ColorDrawable(Color.TRANSPARENT);
        GradientDrawable mask = new GradientDrawable();
        mask.setCornerRadius(dp(ctx, radiusDp));
        mask.setColor(Color.WHITE);
        return new RippleDrawable(android.content.res.ColorStateList.valueOf(
                c(ctx, "ripple")), content, mask);
    }

    /** 主按钮（品牌金，圆角，涟漪，白字）。 */
    public static TextView btnPrimary(Context ctx, String text, int radiusDp) {
        TextView b = new TextView(ctx);
        b.setText(text);
        b.setTextSize(15);
        b.setGravity(Gravity.CENTER);
        b.setTextColor(Color.WHITE);
        b.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
        b.setBackground(ripple(ctx, "brand", radiusDp));
        b.setPadding(dp(ctx, 20), dp(ctx, 12), dp(ctx, 20), dp(ctx, 12));
        return b;
    }

    /** 次按钮（卡片底色 + 描边 + 主色字）。 */
    public static TextView btnSecondary(Context ctx, String text, int radiusDp) {
        TextView b = new TextView(ctx);
        b.setText(text);
        b.setTextSize(14);
        b.setGravity(Gravity.CENTER);
        b.setTextColor(c(ctx, "brandDark"));
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(ctx, radiusDp));
        g.setColor(c(ctx, "card"));
        g.setStroke(dp(ctx, 1), c(ctx, "line"));
        b.setBackground(rippleOn(ctx, g, radiusDp));
        b.setPadding(dp(ctx, 18), dp(ctx, 10), dp(ctx, 18), dp(ctx, 10));
        return b;
    }

    /** 实底按钮（自定义底色/文字色键，粗体，涟漪）。用于红/灰等非品牌主色按钮。 */
    public static TextView btnSolid(Context ctx, String text, String bgKey, String fgKey, int radiusDp) {
        TextView b = new TextView(ctx);
        b.setText(text);
        b.setTextSize(15);
        b.setGravity(Gravity.CENTER);
        b.setTextColor(c(ctx, fgKey));
        b.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
        b.setBackground(ripple(ctx, bgKey, radiusDp));
        b.setPadding(dp(ctx, 18), dp(ctx, 12), dp(ctx, 18), dp(ctx, 12));
        return b;
    }

    /** 涟漪包住已有背景。 */
    public static Drawable rippleOn(Context ctx, GradientDrawable content, int radiusDp) {
        GradientDrawable mask = new GradientDrawable();
        mask.setCornerRadius(dp(ctx, radiusDp));
        mask.setColor(Color.WHITE);
        return new RippleDrawable(android.content.res.ColorStateList.valueOf(
                c(ctx, "ripple")), content, mask);
    }

    /** 芯片（筛选/标签）。 */
    public static TextView chip(Context ctx, String text, boolean selected) {
        TextView ch = new TextView(ctx);
        ch.setText(text);
        ch.setTextSize(12.5f);
        ch.setPadding(dp(ctx, 13), dp(ctx, 6), dp(ctx, 13), dp(ctx, 6));
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(ctx, 15));
        if (selected) {
            g.setColor(c(ctx, "brand"));
            ch.setTextColor(Color.WHITE);
        } else {
            g.setColor(c(ctx, "card"));
            g.setStroke(dp(ctx, 1), c(ctx, "line"));
            ch.setTextColor(c(ctx, "sub"));
        }
        ch.setBackground(rippleOn(ctx, g, 15));
        return ch;
    }

    // ------------------------------ 顶栏 ------------------------------

    /** 深浅自适配弹窗 Builder（框架 Material.Dialog 双主题，夜间自动深色）。 */
    public static android.app.AlertDialog.Builder dialogBuilder(Context ctx) {
        int theme = isDark(ctx) ? android.R.style.Theme_Material_Dialog
                : android.R.style.Theme_Material_Light_Dialog;
        return new android.app.AlertDialog.Builder(ctx, theme);
    }

    // ------------------------------ 批次28 · 自建弹窗与图文混排 ------------------------------

    /** 限高滚动容器：内容矮时贴内容，超过 maxHpx 后滚动（弹窗自适应高度核心）。 */
    public static class MaxHeightScrollView extends android.widget.ScrollView {
        private final int maxH;
        public MaxHeightScrollView(Context c, int maxHpx) {
            super(c);
            this.maxH = maxHpx;
            setVerticalScrollBarEnabled(false);
        }
        @Override protected void onMeasure(int wSpec, int hSpec) {
            super.onMeasure(wSpec, View.MeasureSpec.makeMeasureSpec(
                    maxH, View.MeasureSpec.AT_MOST));
        }
    }

    /**
     * 自建居中卡片弹窗（替代系统 Material 弹窗——弹窗 UI 与设计系统统一）。
     * 结构：遮罩 → 居中圆角卡片（标题 + 限高滚动内容 + 主/次按钮行），
     * 短内容贴内容居中，长内容限屏高 78% 内滚动；日/夜双主题随 Ui.c 切换。
     */
    public static android.app.Dialog centerDialog(Context ctx, String title,
            View body, String positive, View.OnClickListener onPos,
            String negative, View.OnClickListener onNeg) {
        android.app.Dialog d = new android.app.Dialog(ctx);
        d.requestWindowFeature(android.view.Window.FEATURE_NO_TITLE);

        LinearLayout root = new LinearLayout(ctx);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackground(round(ctx, "card", 18));
        int pad = dp(ctx, 18);
        root.setPadding(pad, pad, pad, dp(ctx, 10));

        if (title != null && !title.isEmpty()) {
            TextView t = new TextView(ctx);
            t.setText(title);
            t.setTextSize(16.5f);
            t.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
            t.setTextColor(c(ctx, "text"));
            root.addView(t);
            View line = new View(ctx);
            line.setBackgroundColor(c(ctx, "line"));
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, dp(ctx, 1));
            lp.topMargin = dp(ctx, 10);
            line.setLayoutParams(lp);
            root.addView(line);
        }

        MaxHeightScrollView sv = new MaxHeightScrollView(ctx,
                (int) (ctx.getResources().getDisplayMetrics().heightPixels * 0.62f));
        sv.addView(body, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        slp.topMargin = dp(ctx, 12);
        sv.setLayoutParams(slp);
        root.addView(sv);

        LinearLayout btns = new LinearLayout(ctx);
        btns.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        blp.topMargin = dp(ctx, 14);
        btns.setLayoutParams(blp);
        if (negative != null) {
            TextView nb = btnSecondary(ctx, negative, 12);
            nb.setOnClickListener(v -> { if (onNeg != null) onNeg.onClick(v); d.dismiss(); });
            btns.addView(nb, new LinearLayout.LayoutParams(0,
                    ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            View gap = new View(ctx);
            btns.addView(gap, new LinearLayout.LayoutParams(dp(ctx, 10), 1));
        }
        if (positive != null) {
            TextView pb = btnPrimary(ctx, positive, 12);
            pb.setOnClickListener(v -> { if (onPos != null) onPos.onClick(v); d.dismiss(); });
            btns.addView(pb, new LinearLayout.LayoutParams(0,
                    ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        }
        root.addView(btns);

        android.widget.FrameLayout wrap = new android.widget.FrameLayout(ctx);
        int m = dp(ctx, 22);
        wrap.setPadding(m, m, m, m);
        android.widget.FrameLayout.LayoutParams flp =
                new android.widget.FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT);
        flp.gravity = android.view.Gravity.CENTER;
        wrap.addView(root, flp);
        d.setContentView(wrap);
        android.view.Window w = d.getWindow();
        if (w != null) {
            w.setBackgroundDrawableResource(android.R.color.transparent);
            w.setLayout(ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT);
            w.addFlags(android.view.WindowManager.LayoutParams.FLAG_DIM_BEHIND);
            w.setDimAmount(0.45f);
        }
        return d;
    }

    /**
     * 题干富视图（批次28）：`![图N](url)` 标记渲染为网络图片（ImgLoad 异步），
     * 其余为文本段；连续文本合并为单个 TextView 保留换行。
     * 返回垂直 LinearLayout，调用方自行加进卡片/滚动容器。
     */
    public static LinearLayout richTextView(Context ctx, String text,
                                            float sizeSp, String colorKey) {
        LinearLayout box = new LinearLayout(ctx);
        box.setOrientation(LinearLayout.VERTICAL);
        if (text == null || text.isEmpty()) return box;
        java.util.regex.Pattern P = java.util.regex.Pattern.compile(
                "!\\[图\\d*\\]\\(([^)\\s]+)\\)");
        java.util.regex.Matcher m = P.matcher(text);
        StringBuilder buf = new StringBuilder();
        int last = 0;
        while (m.find()) {
            buf.append(text, last, m.start());
            last = m.end();
            String seg = buf.toString();
            if (!seg.trim().isEmpty()) box.addView(richTextSeg(ctx, seg, sizeSp, colorKey));
            buf.setLength(0);
            android.widget.ImageView iv = new android.widget.ImageView(ctx);
            iv.setScaleType(android.widget.ImageView.ScaleType.FIT_CENTER);
            iv.setAdjustViewBounds(true);
            iv.setMaxHeight(dp(ctx, 230));
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            lp.topMargin = dp(ctx, 6);
            lp.bottomMargin = dp(ctx, 6);
            iv.setLayoutParams(lp);
            iv.setBackground(round(ctx, "cardAlt", 8));
            int ph = dp(ctx, 6);
            iv.setPadding(ph, ph, ph, ph);
            ImgLoad.load(ctx, iv, m.group(1));
            box.addView(iv);
        }
        buf.append(text.substring(last));
        String tail = buf.toString();
        if (!tail.trim().isEmpty()) box.addView(richTextSeg(ctx, tail, sizeSp, colorKey));
        return box;
    }

    /** [图N] 占位符 → 圆角内联提示（下载失败降级形态）。 */
    private static TextView richTextSeg(Context ctx, String seg, float sizeSp, String colorKey) {
        TextView t = new TextView(ctx);
        t.setText(seg.replaceAll("\\[图\\d*\\]", "🖼 [图片加载失败]").trim());
        t.setTextSize(sizeSp);
        t.setTextColor(c(ctx, colorKey));
        t.setLineSpacing(dp(ctx, 2), 1f);
        return t;
    }

    /** 统一顶栏：返回涟漪按钮 + 标题。 */
    public static View topBar(Activity act, String title, View.OnClickListener back) {
        LinearLayout bar = new LinearLayout(act);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(c(act, "card"));
        bar.setElevation(dp(act, 2));
        bar.setPadding(dp(act, 6), dp(act, 8), dp(act, 14), dp(act, 8));

        TextView backBtn = new TextView(act);
        backBtn.setText("←");
        backBtn.setTextSize(20);
        backBtn.setTextColor(c(act, "sub"));
        backBtn.setPadding(dp(act, 12), dp(act, 4), dp(act, 16), dp(act, 4));
        backBtn.setBackground(rippleFlat(act, 20));
        backBtn.setOnClickListener(back);
        bar.addView(backBtn);

        TextView t = new TextView(act);
        t.setText(title);
        t.setTextSize(17);
        t.setTextColor(c(act, "text"));
        t.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
        bar.addView(t);
        return bar;
    }

    // ------------------------------ 骨架屏 ------------------------------

    /** 简易骨架屏：N 行「短行+长条」占位，呼吸闪烁。返回可 addView 的容器。 */
    public static View skeleton(Context ctx, int rows) {
        LinearLayout box = new LinearLayout(ctx);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(ctx, 16), dp(ctx, 10), dp(ctx, 16), dp(ctx, 10));
        for (int i = 0; i < rows; i++) {
            LinearLayout cardBox = new LinearLayout(ctx);
            cardBox.setOrientation(LinearLayout.VERTICAL);
            cardBox.setBackground(card(ctx, 14));
            cardBox.setPadding(dp(ctx, 14), dp(ctx, 14), dp(ctx, 14), dp(ctx, 14));
            View a = bone(ctx, i % 2 == 0 ? 0.42f : 0.62f);
            cardBox.addView(a);
            View b = bone(ctx, 0.9f);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, dp(ctx, 13));
            lp.topMargin = dp(ctx, 10);
            cardBox.addView(b, lp);
            LinearLayout wrap = new LinearLayout(ctx);
            wrap.setPadding(dp(ctx, 2), dp(ctx, 5), dp(ctx, 2), dp(ctx, 5));
            wrap.addView(cardBox, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
            box.addView(wrap);
        }
        startBreathing(box);
        return box;
    }

    private static View bone(Context ctx, float widthWeight) {
        View v = new View(ctx);
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(ctx, 6));
        g.setColor(c(ctx, "line"));
        v.setBackground(g);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(ctx, 13), widthWeight);
        v.setLayoutParams(lp);
        return v;
    }

    /** 呼吸闪烁（骨架/加载态）。 */
    public static void startBreathing(View v) {
        v.setAlpha(0.55f);
        v.animate().alpha(1f).setDuration(700)
                .setInterpolator(new DecelerateInterpolator())
                .withEndAction(() -> v.animate().alpha(0.55f).setDuration(700)
                        .withEndAction(() -> {
                            if (v.isAttachedToWindow()) startBreathing(v);
                        }).start()).start();
    }

    // ------------------------------ 动画 ------------------------------

    /** 列表/卡片入场：淡入 + 上移。delayMs 逐个错开。 */
    public static void enter(View v, long delayMs) {
        v.setAlpha(0f);
        v.setTranslationY(dp(v.getContext(), 14));
        v.animate().alpha(1f).translationY(0f)
                .setDuration(260).setStartDelay(delayMs)
                .setInterpolator(new DecelerateInterpolator(1.4f)).start();
    }

    /** 按压缩放反馈（配合点击）。 */
    public static void pressScale(View v) {
        v.setOnTouchListener((vv, ev) -> {
            switch (ev.getActionMasked()) {
                case android.view.MotionEvent.ACTION_DOWN:
                    vv.animate().scaleX(0.96f).scaleY(0.96f).setDuration(80).start();
                    break;
                case android.view.MotionEvent.ACTION_UP:
                case android.view.MotionEvent.ACTION_CANCEL:
                    vv.animate().scaleX(1f).scaleY(1f).setDuration(140)
                            .setInterpolator(new OvershootInterpolator(1.6f)).start();
                    break;
                default:
                    break;
            }
            return false;   // 不吞事件，OnClickListener 照常
        });
    }

    /** 主内容首次进入：淡入。 */
    public static void reveal(View root) {
        root.setAlpha(0f);
        root.animate().alpha(1f).setDuration(220)
                .setInterpolator(new DecelerateInterpolator()).start();
    }

    /** Activity 转场：右滑入 / 淡出（finish 时传 false）。 */
    public static void transition(Activity act, boolean enter) {
        if (android.os.Build.VERSION.SDK_INT >= 34) {
            // 34+ 系统默认预测式返回动画，不覆盖
            return;
        }
        try {
            if (enter) {
                act.overridePendingTransition(
                        android.R.anim.fade_in, android.R.anim.fade_out);
            } else {
                act.overridePendingTransition(
                        android.R.anim.fade_in, android.R.anim.fade_out);
            }
        } catch (Exception ignored) { }
    }

    // ------------------------------ 通用 ------------------------------

    public static void toast(Context ctx, String msg) {
        android.widget.Toast.makeText(ctx, msg, android.widget.Toast.LENGTH_SHORT).show();
    }

    public static int dp(Context ctx, int v) {
        return Math.round(v * ctx.getResources().getDisplayMetrics().density);
    }

    /** 底部手势条让位高度。 */
    public static int navInset(Context ctx) {
        int rid = ctx.getResources().getIdentifier("navigation_bar_height", "dimen", "android");
        return rid > 0 ? ctx.getResources().getDimensionPixelSize(rid) / 2 : 0;
    }

    /** 状态栏亮暗图标：浅色页面黑字，深色页面白字。 */
    public static void systemBars(Activity act) {
        if (android.os.Build.VERSION.SDK_INT >= 23) {
            View decor = act.getWindow().getDecorView();
            int flags = decor.getSystemUiVisibility();
            if (isDark(act)) {
                flags &= ~View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
            } else {
                flags |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
            }
            decor.setSystemUiVisibility(flags);
        }
        act.getWindow().setStatusBarColor(c(act, "card"));
    }

    /** 简易延迟执行。 */
    public static void post(long ms, Runnable r) {
        new Handler(Looper.getMainLooper()).postDelayed(r, ms);
    }
}
