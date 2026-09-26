# -*- coding: utf-8 -*-
"""批次18 · #13 难度预估单测。

覆盖：
  1. 无答题记录 → fit（匹配当前水平）
  2. 简单题全对（≥80% 且中等≥60%）→ up（建议 4-5 档）
  3. 整体正确率 <40% → down（建议 1-2 档）
  4. today 模式响应带 difficulty_hint 且 today_note 拼接档位提示
  5. up 档位收敛：today 抽题难度落在建议区间内（题库有对应难度题时）

运行：cd backend && python tests/test_difficulty_hint.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from poc.study import StudyStore  # noqa: E402

PASS = 0


def chk(name: str, cond: bool, extra: str = ""):
    global PASS
    assert cond, f"FAIL: {name} {extra}"
    PASS += 1
    print(f"  ok {name}")


def _store() -> StudyStore:
    tmp = tempfile.mkdtemp(prefix="gkdh_")
    return StudyStore(db_path=Path(tmp) / "study.db")


def _seed(st: StudyStore):
    """三档难度题各若干：easy(1-2) / mid(3) / hard(4-5)。"""
    for i, (diff, cat) in enumerate([
        (1, "言语理解"), (2, "言语理解"), (2, "言语理解"),
        (3, "言语理解"), (3, "言语理解"),
        (4, "言语理解"), (5, "言语理解"),
    ], start=1):
        st.add_question("T999", "choice", f"难度{diff}题{i}：选？", "A",
                        options=["A. 甲", "B. 乙", "C. 丙", "D. 丁"],
                        analysis="解析", category=cat, difficulty=diff)


def _do(st: StudyStore, user: str, diff: int, i: int, right: bool):
    st.submit_practice(user, "T999", f"难度{diff}题{i}：选？",
                       "A" if right else "B",
                       question_id=_qid(st, diff, i))


def _qid(st: StudyStore, diff: int, i: int) -> int:
    for q in st._qbank_pool("T999"):
        if q["question"] == f"难度{diff}题{i}：选？":
            return q["id"]
    raise AssertionError(f"题不存在 d{diff}#{i}")


def test_no_records_fit():
    st = _store()
    _seed(st)
    h = st._difficulty_hint("u-none")
    chk("无记录 level=fit", h["level"] == "fit" and h["min_difficulty"] is None)


def test_high_accuracy_up():
    st = _store()
    _seed(st)
    # 简单题 3 全对（easy=100%≥80%）+ 中等 2 全对（mid=100%≥60%）→ up
    st2 = _store()
    _seed(st2)
    _do(st2, "u-up", 1, 1, True)   # easy 全对
    _do(st2, "u-up", 2, 2, True)
    _do(st2, "u-up", 2, 3, True)
    _do(st2, "u-up", 3, 4, True)   # mid 全对
    _do(st2, "u-up", 3, 5, True)
    h = st2._difficulty_hint("u-up")
    chk("高正确率 level=up", h["level"] == "up", str(h))
    chk("up 建议区间 4-5", h["min_difficulty"] == 4 and h["max_difficulty"] == 5)


def test_low_accuracy_down():
    st = _store()
    _seed(st)
    for i, ok in [(1, False), (2, False), (3, False), (4, True), (5, False)]:
        d = 1 if i == 1 else (2 if i in (2, 3) else 3)
        _do(st, "u-down", d, i, ok)
    h = st._difficulty_hint("u-down")
    chk("低正确率 level=down", h["level"] == "down", str(h))
    chk("down 建议区间 1-2", h["min_difficulty"] == 1 and h["max_difficulty"] == 2)


def test_today_payload_has_hint():
    st = _store()
    _seed(st)
    # 制造 up 场景
    _do(st, "u-today", 1, 1, True)
    _do(st, "u-today", 2, 2, True)
    _do(st, "u-today", 2, 3, True)
    d = st.get_next_practice("T999", "u-today", mode="today")
    chk("today 带 difficulty_hint", isinstance(d.get("difficulty_hint"), dict))
    chk("today_note 拼接档位", "📈" in (d.get("today_note") or ""), d.get("today_note", ""))
    if d.get("difficulty_hint", {}).get("min_difficulty"):
        chk("抽题难度在建议区间",
            d["difficulty"] >= d["difficulty_hint"]["min_difficulty"],
            f"diff={d['difficulty']} hint={d['difficulty_hint']}")


def test_today_due_review_no_hint_change():
    st = _store()
    _seed(st)
    d = st.get_next_practice("T999", "u-fresh-today", mode="today")
    chk("无记录 today 返回题", d.get("id") or d.get("question"))
    chk("无记录 hint=fit 或缺失", (d.get("difficulty_hint") or {}).get("level", "fit") == "fit")


print("难度预估单测：")
test_no_records_fit()
test_high_accuracy_up()
test_low_accuracy_down()
test_today_payload_has_hint()
test_today_due_review_no_hint_change()
print(f"PASS {PASS} assertions")
sys.exit(0)
