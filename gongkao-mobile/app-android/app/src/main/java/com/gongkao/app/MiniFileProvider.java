package com.gongkao.app;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;

import java.io.File;
import java.io.FileNotFoundException;

/**
 * 拍照临时文件共享 Provider（androidx FileProvider 的纯 framework 手工版）。
 * 相机应用通过 content://com.gongkao.app.files/<文件名> 写入照片，
 * 避免 file:// Uri 在 API 24+ 触发 FileUriExposedException。
 */
public class MiniFileProvider extends ContentProvider {

    public static final String AUTHORITY = "com.gongkao.app.files";

    /** 构造供相机写入的 content Uri（文件名需随机防碰撞）。 */
    public static Uri buildUri(String fileName) {
        return new Uri.Builder()
                .scheme("content")
                .authority(AUTHORITY)
                .appendPath(fileName)
                .build();
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public String getType(Uri uri) {
        String name = uri.getLastPathSegment();
        if (name != null && name.endsWith(".apk")) {
            return "application/vnd.android.package-archive";
        }
        return name != null && name.endsWith(".png") ? "image/png" : "image/jpeg";
    }

    /**
     * 批次H：为下载好的更新 APK 构造安装用 content Uri。
     * 文件需位于 filesDir/updates/ 下（UpdateManager 下载目录）。
     */
    public static Uri uriForUpdateApk(android.content.Context ctx, String fileName) {
        return new Uri.Builder()
                .scheme("content")
                .authority(AUTHORITY)
                .appendPath("updates")
                .appendPath(fileName)
                .build();
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        // 文件名做白名单校验，防路径穿越
        java.util.List<String> segs = uri.getPathSegments();
        String name = uri.getLastPathSegment();
        if (name == null || name.contains("/") || name.contains("..")) {
            throw new FileNotFoundException("非法文件名");
        }
        File f;
        if (segs.size() >= 2 && "updates".equals(segs.get(segs.size() - 2))) {
            // 更新包分发（批次 J 修复）：UpdateManager 下载目录是
            // getExternalFilesDir(DIRECTORY_DOWNLOADS)/updates（外部私有目录）——
            // 此前误读内部 filesDir/updates 导致安装器必然 FileNotFoundException。
            // 优先读外部下载目录，不存在时回退内部目录（兼容历史落盘位置）。
            if (!"r".equals(mode)) throw new FileNotFoundException("只读");
            android.content.Context ctx = getContext();
            File ext = ctx == null ? null
                    : ctx.getExternalFilesDir(android.os.Environment.DIRECTORY_DOWNLOADS);
            if (ext != null) {
                f = new File(new File(ext, "updates"), name);
                if (!f.exists()) {
                    File alt = new File(new File(ctx.getFilesDir(), "updates"), name);
                    if (alt.exists()) f = alt;
                }
            } else {
                f = new File(new File(ctx.getFilesDir(), "updates"), name);
            }
        } else {
            f = new File(getContext().getCacheDir(), name);   // 拍照临时文件（原通道）
        }
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.parseMode(mode));
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection,
                        String[] selectionArgs, String sortOrder) {
        return null;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        return null;
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        return 0;
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        return 0;
    }
}
