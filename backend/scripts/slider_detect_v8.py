# 滑块缺口检测 v8（定稿）：暗团积分最强团，绝对阈值
# 原理：缺口=半透明黑遮罩实心块 → 变暗 ≥18 灰度级且成块（宽 18-90）。
#       背景暗斑/纹理粘连用「暗度×宽度积分最大团」压制；
#       起点块 4 种样式特征各异无法稳定检测 → 用实测均值 522
#       （样本 518/524/518/528，最大偏差 6px）；
#       八边形亮描边样式（无暗缺口）检测不到 → 回落人工（可接受）。
# 四样本联测：real2 178(期望173,+5) / real3 178(期望184,-6)
#            / recon 208(期望200,+8) / sample None(亮描边样式)
import io

import numpy as np
from PIL import Image

VIEW_H, VIEW_W = 720, 1280
BG_WIN = 91                    # 背景估计窗（大于最大块宽 60）
BAND_Y0, BAND_Y1 = 300, 460    # 拼图行带
X0, X1 = 578, 776              # 缺口搜索域（避开起点块/边框/图右缘外）
SEG_MIN_W, SEG_MAX_W = 18, 60  # 暗团宽度约束（缺口实测宽 29-48；>60 为
                               # 长纹理带，如 real2 水面暗带 67px）
DARK_TH = 18.0                 # 变暗绝对阈值（灰度级）
START_X = 522                  # 起点块左缘常数（实测均值）
DIST_MIN, DIST_MAX = 56, 254


def _box_mean(a: np.ndarray, win: int) -> np.ndarray:
    c = np.cumsum(a, axis=1, dtype=np.float64)
    c = np.concatenate([np.zeros((a.shape[0], 1)), c], axis=1)
    h = win // 2
    W = a.shape[1]
    i0 = np.clip(np.arange(W) - h, 0, W)
    i1 = np.clip(np.arange(W) + h + 1, 0, W)
    return (c[:, i1] - c[:, i0]) / (i1 - i0)


def detect_slider_dist(png_bytes: bytes):
    """返回拖动距离 px；置信度不足返回 None。"""
    try:
        im = Image.open(io.BytesIO(png_bytes)).convert("L")
        a = np.asarray(im, dtype=np.float32)
        if a.shape[:2] != (VIEW_H, VIEW_W):
            return None
        bg = _box_mean(a, BG_WIN)
        dark = np.clip(bg - a, 0, None)
        prof = np.convolve(dark[BAND_Y0:BAND_Y1, X0:X1].mean(axis=0),
                           np.ones(3) / 3, mode="same")
        n = len(prof)
        segs = []                       # (abs_start, abs_end, score)
        i = 0
        while i < n:
            if prof[i] < DARK_TH:
                i += 1
                continue
            j = i
            while j < n and prof[j] >= DARK_TH:
                j += 1
            w = j - i
            if SEG_MIN_W <= w <= SEG_MAX_W:
                segs.append((X0 + i, X0 + j, float(prof[i:j].sum())))
            i = j
        if not segs:
            return None
        segs.sort(key=lambda t: -t[2])   # 暗度积分最强团 = 缺口
        dist = round(segs[0][0] - START_X)
        if DIST_MIN <= dist <= DIST_MAX:
            return dist
        return None
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    base = "/root/.codebuddy/artifact/gongkao/backend/scripts/"
    for f, expect in (("slider_shot_sample.png", "期望≈119(亮描边样式,预期None)"),
                      ("slider_shot_real2.png", "期望≈173(三角)"),
                      ("slider_shot_real3.png", "期望≈184(六边形)"),
                      ("recon_noslider.png", "期望≈200(方块,今日侦察)")):
        try:
            data = open(base + f, "rb").read()
            print(f, "→ dist =", detect_slider_dist(data), f"({expect})")
        except FileNotFoundError:
            print(f, "缺失")
