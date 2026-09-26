# -*- coding: utf-8 -*-
"""批次17 粉笔账号绑定+错题采集+AI 提升计划：鉴权/绑定/管线/冷却/掩码断言。

运行：cd backend && python tests/test_fenbi_bind.py
"""
import json
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import poc.api as api
import poc.fenbi_bind as fb
from poc.fenbi import FenbiAuthError


def _main():
    tmp = Path(tempfile.mkdtemp(prefix="gk_fbbind_"))
    store = fb.FenbiBindStore(db_path=tmp / "fenbi_bind.db")

    def fake_resolve(authorization):
        t = (authorization or "").replace("Bearer ", "", 1).strip()
        if t == "tok-a":
            return {"user_id": "u-a", "username": "小明", "role": "free"}
        if t == "tok-b":
            return {"user_id": "u-b", "username": "小红", "role": "free"}
        if t == "tok-admin":
            return {"user_id": "u-admin", "username": "管理员", "role": "admin"}
        return None

    class FakeAuthStore:
        def user_from_token(self, token):
            m = {"tok-a": {"user_id": "u-a", "username": "小明", "role": "free"},
                 "tok-b": {"user_id": "u-b", "username": "小红", "role": "free"},
                 "tok-admin": {"user_id": "u-admin", "username": "管理员",
                               "role": "admin"}}
            return m.get((token or "").strip())

    orig = (api.fenbi_bind_store, api._resolve_user, api.auth_store)
    orig_check = fb.check_cookie
    orig_tree = fb.fetch_error_tree
    orig_sols = fb.fetch_solutions_batch
    api.fenbi_bind_store = store
    api._resolve_user = fake_resolve
    api.auth_store = FakeAuthStore()
    client = TestClient(api.app)
    fails = []

    def chk(name, cond):
        print(("  ✓ " if cond else "  ✗ ") + name)
        if not cond:
            fails.append(name)

    try:
        h_a = {"Authorization": "Bearer tok-a"}
        h_b = {"Authorization": "Bearer tok-b"}
        h_admin = {"Authorization": "Bearer tok-admin"}

        # ---- 鉴权 ----
        chk("binding 未登录 401",
            client.get("/me/fenbi/binding").status_code == 401)
        chk("cookie 绑定 未登录 401",
            client.post("/me/fenbi/cookie", json={"cookie": "x" * 30}).status_code == 401)
        chk("analyze 未登录 401",
            client.post("/me/fenbi/analyze").status_code == 401)
        chk("admin 列表 学员 403",
            client.get("/admin/fenbi/bindings", headers=h_a).status_code == 403)

        # ---- 空态 ----
        d = client.get("/me/fenbi/binding", headers=h_a).json()
        chk("空态 binding=None", d["binding"] is None and d["latest"] is None)

        # ---- Cookie 绑定（mock 校验） ----
        seen_cookies = []
        fb.check_cookie = lambda c: (seen_cookies.append(c) or
                                     {"ok": True, "total_mistakes": 42})
        r = client.post("/me/fenbi/cookie",
                        json={"cookie": "sess=ABC123; userid=9527;x=1"},
                        headers=h_a)
        chk("绑定成功 200", r.status_code == 200)
        chk("校验收到完整 cookie", seen_cookies and "sess=ABC123" in seen_cookies[0])
        chk("返回错题数", r.json().get("total_mistakes") == 42)
        d = client.get("/me/fenbi/binding", headers=h_a).json()
        chk("状态 bound", d["binding"]["status"] == "bound")
        chk("掩码回显末4位", d["binding"]["cookie_masked"].endswith("x=1"[-4:])
            and "ABC123" not in json.dumps(d))
        chk("cookie 全文不回显", "sess=ABC123" not in json.dumps(d))

        fb.check_cookie = lambda c: (_ for _ in ()).throw(FenbiAuthError("bad"))
        r = client.post("/me/fenbi/cookie",
                        json={"cookie": "sess=BAD; userid=1;x=1"}, headers=h_b)
        chk("无效 cookie 400", r.status_code == 400 and "无效" in r.json()["detail"])

        # ---- analyze 未绑定拒绝 / 绑定后触发 ----
        r = client.post("/me/fenbi/analyze", headers=h_b)
        chk("未绑定 analyze 400", r.status_code == 400)

        calls = {"n": 0}
        orig_tree = fb.fetch_error_tree
        orig_sols = fb.fetch_solutions_batch
        fake_tree = [
            {"id": 1, "name": "言语理解与表达", "questionIds": [101, 102, 103],
             "children": [{"name": "逻辑填空", "questionIds": [101, 102]},
                          {"name": "片段阅读", "questionIds": [103]}]},
            {"id": 2, "name": "数量关系", "questionIds": [201],
             "children": [{"name": "数学运算", "questionIds": [201]}]},
        ]
        fake_sols = {
            "101": {"id": 101, "content": "<p>题干101</p>", "difficulty": 7,
                    "correctAnswer": {"choice": "2"},
                    "accessories": [{"type": 101, "options": ["A1", "B1", "C1", "D1"]}],
                    "solution": "<p>解析101</p>", "keypoints": [{"name": "实词辨析"}],
                    "source": "2026国考"},
            "102": {"id": 102, "content": "<p>题干102</p>", "difficulty": 3,
                    "correctAnswer": {"choice": "0"},
                    "accessories": [{"type": 101, "options": ["A2", "B2", "C2", "D2"]}],
                    "solution": "<p>解析102</p>", "keypoints": [{"name": "成语辨析"}],
                    "source": "2026国考"},
            "103": {"id": 103, "content": "<p>题干103</p>", "difficulty": 5,
                    "correctAnswer": {"choice": "1"},
                    "accessories": [{"type": 101, "options": ["A3", "B3", "C3", "D3"]}],
                    "solution": "<p>解析103</p>", "keypoints": [{"name": "中心理解"}],
                    "source": "2026国考"},
            "201": {"id": 201, "content": "<p>题干201</p>", "difficulty": 9,
                    "correctAnswer": {"choice": "3"},
                    "accessories": [{"type": 101, "options": ["A4", "B4", "C4", "D4"]}],
                    "solution": "<p>解析201</p>", "keypoints": [{"name": "行程问题"}],
                    "source": "2025国考"},
        }
        fb.fetch_error_tree = lambda c: fake_tree
        fb.fetch_solutions_batch = lambda c, ids, interval=2.0: (
            calls.__setitem__("n", calls["n"] + 1) or
            [fake_sols[str(i)] for i in ids if str(i) in fake_sols])
        fb.check_cookie = orig_check  # 恢复（analyze 不走 check）

        fake_report = {
            "summary": "言语是主要失分模块，数量关系薄弱。",
            "weak_points": [{"name": "实词辨析", "module": "言语理解与表达",
                             "level": 4, "reason": "语境分析不足"}],
            "advice": ["每日 20 题逻辑填空限时训练"],
            "weeks": [{"theme": "基础巩固", "goal": "言语正确率 60%→70%",
                       "tasks": [{"slot": "周一晚", "title": "逻辑填空 20 题",
                                  "detail": "限时 25 分钟+错题复盘", "minutes": 45}]}],
        }
        llm_prompts = []

        def fake_llm(messages):
            llm_prompts.append(messages[0]["content"])
            return json.dumps(fake_report, ensure_ascii=False)

        # 管线同步直跑（不起线程，便于断言）
        aid, err = store.start_analysis("u-a")
        chk("任务创建", aid > 0 and not err)
        fb.run_analysis(store, "u-a", aid, fake_llm)
        a = store.latest_analysis("u-a")
        chk("管线 done", a["status"] == "done" and a["progress"] == 100)
        chk("错题总数 4", a["total_mistakes"] == 4)
        chk("快照 4 题", store.wrong_count("u-a") == 4)
        rep = json.loads(a["report"])
        chk("任务书结构", rep["summary"].startswith("言语")
            and len(rep["weeks"]) == 1 and rep["weak_points"][0]["level"] == 4)
        chk("prompt 含分布与样本", "言语理解与表达" in llm_prompts[0]
            and "行程问题" in llm_prompts[0])
        snap = {r["question_id"]: r for r in map(dict, _rows(store, "u-a"))}
        chk("快照字段", snap["101"]["answer"] == "C"
            and snap["101"]["keypoint"] == "逻辑填空"
            and snap["101"]["module"] == "言语理解与表达"
            and snap["102"]["answer"] == "A"
            and snap["102"]["keypoint"] == "逻辑填空"
            and snap["101"]["options"].startswith('["A1"'))
        chk("难度折算 10→5", snap["201"]["difficulty"] == 5
            and snap["102"]["difficulty"] == 2)

        # ---- 冷却 + 单 running ----
        r = client.post("/me/fenbi/analyze", headers=h_a)
        chk("冷却 6h 内 429", r.status_code == 429 and "6 小时" in r.json()["detail"])
        with store._connect() as conn:
            conn.execute(
                "INSERT INTO fenbi_analyses(user_id,status,stage,progress,created_at)"
                " VALUES('u-b','running','',5,datetime('now','localtime'))")
        r = client.post("/me/fenbi/analyze", headers=h_b)
        # u-b 未绑定先 400，先绑定再测 running 拒绝
        fb.check_cookie = lambda c: {"ok": True, "total_mistakes": 1}
        client.post("/me/fenbi/cookie",
                    json={"cookie": "sess=BBB; userid=2;x=1"}, headers=h_b)
        r = client.post("/me/fenbi/analyze", headers=h_b)
        chk("running 中拒绝 429", r.status_code == 429 and "进行中" in r.json()["detail"])

        # ---- latest 端点 ----
        d = client.get("/me/fenbi/analysis/latest", headers=h_a).json()
        chk("latest 返回报告", d["latest"]["status"] == "done"
            and d["latest"]["report"]["summary"].startswith("言语")
            and isinstance(d["latest"]["distribution"], list))
        chk("latest wrong_count", d["latest"]["wrong_count"] == 4)

        # ---- 401 → expired ----
        def _boom(cookie):
            raise FenbiAuthError("login expired")
        fb.fetch_error_tree = _boom
        with store._connect() as conn:
            conn.execute("DELETE FROM fenbi_analyses WHERE user_id='u-b'"
                         " AND status='running'")
        aid2, _ = store.start_analysis("u-b")
        fb.run_analysis(store, "u-b", aid2, fake_llm)
        b = store.get_binding("u-b")
        chk("失效置 expired", b["status"] == "expired")
        a2 = store.latest_analysis("u-b")
        chk("失败任务记录错误", a2["status"] == "failed"
            and "失效" in a2["error"])

        # ---- 管理端列表掩码 ----
        d = client.get("/admin/fenbi/bindings", headers=h_admin).json()
        chk("admin 列表 2 条", len(d["bindings"]) == 2)
        chk("列表无 cookie 全文", all("cookie" not in b
                                     for b in d["bindings"]))

        # ---- 掩码函数 ----
        chk("手机掩码", fb.mask_phone("13812345678") == "138****5678")
        chk("cookie 掩码", fb.mask_cookie("abcdefghijklmnop") == "…mnop")
    finally:
        api.fenbi_bind_store, api._resolve_user, api.auth_store = orig
        fb.check_cookie = orig_check
        fb.fetch_error_tree = orig_tree
        fb.fetch_solutions_batch = orig_sols

    print(f"\n结果：{'全部通过' if not fails else f'失败 {len(fails)} 项: {fails}'}")
    sys.exit(1 if fails else 0)


def _rows(store, user_id):
    with store._connect() as conn:
        return conn.execute(
            "SELECT * FROM fenbi_wrong_questions WHERE user_id=?",
            (user_id,)).fetchall()


if __name__ == "__main__":
    _main()
