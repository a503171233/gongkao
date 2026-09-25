#!/usr/bin/env python3
"""批次12a：存量粉笔题解析图重采回填。

背景：批次11b 修复了采集器解析图下载（fenbi.py to_item 对 solution 走 _download_imgs），
但存量 447 题在修复前采集，analysis 无解析配图；且旧数据未存粉笔题 id（ext_id 为空），
无法直接按 id 回查 solutions 接口。

本脚本按「卷名匹配 + 归一化题干对齐」重建存量题与粉笔源的映射：
  1. question_bank 按 source 卷名分组存量粉笔题（ext_id 为空 或 analysis 含 [图 占位）
  2. 粉笔 list_papers 按卷名匹配 → fetch_paper → solutions 原始题（含 id/solution）
  3. 题干归一化对齐（剥离图片标记后比对），写入 ext_id；解析重走 to_item 图片链路回填
  4. 更新 analysis（含 ![图N](/api/qimg/…) 标记）+ images 追加解析图 URL

用法（backend 容器内，需有效粉笔 Cookie）：
  python scripts/rescrape_analysis.py            # dry-run：仅对齐统计，不写库
  python scripts/rescrape_analysis.py --apply    # 实际回填
  python scripts/rescrape_analysis.py --limit 3  # 只处理前 3 卷（分批跑）
"""
from __future__ import annotations

import re
import sys
import time
import unicodedata

sys.path.insert(0, "/app")

from poc import fenbi  # noqa: E402
from poc.study import StudyStore, _norm_question  # noqa: E402

_IMG_MARK = re.compile(r"!\[图\d*\]\([^)]*\)|\[图\d*\]")


def _core(text: str) -> str:
    """题干对齐键：归一化 + 剥离图片标记 + NFKC（全半角）。"""
    t = _IMG_MARK.sub("", text or "")
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"\s+", "", t)
    return _norm_question(t)


def _imgs_of(row) -> list[str]:
    try:
        import json
        v = json.loads(row["images"] or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


def main() -> int:
    apply_mode = "--apply" in sys.argv
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    cfg = fenbi.fenbi_config()
    cookie = cfg.get("cookie") or ""
    if not cookie:
        print("✗ 粉笔 Cookie 缺失（管理端「粉笔题库直采」粘贴 document.cookie 后重试）")
        return 1
    interval = float(cfg.get("min_interval_secs") or 2)

    st = StudyStore()
    with st._connect() as conn:
        rows = conn.execute(
            "SELECT id, question, analysis, images, source FROM question_bank "
            "WHERE source_type='公开题库' AND qtype='choice'"
        ).fetchall()
    by_paper: dict[str, list] = {}
    for r in rows:
        by_paper.setdefault(r["source"] or "", []).append(r)

    papers = fenbi.list_papers(cookie, page_len=50, cursor=0)
    plist = papers.get("papers") if isinstance(papers, dict) else papers
    name_map = {}
    for p in plist or []:
        if isinstance(p, dict) and p.get("name"):
            name_map[str(p["name"])[:80]] = p

    total_aligned = total_gap = total_updated = 0
    papers_done = 0
    for src, items in sorted(by_paper.items()):
        if limit and papers_done >= limit:
            break
        paper = name_map.get(src)
        if not paper:
            print(f"⚠ 卷名未匹配（跳过）: {src[:50]}")
            total_gap += len(items)
            continue
        key = paper.get("key") or paper.get("exerciseKey") or paper.get("id")
        try:
            raws = fenbi.fetch_paper(cookie, key, interval=interval)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ 拉卷失败 {src[:40]}: {type(e).__name__} {str(e)[:60]}")
            total_gap += len(items)
            continue
        # 粉笔侧对齐键表
        remote = {}
        for raw in raws or []:
            if isinstance(raw, dict) and raw.get("content"):
                remote[_core(fenbi._strip_html(str(raw["content"])))] = raw
        aligned = 0
        for it in items:
            k = _core(it["question"])
            raw = remote.get(k)
            if not raw:
                total_gap += 1
                continue
            aligned += 1
            ext_id = f"fenbi:{raw.get('id') or ''}".rstrip(":")
            # 解析重走图片链路：下载解析图 → ![图N] 标记 → strip html
            raw_sol = str(raw.get("solution") or "")
            new_sol_html, imgs_a = fenbi._download_imgs(raw_sol, cfg.get("teacher_id") or "T001")
            new_analysis = fenbi._strip_html(new_sol_html)
            old_imgs = _imgs_of(it)
            merged = old_imgs + [u for u in imgs_a if u not in old_imgs]
            # 判定是否需要更新：ext_id 缺失 或 解析图有新增
            need_ext = not it["ext_id"] if "ext_id" in it.keys() else True
            import json as _json
            imgs_changed = merged != old_imgs
            if apply_mode and (need_ext or imgs_changed):
                with st._connect() as conn:
                    conn.execute(
                        "UPDATE question_bank SET ext_id=?, analysis=?, images=? WHERE id=?",
                        (ext_id, new_analysis, _json.dumps(merged, ensure_ascii=False), it["id"]),
                    )
                total_updated += 1
        total_aligned += aligned
        papers_done += 1
        print(f"{'✓' if aligned == len(items) else '○'} {src[:44]} 对齐 {aligned}/{len(items)}")
        time.sleep(interval)

    print(f"\n===== 汇总（apply={apply_mode}）=====")
    print(f"卷处理: {papers_done} | 对齐: {total_aligned} | 未匹配: {total_gap} | 回填: {total_updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
