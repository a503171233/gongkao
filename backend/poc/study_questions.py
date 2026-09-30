# -*- coding: utf-8 -*-
"""C4 · 学员增值功能 — StudyStore：题库 CRUD / 分类 / 归一化。"""
import json
import random
import re

from . import study as _study
from .study_common import (
    QUESTION_CATEGORIES, _extract_imgs_from_question, _imgs_json,
    _norm_question, _norm_source, _parse_images, _teacher_scope_sql,
    normalize_category, normalize_source_type,
)


def _now_iso():
    return _study._now_iso()


class StudyQuestionsMixin:
    """题库管理与归一化 方法集合。"""

    # ==================== 练习 ====================
    # #13 真实题库：优先题库抽题，题库为空回落知识库知识点兜底
    def _qbank_pool(self, teacher_id: str, qtype: str | None = None,
                    category: str | None = None) -> list[dict]:
        """题库可用题目（enabled=1，按老师/共享题库/题型/分类过滤）。category 为 #36 分类专项练习。"""
        sql = f"SELECT * FROM question_bank WHERE {_teacher_scope_sql()} AND enabled=1"
        args: list = [teacher_id]
        if qtype:
            sql += " AND qtype=?"
            args.append(qtype)
        if category:
            sql += " AND category=?"
            args.append(category)
        sql += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return self._parse_qrows(rows)

    def add_question(self, teacher_id: str, qtype: str, question: str,
                     answer: str, options: list[str] | None = None,
                     analysis: str = "", difficulty: int = 1,
                     knowledge_point: str = "", category_id: str = "",
                     images: list[str] | None = None,
                     category: str = "",
                     source_type: str = "", source: str = "",
                     source_url: str = "",
                     sensitive_check: bool = False) -> dict:
        """新增题库题目（管理端/CLI 用）。category_id 关联题型树节点（#24 R2）；
        images 为题干配图 URL 列表（#33 R2，白名单外 URL 自动剔除）；
        category 为课程大类（#35 R1，8 枚举，非法值兜底"综合"）；
        source_type/source 为题目来源（枚举类型 + 可选出处）；
        sensitive_check=True 时启用 #10 入库敏感词守卫：题干/答案/解析任一
        命中敏感词即抛 ValueError（调用方决定拦截或标记，避免脏题入库）。"""
        if qtype not in ("choice", "judge", "essay"):
            raise ValueError("qtype 必须是 choice/judge/essay")
        if not question or not answer:
            raise ValueError("题目和答案必填")
        if sensitive_check:
            from .sensitive import check_sensitive
            for field in (question, answer, analysis or ""):
                hit = check_sensitive(field)
                if hit:
                    raise ValueError(f"题目内容包含敏感词: {hit}")
        n_cat = normalize_category(category) or "综合"
        n_source_type = normalize_source_type(source_type)
        now = _now_iso()
        opts_json = ""
        if options:
            import json
            opts_json = json.dumps(options, ensure_ascii=False)
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO question_bank
                   (teacher_id, qtype, question, options, answer, analysis, difficulty,
                    knowledge_point, category_id, images, enabled, created_at, category,
                    source_type, source, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                (teacher_id, qtype, question, opts_json, answer, analysis,
                 max(1, min(5, int(difficulty))), knowledge_point or "",
                 (category_id or "").strip(), _imgs_json(images), now, n_cat,
                 n_source_type, (source or "").strip(), (source_url or "").strip()),
            )
            qid = cur.lastrowid
        return {"id": qid, "teacher_id": teacher_id, "qtype": qtype,
                "question": question, "answer": answer,
                "knowledge_point": knowledge_point or "",
                "category_id": (category_id or "").strip(),
                "category": n_cat,
                "source_type": n_source_type, "source": (source or "").strip(),
                "images": _parse_images(_imgs_json(images))}

    def add_questions_batch(self, teacher_id: str, items: list[dict],
                            sensitive_check: bool = False) -> dict:
        """批量入库题目（AI 采集用）。items 每项 = {qtype, question, options, answer,
        analysis, difficulty, knowledge_point, images}。
        返回 {added, skipped, invalid, filtered}（question 全文查重去重；#33 R2 数据校验：
        choice 选项 <2 或答案为空的题拒收计 invalid，不入库不计数 added；
        #10 sensitive_check=True 时命中敏感词的题计 filtered，不入库不计数 added）。"""
        added = 0
        skipped = 0
        invalid = 0
        filtered = 0
        dupe_source = 0
        # 归一化查重预载：把 #38 原本仅 essay 的归一化查重推广到所有题型，消除跨站
        # 全半角/标点/空白差异导致的近重复入库。norms_all=题干归一化签名 -> 已采来源集合；
        # norms_choice 为选择题"题干\x1f按序选项"签名 -> 已采来源集合（扛选项顺序/写法差异）。
        # 记录来源，便于命中重复时判断是否同源（同源则跳过整张试卷）。
        norms_all, norms_choice = {}, {}
        if any((it or {}).get("qtype") in ("choice", "judge", "essay") for it in items or []):
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT qtype, question, options, source FROM question_bank "
                    "WHERE qtype IN ('choice','judge','essay')"
                ).fetchall()
            import json as _json
            for _r in rows:
                _qn = _norm_question(_r["question"]) if _r["question"] else ""
                _sn = _norm_source(_r["source"])
                if _qn:
                    norms_all.setdefault(_qn, set()).add(_sn)
                if _r["qtype"] == "choice" and _r["options"]:
                    try:
                        _opts = _json.loads(_r["options"])
                    except Exception:
                        _opts = []
                    if isinstance(_opts, list):
                        _cs = _qn + "\x1f" + "\x1e".join(
                            sorted(_norm_question(str(_o)) for _o in _opts if str(_o).strip()))
                        norms_choice.setdefault(_cs, set()).add(_sn)
        for it in items or []:
            qtype = (it.get("qtype") or "essay").strip()
            question = (it.get("question") or "").strip()
            answer = (it.get("answer") or "").strip()
            opts = it.get("options") or []
            if not question or not answer:
                invalid += 1
                continue
            # #33 R2 校验：选择题必须有 ≥2 个选项（防 LLM 丢选项的残缺题入库）
            if qtype == "choice" and (not isinstance(opts, (list, tuple)) or len(opts) < 2):
                invalid += 1
                continue
            # 按题目文本全局去重（共享题库所有老师可见，杜绝重复题入库）
            # 归一化查重承担跨站近重复：题干归一化命中（任何题型）判重复；选择题另比较
            # 按序选项集合，选项顺序/写法不同但题干+选项本质相同仍判重复。
            # 归一化查重 + 同源整卷跳过：命中重复时再判断来源——
            # 若已入库题来源与本批来源一致（同一张试卷被重复采集），整批跳过（dupe_source），
            # 而非逐题跳过；来源不同不跳过整卷（跨站同题可能只是撞题）。
            dupe = False
            whole = False
            cur_src = _norm_source(it.get("source"))
            nn = _norm_question(question)
            if nn and nn in norms_all:
                dupe = True
                if cur_src and cur_src in norms_all[nn]:
                    whole = True
            csig = ""
            if not dupe and qtype == "choice" and isinstance(opts, (list, tuple)):
                csig = nn + "\x1f" + "\x1e".join(
                    sorted(_norm_question(str(o)) for o in opts if str(o).strip()))
                if csig in norms_choice:
                    dupe = True
                    if cur_src and cur_src in norms_choice[csig]:
                        whole = True
            if not dupe:
                with self._connect() as conn:
                    dup = conn.execute(
                        "SELECT id, source FROM question_bank WHERE question=?",
                        (question,),
                    ).fetchone()
                if dup:
                    dupe = True
                    if cur_src and _norm_source(dup["source"]) == cur_src:
                        whole = True
            if whole:
                dupe_source += 1
                break
            if not dupe:
                # 本轮批内新增回填（防批内后续重复，且记录来源用于同源判定）
                if nn:
                    norms_all.setdefault(nn, set()).add(cur_src)
                if qtype == "choice" and isinstance(opts, (list, tuple)) and csig:
                    norms_choice.setdefault(csig, set()).add(cur_src)
            else:
                skipped += 1
                continue
            try:
                self.add_question(
                    teacher_id=teacher_id,
                    qtype=qtype if qtype in ("choice", "judge", "essay") else "essay",
                    question=question,
                    answer=answer,
                    options=list(opts) if isinstance(opts, (list, tuple)) else [],
                    analysis=(it.get("analysis") or "").strip(),
                    difficulty=int(it.get("difficulty") or 1),
                    knowledge_point=(it.get("knowledge_point") or "").strip(),
                    category_id=(it.get("category_id") or "").strip(),
                    # #33 R2：images 缺失时从题干回提（直接调 import 不经 _normalize 的兜底）
                    images=it.get("images") or _extract_imgs_from_question(question),
                    # #35 R1：课程分类透传（缺失/非法兜底"综合"）
                    category=(it.get("category") or "").strip(),
                    # 来源：默认 AI采集，可透传公开题库/文档库/互联网搜索等
                    source_type=(it.get("source_type") or "AI采集").strip(),
                    source=(it.get("source") or "").strip(),
                    # 溯源：采集来源页 URL（套卷链接），支持后续按页回查/修复
                    source_url=(it.get("source_url") or "").strip(),
                    sensitive_check=sensitive_check,
                )
            except ValueError:
                # #10 敏感词命中（或其它校验失败）：整题拒绝，不计入 added
                filtered += 1
                continue
            added += 1
        return {"added": added, "skipped": skipped, "invalid": invalid,
                "filtered": filtered, "dupe_source": dupe_source}

    def question_count_by_teacher(self) -> dict[str, int]:
        """#26 R3 每老师题目数汇总（老师管理速览列用）。返回 {teacher_id: count}。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT teacher_id, COUNT(*) AS n FROM question_bank GROUP BY teacher_id"
            ).fetchall()
        return {r["teacher_id"]: r["n"] for r in rows}

    def list_questions(self, teacher_id: str, qtype: str | None = None,
                       limit: int = 100, offset: int = 0,
                       difficulty: int | None = None,
                       knowledge_point: str = "",
                       keyword: str = "",
                       category_ids: list[str] | None = None,
                       category: str = "",
                       source_type: str = "") -> list[dict]:
        """题库列表（管理端）。支持题型/难度/知识点/关键词/题型分类(含子树)/课程分类/来源筛选 + 分页。
        category（#35 R1）：''=不过滤；'__uncat__'=未分类；其他=精确匹配枚举值。
        source_type：题目来源枚举筛选（''=不过滤）。共享题库（SHARED）对所有老师可见。"""
        sql = f"SELECT * FROM question_bank WHERE {_teacher_scope_sql()}"
        args: list = [teacher_id]
        if qtype:
            sql += " AND qtype=?"
            args.append(qtype)
        if difficulty:
            sql += " AND difficulty=?"
            args.append(int(difficulty))
        if knowledge_point:
            sql += " AND knowledge_point=?"
            args.append(knowledge_point)
        if keyword:
            sql += " AND (question LIKE ? OR analysis LIKE ?)"
            kw = f"%{keyword}%"
            args += [kw, kw]
        if category_ids is not None:
            # 分类筛选（含子树展开）：空列表 = 该分类下无题（直接短路）；[''] 匹配未分类
            if not category_ids:
                return []
            sql += f" AND category_id IN ({','.join('?' * len(category_ids))})"
            args.extend(category_ids)
        if category == "__uncat__":
            sql += " AND (category='' OR category IS NULL)"
        elif category:
            sql += " AND category=?"
            args.append(normalize_category(category) or category)
        if source_type:
            sql += " AND source_type=?"
            args.append(source_type)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        args += [int(limit), int(offset)]
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
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

    def delete_question(self, qid: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM question_bank WHERE id=?", (qid,))
        return cur.rowcount > 0

    def delete_questions_batch(self, qids: list[int]) -> dict:
        """#25 批量删除题目：返回 {deleted, missing}。"""
        ids = [int(i) for i in (qids or []) if int(i) > 0]
        if not ids:
            raise ValueError("qids 不能为空")
        missing = []
        deleted = 0
        for i in ids:
            if self.delete_question(i):
                deleted += 1
            else:
                missing.append(i)
        return {"deleted": deleted, "missing": missing}

    def get_question(self, qid: int) -> dict:
        """#26 R2 查询单题详情（含 options/images 反序列化）。"""
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM question_bank WHERE id=?", (qid,)).fetchone()
        if not r:
            raise ValueError(f"题目不存在: {qid}")
        d = dict(r)
        if d.get("options"):
            import json
            try:
                d["options"] = json.loads(d["options"])
            except (ValueError, TypeError):
                d["options"] = []
        else:
            d["options"] = []
        d["images"] = _parse_images(d.get("images"))  # #33 R2
        return d

    def update_question(self, qid: int, qtype: str | None = None,
                        question: str | None = None, answer: str | None = None,
                        options: list[str] | None = None, analysis: str | None = None,
                        difficulty: int | None = None, knowledge_point: str | None = None,
                        category_id: str | None = None,
                        category: str | None = None,
                        source_type: str | None = None,
                        source: str | None = None) -> dict:
        """#26 R2 编辑题目：仅更新传入字段（None = 保持不变）。返回更新后字典。
        #35 R1：category 支持 ''（清除为未分类）/合法枚举（非法值报错）；
        source_type/source：题目来源（枚举类型 + 可选出处）。"""
        cur = self.get_question(qid)  # 存在性 + 当前值
        n_qtype = cur["qtype"] if qtype is None else qtype
        if n_qtype not in ("choice", "judge", "essay"):
            raise ValueError("qtype 必须是 choice/judge/essay")
        n_question = cur["question"] if question is None else question
        n_answer = cur["answer"] if answer is None else answer
        if not (n_question or "").strip() or not (n_answer or "").strip():
            raise ValueError("题目和答案必填")
        n_opts = (cur["options"] if options is None else (options or [])) or []
        import json
        opts_json = json.dumps(n_opts, ensure_ascii=False) if n_opts else ""
        n_difficulty = max(1, min(5, int(cur["difficulty"] if difficulty is None else difficulty)))
        n_kp = (cur["knowledge_point"] if knowledge_point is None else (knowledge_point or "")).strip()
        n_cat = ("" if category_id is None else (category_id or "").strip()) if category_id is not None \
            else (cur["category_id"] or "")
        n_category = cur.get("category") or ""
        if category is not None:
            n_category = category.strip()
            if n_category and normalize_category(n_category) != n_category:
                raise ValueError(f"category 必须是 {QUESTION_CATEGORIES} 之一或空")
        n_source_type = normalize_source_type(cur.get("source_type") if source_type is None else source_type)
        n_source = (cur.get("source") if source is None else (source or "")).strip()
        n_analysis = cur["analysis"] if analysis is None else (analysis or "")
        # #33 R2：题干变更时从新题干重提图片 URL（编辑可能增删图片标记）
        if question is None:
            n_imgs_json = _imgs_json(cur.get("images") or [])
        else:
            from .ingest import extract_img_urls
            n_imgs_json = _imgs_json(extract_img_urls(n_question))
        with self._connect() as conn:
            conn.execute(
                """UPDATE question_bank SET qtype=?, question=?, options=?, answer=?,
                   analysis=?, difficulty=?, knowledge_point=?, category_id=?, images=?,
                   category=?, source_type=?, source=? WHERE id=?""",
                (n_qtype, n_question, opts_json, n_answer, n_analysis,
                 n_difficulty, n_kp, n_cat, n_imgs_json, n_category,
                 n_source_type, n_source, qid))
        return self.get_question(qid)

    def copy_question(self, qid: int) -> dict:
        """#26 R2 复制题目：按原题内容新增一条（保持题干文本以延续查重口径），返回新题。"""
        cur = self.get_question(qid)
        return self.add_question(
            teacher_id=cur["teacher_id"], qtype=cur["qtype"], question=cur["question"],
            answer=cur["answer"], options=cur["options"] or [], analysis=cur["analysis"] or "",
            difficulty=cur["difficulty"] or 1, knowledge_point=cur["knowledge_point"] or "",
            category_id=cur["category_id"] or "", images=cur.get("images") or [],
            category=cur.get("category") or "",
            source_type=cur.get("source_type") or "用户提供", source=cur.get("source") or "")

    def find_duplicates(self, teacher_id: str, question: str, limit: int = 10) -> list[dict]:
        """#26 R2 题干查重：同老师下（含共享题库），题干精确相等 或 规范化(去空白)后相等 视为重复。
        不做子串匹配（照坑#15：短 token 子串必然假命中），避免误报。返回 [{id,qtype,question,answer,...}]。"""
        q = (question or "").strip()
        if not q:
            return []
        norm = re.sub(r"\s+", "", q)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, qtype, question, answer, knowledge_point, category_id "
                f"FROM question_bank WHERE {_teacher_scope_sql()}",
                (teacher_id,)).fetchall()
        out = []
        for r in rows:
            if r["question"] == q or (r["question"] and re.sub(r"\s+", "", r["question"]) == norm):
                out.append(dict(r))
                if len(out) >= limit:
                    break
        return out

    def find_questions_by_source_url(self, source_url: str,
                                     limit: int = 300) -> list[dict]:
        """溯源回查：按采集来源页 URL 查该套题已全部入库题目。
        用于「已采套卷直接调本网站查找原因/修复」。返回含题目详情字段。"""
        u = (source_url or "").strip()
        if not u:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM question_bank WHERE source_url=? OR source=? "
                "ORDER BY id LIMIT ?", (u, u, int(limit))).fetchall()
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
            d["images"] = _parse_images(d.get("images"))
            out.append(d)
        return out

    def question_stats(self, teacher_id: str | None = None) -> dict:
        """题库维度统计：按老师(含共享题库)/题型/难度/知识点/课程分类分组计数（管理端题库页顶部用）。"""
        with self._connect() as conn:
            where = f"WHERE {_teacher_scope_sql()}" if teacher_id else ""
            args = (teacher_id,) if teacher_id else ()
            by_type = conn.execute(
                f"SELECT qtype, COUNT(*) n FROM question_bank {where} GROUP BY qtype", args
            ).fetchall()
            by_diff = conn.execute(
                f"SELECT difficulty, COUNT(*) n FROM question_bank {where} GROUP BY difficulty", args
            ).fetchall()
            kp_where = " AND knowledge_point != ''" if teacher_id else " WHERE knowledge_point != ''"
            by_kp = conn.execute(
                f"SELECT knowledge_point, COUNT(*) n FROM question_bank {where}{kp_where} "
                "GROUP BY knowledge_point ORDER BY n DESC LIMIT 30", args
            ).fetchall()
            by_cat = conn.execute(
                f"SELECT category, COUNT(*) n FROM question_bank {where} "
                "GROUP BY category ORDER BY n DESC", args
            ).fetchall()
            total = conn.execute(
                f"SELECT COUNT(*) n FROM question_bank {where}", args
            ).fetchone()["n"]
        return {
            "total": total,
            "by_type": [{"qtype": r["qtype"], "n": r["n"]} for r in by_type],
            "by_difficulty": [{"difficulty": r["difficulty"], "n": r["n"]} for r in by_diff],
            "by_knowledge_point": [{"kp": r["knowledge_point"], "n": r["n"]} for r in by_kp],
            "by_category": [{"category": r["category"] or "", "n": r["n"]} for r in by_cat],
        }

    def list_uncategorized_questions(self, teacher_id: str, limit: int = 50,
                                     qids: list[int] | None = None) -> list[dict]:
        """#35 R1 取待补分类的题目（含共享题库）：qids 指定（优先）；否则取 category 为空的题。"""
        with self._connect() as conn:
            if qids:
                if not qids:
                    return []
                ph = ",".join("?" * len(qids))
                rows = conn.execute(
                    f"SELECT * FROM question_bank WHERE {_teacher_scope_sql()} "
                    f"AND id IN ({ph}) "
                    "ORDER BY id", [teacher_id, *qids]).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM question_bank WHERE {_teacher_scope_sql()} "
                    "AND (category='' OR category IS NULL) ORDER BY id LIMIT ?",
                    (teacher_id, int(limit))).fetchall()
        out = [dict(r) for r in rows]
        return out

    def sample_questions(self, teacher_id: str, category: str = "",
                         qtype: str = "", difficulty: int | None = None,
                         knowledge_point: str = "", count: int = 5,
                         exclude_qids: list[int] | None = None) -> list[dict]:
        """#17 智能组卷：按条件随机抽题（含共享题库）。返回 question_bank 全文
        （含 answer/analysis，用于组卷快照；前端下发时剥掉答案）。"""
        import random
        sql = f"SELECT * FROM question_bank WHERE {_teacher_scope_sql()} AND enabled=1"
        args: list = [teacher_id]
        if category:
            sql += " AND category=?"
            args.append(normalize_category(category) or category)
        if qtype:
            sql += " AND qtype=?"
            args.append(qtype)
        if difficulty:
            sql += " AND difficulty=?"
            args.append(int(difficulty))
        if knowledge_point:
            sql += " AND knowledge_point=?"
            args.append(knowledge_point)
        if exclude_qids:
            ph = ",".join("?" * len(exclude_qids))
            sql += f" AND id NOT IN ({ph})"
            args.extend([int(x) for x in exclude_qids])
        sql += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        pool = self._parse_qrows(rows)
        if not pool:
            return []
        n = max(0, int(count))
        # 题库不足时全量返回；已抽题去重（exclude 已过滤，这里再打乱抽样）
        random.shuffle(pool)
        return pool[:n] if n > 0 else []
