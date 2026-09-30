package com.gongkao.app;

import android.content.Context;
import android.content.SharedPreferences;

/** 登录态与配置存储（token 契约与 Web 端 localStorage gk_token 一致）。 */
public class Prefs {
    private static final String NAME = "gongkao";
    private final SharedPreferences sp;

    public Prefs(Context c) {
        sp = c.getApplicationContext().getSharedPreferences(NAME, Context.MODE_PRIVATE);
    }

    public String token() { return sp.getString("gk_token", ""); }
    public void setToken(String t) { sp.edit().putString("gk_token", t).apply(); }

    public String username() { return sp.getString("username", ""); }
    public void setUsername(String u) { sp.edit().putString("username", u).apply(); }

    public String role() { return sp.getString("role", ""); }
    public void setRole(String r) { sp.edit().putString("role", r).apply(); }

    public int quotaLeft() { return sp.getInt("quota_left", -1); }
    public void setQuotaLeft(int q) { sp.edit().putInt("quota_left", q).apply(); }

    /** 当前老师（T001 星辰 / T002 云舟），问答请求使用。 */
    public String teacherId() { return sp.getString("teacher_id", "T001"); }
    public void setTeacherId(String t) { sp.edit().putString("teacher_id", t).apply(); }

    public boolean loggedIn() { return !token().isEmpty(); }

    /** 夜间模式三态：0 跟随系统 / 1 浅色 / 2 深色（设置页可改，Ui.setNightMode 同步缓存）。 */
    public int nightMode() { return sp.getInt("night_mode", 0); }
    public void setNightMode(int m) { sp.edit().putInt("night_mode", m).apply(); }

    /** 已弹过更新提示的 latest_vc（避免同一版本反复打扰）。 */
    public int updatePromptedVc() { return sp.getInt("update_prompted_vc", 0); }
    public void setUpdatePromptedVc(int vc) { sp.edit().putInt("update_prompted_vc", vc).apply(); }

    /** 本地已加载热更补丁的 vc（远程 patch.vc 大于它才下载）。 */
    public int patchVc() { return sp.getInt("patch_vc", 0); }
    public void setPatchVc(int vc) { sp.edit().putInt("patch_vc", vc).apply(); }

    public void logout() {
        sp.edit().remove("gk_token").remove("username").remove("role")
                .remove("quota_left").remove("session_id").apply();
    }

    /** 会话 id（多轮上下文，start 事件回填）。 */
    public String sessionId() { return sp.getString("session_id", ""); }
    public void setSessionId(String s) { sp.edit().putString("session_id", s).apply(); }
}
