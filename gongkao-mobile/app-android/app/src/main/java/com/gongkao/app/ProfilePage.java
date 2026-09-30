package com.gongkao.app;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

/**
 * 我的页（批次A 宫格版，原生）：用户/会员卡、今日额度、消息中心入口（未读红点）、
 * 功能九宫格（后续批次逐格实装）、退出登录。
 * 数据经 /api/me 与 /messages/unread 拉取（契约与 Web 端一致）。
 */
public class ProfilePage {

    private final Activity act;
    private final Prefs prefs;
    private TextView infoCard;
    private TextView quotaCard;
    private TextView unreadBadge;
    private TextView t1Btn;
    private TextView t2Btn;

    public ProfilePage(Activity act, Prefs prefs) {
        this.act = act;
        this.prefs = prefs;
    }

    public View build() {
        ScrollView scroll = new ScrollView(act);
        scroll.setBackgroundColor(Ui.c(act, "bg"));
        LinearLayout page = new LinearLayout(act);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(16), dp(20), dp(16), dp(20) + dp(58));

        // 用户信息卡
        infoCard = card("👤 " + (prefs.username().isEmpty() ? "未登录" : prefs.username()),
                "角色：" + roleCn(prefs.role()));
        page.addView(infoCard);

        // 额度卡
        quotaCard = card("⚡ 今日剩余提问额度", prefs.quotaLeft() < 0 ? "刷新中…" : String.valueOf(prefs.quotaLeft()));
        page.addView(quotaCard);

        // 消息中心入口行（带未读徽标）
        page.addView(sectionTitle("消息"));
        LinearLayout msgRow = new LinearLayout(act);
        msgRow.setOrientation(LinearLayout.HORIZONTAL);
        msgRow.setGravity(Gravity.CENTER_VERTICAL);
        msgRow.setPadding(dp(14), dp(13), dp(14), dp(13));
        GradientDrawable msgBg = new GradientDrawable();
        msgBg.setCornerRadius(dp(14));
        msgBg.setColor(Ui.c(act, "card"));
        msgBg.setStroke(dp(1), Ui.c(act, "line"));
        msgRow.setBackground(msgBg);
        msgRow.setOnClickListener(v -> act.startActivity(new Intent(act, MessagesActivity.class)));

        TextView msgIcon = new TextView(act);
        msgIcon.setText("🔔");
        msgIcon.setTextSize(18);
        msgRow.addView(msgIcon);

        TextView msgLabel = new TextView(act);
        msgLabel.setText("  消息中心");
        msgLabel.setTextSize(15);
        msgLabel.setTextColor(Ui.c(act, "text"));
        msgRow.addView(msgLabel, rowWeight());

        unreadBadge = new TextView(act);
        unreadBadge.setTextSize(11);
        unreadBadge.setTextColor(Color.WHITE);
        unreadBadge.setGravity(Gravity.CENTER);
        unreadBadge.setVisibility(View.GONE);
        GradientDrawable badgeBg = new GradientDrawable();
        badgeBg.setCornerRadius(dp(9));
        badgeBg.setColor(Ui.c(act, "red"));
        unreadBadge.setBackground(badgeBg);
        unreadBadge.setPadding(dp(7), dp(2), dp(7), dp(2));
        msgRow.addView(unreadBadge);

        TextView arrow = new TextView(act);
        arrow.setText(" ›");
        arrow.setTextSize(16);
        arrow.setTextColor(Ui.c(act, "faint"));
        msgRow.addView(arrow);
        page.addView(msgRow);

        // 老师切换卡（问答页当前老师，快捷切换）
        page.addView(sectionTitle("当前老师"));
        LinearLayout row = new LinearLayout(act);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER);
        t1Btn = teacherBtn("星辰老师 T001", "T001");
        t2Btn = teacherBtn("云舟老师 T002", "T002");
        row.addView(t1Btn, rowLp());
        row.addView(t2Btn, rowLp());
        page.addView(row);

        // 功能九宫格（批次E：九格全部实装）
        page.addView(sectionTitle("学习工具"));
        page.addView(gridRow(
                gridBtn("🪄", "粉笔绑定", "", FenbiActivity.class),
                gridBtn("✍️", "申论批改", "", EssayActivity.class),
                gridBtn("🎤", "面试练习", "", InterviewActivity.class)));
        page.addView(gridRow(
                gridBtn("📅", "学习计划", "", StudyPlanActivity.class),
                gridBtn("📈", "学习报告", "", LearningReportActivity.class),
                gridBtn("🏆", "激励排行", "", IncentiveBoardActivity.class)));
        page.addView(gridRow(
                gridBtn("📖", "备考文章", "", ArticlesActivity.class),
                gridBtn("🧠", "知识体系", "", KnowledgeActivity.class),
                gridBtn("💎", "会员充值", "", RechargeActivity.class)));

        // 批次F：通用设置（夜间模式/字号/会话管理）
        page.addView(sectionTitle("通用"));
        LinearLayout setRow = linkRow("⚙ 设置", "夜间模式 · 聊天字号 · 会话管理");
        setRow.setOnClickListener(v -> act.startActivity(
                new android.content.Intent(act, SettingsActivity.class)));
        page.addView(setRow);

        // 批次H：在线更新（应用内下载覆盖安装 / 网盘通道 / 热更补丁）
        LinearLayout upRow = linkRow("🚀 检查更新",
                "v" + UpdateManager.appVersion(act)
                        + " · 应用内升级 / 网盘下载 / 热更补丁");
        upRow.setOnClickListener(v -> act.startActivity(
                new android.content.Intent(act, UpdateActivity.class)));
        page.addView(upRow);

        // 批次E：设置密保 + 意见反馈
        page.addView(sectionTitle("账号与反馈"));
        LinearLayout secRow = linkRow("🔐 设置密保问题", "用于忘记密码时自助找回");
        secRow.setOnClickListener(v -> showSecurityDialog());
        page.addView(secRow);
        LinearLayout fbRow = linkRow("💬 意见反馈", "功能建议 · 问题反馈直达老师");
        fbRow.setOnClickListener(v -> showFeedbackDialog());
        page.addView(fbRow);

        // 退出登录
        TextView logout = new TextView(act);
        logout.setText("退出登录");
        logout.setTextColor(Ui.c(act, "red"));
        logout.setTextSize(16);
        logout.setGravity(Gravity.CENTER);
        logout.setPadding(0, dp(28), 0, 0);
        logout.setOnClickListener(v -> {
            // 批次29修复：先吊销服务端 token（后端 /me/logout），再清本地登录态；
            // 弹窗确认后拉起登录页（走 startActivityForResult 闭环，不再直接 finish 防窗口泄漏）。
            Api.logout(prefs.token());
            prefs.logout();
            Ui.dialogBuilder(act)
                    .setMessage("已退出登录")
                    .setCancelable(false)
                    .setPositiveButton("重新登录", (d, w) ->
                            act.startActivityForResult(new Intent(act, LoginActivity.class), 1))
                    .show();
        });
        page.addView(logout);

        // 批次F：区块错峰入场动画（头像→消息→宫格→设置…依次淡入上移）
        for (int i = 0; i < page.getChildCount(); i++) {
            Ui.enter(page.getChildAt(i), i * 35L);
        }

        scroll.addView(page, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return scroll;
    }

    // ------------------------------------------------------------------
    // 宫格
    // ------------------------------------------------------------------
    /**
     * 宫格按钮：target 非 null 直接跳转对应 Activity；null 显示「待上线」占位。
     * eta 为空时不显示版本徽标（已上线）。
     */
    private TextView gridBtn(String icon, String label, String eta, Class<?> target) {
        LinearLayout cell = new LinearLayout(act);
        cell.setOrientation(LinearLayout.VERTICAL);
        cell.setGravity(Gravity.CENTER);
        cell.setPadding(dp(6), dp(12), dp(6), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(14));
        bg.setColor(Ui.c(act, "card"));
        bg.setStroke(dp(1), Ui.c(act, "line"));
        cell.setBackground(bg);

        TextView ic = new TextView(act);
        ic.setText(icon);
        ic.setTextSize(22);
        cell.addView(ic);

        TextView lb = new TextView(act);
        lb.setText(label);
        lb.setTextSize(12.5f);
        lb.setTextColor(Ui.c(act, "text"));
        lb.setPadding(0, dp(6), 0, 0);
        cell.addView(lb);

        TextView et = new TextView(act);
        if (eta != null && !eta.isEmpty()) {
            et.setText(eta + " 上线");
            et.setTextSize(9.5f);
            et.setTextColor(Ui.c(act, "brandDark"));
            et.setPadding(0, dp(2), 0, 0);
            cell.addView(et);
        }

        cell.setOnClickListener(v -> {
            if (target != null) {
                act.startActivity(new Intent(act, target));
            } else {
                toast("「" + label + "」将在 " + (eta == null || eta.isEmpty()
                        ? "后续版本" : eta) + " 上线，敬请期待");
            }
        });

        // 用 tag 携带外层 TextView 供宫格布局包裹
        TextView fake = new TextView(act);
        fake.setVisibility(View.GONE);
        fake.setTag(cell);
        return fake;
    }

    private LinearLayout gridRow(TextView... btns) {
        LinearLayout row = new LinearLayout(act);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(0, dp(5), 0, dp(5));
        for (TextView b : btns) {
            View cell = (View) b.getTag();
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0,
                    ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            lp.setMargins(dp(3), 0, dp(3), 0);
            row.addView(cell, lp);
        }
        return row;
    }

    // ------------------------------------------------------------------
    // 数据刷新
    // ------------------------------------------------------------------
    /** 拉取最新额度/角色/未读数。 */
    public void refresh() {
        paintTeacher();
        Api.me(prefs.token(), new Api.Cb() {
            @Override
            public void ok(JSONObject data) {
                act.runOnUiThread(() -> {
                    int q = data.optInt("quota_left", -1);
                    String user = data.optString("username", prefs.username());
                    String role = data.optString("role", prefs.role());
                    prefs.setUsername(user);
                    prefs.setRole(role);
                    prefs.setQuotaLeft(q);
                    String memberLine = memberLine(data);
                    infoCard.setText("👤 " + user + "\n角色：" + roleCn(role) + memberLine);
                    quotaCard.setText("⚡ 今日剩余提问额度：" + (q < 0 ? "—" : q));
                });
            }

            @Override
            public void err(String message) {
                act.runOnUiThread(() -> quotaCard.setText("⚡ 额度获取失败：" + message));
            }
        });
        Api.messageUnread(prefs.token(), new Api.Cb() {
            @Override
            public void ok(JSONObject data) {
                act.runOnUiThread(() -> paintBadge(data.optInt("count", 0)));
            }

            @Override
            public void err(String m) { /* 未读失败静默 */ }
        });
    }

    /** 会员到期信息（/me 的 membership 字段，可选）。 */
    private String memberLine(JSONObject data) {
        String expire = data.optString("member_expire_at", "");
        if (expire == null || expire.isEmpty()) return "";
        return "\n会员到期：" + expire.substring(0, Math.min(10, expire.length()));
    }

    private void paintBadge(int n) {
        if (unreadBadge == null) return;
        if (n > 0) {
            unreadBadge.setVisibility(View.VISIBLE);
            unreadBadge.setText(n > 99 ? "99+" : String.valueOf(n));
        } else {
            unreadBadge.setVisibility(View.GONE);
        }
    }

    // ------------------------------------------------------------------
    // UI 组件
    // ------------------------------------------------------------------
    private TextView teacherBtn(String label, String id) {
        TextView b = new TextView(act);
        b.setText(label);
        b.setTextSize(14);
        b.setGravity(Gravity.CENTER);
        b.setPadding(dp(10), dp(16), dp(10), dp(16));
        b.setOnClickListener(v -> {
            prefs.setTeacherId(id);
            paintTeacher();
            toast("已切换到" + ChatPage.teacherName(id));
        });
        return b;
    }

    private void paintTeacher() {
        String cur = prefs.teacherId();
        GradientDrawable on = new GradientDrawable();
        on.setCornerRadius(dp(12));
        on.setColor(Ui.c(act, "brand"));
        GradientDrawable off = new GradientDrawable();
        off.setCornerRadius(dp(12));
        off.setColor(Ui.c(act, "card"));
        off.setStroke(dp(1), Ui.c(act, "line"));
        t1Btn.setBackground("T001".equals(cur) ? on : off);
        t1Btn.setTextColor("T001".equals(cur) ? Color.WHITE : Ui.c(act, "sub"));
        t2Btn.setBackground("T002".equals(cur) ? on : off);
        t2Btn.setTextColor("T002".equals(cur) ? Color.WHITE : Ui.c(act, "sub"));
    }

    private TextView sectionTitle(String s) {
        TextView t = new TextView(act);
        t.setText(s);
        t.setTextSize(13);
        t.setTextColor(Ui.c(act, "sub"));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setPadding(dp(4), dp(14), 0, dp(8));
        return t;
    }

    private TextView card(String line1, String line2) {
        TextView c = new TextView(act);
        c.setText(line1 + "\n" + line2);
        c.setTextSize(15);
        c.setTextColor(Ui.c(act, "text"));
        c.setLineSpacing(dp(3), 1f);
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(14));
        bg.setColor(Ui.c(act, "card"));
        bg.setStroke(dp(1), Ui.c(act, "line"));
        c.setBackground(bg);
        c.setPadding(dp(16), dp(14), dp(16), dp(14));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, 0, 0, dp(12));
        c.setLayoutParams(lp);
        return c;
    }

    private LinearLayout.LayoutParams rowLp() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        lp.setMargins(dp(6), 0, dp(6), 0);
        return lp;
    }

    private LinearLayout.LayoutParams rowWeight() {
        return new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
    }

    private String roleCn(String r) {
        if ("member".equals(r) || "vip".equals(r)) return "会员";
        if ("admin".equals(r)) return "管理员";
        return "免费用户";
    }

    /** 设置型入口行（标题 + 副文案 + 箭头），直接返回可 add 的行视图。 */
    private LinearLayout linkRow(String title, String sub) {
        LinearLayout row = new LinearLayout(act);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(14), dp(12), dp(14), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(12));
        bg.setColor(Ui.c(act, "card"));
        bg.setStroke(dp(1), Ui.c(act, "line"));
        row.setBackground(bg);
        LinearLayout mid = new LinearLayout(act);
        mid.setOrientation(LinearLayout.VERTICAL);
        TextView t = new TextView(act);
        t.setText(title);
        t.setTextSize(14.5f);
        t.setTextColor(Ui.c(act, "text"));
        mid.addView(t);
        TextView s = new TextView(act);
        s.setText(sub);
        s.setTextSize(11);
        s.setTextColor(Ui.c(act, "faint"));
        s.setPadding(0, dp(2), 0, 0);
        mid.addView(s);
        row.addView(mid, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView arrow = new TextView(act);
        arrow.setText(" ›");
        arrow.setTextSize(16);
        arrow.setTextColor(Ui.c(act, "faint"));
        row.addView(arrow);
        return row;
    }

    /** 设置/更新密保问题（ premade 列表 + 自定义）。 */
    private void showSecurityDialog() {
        Api.securityQuestionGet(prefs.token(), new Api.Cb() {
            @Override
            public void ok(JSONObject d) {
                runUi(() -> {
                    java.util.List<String> qs = new java.util.ArrayList<>();
                    org.json.JSONArray premade = d.optJSONArray("premade");
                    if (premade != null) {
                        for (int i = 0; i < premade.length(); i++)
                            qs.add(premade.optString(i));
                    }
                    String current = d.optString("security_question", "");
                    LinearLayout box = new LinearLayout(act);
                    box.setOrientation(LinearLayout.VERTICAL);
                    box.setPadding(dp(20), dp(10), dp(20), dp(4));

                    TextView cur = new TextView(act);
                    cur.setText(current.isEmpty() ? "尚未设置密保"
                            : "当前密保：" + current);
                    cur.setTextSize(12.5f);
                    cur.setTextColor(current.isEmpty() ? Ui.c(act, "faint") : Ui.c(act, "green"));
                    box.addView(cur);

                    final String[] picked = {qs.isEmpty() ? "我的昵称是？" : qs.get(0)};
                    android.widget.Spinner spinner = new android.widget.Spinner(act);
                    android.widget.ArrayAdapter<String> ad = new android.widget.ArrayAdapter<>(
                            act, android.R.layout.simple_spinner_item, qs);
                    ad.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
                    spinner.setAdapter(ad);
                    if (qs.size() > 0) {
                        int sel = qs.indexOf(current);
                        if (sel >= 0) spinner.setSelection(sel);
                        spinner.setOnItemSelectedListener(
                                new android.widget.AdapterView.OnItemSelectedListener() {
                                    @Override
                                    public void onItemSelected(android.widget.AdapterView<?> p,
                                                               View v2, int pos, long id) {
                                        picked[0] = qs.get(pos);
                                    }

                                    @Override
                                    public void onNothingSelected(android.widget.AdapterView<?> p) { }
                                });
                        box.addView(spinner);
                    }
                    EditText ansEt = new EditText(act);
                    ansEt.setHint("答案（提交后不可查看）");
                    ansEt.setTextSize(13.5f);
                    box.addView(ansEt);

                    Ui.dialogBuilder(act)
                            .setTitle("🔐 设置密保")
                            .setView(box)
                            .setPositiveButton("保存", (dg, w) -> {
                                String ans = ansEt.getText().toString().trim();
                                if (ans.length() < 1) {
                                    toast("请填写答案");
                                    return;
                                }
                                Api.securityQuestionSet(prefs.token(), picked[0], ans,
                                        new Api.Cb() {
                                            @Override
                                            public void ok(JSONObject r) {
                                                toast("密保已保存");
                                            }

                                            @Override
                                            public void err(String m) { toast(m); }
                                        });
                            })
                            .setNegativeButton("取消", null)
                            .show();
                });
            }

            @Override
            public void err(String m) { toast(m); }
        });
    }

    /** 意见反馈（走 /feedback 通道，管理端可见）。 */
    private void showFeedbackDialog() {
        LinearLayout box = new LinearLayout(act);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(20), dp(10), dp(20), dp(4));
        EditText et = new EditText(act);
        et.setHint("写下你的建议或遇到的问题（10-200 字）");
        et.setTextSize(13.5f);
        et.setMinLines(4);
        et.setGravity(android.view.Gravity.TOP);
        box.addView(et);
        Ui.dialogBuilder(act)
                .setTitle("💬 意见反馈")
                .setView(box)
                .setPositiveButton("提交", (dg, w) -> {
                    String txt = et.getText().toString().trim();
                    if (txt.length() < 10) {
                        toast("再写详细一点（至少 10 字）");
                        return;
                    }
                    Api.sendFeedback(prefs.token(), txt, new Api.Cb() {
                        @Override
                        public void ok(JSONObject r) {
                            toast("已收到，谢谢你的反馈！");
                        }

                        @Override
                        public void err(String m) { toast(m); }
                    });
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void toast(String s) {
        android.widget.Toast.makeText(act, s, android.widget.Toast.LENGTH_SHORT).show();
    }

    private void runUi(Runnable r) {
        act.runOnUiThread(r);
    }

    private int dp(int v) {
        return Math.round(v * act.getResources().getDisplayMetrics().density);
    }
}
