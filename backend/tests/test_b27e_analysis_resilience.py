# -*- coding: utf-8 -*-
"""批次27-E 提升计划（AI 任务书）生成失败根因修复：截断修复/空输出回退/降级统计报告/僵死任务清理。

失败三根因（生产日志实锤）：
  ① eff 通道空输出（flash 模型长文 JSON 场景 HTTP 200 但 content=''）
  ② 回退全局通道 api_key 未配置 → openai「Missing credentials」
  ③ LLM 输出超 max_tokens 截断 → JSONDecodeError「AI 批改返回格式异常」
修复：空串=失败继续回退 / 截断 JSON 修复器 / AI 两跑全败降级本地统计报告（done+error）
     / running 超 30min 僵死自动放行 / 降级版不进 6h 冷却。

运行：cd backend && python tests/test_b27e_analysis_resilience.py
"""
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import poc.api as api
import poc.fenbi_bind as fb
import poc.llm as llm_biz


def _main():
    fails = []

    def chk(name, cond):
        print(("  ✓ " if cond else "  ✗ ") + name)
        if not cond:
            fails.append(name)

    # ================= 1. 截断 JSON 修复器 =================
    rep = api._repair_truncated_json
    r = rep('{"summary": "言语是薄弱模块", "advice": ["每天练')
    chk("截断修复 字符串+对象", isinstance(r, dict) and r.get("summary") == "言语是薄弱模块"
        and r.get("advice") == ["每天练"])
    r = rep('{"weak_points": [{"name": "逻辑填空", "reason": "错题多')
    chk("截断修复 嵌套数组", isinstance(r, dict) and r["weak_points"][0]["name"] == "逻辑填空")
    r = rep('{"summary": "诊断", "weeks": [{"theme": "基础期"')
    chk("截断修复 数组内对象", isinstance(r, dict) and r["weeks"][0]["theme"] == "基础期")
    r = rep('{"summary": "完好", "advice": ["a", "b"], "weeks": []}')
    chk("完整 JSON 原样解析", isinstance(r, dict) and r["summary"] == "完好")
    chk("垃圾文本不硬修", rep("这是纯文本不是 JSON") is None)
    r = rep('{"summary": "尾部逗号", "advice": ["a",')
    chk("尾部悬挂逗号回退", isinstance(r, dict) and r.get("advice") == ["a"])

    # ================= 2. 降级统计报告 =================
    dist = [
        {"module": "言语理解与表达", "total": 120, "points": []},
        {"module": "数量关系", "total": 60, "points": []},
        {"module": "判断推理", "total": 30, "points": []},
    ]
    fr = fb._fallback_report(dist, {}, 210)
    chk("降级报告 结构完整",
        all(k in fr for k in ("summary", "weak_points", "advice", "weeks"))
        and isinstance(fr["weak_points"], list) and isinstance(fr["advice"], list))
    chk("降级报告 summary 含总量与 top 模块",
        "210" in fr["summary"] and "言语理解与表达" in fr["summary"]
        and "基础统计版" in fr["summary"])
    chk("降级报告 薄弱点排序与占比",
        fr["weak_points"][0]["name"] == "言语理解与表达"
        and fr["weak_points"][0]["level"] == 5
        and "57%" in fr["weak_points"][0]["reason"] or "58%" in fr["weak_points"][0]["reason"])
    chk("降级报告 周计划可渲染",
        all("theme" in w and "goal" in w and "tasks" in w for w in fr["weeks"]))

    # ================= 3. run_analysis 弹性管线 =================
    tmp = Path(tempfile.mkdtemp(prefix="gk_b27e_"))
    store = fb.FenbiBindStore(db_path=tmp / "fenbi_bind.db")
    fake_tree = [
        {"id": 1, "name": "言语理解与表达", "questionIds": [101, 102],
         "children": [{"name": "逻辑填空", "questionIds": [101, 102]}]},
        {"id": 2, "name": "数量关系", "questionIds": [201],
         "children": [{"name": "数学运算", "questionIds": [201]}]},
    ]
    fake_sols = {
        "101": {"id": 101, "content": "<p>t101</p>", "difficulty": 7,
                "correctAnswer": {"choice": "2"},
                "accessories": [{"type": 101, "options": ["A", "B", "C", "D"]}],
                "solution": "<p>s101</p>", "keypoints": [{"name": "实词辨析"}]},
        "102": {"id": 102, "content": "<p>t102</p>", "difficulty": 3,
                "correctAnswer": {"choice": "0"},
                "accessories": [{"type": 101, "options": ["A", "B", "C", "D"]}],
                "solution": "<p>s102</p>", "keypoints": [{"name": "成语辨析"}]},
        "201": {"id": 201, "content": "<p>t201</p>", "difficulty": 9,
                "correctAnswer": {"choice": "3"},
                "accessories": [{"type": 101, "options": ["A", "B", "C", "D"]}],
                "solution": "<p>s201</p>", "keypoints": [{"name": "行程问题"}]},
    }
    orig_tree, orig_sols = fb.fetch_error_tree, fb.fetch_solutions_batch
    fb.fetch_error_tree = lambda c: fake_tree
    fb.fetch_solutions_batch = lambda c, ids, interval=2.0: [
        fake_sols[str(i)] for i in ids if str(i) in fake_sols]

    good_report = {"summary": "言语薄弱，需专项突破。",
                   "weak_points": [{"name": "实词辨析", "module": "言语理解与表达",
                                    "level": 4, "reason": "语境分析不足"}],
                   "advice": ["每日 20 题限时"], "weeks": []}

    # 3a. AI 全败 → 降级 done + 兜底报告
    def llm_always_fail(messages):
        return ""   # 模拟 flash 空输出
    aid, err = store.start_analysis("u-x")
    fb.run_analysis(store, "u-x", aid, llm_always_fail)
    a = store.latest_analysis("u-x")
    rep_x = json.loads(a["report"])
    chk("AI 全败 仍 done", a["status"] == "done" and a["progress"] == 100)
    chk("降级报告落库", rep_x["summary"] and "基础统计版" in rep_x["summary"])
    chk("降级标记 error", a["error"] and "降级" in a["error"])

    # 3b. 首次空输出 → 重试成功 → 正常报告 + 无降级标记
    calls = {"n": 0}
    def llm_empty_then_good(messages):
        calls["n"] += 1
        return "" if calls["n"] == 1 else json.dumps(good_report, ensure_ascii=False)
    aid2, _ = store.start_analysis("u-y")
    fb.run_analysis(store, "u-y", aid2, llm_empty_then_good)
    a2 = store.latest_analysis("u-y")
    rep_y = json.loads(a2["report"])
    chk("空输出自动重试", calls["n"] == 2 and a2["status"] == "done")
    chk("重试成功 正常报告无降级", rep_y["summary"] == "言语薄弱，需专项突破。"
        and not (a2["error"] or "").strip())

    # 3c. 首次截断 JSON → 重试成功
    calls2 = {"n": 0}
    def llm_truncated_then_good(messages):
        calls2["n"] += 1
        if calls2["n"] == 1:
            return '{"summary": "被截断的任务书", "advice": ["每天练'
        return json.dumps(good_report, ensure_ascii=False)
    aid3, _ = store.start_analysis("u-z")
    fb.run_analysis(store, "u-z", aid3, llm_truncated_then_good)
    a3 = store.latest_analysis("u-z")
    rep_z = json.loads(a3["report"])
    chk("截断输出自动重试", calls2["n"] == 2
        and rep_z["summary"] == "言语薄弱，需专项突破。")

    # 3d. 重试仍截断 → 修复器抢救缩水版（有 summary 即可用，不降级）
    def llm_truncated_forever(messages):
        return '{"summary": "永远被截断", "advice": ["x'
    aid4, _ = store.start_analysis("u-w")
    fb.run_analysis(store, "u-w", aid4, llm_truncated_forever)
    a4 = store.latest_analysis("u-w")
    rep_w = json.loads(a4["report"])
    chk("重试仍截断 修复器抢救", a4["status"] == "done"
        and rep_w["summary"] == "永远被截断" and rep_w["advice"] == ["x"])

    # 3e. 输出纯垃圾（无任何 JSON）→ 降级统计版 + error 标记
    def llm_garbage(messages):
        return "抱歉，我无法完成这个任务。"
    aid5, _ = store.start_analysis("u-v")
    fb.run_analysis(store, "u-v", aid5, llm_garbage)
    a5 = store.latest_analysis("u-v")
    rep_v = json.loads(a5["report"])
    chk("纯垃圾输出 降级兜底", a5["status"] == "done"
        and "基础统计版" in rep_v["summary"] and a5["error"])

    fb.fetch_error_tree, fb.fetch_solutions_batch = orig_tree, orig_sols

    # ================= 4. 僵死任务清理 + 冷却语义 =================
    # 4a. running 刚创建 → 拒绝
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO fenbi_analyses(user_id,status,stage,progress,created_at)"
            " VALUES('u-dead','running','拉取',40,datetime('now','localtime'))")
    aid_new, err_new = store.start_analysis("u-dead")
    chk("新 running 拒绝", aid_new == 0 and "进行中" in err_new)
    # 4b. running 31 分钟前 → 僵死放行 + 旧任务置 failed
    with store._connect() as conn:
        conn.execute(
            "UPDATE fenbi_analyses SET created_at=datetime('now','localtime',"
            "'-31 minutes') WHERE user_id='u-dead' AND status='running'")
    aid_dead, err_dead = store.start_analysis("u-dead")
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT status FROM fenbi_analyses WHERE user_id='u-dead'"
            " ORDER BY id").fetchall()
    old_status = [dict(r)["status"] for r in rows][0]
    chk("僵死 running 放行", aid_dead > 0 and not err_dead)
    chk("僵死任务置 failed", old_status == "failed")

    # 4c. 降级 done（带 error）不进冷却，可立即重试
    aid_r1, err_r1 = store.start_analysis("u-x")   # u-x 上轮是降级 done
    chk("降级 done 不进冷却", aid_r1 > 0 and not err_r1)
    # 4d. 正常 done 进冷却（u-y 成功报告）
    aid_y2, err_y2 = store.start_analysis("u-y")
    chk("正常 done 6h 冷却", aid_y2 == 0 and "6 小时" in err_y2)

    # ================= 5. _grade_llm_call 空输出回退 =================
    orig_eff = api._get_aimodel
    orig_call = llm_biz.call_llm
    class FakeModel:
        def effective_default(self):
            return {"model_id": "flash-x", "base_url": "http://eff/v1",
                    "api_key": "sk-eff", "temperature": 0.3}
    api._get_aimodel = lambda: FakeModel()
    seen = []
    def fake_call_llm(messages, tcfg, max_tokens=2000, model=None,
                      temperature=None, base_url=None, api_key=None):
        seen.append({"model": model, "key": api_key})
        if model == "flash-x":
            return ""          # eff 通道空输出
        return "全局通道正常输出"
    llm_biz.call_llm = fake_call_llm
    try:
        out = api._grade_llm_call([{"role": "user", "content": "hi"}],
                                  api.cfg.get_teacher("T001"), max_tokens=100)
        chk("eff 空输出回退全局", out == "全局通道正常输出"
            and [s["model"] for s in seen] == ["flash-x", None])
    finally:
        api._get_aimodel = orig_eff
        llm_biz.call_llm = orig_call

    # 5b. 全局通道 key 未配置 → 快速失败清晰文案
    class FakeModelNoEff:
        def effective_default(self):
            return {}
    api._get_aimodel = lambda: FakeModelNoEff()
    def fake_call_nokey(messages, tcfg, **kw):
        raise ValueError("LLM API Key 未配置：请在管理后台「AI 模型管理」设置可用默认模型，"
                         "或在 .env 配置全局 API_KEY 后重启服务")
    llm_biz.call_llm = fake_call_nokey
    try:
        try:
            api._grade_llm_call([{"role": "user", "content": "hi"}],
                                api.cfg.get_teacher("T001"))
            chk("无 key 清晰报错", False)
        except ValueError as e:
            chk("无 key 清晰报错", "API Key 未配置" in str(e))
    finally:
        api._get_aimodel = orig_eff
        llm_biz.call_llm = orig_call

    print()
    if fails:
        print(f"❌ {len(fails)} 项失败：{fails}")
        sys.exit(1)
    print("✅ 批次27-E 提升计划弹性修复测试全部通过")


if __name__ == "__main__":
    _main()
