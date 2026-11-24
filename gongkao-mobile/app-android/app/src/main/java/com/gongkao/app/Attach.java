package com.gongkao.app;

import android.app.Activity;
import android.content.Intent;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Matrix;
import android.media.ExifInterface;
import android.net.Uri;
import android.provider.MediaStore;
import android.view.Gravity;
import android.view.View;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;

/**
 * 问答附件管理（批次A）：
 * - 📷 拍照 / 🖼 相册 / 📄 文件 三个来源（拍照走 MiniFileProvider content://）
 * - 图片解码采样压缩至 ≤1600px / JPEG 82（后端上限 8MB）
 * - 上传 POST /ask/upload → attachment_id，随 /ask 的 attachments 字段携带（≤3 个）
 * - chips 行展示上传进度与附件名，可单个移除
 */
public class Attach {

    public static class Item {
        public String attId;     // 后端 attachment_id（上传完成前为 null）
        public String label;     // 显示名
        public boolean uploading;
    }

    /** MainActivity.onActivityResult 转发的请求码。 */
    public static final int REQ_CAMERA = 101;
    public static final int REQ_GALLERY = 102;
    public static final int REQ_FILE = 103;

    private static final int MAX_ATTACH = 3;

    private final Activity act;
    private final Prefs prefs;
    private final List<Item> items = new ArrayList<>();
    private LinearLayout chipsBox;      // chips 容器（ChatPage 注入）
    private Uri pendingCameraUri;       // 拍照目标文件
    private String pendingCameraName;
    private String pendingFileName = "图片.jpg";  // 选中的原始文件名（由来源更新）

    public Attach(Activity act, Prefs prefs) {
        this.act = act;
        this.prefs = prefs;
    }

    /** 注入 chips 容器（视图缓存后仍有效）。 */
    public void bindChipsBox(LinearLayout box) {
        chipsBox = box;
        paintChips();
    }

    public int count() {
        return items.size();
    }

    /** 已上传完成的 attachment_id 列表（供 /ask 携带）。 */
    public List<String> readyIds() {
        List<String> out = new ArrayList<>();
        for (Item it : items) if (it.attId != null) out.add(it.attId);
        return out;
    }

    /** 是否还有附件在传（发送前校验）。 */
    public boolean anyUploading() {
        for (Item it : items) if (it.attId == null) return true;
        return false;
    }

    public void clear() {
        items.clear();
        paintChips();
    }

    // ------------------------------------------------------------------
    // 入口动作
    // ------------------------------------------------------------------
    public void pickImage() {
        if (full()) return;
        Intent i = new Intent(Intent.ACTION_GET_CONTENT);
        i.addCategory(Intent.CATEGORY_OPENABLE);
        i.setType("image/*");
        act.startActivityForResult(Intent.createChooser(i, "选择图片"), REQ_GALLERY);
    }

    public void pickFile() {
        if (full()) return;
        Intent i = new Intent(Intent.ACTION_GET_CONTENT);
        i.addCategory(Intent.CATEGORY_OPENABLE);
        i.setType("*/*");
        act.startActivityForResult(Intent.createChooser(i, "选择文件（doc/pdf/txt）"), REQ_FILE);
    }

    public void takePhoto() {
        if (full()) return;
        try {
            pendingCameraName = "gk_cam_" + System.currentTimeMillis() + ".jpg";
            File out = new File(act.getCacheDir(), pendingCameraName);
            out.getParentFile().mkdirs();
            out.delete();
            pendingCameraUri = MiniFileProvider.buildUri(pendingCameraName);
            Intent i = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
            i.putExtra(MediaStore.EXTRA_OUTPUT, pendingCameraUri);
            i.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);
            if (i.resolveActivity(act.getPackageManager()) != null) {
                act.startActivityForResult(i, REQ_CAMERA);
            } else {
                toast("未找到相机应用");
            }
        } catch (Exception e) {
            toast("相机启动失败：" + e.getMessage());
        }
    }

    private boolean full() {
        if (items.size() >= MAX_ATTACH) {
            toast("每问最多携带 " + MAX_ATTACH + " 个附件");
            return true;
        }
        return false;
    }

    // ------------------------------------------------------------------
    // 结果处理（MainActivity.onActivityResult 转发）
    // ------------------------------------------------------------------
    public void handleResult(int requestCode, int resultCode, Intent data) {
        if (resultCode != Activity.RESULT_OK) return;
        try {
            if (requestCode == REQ_CAMERA) {
                if (pendingCameraUri == null) return;
                upload(pendingCameraUri, "拍照_" + System.currentTimeMillis() / 1000 % 100000 + ".jpg", true);
            } else if (requestCode == REQ_GALLERY || requestCode == REQ_FILE) {
                Uri uri = data == null ? null : data.getData();
                if (uri == null) return;
                String name = queryName(uri);
                boolean isImg = requestCode == REQ_GALLERY
                        || (name != null && name.matches("(?i).*\\.(jpe?g|png|webp|gif|bmp)$"));
                upload(uri, name, isImg);
            }
        } catch (Exception e) {
            toast("读取附件失败：" + e.getMessage());
        }
    }

    private String queryName(Uri uri) {
        Cursor c = null;
        try {
            c = act.getContentResolver().query(uri, null, null, null, null);
            if (c != null) {
                int idx = c.getColumnIndex(MediaStore.MediaColumns.DISPLAY_NAME);
                if (idx >= 0 && c.moveToFirst() && c.getString(idx) != null) {
                    return c.getString(idx);
                }
            }
        } catch (Exception ignored) {
        } finally {
            if (c != null) c.close();
        }
        String last = uri.getLastPathSegment();
        return last == null ? "附件" : last;
    }

    // ------------------------------------------------------------------
    // 压缩 + 上传
    // ------------------------------------------------------------------
    private void upload(Uri uri, String name, boolean isImage) {
        final Item it = new Item();
        it.label = name == null ? "附件" : name;
        it.uploading = true;
        items.add(it);
        paintChips();

        new Thread(() -> {
            try {
                byte[] data;
                String mime;
                if (isImage) {
                    data = compressImage(uri);
                    mime = "image/jpeg";
                    if (it.label.matches("(?i).*\\.(jpe?g|png|webp|gif|bmp|pdf|docx?|txt)$")) {
                        // 保留原始扩展名便于后端识别
                        it.label = it.label.replaceAll("\\.[^.]+$", "") + ".jpg";
                    }
                } else {
                    data = readAll(uri);
                    mime = guessMime(it.label);
                    if (data.length > 8 * 1024 * 1024) throw new IllegalStateException("文件超过 8MB 上限");
                }
                if (data == null || data.length == 0) throw new IllegalStateException("读取内容为空");

                final byte[] payload = data;
                Api.uploadAttachment(prefs.token(), it.label, mime, payload, new Api.Cb() {
                    @Override
                    public void ok(JSONObject o) {
                        it.attId = o.optString("attachment_id", "");
                        it.uploading = false;
                        if (it.attId.isEmpty()) it.attId = null;
                        act.runOnUiThread(Attach.this::paintChips);
                    }

                    @Override
                    public void err(String msg) {
                        items.remove(it);
                        act.runOnUiThread(() -> {
                            paintChips();
                            toast("附件上传失败：" + msg);
                        });
                    }
                });
            } catch (Exception e) {
                items.remove(it);
                act.runOnUiThread(() -> {
                    paintChips();
                    toast("附件处理失败：" + e.getMessage());
                });
            }
        }).start();
    }

    /** 图片解码 → EXIF 转正 → 采样压缩 → JPEG 字节。 */
    private byte[] compressImage(Uri uri) throws Exception {
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        InputStream is = act.getContentResolver().openInputStream(uri);
        BitmapFactory.decodeStream(is, null, bounds);
        if (is != null) is.close();
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) {
            throw new IllegalStateException("无法解析图片");
        }

        int sample = 1;
        while (bounds.outWidth / (sample * 2) >= 1600 || bounds.outHeight / (sample * 2) >= 1600) {
            sample *= 2;
        }
        BitmapFactory.Options o = new BitmapFactory.Options();
        o.inSampleSize = sample;
        is = act.getContentResolver().openInputStream(uri);
        Bitmap bmp = BitmapFactory.decodeStream(is, null, o);
        if (is != null) is.close();
        if (bmp == null) throw new IllegalStateException("图片解码失败");

        // EXIF 旋转（拍照常见 90°）
        int rotate = 0;
        try {
            is = act.getContentResolver().openInputStream(uri);
            ExifInterface ex = new ExifInterface(is);
            int ori = ex.getAttributeInt(ExifInterface.TAG_ORIENTATION,
                    ExifInterface.ORIENTATION_NORMAL);
            if (ori == ExifInterface.ORIENTATION_ROTATE_90) rotate = 90;
            else if (ori == ExifInterface.ORIENTATION_ROTATE_180) rotate = 180;
            else if (ori == ExifInterface.ORIENTATION_ROTATE_270) rotate = 270;
        } catch (Exception ignored) {
        } finally {
            if (is != null) is.close();
        }
        if (rotate != 0) {
            Matrix m = new Matrix();
            m.postRotate(rotate);
            Bitmap r = Bitmap.createBitmap(bmp, 0, 0, bmp.getWidth(), bmp.getHeight(), m, true);
            if (r != bmp) bmp.recycle();
            bmp = r;
        }

        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        bmp.compress(Bitmap.CompressFormat.JPEG, 82, bos);
        bmp.recycle();
        byte[] out = bos.toByteArray();
        // 超 5MB 再压一档
        if (out.length > 5 * 1024 * 1024) {
            bos.reset();
            is = act.getContentResolver().openInputStream(uri);
            BitmapFactory.Options o2 = new BitmapFactory.Options();
            o2.inSampleSize = sample * 2;
            Bitmap b2 = BitmapFactory.decodeStream(is, null, o2);
            if (is != null) is.close();
            if (b2 != null) {
                b2.compress(Bitmap.CompressFormat.JPEG, 75, bos);
                b2.recycle();
                out = bos.toByteArray();
            }
        }
        return out;
    }

    private byte[] readAll(Uri uri) throws Exception {
        InputStream is = act.getContentResolver().openInputStream(uri);
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while (is != null && (n = is.read(buf)) > 0) bos.write(buf, 0, n);
        if (is != null) is.close();
        return bos.toByteArray();
    }

    private String guessMime(String name) {
        if (name == null) return "application/octet-stream";
        String n = name.toLowerCase();
        if (n.endsWith(".png")) return "image/png";
        if (n.endsWith(".jpg") || n.endsWith(".jpeg")) return "image/jpeg";
        if (n.endsWith(".pdf")) return "application/pdf";
        if (n.endsWith(".txt")) return "text/plain";
        if (n.endsWith(".doc")) return "application/msword";
        if (n.endsWith(".docx")) return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
        return "application/octet-stream";
    }

    // ------------------------------------------------------------------
    // chips 渲染
    // ------------------------------------------------------------------
    private void paintChips() {
        if (chipsBox == null) return;
        chipsBox.removeAllViews();
        if (items.isEmpty()) {
            chipsBox.setVisibility(View.GONE);
            return;
        }
        chipsBox.setVisibility(View.VISIBLE);
        for (final Item it : items) {
            LinearLayout chip = new LinearLayout(act);
            chip.setOrientation(LinearLayout.HORIZONTAL);
            chip.setGravity(Gravity.CENTER_VERTICAL);
            chip.setPadding(dp(10), dp(5), dp(10), dp(5));
            android.graphics.drawable.GradientDrawable bg = new android.graphics.drawable.GradientDrawable();
            bg.setCornerRadius(dp(12));
            bg.setColor(Ui.c(act, "brandSoft"));
            chip.setBackground(bg);

            TextView tv = new TextView(act);
            tv.setTextSize(12);
            tv.setTextColor(Ui.c(act, "brandDark"));
            tv.setText(it.uploading ? "⏳ 上传中…" : "📄 " + it.label);
            tv.setMaxWidth(dp(140));
            tv.setSingleLine(true);
            chip.addView(tv);

            if (!it.uploading) {
                TextView x = new TextView(act);
                x.setText("  ×");
                x.setTextSize(14);
                x.setTextColor(Ui.c(act, "brandDark"));
                x.setOnClickListener(v -> {
                    items.remove(it);
                    paintChips();
                });
                chip.addView(x);
            }

            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
            lp.setMargins(0, 0, dp(8), 0);
            chipsBox.addView(chip, lp);
        }
    }

    private void toast(String s) {
        Toast.makeText(act, s, Toast.LENGTH_SHORT).show();
    }

    private int dp(int v) {
        return Math.round(v * act.getResources().getDisplayMetrics().density);
    }
}
