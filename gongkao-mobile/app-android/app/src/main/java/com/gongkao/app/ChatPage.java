package com.gongkao.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.InputMethodManager;
import android.widget.EditText;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;

/**
 * 学习问答页（批次F 质感升级）：
 * - AI 回答 Markdown 排版 + 日/夜双主题（Ui 设计系统统一取色）
 * - 「停止生成」：流式进行中发送键变红色停止键，断开 SSE 连接、保留已生成内容
 * - 长按气泡：复制全文；bot 气泡还可「重新生成」
 * - 视图可重建（主题切换）：adapter 复用，聊天记录不丢
 * 多轮会话 session 契约与附件链路（批次A）保持不变。
 */
public class ChatPage {

    private final Activity act;
    private final Prefs prefs;
    private MsgAdapter adapter;
    private ListView list;
    private EditText input;
    private TextView sendBtn;
    private TextView teacherLabel;
    private volatile boolean sending = false;
    private volatile boolean userStopped = false;
    private String lastQuery = "";                    // 「重新生成」用
    private final StringBuilder currentFull = new StringBuilder();
    private String currentNotice;                     // 本轮流式附注（引用数/守卫提示）
    /** 批次A：附件管理器（拍照/相册/文件 → attachment_id 随问携带）。 */
    private final Attach attach;
    /** 拒答话术标志（与 answer.REJECT_TEXT 匹配）。 */
    private static final String REJECT_MARK = "暂未录入资料库";

    public ChatPage(Activity act, Prefs prefs) {
        this.act = act;
        this.prefs = prefs;
        this.attach = new Attach(act, prefs);
    }

    /** MainActivity.onActivityResult 转发入口。 */
    public void onAttachResult(int requestCode, int resultCode, android.content.Intent data) {
        attach.handleResult(requestCode, resultCode, data);
    }

    public View build() {
        LinearLayout page = new LinearLayout(act);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Ui.c(act, "bg"));

        // 顶部老师条
        LinearLayout head = new LinearLayout(act);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        head.setBackgroundColor(Ui.c(act, "card"));
        head.setPadding(Ui.dp(act, 16), Ui.dp(act, 10), Ui.dp(act, 16), Ui.dp(act, 10));
        TextView teacher = new TextView(act);
        teacher.setTextSize(15);
        teacher.setTextColor(Ui.c(act, "text"));
        teacher.setText("👩‍🏫 " + teacherName(prefs.teacherId()));
        teacherLabel = teacher;
        head.addView(teacher);
        page.addView(head, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        // 消息列表（adapter 复用：主题切换重建视图时保留聊天记录）
        if (adapter == null) {
            adapter = new MsgAdapter(act, this::showMsgMenu);
            adapter.add(new MsgAdapter.Msg("bot",
                    "你好，我是" + teacherName(prefs.teacherId())
                            + "。你的问题我会严格依据已录入的课件资料回答，尽管问吧 ✨"));
        }
        list = new ListView(act);
        list.setDivider(null);
        list.setStackFromBottom(true);
        list.setTranscriptMode(ListView.TRANSCRIPT_MODE_ALWAYS_SCROLL);
        list.setAdapter(adapter);
        page.addView(list, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        // 批次A：附件 chips 行（上传中/已就绪，≤3 个）
        HorizontalScrollView chipsScroll = new HorizontalScrollView(act);
        chipsScroll.setHorizontalScrollBarEnabled(false);
        LinearLayout chips = new LinearLayout(act);
        chips.setOrientation(LinearLayout.HORIZONTAL);
        chips.setPadding(Ui.dp(act, 8), Ui.dp(act, 4), Ui.dp(act, 8), Ui.dp(act, 4));
        chipsScroll.addView(chips);
        attach.bindChipsBox(chips);
        chipsScroll.setVisibility(View.GONE);
        page.addView(chipsScroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        // 输入栏
        LinearLayout bar = new LinearLayout(act);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Ui.c(act, "card"));
        bar.setPadding(Ui.dp(act, 12), Ui.dp(act, 8), Ui.dp(act, 12),
                Ui.dp(act, 8) + Ui.navInset(act));

        // 📎 附件按钮（拍照 / 相册图片 / 选择文件）
        TextView attachBtn = new TextView(act);
        attachBtn.setText("📎");
        attachBtn.setTextSize(19);
        attachBtn.setPadding(Ui.dp(act, 4), Ui.dp(act, 4), Ui.dp(act, 8), Ui.dp(act, 4));
        attachBtn.setOnClickListener(v -> {
            android.widget.PopupMenu pm = new android.widget.PopupMenu(act, v);
            pm.getMenu().add(0, 1, 0, "📷 拍照上传");
            pm.getMenu().add(0, 2, 1, "🖼 相册图片");
            pm.getMenu().add(0, 3, 2, "📄 选择文件");
            pm.setOnMenuItemClickListener(item -> {
                if (item.getItemId() == 1) attach.takePhoto();
                else if (item.getItemId() == 2) attach.pickImage();
                else attach.pickFile();
                return true;
            });
            pm.show();
        });
        bar.addView(attachBtn);

        input = new EditText(act);
        input.setHint("输入你的问题…");
        input.setTextSize(15);
        input.setMaxLines(3);
        input.setBackgroundResource(android.R.color.transparent);
        input.setTextColor(Ui.c(act, "text"));
        input.setHintTextColor(Ui.c(act, "faint"));
        input.setPadding(Ui.dp(act, 14), Ui.dp(act, 10), Ui.dp(act, 14), Ui.dp(act, 10));
        LinearLayout.LayoutParams ip = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        input.setOnEditorActionListener((v, actionId, event) -> {
            if (event != null && event.getKeyCode() == KeyEvent.KEYCODE_ENTER) {
                send();
                return true;
            }
            return false;
        });
        bar.addView(input, ip);

        sendBtn = new TextView(act);
        sendBtn.setText("发送");
        sendBtn.setTextColor(0xFFFFFFFF);
        sendBtn.setTextSize(15);
        sendBtn.setGravity(Gravity.CENTER);
        sendBtn.setBackground(Ui.ripple(act, "brand", 20));
        sendBtn.setPadding(Ui.dp(act, 20), Ui.dp(act, 9), Ui.dp(act, 20), Ui.dp(act, 9));
        sendBtn.setOnClickListener(v -> send());
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        sp.setMargins(Ui.dp(act, 10), 0, 0, 0);
        bar.addView(sendBtn, sp);

        page.addView(bar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return page;
    }

    // ------------------------------------------------------------------
    // 发送 / 停止 / 重新生成
    // ------------------------------------------------------------------

    private void send() {
        if (sending) {              // 流式进行中点击 = 停止生成
            stopGenerate();
            return;
        }
        String q = (input.getText() == null ? "" : input.getText().toString()).trim();
        // 批次A：附件就绪但输入为空 → 自动补问题文案（与 Web 端批次28行为一致）
        int attCount = attach.count();
        if (q.isEmpty() && attCount > 0) {
            if (attach.anyUploading()) {
                input.setError("附件还在上传，稍等一下");
                return;
            }
            q = "请结合我上传的内容进行讲解。";
            input.setText(q);
        }
        if (q.isEmpty()) {
            input.setError("先输入问题哦");
            return;
        }
        if (attach.anyUploading()) {
            input.setError("附件还在上传，稍等一下");
            return;
        }
        input.setText("");
        hideKeyboard();
        ask(q, true);
    }

    /** 发起一次问答；addUser=false 用于「重新生成」（不重复添加用户气泡）。 */
    private void ask(String q, boolean addUserMsg) {
        lastQuery = q;
        sending = true;
        userStopped = false;
        currentFull.setLength(0);
        currentNotice = null;
        updateSendBtn();

        java.util.List<String> attIds = attach.readyIds();
        attach.clear();

        if (addUserMsg) adapter.add(new MsgAdapter.Msg("user", q));
        adapter.add(new MsgAdapter.Msg("bot", ""));
        adapter.updateLast("…", null);

        Api.askSSE(prefs.token(), q, prefs.teacherId(), prefs.sessionId(), attIds,
                new Api.SseCb() {
                    @Override
                    public void onStart(String sessionId) {
                        if (!sessionId.isEmpty()) prefs.setSessionId(sessionId);
                    }

                    @Override
                    public void onDelta(String text) {
                        if (text.isEmpty()) return;
                        currentFull.append(text);
                        act.runOnUiThread(() -> adapter.updateLast(
                                currentFull.toString(), currentNotice));
                    }

                    @Override
                    public void onRefs(String rawJson) {
                        try {
                            org.json.JSONObject j = new org.json.JSONObject(rawJson);
                            int n = j.optJSONArray("references") == null
                                    ? 0 : j.optJSONArray("references").length();
                            if (n > 0) currentNotice = "📎 引用 " + n + " 条课件来源";
                            act.runOnUiThread(() -> adapter.updateLast(
                                    currentFull.toString(), currentNotice));
                        } catch (Exception ignored) { }
                    }

                    @Override
                    public void onGuard(String event, String rawJson) {
                        try {
                            org.json.JSONObject j = new org.json.JSONObject(rawJson);
                            if ("guard_block".equals(event)) {
                                currentNotice = "⛔ 内容已拦截："
                                        + j.optString("reason", "与资料不符");
                            } else {
                                currentNotice = "⚠️ 部分表述为助教补充，仅供参考";
                            }
                            act.runOnUiThread(() -> adapter.updateLast(
                                    currentFull.toString(), currentNotice));
                        } catch (Exception ignored) { }
                    }

                    @Override
                    public void onError(String message) {
                        act.runOnUiThread(() -> {
                            if (userStopped) {
                                // 主动停止：保留已生成内容
                                adapter.updateLast(currentFull.length() == 0
                                        ? "（已停止，未收到内容）" : currentFull.toString(),
                                        currentFull.length() == 0 ? null : "⏹ 已停止生成");
                            } else if (currentFull.length() == 0) {
                                adapter.updateLast("⚠️ " + message, null);
                            } else {
                                // 流中断但已有部分回答：保留已生成内容并注明
                                adapter.updateLast(currentFull.toString(),
                                        "⚠️ 连接中断，回答可能不完整");
                            }
                            finishStream();
                        });
                    }

                    @Override
                    public void onDone() {
                        act.runOnUiThread(() -> {
                            String t = currentFull.toString();
                            if (t.contains(REJECT_MARK)) {
                                currentNotice = "该问题超出本老师资料范围";
                            }
                            adapter.updateLast(t.isEmpty() ? "（未收到回答，请重试）" : t,
                                    currentNotice);
                            finishStream();
                        });
                    }
                });
    }

    /** 停止生成：断开 SSE 连接，onError 回调随后收尾（保留已生成内容）。 */
    private void stopGenerate() {
        if (!sending) return;
        userStopped = true;
        Api.cancelSSE();
        // 兜底：极少数情况下断开不抛异常 → 800ms 后自行收尾
        Ui.post(800, () -> {
            if (userStopped && sending) {
                act.runOnUiThread(() -> {
                    adapter.updateLast(currentFull.length() == 0
                            ? "（已停止，未收到内容）" : currentFull.toString(),
                            currentFull.length() == 0 ? null : "⏹ 已停止生成");
                    finishStream();
                });
            }
        });
    }

    private void finishStream() {
        sending = false;
        userStopped = false;
        updateSendBtn();
    }

    /** 发送键 ↔ 停止键外观切换。 */
    private void updateSendBtn() {
        if (sendBtn == null) return;
        act.runOnUiThread(() -> {
            sendBtn.setText(sending ? "■ 停止" : "发送");
            sendBtn.setBackground(Ui.ripple(act, sending ? "red" : "brand", 20));
        });
    }

    // ------------------------------------------------------------------
    // 气泡长按菜单：复制 / 重新生成
    // ------------------------------------------------------------------

    private void showMsgMenu(MsgAdapter.Msg m) {
        boolean isBot = "bot".equals(m.role);
        List<String> opts = new ArrayList<>();
        opts.add("📋 复制全文");
        if (isBot) opts.add("🔄 重新生成");
        Ui.dialogBuilder(act)
                .setItems(opts.toArray(new String[0]), (d, which) -> {
                    String pick = opts.get(which);
                    if (pick.startsWith("📋")) {
                        copyText(m.text);
                    } else {
                        regenerate();
                    }
                })
                .show();
    }

    private void copyText(String s) {
        ClipboardManager cm = (ClipboardManager) act.getSystemService(Context.CLIPBOARD_SERVICE);
        if (cm != null) {
            cm.setPrimaryClip(ClipData.newPlainText("gk", s == null ? "" : s));
            Ui.toast(act, "已复制");
        }
    }

    private void regenerate() {
        if (sending) {
            Ui.toast(act, "等这一轮结束后再试");
            return;
        }
        if (lastQuery.isEmpty()) {
            Ui.toast(act, "暂时没有可重新生成的问题");
            return;
        }
        adapter.removeLast();     // 移除旧回答
        ask(lastQuery, false);    // 不重复添加用户气泡
    }

    private void hideKeyboard() {
        InputMethodManager im = (InputMethodManager) act.getSystemService(Context.INPUT_METHOD_SERVICE);
        if (im != null && input != null) {
            im.hideSoftInputFromWindow(input.getWindowToken(), 0);
        }
    }

    /** 每次切回本页时同步老师名（老师可在「我的」页切换，视图已缓存不再重建）。 */
    public void onShow() {
        if (teacherLabel != null) {
            teacherLabel.setText("👩‍🏫 " + teacherName(prefs.teacherId()));
        }
    }

    static String teacherName(String id) {
        return "T002".equals(id) ? "云舟老师" : "星辰老师";
    }
}
