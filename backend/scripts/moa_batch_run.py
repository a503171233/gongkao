# 批次26-I 收尾运维脚本：MoA 后台批量生产（发起 + 轮询 + 缓存表质量统计）
# 用法: docker exec gongkao-backend python3 /tmp/moa_batch_run.py [limit]
# 依赖: 管理员账号（脚本内临时提权 smoke26g → admin，结束撤回）
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:9000"
ADMIN = ("smoke26g", "Smk26g!x")
DEADLINE = 40 * 60  # 40 分钟硬上限（10 题 × ~4 分钟最坏）


def req(method, path, token=None, body=None, timeout=30):
    r = urllib.request.Request(BASE + path, method=method)
    if token:
        r.add_header("Authorization", "Bearer " + token)
    data = None
    if body is not None:
        r.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    try:
        resp = urllib.request.urlopen(r, data=data, timeout=timeout)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read() or b"{}"
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw.decode(errors="replace")[:200]}


def temp_admin():
    """临时提权 → 返回 token（结束须调用 undo）。"""
    sys.path.insert(0, "/app")
    from poc.auth import AuthStore
    store = AuthStore()  # 默认 /data/auth.db，勿传自定义路径
    u = store.get_user_by_username(ADMIN[0])
    store.set_role(u["user_id"], "admin")
    code, d = req("POST", "/login", body={"username": ADMIN[0], "password": ADMIN[1]})
    assert code == 200, f"登录失败 {code}: {d}"
    return d["token"], store, u["user_id"]


tok, store, uid = temp_admin()
try:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    code, d = req("POST", "/admin/ai-analysis/moa-batch", token=tok,
                  body={"limit": limit})
    print(f"[发起] {code}: {json.dumps(d, ensure_ascii=False)}", flush=True)
    if not d.get("started"):
        raise SystemExit(0)
    total = d["total"]
    t0, last_done = time.time(), -1
    results = []
    while time.time() - t0 < DEADLINE:
        time.sleep(15)
        code, d = req("GET", "/admin/ai-analysis/moa-batch/status", token=tok)
        if code != 200:
            print(f"[轮询] 异常 {code}", flush=True)
            continue
        results = d.get("results", [])
        if d["done"] != last_done:
            last_done = d["done"]
            print(f"[进度] {d['done']}/{d['total']}", flush=True)
        if not d["running"]:
            break
    ok_n = sum(1 for r in results if r["ok"])
    print(f"\n===== 批量结果: {ok_n}/{len(results)} 成功 =====", flush=True)
    for r in results:
        if not r["ok"]:
            print(f"失败 id={r['id']}: {r['err'][:100]}", flush=True)

    # 质量统计：从 ai_analysis 缓存表读本批新增（question_key 归一化匹配）
    sys.path.insert(0, "/app")
    from poc.study import _norm_question, _connect as _c  # noqa: F401
    conn = sqlite3.connect("file:/data/study.db?mode=ro", uri=True)
    cols = [x[1] for x in conn.execute("PRAGMA table_info(ai_analysis)")]
    moa = single = 0
    for r in results:
        if not r["ok"]:
            continue
        row = conn.execute(
            "SELECT content FROM ai_analysis WHERE question_key IN "
            "(SELECT question_key FROM ai_analysis) AND rowid=("
            "SELECT MAX(rowid) FROM ai_analysis WHERE question_key=("
            "SELECT question_key FROM question_bank WHERE id=?))",
            (r["id"],)).fetchone()
        if not row:
            continue
        p = (json.loads(row[0]) or {}).get("pipeline")
        moa += p == "moa"
        single += p == "single"
    print(f"pipeline=moa: {moa}  降级single: {single}", flush=True)
finally:
    store.set_role(uid, "free")
    print("[权限已撤回]", flush=True)
