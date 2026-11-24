package com.gongkao.app;

import android.content.Context;

import java.io.File;

import dalvik.system.DexClassLoader;

/**
 * 热更补丁运行时（批次H · 在线更新）。
 * 流程：UpdateManager 下载 patch.dex（sha256 校验）→ files/patch/patch.dex
 * → 宿主下次启动（或「检查更新」页手动重载）时 child-first 加载，
 * 实例化补丁约定入口 com.gongkao.patch.PatchMain 并执行 apply()。
 * （入口类独立于宿主包名，避免与宿主接口 com.gongkao.app.PatchMain 同名冲突）
 * child-first：先查补丁 dex 里的类，查不到再回退宿主/系统类——
 * 保证补丁可覆盖宿主尚未加载的类，同时共享宿主的接口类型（可安全 cast）。
 */
public final class PatchRuntime {

    private PatchRuntime() { }

    private static volatile String loadedName;   // 本次进程已加载的补丁名（null=未加载）
    private static volatile int loadedVc = -1;

    public static File patchFile(Context ctx) {
        return new File(new File(ctx.getFilesDir(), "patch"), "patch.dex");
    }

    /** 本地是否存有补丁文件（无论本次进程是否已执行）。 */
    public static boolean present(Context ctx) {
        return patchFile(ctx).exists();
    }

    /** 本次进程补丁执行状态（展示用）。 */
    public static String loadedName() { return loadedName; }

    public static int loadedVc() { return loadedVc; }

    /**
     * 尝试加载并执行补丁（宿主 MainActivity 启动时调用，或更新页手动重载）。
     * 幂等：本进程已加载过则直接返回 true。任何异常都吞掉并返回 false——
     * 补丁失败绝不阻塞主流程。
     */
    public static synchronized boolean tryApply(Context ctx) {
        if (loadedName != null) return true;
        File dex = patchFile(ctx);
        if (!dex.exists()) return false;
        try {
            File odex = new File(ctx.getFilesDir(), "patch/odex");
            if (!odex.exists()) odex.mkdirs();
            DexClassLoader patchCl = new DexClassLoader(
                    dex.getAbsolutePath(), odex.getAbsolutePath(),
                    null, ctx.getClassLoader());

            // child-first 委托加载器：补丁类优先，宿主/系统类兜底
            ClassLoader host = ctx.getClassLoader();
            ClassLoader cl = new ClassLoader(host) {
                @Override
                protected Class<?> loadClass(String name, boolean resolve)
                        throws ClassNotFoundException {
                    try {
                        return patchCl.loadClass(name);
                    } catch (ClassNotFoundException e) {
                        return super.loadClass(name, resolve);
                    }
                }
            };

            Class<?> c = cl.loadClass("com.gongkao.patch.PatchMain");
            PatchMain patch = (PatchMain) c.getDeclaredConstructor().newInstance();
            patch.apply(ctx);
            loadedName = patch.name();
            loadedVc = patch.vc();
            android.util.Log.i("PatchRuntime", "补丁已加载: " + loadedName + " vc" + loadedVc);
            return true;
        } catch (Throwable t) {
            android.util.Log.w("PatchRuntime", "补丁加载失败(不阻塞): " + t);
            return false;
        }
    }

    /** 清除补丁（设置/更新页「清除热更补丁」）：删文件 + 复位状态，重启后回到纯宿主。 */
    public static void clear(Context ctx) {
        try {
            File dex = patchFile(ctx);
            if (dex.exists()) dex.delete();
            File odex = new File(ctx.getFilesDir(), "patch/odex");
            File[] fs = odex.listFiles();
            if (fs != null) for (File f : fs) f.delete();
        } catch (Exception ignored) { }
        loadedName = null;
        loadedVc = -1;
    }
}
