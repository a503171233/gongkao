# -*- coding: utf-8 -*-
"""批次4：搜索多源降级链 + 入库语义查重。

运行：cd backend && python tests/test_search_dedup.py
覆盖：
  1) search._providers 解析：单值 / 逗号链 / none / 空 / 未知值 / 带空白；
  2) web_search 多源降级：首源成功直返；首源失败降级第二源；全败汇总 warning；
     单值行为与历史版本一致；
  3) search_available 与 none 语义；
  4) store.find_near_duplicates：空库 / 空输入 / 相似命中 / 相异不命中 /
     threshold 参数与 DEDUP_THRESHOLD env；
  5) api._save_and_ingest 端到端：首传 duplicates=[]，重传同文件全命中；
     DEDUP_MODE=skip 时跳过命中块（stored < chunks）。
embedding 走 selftest 离线模式（零网络零密钥，同文本向量一致）。
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk_search_dedup_")
os.environ["DATA_DIR"] = str(Path(TMP) / "data")
os.environ["DB_PATH"] = str(Path(TMP) / "vector")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["CHAT_DB"] = str(Path(TMP) / "chat.db")

ok_count = 0
fail_count = 0


def check(name, cond, detail=""):
    global ok_count, fail_count
    if cond:
        print(f"  ✅ {name}")
        ok_count += 1
    else:
        print(f"  ❌ {name}" + (f"  | {detail}" if detail else ""))
        fail_count += 1


from poc import search  # noqa: E402

# ==================== 1) _providers 解析 ====================
def _with_env(key, val, fn):
    old = os.environ.get(key)
    os.environ[key] = val
    try:
        return fn()
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


check("单值 bing", _with_env("SEARCH_PROVIDER", "bing", search._providers) == ["bing"])
check("单值 bocha", _with_env("SEARCH_PROVIDER", "bocha", search._providers) == ["bocha"])
check("逗号链 bing,bocha 保序", _with_env("SEARCH_PROVIDER", "bing,bocha", search._providers) == ["bing", "bocha"])
check("逗号链带空白", _with_env("SEARCH_PROVIDER", " bocha , bing ", search._providers) == ["bocha", "bing"])
check("none → 空列表", _with_env("SEARCH_PROVIDER", "none", search._providers) == [])
check("空 → 空列表", _with_env("SEARCH_PROVIDER", "", search._providers) == [])
check("未知值按 bing", _with_env("SEARCH_PROVIDER", "google", search._providers) == ["bing"])
check("未设置默认 bing", (lambda: (os.environ.pop("SEARCH_PROVIDER", None), search._providers())[1])() == ["bing"])

# ==================== 2) web_search 多源降级 ====================
_calls = []


def _mk_stub(tag, ok, results):
    def _fn(q, n):
        _calls.append(tag)
        if ok:
            return results
        raise RuntimeError(f"{tag} 网络超时")
    return _fn


_res_bing = [{"title": "bing结果", "url": "https://b.example/1", "snippet": "s1"}]
_res_bocha = [{"title": "bocha结果", "url": "https://c.example/1", "snippet": "s2"}]

# 首源成功：直返，不调第二源
_calls.clear()
os.environ["SEARCH_PROVIDER"] = "bing,bocha"
orig_bing, orig_bocha = search._search_bing, search._search_bocha
search._search_bing = _mk_stub("bing", True, _res_bing)
search._search_bocha = _mk_stub("bocha", True, _res_bocha)
r, w = search.web_search("行测技巧")
check("首源成功直返", r == _res_bing and w == "" and _calls == ["bing"], (r, w, _calls))

# 首源失败 → 降级第二源成功
_calls.clear()
search._search_bing = _mk_stub("bing", False, [])
r, w = search.web_search("行测技巧")
check("首源失败降级第二源", r == _res_bocha and w == "" and _calls == ["bing", "bocha"], (r, w, _calls))

# 全败 → 汇总 warning（含各源原因）
_calls.clear()
search._search_bocha = _mk_stub("bocha", False, [])
r, w = search.web_search("行测技巧")
check("全败汇总 warning", r == [] and "bing" in w and "bocha" in w and "RuntimeError" in w, (r, w))

# 单值 bocha（历史行为）：只调 bocha
_calls.clear()
os.environ["SEARCH_PROVIDER"] = "bocha"
search._search_bocha = _mk_stub("bocha", True, _res_bocha)
r, w = search.web_search("申论范文")
check("单值 bocha 行为不变", r == _res_bocha and w == "" and _calls == ["bocha"], (r, w, _calls))

search._search_bing, search._search_bocha = orig_bing, orig_bocha

# ==================== 3) search_available ====================
check("none 不可用", _with_env("SEARCH_PROVIDER", "none", search.search_available) is False)
check("链式可用", _with_env("SEARCH_PROVIDER", "bing,bocha", search.search_available) is True)

# ==================== 4) find_near_duplicates（store 层） ====================
os.environ.pop("SEARCH_PROVIDER", None)
from poc import store  # noqa: E402
from poc.ingest import Chunk  # noqa: E402

TID = "T-dedup"
text_a = "行测言语理解：主旨概括题的解题步骤是先找关联词，再看行文脉络，最后对比选项排除干扰项。"
text_b = "申论写作技巧：议论文开头要亮明总论点，中间段落分层论证，结尾回扣主题升华立意。"


def _mk_chunk(doc, idx, content):
    return Chunk(content=content, doc_name=doc, idx=idx)


# 空库 → []
check("空库返回空", store.find_near_duplicates([_mk_chunk("新文档.md", 0, text_a)], TID) == [])

# 入库 1 个文档 1 块
store.upsert_chunks([_mk_chunk("课件A.md", 0, text_a)], TID)

# 相同内容（不同文档名）→ 命中 sim≈1.0
dups = store.find_near_duplicates([_mk_chunk("课件A.md", 0, text_a)], TID)
check("同文本命中近重复", len(dups) == 1 and dups[0]["similarity"] >= 0.93
      and dups[0]["existing_doc"] == "课件A.md" and dups[0]["doc_name"] == "课件A.md", dups)

# 相异文本 → 不命中（selftest 哈希向量对不相关文本相似度低）
dups = store.find_near_duplicates([_mk_chunk("课件B.md", 0, text_b)], TID, threshold=0.99)
check("相异文本高阈值不命中", dups == [], dups)

# threshold 参数生效：同文本在极高阈值下不命中
dups = store.find_near_duplicates([_mk_chunk("课件A.md", 1, text_a)], TID, threshold=1.01)
check("阈值参数生效", dups == [], dups)

# DEDUP_THRESHOLD env 生效（0.5 宽松阈值：同文本仍命中）
dups = _with_env("DEDUP_THRESHOLD", "0.5",
                 lambda: store.find_near_duplicates([_mk_chunk("课件A.md", 2, text_a)], TID))
check("DEDUP_THRESHOLD env 生效", len(dups) == 1 and dups[0]["similarity"] >= 0.5, dups)

# 空输入 → []
check("空 chunks 返回空", store.find_near_duplicates([], TID) == [])

# ==================== 5) _save_and_ingest 端到端 ====================
os.environ["DEDUP_THRESHOLD"] = "0.93"
os.environ.pop("DEDUP_MODE", None)
import poc.api as api  # noqa: E402
from poc.config import Config  # noqa: E402

cfg = Config()
tcfg = cfg.get_teacher("T001")
_doc = ("行测主旨概括专题讲义。" * 3 + "\n\n"
        "1、下列选项中，最能概括上文主旨的是（　）。\nA. 关联词定主旨\nB. 逐字翻译\n"
        "C. 只看首句\nD. 忽略行文\n答案：A\n\n"
        "申论概括题作答要求：全面、准确、有条理，不超过300字。\n")

r1 = api._save_and_ingest(_doc.encode("utf-8"), "讲义v1.md", "T001", tcfg)
check("首传无 duplicates", r1["duplicates"] == [] and r1["stored"] == r1["chunks"], r1)

r2 = api._save_and_ingest(_doc.encode("utf-8"), "讲义v2.md", "T001", tcfg)
check("重传同内容全命中", len(r2["duplicates"]) == r2["chunks"] and r2["chunks"] > 0
      and all(d["similarity"] >= 0.93 for d in r2["duplicates"]), r2)
check("重传命中块 existing_doc 指向首传", all(d["existing_doc"] == "讲义v1.md" for d in r2["duplicates"]),
      [d["existing_doc"] for d in r2["duplicates"][:3]])

# DEDUP_MODE=skip：命中块跳过入库（检测到 duplicates，但入库 0 块）
os.environ["DEDUP_MODE"] = "skip"
r3 = api._save_and_ingest(_doc.encode("utf-8"), "讲义v3.md", "T001", tcfg)
check("skip 模式重复块跳过入库", r3["stored"] == 0 and r3["chunks"] == 0
      and len(r3["duplicates"]) == 1, r3)
os.environ.pop("DEDUP_MODE", None)

print(f"\n总计: 通过 {ok_count} · 失败 {fail_count}")
sys.exit(0 if fail_count == 0 else 1)
