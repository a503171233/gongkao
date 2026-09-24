# -*- coding: utf-8 -*-
"""G1 智能组卷：按课程分类/题型/难度/知识点自助组卷 → 在线作答 → 客观题自动评分。
独立 smartexam.db。与 F1 模考（整卷上传）互补：本模块纯查询组装 question_bank，
零 LLM 依赖，秒级出卷，客观题提交即判分。
"""
import json
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import Config


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _grade_objective(qtype: str, correct: str, user_answer: str,
                     trusted: bool = True) -> float:
    """客观题判分：选择/判断 → 0/1；无答案/答案待核/主观题 → -1（待批改，不计对错）。"""
    qtype = qtype or ""
    ua = (user_answer or "").strip().upper()
    ca = (correct or "").strip().upper()
    # 无答案题（仅题干+选项的练习卷）或答案待核题：不可判分，
    # 否则会一律判错并拉低组卷得分
    if not ca or not trusted:
        return -1.0
    if qtype == "choice":
        return 1.0 if ua == ca else 0.0
    if qtype == "judge":
        norm = {"对": "对", "错": "错", "正确": "对", "错误": "错",
                "T": "对", "F": "错", "TRUE": "对", "FALSE": "错",
                "√": "对", "×": "错", "X": "错"}
        return 1.0 if norm.get(ua, ua) == ca else 0.0
    return -1.0


class SmartExamStore:
    def __init__(self, db_path: str | Path | None = None):
        default = Config.data_dir / "smartexam.db"
        self.db_path = Path(db_path or __import__("os").environ.get("SMARTEXAM_DB") or default)
        if not self.db_path.is_absolute():
            self.db_path = Config.ROOT / self.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS smartexam_papers (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      TEXT NOT NULL,
                title        TEXT NOT NULL,
                teacher_id   TEXT NOT NULL DEFAULT '',
                config       TEXT NOT NULL DEFAULT '{}',   -- JSON 组卷条件快照
                questions    TEXT NOT NULL DEFAULT '[]',   -- JSON 题目快照（含答案，答题时剥除）
                answers      TEXT NOT NULL DEFAULT '{}',   -- JSON {question_id: user_answer}
                status       TEXT NOT NULL DEFAULT 'draft',  -- 'draft' | 'graded'
                total        INTEGER NOT NULL DEFAULT 0,
                correct      INTEGER NOT NULL DEFAULT 0,
                score        REAL,                          -- 客观题正确率 0-1（无客观题则 None）
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_smartexam_user ON smartexam_papers(user_id, id);
            """)

    # ---------- 序列化 ----------
    def _row_to_paper(self, row: sqlite3.Row, strip_answer: bool = False) -> dict:
        d = dict(row)
        for k in ("config", "questions", "answers"):
            try:
                d[k] = json.loads(d.get(k) or ("{}" if k == "answers" else "[]"))
            except (ValueError, TypeError):
                d[k] = {} if k == "answers" else []
        if strip_answer and isinstance(d.get("questions"), list):
            d["questions"] = [
                {k: v for k, v in q.items() if k not in ("answer", "analysis")}
                for q in d["questions"]
            ]
        return d

    # ---------- 题目快照（对外剥答案） ----------
    @staticmethod
    def _strip_answer(q: dict) -> dict:
        return {k: v for k, v in q.items()
                if k not in ("answer", "analysis", "answer_status")}

    # ---------- 组卷 ----------
    def create_paper(self, user_id: str, title: str, teacher_id: str,
                     config: dict, questions: list[dict]) -> int:
        now = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO smartexam_papers(user_id, title, teacher_id, config, questions, "
                "status, total, created_at, updated_at) "
                "VALUES(?,?,?,?,?,'draft',?,?,?)",
                (user_id, title, teacher_id, json.dumps(config, ensure_ascii=False),
                 json.dumps(questions, ensure_ascii=False), len(questions), now, now))
            return int(cur.lastrowid)

    def get_paper(self, paper_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM smartexam_papers WHERE id=?", (paper_id,)).fetchone()
        return self._row_to_paper(row) if row else None

    def get_paper_for_user(self, paper_id: int, user_id: str) -> dict | None:
        p = self.get_paper(paper_id)
        if p is None or p["user_id"] != user_id:
            return None
        return p

    def list_papers(self, user_id: str, limit: int = 50, offset: int = 0) -> tuple[int, list[dict]]:
        with self._connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM smartexam_papers WHERE user_id=?", (user_id,)
            ).fetchone()["n"]
            rows = conn.execute(
                "SELECT * FROM smartexam_papers WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset)).fetchall()
        papers = []
        for r in rows:
            d = dict(r)
            try:
                d["config"] = json.loads(d.get("config") or "{}")
            except (ValueError, TypeError):
                d["config"] = {}
            papers.append(d)
        return int(n), papers

    def delete_paper(self, paper_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM smartexam_papers WHERE id=?", (paper_id,))

    def submit_paper(self, paper_id: int, answers: dict) -> dict:
        """提交作答 → 客观题自动评分。返回 {paper, correct, total, score, essay_pending}。
        客观题判分后写回 answers + status=graded；主观题记录待批改（不评）。"""
        p = self.get_paper(paper_id)
        if p is None:
            raise ValueError("试卷不存在")
        questions = p["questions"]
        correct = 0
        objective = 0
        essay = 0
        # answers: {question_id(str): answer(str)}
        graded: dict = {}
        for q in questions:
            qid = str(q.get("id"))
            ua = (answers or {}).get(qid, "")
            qtype = q.get("qtype") or ""
            s = _grade_objective(
                qtype, q.get("answer") or "", ua,
                trusted=(q.get("answer_status") or "").strip() != "pending")
            if s == -1.0:
                essay += 1
                graded[qid] = ua
            else:
                objective += 1
                graded[qid] = ua
                if s == 1.0:
                    correct += 1
        score = round(correct / objective, 4) if objective else None
        with self._connect() as conn:
            conn.execute(
                "UPDATE smartexam_papers SET answers=?, status='graded', correct=?, score=?, "
                "updated_at=? WHERE id=?",
                (json.dumps(graded, ensure_ascii=False), correct, score, _now_iso(), paper_id))
        return {
            "paper_id": paper_id,
            "total": len(questions),
            "objective": objective,
            "correct": correct,
            "score": score,
            "essay_pending": essay,
        }

    def score_trend(self, user_id: str, days: int = 30) -> dict:
        """#20 学习报告：模考成绩趋势（仅 graded 且有客观分），按日聚合。
        返回 {trend: [{d, papers, avg_score, best_score}]（日期升序）, summary}。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT substr(created_at,1,10) d, COUNT(*) papers, "
                "AVG(score) avg_score, MAX(score) best_score "
                "FROM smartexam_papers "
                "WHERE user_id=? AND status='graded' AND score IS NOT NULL "
                "GROUP BY d ORDER BY d DESC LIMIT ?",
                (user_id, days),
            ).fetchall()
            summary = conn.execute(
                "SELECT COUNT(*) papers, AVG(score) avg_score, MAX(score) best_score "
                "FROM smartexam_papers WHERE user_id=? AND status='graded' AND score IS NOT NULL",
                (user_id,),
            ).fetchone()
        trend = [{
            "d": r["d"],
            "papers": r["papers"],
            "avg_score": round(r["avg_score"], 4) if r["avg_score"] is not None else None,
            "best_score": round(r["best_score"], 4) if r["best_score"] is not None else None,
        } for r in rows]
        trend.reverse()  # 升序（供前端趋势图）
        return {
            "trend": trend,
            "summary": {
                "papers": summary["papers"] or 0,
                "avg_score": round(summary["avg_score"], 4) if summary["avg_score"] is not None else None,
                "best_score": round(summary["best_score"], 4) if summary["best_score"] is not None else None,
            },
        }


_store: Optional[SmartExamStore] = None
_store_lock = __import__("threading").Lock()


def get_smartexam_store() -> SmartExamStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = SmartExamStore()
    return _store