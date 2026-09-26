#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""粉笔整卷 PDF 导出（web 端下载接口逆向，2026-09-26 破解）。

接口公式（web 端答题页「下载」按钮，chunk-232 构造 / chunk-239 消费）：
    https://urlimg.fenbi.com/api/pdf/tiku/combine/exercise/{exercise_key}
        ?routecs=xingce&combineType={1|2}

要点：
- exercise_key 直接用 papers 列表返回的 key（形如 1_1_3sdngv2），无需先开练习；
- 纯 HTTP + 登录 Cookie 即可，与 getExercise（tiku API）不同服务，
  4s 间隔实测 6/6 成功零风控；
- combineType=1 紧凑题目卷（材料内嵌），=2 纸质排版卷（独立材料页+答题纸）；
- 内容边界：仅题干/选项/材料/图形，**不含答案与解析**（全 JS 仅此一个 PDF 接口）；
- 覆盖边界：仅限列表带 exercise.key 的免费卷，付费卷无 key 不可构造。

用法（容器内）：
    docker cp scripts/fenbi_pdf_export.py gongkao-backend:/tmp/pdf.py
    docker exec -w /app gongkao-backend python3 /tmp/pdf.py                # 全部免费卷
    docker exec -w /app gongkao-backend python3 /tmp/pdf.py --ct 2         # 纸质排版版
    docker exec -w /app gongkao-backend python3 /tmp/pdf.py --key 1_1_3ri4sfl
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests  # noqa: E402

from poc import fenbi  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
PDF_BASE = "https://urlimg.fenbi.com/api/pdf/tiku/combine/exercise/{key}"


def export_all(out_dir: str, combine_type: int = 1, interval: float = 4.0) -> int:
    _rt = fenbi._load_rt().get(fenbi._CFG_KEY) or {}
    cookie = str(_rt.get("cookie") or "")
    if not cookie:
        print("✗ 粉笔 Cookie 缺失（先经管理端或 fenbi_login.py 落 Cookie）")
        return 1
    hdr = {"Cookie": cookie, "User-Agent": UA, "Referer": "https://spa.fenbi.com/"}

    papers = fenbi.list_papers(cookie, page_len=50, cursor=0)
    print(f"papers 总数: {len(papers)}")
    os.makedirs(out_dir, exist_ok=True)
    ok = fail = 0
    for p in papers:
        name = p["name"][:60].replace("/", "_")
        path = os.path.join(out_dir, f"{name}.pdf")
        url = PDF_BASE.format(key=p["exercise_key"]) + \
            f"?routecs=xingce&combineType={combine_type}"
        try:
            r = requests.get(url, headers=hdr, timeout=90)
            is_pdf = r.status_code == 200 and "pdf" in r.headers.get(
                "content-type", "").lower()
            if is_pdf:
                with open(path, "wb") as f:
                    f.write(r.content)
                ok += 1
            else:
                fail += 1
            print(f"[{'OK' if is_pdf else 'FAIL'}] {r.status_code} "
                  f"{len(r.content)}B  {p['name'][:40]}")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"[ERR] {p['name'][:40]}: {str(e)[:80]}")
        time.sleep(interval)
    print(f"\n完成：成功 {ok} / 失败 {fail} → {out_dir}")
    return 0 if fail == 0 else 2


def export_one(key: str, out_path: str, combine_type: int = 1) -> int:
    _rt = fenbi._load_rt().get(fenbi._CFG_KEY) or {}
    cookie = str(_rt.get("cookie") or "")
    hdr = {"Cookie": cookie, "User-Agent": UA, "Referer": "https://spa.fenbi.com/"}
    url = PDF_BASE.format(key=key) + f"?routecs=xingce&combineType={combine_type}"
    r = requests.get(url, headers=hdr, timeout=90)
    if r.status_code == 200 and "pdf" in r.headers.get("content-type", "").lower():
        with open(out_path, "wb") as f:
            f.write(r.content)
        print(f"OK {len(r.content)}B → {out_path}")
        return 0
    print(f"FAIL {r.status_code} {r.headers.get('content-type', '')}")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="粉笔整卷 PDF 导出")
    ap.add_argument("--out", default="/data/pdf_exports", help="输出目录")
    ap.add_argument("--ct", type=int, default=1, choices=[1, 2],
                    help="combineType：1 紧凑卷 / 2 纸质排版卷")
    ap.add_argument("--key", default=None, help="单个 exercise_key 导出")
    ap.add_argument("--path", default=None, help="单卷输出文件路径")
    ap.add_argument("--interval", type=float, default=4.0, help="卷间秒数")
    args = ap.parse_args()
    if args.key:
        sys.exit(export_one(args.key, args.path or f"{args.out}/{args.key}.pdf",
                            args.ct))
    sys.exit(export_all(args.out, args.ct, args.interval))
