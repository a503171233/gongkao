# -*- coding: utf-8 -*-
"""C4 · 学员增值功能 — StudyStore：练习抽题 / 判分 / 复习排程 / 学情统计。"""
import json
import random
from datetime import datetime, timezone

from . import study as _study
from .study_common import (
    REVIEW_INTERVALS_DAYS, REVIEW_MASTER_STAGE, _teacher_scope_sql,
)


def _now_iso():
    return _study._now_iso()


class StudyPracticeMixin:
    """练习 / 复习 / 统计 方法集合。"""

    @staticmethod
    def _answer_trusted(q: dict) -> bool:
        """该题答案是否可信到可用于判分。

        不可信的两类：
          · 无答案（如仅题干+选项的练习卷）
          · answer_status='pending'：采集所得答案相互矛盾或为占位符，
            待人工核实。判分会把用户误记错题本并推进复习排程。
        """
        if not (q.get("answer") or "").strip():
            return False
        return (q.get("answer_status") or "").strip() != "pending"

    def _grade_auto(self, q: dict, user_answer: str) -> float:
        """客观题自动判分（0/1）。主观题或答案不可信题返回 -1（待评分，不计对错）。"""
        qtype = q["qtype"]
        ua = (user_answer or "").strip().upper()
        ans = (q["answer"] or "").strip()
        if not self._answer_trusted(q):
            return -1.0
        if qtype == "choice":
            return 1.0 if ua == ans.upper() else 0.0
        if qtype == "judge":
            norm = {"对": "对", "错": "错", "正确": "对", "错误": "错",
                    "T": "对", "F": "错", "TRUE": "对", "FALSE": "错",
                    "√": "对", "×": "错", "X": "错"}
            return 1.0 if norm.get(ua, ua) == ans else 0.0
        return -1.0  # 主观题：待人工/LLM 评分

    @staticmethod
    def _parse_qrows(rows) -> list[dict]:
        """question_bank 行批量解析：options JSON / images 白名单。"""
        out = []
        for r in rows:
            d = dict(r)
            if d.get("options"):
                import json
                try:
                    d["options"] = json.loads(d["options"])
                except (ValueError, TypeError):
                    d["options"] = []
            else:
                d["options"] = []
            d["images"] = _parse_images(d.get("images"))  # #33 R2 题干配图
            out.append(d)
        return out

    def _practice_payload(self, q: dict) -> dict:
        """题库行 → 练习响应（#36 附 category 供前端徽标）。"""
        resp = {
            "question": q["question"],
            "type": q["qtype"],
            "options": q.get("options") or [],
            "id": q["id"],
            "hint": self._qtype_label(q["qtype"]),
            "category": q.get("category") or "综合",
            # 无答案题（仅题干+选项的练习卷）或答案待核题：前端据此提示"暂无答案"，
            # 不再误走主观题 AI 批改分支（score=-1 与"主观题待评"共用）
            "has_answer": self._answer_trusted(q),
        }
        if q["qtype"] != "choice":
            resp.pop("options", None)
        return resp

    def _wrong_pool(self, teacher_id: str, user_id: str,
                    category: str | None = None) -> list[dict]:
        """#36 错题池：做错过（score=0）且从未做对过的题。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT question_id FROM practice_logs "
                "WHERE user_id=? AND teacher_id=? AND question_id IS NOT NULL AND score>=0 "
                "GROUP BY question_id "
                "HAVING SUM(CASE WHEN score>0 THEN 1 ELSE 0 END)=0 "
                "AND SUM(CASE WHEN score=0 THEN 1 ELSE 0 END)>0",
                (user_id, teacher_id),
            ).fetchall()
        ids = [r["question_id"] for r in rows]
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        sql = f"SELECT * FROM question_bank WHERE id IN ({ph}) AND {_teacher_scope_sql()} AND enabled=1"
        args = [*ids, teacher_id]
        if category:
            sql += " AND category=?"
            args.append(category)
        with self._connect() as conn:
            qrows = conn.execute(sql + " ORDER BY id", args).fetchall()
        return self._parse_qrows(qrows)

    def get_next_practice(self, teacher_id: str, user_id: str = "anonymous",
                          category: str | None = None, mode: str | None = None,
                          question_id: int | None = None) -> dict:
        """
        抽一道练习题（#36 增强：分类专项 / 只刷错题 / 指定题重练）。
        - question_id：指定题库题重练（错题本「重练」入口）
        - mode="wrong"：只抽「做错过且从未做对」的客观题
        - category：#35 分类专项筛选
        策略：优先题库（未做过/做错优先），题库为空回落知识库知识点兜底。
        """
        import random

        # 0) 指定题重练
        if question_id:
            pool = self._qbank_pool(teacher_id)
            q = next((x for x in pool if x["id"] == question_id), None)
            if q:
                return self._practice_payload(q)

        # 1) 只刷错题：错题池空则回落普通抽题（mode_fallback 提示前端）
        if mode == "wrong":
            wrong_pool = self._wrong_pool(teacher_id, user_id, category)
            if wrong_pool:
                return self._practice_payload(random.choice(wrong_pool))

        # 2) 正常抽题（可带分类）
        pool = self._qbank_pool(teacher_id, category=category)
        if pool:
            # 查询用户做过的题（客观题自动判分后不再重复；主观题可重做）
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT question, score FROM practice_logs "
                    "WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
                    (user_id,),
                ).fetchall()
            done_right = {r["question"] for r in rows
                          if r["score"] is not None and r["score"] >= 0.99}
            # 优先未做对过的题；全部做对则随机
            candidates = [q for q in pool if q["question"] not in done_right]
            if not candidates:
                candidates = pool
            q = random.choice(candidates)
            resp = self._practice_payload(q)
            if mode == "wrong":
                resp["mode_fallback"] = "no_wrong"  # 错题池空，已回落普通抽题
            return resp

        # 兜底：知识库知识点
        fallback_questions = self._get_fallback_questions(teacher_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT question, score FROM practice_logs WHERE user_id=? ORDER BY created_at DESC LIMIT 10",
                (user_id,),
            ).fetchall()
        done_questions = {r["question"] for r in rows if r["score"] is not None}
        for q in fallback_questions:
            if q not in done_questions:
                return {"question": q, "type": "knowledge_point",
                        "hint": "请先思考，再查看答案"}
        import random
        q = random.choice(fallback_questions) if fallback_questions else "请回答：公考言语理解的核心方法是什么？"
        return {"question": q, "type": "knowledge_point", "hint": "这是一道复习题"}

    @staticmethod
    def _qtype_label(qtype: str) -> str:
        return {"choice": "选择题，选择你认为正确的选项", "judge": "判断题，回答「对」或「错」",
                "essay": "简答题，请组织语言作答"}.get(qtype, "")

    def submit_practice(self, user_id: str, teacher_id: str, question: str,
                        answer: str, score: float | None = None,
                        feedback: str = "", question_id: int | None = None) -> dict:
        """提交练习答案并评分。
        客观题（题库）自动判分；主观题/知识点题 score 为 None 时标记待评分(-1)。
        """
        # 题库题自动判分
        auto_score = None
        qcat = ""
        if question_id:
            with self._connect() as conn:
                qrow = conn.execute(
                    "SELECT * FROM question_bank WHERE id=?", (question_id,)
                ).fetchone()
            if qrow:
                q = dict(qrow)
                qcat = q.get("category") or ""
                auto_score = self._grade_auto(q, answer)
                if auto_score is not None and auto_score >= 0:
                    score = auto_score
                    if feedback == "":
                        feedback = ("回答正确" if auto_score == 1.0
                                    else f"回答错误，正确答案：{q['answer']}")
                    if auto_score == 0.0:
                        # 客观题答错 → 自动记错题本（#36 带原题关联）
                        self.add_mistake(user_id, teacher_id, q["question"],
                                         answer, q.get("analysis") or f"正确答案：{q['answer']}",
                                         question_id=question_id)
                    # #16 艾宾浩斯节奏：客观题答对/答错均推进复习排程
                    self._record_review_event(user_id, question_id,
                                              correct=(auto_score > 0.0))
        now = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO practice_logs(user_id, teacher_id, question, answer, score, feedback, created_at, question_id, category)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, teacher_id, question, answer,
                 score if score is not None else -1, feedback, now,
                 question_id, qcat),
            )
            pid = cur.lastrowid
        return {
            "id": pid,
            "user_id": user_id,
            "teacher_id": teacher_id,
            "question": question,
            "answer": answer,
            "score": score,
            "auto_graded": auto_score is not None and auto_score >= 0,
            "feedback": feedback,
            "question_id": question_id,
            "category": qcat or "综合",
            "created_at": now,
        }

    def list_practice_logs(self, user_id: str, teacher_id: str | None = None,
                           limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            if teacher_id:
                rows = conn.execute(
                    "SELECT * FROM practice_logs WHERE user_id=? AND teacher_id=? ORDER BY created_at DESC LIMIT ?",
                    (user_id, teacher_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM practice_logs WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def all_practice_logs(self) -> list[dict]:
        """全量练习记录（按用户、插入序），供 #18 激励体系全量重算。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM practice_logs ORDER BY user_id, id").fetchall()
        return [dict(r) for r in rows]

    def is_mistake(self, user_id: str, question_id: int) -> bool:
        """#18 激励：某题是否在用户错题本中（攻克错题积分/成就算法用）。"""
        if question_id is None:
            return False
        with self._connect() as conn:
            r = conn.execute(
                "SELECT 1 FROM mistakes WHERE user_id=? AND question_id=? LIMIT 1",
                (user_id, question_id)).fetchone()
        return r is not None

    def review_stage_of(self, user_id: str, question_id: int) -> int | None:
        """#18 激励：返回用户某题当前复习阶段（review_schedule.stage）；
        不在排程队列中返回 None（未答错过的题无复习排程）。"""
        if question_id is None:
            return None
        with self._connect() as conn:
            r = conn.execute(
                "SELECT stage FROM review_schedule WHERE user_id=? AND question_id=?",
                (user_id, question_id)).fetchone()
        return int(r["stage"]) if r else None

    # ==================== #16 错题强化重练：艾宾浩斯复习节奏 ====================
    @staticmethod
    def _review_due_at(stage: int) -> str:
        """按当前 stage 计算下一轮复习时间（ISO）。stage=0 → 立即；1~5 → INTERVALS[stage-1] 天后。"""
        from datetime import timedelta
        if stage <= 0:
            return _now_iso()
        days = REVIEW_INTERVALS_DAYS[min(stage - 1, len(REVIEW_INTERVALS_DAYS) - 1)]
        return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _record_review_event(self, user_id: str, question_id: int, correct: bool) -> None:
        """答题后推进艾宾浩斯节奏：
        - 答错：stage 归零，立即到期（进入待巩固队列）。
        - 答对：stage+1；达到 MASTER_STAGE 视为已掌握（移出队列）；否则按间隔顺延。
        """
        if question_id is None:
            return
        now = _now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT stage FROM review_schedule WHERE user_id=? AND question_id=?",
                (user_id, question_id),
            ).fetchone()
            if not correct:
                # 答错：重置为 0，立即到期；缺身份时新建
                conn.execute(
                    "INSERT INTO review_schedule(user_id, question_id, stage, next_review_at, updated_at) "
                    "VALUES(?,?,0,?,?) "
                    "ON CONFLICT(user_id, question_id) DO UPDATE SET stage=0, next_review_at=excluded.next_review_at, updated_at=excluded.updated_at",
                    (user_id, question_id, now, now),
                )
                return
            # 答对
            if row is None:
                # 从未入队（非错题）→ 无需排程
                return
            new_stage = int(row["stage"]) + 1
            if new_stage >= REVIEW_MASTER_STAGE:
                conn.execute(
                    "DELETE FROM review_schedule WHERE user_id=? AND question_id=?",
                    (user_id, question_id),
                )
            else:
                due = self._review_due_at(new_stage)
                conn.execute(
                    "UPDATE review_schedule SET stage=?, next_review_at=?, updated_at=? "
                    "WHERE user_id=? AND question_id=?",
                    (new_stage, due, now, user_id, question_id),
                )

    def list_due_reviews(self, user_id: str, teacher_id: str | None = None,
                         limit: int = 50) -> dict:
        """#16 今日待巩固：到期（next_review_at<=now）的错题队列，按最急优先。
        关联 question_bank 取题干/选项/知识点/分类。若 question_bank 缺行（题已删），自动清理排程。"""
        now = _now_iso()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT s.id, s.question_id, s.stage, s.next_review_at "
                "FROM review_schedule s "
                "WHERE s.user_id=? AND s.next_review_at<=? ORDER BY s.next_review_at ASC, s.id ASC LIMIT ?",
                (user_id, now, limit),
            ).fetchall()
        out = []
        orphan_ids = []
        with self._connect() as conn:
            for r in rows:
                q = conn.execute(
                    "SELECT * FROM question_bank WHERE id=? AND enabled=1",
                    (r["question_id"],),
                ).fetchone()
                if q is None:
                    orphan_ids.append(r["id"])
                    continue
                if teacher_id and q["teacher_id"] not in (teacher_id, SHARED_TEACHER_ID):
                    continue
                item = self._practice_payload(dict(q))
                item["review_stage"] = r["stage"]
                item["next_review_at"] = r["next_review_at"]
                out.append(item)
        if orphan_ids:
            ph = ",".join("?" * len(orphan_ids))
            with self._connect() as conn:
                conn.execute(
                    f"DELETE FROM review_schedule WHERE id IN ({ph})", orphan_ids)
        return {
            "due": out,
            "total": len(out),
            "master_stage": REVIEW_MASTER_STAGE,
            "intervals_days": REVIEW_INTERVALS_DAYS,
        }

    def review_stats(self, user_id: str) -> dict:
        """#16 复习概览：待巩固数（到期）/ 队列总数 / 已掌握数 / 下次到期最早时间。
        已掌握 = 曾经答错（practice_logs 有 score=0 记录）但已移出排程队列的题数。"""
        now = _now_iso()
        with self._connect() as conn:
            due = conn.execute(
                "SELECT COUNT(*) n FROM review_schedule WHERE user_id=? AND next_review_at<=?",
                (user_id, now),
            ).fetchone()["n"]
            in_queue = conn.execute(
                "SELECT COUNT(*) n FROM review_schedule WHERE user_id=?", (user_id,),
            ).fetchone()["n"]
            nxt = conn.execute(
                "SELECT MIN(next_review_at) t FROM review_schedule WHERE user_id=? AND next_review_at>?",
                (user_id, now),
            ).fetchone()["t"]
            ever_wrong = conn.execute(
                "SELECT COUNT(DISTINCT question_id) n FROM practice_logs "
                "WHERE user_id=? AND question_id IS NOT NULL AND score=0",
                (user_id,),
            ).fetchone()["n"]
        return {
            "due": due,
            "in_queue": in_queue,
            "mastered": max(0, ever_wrong - in_queue),
            "next_review_at": nxt,
        }

    def mistake_knowledge_groups(self, user_id: str,
                                 teacher_id: str | None = None) -> list[dict]:
        """#16 按知识点归组错题：mistakes.question_id → question_bank.knowledge_point/category。
        有知识点用知识点，否则用课程分类，末位兜底「未归类」。"""
        with self._connect() as conn:
            sql = (
                "SELECT m.question_id, q.knowledge_point, q.category, COUNT(*) AS n "
                "FROM mistakes m LEFT JOIN question_bank q ON m.question_id=q.id "
                "WHERE m.user_id=? "
            )
            args: list = [user_id]
            if teacher_id:
                sql += "AND m.teacher_id=? "
                args.append(teacher_id)
            sql += "GROUP BY m.question_id, q.knowledge_point, q.category ORDER BY n DESC"
            rows = conn.execute(sql, args).fetchall()
        groups: dict[str, dict] = {}
        for r in rows:
            kp = ((r["knowledge_point"] or "").strip()) or ((r["category"] or "").strip()) or "未归类"
            g = groups.setdefault(kp, {"knowledge_point": kp, "mistake_count": 0, "question_ids": []})
            g["mistake_count"] += r["n"]
            if r["question_id"] is not None:
                g["question_ids"].append(r["question_id"])
        result = sorted(groups.values(), key=lambda x: x["mistake_count"], reverse=True)
        return result

    # ==================== 练习统计 / CSV 导出（C1/C2 拓展） ====================
    def practice_stats(self, user_id: str) -> dict:
        """我的练习统计：总量/客观正确率/按老师分布/最近 7 天趋势。
        score>0 视为正确；score=-1 待评分不计入正确率。"""
        with self._connect() as conn:
            graded = conn.execute(
                "SELECT COUNT(*) c, SUM(CASE WHEN score>0 THEN 1 ELSE 0 END) ok "
                "FROM practice_logs WHERE user_id=? AND score>=0", (user_id,)
            ).fetchone()
            total = conn.execute(
                "SELECT COUNT(*) c FROM practice_logs WHERE user_id=?", (user_id,)
            ).fetchone()
            by_teacher = conn.execute(
                "SELECT teacher_id, COUNT(*) n, "
                "SUM(CASE WHEN score>0 THEN 1 ELSE 0 END) ok, "
                "SUM(CASE WHEN score<0 THEN 1 ELSE 0 END) pending "
                "FROM practice_logs WHERE user_id=? GROUP BY teacher_id ORDER BY n DESC",
                (user_id,),
            ).fetchall()
            recent = conn.execute(
                "SELECT substr(created_at,1,10) d, COUNT(*) n "
                "FROM practice_logs WHERE user_id=? GROUP BY d ORDER BY d DESC LIMIT 7",
                (user_id,),
            ).fetchall()
        graded_n = graded["c"] or 0
        return {
            "total": total["c"] or 0,
            "graded": graded_n,
            "correct": graded["ok"] or 0,
            "accuracy": (graded["ok"] / graded_n) if graded_n else None,
            "by_teacher": [dict(r) for r in by_teacher],
            "recent_days": sorted([dict(r) for r in recent], key=lambda x: x["d"]),
        }

    # ==================== #36 分类专项练习 / 学情报告 / AI 批改 ====================
    def get_question_by_id(self, teacher_id: str, question_id: int) -> dict | None:
        """按 id+老师（含共享题库）取题库题（#36 AI 批改取参考答案用；与既有 get_question(qid) 区分）。"""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM question_bank WHERE id=? AND {_teacher_scope_sql()} AND enabled=1",
                (question_id, teacher_id),
            ).fetchone()
        return self._parse_qrows([row])[0] if row else None

    def practice_categories(self, teacher_id: str) -> list[dict]:
        """老师题库（含共享题库）的分类清单（含各分类题量），供练习面板下拉。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT category, COUNT(*) n FROM question_bank "
                f"WHERE {_teacher_scope_sql()} AND enabled=1 "
                "GROUP BY category ORDER BY n DESC",
                (teacher_id,),
            ).fetchall()
        return [{"category": (r["category"] or "综合"), "count": r["n"]} for r in rows]
