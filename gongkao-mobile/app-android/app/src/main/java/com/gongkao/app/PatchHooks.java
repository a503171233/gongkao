package com.gongkao.app;

/**
 * 宿主运行时钩子（批次H）：热更补丁可安全改写的行为集中在这里。
 * 场景示例：主 API 域名故障时，下发补丁在 apply() 里调用
 * PatchHooks.setApiBase("https://备用域名/api") 即可完成灾备切换。
 * 所有钩子 volatile——主线程与后台线程即时可见。
 */
public final class PatchHooks {

    private PatchHooks() { }

    /** 当前 API 基址（null = 用编译期默认 Api.BASE）。 */
    private static volatile String sApiBase;

    public static void setApiBase(String base) {
        sApiBase = (base == null || base.isEmpty()) ? null : base;
    }

    public static String apiBase() {
        String b = sApiBase;
        return b != null ? b : Api.BASE;
    }

    /** 公告条文案（非 null 时首页顶部显示，补丁可下发临时公告）。 */
    private static volatile String sAnnouncement;

    public static void setAnnouncement(String text) {
        sAnnouncement = text;
    }

    public static String announcement() {
        return sAnnouncement;
    }
}
