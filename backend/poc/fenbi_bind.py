#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""粉笔账号绑定 + 错题采集 + AI 提升计划任务书（批次17）。

学员在前端自助绑定自己的粉笔账号（验证码/扫码 playwright 会话见
poc/fenbi_login.py；Cookie 粘贴兜底在本模块），系统按学员隔离采集其
错题（keypoint-tree 分布 + solutions 详情快照），再调用 LLM 生成专属
提升计划任务书。

版权红线：学员错题可能含粉笔付费内容 → 仅存本模块学员隔离快照表
（fenbi_wrong_questions，UNIQUE(user_id, question_id)），绝不写入
question_bank 公共题库。

接口前提（2026-09-26 实测）：
- GET tiku.fenbi.com/api/xingce/errors/keypoint-tree?timeRange=0&order=desc
  &examcatid=1000183 → [{name, questionIds[], children:[...]}]（两级树）
- GET tiku.fenbi.com/api/xingce/solutions?app=web&kav=12&ver=3.88&ids=（≤50/批）
- keypoint-tree / solutions 实测配额宽松（与 getExercise 不同服务）
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from .config import Config
from . import fenbi

# 粉笔行测（国考）exam category；keypoint-tree/solutions 定位参数
_EXAMCATID = 1000183
_KEYPOINT_TREE_URL = (
    "https://tiku.fenbi.com/api/xingce/errors/keypoint-tree"
    "?timeRange=0&order=desc&app=web&examcatid=%d" % _EXAMCATID)
_SOLUTIONS_URL = ("https://tiku.fenbi.com/api/xingce/solutions"
                  "?app=web&kav=12&ver=3.88&ids={ids}")

ANALYSIS_COOLDOWN_SECS = 6 * 3600   # 生成冷却（防滥用 + LLM 成本）
_BATCH = 50                          # solutions 单批题数上限（粉笔硬限）


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def mask_cookie(cookie: str) -> str:
    """Cookie 掩码：只露末 4 位。"""
    c = str(cookie or "")
    return f"…{c[-4:]}" if len(c) > 8 else ("已配置" if c else "")


def mask_phone(phone: str) -> str:
    p = re.sub(r"\D", "", str(phone or ""))
    return f"{p[:3]}****{p[-4:]}" if len(p) >= 7 else (p or "")


# ---------------- 存储 ----------------

class FenbiBindStore:
    """绑定/错题快照/分析记录/登录会话 四表（独立 db，学员数据隔离）。"""

    def __init__(self, db_path: str | Path | None = None):
        default = Config.data_dir / "fenbi_bind.db"
        self.db_path = Path(db_path or os.environ.get("FENBI_BIND_DB") or default)
        if not self.db_path.is_absolute():
            self.db_path = Config.ROOT / self.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS fenbi_bindings (
                user_id       TEXT PRIMARY KEY,
                cookie        TEXT NOT NULL DEFAULT '',
                status        TEXT NOT NULL DEFAULT 'none', -- none/bound/expired
                phone         TEXT NOT NULL DEFAULT '',     -- 掩码
                bound_at      TEXT,
                last_check_at TEXT,
                last_check_ok INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS fenbi_wrong_questions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                question_id TEXT NOT NULL,
                module      TEXT NOT NULL DEFAULT '',
                keypoint    TEXT NOT NULL DEFAULT '',
                content     TEXT NOT NULL DEFAULT '',
                options     TEXT NOT NULL DEFAULT '[]',
                answer      TEXT NOT NULL DEFAULT '',
                analysis    TEXT NOT NULL DEFAULT '',
                difficulty  INTEGER NOT NULL DEFAULT 0,
                source      TEXT NOT NULL DEFAULT '',
                created_at  TEXT,
                UNIQUE(user_id, question_id)
            );
            CREATE INDEX IF NOT EXISTS idx_fwq_user
                ON fenbi_wrong_questions(user_id);
            CREATE TABLE IF NOT EXISTS fenbi_analyses (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT 'running', -- running/done/failed
                stage          TEXT NOT NULL DEFAULT '',
                progress       INTEGER NOT NULL DEFAULT 0,
                total_mistakes INTEGER NOT NULL DEFAULT 0,
                distribution   TEXT NOT NULL DEFAULT '[]',      -- JSON 分布树
                report         TEXT NOT NULL DEFAULT '',        -- LLM 任务书 JSON
                error          TEXT NOT NULL DEFAULT '',
                created_at     TEXT,
                done_at        TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_fa_user
                ON fenbi_analyses(user_id, created_at);
            CREATE TABLE IF NOT EXISTS fenbi_login_sessions (
                sid        TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                mode       TEXT NOT NULL DEFAULT 'sms',          -- sms/qrcode
                state      TEXT NOT NULL DEFAULT 'init',
                phone      TEXT NOT NULL DEFAULT '',             -- 掩码
                shot       TEXT NOT NULL DEFAULT '',             -- base64 截图
                error      TEXT NOT NULL DEFAULT '',
                created_at TEXT,
                updated_at TEXT
            );
            """)

    # ---- bindings ----

    def get_binding(self, user_id: str) -> dict | None:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM fenbi_bindings WHERE user_id=?",
                (user_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        cookie = d.pop("cookie", "")              # cookie 永不回显全文
        d["cookie_masked"] = mask_cookie(cookie)
        return d

    def get_cookie(self, user_id: str) -> str:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT cookie FROM fenbi_bindings WHERE user_id=?",
                (user_id,)).fetchone()
        return str(r["cookie"]) if r else ""

    def upsert_binding(self, user_id: str, cookie: str,
                       phone: str = "", status: str = "bound",
                       check_ok: int = 1) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO fenbi_bindings(user_id, cookie, status, phone,"
                " bound_at, last_check_at, last_check_ok)"
                " VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET cookie=excluded.cookie,"
                " status=excluded.status, phone=excluded.phone,"
                " bound_at=excluded.bound_at,"
                " last_check_at=excluded.last_check_at,"
                " last_check_ok=excluded.last_check_ok",
                (user_id, cookie, status, mask_phone(phone),
                 _now(), _now(), check_ok))

    def set_binding_status(self, user_id: str, status: str,
                           check_ok: int | None = None) -> None:
        with self._connect() as conn:
            if check_ok is None:
                conn.execute(
                    "UPDATE fenbi_bindings SET status=? WHERE user_id=?",
                    (status, user_id))
            else:
                conn.execute(
                    "UPDATE fenbi_bindings SET status=?, last_check_at=?,"
                    " last_check_ok=? WHERE user_id=?",
                    (status, _now(), check_ok, user_id))

    def list_bindings(self) -> list[dict]:
        """管理端：仅掩码，不含 cookie 全文。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id, status, phone, bound_at, last_check_at,"
                " last_check_ok, length(cookie) AS cookie_len"
                " FROM fenbi_bindings ORDER BY bound_at DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d.pop("cookie_len", None)
            out.append(d)
        return out

    # ---- wrong questions ----

    def save_wrong_questions(self, user_id: str,
                             rows: list[dict]) -> int:
        """INSERT OR IGNORE；rows: {question_id, module, keypoint, content,
        options(list), answer, analysis, difficulty, source}。"""
        n = 0
        with self._connect() as conn:
            for r in rows:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO fenbi_wrong_questions"
                    "(user_id, question_id, module, keypoint, content,"
                    " options, answer, analysis, difficulty, source,"
                    " created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (user_id, str(r.get("question_id") or ""),
                     str(r.get("module") or ""),
                     str(r.get("keypoint") or ""),
                     str(r.get("content") or ""),
                     json.dumps(r.get("options") or [], ensure_ascii=False),
                     str(r.get("answer") or ""),
                     str(r.get("analysis") or ""),
                     int(r.get("difficulty") or 0),
                     str(r.get("source") or ""), _now()))
                n += cur.rowcount
        return n

    def wrong_count(self, user_id: str) -> int:
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM fenbi_wrong_questions WHERE user_id=?",
                (user_id,)).fetchone()[0]

    def wrong_ids(self, user_id: str) -> set[str]:
        with self._connect() as conn:
            return {str(r[0]) for r in conn.execute(
                "SELECT question_id FROM fenbi_wrong_questions"
                " WHERE user_id=?", (user_id,)).fetchall()}

    # ---- analyses ----

    def start_analysis(self, user_id: str) -> tuple[int, str]:
        """新建分析任务；返回 (id, error)。冷却 6h + 每用户单 running。"""
        with self._connect() as conn:
            run = conn.execute(
                "SELECT id FROM fenbi_analyses WHERE user_id=? AND"
                " status='running' ORDER BY id DESC LIMIT 1",
                (user_id,)).fetchone()
            if run:
                return 0, "已有分析在进行中，请稍候"
            last = conn.execute(
                "SELECT created_at FROM fenbi_analyses WHERE user_id=? AND"
                " status='done' ORDER BY id DESC LIMIT 1",
                (user_id,)).fetchone()
            if last and last["created_at"]:
                try:
                    dt = datetime.strptime(last["created_at"],
                                           "%Y-%m-%d %H:%M:%S")
                    if time.time() - dt.timestamp() < ANALYSIS_COOLDOWN_SECS:
                        return 0, "距上次生成不足 6 小时，可先查看现有任务书"
                except ValueError:
                    pass
            cur = conn.execute(
                "INSERT INTO fenbi_analyses(user_id, status, stage, progress,"
                " created_at) VALUES(?, 'running', '准备中', 5, ?)",
                (user_id, _now()))
            return int(cur.lastrowid), ""

    def update_analysis(self, aid: int, **kw) -> None:
        if not kw:
            return
        cols, vals = [], []
        for k in ("status", "stage", "progress", "total_mistakes",
                  "distribution", "report", "error", "done_at"):
            if k in kw:
                cols.append(f"{k}=?")
                vals.append(kw[k])
        vals.append(aid)
        with self._connect() as conn:
            conn.execute(f"UPDATE fenbi_analyses SET {','.join(cols)}"
                         " WHERE id=?", vals)

    def latest_analysis(self, user_id: str) -> dict | None:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM fenbi_analyses WHERE user_id=?"
                " ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
        return dict(r) if r else None

    # ---- login sessions ----

    def save_session(self, sid: str, user_id: str, mode: str,
                     state: str, phone: str = "", shot: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO fenbi_login_sessions(sid, user_id, mode, state,"
                " phone, shot, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)"
                " ON CONFLICT(sid) DO UPDATE SET state=excluded.state,"
                " phone=excluded.phone,"
                " shot=CASE WHEN excluded.shot='' THEN fenbi_login_sessions.shot"
                " ELSE excluded.shot END,"
                " error='', updated_at=excluded.updated_at",
                (sid, user_id, mode, state, mask_phone(phone), shot,
                 _now(), _now()))

    def update_session(self, sid: str, **kw) -> None:
        cols, vals = [], []
        for k in ("state", "shot", "error", "phone"):
            if k in kw:
                v = kw[k]
                if k == "phone":
                    v = mask_phone(v)
                cols.append(f"{k}=?")
                vals.append(v)
        if not cols:
            return
        cols.append("updated_at=?")
        vals.append(_now())
        vals.append(sid)
        with self._connect() as conn:
            conn.execute(f"UPDATE fenbi_login_sessions SET {','.join(cols)}"
                         " WHERE sid=?", vals)

    def get_session(self, sid: str) -> dict | None:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM fenbi_login_sessions WHERE sid=?",
                             (sid,)).fetchone()
        return dict(r) if r else None

    def clear_session_shot(self, sid: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE fenbi_login_sessions SET shot='' WHERE sid=?",
                         (sid,))


# ---------------- 粉笔接口（学员 Cookie 参数化，复用 fenbi._get_json） ----------------

def check_cookie(cookie: str) -> dict:
    """校验 Cookie 有效性：拉错题分布树（一次请求同时拿错题数）。
    失效抛 fenbi.FenbiAuthError；网络异常抛原异常。"""
    tree = fetch_error_tree(cookie)
    total = len({str(q) for n in tree
                 for q in (n.get("questionIds") or [])})
    return {"ok": True, "total_mistakes": total}


def fetch_error_tree(cookie: str) -> list:
    """错题知识点两级树：[{name, id, questionIds[], children:[...]}]。
    裸 key URL 必带 examcatid 定位参数（缺则 404，同 getExercise 深坑）。"""
    d = fenbi._get_json(_KEYPOINT_TREE_URL, cookie, 2.0, None,
                        referer="https://spa.fenbi.com/")
    return d if isinstance(d, list) else []


def fetch_solutions_batch(cookie: str, ids: list[str],
                          interval: float = 2.0) -> list[dict]:
    """错题详情（≤50/批）：content/correctAnswer/solution/difficulty/keypoints。"""
    url = _SOLUTIONS_URL.format(ids=",".join(str(i) for i in ids))
    d = fenbi._get_json(url, cookie, interval, None,
                        referer="https://spa.fenbi.com/")
    if isinstance(d, list):
        return d
    return d.get("solutions") or []


# ---------------- 批次26-G · 粉笔搜题（答案核对） ----------------

def _norm_stem(text: str) -> str:
    """题干匹配归一化：去图片占位/残留选项标记/空白/标点，只留可比对核心字符。"""
    import re
    t = re.sub(r"\[[^\]]{0,6}\]", "", text or "")      # [图] 等占位
    # 独立选项标记（" A. " 行首/空格后的字母+明确标点），正文普通字母不受影响
    t = re.sub(r"(?:^|\s)[A-DＡ-Ｄ][.、．:：]\s*", "", t)
    t = re.sub(r"[\s\u3000]+", "", t)
    t = re.sub(r"[，。；：、,. ;:;（）()\"'‘’“”？！?~～!！【】\[\]「」『』…·-]+", "", t)
    return t


def search_similar_local(store: "FenbiBindStore", stem: str,
                         top: int = 3, min_sim: float = 0.45) -> list[dict]:
    """本地粉笔题库相似题匹配（零网络零风控）：全库错题快照按归一化题干
    difflib 相似度排序取 top。两段式加速：前 12 字签名预筛 → 精算相似度。
    返回 [{question_id, content, options, answer, analysis, source,
           difficulty, similarity}]，按相似度降序。"""
    import difflib
    q = _norm_stem(stem)
    if len(q) < 8:
        return []
    pre = q[:12]
    cands: list[tuple[str, sqlite3.Row]] = []
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT question_id, content, options, answer, analysis, source, "
            "difficulty FROM fenbi_wrong_questions").fetchall()
    for r in rows:
        n = _norm_stem(r["content"])
        if not n:
            continue
        # 预筛：签名重合（前 12 字任一子串命中）才进精算，全库 2233 行毫秒级
        if pre[:6] not in n and n[:6] not in q and pre not in n and n[:12] not in q:
            common = len(set(pre) & set(n[:24]))
            if common < max(3, len(set(pre)) // 2):
                continue
        cands.append((n, r))
    out: list[dict] = []
    for n, r in cands[:400]:          # 精算上限：防极端长文本拖慢
        sim = difflib.SequenceMatcher(None, q, n).ratio()
        if sim >= min_sim:
            out.append({"question_id": r["question_id"],
                        "content": r["content"], "options": r["options"],
                        "answer": r["answer"], "analysis": r["analysis"],
                        "source": r["source"], "difficulty": r["difficulty"],
                        "similarity": round(sim, 3)})
    out.sort(key=lambda x: x["similarity"], reverse=True)
    return out[:max(1, top)]


def verify_answer_with_fenbi(store: "FenbiBindStore", limit: int = 50,
                             interval: float = 2.0,
                             local_min_sim: float = 0.80) -> dict:
    """答案核对（管理端）：pending/待核题逐题核对。
    路径1 精确：question_bank.ext_id 为粉笔题号 → solutions 端点取官方答案
    （需任一有效绑定 Cookie）；路径2 相似：本地粉笔题库匹配 ≥local_min_sim。
    一致 → answer_status 清空（恢复判分）；粉笔解析可回填原空解析。
    返回核对报告 {checked, cleared, mismatched, unmatched, details}。"""
    from . import fenbi as fb
    from .study import get_study_store
    ss = get_study_store()
    cookie = ""
    with store._connect() as conn:
        row = conn.execute(
            "SELECT cookie FROM fenbi_bindings WHERE status='bound' AND "
            "last_check_ok=1 ORDER BY bound_at DESC LIMIT 1").fetchone()
        if row:
            cookie = row["cookie"]
    details: list[dict] = []
    cleared = mismatched = unmatched = 0
    with ss._connect() as conn:
        rows = conn.execute(
            "SELECT id, qtype, question, options, answer, analysis, ext_id, "
            "answer_status FROM question_bank WHERE qtype='choice' AND "
            "COALESCE(answer_status,'')='pending' ORDER BY id LIMIT ?",
            (max(1, min(200, int(limit))),)).fetchall()
    for r in rows:
        item = dict(r)
        opts = []
        try:
            import json as _j
            opts = _j.loads(item["options"]) if item["options"] else []
        except Exception:  # noqa: BLE001
            opts = []
        fenbi_ans = ""
        fenbi_analysis = ""
        source = ""
        sim = 0.0
        via = ""
        if cookie and str(item["ext_id"] or "").strip().isdigit():
            try:
                sols = fetch_solutions_batch(
                    cookie, [str(item["ext_id"]).strip()], interval)
                if sols:
                    s = sols[0]
                    fenbi_ans = fb._answer_letters(s)
                    fenbi_analysis = fb._strip_html(str(s.get("solution") or ""))
                    source = str(s.get("source") or "")[:80]
                    sim, via = 1.0, "solutions"
            except Exception:  # noqa: BLE001  网络失败走本地匹配兜底
                pass
        if not fenbi_ans:
            hits = search_similar_local(store, item["question"], top=1,
                                        min_sim=local_min_sim)
            if hits:
                h = hits[0]
                fenbi_ans = str(h["answer"] or "").strip()
                fenbi_analysis = str(h["analysis"] or "")
                source = str(h["source"] or "")[:80]
                sim, via = float(h["similarity"]), "local"
        if not fenbi_ans:
            unmatched += 1
            details.append({"id": item["id"], "result": "unmatched"})
            continue
        lib = {c.upper() for c in str(item["answer"] or "") if c.isalpha()}
        fb_l = {c.upper() for c in fenbi_ans if c.isalpha()}
        consistent = bool(lib) and bool(fb_l) and lib == fb_l
        if consistent:
            new_analysis = item["analysis"] or fenbi_analysis
            with ss._connect() as conn:
                conn.execute(
                    "UPDATE question_bank SET answer_status='', analysis=? "
                    "WHERE id=?", (new_analysis, item["id"]))
            cleared += 1
            details.append({"id": item["id"], "result": "cleared",
                            "via": via, "similarity": sim, "source": source})
        else:
            mismatched += 1
            details.append({"id": item["id"], "result": "mismatch",
                            "lib_answer": item["answer"],
                            "fenbi_answer": fenbi_ans, "via": via,
                            "similarity": sim})
    return {"checked": len(details) + 0, "cleared": cleared,
            "mismatched": mismatched, "unmatched": unmatched,
            "details": details}


# ---------------- 分析管线 ----------------

def _flatten_tree(tree: list) -> list[dict]:
    """两级树 → 模块统计列表 [{module, total, points:[{name, count}]}]。
    questionIds 并集去重计入模块 total；children 为知识点级。"""
    out = []
    for n in tree or []:
        qids = {str(q) for q in (n.get("questionIds") or [])}
        pts = []
        for ch in n.get("children") or []:
            cq = {str(q) for q in (ch.get("questionIds") or [])}
            qids |= cq
            if cq:
                pts.append({"name": str(ch.get("name") or ""),
                            "count": len(cq)})
        pts.sort(key=lambda x: -x["count"])
        out.append({"module": str(n.get("name") or ""),
                    "total": len(qids), "points": pts})
    out.sort(key=lambda x: -x["total"])
    return out


def _pick_samples(tree: list, raw_by_id: dict[str, dict],
                  per_module: int = 3) -> list[dict]:
    """每模块抽 difficulty 最高 2 + 最低 1 的代表题精简样本（控 token）。"""
    samples = []
    for n in tree or []:
        qids = [str(q) for q in (n.get("questionIds") or [])]
        for ch in n.get("children") or []:
            qids += [str(q) for q in (ch.get("questionIds") or [])]
        rows = []
        seen = set()
        for q in qids:
            if q in seen or q not in raw_by_id:
                continue
            seen.add(q)
            raw = raw_by_id[q]
            try:
                d10 = int(raw.get("difficulty") or 0)
            except (TypeError, ValueError):
                d10 = 0
            kps = ",".join(str(k.get("name") or "")
                           for k in (raw.get("keypoints") or [])[:2])
            rows.append({"id": q, "d": d10, "kp": kps,
                         "type": raw.get("typeName") or "",
                         "sol": fenbi._strip_html(
                             str(raw.get("solution") or ""))[:120]})
        if not rows:
            continue
        rows.sort(key=lambda x: -x["d"])
        chosen = rows[:2] + (rows[-1:] if len(rows) > 2 else [])
        for c in chosen:
            samples.append({"module": n.get("name"), "qid": c["id"],
                            "difficulty10": c["d"], "keypoint": c["kp"],
                            "qtype": c["type"], "solution_excerpt": c["sol"]})
    return samples


def _build_prompt(dist: list[dict], samples: list[dict],
                  total: int) -> str:
    pts = json.dumps(dist, ensure_ascii=False)
    smp = json.dumps(samples[:24], ensure_ascii=False)
    return f"""你是公务员考试备考规划专家。请基于学员的粉笔行测错题数据，生成一份专属提升计划任务书。

【学员错题分布】（module=模块, total=该模块错题数, points=知识点级分布；总错题 {total} 道）
{pts}

【代表错题样本】（每模块抽高/低难度代表题，difficulty10 为粉笔 1-10 难度，solution_excerpt 为解析节选）
{smp}

请严格按以下 JSON 格式输出，不要输出 JSON 以外的任何内容：
{{
  "summary": "总体诊断（错题结构、强弱模块格局、备考建议方向），220字以内",
  "weak_points": [
    {{"name": "薄弱知识点", "module": "所属模块", "level": 1到5整数(5最严重), "reason": "错因分析40字内"}}
  ],
  "advice": ["可执行的学习建议，每条50字内"],
  "weeks": [
    {{"theme": "本周主题", "goal": "本周目标40字内",
      "tasks": [
        {{"slot": "周一晚/周三晚/周末上午 等时间槽", "title": "任务名", "detail": "具体做法与目标，60字内", "minutes": 60}}
      ]}}
  ]
}}

要求：
- weak_points 给 5~8 个，按严重程度排序，必须来自分布中的真实知识点
- advice 给 3~5 条，针对性回应错题结构（如某模块占比高给出专项策略）
- weeks 恰好 4 周，从基础巩固到冲刺递进；每周 3~5 个任务；任务要具体可执行（含题量/时长）
- 全文使用中文"""


_DEFAULT_REPORT = {"summary": "", "weak_points": [], "advice": [], "weeks": []}


def run_analysis(store: FenbiBindStore, user_id: str, aid: int,
                 llm_call=None) -> None:
    """完整管线（同步执行，调用方放后台线程）：
    分布树 → solutions 分批 → 错题快照 → LLM 任务书 → done。
    llm_call(messages) -> str 由调用方注入（api 层传 _grade_llm_call 闭包；
    测试注入假实现）。"""
    cookie = store.get_cookie(user_id)
    try:
        def _p(stage: str, progress: int) -> None:
            store.update_analysis(aid, stage=stage, progress=progress)

        _p("校验登录态", 10)
        tree = fetch_error_tree(cookie)          # 失效抛 FenbiAuthError
        dist = _flatten_tree(tree)
        qids = sorted({str(q) for n in tree
                       for q in (n.get("questionIds") or [])})
        store.update_analysis(aid, distribution=json.dumps(
            dist, ensure_ascii=False), total_mistakes=len(qids))

        _p(f"拉取错题详情（共 {len(qids)} 题）", 20)
        raw_by_id: dict[str, dict] = {}
        for i in range(0, len(qids), _BATCH):
            chunk = qids[i:i + _BATCH]
            for s in fetch_solutions_batch(cookie, chunk):
                if isinstance(s, dict) and s.get("id"):
                    raw_by_id[str(s["id"])] = s
            done = min(len(raw_by_id), len(qids))
            pct = 20 + int(40 * done / max(len(qids), 1))
            _p(f"拉取错题详情 {done}/{len(qids)}", pct)

        # 错题快照落库（学员隔离；解析 HTML → 纯文本，不落盘图片省空间）
        rows = []
        for n in tree:
            mod = str(n.get("name") or "")
            mod_qids = {str(q) for q in (n.get("questionIds") or [])}
            point_of = {}
            for ch in n.get("children") or []:
                cname = str(ch.get("name") or "")
                for q in (ch.get("questionIds") or []):
                    point_of[str(q)] = cname
            for q in mod_qids:
                raw = raw_by_id.get(q)
                if not raw:
                    continue
                kps = ",".join(str(k.get("name") or "")
                               for k in (raw.get("keypoints") or [])[:2])
                try:
                    d10 = int(raw.get("difficulty") or 0)
                except (TypeError, ValueError):
                    d10 = 0
                rows.append({
                    "question_id": q, "module": mod,
                    "keypoint": point_of.get(q) or kps,
                    "content": fenbi._strip_html(str(raw.get("content") or "")),
                    "options": [fenbi._strip_html(o)
                                for o in fenbi._options_of(raw)],
                    "answer": fenbi._answer_letters(raw),
                    "analysis": fenbi._strip_html(
                        str(raw.get("solution") or "")),
                    "difficulty": (d10 + 1) // 2 or 2,
                    "source": str(raw.get("source") or
                                  raw.get("shortSource") or "")})
        saved = store.save_wrong_questions(user_id, rows)

        _p("AI 生成提升计划", 70)
        samples = _pick_samples(tree, raw_by_id)
        prompt = _build_prompt(dist, samples, len(qids))
        report = dict(_DEFAULT_REPORT)
        if llm_call is not None:
            out = llm_call([{"role": "user", "content": prompt}])
            try:
                obj = json.loads(out) if isinstance(out, str) else out
            except ValueError:
                from .api import _extract_json_obj  # 延迟导入避免循环
                obj = _extract_json_obj(out)
            if isinstance(obj, dict) and obj.get("summary"):
                report = obj

        store.update_analysis(
            aid, status="done", stage="完成", progress=100,
            report=json.dumps(report, ensure_ascii=False),
            total_mistakes=len(qids), done_at=_now())
        print(f"[fenbi_bind] 分析完成 user={user_id} aid={aid} "
              f"错题{len(qids)} 新快照{saved}", flush=True)
    except fenbi.FenbiAuthError as e:
        store.set_binding_status(user_id, "expired", check_ok=0)
        store.update_analysis(aid, status="failed", stage="登录态失效",
                              error=f"粉笔登录态已失效，请重新绑定（{str(e)[:60]}）",
                              done_at=_now())
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        store.update_analysis(aid, status="failed", stage="异常",
                              error=str(e)[:200], done_at=_now())


# ---------------- 登录会话留痕（真实会话管理在 fenbi_login.py） ----------------

def new_session_id() -> str:
    return secrets.token_hex(16)
