package com.gongkao.app;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * 未实装模块占位页（批次A→E 渐次替换为真实功能页）。
 * 统一视觉：大图标 + 模块名 + 说明 + 版本预告徽标。
 */
public class PlaceholderPage {

    private final Activity act;

    public PlaceholderPage(Activity act) {
        this.act = act;
    }

    public View build(String icon, String title, String desc, String eta) {
        LinearLayout page = new LinearLayout(act);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setGravity(Gravity.CENTER);
        page.setBackgroundColor(Ui.c(act, "bg"));

        TextView ic = new TextView(act);
        ic.setText(icon);
        ic.setTextSize(52);
        ic.setGravity(Gravity.CENTER);
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(28));
        g.setColor(Ui.c(act, "brandSoft"));
        ic.setBackground(g);
        page.addView(ic, new LinearLayout.LayoutParams(dp(96), dp(96)));

        TextView t = new TextView(act);
        t.setText(title);
        t.setTextSize(20);
        t.setTextColor(Ui.c(act, "text"));
        t.setGravity(Gravity.CENTER);
        t.setPadding(0, dp(20), 0, dp(6));
        page.addView(t);

        TextView d = new TextView(act);
        d.setText(desc);
        d.setTextSize(14);
        d.setTextColor(Ui.c(act, "faint"));
        d.setGravity(Gravity.CENTER);
        d.setPadding(dp(32), 0, dp(32), dp(14));
        page.addView(d);

        TextView e = new TextView(act);
        e.setText("🚧 该模块在后续版本上线 " + eta);
        e.setTextSize(12);
        e.setTextColor(Ui.c(act, "brandDark"));
        e.setGravity(Gravity.CENTER);
        GradientDrawable badge = new GradientDrawable();
        badge.setCornerRadius(dp(11));
        badge.setColor(Ui.c(act, "brandSoft"));
        e.setBackground(badge);
        e.setPadding(dp(14), dp(5), dp(14), dp(5));
        page.addView(e, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        return page;
    }

    private int dp(int v) {
        return Math.round(v * act.getResources().getDisplayMetrics().density);
    }
}
