# -*- coding: utf-8 -*-
"""批次27-F 提升计划时间分配增强：模考时间画像提取 + time_plan 建议键。

需求（用户）：任务书把学员缺点、提升方向写进去，特别是模考的时间分配。
数据源：fenbi_mock_exams.report_json（getReport 原样）——
  subReports type=0 → 整卷（time=计划秒/score/fullmark + courseStat.elapseTime 实际用时）
  subReports type=1 → courseReports[0].details[] 顶层模块行（time 秒/qc/cc；全 0 = 整场未作答）
实现：_extract_mock_time 跨场聚合画像 → _build_prompt 注入数据段+time_plan schema
     → _fallback_report 本地规则版 time_plan（AI 全败兜底）→ 前端「⏱ 模考时间分配」区块。

运行：cd backend && python tests/test_b27f_time_plan.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import poc.fenbi_bind as fb


def _mock_report(sub_reports):
    return json.dumps({"subReports": sub_reports}, ensure_ascii=False)


# 真实结构样本（源自生产 id=9 模考大赛）
_FULL_MOCK = _mock_report([
    {"topTitle": "练习报告", "type": 9},
    {"basicType": 2, "name": "模考大赛", "time": 7200, "difficulty": 4.7,
     "submitTime": 1777796463569, "paperId": 6674655, "startTime": 0,
     "endTime": 0, "basicNameType": 0, "noticeType": 0, "fullmark": 100.0,
     "score": 17.2, "type": 0},
    {"courseReports": [{"tikuPrefix": "xingce", "courseName": "行测",
                        "mixType": 0,
                        "courseStat": {"questionCount": 135, "answerCount": 35,
                                       "correctCount": 26, "elapseTime": 4032,
                                       "score": 0.0, "subjCount": 0,
                                       "unAnswerableCount": 0},
                        "details": [
                            {"type": 1, "name": "数量关系", "time": 2294,
                             "questionCount": 15, "correctCount": 10,
                             "children": []},
                            {"type": 1, "name": "资料分析", "time": 1738,
                             "questionCount": 20, "correctCount": 16,
                             "children": []},
                            {"type": 1, "name": "言语理解与表达", "time": 0,
                             "questionCount": 30, "correctCount": 0,
                             "children": []},
                            {"type": 1, "name": "判断推理", "time": 0,
                             "questionCount": 35, "correctCount": 0,
                             "children": []}]}], "type": 1},
])

# 放弃型练习报告（score 缺失，单模块有作答）
_PRACTICE = _mock_report([
    {"basicType": 2, "name": "专项练习", "time": 0, "fullmark": None,
     "score": None, "type": 0},
    {"courseReports": [{"courseStat": {"questionCount": 10, "answerCount": 10,
                                       "correctCount": 7, "elapseTime": 873},
                        "details": [{"type": 1, "name": "资料分析",
                                     "time": 873, "questionCount": 10,
                                     "correctCount": 7, "children": []}]}],
     "type": 1},
])


def _main():
    fails = []

    def chk(name, cond):
        print(("  ✓ " if cond else "  ✗ ") + name)
        if not cond:
            fails.append(name)

    # ================= 1. _parse_mock_report =================
    print("[1] _parse_mock_report 结构解析")
    basic, rows = fb._parse_mock_report(_FULL_MOCK)
    chk("完整模考 整卷字段", basic["plan_sec"] == 7200 and basic["score"] == 17.2
        and basic["fullmark"] == 100.0 and basic["elapsed_sec"] == 4032
        and basic["answered"] == 35 and basic["total"] == 135)
    chk("完整模考 模块行", len(rows) == 4
        and rows[0] == {"module": "数量关系", "time": 2294, "qc": 15, "cc": 10})
    chk("未作答模块行 time=0 cc=0",
        any(r["module"] == "言语理解与表达" and r["time"] == 0 and r["cc"] == 0
            for r in rows))
    b2, r2 = fb._parse_mock_report("not-json{{")
    chk("坏 JSON 返回空", b2 is None and r2 == [])
    b3, r3 = fb._parse_mock_report(_PRACTICE)
    chk("练习型 score=None 但模块行保留", b3["score"] is None
        and r3 == [{"module": "资料分析", "time": 873, "qc": 10, "cc": 7}])

    # ================= 2. _extract_mock_time 聚合 =================
    print("[2] _extract_mock_time 跨场聚合")
    tmp = Path(tempfile.mkdtemp(prefix="gk_b27f_"))
    store = fb.FenbiBindStore(db_path=tmp / "fenbi.db")
    store.save_mock_exam("u-t", {
        "exercise_key": "1_1_aaa", "name": "第十八季模考", "sheet_type": 1,
        "score": 17.2, "fullmark": 100.0, "question_count": 135,
        "submit_time": "2026-09-20 10:00:00", "report": _FULL_MOCK}, [])
    store.save_mock_exam("u-t", {
        "exercise_key": "1_1_bbb", "name": "资料专项", "sheet_type": 3,
        "submit_time": "2026-09-25 10:00:00", "report": _PRACTICE}, [])
    tp = fb._extract_mock_time(store, "u-t")
    chk("有数据 返回画像", tp is not None and len(tp["exams"]) == 1
        and tp["exams"][0]["plan_sec"] == 7200)
    mods = {m["module"]: m for m in tp["modules"]}
    chk("数量关系 跨场聚合求均值", mods["数量关系"]["attempted_qc"] == 15
        and mods["数量关系"]["avg_sec_per_q"] == 152.9
        and mods["数量关系"]["correct_rate"] == round(10 / 15, 2))
    chk("资料分析 双源聚合", mods["资料分析"]["attempted_qc"] == 30
        and mods["资料分析"]["total_sec"] == 1738 + 873
        and mods["资料分析"]["correct"] == 23)
    chk("言语/判断 记为整场未作答", mods["言语理解与表达"]["unanswered_exams"] == 1
        and mods["判断推理"]["unanswered_exams"] == 1
        and mods["言语理解与表达"]["attempted_qc"] == 0)
    chk("无数据用户 返回 None", fb._extract_mock_time(store, "u-none") is None)

    # ================= 3. _build_prompt 两态 =================
    print("[3] _build_prompt 时间段注入")
    dist = [{"module": "言语理解与表达", "total": 120,
             "points": [{"name": "逻辑填空", "count": 60}]}]
    p_no = fb._build_prompt(dist, [], 120)
    chk("无画像 不含 time_plan/模考段",
        "time_plan" not in p_no and "模考作答与时间分配" not in p_no)
    p_tp = fb._build_prompt(dist, [], 120, tp)
    chk("有画像 注入数据段与 schema",
        "time_plan" in p_tp and "模考作答与时间分配" in p_tp
        and "152.9" in p_tp and "unanswered_exams" in p_tp)
    chk("有画像 保留取舍规则", "策略性放弃" in p_tp and "55 秒/题" in p_tp)

    # ================= 4. _shrink_dist 防御 =================
    print("[4] _shrink_dist 万题防御")
    big = [{"module": f"m{i}", "total": 1,
            "points": [{"name": f"p{j}", "count": 1} for j in range(60)]}
           for i in range(50)]
    shrunk = fb._shrink_dist(big, max_chars=100)
    chk("超长截断 每模块知识点≤12",
        all(len(m["points"]) <= 12 for m in shrunk) and len(shrunk) == 50)
    chk("正常规模原样保留", fb._shrink_dist(dist) == dist)

    # ================= 5. _local_time_plan 本地规则 =================
    print("[5] _local_time_plan 四档规则")
    tp_rules = {"exams": [], "modules": [
        {"module": "数量关系", "attempted_qc": 10, "correct": 7,
         "total_sec": 1253, "avg_sec_per_q": 125.3, "correct_rate": 0.7,
         "unanswered_exams": 2},
        {"module": "判断推理", "attempted_qc": 35, "correct": 9,
         "total_sec": 750, "avg_sec_per_q": 21.4, "correct_rate": 0.26,
         "unanswered_exams": 2},
        {"module": "政治理论", "attempted_qc": 0, "correct": 0,
         "total_sec": 0, "avg_sec_per_q": 0, "correct_rate": 0,
         "unanswered_exams": 3},
        {"module": "资料分析", "attempted_qc": 30, "correct": 22,
         "total_sec": 1800, "avg_sec_per_q": 60.0, "correct_rate": 0.73,
         "unanswered_exams": 0},
        {"module": "常识判断", "attempted_qc": 20, "correct": 8,
         "total_sec": 1200, "avg_sec_per_q": 60.0, "correct_rate": 0.4,
         "unanswered_exams": 0},
    ]}
    plan = fb._local_time_plan(tp_rules)
    by_mod = {t["module"]: t for t in plan}
    chk("过慢+正确率尚可 → 提速建议", "远超 55 秒基准" in by_mod["数量关系"]["issue"]
        and "练速度" in by_mod["数量关系"]["suggestion"])
    chk("过快+低正确率 → 放慢建议", "答题过快" in by_mod["判断推理"]["issue"]
        and "放慢" in by_mod["判断推理"]["suggestion"])
    chk("整场放弃 → 取舍建议（非弃考区劝攻）",
        "整场未作答" in by_mod["政治理论"]["issue"]
        and "不建议放弃" in by_mod["政治理论"]["suggestion"])
    chk("健康节奏 → 保持", "节奏健康" in by_mod["资料分析"]["issue"])
    chk("速度尚可低正确率 → 基础优先",
        "基础" in by_mod["常识判断"]["suggestion"])
    chk("空画像返回空列表", fb._local_time_plan(None) == [])

    # ================= 6. _fallback_report 集成 =================
    print("[6] _fallback_report 含 time_plan")
    fr = fb._fallback_report(
        [{"module": "言语理解与表达", "total": 120, "points": []}],
        {}, 120, tp_rules)
    chk("降级报告 time_plan 非空", isinstance(fr.get("time_plan"), list)
        and len(fr["time_plan"]) >= 4)
    chk("summary 追加时间结论", "时间分配" in fr["summary"])
    fr2 = fb._fallback_report([{"module": "言语", "total": 5, "points": []}],
                              {}, 5, None)
    chk("无画像 time_plan 为空列表（键仍存在，前端零判断）",
        fr2.get("time_plan") == [])

    # ================= 7. run_analysis 全链（AI 全败→降级含 time_plan） =================
    print("[7] run_analysis 管线接线")
    fake_tree = [
        {"id": 1, "name": "言语理解与表达", "questionIds": [101],
         "children": [{"name": "逻辑填空", "questionIds": [101]}]},
    ]
    fake_sols = {
        "101": {"id": 101, "content": "<p>t</p>", "difficulty": 5,
                "correctAnswer": {"choice": "0"},
                "accessories": [{"type": 101, "options": ["A", "B", "C", "D"]}],
                "solution": "<p>s</p>", "keypoints": [{"name": "实词辨析"}]}}
    orig_tree, orig_sols = fb.fetch_error_tree, fb.fetch_solutions_batch
    fb.fetch_error_tree = lambda c: fake_tree
    fb.fetch_solutions_batch = lambda c, ids, interval=2.0: [
        fake_sols[str(i)] for i in ids if str(i) in fake_sols]
    try:
        aid, err = store.start_analysis("u-t")
        assert not err, err
        fb.run_analysis(store, "u-t", aid,
                        lambda m: (_ for _ in ()).throw(RuntimeError("模拟 AI 全挂")))
        a = store.latest_analysis("u-t")
        rep = json.loads(a["report"])
        chk("AI 全败降级 done", a["status"] == "done"
            and "降级" in (a["error"] or ""))
        chk("降级版含 time_plan 且数据来自库中模考",
            len(rep.get("time_plan") or []) >= 3)
        # 有画像时 prompt 应含时间段（hook 捕获；u-t 降级记录带 error 不进冷却，可立即重跑）
        seen = {}
        orig_bp = fb._build_prompt
        fb._build_prompt = lambda d, s, t, tpn=None: seen.setdefault(
            "has_tp", tpn is not None) or orig_bp(d, s, t, tpn)
        aid2, _ = store.start_analysis("u-t")
        fb.run_analysis(store, "u-t", aid2,
                        lambda m: json.dumps({"summary": "ok", "weeks": []},
                                             ensure_ascii=False))
        chk("管线把画像传给 prompt", seen.get("has_tp") is True)
        fb._build_prompt = orig_bp
    finally:
        fb.fetch_error_tree, fb.fetch_solutions_batch = orig_tree, orig_sols

    print()
    if fails:
        print(f"❌ {len(fails)} 项失败：", fails)
        sys.exit(1)
    print("✅ 批次27-F 时间分配增强测试全部通过")


if __name__ == "__main__":
    _main()
