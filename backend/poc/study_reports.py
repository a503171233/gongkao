# -*- coding: utf-8 -*-
"""C4 · 学员增值功能 — StudyStore：学情报告 / CSV 导出 / 兜底题库。"""
import csv
import io
import json


class StudyReportsMixin:
    """学情报告与导出 方法集合。"""

    def practice_report(self, user_id: str, teacher_id: str | None = None) -> dict:
        """#36 学情报告：总览 / 分类正确率 / 薄弱点 / 近 7 天趋势 / 主观题批改概况。
        口径与 practice_stats 一致：score>0 判对，score=-1 待评分不计入正确率。"""
        where = "user_id=?"
        args: list = [user_id]
        if teacher_id:
            where += " AND teacher_id=?"
            args.append(teacher_id)
        with self._connect() as conn:
            graded = conn.execute(
                f"SELECT COUNT(*) c, SUM(CASE WHEN score>0 THEN 1 ELSE 0 END) ok "
                f"FROM practice_logs WHERE {where} AND score>=0", args,
            ).fetchone()
            cat_rows = conn.execute(
                f"SELECT category, COUNT(*) n, SUM(CASE WHEN score>0 THEN 1 ELSE 0 END) ok "
                f"FROM practice_logs WHERE {where} AND score>=0 GROUP BY category ORDER BY n DESC",
                args,
            ).fetchall()
            trend = conn.execute(
                f"SELECT substr(created_at,1,10) d, COUNT(*) n, "
                f"SUM(CASE WHEN score>0 THEN 1 ELSE 0 END) ok "
                f"FROM practice_logs WHERE {where} AND score>=0 "
                f"GROUP BY d ORDER BY d DESC LIMIT 7", args,
            ).fetchall()
            essay = conn.execute(
                f"SELECT SUM(CASE WHEN score<0 AND (grade_report='' OR grade_report IS NULL) "
                f"THEN 1 ELSE 0 END) pending, "
                f"AVG(CASE WHEN grade_report!='' THEN score END) avg_score "
                f"FROM practice_logs WHERE {where}", args,
            ).fetchone()
        graded_n = graded["c"] or 0
        by_category = [{
            "category": (r["category"] or "未分类"),
            "total": r["n"],
            "correct": r["ok"] or 0,
            "accuracy": round((r["ok"] or 0) / r["n"], 3) if r["n"] else 0,
        } for r in cat_rows]
        # 薄弱点：≥3 题且正确率 < 60% 的分类，按正确率升序
        weak = [x["category"] for x in by_category
                if x["total"] >= 3 and x["accuracy"] < 0.6]
        weak.sort(key=lambda c: next(x["accuracy"] for x in by_category if x["category"] == c))
        return {
            "graded": graded_n,
            "correct": graded["ok"] or 0,
            "accuracy": round((graded["ok"] or 0) / graded_n, 3) if graded_n else None,
            "by_category": by_category,
            "weak": weak,
            "recent_days": sorted([dict(r) for r in trend], key=lambda x: x["d"]),
            "essay": {"pending": essay["pending"] or 0,
                      "avg_score": round(essay["avg_score"], 3) if essay["avg_score"] is not None else None},
        }

    def learning_report(self, user_id: str) -> dict:
        """#20 学习报告（学情部分）：复用 practice_report 周报 + 复习概览 + 收藏/错题库存。"""
        report = self.practice_report(user_id)
        review = self.review_stats(user_id)
        with self._connect() as conn:
            fav = conn.execute(
                "SELECT COUNT(*) n FROM favorites WHERE user_id=?", (user_id,)
            ).fetchone()["n"]
            mist = conn.execute(
                "SELECT COUNT(*) n FROM mistakes WHERE user_id=?", (user_id,)
            ).fetchone()["n"]
        return {
            "weekly": report,
            "review": review,
            "inventory": {"favorites": fav or 0, "mistakes": mist or 0},
        }

    def get_practice_log(self, user_id: str, log_id: int) -> dict | None:
        """取单条练习记录（AI 批改入口，校验归属）。grade_report 解析为 dict。"""
        import json
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM practice_logs WHERE id=? AND user_id=?",
                (log_id, user_id),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("grade_report"):
            try:
                d["grade_report"] = json.loads(d["grade_report"])
            except (ValueError, TypeError):
                d["grade_report"] = None
        else:
            d["grade_report"] = None
        return d

    def save_grade(self, user_id: str, log_id: int, score: float,
                   feedback: str, report: dict) -> bool:
        """#36 写回 AI 批改结果：score（0-1）+ 总评 feedback + 完整报告 JSON。"""
        import json
        with self._connect() as conn:
            qid_row = conn.execute(
                "SELECT question_id FROM practice_logs WHERE id=? AND user_id=?",
                (log_id, user_id),
            ).fetchone()
            cur = conn.execute(
                "UPDATE practice_logs SET score=?, feedback=?, grade_report=? "
                "WHERE id=? AND user_id=?",
                (score, feedback, json.dumps(report, ensure_ascii=False), log_id, user_id),
            )
        # #16 主观题批改后推进艾宾浩斯节奏（≥0.7 视为掌握，否则重置待巩固）
        if qid_row and qid_row["question_id"] is not None:
            self._record_review_event(user_id, qid_row["question_id"],
                                      correct=(score is not None and score >= 0.7))
        return cur.rowcount > 0

    def export_favorites_csv(self, user_id: str) -> str:
        """收藏导出 CSV（UTF-8 BOM，Excel 直接打开不乱码）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT teacher_id, question, answer, created_at FROM favorites WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return self._to_csv(
            ["teacher_id", "question", "answer", "created_at"], rows)

    def export_mistakes_csv(self, user_id: str) -> str:
        """错题本导出 CSV（UTF-8 BOM）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT teacher_id, question, user_answer, correct_note, created_at "
                "FROM mistakes WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return self._to_csv(
            ["teacher_id", "question", "user_answer", "correct_note", "created_at"], rows)

    @staticmethod
    def _to_csv(headers: list[str], rows) -> str:
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(headers)
        for r in rows:
            w.writerow([r[k] for k in headers])
        return "\ufeff" + buf.getvalue()

    # ==================== 兜底题库 ====================
    def _get_fallback_questions(self, teacher_id: str) -> list[str]:
        """
        从知识库提取知识点作为练习题（题库为空时兜底）。
        用 Chroma 检索通用知识点短语，命中即作为练习题。
        """
        try:
            from . import store
            if not store._collection_exists(teacher_id):
                return self._default_questions()
            coll = store.get_collection(teacher_id)
            if coll is None:
                return self._default_questions()
            # 检索一些通用知识点
            queries = ["言语理解核心方法", "逻辑判断常见题型", "公考备考技巧"]
            questions = []
            for q in queries:
                results = coll.query(query_texts=[q], n_results=3)
                for doc in results.get("documents", [[]])[0]:
                    if doc and doc not in questions:
                        questions.append(doc[:200])  # 截断过长文本
            return questions[:5]
        except Exception:
            return self._default_questions()

    def _default_questions(self) -> list[str]:
        return [
            "公考言语理解中，如何快速把握文段主旨？",
            "逻辑判断里，削弱型题目的解题思路是什么？",
            "言语理解题中，成语辨析的常见考点有哪些？",
            "如何在有限时间内提高行测言语理解的正确率？",
            "逻辑判断加强型题目的常见解题技巧是什么？",
        ]
