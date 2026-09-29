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
import urllib.parse
from datetime import datetime
from pathlib import Path

from .config import Config
from . import fenbi

# 粉笔行测（国考）exam category；keypoint-tree/solutions 定位参数
_EXAMCATID = 1000183
_KEYPOINT_TREE_URL = (
    "https://tiku.fenbi.com/api/xingce/errors/keypoint-tree"
    "?timeRange=0&order=desc&app=web&examcatid=%d" % _EXAMCATID)
_COLLECT_TREE_URL = (
    "https://tiku.fenbi.com/api/xingce/collects/keypoint-tree"
    "?timeRange=0&order=desc&app=web&examcatid=%d" % _EXAMCATID)
_SOLUTIONS_URL = ("https://tiku.fenbi.com/api/xingce/solutions"
                  "?app=web&kav=12&ver=3.88&ids={ids}")
# 批次27 · 练习/模考（combine 域，无 /api/ 前缀；2026-10-03 实测）
_HISTORY_URL = ("https://tiku.fenbi.com/combine/exercise"
                "/getExerciseBriefHistory?categoryId={cat}&limit={limit}"
                "&cursor={cursor}&routecs=xingce&app=web"
                "&examcatid=%d" % _EXAMCATID)
# getReport/getSolution 必须带 format=html + 全套 app 版本参数（缺则空 data）
_COMBINE_Q = ("format=html&key={key}&routecs=xingce&kav=125&av=127&hav=125"
              "&app=web&apcid=0&deviceId=&gav=2")
_REPORT_URL = ("https://tiku.fenbi.com/combine/exercise/getReport?"
               + _COMBINE_Q + "&no_toast")
_GETSOLUTION_URL = ("https://tiku.fenbi.com/combine/exercise/getSolution?"
                    + _COMBINE_Q)
# userAnswers.status 语义（130 题 5/5/120 与报告页完全吻合）
ANSWER_STATUS = {"right": 1, "wrong": -1, "unanswered": 10}

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
            CREATE TABLE IF NOT EXISTS fenbi_collect_questions (
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
            CREATE INDEX IF NOT EXISTS idx_fcq_user
                ON fenbi_collect_questions(user_id);
            CREATE TABLE IF NOT EXISTS fenbi_mock_exams (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        TEXT NOT NULL,
                exercise_key   TEXT NOT NULL,               -- 如 1_1_3oedvnf
                name           TEXT NOT NULL DEFAULT '',
                sheet_type     INTEGER NOT NULL DEFAULT 0,  -- 1=模考 3=专项
                score          REAL,
                fullmark       REAL,
                question_count INTEGER NOT NULL DEFAULT 0,
                correct_count  INTEGER NOT NULL DEFAULT 0,
                wrong_count    INTEGER NOT NULL DEFAULT 0,
                unanswered     INTEGER NOT NULL DEFAULT 0,
                duration_sec   INTEGER NOT NULL DEFAULT 0,
                submit_time    TEXT,
                difficulty     REAL,
                paper_id       TEXT NOT NULL DEFAULT '',
                report_json    TEXT NOT NULL DEFAULT '{}',  -- getReport data 原样
                synced_at      TEXT,
                UNIQUE(user_id, exercise_key)
            );
            CREATE INDEX IF NOT EXISTS idx_fme_user
                ON fenbi_mock_exams(user_id);
            CREATE TABLE IF NOT EXISTS fenbi_mock_answers (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        TEXT NOT NULL,
                exam_row_id    INTEGER NOT NULL,            -- fenbi_mock_exams.id
                question_id    TEXT NOT NULL,
                global_key     TEXT NOT NULL DEFAULT '',
                status         INTEGER NOT NULL DEFAULT 0,  -- 1对 -1错 10未答
                user_answer    TEXT NOT NULL DEFAULT '',
                correct_answer TEXT NOT NULL DEFAULT '',
                content        TEXT NOT NULL DEFAULT '',
                analysis       TEXT NOT NULL DEFAULT '',
                keypoints      TEXT NOT NULL DEFAULT '[]',
                UNIQUE(exam_row_id, question_id)
            );
            CREATE INDEX IF NOT EXISTS idx_fma_exam
                ON fenbi_mock_answers(exam_row_id);
            CREATE TABLE IF NOT EXISTS fenbi_sync_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                kind       TEXT NOT NULL,                   -- wrong/collect/mock
                status     TEXT NOT NULL DEFAULT 'running', -- running/done/failed
                stage      TEXT NOT NULL DEFAULT '',
                progress   INTEGER NOT NULL DEFAULT 0,
                total      INTEGER NOT NULL DEFAULT 0,
                new_count  INTEGER NOT NULL DEFAULT 0,
                detail     TEXT NOT NULL DEFAULT '',
                error      TEXT NOT NULL DEFAULT '',
                created_at TEXT,
                done_at    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_fsl_user
                ON fenbi_sync_log(user_id, created_at);
            """)
            # 批次28 迁移：三表补 material 列（资料分析材料/图文题材料快照）。
            # CREATE TABLE IF NOT EXISTS 对已存在的旧表不生效，须逐表检查补列。
            for tbl in ("fenbi_wrong_questions", "fenbi_collect_questions",
                        "fenbi_mock_answers"):
                cols = {r[1] for r in conn.execute(
                    f"PRAGMA table_info({tbl})").fetchall()}
                if "material" not in cols:
                    conn.execute(
                        f"ALTER TABLE {tbl} ADD COLUMN material"
                        " TEXT NOT NULL DEFAULT ''")

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
        material, options(list), answer, analysis, difficulty, source}。"""
        n = 0
        with self._connect() as conn:
            for r in rows:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO fenbi_wrong_questions"
                    "(user_id, question_id, module, keypoint, content,"
                    " material, options, answer, analysis, difficulty, source,"
                    " created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (user_id, str(r.get("question_id") or ""),
                     str(r.get("module") or ""),
                     str(r.get("keypoint") or ""),
                     str(r.get("content") or ""),
                     str(r.get("material") or ""),
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

    # ---- 批次27 · 收藏快照 ----

    def save_collect_questions(self, user_id: str,
                               rows: list[dict]) -> int:
        """INSERT OR IGNORE（同错题表结构，含 material）。"""
        n = 0
        with self._connect() as conn:
            for r in rows:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO fenbi_collect_questions"
                    "(user_id, question_id, module, keypoint, content,"
                    " material, options, answer, analysis, difficulty, source,"
                    " created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (user_id, str(r.get("question_id") or ""),
                     str(r.get("module") or ""),
                     str(r.get("keypoint") or ""),
                     str(r.get("content") or ""),
                     str(r.get("material") or ""),
                     json.dumps(r.get("options") or [], ensure_ascii=False),
                     str(r.get("answer") or ""),
                     str(r.get("analysis") or ""),
                     int(r.get("difficulty") or 0),
                     str(r.get("source") or ""), _now()))
                n += cur.rowcount
        return n

    def collect_count(self, user_id: str) -> int:
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM fenbi_collect_questions WHERE user_id=?",
                (user_id,)).fetchone()[0]

    def collect_ids(self, user_id: str) -> set[str]:
        with self._connect() as conn:
            return {str(r[0]) for r in conn.execute(
                "SELECT question_id FROM fenbi_collect_questions"
                " WHERE user_id=?", (user_id,)).fetchall()}

    def mock_exam_keys(self, user_id: str) -> set[str]:
        with self._connect() as conn:
            return {str(r[0]) for r in conn.execute(
                "SELECT exercise_key FROM fenbi_mock_exams"
                " WHERE user_id=?", (user_id,)).fetchall()}

    # ---- 批次27 · 快照列表查询 ----

    def wrong_list(self, user_id: str, limit: int = 20,
                   offset: int = 0) -> tuple[list[dict], int]:
        """错题快照分页列表 → (rows, total)。"""
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM fenbi_wrong_questions WHERE user_id=?",
                (user_id,)).fetchone()[0]
            rows = conn.execute(
                "SELECT id, question_id, module, keypoint, content, material,"
                " options, answer, analysis, difficulty, source, created_at"
                " FROM fenbi_wrong_questions WHERE user_id=?"
                " ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["options"] = json.loads(d.get("options") or "[]")
            except ValueError:
                d["options"] = []
            out.append(d)
        return out, total

    def collect_list(self, user_id: str, limit: int = 20,
                     offset: int = 0) -> tuple[list[dict], int]:
        """收藏快照分页列表 → (rows, total)。"""
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM fenbi_collect_questions WHERE user_id=?",
                (user_id,)).fetchone()[0]
            rows = conn.execute(
                "SELECT id, question_id, module, keypoint, content, material,"
                " options, answer, analysis, difficulty, source, created_at"
                " FROM fenbi_collect_questions WHERE user_id=?"
                " ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["options"] = json.loads(d.get("options") or "[]")
            except ValueError:
                d["options"] = []
            out.append(d)
        return out, total

    # ---- 批次27 · 模考试卷 + 作答 ----

    def save_mock_exam(self, user_id: str, exam: dict,
                       answers: list[dict]) -> int:
        """UPSERT 试卷 + 整卷替换作答明细。exam 含 exercise_key/name/...；
        answers: {question_id, global_key, status, user_answer,
        correct_answer, content, analysis, keypoints}。返回试卷行 id。"""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO fenbi_mock_exams(user_id, exercise_key, name,"
                " sheet_type, score, fullmark, question_count, correct_count,"
                " wrong_count, unanswered, duration_sec, submit_time,"
                " difficulty, paper_id, report_json, synced_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(user_id, exercise_key) DO UPDATE SET"
                " name=excluded.name, sheet_type=excluded.sheet_type,"
                " score=excluded.score, fullmark=excluded.fullmark,"
                " question_count=excluded.question_count,"
                " correct_count=excluded.correct_count,"
                " wrong_count=excluded.wrong_count,"
                " unanswered=excluded.unanswered,"
                " duration_sec=excluded.duration_sec,"
                " submit_time=excluded.submit_time,"
                " difficulty=excluded.difficulty,"
                " paper_id=excluded.paper_id,"
                " report_json=excluded.report_json,"
                " synced_at=excluded.synced_at",
                (user_id, exam.get("exercise_key") or "",
                 exam.get("name") or "", int(exam.get("sheet_type") or 0),
                 exam.get("score"), exam.get("fullmark"),
                 int(exam.get("question_count") or 0),
                 int(exam.get("correct_count") or 0),
                 int(exam.get("wrong_count") or 0),
                 int(exam.get("unanswered") or 0),
                 int(exam.get("duration_sec") or 0),
                 exam.get("submit_time") or "",
                 exam.get("difficulty"),
                 str(exam.get("paper_id") or ""),
                 json.dumps(exam.get("report") or {}, ensure_ascii=False),
                 _now()))
            row = conn.execute(
                "SELECT id FROM fenbi_mock_exams WHERE user_id=? AND"
                " exercise_key=?", (user_id, exam.get("exercise_key"))
            ).fetchone()
            eid = int(row[0])
            conn.execute("DELETE FROM fenbi_mock_answers WHERE exam_row_id=?",
                         (eid,))
            for a in answers:
                conn.execute(
                    "INSERT OR IGNORE INTO fenbi_mock_answers"
                    "(user_id, exam_row_id, question_id, global_key, status,"
                    " user_answer, correct_answer, content, material,"
                    " analysis, keypoints) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (user_id, eid, str(a.get("question_id") or ""),
                     str(a.get("global_key") or ""),
                     int(a.get("status") or 0),
                     str(a.get("user_answer") or ""),
                     str(a.get("correct_answer") or ""),
                     str(a.get("content") or ""),
                     str(a.get("material") or ""),
                     str(a.get("analysis") or ""),
                     json.dumps(a.get("keypoints") or [], ensure_ascii=False)))
        return eid

    def mock_exam_list(self, user_id: str) -> list[dict]:
        """试卷列表（不含 report_json 大字段）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, exercise_key, name, sheet_type, score, fullmark,"
                " question_count, correct_count, wrong_count, unanswered,"
                " duration_sec, submit_time, difficulty, paper_id, synced_at"
                " FROM fenbi_mock_exams WHERE user_id=?"
                " ORDER BY synced_at DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def mock_exam_detail(self, user_id: str, exam_row_id: int) -> dict | None:
        """试卷详情：基础信息 + 整卷分析（report 解析）+ 全部作答明细。"""
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM fenbi_mock_exams WHERE user_id=? AND id=?",
                (user_id, exam_row_id)).fetchone()
            if not r:
                return None
            d = dict(r)
            ans = conn.execute(
                "SELECT question_id, global_key, status, user_answer,"
                " correct_answer, content, material, analysis, keypoints"
                " FROM fenbi_mock_answers WHERE exam_row_id=?",
                (exam_row_id,)).fetchall()
        for jk in ("report_json",):
            try:
                d["report"] = json.loads(d.pop(jk) or "{}")
            except ValueError:
                d["report"] = {}
        for a in ans:
            a = dict(a)
            try:
                a["keypoints"] = json.loads(a.get("keypoints") or "[]")
            except ValueError:
                a["keypoints"] = []
            d.setdefault("answers", []).append(a)
        return d

    # ---- 批次27 · 同步任务留痕 ----

    def start_sync(self, user_id: str, kind: str) -> tuple[int, str]:
        """新建同步任务；同 kind 有 running 则拒绝（防并发重复拉取）。"""
        with self._connect() as conn:
            r = conn.execute(
                "SELECT id FROM fenbi_sync_log WHERE user_id=? AND kind=?"
                " AND status='running'", (user_id, kind)).fetchone()
            if r:
                return 0, "该同步正在进行中，请稍候"
            cur = conn.execute(
                "INSERT INTO fenbi_sync_log(user_id, kind, status,"
                " stage, created_at) VALUES(?,?,'running','准备',?)",
                (user_id, kind, _now()))
        return int(cur.lastrowid), ""

    def update_sync(self, sid: int, **kw) -> None:
        cols, vals = [], []
        for k in ("status", "stage", "progress", "total", "new_count",
                  "detail", "error", "done_at"):
            if k in kw:
                cols.append(f"{k}=?")
                vals.append(kw[k])
        if cols:
            vals.append(sid)
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE fenbi_sync_log SET {','.join(cols)} WHERE id=?",
                    vals)

    def latest_sync(self, user_id: str, kind: str) -> dict | None:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM fenbi_sync_log WHERE user_id=? AND kind=?"
                " ORDER BY id DESC LIMIT 1", (user_id, kind)).fetchone()
        return dict(r) if r else None

    def sync_history(self, user_id: str, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM fenbi_sync_log WHERE user_id=?"
                " ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # ---- analyses ----

    def start_analysis(self, user_id: str) -> tuple[int, str]:
        """新建分析任务；返回 (id, error)。冷却 6h + 每用户单 running。
        批次27-E：running 超时视为僵死（进程重启/线程被杀）自动置
        failed 放行——否则服务重启后用户被「已有分析在进行中」永久卡死。
        27-N P1-5：僵死阈值 1800→480s——分析带硬预算（强模型 300s+默认链），
        480s 仍未完成必属异常；30 分钟阈值会让用户中途重试全部 429。"""
        with self._connect() as conn:
            run = conn.execute(
                "SELECT id, created_at FROM fenbi_analyses WHERE user_id=? AND"
                " status='running' ORDER BY id DESC LIMIT 1",
                (user_id,)).fetchone()
            if run:
                stale = False
                if run["created_at"]:
                    try:
                        dt = datetime.strptime(run["created_at"],
                                               "%Y-%m-%d %H:%M:%S")
                        stale = time.time() - dt.timestamp() > 480
                    except ValueError:
                        stale = False
                if not stale:
                    return 0, "已有分析在进行中，请稍候（正常约 1-3 分钟）"
                conn.execute(
                    "UPDATE fenbi_analyses SET status='failed',"
                    " stage='异常', error='任务超时中断（服务重启或进程异常），请重新生成',"
                    " done_at=? WHERE id=?", (_now(), run["id"]))
            last = conn.execute(
                "SELECT created_at FROM fenbi_analyses WHERE user_id=? AND"
                " status='done' AND (error IS NULL OR error='')"
                " ORDER BY id DESC LIMIT 1",
                (user_id,)).fetchone()
            # 批次27-E：降级统计版（done+error）不进冷却——允许立即重试 AI 完整版
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


# ---------------- 批次27 · 收藏 / 练习历史 / 模考报告 ----------------

def fetch_collect_tree(cookie: str) -> list:
    """收藏知识点两级树（与错题树同构）：[{name, questionIds[], children}]。"""
    d = fenbi._get_json(_COLLECT_TREE_URL, cookie, 2.0, None,
                        referer="https://spa.fenbi.com/")
    return d if isinstance(d, list) else []


def fetch_exercise_history(cookie: str, category_id: int = 1,
                           limit: int = 30, cursor: str = "",
                           interval: float = 1.5) -> dict:
    """练习/试卷历史。categoryId：1=试卷（模考/真题）、3=练习（专项）。
    返回 {cursor, items[]}；items 含 exerciseKey/sheetName/sheetType/
    difficulty/status/questionCount/correctCount/score/fullScore/updatedTime。"""
    url = _HISTORY_URL.format(cat=category_id, limit=limit,
                              cursor=urllib.parse.quote(cursor or ""))
    d = fenbi._get_json(url, cookie, interval, None,
                        referer="https://spa.fenbi.com/")
    data = d.get("data") if isinstance(d, dict) else None
    return {"cursor": (data or {}).get("cursor") or "",
            "items": (data or {}).get("historyItems") or []}


def fetch_mock_report(cookie: str, exercise_key: str,
                      interval: float = 1.5) -> dict:
    """已提交练习/模考的整卷分析：score/fullmark/difficulty/paperId/
    subReports（basic + courseReports 分模块统计）/userAnswers(status 语义)。"""
    d = fenbi._get_json(_REPORT_URL.format(key=exercise_key), cookie,
                        interval, None, referer="https://spa.fenbi.com/")
    if isinstance(d, dict) and d.get("code") == 1:
        return d.get("data") or {}
    raise ValueError(
        f"getReport 失败：{str((d or {}).get('msg') or d)[:80]}")


def fetch_mock_solution(cookie: str, exercise_key: str,
                        interval: float = 1.5) -> dict:
    """已提交练习/模考的题目与作答：materials + solutions[]（含
    correctAnswer/solution 解析/keypoints）+ userAnswers + card。"""
    d = fenbi._get_json(_GETSOLUTION_URL.format(key=exercise_key), cookie,
                        interval, None, referer="https://spa.fenbi.com/")
    data = d.get("data") if isinstance(d, dict) else None
    urls = ((data or {}).get("staticUrl") or {}).get("urls") or []
    if not urls:
        raise ValueError("getSolution 未返回 staticUrl（练习可能未提交）")
    st = fenbi._get_json(urls[0] + fenbi._STATIC_QUERY, cookie, interval,
                         None, referer="https://spa.fenbi.com/")
    top = st.get("data") if isinstance(st.get("data"), dict) else st
    return {"materials": top.get("materials") or [],
            "solutions": top.get("solutions") or [],
            "userAnswers": data.get("userAnswers") or {},
            "name": data.get("name") or "",
            "sheetType": data.get("sheetType") or 0}


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


def _shrink_dist(dist: list, max_chars: int = 12000) -> list:
    """批次27-F 防御：知识点分布超长时按模块保留 total、每模块知识点截 top12
    （万题级错题只是计数变大、知识点种类有限，正常不会触发；纯保险）。"""
    pts = json.dumps(dist, ensure_ascii=False)
    if len(pts) <= max_chars:
        return dist
    out = []
    for m in dist:
        m = dict(m)
        p = sorted(m.get("points") or [],
                   key=lambda x: int(x.get("count") or 0), reverse=True)[:12]
        m["points"] = p
        out.append(m)
    return out


def _parse_mock_report(report_json: str) -> tuple[dict, list[dict]]:
    """批次27-F：解析一份模考 getReport 原样 JSON → (整卷信息, 模块级行)。
    basic 取 subReports 中 type=0（time=计划秒数/score/fullmark）与
    courseStat（elapseTime 实际用时/answerCount/questionCount）；
    模块行取 type=1 → courseReports[0].details[] 顶层
    （name/time 秒/questionCount/correctCount；time=0 且对 0 = 整场未作答）。
    结构不符返回 (None, [])。"""
    try:
        obj = json.loads(report_json) if report_json else {}
        if isinstance(obj, str):          # 兼容被二次 dumps 的存量数据
            obj = json.loads(obj)
    except ValueError:
        return None, []
    if not isinstance(obj, dict):
        return None, []
    srs = obj.get("subReports") or []
    if not isinstance(srs, list):
        return None, []
    basic = None
    mod_rows: list[dict] = []
    for s in srs:
        if not isinstance(s, dict):
            continue
        if s.get("type") == 0 and basic is None:
            basic = {"plan_sec": int(s.get("time") or 0),
                     "score": s.get("score"), "fullmark": s.get("fullmark")}
        elif s.get("type") == 1 and not mod_rows:
            crs = s.get("courseReports") or []
            if crs and isinstance(crs[0], dict):
                cs = crs[0].get("courseStat") or {}
                basic = dict(basic or {})
                basic.update({
                    "elapsed_sec": int(cs.get("elapseTime") or 0),
                    "answered": int(cs.get("answerCount") or 0),
                    "total": int(cs.get("questionCount") or 0)})
                for d in crs[0].get("details") or []:
                    if isinstance(d, dict) and d.get("type") == 1:
                        mod_rows.append({
                            "module": str(d.get("name") or ""),
                            "time": int(d.get("time") or 0),
                            "qc": int(d.get("questionCount") or 0),
                            "cc": int(d.get("correctCount") or 0)})
    return basic, mod_rows


def _extract_mock_time(store, user_id: str, limit: int = 3) -> dict | None:
    """批次27-F：最近模考的时间分配画像（供任务书时间分配建议）。
    exams=最近场次整卷数据；modules=跨场聚合（含「整场未作答」计数）。
    无任何可用模考数据返回 None——调用方据此跳过时间段。"""
    try:
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT name, score, fullmark, question_count, correct_count,"
                " duration_sec, submit_time, report_json FROM fenbi_mock_exams"
                " WHERE user_id=? ORDER BY submit_time DESC LIMIT ?",
                (user_id, limit)).fetchall()
    except Exception:  # noqa: BLE001  表缺失等一律降级为无画像
        return None
    exams: list[dict] = []
    agg: dict[str, dict] = {}
    for r in rows:
        basic, mod_rows = _parse_mock_report(r["report_json"])
        if basic is None:
            continue
        if basic.get("score") is not None:
            exams.append({
                "name": str(r["name"] or "")[:40],
                "score": basic.get("score"), "fullmark": basic.get("fullmark"),
                "plan_sec": basic.get("plan_sec"),
                "elapsed_sec": basic.get("elapsed_sec"),
                "answered": basic.get("answered"), "total": basic.get("total"),
                "submit_time": str(r["submit_time"] or "")[:10]})
        for m in mod_rows:
            name = m["module"] or "未知模块"
            a = agg.setdefault(name, {
                "module": name, "attempted_qc": 0, "correct": 0,
                "total_sec": 0, "unanswered_exams": 0})
            if m["qc"] > 0 and (m["time"] > 0 or m["cc"] > 0):
                a["attempted_qc"] += m["qc"]
                a["correct"] += m["cc"]
                a["total_sec"] += m["time"]
            else:
                a["unanswered_exams"] += 1
    if not exams and not agg:
        return None
    modules = []
    for a in agg.values():
        a["avg_sec_per_q"] = (round(a["total_sec"] / a["attempted_qc"], 1)
                              if a["attempted_qc"] else 0)
        a["correct_rate"] = (round(a["correct"] / a["attempted_qc"], 2)
                             if a["attempted_qc"] else 0)
        modules.append(a)
    modules.sort(key=lambda x: (x["attempted_qc"], x["total_sec"]),
                 reverse=True)
    return {"exams": exams, "modules": modules}


def _build_prompt(dist: list[dict], samples: list[dict],
                  total: int, time_profile: dict | None = None) -> str:
    """构建任务书生成 prompt。time_profile 非空时追加模考时间数据段，
    并要求输出 time_plan（模考时间分配建议）键。"""
    pts = json.dumps(_shrink_dist(dist), ensure_ascii=False)
    smp = json.dumps(samples[:24], ensure_ascii=False)
    has_tp = bool(time_profile
                  and (time_profile.get("exams") or time_profile.get("modules")))
    time_seg = ""
    time_rule = ""
    time_schema = ""
    time_ask = ""
    if has_tp:
        tp = json.dumps(time_profile, ensure_ascii=False)
        time_seg = (
            "\n【模考作答与时间分配】（exams=最近场次整卷：plan_sec=计划时长秒/"
            "elapsed_sec=实际用时/answered·total=作答题数；modules=分模块跨场聚合："
            "attempted_qc=有作答题次累计/avg_sec_per_q=作答题平均每题秒数"
            "（行测基准约 55 秒/题）/correct_rate=累计正确率/"
            "unanswered_exams=该模块整场未作答（放弃）的次数）\n" + tp + "\n")
        time_rule = ("- 必须结合模考时间数据分析：耗时远超基准且正确率低→提速策略；"
                     "耗时合理正确率高→保持；unanswered_exams>0 的模块给出"
                     "「攻坚还是策略性放弃」的明确取舍（资料分析/判断推理/言语性价比高"
                     "不建议放弃；数量关系可后置但需保底）\n")
        time_schema = (
            ',\n  "time_plan": [\n'
            '    {"module": "模块名", "issue": "时间分配问题30字内", '
            '"suggestion": "具体分配建议50字内（含秒数/题量）"}\n  ]')
        time_ask = ("- time_plan 给 4~8 条，按模块组织：issue 指出时间分配问题"
                    "（耗时超标/整场放弃/提速空间），suggestion 给出具体到秒数或题量的建议"
                    "（如「资料分析保持 80 秒/题，整卷预留 30 分钟」）\n")
    else:
        time_rule = ("- 若学员错题显示某模块占比过高，在 advice 中给出针对性的"
                     "时间投入建议\n")
    return f"""你是公务员考试备考规划专家。请基于学员的粉笔行测错题数据，生成一份专属提升计划任务书。

【学员错题分布】（module=模块, total=该模块错题数, points=知识点级分布；总错题 {total} 道）
{pts}

【代表错题样本】（每模块抽高/低难度代表题，difficulty10 为粉笔 1-10 难度，solution_excerpt 为解析节选）
{smp}{time_seg}
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
  ]{time_schema}
}}

要求：
- weak_points 给 5~8 个，按严重程度排序，必须来自分布中的真实知识点
- advice 给 3~5 条，针对性回应错题结构（如某模块占比高给出专项策略）
{time_rule}- weeks 恰好 4 周，从基础巩固到冲刺递进；每周 3~5 个任务；任务要具体可执行（含题量/时长）
{time_ask}- 全文使用中文"""


_DEFAULT_REPORT = {"summary": "", "weak_points": [], "advice": [],
                   "weeks": [], "time_plan": []}


def _local_time_plan(time_profile: dict | None) -> list[dict]:
    """批次27-F：本地规则生成模考时间分配建议（零 LLM，供降级版任务书）。
    规则：每题耗时 >90s=提速；50-90s 且正确率<60%=基础优先；正确率>=70%=保持；
    整场未作答模块给取舍建议（性价比高的劝攻、数量关系允许后置）。"""
    if not time_profile:
        return []
    out = []
    for m in time_profile.get("modules") or []:
        spq = float(m.get("avg_sec_per_q") or 0)
        cr = float(m.get("correct_rate") or 0)
        name = str(m.get("module") or "")
        una = int(m.get("unanswered_exams") or 0)
        qc = int(m.get("attempted_qc") or 0)
        if qc and spq > 90:
            out.append({"module": name,
                        "issue": f"每题 {int(spq)} 秒远超 55 秒基准"
                                 + (f"，正确率 {int(cr*100)}%" if cr else ""),
                        "suggestion": "先练速度：该模块限时 "
                                      f"{max(60, int(spq*0.6))} 秒/题，"
                                      "简单题先做难题后置，超 90 秒果断标记跳过"})
        elif qc and 50 <= spq <= 90 and cr < 0.6:
            out.append({"module": name,
                        "issue": f"速度尚可（{int(spq)} 秒/题）但正确率仅 "
                                 f"{int(cr*100)}%",
                        "suggestion": "时间先投基础：错误题型回炉讲义与错题本，"
                                      "正确率到 70% 前不追求提速"})
        elif qc and spq < 40 and cr < 0.6:
            out.append({"module": name,
                        "issue": f"答题过快（{int(spq)} 秒/题）但正确率仅 "
                                 f"{int(cr*100)}%，疑似仓促作答",
                        "suggestion": "刻意放慢：读题圈关键信息再作答，"
                                      "目标 50-60 秒/题，正确率优先于速度"})
        elif qc and cr >= 0.7:
            out.append({"module": name,
                        "issue": f"节奏健康（{int(spq)} 秒/题，正确率 "
                                 f"{int(cr*100)}%）",
                        "suggestion": "保持当前节奏，每周 1 次限时整段练习维持手感"})
        if una >= 1 and not qc:
            give_up_ok = name in ("数量关系", "常识判断")
            out.append({"module": name,
                        "issue": f"最近 {una} 场整场未作答（放弃）",
                        "suggestion": ("性价比低可策略性后置：最后 10 分钟凭直觉全选"
                                       "同一选项保底" if give_up_ok else
                                       "性价比高不建议放弃：先保底完成前 60% 简单题"
                                       "（正确率可达 70%），难题放弃")})
    return out[:8]


def _fallback_report(dist: list, raw_by_id: dict, total: int,
                     time_profile: dict | None = None) -> dict:
    """批次27-E 极端兜底：AI 全败时的本地统计报告（零 LLM 依赖）。
    结构与 LLM 报告一致（summary/weak_points/advice/weeks/time_plan），前端零改动可渲染；
    数据全部来自本轮已拉取的分布树与错题快照（批次27-F 起含模考时间画像）。"""
    mods = sorted(dist or [], key=lambda m: int(m.get("total") or 0),
                  reverse=True)
    total_eff = sum(int(m.get("total") or 0) for m in mods) or total or 1
    summary = (f"本次共采集错题 {total} 道，覆盖 {len(mods)} 个模块。"
               f"错题最集中的模块为「{mods[0].get('module', '—')}」"
               f"（{mods[0].get('total', 0)} 道，占 "
               f"{int(100 * int(mods[0].get('total') or 0) / total_eff)}%）。"
               "（AI 服务暂不可用，本报告为基础统计版；服务恢复后可重新生成完整版任务书）")

    weak_points = []
    for m in mods[:5]:
        t = int(m.get("total") or 0)
        ratio = t / total_eff
        level = 5 if ratio >= 0.35 else (4 if ratio >= 0.25 else
                (3 if ratio >= 0.15 else 2))
        pts = m.get("points") or []
        top_pt = ""
        if isinstance(pts, list) and pts:
            try:
                top = max(pts, key=lambda p: int(p.get("count") or 0))
                top_pt = str(top.get("name") or "")
            except (ValueError, TypeError):
                top_pt = ""
        reason = (f"错题 {t} 道（占 {int(ratio * 100)}%）"
                  + (f"，其中「{top_pt}」最集中" if top_pt else ""))
        weak_points.append({"name": top_pt or str(m.get("module") or ""),
                            "module": str(m.get("module") or ""),
                            "level": level, "reason": reason})

    advice = []
    for i, m in enumerate(mods[:3]):
        advice.append(f"优先攻克「{m.get('module')}」：按知识点逐个过一遍错题解析，"
                      f"先重做最近 20 道错题再对照答案复盘"
                      + ("（第一优先）" if i == 0 else ""))
    advice.append("每天固定 30-40 分钟错题重做，用系统练习模式按知识点抽题检验")
    advice.append("每周日用一次「学习报告」回顾正确率变化，动态调整下周重心")

    weeks = []
    for i, m in enumerate(mods[:3]):
        weeks.append({"theme": f"{m.get('module')} 专项突破",
                      "goal": f"该模块错题重做正确率达到 70% 以上",
                      "tasks": [
                          {"time": f"周一-周三", "desc": f"过「{m.get('module')}」全部错题解析，归纳共性错误"},
                          {"time": "周四-周六", "desc": "按知识点抽题重做，错两次的加入收藏反复练"},
                          {"time": "周日", "desc": "限时小测 20 题检验阶段成果"},
                      ]})
    if len(weeks) < 3:
        weeks.append({"theme": "综合模拟检验", "goal": "整卷体验与查漏",
                      "tasks": [{"time": "本周", "desc": "完成一次完整模考，对照分布检查薄弱模块是否改善"}]})
    time_plan = _local_time_plan(time_profile)
    if time_plan:
        slow = [t for t in time_plan if "秒" in t.get("issue", "")]
        head = ("时间分配上，" + "；".join(t["module"] for t in slow[:2]) + " 需重点调整。"
                if slow else "时间分配建议见 time_plan 区块。")
        summary += head
    return {"summary": summary, "weak_points": weak_points,
            "advice": advice, "weeks": weeks, "time_plan": time_plan}


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
                # 批次28：统一走 _sol_row（图片本地化 + 材料采集），替代
                # 此前的就地 strip（图片丢失 / 材料缺失的根因）
                rows.append(_sol_row(raw, q, qid_paths))
        saved = store.save_wrong_questions(user_id, rows)

        _p("AI 生成提升计划", 70)
        samples = _pick_samples(tree, raw_by_id)
        time_profile = _extract_mock_time(store, user_id)  # 批次27-F 模考时间画像
        prompt = _build_prompt(dist, samples, len(qids), time_profile)
        report = dict(_DEFAULT_REPORT)
        degraded = False
        llm_err = ""

        def _parse(out: str, repair: bool):
            """解析任务书 JSON；repair=False 严格模式（截断不抢救，留给重试）。"""
            try:
                obj = json.loads(out)
                return obj if isinstance(obj, dict) else None
            except ValueError:
                from .api import _extract_json_obj  # 延迟导入避免循环
                try:
                    return _extract_json_obj(out, repair=repair)
                except ValueError:
                    return None

        if llm_call is not None:
            # 批次27-E：四级递进——①首跑严格解析（截断不抢救）→ ②带精简约束重试
            # → ③修复器抢救截断 JSON（缩水但可用）→ ④降级本地统计报告（零 LLM）。
            out = ""
            for attempt in (1, 2):
                try:
                    msg = prompt if attempt == 1 else (
                        prompt + "\n\n【重试要求】上一轮输出被截断或为空。"
                        "请务必输出完整可解析的 JSON：weak_points 不超过 6 条、"
                        "advice 每条不超过 40 字、weeks 精简到 3 条，"
                        "总长度控制在 2500 字符以内，不要有任何 JSON 之外的文字。")
                    out = llm_call([{"role": "user", "content": msg}])
                    if not isinstance(out, str) or not out.strip():
                        raise ValueError("模型返回空内容")
                    obj = _parse(out, repair=(attempt == 2))
                    if isinstance(obj, dict) and obj.get("summary"):
                        report = obj
                        break
                    raise ValueError("报告缺少 summary 字段")
                except Exception as le:  # noqa: BLE001  记录后重试/降级
                    llm_err = str(le)[:120]
                    print(f"[fenbi_bind] AI 任务书第 {attempt} 次尝试失败："
                          f"{llm_err}", flush=True)
            if not report.get("summary"):
                degraded = True
                report = _fallback_report(dist, raw_by_id, len(qids),
                                          time_profile)

        if degraded:
            store.update_analysis(
                aid, status="done", stage="完成", progress=100,
                report=json.dumps(report, ensure_ascii=False),
                total_mistakes=len(qids), done_at=_now(),
                error="AI 服务不可用，已降级为基础统计版（" + llm_err + "）")
        else:
            store.update_analysis(
                aid, status="done", stage="完成", progress=100,
                report=json.dumps(report, ensure_ascii=False),
                total_mistakes=len(qids), done_at=_now())
        # 批次 J：任务书生成完成 → 自动同步到学习计划（失败不影响分析结果）
        try:
            if report.get("summary"):
                from .study import get_study_store
                plan = get_study_store().sync_fenbi_report_plan(
                    "T001", user_id, report)
                print(f"[fenbi_bind] 任务书已同步到学习计划 "
                      f"plan_id={plan['plan_id']}", flush=True)
        except Exception as se:  # noqa: BLE001
            print(f"[fenbi_bind] 任务书同步学习计划失败（不影响分析）：{se}",
                  flush=True)
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


# ---------------- 批次27 · 三大同步（错题/收藏/模考） ----------------

def _ms_to_dt(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _tree_qid_paths(tree: list) -> dict[str, tuple[str, str]]:
    """两级树 → {qid: (模块名, 知点名)}（首见优先，避免重复计）。"""
    out: dict[str, tuple[str, str]] = {}
    for n in tree or []:
        mod = str(n.get("name") or "")
        for q in (n.get("questionIds") or []):
            out.setdefault(str(q), (mod, ""))
        for ch in n.get("children") or []:
            pt = str(ch.get("name") or "")
            for q in (ch.get("questionIds") or []):
                out.setdefault(str(q), (mod, pt))
    return out


# 批次28 · 快照图片本地化目录（data/qimg/fenbi/，内容寻址幂等；与题库采集
# 共用 /api/qimg/ 公开只读端点，文件名为 sha1 前缀，与学员身份无关联）
_QIMG_DIR = "fenbi"


def _sol_row(sol: dict, qid: str, qid_paths: dict) -> dict:
    """粉笔 solution → 快照行（复用 17 号管线字段规整）。

    批次28：题干/材料/解析走图片本地化管线（fenbi._download_imgs →
    data/qimg/fenbi/ 内容寻址落盘 → ![图N](/api/qimg/…) 标记，单张失败
    降级 [图N] 占位），修复「图片未显示」「资料分析缺材料」「解析配图丢失」。"""
    mod, pt = qid_paths.get(qid, ("", ""))
    kps = ",".join(str(k.get("name") or "")
                   for k in (sol.get("keypoints") or [])[:2])
    try:
        d10 = int(sol.get("difficulty") or 0)
    except (TypeError, ValueError):
        d10 = 0
    raw_content = str(sol.get("content") or "")
    m = sol.get("material")
    raw_material = (str(m.get("content") or "") if isinstance(m, dict)
                    else str(m or ""))
    raw_solution = str(sol.get("solution") or "")
    raw_content, _ = fenbi._download_imgs(raw_content, _QIMG_DIR)
    raw_material, _ = fenbi._download_imgs(raw_material, _QIMG_DIR)
    raw_solution, _ = fenbi._download_imgs(raw_solution, _QIMG_DIR)
    content = fenbi._strip_html(raw_content)
    material = fenbi._strip_html(raw_material)
    # 批次28：材料不并入 content，独立存 material 列——端上分区块渲染
    # （「资料」卡与题干卡分离），快照表结构即展示契约
    return {
        "question_id": qid, "module": mod,
        "keypoint": pt or kps,
        "content": content,
        "material": material,
        "options": [fenbi._strip_html(o) for o in fenbi._options_of(sol)],
        "answer": fenbi._answer_letters(sol),
        "analysis": fenbi._strip_html(raw_solution),
        "difficulty": (d10 + 1) // 2 or 2,
        "source": str(sol.get("source") or sol.get("shortSource") or "")}


def _sync_snapshot(store: FenbiBindStore, user_id: str, cookie: str,
                   tree: list, existing: set[str], save_fn,
                   progress=None) -> tuple[int, int]:
    """树 + 增量 qids → 拉详情 → save_fn 落库；返回 (总题数, 新增数)。
    save_fn(user_id, rows) 由调用方注入（错题/收藏表）。"""
    qid_paths = _tree_qid_paths(tree)
    qids = sorted(qid_paths.keys())
    todo = [q for q in qids if q not in existing]
    if progress:
        progress(f"拉取题目详情（新增 {len(todo)}/{len(qids)}）", 30)
    rows: list[dict] = []
    for i in range(0, len(todo), _BATCH):
        chunk = todo[i:i + _BATCH]
        for sol in fetch_solutions_batch(cookie, chunk):
            if isinstance(sol, dict) and sol.get("id"):
                rows.append(_sol_row(sol, str(sol["id"]), qid_paths))
        if progress:
            done = min(i + _BATCH, len(todo))
            pct = 30 + int(60 * done / max(len(todo), 1))
            progress(f"拉取题目详情 {done}/{len(todo)}", pct)
    saved = save_fn(user_id, rows) if rows else 0
    return len(qids), saved


def sync_wrong(store: FenbiBindStore, user_id: str,
               progress=None) -> tuple[int, int]:
    """错题同步（增量）。返回 (总错题数, 新增快照数)。"""
    cookie = store.get_cookie(user_id)
    tree = fetch_error_tree(cookie)              # 失效抛 FenbiAuthError
    total, saved = _sync_snapshot(store, user_id, cookie, tree,
                                  store.wrong_ids(user_id),
                                  store.save_wrong_questions, progress)
    return total, saved


def sync_collect(store: FenbiBindStore, user_id: str,
                 progress=None) -> tuple[int, int]:
    """收藏同步（增量）。返回 (总收藏数, 新增快照数)。"""
    cookie = store.get_cookie(user_id)
    tree = fetch_collect_tree(cookie)
    total, saved = _sync_snapshot(store, user_id, cookie, tree,
                                  store.collect_ids(user_id),
                                  store.save_collect_questions, progress)
    return total, saved


def _extract_basic(report: dict) -> dict:
    """getReport.subReports → {basic, courseReports}。"""
    basic, courses = {}, []
    for sub in report.get("subReports") or []:
        if isinstance(sub, dict) and sub.get("score") is not None \
                and sub.get("fullmark") is not None and not basic:
            basic = sub
        if isinstance(sub, dict) and sub.get("courseReports"):
            courses = sub["courseReports"]
    return {"basic": basic, "courses": courses}


def sync_mock(store: FenbiBindStore, user_id: str, progress=None,
              max_exams: int = 20, interval: float = 1.2) -> tuple[int, int]:
    """模考/练习同步（增量）：试卷+练习历史 → 每份已提交卷
    getReport（整卷分析）+ getSolution（题目+作答）落库。
    返回 (历史已完成份数, 新同步份数)。"""
    cookie = store.get_cookie(user_id)
    have = store.mock_exam_keys(user_id)
    todo: list[dict] = []
    done_total = 0
    for cat in (1, 3):                            # 1=试卷(模考/真题) 3=练习
        h = fetch_exercise_history(cookie, cat, 30)
        for it in h.get("items") or []:
            key = str(it.get("exerciseKey") or "")
            if not key:
                continue
            if int(it.get("status") or 0) != 1:
                continue                          # 仅已提交（有报告）的
            done_total += 1
            if key not in have:
                todo.append({"key": key, "name": it.get("sheetName") or "",
                             "sheetType": it.get("sheetType") or 0})
    todo = todo[:max_exams]
    n_synced = 0
    for idx, item in enumerate(todo):
        if progress:
            progress(f"同步试卷 {idx + 1}/{len(todo)}：{item['name'][:16]}",
                     20 + int(70 * idx / max(len(todo), 1)))
        report = fetch_mock_report(cookie, item["key"], interval)
        sol = fetch_mock_solution(cookie, item["key"], interval)
        basic = _extract_basic(report).get("basic") or {}
        sol_by_id = {str(s.get("id")): s for s in sol.get("solutions") or []
                     if s.get("id")}
        answers, n_right, n_wrong, n_un = [], 0, 0, 0
        for gk, ua in (sol.get("userAnswers") or {}).items():
            qid = str(ua.get("id") or "")
            st = int(ua.get("status") or 0)
            if st == ANSWER_STATUS["right"]:
                n_right += 1
            elif st == ANSWER_STATUS["wrong"]:
                n_wrong += 1
            else:
                n_un += 1
            raw = sol_by_id.get(qid) or {}
            # 批次28：模考明细同走图片本地化 + 材料采集（与错题/收藏同管线）
            _row = _sol_row(raw, qid, {}) if raw else {}
            answers.append({
                "question_id": qid, "global_key": str(ua.get("key") or gk),
                "status": st,
                "user_answer": json.dumps(ua.get("answer") or "",
                                          ensure_ascii=False),
                "correct_answer": json.dumps(raw.get("correctAnswer") or "",
                                             ensure_ascii=False),
                "content": _row.get("content") or "",
                "material": _row.get("material") or "",
                "analysis": _row.get("analysis") or "",
                "keypoints": [str(k.get("name") or "")
                              for k in (raw.get("keypoints") or [])[:3]]})
        exam = {
            "exercise_key": item["key"],
            "name": report.get("name") or item["name"],
            "sheet_type": report.get("sheetType") or item["sheetType"],
            "score": basic.get("score"),
            "fullmark": basic.get("fullmark"),
            "question_count": len(answers),
            "correct_count": n_right, "wrong_count": n_wrong,
            "unanswered": n_un,
            "duration_sec": int(basic.get("time") or 0),
            "submit_time": _ms_to_dt(basic.get("submitTime")),
            "difficulty": basic.get("difficulty"),
            "paper_id": basic.get("paperId") or "",
            "report": report}
        store.save_mock_exam(user_id, exam, answers)
        n_synced += 1
    return done_total, n_synced


# ---------------- 批次28 · 快照联动系统错题本/收藏夹 ----------------

_NOTE_MAX = 600            # 解析写入系统错题本/收藏夹的截断长度
_SYS_TEACHER = "T001"      # 系统侧老师维度（错题本/收藏夹按此归属）
_QLETTERS = "ABCDEFGH"


def _snapshot_question_text(row: dict) -> str:
    """快照行 → 系统错题本/收藏夹的 question 文本（材料+题干+选项）。"""
    parts = []
    material = str(row.get("material") or "").strip()
    if material:
        parts.append("【资料】" + material)
    content = str(row.get("content") or "").strip()
    if content:
        parts.append(content)
    opts = row.get("options") or []
    if isinstance(opts, str):
        try:
            opts = json.loads(opts or "[]")
        except ValueError:
            opts = []
    if opts:
        parts.append("\n".join(
            f"{_QLETTERS[i]}. {o}" for i, o in enumerate(opts[:8])))
    return "\n".join(parts).strip()


def _note_of(row: dict) -> str:
    """快照行 → 答案+解析备注文本。"""
    note = "答案：" + (str(row.get("answer") or "").strip() or "—")
    ana = str(row.get("analysis") or "").strip()
    if ana:
        note += "\n解析：" + ana[:_NOTE_MAX]
    return note


def sync_into_system(store: "FenbiBindStore", study_store, user_id: str,
                     progress=None) -> dict:
    """批次28：粉笔快照（错题/收藏）联动写入系统错题本与收藏夹。

    版权口径：付费内容以快照形式仅进学员本人的错题本/收藏夹（与快照表
    同级隔离），不入共享题库。幂等：study 侧 ext_id 去重——
    'fb:{qid}'=错题，'fbc:{qid}'=收藏。返回 {mistakes_new, favorites_new}。"""
    with store._connect() as conn:
        wrongs = [dict(r) for r in conn.execute(
            "SELECT question_id, module, keypoint, content, material,"
            " options, answer, analysis FROM fenbi_wrong_questions"
            " WHERE user_id=? ORDER BY id DESC", (user_id,))]
        collects = [dict(r) for r in conn.execute(
            "SELECT question_id, module, keypoint, content, material,"
            " options, answer, analysis FROM fenbi_collect_questions"
            " WHERE user_id=? ORDER BY id DESC", (user_id,))]
    have = study_store.ext_ids(user_id)
    m_new = f_new = 0
    total = len(wrongs) + len(collects)
    for i, r in enumerate(wrongs + collects):
        if progress and i % 200 == 0:
            progress(f"写入系统错题本/收藏夹 {i}/{total}",
                     80 + int(18 * i / max(total, 1)))
        is_wrong = i < len(wrongs)
        ext = ("fb:" if is_wrong else "fbc:") + str(r["question_id"])
        if ext in have:
            continue
        q = _snapshot_question_text(r)
        if not q:
            continue
        tag = (f"【粉笔错题 · {r['module']}{' · ' + r['keypoint'] if r['keypoint'] else ''}】\n"
               if is_wrong else
               f"【粉笔收藏 · {r['module']}{' · ' + r['keypoint'] if r['keypoint'] else ''}】\n")
        if is_wrong:
            study_store.add_mistake(user_id, _SYS_TEACHER, tag + q, "",
                                    _note_of(r), [], session_id="fenbi",
                                    ext_id=ext)
            m_new += 1
        else:
            study_store.add_favorite(user_id, _SYS_TEACHER, tag + q,
                                     _note_of(r), [], session_id="fenbi",
                                     group="粉笔收藏", ext_id=ext)
            f_new += 1
        have.add(ext)
    return {"mistakes_new": m_new, "favorites_new": f_new}
