package com.gongkao.app;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * 后端 API 客户端（与 Web 端 a4-request 同一契约，直连 FastAPI，无 WebView）。
 * 全部网络操作在后台线程，结果经回调抛回（回调在后台线程触发，调用方自行切 UI 线程）。
 */
public class Api {

    /** 后端基址：与站点同域的 /api 反代入口。更换部署地址时改这里。
     *  运行时实际取值经 PatchHooks.apiBase()（热更补丁可灾备切换域名）。 */
    public static final String BASE =
            "https://a2a0641667069c341.app.workbuddy.host/api";

    public interface Cb {
        void ok(JSONObject data);
        void err(String message);
    }

    /** SSE 事件回调（与 Web 端 a4 GK.sse 事件一一对应）。 */
    public interface SseCb {
        void onStart(String sessionId);
        void onDelta(String text);
        void onRefs(String rawJson);
        void onGuard(String event, String rawJson);
        void onError(String message);
        void onDone();
    }

    /** 当前流式 /ask 连接（「停止生成」用：断开底层连接，readLine 随即抛异常收尾）。 */
    private static volatile HttpURLConnection sSseConn;

    /** 停止正在进行的 AI 流式生成。 */
    public static void cancelSSE() {
        HttpURLConnection c = sSseConn;
        sSseConn = null;
        if (c != null) {
            try { c.disconnect(); } catch (Exception ignored) { }
        }
    }

    // ------------------------------------------------------------------
    // 通用 POST/GET（JSON）
    // ------------------------------------------------------------------
    private static HttpURLConnection open(String path, String method, String token) throws Exception {
        HttpURLConnection conn = (HttpURLConnection)
                new URL(PatchHooks.apiBase() + path).openConnection();
        conn.setRequestMethod(method);
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(method.equals("POST") && path.equals("/ask") ? 310000 : 20000);
        if (token != null && !token.isEmpty()) {
            conn.setRequestProperty("Authorization", "Bearer " + token);
            // 托管平台公网代理可能剥离 Authorization 头（Web 端 a4-request 同款冗余），
            // 冗余携带 X-Auth-Token，nginx 侧会转换回传，双保险。
            conn.setRequestProperty("X-Auth-Token", token);
        }
        return conn;
    }

    /** Query 参数 URL 编码（UTF-8）。 */
    private static String urlEncode(String s) {
        try {
            return java.net.URLEncoder.encode(s == null ? "" : s, "UTF-8");
        } catch (Exception e) {
            return s == null ? "" : s;
        }
    }

    private static String readStream(InputStream is) throws Exception {
        BufferedReader br = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8));
        StringBuilder sb = new StringBuilder();
        String line;
        while ((line = br.readLine()) != null) sb.append(line);
        br.close();
        return sb.toString();
    }

    private static void writeBody(HttpURLConnection conn, JSONObject body) throws Exception {
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json");
        OutputStream os = conn.getOutputStream();
        os.write(body.toString().getBytes(StandardCharsets.UTF_8));
        os.close();
    }

    private static void errFromResp(HttpURLConnection conn, Cb cb) {
        try {
            InputStream es = conn.getErrorStream();
            String raw = es == null ? "" : readStream(es);
            String msg = raw;
            try {
                JSONObject j = new JSONObject(raw);
                msg = j.optString("detail", j.optString("message", raw));
            } catch (Exception ignored) { }
            cb.err(msg.isEmpty() ? ("HTTP " + conn.getResponseCode()) : msg);
        } catch (Exception e) {
            int code = -1;
            try { code = conn.getResponseCode(); } catch (Exception ignored) { }
            cb.err(code > 0 ? ("HTTP " + code) : "网络异常");
        }
    }

    /** 更新检查 GET /app-update.json（nginx 静态配置，公开接口；批次H 在线更新）。 */
    public static void updateCheck(Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/app-update.json", "GET", null);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) {
                cb.err("网络异常：" + e.getMessage());
            }
        }).start();
    }

    /** 登录（登录即注册，契约与 Web 端一致）。 */
    public static void login(String username, String password, Cb cb) {        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/login", "POST", null);
                JSONObject body = new JSONObject();
                body.put("username", username);
                body.put("password", password);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) {
                cb.err("网络异常：" + e.getMessage());
            }
        }).start();
    }

    /** 当前用户信息（含 quota_left）。 */
    public static void me(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) {
                cb.err("网络异常：" + e.getMessage());
            }
        }).start();
    }

    /** 登出：吊销服务端 token（幂等）。失败不阻塞——调用方随后清理本地存储。 */
    public static void logout(String token) {
        if (token == null || token.isEmpty()) return;
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/logout", "POST", token);
                conn.getResponseCode();   // 只要请求发出即可，响应内容不关心
            } catch (Exception ignored) { }
        }).start();
    }

    // ------------------------------------------------------------------
    // 附件上传（POST /ask/upload，multipart/form-data 字段名 file）
    // 返回 {attachment_id, kind, name, size, text_chars}；图片≤8MB、≤3 个/问
    // ------------------------------------------------------------------
    public static void uploadAttachment(String token, String filename, String mime,
                                        byte[] data, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/ask/upload", "POST", token);
                String boundary = "----GK" + System.currentTimeMillis();
                conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
                java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
                bos.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\""
                        + filename + "\"\r\nContent-Type: " + mime + "\r\n\r\n")
                        .getBytes(StandardCharsets.UTF_8));
                bos.write(data);
                bos.write(("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
                OutputStream os = conn.getOutputStream();
                os.write(bos.toByteArray());
                os.close();
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) {
                cb.err("上传失败：" + e.getMessage());
            }
        }).start();
    }

    // ------------------------------------------------------------------
    // 消息中心（学习中心顶部角标 + 列表 + 已读）
    // ------------------------------------------------------------------
    /** 未读数 GET /messages/unread → {count}。 */
    public static void messageUnread(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/messages/unread", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) {
                cb.err("网络异常：" + e.getMessage());
            }
        }).start();
    }

    /** 消息列表 GET /messages?page=&size= → {total, messages:[…], unread}。 */
    public static void messages(String token, int page, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/messages?page=" + page + "&size=20", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) {
                cb.err("网络异常：" + e.getMessage());
            }
        }).start();
    }

    /** 标记单条已读 POST /messages/{id}/read → {ok}。 */
    public static void messageRead(String token, long messageId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/messages/" + messageId + "/read", "POST", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) {
                cb.err("网络异常：" + e.getMessage());
            }
        }).start();
    }

    /** 全部已读 POST /messages/read_all → {ok, marked}。 */
    public static void messageReadAll(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/messages/read_all", "POST", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) {
                cb.err("网络异常：" + e.getMessage());
            }
        }).start();
    }

    // ------------------------------------------------------------------
    // 批次B：刷题线（练习 / 智能组卷 / 在线模考）
    // 契约：/practice/* · /smartexam/* （题目 {question,type,choices,id,category,…}）
    // ------------------------------------------------------------------
    /** 练习分类 GET /practice/categories → {categories:[{category,count}]}。 */
    public static void practiceCategories(String token, String teacherId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/practice/categories?teacher_id=" + teacherId, "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 抽题 GET /practice/next（mode: "" 普通 / today / wrong；category 分类专项）。 */
    public static void practiceNext(String token, String teacherId, String category,
                                    String mode, long questionId, Cb cb) {
        new Thread(() -> {
            try {
                StringBuilder p = new StringBuilder("/practice/next?teacher_id=").append(teacherId);
                if (category != null && !category.isEmpty()) {
                    p.append("&category=").append(java.net.URLEncoder.encode(category, "UTF-8"));
                }
                if (mode != null && !mode.isEmpty()) p.append("&mode=").append(mode);
                if (questionId > 0) p.append("&question_id=").append(questionId);
                HttpURLConnection conn = open(p.toString(), "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 提交练习 POST /practice/submit → {score,auto_graded,feedback,analysis,category}。 */
    public static void practiceSubmit(String token, String question, String answer,
                                      String teacherId, long questionId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/practice/submit", "POST", token);
                JSONObject body = new JSONObject();
                body.put("question", question);
                body.put("answer", answer);
                body.put("teacher_id", teacherId);
                if (questionId > 0) body.put("question_id", questionId);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 练习统计 GET /practice/stats → {total,graded,correct,accuracy,…}。 */
    public static void practiceStats(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/practice/stats", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 学情报告 GET /practice/report → {graded,correct,accuracy,by_category,weak,recent_days}。 */
    public static void practiceReport(String token, String teacherId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/practice/report?teacher_id=" + teacherId, "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 智能组卷 POST /smartexam/generate（body 直接透传）。 */
    public static void smartGenerate(String token, JSONObject body, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/smartexam/generate", "POST", token);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 在线模考生成 POST /smartexam/mock/generate（mix 结构配比默认空=均衡组卷）。 */
    public static void mockGenerate(String token, String teacherId, int durationMins, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/smartexam/mock/generate", "POST", token);
                JSONObject body = new JSONObject();
                body.put("teacher_id", teacherId);
                body.put("duration_mins", durationMins);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 组卷详情 GET /smartexam/papers/{id}（作答中剥答案）。 */
    public static void paperDetail(String token, long paperId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/smartexam/papers/" + paperId, "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 交卷 POST /smartexam/papers/{id}/submit answers={"<qid>": "答案"} → 成绩。 */
    public static void paperSubmit(String token, long paperId, JSONObject answers, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/smartexam/papers/" + paperId + "/submit", "POST", token);
                JSONObject body = new JSONObject();
                body.put("answers", answers);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 我的组卷/模考列表 GET /smartexam/papers → {total, papers}。 */
    public static void paperList(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/smartexam/papers", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    // ------------------------------------------------------------------
    // 批次C：错题本 / 收藏夹
    // ------------------------------------------------------------------
    /** 错题列表 GET /mistakes → {mistakes:[…]}。 */
    public static void listMistakes(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/mistakes", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 收藏列表 GET /favorites → {favorites:[…]}。 */
    public static void listFavorites(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/favorites", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 删除错题 DELETE /mistakes/{id}。 */
    public static void deleteMistake(String token, long id, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/mistakes/" + id, "DELETE", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject());
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 取消收藏 DELETE /favorites/{id}。 */
    public static void deleteFavorite(String token, long id, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/favorites/" + id, "DELETE", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject());
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    // ------------------------------------------------------------------
    // 批次D：成长线（学习计划 / 学习报告 / 激励排行 / 备考文章 / 申论 / 面试）
    // ------------------------------------------------------------------
    /** 当前学习计划 GET /study/plan → {plan}。 */
    public static void studyPlanGet(String token, String teacherId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/study/plan?teacher_id=" + teacherId, "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 生成 7 天计划 POST /study/plan/generate。 */
    public static void studyPlanGenerate(String token, String teacherId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/study/plan/generate?teacher_id=" + teacherId, "POST", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 计划打卡 POST /study/plan/checkin {plan_id, day, idx}。 */
    public static void studyPlanCheckin(String token, long planId, int day, int idx, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/study/plan/checkin", "POST", token);
                JSONObject body = new JSONObject();
                body.put("plan_id", planId);
                body.put("day", day);
                body.put("idx", idx);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 学习报告 GET /me/learning-report → {weekly, review, inventory, exam}。 */
    public static void learningReport(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/learning-report", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 激励面板 GET /me/incentive → {points, level, level_name, streak_days, stats}。 */
    public static void incentive(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/incentive", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 排行榜 GET /me/incentive/board → {board:[…], me?}。 */
    public static void incentiveBoard(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/incentive/board", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 文章详情 GET /articles/{post_id} → post + replies + analysis。 */
    public static void articlesDetail(String token, String postId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/articles/" + postId, "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 文章 AI 要点提炼 POST /articles/analyze {post_id} → {analysis, cached}（20s 冷却）。 */
    public static void articlesAnalyze(String token, String postId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/articles/analyze", "POST", token);
                JSONObject body = new JSONObject();
                body.put("post_id", postId);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 知识树 GET /public/knowledge/tree?teacher_id → {nodes[{node_id,name,category,description,children[]}], total}。 */
    public static void knowledgeTree(String token, String teacherId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/public/knowledge/tree?teacher_id="
                        + urlEncode(teacherId), "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 知识掌握度 GET /knowledge/mastery?teacher_id → {mastery{nodeId:{done,correct,accuracy,level}}, total_done}。 */
    public static void knowledgeMastery(String token, String teacherId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/knowledge/mastery?teacher_id="
                        + urlEncode(teacherId), "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 按知识节点抽题 GET /practice/by-knowledge?node_id → 一道题结构。 */
    public static void practiceByKnowledge(String token, String nodeId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/practice/by-knowledge?node_id="
                        + urlEncode(nodeId), "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 备考文章 GET /articles/home → {items, total}。 */
    public static void articlesHome(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/articles/home", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 申论批改 POST /me/essay/grade {essay_type,title,prompt,essay} → 维度评分。 */
    public static void essayGrade(String token, String essayType, String title,
                                  String prompt, String essay, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/essay/grade", "POST", token);
                JSONObject body = new JSONObject();
                body.put("essay_type", essayType);
                if (!title.isEmpty()) body.put("title", title);
                if (!prompt.isEmpty()) body.put("prompt", prompt);
                body.put("essay", essay);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 申论历史 GET /me/essay/history → {items:[…]}。 */
    public static void essayHistory(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/essay/history", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 面试出题 POST /me/interview/question {module,difficulty} → {id,question}。 */
    public static void interviewQuestion(String token, String module, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/interview/question", "POST", token);
                JSONObject body = new JSONObject();
                body.put("module", module);
                body.put("difficulty", "standard");
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 面试点评 POST /me/interview/answer {qid,answer} → {score,review,advice}。 */
    public static void interviewAnswer(String token, long qid, String answer, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/interview/answer", "POST", token);
                JSONObject body = new JSONObject();
                body.put("qid", qid);
                body.put("answer", answer);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    // ------------------------------------------------------------------
    // 学习问答 SSE 流（POST /ask, text/event-stream）
    // 事件协议与 Web 端一致：start/delta/refs/guard_warn/guard_block/error/done
    // ------------------------------------------------------------------
public static void askSSE(String token, String query, String teacherId,
                               String sessionId, java.util.List<String> attachments, SseCb cb) {
        final int MAX_RETRIES = 3;
        new Thread(() -> {
            for (int attempt = 0; attempt < MAX_RETRIES; attempt++) {
                if (attempt > 0) {
                    try { Thread.sleep(2000L << (attempt - 1)); } catch (InterruptedException ignored) {}
                }
                java.util.concurrent.atomic.AtomicBoolean errored =
                        new java.util.concurrent.atomic.AtomicBoolean(false);
                SseCb safe = once(cb, errored);
                try {
                    HttpURLConnection conn = open("/ask", "POST", token);
                    sSseConn = conn;
                    conn.setRequestProperty("Accept", "text/event-stream");
                    JSONObject body = new JSONObject();
                    body.put("query", query);
                    body.put("teacher_id", teacherId);
                    body.put("stream", true);
                    body.put("session_id", sessionId == null ? "" : sessionId);
                    if (attachments != null && !attachments.isEmpty()) {
                        org.json.JSONArray arr = new org.json.JSONArray();
                        for (String a : attachments) arr.put(a);
                        body.put("attachments", arr);
                    }
                    writeBody(conn, body);
                    int code = conn.getResponseCode();
                    if (code != 200) {
                        InputStream es = conn.getErrorStream();
                        String raw = es == null ? "" : readStream(es);
                        String msg = raw;
                        try {
                            JSONObject j = new JSONObject(raw);
                            msg = j.optString("detail", j.optString("message", raw));
                        } catch (Exception ignored) {}
                        safe.onError(msg.isEmpty() ? ("HTTP " + code) : msg);
                        return;
                    }
                    BufferedReader br = new BufferedReader(
                            new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8));
                    String line;
                    String event = "message";
                    StringBuilder data = new StringBuilder();
                    while ((line = br.readLine()) != null) {
                        if (line.isEmpty()) {
                            dispatch(event, data.toString(), safe);
                            event = "message";
                            data.setLength(0);
                            continue;
                        }
                        if (line.startsWith("event:")) {
                            event = line.substring(6).trim();
                        } else if (line.startsWith("data:")) {
                            data.append(line.substring(5).trim());
                        }
                    }
                    br.close();
                    safe.onDone();
                    return;
                } catch (java.net.ConnectException | java.net.SocketTimeoutException e) {
                    if (attempt == MAX_RETRIES - 1) cb.onError("连接失败（已重试" + MAX_RETRIES + "次）: " + e.getMessage());
                } catch (java.io.IOException e) {
                    if (attempt == MAX_RETRIES - 1) cb.onError("连接中断（已重试" + MAX_RETRIES + "次）: " + e.getMessage());
                } catch (Exception e) {
                    cb.onError("连接中断：" + e.getMessage());
                    return;
                }
            }
        }).start();
    }

    /** error 后回调只发生一次：onError 去重、onDone 被跳过。 */
    private static SseCb once(SseCb cb, java.util.concurrent.atomic.AtomicBoolean errored) {
        return new SseCb() {
            @Override public void onStart(String s) { cb.onStart(s); }
            @Override public void onDelta(String t) { cb.onDelta(t); }
            @Override public void onRefs(String r) { cb.onRefs(r); }
            @Override public void onGuard(String e, String r) { cb.onGuard(e, r); }
            @Override public void onError(String m) {
                if (errored.compareAndSet(false, true)) cb.onError(m);
            }
            @Override public void onDone() {
                if (!errored.get()) cb.onDone();
            }
        };
    }

    // ------------------------------------------------------------------
    // 批次E · 论坛
    // ------------------------------------------------------------------

    /** 版块列表 GET /forum/categories → {categories:[{category,count}]}。 */
    public static void forumCategories(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/forum/categories", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 帖子列表 GET /forum/posts?category&sort(new/hot)&keyword&page&size → {items,total}。 */
    public static void forumPosts(String token, String category, String sort,
                                  int page, int size, Cb cb) {
        new Thread(() -> {
            try {
                String q = "/forum/posts?sort=" + urlEncode(sort)
                        + "&page=" + page + "&size=" + size;
                if (category != null && !category.isEmpty())
                    q += "&category=" + urlEncode(category);
                HttpURLConnection conn = open(q, "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 帖子详情 GET /forum/posts/{id} → post（含 liked）+ replies 不含（另拉）。 */
    public static void forumPost(String token, String postId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/forum/posts/" + postId, "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 发帖 POST /forum/posts {title,category,content}（标题 3-80 字、内容 5-8000 字、15s 频控）。 */
    public static void forumCreate(String token, String title, String category,
                                   String content, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/forum/posts", "POST", token);
                JSONObject body = new JSONObject();
                body.put("title", title);
                body.put("category", category);
                body.put("content", content);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 回帖列表 GET /forum/posts/{id}/replies?page&size → {items,total}。 */
    public static void forumReplies(String token, String postId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/forum/posts/" + postId
                        + "/replies?page=1&size=50", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 发回帖 POST /forum/posts/{id}/replies {content}。 */
    public static void forumReply(String token, String postId, String content, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/forum/posts/" + postId + "/replies",
                        "POST", token);
                JSONObject body = new JSONObject();
                body.put("content", content);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 点赞 POST /forum/posts/{id}/like（幂等翻转，返回 {likes,liked}）。 */
    public static void forumLike(String token, String postId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/forum/posts/" + postId + "/like",
                        "POST", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 举报 POST /forum/reports {target_kind:"post",target_id,reason}。 */
    public static void forumReport(String token, String postId, String reason, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/forum/reports", "POST", token);
                JSONObject body = new JSONObject();
                body.put("target_kind", "post");
                body.put("target_id", postId);
                body.put("reason", reason);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    // ------------------------------------------------------------------
    // 批次E · 粉笔绑定（账密/短信原生；滑块跳网页处理）
    // ------------------------------------------------------------------

    /** 绑定状态 GET /me/fenbi/binding → {binding{status,phone,check_ok,wrong_count,…},latest}。 */
    public static void fenbiBinding(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/fenbi/binding", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 账密登录会话 POST /me/fenbi/login/password {account,password} → {sid,state,hint}。 */
    public static void fenbiLoginPassword(String token, String account,
                                          String password, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/fenbi/login/password", "POST", token);
                JSONObject body = new JSONObject();
                body.put("account", account);
                body.put("password", password);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 短信登录会话 POST /me/fenbi/login/sms {phone} → {sid,state,hint}。 */
    public static void fenbiLoginSms(String token, String phone, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/fenbi/login/sms", "POST", token);
                JSONObject body = new JSONObject();
                body.put("phone", phone);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 会话状态轮询 GET /me/fenbi/login/status?sid → {sid,state,shot,error,hint,phone,mode}。 */
    public static void fenbiLoginStatus(String token, String sid, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/fenbi/login/status?sid="
                        + urlEncode(sid), "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 提交短信验证码 POST /me/fenbi/login/code {sid,code}。 */
    public static void fenbiLoginCode(String token, String sid, String code, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/fenbi/login/code", "POST", token);
                JSONObject body = new JSONObject();
                body.put("sid", sid);
                body.put("code", code);
                writeBody(conn, body);
                int code2 = conn.getResponseCode();
                if (code2 != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 取消会话 POST /me/fenbi/login/cancel {sid}。 */
    public static void fenbiLoginCancel(String token, String sid, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/fenbi/login/cancel", "POST", token);
                JSONObject body = new JSONObject();
                body.put("sid", sid);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 解绑 POST /me/fenbi/unbind。 */
    public static void fenbiUnbind(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/fenbi/unbind", "POST", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    // ---------------- 批次 J · 粉笔同步明细 / 分析触发 / 任务书同步 ----------------

    /** 错题快照明细 GET /me/fenbi/wrongs?limit&offset → {items,total}。 */
    public static void fenbiWrong(String token, int limit, int offset, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/fenbi/wrongs?limit=" + limit
                        + "&offset=" + offset, "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 收藏快照明细 GET /me/fenbi/collects?limit&offset → {items,total}。 */
    public static void fenbiCollects(String token, int limit, int offset, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/fenbi/collects?limit=" + limit
                        + "&offset=" + offset, "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 模考试卷列表 GET /me/fenbi/mock/exams → {exams:[…]}。 */
    public static void fenbiMocks(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/fenbi/mock/exams", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 模考试卷详情 GET /me/fenbi/mock/exams/{id} → 试卷 + report + answers。 */
    public static void fenbiMockDetail(String token, long examId, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/fenbi/mock/exams/" + examId, "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 触发同步+AI 提升计划 POST /me/fenbi/analyze（冷却 6h）→ {ok,analysis_id}。 */
    public static void fenbiAnalyze(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/fenbi/analyze", "POST", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 分析进度/任务书轮询 GET /me/fenbi/analysis/latest → {latest}。 */
    public static void fenbiAnalysisLatest(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/fenbi/analysis/latest", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 任务书手动同步到学习计划 POST /me/fenbi/sync-plan → {ok,plan_id}。 */
    public static void fenbiSyncPlan(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/fenbi/sync-plan", "POST", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    // ------------------------------------------------------------------
    // 批次E · 会员充值 / 密码找回 / 意见反馈
    // ------------------------------------------------------------------

    /** 会员状态 GET /me/membership → {role,is_member,member_expire_at,days_remaining}。 */
    public static void membership(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/membership", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 充值码激活 POST /orders/recharge-code/activate {code}（5 次/10 分钟频控，成败均计数）。 */
    public static void redeemCode(String token, String code, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/orders/recharge-code/activate",
                        "POST", token);
                JSONObject body = new JSONObject();
                body.put("code", code);
                writeBody(conn, body);
                int rc = conn.getResponseCode();
                if (rc != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 找回第一步 POST /password/forgot/question {username} → {can_recover,security_question}。 */
    public static void forgotQuestion(String username, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/password/forgot/question", "POST", null);
                JSONObject body = new JSONObject();
                body.put("username", username);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 找回第二步 POST /password/forgot/verify {username,answer} → {ticket}。 */
    public static void forgotVerify(String username, String answer, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/password/forgot/verify", "POST", null);
                JSONObject body = new JSONObject();
                body.put("username", username);
                body.put("answer", answer);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 找回第三步 POST /password/forgot/reset {ticket,new_password} → {reset}。 */
    public static void forgotReset(String ticket, String newPassword, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/password/forgot/reset", "POST", null);
                JSONObject body = new JSONObject();
                body.put("ticket", ticket);
                body.put("new_password", newPassword);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 设置密保 GET /me/security-question → {has_security,security_question,premade[]}。 */
    public static void securityQuestionGet(String token, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/security-question", "GET", token);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 设置/更新密保 POST /me/security-question {question,answer}。 */
    public static void securityQuestionSet(String token, String question,
                                           String answer, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/me/security-question", "POST", token);
                JSONObject body = new JSONObject();
                body.put("question", question);
                body.put("answer", answer);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    /** 意见反馈 POST /feedback {rating:"up",question:"App意见反馈",answer:"",reason:内容}。 */
    public static void sendFeedback(String token, String content, Cb cb) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = open("/feedback", "POST", token);
                JSONObject body = new JSONObject();
                body.put("rating", "up");
                body.put("question", "App 意见反馈");
                body.put("answer", "");
                body.put("reason", content);
                writeBody(conn, body);
                int code = conn.getResponseCode();
                if (code != 200) { errFromResp(conn, cb); return; }
                cb.ok(new JSONObject(readStream(conn.getInputStream())));
            } catch (Exception e) { cb.err("网络异常：" + e.getMessage()); }
        }).start();
    }

    private static void dispatch(String event, String data, SseCb cb) {
        if (data.isEmpty()) return;
        try {
            JSONObject j = new JSONObject(data);
            switch (event) {
                case "start":
                    cb.onStart(j.optString("session_id", ""));
                    break;
                case "delta":
                    cb.onDelta(j.optString("text", ""));
                    break;
                case "refs":
                    cb.onRefs(data);
                    break;
                case "guard_block":
                case "guard_warn":
                    cb.onGuard(event, data);
                    break;
                case "error":
                    cb.onError(j.optString("message", "服务异常"));
                    break;
                case "done":
                    cb.onDone();
                    break;
                default:
                    break;   // 未知事件静默（协议向后兼容）
            }
        } catch (Exception ignored) { }
    }
}
