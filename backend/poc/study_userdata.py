# -*- coding: utf-8 -*-
"""C4 · 学员增值功能 — StudyStore：收藏 / 答案反馈 / 错题本。"""
import sqlite3

from . import study as _study


def _now_iso():
    """Late-bound：转发 poc.study._now_iso，使测试对该函数的 patch 仍然生效。"""
    return _study._now_iso()


class StudyUserdataMixin:
    """收藏 / 答案反馈 / 错题本 方法集合。"""

    # ==================== 收藏 ====================
    def add_favorite(self, user_id: str, teacher_id: str, question: str,
                     answer: str, refs: list[dict] | None = None,
                     session_id: str = "") -> dict:
        """收藏一条问答记录。"""
        # 检查限额（匿名不限）
        if user_id != 'anonymous':
            count = self._count_favorites(user_id)
            # 这里简化：不强制限制，实际可按需开启
        now = _now_iso()
        refs_json = ''
        if refs:
            import json
            refs_json = json.dumps(refs, ensure_ascii=False)
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO favorites(user_id, teacher_id, question, answer, refs, session_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, teacher_id, question, answer, refs_json, session_id, now),
            )
            fid = cur.lastrowid
        return {
            "id": fid,
            "user_id": user_id,
            "teacher_id": teacher_id,
            "question": question,
            "answer": answer,
            "refs": refs or [],
            "session_id": session_id,
            "created_at": now,
        }

    def list_favorites(self, user_id: str, teacher_id: str | None = None,
                       limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            if teacher_id:
                rows = conn.execute(
                    "SELECT * FROM favorites WHERE user_id=? AND teacher_id=? ORDER BY created_at DESC LIMIT ?",
                    (user_id, teacher_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM favorites WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
        return [self._parse_refs(r) for r in rows]

    def delete_favorite(self, user_id: str, fav_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM favorites WHERE id=? AND user_id=?",
                (fav_id, user_id),
            )
        return cur.rowcount > 0

    def _count_favorites(self, user_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM favorites WHERE user_id=?",
                (user_id,),
            ).fetchone()
        return row["cnt"] if row else 0

    # ==================== 答案反馈（A1） ====================
    def add_feedback(self, user_id: str, teacher_id: str, session_id: str,
                     question: str, answer: str, rating: str,
                     reason: str = "") -> dict:
        """提交答案反馈（👍/👎）。rating: 'up' | 'down'。"""
        if rating not in ("up", "down"):
            raise ValueError("rating 必须是 up 或 down")
        now = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO answer_feedback
                   (user_id, teacher_id, session_id, question, answer, rating, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, teacher_id, session_id, question, answer, rating, reason, now),
            )
            fid = cur.lastrowid
        return {"id": fid, "rating": rating, "status": "new", "created_at": now}

    def set_feedback_status(self, fid: int, status: str, note: str = "") -> dict:
        """#26 R3 更新反馈处理状态。status: 'new' | 'resolved'（工单式闭环）。"""
        if status not in ("new", "resolved"):
            raise ValueError("status 必须是 new 或 resolved")
        with self._connect() as conn:
            r = conn.execute(
                "SELECT id FROM answer_feedback WHERE id=?", (fid,)).fetchone()
            if not r:
                raise ValueError(f"反馈不存在: {fid}")
            if status == "resolved":
                conn.execute(
                    "UPDATE answer_feedback SET status=?, handler_note=?, handled_at=? WHERE id=?",
                    (status, note, _now_iso(), fid))
            else:
                conn.execute(
                    "UPDATE answer_feedback SET status='new', handler_note='', handled_at='' WHERE id=?",
                    (fid,))
        return self.get_feedback(fid)

    def get_feedback(self, fid: int) -> dict:
        """#26 R3 查询单条反馈。"""
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM answer_feedback WHERE id=?", (fid,)).fetchone()
        if not r:
            raise ValueError(f"反馈不存在: {fid}")
        return dict(r)

    def list_feedback(self, teacher_id: str | None = None,
                      status: str | None = None,
                      limit: int = 100) -> list[dict]:
        """反馈列表（管理端统计用）。#26 R3 支持 status 筛选。"""
        sql = "SELECT * FROM answer_feedback"
        conds, args = [], []
        if teacher_id:
            conds.append("teacher_id=?")
            args.append(teacher_id)
        if status in ("new", "resolved"):
            conds.append("status=?")
            args.append(status)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def feedback_stats(self, teacher_id: str | None = None) -> dict:
        """反馈汇总：总条数/好评/差评/待处理（管理端统计用）。"""
        where, args = "", []
        if teacher_id:
            where = "WHERE teacher_id=?"
            args.append(teacher_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT rating, COUNT(*) AS n FROM answer_feedback " + where +
                " GROUP BY rating", args).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM answer_feedback " + where, args).fetchone()["n"]
            pend = conn.execute(
                "SELECT COUNT(*) AS n FROM answer_feedback " + where +
                (" AND " if where else "WHERE ") + "status='new'", args).fetchone()["n"]
        stats = {"total": total, "up": 0, "down": 0, "up_pct": 0.0, "pending": pend or 0}
        for r in rows:
            if r["rating"] in stats:
                stats[r["rating"]] = r["n"]
        if total:
            stats["up_pct"] = round(stats["up"] / total * 100, 1)
        return stats

    def _parse_refs(self, row: sqlite3.Row) -> dict:
        r = dict(row)
        if r.get("refs"):
            import json
            try:
                r["refs"] = json.loads(r["refs"])
            except (ValueError, TypeError):
                r["refs"] = []
        else:
            r["refs"] = []
        return r

    # ==================== 错题本 ====================
    def add_mistake(self, user_id: str, teacher_id: str, question: str,
                    user_answer: str, correct_note: str,
                    refs: list[dict] | None = None, session_id: str = "",
                    question_id: int | None = None) -> dict:
        """记录错题。question_id 关联题库原题（#36 错题重练）。"""
        now = _now_iso()
        refs_json = ''
        if refs:
            import json
            refs_json = json.dumps(refs, ensure_ascii=False)
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO mistakes(user_id, teacher_id, question, user_answer, correct_note, refs, session_id, created_at, question_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, teacher_id, question, user_answer, correct_note, refs_json, session_id, now, question_id),
            )
            mid = cur.lastrowid
        return {
            "id": mid,
            "user_id": user_id,
            "teacher_id": teacher_id,
            "question": question,
            "user_answer": user_answer,
            "correct_note": correct_note,
            "refs": refs or [],
            "session_id": session_id,
            "question_id": question_id,
            "created_at": now,
        }

    def list_mistakes(self, user_id: str, teacher_id: str | None = None,
                      limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            if teacher_id:
                rows = conn.execute(
                    "SELECT * FROM mistakes WHERE user_id=? AND teacher_id=? ORDER BY created_at DESC LIMIT ?",
                    (user_id, teacher_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM mistakes WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
        return [self._parse_mistake_refs(r) for r in rows]

    def delete_mistake(self, user_id: str, mist_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM mistakes WHERE id=? AND user_id=?",
                (mist_id, user_id),
            )
        return cur.rowcount > 0

    def _parse_mistake_refs(self, row: sqlite3.Row) -> dict:
        r = dict(row)
        if r.get("refs"):
            import json
            try:
                r["refs"] = json.loads(r["refs"])
            except (ValueError, TypeError):
                r["refs"] = []
        else:
            r["refs"] = []
        return r
