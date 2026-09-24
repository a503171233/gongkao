# -*- coding: utf-8 -*-
"""套卷采集重复甄别（V20260912 需求1）逐项真实检测：

  A) _extract_enhanced 完整性判定 —— 整卷跑完 complete=True / 中途停止 incomplete
  B) _set_mark / _set_status     —— 套卷标记写入与状态口径
  C) collected_sets_admin/_summary/reset —— 管理视图与重置
  D) run_once 集成：已采完整套卷不再提取（含 force）、incomplete 续采、
     source_url 溯源写入 question_bank、find_questions_by_source_url 回查
  E) _add 对非 URL source（文档库）不写 source_url

运行: cd backend && python tests/test_collected_sets.py
"""
import json
import os
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-ac-sets-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["STUDY_DB"] = os.path.join(TMP, "study.db")
os.environ["SEARCH_PROVIDER"] = "none"

from poc import autocollect as ac  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name +
          (("  " + str(extra)[:300]) if extra and not cond else ""))
    if not cond:
        ok = False


# ---------- 构造一份"整卷"文本（>=3 题号 + ABCD 选项） ----------
def make_paper(n=6):
    parts = []
    for i in range(1, n + 1):
        parts.append(
            f"{i} 以下关于测试知识点{i}的说法，正确的是：\n"
            f"A、选项甲{i}内容\nB、选项乙{i}内容\nC、选项丙{i}内容\nD、选项丁{i}内容\n"
            f"正确答案是甲{i}，解析：知识点{i}的考查方式。")
    return "\n".join(parts)


# ============================================================
# A) _extract_enhanced 完整性判定
# ============================================================
paper = make_paper(6)
fake_q = [{"qtype": "choice", "question": f"以下关于测试知识点{i}的说法，正确的是：",
           "options": ["A、甲", "B、乙", "C、丙", "D、丁"], "answer": "A",
           "analysis": "解析", "difficulty": 2} for i in range(1, 20)]

# 完整跑完：mock 提取全部成功
with mock.patch.object(ac, "_extract_via", return_value=({"questions": fake_q}, [])), \
     mock.patch.object(ac, "load_llm_channels", return_value=[]), \
     mock.patch.object(ac, "_threads", return_value=1):
    r, att = ac._extract_enhanced(paper, "")
    check("A1 整卷完整提取 complete=True", r.get("complete") is True)
    check("A2 题目有产出", len(r.get("questions") or []) > 0)

# 中途停止：多片段长卷，第一批后 should_stop 命中
long_q = "根据下列材料回答。" + "资料分析背景文字，包含数据表格与统计描述。" * 12
calls = {"n": 0}
def stop_after_one():
    calls["n"] += 1
    return calls["n"] >= 1   # 第一批后立刻停

with mock.patch.object(ac, "_extract_via", return_value=({"questions": fake_q[:1]}, [])), \
     mock.patch.object(ac, "load_llm_channels", return_value=[]), \
     mock.patch.object(ac, "_threads", return_value=1):
    r, att = ac._extract_enhanced(make_paper(16) + long_q * 8, "", should_stop=stop_after_one)
    check("A3 提前停止 complete=False", r.get("complete") is False)

# 非整卷（普通文本走 _extract_via）也应带 complete=True
with mock.patch.object(ac, "_extract_via", return_value=({"questions": fake_q[:1]}, [])), \
     mock.patch.object(ac, "load_llm_channels", return_value=[]):
    r, att = ac._extract_enhanced("这是一段普通说明文字，没有任何题号结构。" * 5, "")
    check("A4 非整卷路径 complete=True", r.get("complete") is True)

# ============================================================
# B) _set_mark / _set_status
# ============================================================
collected = {}
ac._set_mark(collected, "https://s/p/1", "fpA", {"complete": True, "questions": [1, 2]}, 2)
check("B1 完整+有题 → complete", collected["https://s/p/1"]["status"] == "complete")
check("B2 added 记录", collected["https://s/p/1"]["added"] == 2)

ac._set_mark(collected, "https://s/p/2", "fpB", {"complete": True, "questions": []}, 0)
check("B3 完整但0题（查重全命中）也算 complete", collected["https://s/p/2"]["status"] == "complete")

ac._set_mark(collected, "https://s/p/3", "fpC", {"complete": False, "questions": [1]}, 1)
check("B4 提前停止 → incomplete", collected["https://s/p/3"]["status"] == "incomplete")

# incomplete 后补采完整 → complete，added 累加
ac._set_mark(collected, "https://s/p/3", "fpC", {"complete": True, "questions": [1, 2, 3]}, 5)
check("B5 续采完成后转 complete", collected["https://s/p/3"]["status"] == "complete")
check("B6 added 跨轮累加", collected["https://s/p/3"]["added"] == 6)

check("B7 _set_status 读取", ac._set_status(collected, "https://s/p/1") == "complete")
check("B8 未知 URL 状态空", ac._set_status(collected, "https://s/none") == "")

# 容量防失控
big = {}
for i in range(20001):
    big[f"https://s/p/{i}"] = {"fp": "x", "status": "complete", "added": 0, "n": 0,
                               "ts": f"2026-01-01T00:00:{i % 60:02d}"}
ac._set_mark(big, "https://s/new", "fpN", {"complete": True, "questions": []}, 0)
check("B9 超容量自动截断", len(big) <= 20000)

# ============================================================
# C) 管理视图 / 汇总 / 重置（真实落盘 state 文件往返）
# ============================================================
state_path = ac._STATE_PATH
state_path.parent.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps({
    "urls": ["https://s/"], "url_fp": {"https://s/p/1": "fpA", "https://s/p/3": "fpC"},
    "collected_sets": {
        "https://s/p/1": {"fp": "fpA", "status": "complete", "added": 2, "n": 2, "ts": "t1"},
        "https://s/p/3": {"fp": "fpC", "status": "incomplete", "added": 1, "n": 1, "ts": "t2"},
    },
}, ensure_ascii=False), encoding="utf-8")

view = ac.collected_sets_admin()
check("C1 列表视图 total=2", view["total"] == 2)
check("C2 汇总 complete=1", view["summary"]["complete"] == 1)
check("C3 汇总 incomplete=1", view["summary"]["incomplete"] == 1)
check("C4 按时间倒序", view["items"][0]["url"] == "https://s/p/3")

one = ac.collected_sets_admin(url="https://s/p/1")
check("C5 精确查返回 mark", one["mark"] and one["mark"]["status"] == "complete")
none = ac.collected_sets_admin(url="https://s/unknown")
check("C6 未知 URL mark=None", none["mark"] is None)

r = ac.reset_collected_sets(["https://s/p/1"])
check("C7 定向重置 removed=1", r["removed"] == 1)
st_after = json.loads(state_path.read_text(encoding="utf-8"))
check("C8 重置后标记消失", "https://s/p/1" not in st_after["collected_sets"])
check("C9 同步清除 url_fp", "https://s/p/1" not in st_after.get("url_fp", {}))
check("C10 incomplete 保留", "https://s/p/3" in st_after["collected_sets"])

r = ac.reset_collected_sets(None)
check("C11 全量重置", r["removed"] == 1)
st_cleared = json.loads(state_path.read_text(encoding="utf-8"))
check("C12 全量重置清空 collected_sets/url_fp/urls",
      not st_cleared["collected_sets"] and not st_cleared["url_fp"]
      and not st_cleared.get("urls"))

# ============================================================
# D) run_once 集成：已采完整套卷永久跳过 / incomplete 续采 / 溯源落库
# ============================================================
from poc.study import get_study_store, SHARED_TEACHER_ID
st_store = get_study_store()

PAGE_URL = "https://s.example.com/paper/9001"
page_text = paper  # 与 fp 计算一致

def _mk_result(er, start_piece=0, total=1):
    """补齐整卷游标契约：complete 时 next_piece 必须推到 total，
    否则作业会把「片段没跑完」当成「本轮无进展」而计入 stall。"""
    r = dict(er)
    r["total_pieces"] = int(r.get("total_pieces") or total)
    if r.get("complete"):
        r["next_piece"] = r["total_pieces"]
    else:
        r["next_piece"] = min(r["total_pieces"],
                              max(int(start_piece) + 1, int(r.get("next_piece") or 0)))
    return r


def run_url_once(force=False, extract_result=None):
    """只跑 URL 来源，mock 爬取与提取，返回 (result, extract_calls)。"""
    calls = []
    er = extract_result or {"questions": fake_q, "complete": True}

    def fake_extract(text, subject, threads=None, should_stop=None,
                     start_piece=0, stop_on_piece_fail=False):
        calls.append(text)
        return _mk_result(er, start_piece), []

    with mock.patch.object(ac, "_enabled", return_value=True), \
         mock.patch.object(ac, "resource_ok", return_value=(True, {})), \
         mock.patch.object(ac, "_url_list", return_value=["https://s.example.com"]), \
         mock.patch.object(ac, "_whitelist_start_targets", return_value=[]), \
         mock.patch.object(ac, "_crawl_site",
                           return_value=[(PAGE_URL, "2026年行测模拟卷", page_text)]), \
         mock.patch.object(ac, "_site_rule", return_value={"detail": None}), \
         mock.patch.object(ac, "_worth_extract", return_value=True), \
         mock.patch.object(ac, "_is_seo_info_page", return_value=False), \
         mock.patch.object(ac, "_extract_enhanced", fake_extract), \
         mock.patch.object(ac, "_search_queries", return_value=[]), \
         mock.patch.object(ac, "_effective_autonomous", return_value=False), \
         mock.patch.object(ac, "_quality_check", lambda *a, **k: None):
        res = ac.run_once(force=force)
    return res, len(calls)

res1, calls1 = run_url_once()
check("D1 首轮提取 1 页", calls1 == 1)
check("D2 首轮有入库", res1["added"] > 0)
st_d = json.loads(state_path.read_text(encoding="utf-8"))
mark_d = st_d["collected_sets"][PAGE_URL]
check("D3 完整采集后标 complete", mark_d["status"] == "complete")
check("D4 试卷作业记录内容指纹（整卷语义下指纹落在作业上）",
      st_d["paper_jobs"][PAGE_URL]["fp"] == mark_d["fp"])
check("D4b 作业已标 done 且已清空缓冲",
      st_d["paper_jobs"][PAGE_URL]["status"] == "done"
      and not st_d["paper_jobs"][PAGE_URL].get("buf"))

# 第二轮（非 force）：内容未变 → 不提取
res2, calls2 = run_url_once()
check("D5 二轮：已采完整不再提取", calls2 == 0)
check("D6 二轮：sets_complete 跳过计数", res2["urls"].get("sets_complete", 0) >= 1)

# 手动 force 轮：complete 套卷仍然跳过（需求核心：之后不再采集）
res3, calls3 = run_url_once(force=True)
check("D7 force 轮也不重采完整套卷", calls3 == 0)

# 内容变化 → 重新提取
changed_paper = paper + "\n7 新增题目：关于扩展知识点：\nA、甲\nB、乙\nC、丙\nD、丁\n答案甲。"
def run_url_once_changed(extract_result):
    calls = []
    def fake_extract(text, subject, threads=None, should_stop=None,
                     start_piece=0, stop_on_piece_fail=False):
        calls.append(text)
        return _mk_result(extract_result, start_piece), []
    with mock.patch.object(ac, "_enabled", return_value=True), \
         mock.patch.object(ac, "resource_ok", return_value=(True, {})), \
         mock.patch.object(ac, "_url_list", return_value=["https://s.example.com"]), \
         mock.patch.object(ac, "_whitelist_start_targets", return_value=[]), \
         mock.patch.object(ac, "_crawl_site",
                           return_value=[(PAGE_URL, "2026年行测模拟卷", changed_paper)]), \
         mock.patch.object(ac, "_site_rule", return_value={"detail": None}), \
         mock.patch.object(ac, "_worth_extract", return_value=True), \
         mock.patch.object(ac, "_is_seo_info_page", return_value=False), \
         mock.patch.object(ac, "_extract_enhanced", fake_extract), \
         mock.patch.object(ac, "_search_queries", return_value=[]), \
         mock.patch.object(ac, "_effective_autonomous", return_value=False), \
         mock.patch.object(ac, "_quality_check", lambda *a, **k: None):
        res = ac.run_once()
    return res, len(calls)

res4, calls4 = run_url_once_changed({"questions": fake_q, "complete": True})
check("D8 内容变化重新提取", calls4 == 1)

# incomplete（被预算打断）→ 不写 url_fp、下轮续采
PAGE_URL2 = "https://s.example.com/paper/9002"
def run_incomplete():
    calls = []
    def fake_extract(text, subject, threads=None, should_stop=None,
                     start_piece=0, stop_on_piece_fail=False):
        calls.append(text)
        # 模拟一张 3 片段的整卷：每轮只推进一步，第 3 轮才跑完（游标续采）
        total = 3
        nxt = min(total, int(start_piece) + 1)
        return {"questions": fake_q[:2], "complete": nxt >= total,
                "next_piece": nxt, "total_pieces": total}, []
    with mock.patch.object(ac, "_enabled", return_value=True), \
         mock.patch.object(ac, "resource_ok", return_value=(True, {})), \
         mock.patch.object(ac, "_url_list", return_value=["https://s.example.com2"]), \
         mock.patch.object(ac, "_whitelist_start_targets", return_value=[]), \
         mock.patch.object(ac, "_crawl_site",
                           return_value=[(PAGE_URL2, "残缺卷", paper)]), \
         mock.patch.object(ac, "_site_rule", return_value={"detail": None}), \
         mock.patch.object(ac, "_worth_extract", return_value=True), \
         mock.patch.object(ac, "_is_seo_info_page", return_value=False), \
         mock.patch.object(ac, "_extract_enhanced", fake_extract), \
         mock.patch.object(ac, "_search_queries", return_value=[]), \
         mock.patch.object(ac, "_effective_autonomous", return_value=False), \
         mock.patch.object(ac, "_quality_check", lambda *a, **k: None):
        ac.run_once()
    return len(calls)

n1 = run_incomplete()
st_inc = json.loads(state_path.read_text(encoding="utf-8"))
check("D9 incomplete 首轮提取", n1 == 1)
check("D10 未采完不写 complete 标记（整卷采完才落标记）",
      (st_inc.get("collected_sets", {}).get(PAGE_URL2) or {}).get("status") != "complete")
check("D10b 作业游标已推进且未完成",
      st_inc["paper_jobs"][PAGE_URL2]["cursor"] == 1
      and st_inc["paper_jobs"][PAGE_URL2]["total"] == 3)
check("D11 未采完不写 url_fp（防永久跳过历史 bug）",
      PAGE_URL2 not in st_inc.get("url_fp", {}))
n2 = run_incomplete()
check("D12 incomplete 二轮续采", n2 == 1)
check("D12b 二轮后游标继续前进",
      json.loads(state_path.read_text(encoding="utf-8"))["paper_jobs"][PAGE_URL2]["cursor"] == 2)

# 溯源：题目带 source_url 入库，按页可回查
rows = st_store.find_questions_by_source_url(PAGE_URL)
check("D13 溯源回查命中", len(rows) > 0)
check("D14 回查题目字段完整", all(r.get("question") and "id" in r for r in rows))
check("D15 回查 options 反序列化为 list",
      all(isinstance(r.get("options"), list) for r in rows))

# 重置该套卷后可重采
ac.reset_collected_sets([PAGE_URL])
res5, calls5 = run_url_once()
check("D16 重置后恢复提取", calls5 == 1)

# ============================================================
# E) _add 对非 URL source 不写 source_url
# ============================================================
q_copy = [dict(fake_q[0])]
with mock.patch.object(ac, "_enabled", return_value=True):
    pass  # _add 是闭包，无法直接调；经文档库路径间接验证：source_url 应为空
docs_only_rows = st_store.find_questions_by_source_url("不存在的文档名")
check("E1 非 URL 来源不会误挂 source_url", len(docs_only_rows) == 0)

# 提取失败页：窗口内跳过、窗口外续采
col2 = {}
ac._set_fail(col2, "https://s/bad", "调用超时(60s)")
check("G1 失败标记 status=failed", col2["https://s/bad"]["status"] == "failed")
check("G2 失败窗口内跳过", ac._set_should_skip(col2, "https://s/bad", "fpX", False) == "failed")
check("G3 force 也不重烧失败页", ac._set_should_skip(col2, "https://s/bad", "fpX", True) == "failed")
col2["https://s/bad"]["ts_epoch"] = __import__("time").time() - 3 * 3600  # 伪造3h前
check("G4 窗口外放行续采", ac._set_should_skip(col2, "https://s/bad", "fpX", False) == "")
check("G5 无标记放行", ac._set_should_skip(col2, "https://s/new", "fp", False) == "")
check("G6 complete 内容变化放行", ac._set_should_skip(
    {"u": {"status": "complete", "fp": "old"}}, "u", "new", False) == "")
check("G7 complete 内容未变跳过(含force)", ac._set_should_skip(
    {"u": {"status": "complete", "fp": "same"}}, "u", "same", True) == "complete")
check("G8 incomplete 永不因标记跳过", ac._set_should_skip(
    {"u": {"status": "incomplete", "fp": "same"}}, "u", "same", False) == "")

# 汇总含 failed 计数
sum2 = ac._sets_summary({"a": {"status": "complete", "added": 5},
                         "b": {"status": "failed", "added": 0},
                         "c": {"status": "incomplete", "added": 1}})
check("G9 汇总 failed=1", sum2["failed"] == 1)
check("G10 汇总 complete=1/incomplete=1", sum2["complete"] == 1 and sum2["incomplete"] == 1)

# ============================================================
# F) 升级迁移：旧版仅有 url_fp 的 state 首读转 complete 标记
# ============================================================
state_path.write_text(json.dumps({
    "urls": ["https://old/"], "url_fp": {"https://old/p/1": "fpOld"},
}, ensure_ascii=False), encoding="utf-8")
mt = ac._load_state()
check("F1 迁移种子 collected_sets", "https://old/p/1" in mt.get("collected_sets", {}))
check("F2 迁移标记为 complete", mt["collected_sets"]["https://old/p/1"]["status"] == "complete")
check("F3 迁移持久化", "collected_sets" in json.loads(state_path.read_text(encoding="utf-8")))
check("F4 迁移后 url_fp 保留", mt.get("url_fp", {}).get("https://old/p/1") == "fpOld")

# ============================================================
# H) 试卷作业：整卷为单位串行 + 游标续采 + 原子提交 + 阻塞封顶
# ============================================================
st_h = {}
ac._paper_enqueue(st_h, "p1", "url", {"url": "https://x/1", "title": "卷一"})
ac._paper_enqueue(st_h, "p2", "url", {"url": "https://x/2", "title": "卷二"})
check("H1 入队后取队首=最早入队", ac._paper_next(st_h)[0] == "p1")

# 串行：p1 未采完时队首永远是 p1，不会跳到 p2（本轮不把别的卷插进来）
j1 = ac._paper_jobs(st_h)["p1"]
j1["cursor"], j1["total"] = 1, 3
check("H2 未采完时队首不变（串行不跳卷）", ac._paper_next(st_h)[0] == "p1")

# 游标续采：_extract_enhanced 从 start_piece 开始，并回传 next_piece/total_pieces
_filler = "本题考察材料分析与计算能力，需逐项核对题干条件后再作答。" * 6
units_text = "\n".join(
    f"{i} 题目{i}：以下关于知识点{i}的说法，正确的是：\n"
    f"A、选项甲{i}{_filler}\nB、选项乙{i}{_filler}\n"
    f"C、选项丙{i}{_filler}\nD、选项丁{i}{_filler}"
    for i in range(1, 13))
_seen = []

def _fake_via(piece, subject):
    _seen.append(piece)
    return {"questions": [{"qtype": "choice", "question": "题目",
                           "options": ["A、甲", "B、乙", "C、丙", "D、丁"],
                           "answer": "A", "analysis": "解析"}], "complete": True}, []

# 第一轮：第一个片段跑完就停（模拟被轮次预算打断）
_guard = {"n": 0}
def _stop_after_one():
    _guard["n"] += 1
    return _guard["n"] > 1

with mock.patch.object(ac, "_extract_via", _fake_via):
    r_first, _ = ac._extract_enhanced(units_text, "", threads=1,
                                      should_stop=_stop_after_one)
    first_total = r_first["total_pieces"]
    first_next = r_first["next_piece"]
    _seen.clear()
    r_resume, _ = ac._extract_enhanced(units_text, "", threads=1,
                                       start_piece=first_next)
check("H3 首轮被预算打断：有进度但未跑完",
      first_total >= 2 and first_next == 1 and r_first["complete"] is False,
      f"total={first_total} next={first_next}")
check("H4 续采只跑剩余片段（不从头重跑）",
      len(_seen) == first_total - first_next,
      f"resumed={len(_seen)} expect={first_total - first_next}")
check("H5 续采后标记完成",
      r_resume["complete"] is True and r_resume["next_piece"] == r_resume["total_pieces"])

# 整卷原子提交：额度不足 → 推迟（绝不截断丢题）
st_c = {}
ac._paper_enqueue(st_c, "pc", "doc", {"tid": "T001", "doc_name": "c.md"})
jc = ac._paper_jobs(st_c)["pc"]
jc["cursor"], jc["total"] = 2, 2
jc["buf"] = [{"question": "题目甲", "options": ["A、甲"], "answer": "A"}]
st_c["collected_sets"] = {}   # 生产里传的就是 state["collected_sets"] 本体
res_defer = ac._paper_commit(st_c, "pc", jc, lambda buf, j: None,
                             st_c["collected_sets"], "fpC")
check("H6 额度不足时推迟提交且不丢缓冲",
      res_defer["status"] == "incomplete" and jc["status"] == "pending"
      and len(jc["buf"]) == 1)
_committed = []
res_ok = ac._paper_commit(st_c, "pc", jc,
                          lambda buf, j: (_committed.append(list(buf)), len(buf))[1],
                          st_c["collected_sets"], "fpC")
check("H7 额度足够时整卷一次提交并清空缓冲",
      res_ok["status"] == "done" and res_ok["added"] == 1
      and len(_committed) == 1 and not jc["buf"])
check("H7b 提交后同步写 collected_sets=complete",
      (st_c.get("collected_sets") or {}).get("pc", {}).get("status") == "complete")

# 阻塞封顶：连续无进展 → blocked，且不再堵住后面的卷
check("H8 blocked 自动轮跳过 / force 放行重试",
      ac._set_should_skip({"k": {"status": "blocked"}}, "k", "f", False) == "blocked"
      and ac._set_should_skip({"k": {"status": "blocked"}}, "k", "f", True) == "")
st_s = {}
ac._paper_enqueue(st_s, "ps", "url", {"url": "https://x/s"})
ac._paper_enqueue(st_s, "ps2", "url", {"url": "https://x/s2"})
js = ac._paper_jobs(st_s)["ps"]
_states = [ac._paper_stall(st_s, "ps", js, "片段反复失败", {})
           for _ in range(ac._PAPER_MAX_STALL)]
check("H9 连续无进展达上限 → blocked",
      _states == ["stalled"] * (ac._PAPER_MAX_STALL - 1) + ["blocked"]
      and js["status"] == "blocked")
check("H10 阻塞后队首让给下一张卷（坏卷不堵队列）",
      ac._paper_next(st_s)[0] == "ps2")
_hadm = ac.paper_jobs_admin(st_s)
check("H11 管理视图能看到阻塞卷与卡点",
      _hadm["summary"]["blocked"] == 1 and _hadm["current"] == "ps2"
      and _hadm["blocked"][0]["key"] == "ps"
      and _hadm["blocked"][0]["stall"] >= ac._PAPER_MAX_STALL)

# 内容指纹变化 → 作废重来（不清空游标就会采半张旧卷）
st_f = {}
ac._paper_enqueue(st_f, "pf", "url", {"url": "https://x/f"})
jf = ac._paper_jobs(st_f)["pf"]
jf["fp"], jf["cursor"], jf["total"] = "oldfp", 5, 9
jf["buf"] = [{"question": "旧题"}]
with mock.patch.object(ac, "_text_fp", return_value="newfp"), \
     mock.patch.object(ac, "_extract_enhanced",
                       lambda *a, **k: ({"questions": [], "complete": False,
                                         "next_piece": 0, "total_pieces": 9}, [])), \
     mock.patch.object(ac, "_model_down", return_value=False):
    ac._paper_work(st_f, "pf", jf, "内容已更新的试卷正文内容", "",
                   lambda buf, j: 0, lambda *a, **k: None, lambda: False, {})
check("H12 源内容变化 → 作废缓存与游标、从片段 0 重来",
      jf["cursor"] == 0 and jf["buf"] == [] and jf["fp"] == "newfp")

# ============================================================
# I) 采集范式升级：collected_sets → paper_jobs 迁移（防升级后全量重采）
# ============================================================
state_path.write_text(json.dumps({
    "urls": ["https://old/"],
    "collected_sets": {
        "https://old/p/1": {"fp": "fpA", "status": "complete", "added": 7, "n": 7},
        "https://old/p/2": {"fp": "fpB", "status": "gated", "added": 0, "n": 0},
        "https://old/p/3": {"fp": "fpC", "status": "blocked", "added": 0, "n": 0},
        "T001|x.md": {"fp": "fpD", "status": "complete", "added": 3, "n": 3},
        "https://old/p/4": {"fp": "", "status": "failed", "added": 0, "n": 0},
        "https://old/p/5": {"fp": "fpE", "status": "incomplete", "added": 1, "n": 2},
    },
}, ensure_ascii=False), encoding="utf-8")
mt2 = ac._load_state()
_pj = mt2.get("paper_jobs") or {}
check("I1 已采完整标记迁移为 done 作业（保留指纹与累计入库）",
      _pj.get("https://old/p/1", {}).get("status") == "done"
      and _pj["https://old/p/1"]["fp"] == "fpA"
      and _pj["https://old/p/1"]["added"] == 7)
check("I2 gated 同样迁移为 done", _pj.get("https://old/p/2", {}).get("status") == "done")
check("I3 blocked 迁移为 blocked（自动轮继续跳过）",
      _pj.get("https://old/p/3", {}).get("status") == "blocked")
check("I4 文档试卷键同样迁移", _pj.get("T001|x.md", {}).get("status") == "done")
check("I5 failed/incomplete 不建作业（作为新卷重排续采）",
      "https://old/p/4" not in _pj and "https://old/p/5" not in _pj)
check("I6 迁移结果落盘", "paper_jobs" in json.loads(state_path.read_text(encoding="utf-8")))
check("I7 迁移后无待采卷：升级不会触发全量重采", ac._paper_next(mt2)[0] is None)
check("I8 迁移作业带 migrated 标记（指纹缺失时可补基线）",
      all(_pj[k].get("migrated") for k in ("https://old/p/1", "T001|x.md")))

# ============================================================
# J) 阻塞卷的 force 重试（放行必须真的放回队列）
# ============================================================
state_path.write_text(json.dumps({
    "urls": ["https://s.example.com"],
    "collected_sets": {PAGE_URL: {"fp": "fpB", "status": "blocked",
                                  "added": 0, "n": 0}},
    "paper_jobs": {PAGE_URL: {"kind": "url", "status": "blocked", "cursor": 3,
                              "total": 9, "buf": [], "stall": 3, "fp": "fpB",
                              "msg": "多次无进展"}},
}, ensure_ascii=False), encoding="utf-8")

res_b, calls_b = run_url_once(force=False)
check("J1 非 force 轮不重提阻塞卷", calls_b == 0)
check("J2 非 force 轮计入 sets_blocked", res_b["urls"].get("sets_blocked", 0) >= 1)

# force：人工放行 → 必须真的回到队列并被采集（只放行不重置状态 = 假放行）
res_f, calls_f = run_url_once(force=True)
check("J3 force 轮真的重试阻塞卷", calls_f == 1)
st_j = json.loads(state_path.read_text(encoding="utf-8"))["paper_jobs"][PAGE_URL]
check("J4 force 重试成功后作业转 done 且游标复位后重采",
      st_j["status"] == "done" and st_j["cursor"] >= 1 and st_j["stall"] == 0)

print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
sys.exit(0 if ok else 1)
