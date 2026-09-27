# -*- coding: utf-8 -*-
"""批次27 三大同步（错题/收藏/模考）：Store/同步逻辑/端点/幂等/鉴权断言。

运行：cd backend && python tests/test_b27_sync.py
"""
import json
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import poc.api as api
import poc.fenbi_bind as fb


def _sol(qid, content="题干", answer={"choice": "0", "type": 201}):
    return {"id": qid, "content": f"<p>{content}</p>",
            "options": [], "correctAnswer": answer,
            "solution": "<p>解析</p>", "difficulty": 5,
            "keypoints": [{"name": "kp"}], "source": "2026国考"}


def _tree(mod="言语", point="逻辑填空", qids=(1, 2)):
    return [{"name": mod, "questionIds": [qids[0]],
             "children": [{"name": point, "questionIds": list(qids[1:])}]}]


def _main():
    tmp = Path(tempfile.mkdtemp(prefix="gk_b27_"))
    store = fb.FenbiBindStore(db_path=tmp / "fenbi_bind.db")
    store.upsert_binding("u-a", "ck-a" * 8)
    store.upsert_binding("u-b", "ck-b" * 8)

    def fake_resolve(authorization):
        t = (authorization or "").replace("Bearer ", "", 1).strip()
        return {"user_id": "u-a", "username": "甲", "role": "free"} \
            if t == "tok-a" else None

    api.fenbi_bind_store = store
    api._resolve_user = fake_resolve
    client = TestClient(api.app)
    h = {"Authorization": "Bearer tok-a"}
    fails = []

    def chk(name, cond, extra=""):
        print(("  ✓ " if cond else "  ✗ ") + name + (f" [{extra}]" if extra and not cond else ""))
        if not cond:
            fails.append(name)

    orig = (fb.fetch_error_tree, fb.fetch_collect_tree,
            fb.fetch_solutions_batch, fb.fetch_exercise_history,
            fb.fetch_mock_report, fb.fetch_mock_solution)
    try:
        # ---------- Store 层 ----------
        n = store.save_collect_questions("u-a", [
            {"question_id": "11", "module": "言语", "keypoint": "主旨",
             "content": "c", "options": ["A"], "answer": "A",
             "analysis": "s", "difficulty": 3, "source": "x"},
            {"question_id": "12"}])
        chk("收藏快照落库", n == 2)
        n = store.save_collect_questions("u-a", [{"question_id": "11"}])
        chk("收藏快照幂等（重复 0 新增）", n == 0)
        chk("collect_ids", store.collect_ids("u-a") == {"11", "12"})
        chk("collect_count", store.collect_count("u-a") == 2)
        rows, total = store.collect_list("u-a")
        chk("collect_list 分页", total == 2
            and rows[0]["question_id"] == "12"          # ORDER BY id DESC
            and any(r["module"] == "言语" for r in rows))

        # 错题列表（空）
        rows, total = store.wrong_list("u-a")
        chk("wrong_list 空", total == 0)

        # 模考 UPSERT + answers 整卷替换
        eid = store.save_mock_exam("u-a", {
            "exercise_key": "1_1_x", "name": "模考一", "sheet_type": 1,
            "score": 12.5, "fullmark": 100.0, "question_count": 2,
            "correct_count": 1, "wrong_count": 1, "unanswered": 0,
            "duration_sec": 600, "submit_time": "2026-09-01 10:00:00",
            "difficulty": 4.5, "paper_id": 5500868,
            "report": {"subReports": [{"score": 12.5}]}}, [
            {"question_id": "1", "global_key": "3_1_a", "status": 1,
             "user_answer": '{"choice":"0"}', "correct_answer": '{"choice":"0"}',
             "content": "q1", "analysis": "a1", "keypoints": ["k1"]},
            {"question_id": "2", "global_key": "3_1_b", "status": -1,
             "user_answer": '{"choice":"1"}', "correct_answer": '{"choice":"0"}',
             "content": "q2", "analysis": "a2", "keypoints": []}])
        d0 = store.mock_exam_detail("u-a", eid)
        chk("report JSON 解析（首存）",
            (d0["report"].get("subReports") or [{}])[0].get("score") == 12.5)
        # 重存（改分数+少一题）→ answers 整卷替换
        store.save_mock_exam("u-a", {
            "exercise_key": "1_1_x", "name": "模考一改", "sheet_type": 1,
            "score": 20.0, "fullmark": 100.0, "question_count": 1,
            "correct_count": 1, "wrong_count": 0, "unanswered": 0,
            "report": {}}, [
            {"question_id": "1", "global_key": "3_1_a", "status": 1,
             "user_answer": "", "correct_answer": "", "content": "q1'",
             "analysis": "", "keypoints": []}])
        lst = store.mock_exam_list("u-a")
        chk("模考 UPSERT（无重复行）", len(lst) == 1 and lst[0]["name"] == "模考一改"
            and lst[0]["score"] == 20.0)
        d = store.mock_exam_detail("u-a", eid)
        chk("作答明细整卷替换", len(d["answers"]) == 1
            and d["answers"][0]["content"] == "q1'")
        chk("report JSON 解析", isinstance(d["report"], dict))
        chk("mock_exam_keys", store.mock_exam_keys("u-a") == {"1_1_x"})

        # sync_log 去重
        sid, err = store.start_sync("u-a", "mock")
        sid2, err2 = store.start_sync("u-a", "mock")
        chk("同步任务 running 去重", sid > 0 and sid2 == 0 and err2)
        store.update_sync(sid, status="done", total=1, new_count=1,
                          stage="完成", progress=100, done_at="t")
        chk("latest_sync", store.latest_sync("u-a", "mock")["status"] == "done")
        sid3, _ = store.start_sync("u-a", "mock")
        store.update_sync(sid3, status="failed", error="x", done_at="t")
        chk("failed 后可重新同步", sid3 > 0)
        chk("sync_history", len(store.sync_history("u-a")) == 2)

        # ---------- 同步逻辑（mock 粉笔 API） ----------
        fb.fetch_error_tree = lambda ck: _tree("言语", "逻辑填空", (1, 2))
        fb.fetch_collect_tree = lambda ck: _tree("判断", "图形", (21, 22))
        fb.fetch_solutions_batch = lambda ck, ids, interval=2.0: [
            _sol(int(i), f"题干{i}") for i in ids]

        def _hist(cookie, cat, limit=30, cursor="", interval=1.5):
            items = []
            if cat == 1:
                items = [{"exerciseKey": "1_1_m1", "sheetName": "模考大赛",
                          "sheetType": 1, "status": 1, "questionCount": 2,
                          "correctCount": 1},
                         {"exerciseKey": "1_1_m0", "sheetName": "未交卷",
                          "sheetType": 1, "status": 0}]
            else:
                items = [{"exerciseKey": "1_1_p1", "sheetName": "专项练习",
                          "sheetType": 3, "status": 1, "questionCount": 1}]
            return {"cursor": "", "items": items}
        fb.fetch_exercise_history = _hist

        def _report(cookie, key, interval=1.5):
            return {"exerciseId": key, "name": f"卷{key}", "sheetType": 1,
                    "subReports": [
                        {"basicType": 2, "score": 4.0, "fullmark": 100.0,
                         "time": 7200, "difficulty": 4.8,
                         "submitTime": 1784735999021, "paperId": 5500868},
                        {"courseReports": [{"courseName": "行测",
                                            "courseStat": {
                                                "questionCount": 2,
                                                "correctCount": 1}}]}],
                    "userAnswers": {}}
        fb.fetch_mock_report = _report

        def _solution(cookie, key, interval=1.5):
            uas = {}
            if key == "1_1_m1":
                uas = {"3_1_a": {"key": "3_1_a", "id": 1, "status": 1,
                                 "answer": {"choice": "0"}},
                       "3_1_b": {"key": "3_1_b", "id": 2, "status": -1,
                                 "answer": {"choice": "1"}}}
            else:
                uas = {"3_1_c": {"key": "3_1_c", "id": 1, "status": 1,
                                 "answer": {"choice": "0"}}}
            return {"materials": [], "solutions": [_sol(1, "q1"), _sol(2, "q2")],
                    "userAnswers": uas, "name": f"卷{key}", "sheetType": 1}
        fb.fetch_mock_solution = _solution

        total, saved = fb.sync_wrong(store, "u-a")
        chk("sync_wrong 全量", total == 2 and saved == 2)
        rows, t = store.wrong_list("u-a")
        chk("错题落库字段", t == 2 and rows[0]["module"] == "言语"
            and rows[0]["keypoint"] in ("逻辑填空", "kp"))
        total, saved = fb.sync_wrong(store, "u-a")
        chk("sync_wrong 幂等增量", total == 2 and saved == 0)

        total, saved = fb.sync_collect(store, "u-a")
        chk("sync_collect", total == 2 and saved == 2)
        total, saved = fb.sync_collect(store, "u-a")
        chk("sync_collect 幂等", total == 2 and saved == 0)

        done_n, new_n = fb.sync_mock(store, "u-a")
        chk("sync_mock 跳过未提交", done_n == 2 and new_n == 2)
        done_n, new_n = fb.sync_mock(store, "u-a")
        chk("sync_mock 幂等增量", done_n == 2 and new_n == 0)
        exams = store.mock_exam_list("u-a")
        chk("模考三卷落库（含 Store 层 1_1_x）", len(exams) == 3)
        m1 = [e for e in exams if e["exercise_key"] == "1_1_m1"][0]
        chk("模考统计提取", m1["score"] == 4.0 and m1["correct_count"] == 1
            and m1["wrong_count"] == 1 and m1["paper_id"] == "5500868"
            and m1["submit_time"].startswith("2026-"))
        d = store.mock_exam_detail("u-a", m1["id"])
        chk("模考作答明细", len(d["answers"]) == 2
            and {a["status"] for a in d["answers"]} == {1, -1})

        # ---------- API 端点 ----------
        r = client.post("/me/fenbi/sync", json={"kind": "bad"}, headers=h)
        chk("非法 kind 400", r.status_code == 400)
        r = client.post("/me/fenbi/sync", json={"kind": "wrong"})
        chk("未登录触发 401", r.status_code == 401)
        r = client.post("/me/fenbi/sync", json={"kind": "wrong"},
                        headers={"Authorization": "Bearer nope"})
        chk("未登录 401", r.status_code == 401)
        r = client.get("/me/fenbi/mock/exams")
        chk("未登录列表 401", r.status_code == 401)

        store.upsert_binding("u-b", "ck" * 20)   # u-b 有绑定（无 cookie 也行，已 mock）
        r = client.post("/me/fenbi/sync", json={"kind": "wrong"},
                        headers={"Authorization": "Bearer tok-nobody"})
        chk("未绑定 401（token 无效）", r.status_code == 401)

        # u-a 绑定着（upsert_binding 已建），正常触发同步（后台线程，mock api 快）
        r = client.post("/me/fenbi/sync", json={"kind": "collect"},
                        headers=h)
        chk("触发同步 200", r.status_code == 200 and r.json().get("ok"))
        r2 = client.post("/me/fenbi/sync", json={"kind": "collect"}, headers=h)
        chk("并发触发 429", r2.status_code == 429)
        import time as _t
        for _ in range(30):
            _t.sleep(0.4)
            r = client.get("/me/fenbi/sync/status?kind=collect", headers=h)
            lat = (r.json().get("latest") or {})
            if lat.get("status") in ("done", "failed"):
                break
        chk("同步完成 done", lat.get("status") == "done"
            and lat.get("total") == 2, str(lat))
        r = client.get("/me/fenbi/wrongs?limit=1", headers=h)
        chk("wrongs 端点", r.status_code == 200 and r.json()["total"] == 2)
        r = client.get("/me/fenbi/collects?limit=1", headers=h)
        chk("collects 端点", r.status_code == 200 and r.json()["total"] == 4)
        r = client.get("/me/fenbi/mock/exams", headers=h)
        chk("mock exams 端点", r.status_code == 200
            and len(r.json()["exams"]) == 3)
        eid0 = r.json()["exams"][0]["id"]
        r = client.get(f"/me/fenbi/mock/exams/{eid0}", headers=h)
        chk("mock detail 端点", r.status_code == 200
            and "userAnswers" not in (r.json().get("report") or {}))
        r = client.get("/me/fenbi/mock/exams/99999", headers=h)
        chk("不存在试卷 404", r.status_code == 404)
        r = client.get("/me/fenbi/binding", headers=h)
        bj = r.json().get("binding") or {}
        chk("binding 三计数", r.status_code == 200
            and bj.get("wrong_count") == 2 and bj.get("collect_count") == 4
            and bj.get("mock_count") == 3, json.dumps(bj, ensure_ascii=False))
    finally:
        (fb.fetch_error_tree, fb.fetch_collect_tree,
         fb.fetch_solutions_batch, fb.fetch_exercise_history,
         fb.fetch_mock_report, fb.fetch_mock_solution) = orig

    print()
    if fails:
        print(f"❌ 失败 {len(fails)} 项: {fails}")
        sys.exit(1)
    print("✅ 批次27 三大同步测试全部通过")


if __name__ == "__main__":
    _main()
