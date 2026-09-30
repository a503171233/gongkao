package com.gongkao.app;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;

/**
 * 统一 Activity 基类（批次G）：
 * - onCreate：按当前主题同步状态栏（Ui.systemBars）+ 进入转场动画
 * - 返回键 / finish()：退出转场动画
 * 所有功能页 extends BaseActivity；MainActivity 主容器保持原生 Activity（无需转场）。
 */
public abstract class BaseActivity extends Activity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Ui.systemBars(this);
        Ui.transition(this, true);
    }

    @Override
    public void onBackPressed() {
        super.onBackPressed();
        Ui.transition(this, false);
    }

    @Override
    public void finish() {
        super.finish();
        Ui.transition(this, false);
    }

    /** 供子类快捷取语义色。 */
    protected int uc(String key) {
        return Ui.c(this, key);
    }

    /** 供子类快捷取 dp。 */
    protected int udp(int v) {
        return Ui.dp(this, v);
    }

    /** 未使用的 View 抑制警告占位（保持基类简洁）。 */
    @SuppressWarnings("unused")
    private static void touch(View v) { }
}
