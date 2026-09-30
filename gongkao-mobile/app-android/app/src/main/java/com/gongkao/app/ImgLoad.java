package com.gongkao.app;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.widget.ImageView;

import java.io.File;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import android.os.Handler;
import android.os.Looper;

/**
 * 批次28：轻量网络图片加载（纯 framework，零第三方依赖）。
 *
 * - 内存 LRU（约 24MB 上限）+ 磁盘缓存（filesDir/imgcache/，文件名 = URL sha1）
 * - {@link #load}：传入相对路径（/api/qimg/…，自动拼 API 域名）或完整 URL
 * - 解码按目标宽降采样，避免大图 OOM；失败静默保留占位背景
 * - 同一 ImageView 重复 load 时以 job 标记防串图
 */
public final class ImgLoad {

    private static final ExecutorService POOL = Executors.newFixedThreadPool(3);
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final int MEM_MAX = 24 * 1024 * 1024;

    /** 简易 LRU：LinkedHashMap accessOrder + 淘汰头节点。 */
    private static final Map<String, Bitmap> MEM =
            new LinkedHashMap<String, Bitmap>(32, 0.75f, true) {
                @Override protected boolean removeEldestEntry(Map.Entry<String, Bitmap> e) {
                    return size() > 0 && memSize() > MEM_MAX;
                }
            };

    private ImgLoad() { }

    private static int memSize() {
        int n = 0;
        for (Bitmap b : MEM.values()) n += b.getByteCount();
        return n;
    }

    /** URL → 磁盘缓存文件名（sha1 hex 前 20 位）。 */
    private static String hashOf(String url) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-1");
            byte[] d = md.digest(url.getBytes("UTF-8"));
            StringBuilder sb = new StringBuilder();
            for (byte b : d) sb.append(String.format("%02x", b));
            return sb.substring(0, 20);
        } catch (Exception e) {
            return String.valueOf(url.hashCode());
        }
    }

    private static File cacheDir(Context ctx) {
        File d = new File(ctx.getFilesDir(), "imgcache");
        if (!d.exists()) d.mkdirs();
        return d;
    }

    /** 相对路径补全：/api/… → API 域名（热补丁备用域名钩子同样生效）。 */
    public static String resolve(String path) {
        if (path == null || path.isEmpty()) return path;
        if (path.startsWith("http://") || path.startsWith("https://")) return path;
        return PatchHooks.apiBase() + path;
    }

    /**
     * 异步加载图片到 ImageView。加载前保留现有内容/占位背景；
     * 成功后主线程 setImageBitmap 并去掉占位背景。
     */
    public static void load(Context ctx, ImageView iv, String path) {
        final String url = resolve(path);
        if (url == null || url.isEmpty()) return;
        final String key = hashOf(url);
        iv.setTag(R.id.img_job, key);

        Bitmap mem = MEM.get(key);
        if (mem != null && !mem.isRecycled()) {
            iv.setImageBitmap(mem);
            iv.setBackground(null);
            iv.setPadding(0, 0, 0, 0);
            return;
        }

        // 磁盘缓存命中（后台解码，小文件解码很快但避免主线程 IO）
        final File f = new File(cacheDir(ctx), key);
        POOL.execute(() -> {
            Bitmap bmp = null;
            try {
                if (f.exists() && f.length() > 0) {
                    bmp = decodeFile(f, 1080);
                }
                if (bmp == null) {
                    bmp = fetch(ctx, url, f, 1080);
                }
            } catch (Exception ignored) { }
            Bitmap fin = bmp;
            MAIN.post(() -> {
                if (!key.equals(iv.getTag(R.id.img_job))) return;  // 已复用他图
                if (fin != null) {
                    MEM.put(key, fin);
                    iv.setImageBitmap(fin);
                    iv.setBackground(null);
                    iv.setPadding(0, 0, 0, 0);
                }   // 失败：保留占位底
            });
        });
    }

    /** 网络抓取 → 落盘 → 降采样解码。 */
    private static Bitmap fetch(Context ctx, String url, File f, int maxW)
            throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(10000);
        conn.setReadTimeout(15000);
        int code = conn.getResponseCode();
        if (code != 200) return null;
        InputStream is = conn.getInputStream();
        java.io.FileOutputStream os = new java.io.FileOutputStream(f);
        byte[] buf = new byte[8192];
        int n, total = 0;
        while ((n = is.read(buf)) > 0 && total < 4 * 1024 * 1024) {
            os.write(buf, 0, n);
            total += n;
        }
        os.close();
        is.close();
        if (total == 0) return null;
        return decodeFile(f, maxW);
    }

    /** 按宽降采样解码（inSampleSize 取 2 的幂）。 */
    private static Bitmap decodeFile(File f, int maxW) {
        try {
            BitmapFactory.Options o = new BitmapFactory.Options();
            o.inJustDecodeBounds = true;
            BitmapFactory.decodeFile(f.getAbsolutePath(), o);
            int sample = 1;
            while (o.outWidth / sample > maxW) sample *= 2;
            BitmapFactory.Options o2 = new BitmapFactory.Options();
            o2.inSampleSize = sample;
            Bitmap b = BitmapFactory.decodeFile(f.getAbsolutePath(), o2);
            if (b == null && f.exists()) f.delete();   // 缓存损坏 → 重下
            return b;
        } catch (Exception e) {
            return null;
        }
    }
}
