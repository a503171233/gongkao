"""批次28 存量修复：粉笔快照全量重采 + 系统错题本/收藏夹联动。

背景：批次17/27 落库时对 content/analysis 直接 strip HTML，<img> 变 [图]
占位（存量 3096/6699 条），且 solutions.material（资料分析材料）未采集。
本脚本对每个绑定学员全量重拉 solutions（走新管线：图片本地化落盘
data/qimg/fenbi/ + 材料采集），覆盖旧快照后联动系统错题本/收藏夹。

容器内执行（后台）：
    docker exec gongkao-backend python -m poc.fenbi_resync
幂等：快照按 (user_id, question_id) 覆盖；系统侧 ext_id 去重。
模考：清空已同步试卷记录后按 sync_mock 分批重拉（每轮 ≤20 份）。
"""

import sys
import time
import traceback

from . import fenbi_bind
from .api import study_store          # 复用 api 已初始化的 study store


def _log(msg: str) -> None:
    print(f"[fenbi-resync] {msg}", flush=True)


def _fetch_all_solutions(cookie: str, qids: list[str]) -> dict[str, dict]:
    """分批拉全量 solutions 详情（含限速），返回 {qid: raw}。"""
    out: dict[str, dict] = {}
    for i in range(0, len(qids), fenbi_bind._BATCH):
        chunk = qids[i:i + fenbi_bind._BATCH]
        for s in fenbi_bind.fetch_solutions_batch(cookie, chunk):
            if isinstance(s, dict) and s.get("id"):
                out[str(s["id"])] = s
        _log(f"  solutions {min(i + len(chunk), len(qids))}/{len(qids)}")
    return out


def _resync_kind(store, uid: str, cookie: str, kind: str) -> int:
    """错题/收藏单类重采：树 → 全量详情 → 覆盖旧快照。返回行数。"""
    tree = (fenbi_bind.fetch_error_tree(cookie) if kind == "wrong"
            else fenbi_bind.fetch_collect_tree(cookie))
    paths = fenbi_bind._tree_qid_paths(tree)
    qids = sorted(paths.keys())
    if not qids:
        _log(f"user={uid} {kind}: 树为空，跳过")
        return 0
    raw_by_id = _fetch_all_solutions(cookie, qids)
    rows = [fenbi_bind._sol_row(raw_by_id[q], q, paths)
            for q in qids if q in raw_by_id]
    tbl = ("fenbi_wrong_questions" if kind == "wrong"
           else "fenbi_collect_questions")
    with store._connect() as conn:
        for r in rows:   # 只删将覆盖的行（拉取失败的题保留旧快照兜底）
            conn.execute(
                f"DELETE FROM {tbl} WHERE user_id=? AND question_id=?",
                (uid, str(r["question_id"])))
    n = store.save_wrong_questions(uid, rows) if kind == "wrong" \
        else store.save_collect_questions(uid, rows)
    _log(f"user={uid} {kind}: 重采 {n}/{len(rows)} 行")
    return n


def resync_user(store, uid: str) -> None:
    cookie = store.get_cookie(uid)
    if not cookie:
        return
    _log(f"== user={uid} 开始 ==")
    try:
        _resync_kind(store, uid, cookie, "wrong")
        _resync_kind(store, uid, cookie, "collect")
        # 模考：清已同步记录 → 分批重拉（每轮 ≤20 份直到无新增）
        with store._connect() as conn:
            conn.execute(
                "DELETE FROM fenbi_mock_answers WHERE exam_row_id IN"
                " (SELECT id FROM fenbi_mock_exams WHERE user_id=?)", (uid,))
            conn.execute(
                "DELETE FROM fenbi_mock_exams WHERE user_id=?", (uid,))
        rounds = 0
        while rounds < 10:
            total, n = fenbi_bind.sync_mock(store, uid)
            _log(f"user={uid} mock: 第{rounds + 1}轮 +{n}（共 {total} 份）")
            rounds += 1
            if n == 0:
                break
        r = fenbi_bind.sync_into_system(store, study_store, uid)
        _log(f"user={uid} 联动：错题本 +{r['mistakes_new']}，"
             f"收藏夹 +{r['favorites_new']}")
    except fenbi_bind.fenbi.FenbiAuthError:
        _log(f"user={uid} 登录态失效，跳过（需用户重新绑定后再采）")
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _log(f"user={uid} 异常终止，继续下一用户")


def main() -> None:
    store = fenbi_bind.FenbiBindStore()
    with store._connect() as conn:
        bindings = [dict(r) for r in conn.execute(
            "SELECT user_id, status FROM fenbi_bindings").fetchall()]
    uids = [b["user_id"] for b in bindings if b.get("status") == "bound"]
    _log(f"待重采学员 {len(uids)} 人（bound）")
    t0 = time.time()
    for uid in uids:
        resync_user(store, uid)
    _log(f"全部完成，用时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
