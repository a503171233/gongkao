# -*- coding: utf-8 -*-
"""F1 模考解析：整卷上传 → 识别拆题 → 按题型路由老师逐题作答并解析。
独立 mockexam.db，后台线程执行长任务（Init→Poll），前端展示进度。
"""
import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .config import Config

ANSWER_WORKERS = 3
STALE_SECONDS = 1800

_ROUTE_KEYWORDS = {
    "言语理解": ("言语", "星辰", "逻辑填空"),
    "判断推理": ("判断", "逻辑", "云舟", "图形", "类比"),
    "数量关系": ("数量", "数学", "运算"),
    "资料分析": ("资料"),
    "常识判断": ("常识", "公共"),
    "申论": ("申论", "谭"),
    "面试": ("面试"),
    "综合": (),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_category(raw) -> str:
    from .study import normalize_category as _nc
    return _nc(raw)


def _infer_exam_type(questions: list[dict]) -> str:
    total = len(questions)
    if total <= 0:
        return ""
    shen = sum(1 for q in questions if q.get("category") == "申论")
    if shen * 2 >= total:
        return "申论"
    return "行测"


def _route_teacher(category: str) -> tuple[str, str]:
    """按题型归属老师：在启用老师中按关键词匹配最贴合学科的老师；无专属老师回退首个启用老师。"""
    cfg = Config()
    enabled = [t for t in cfg.list_teachers_all() if t.get("enabled")]
    if not enabled:
        enabled = cfg.list_teachers_all()
    default = sorted(enabled, key=lambda t: (t.get("sort_order", 0), t.get("teacher_id")))[0]
    cat = normalize_category(category)
    keys = _ROUTE_KEYWORDS.get(cat, ())
    best: dict | None = None
    best_len = 0
    for t in enabled:
        blob = " ".join([t.get("teacher_subject", ""), t.get("course_category", ""), t.get("teacher_name", "")])
        for kw in keys:
            if kw and kw in blob and len(kw) > best_len:
                best = t
                best_len = len(kw)
    t = best or default
    return t["teacher_id"], t.get("teacher_name") or t["teacher_id"]


def _llm_call_eff(messages, tcfg, max_tokens: int = 2000, temperature: float = 0.2) -> str:
    """注册表默认模型优先，通道失败回退全局主模型（仿 admin._llm_call / api._grade_llm_call）。"""
    from . import llm as llm_biz
    eff = None
    try:
        from .models import get_ai_model_store
        eff = get_ai_model_store().effective_default()
    except Exception:
        eff = None
    if eff:
        try:
            return llm_biz.call_llm(
                messages, tcfg, max_tokens=max_tokens,
                model=eff.get("model_id"), base_url=eff.get("base_url"),
                api_key=eff.get("api_key"), temperature=temperature)
        except ValueError:
            raise
        except Exception:
            pass
    try:
        return llm_biz.call_llm(messages, tcfg, max_tokens=max_tokens, temperature=temperature)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"AI 服务暂不可用（{str(e)[:80]}），请稍后重试")


def _parse_answer_obj(raw: str) -> dict:
    t = (raw or "").strip()
    if t.startswith("```"):
        t = t.replace("```json", "", 1).replace("```JSON", "", 1)
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    dec = json.JSONDecoder()
    found = None
    for i, ch in enumerate(t):
        if ch != "{":
            continue
        try:
            obj, _end = dec.raw_decode(t[i:])
        except ValueError:
            continue
        if isinstance(obj, dict):
            if "answer" in obj or "analysis" in obj:
                return obj
            if found is None:
                found = obj
    if found is not None:
        return found
    raise ValueError("模型返回格式异常（期望含 answer/analysis 的 JSON）")


def _answer_one(q: dict) -> dict:
    teacher_id = q.get("teacher_id") or ""
    cfg = Config()
    tcfg = cfg.get_teacher(teacher_id)
    qtype = q.get("qtype") or "essay"
    type_name = {"choice": "选择题", "judge": "判断题", "essay": "主观题"}.get(qtype, "题目")
    opts = q.get("options") or []
    opts_block = ""
    if opts:
        opts_block = "选项：\n" + "\n".join(opts) + "\n"
    subject = tcfg.teacher_subject or ""
    persona = (
        f"你是{tcfg.teacher_name}（公考{subject}讲师）。请以资深公考教师身份独立作答并解析下面这道题，"
        "不要默认相信原卷可能存在的参考答案，以你的专业判断为准。"
    )
    rules = (
        "- answer 字段：选择题给选项字母（如 A）；判断题给\"对\"或\"错\"；主观题给参考作答（要点完整、层次清晰）。\n"
        "- analysis 字段：选择题/判断题给结构化解析（解题思路、选项正误辨析、易错点）；"
        "主观题给命题意图、作答方法、评分要点或成文思路。\n"
        "- 题干中 ![图N](/api/qimg/...) 为题目配图标记，图片对你不可见，请按该题型通用方法分析，禁止编造图片内容。\n"
        "输出严格 JSON：{\"answer\": \"...\", \"analysis\": \"...\"}，不要输出 JSON 以外的任何文字。"
    )
    user_prompt = (
        f"题目类型：{type_name}\n"
        f"考察方向：{q.get('category') or ''} / {q.get('knowledge_point') or ''}\n"
        f"题干：\n{q.get('question') or ''}\n"
        + opts_block +
        rules
    )
    sys_prompt = persona + "\n" + (
        "输出严格 JSON：{\"answer\": \"...\", \"analysis\": \"...\"}，不要输出 JSON 以外的任何文字。"
    )
    last_err = ""
    for _attempt in range(2):
        msgs = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if _attempt > 0 and last_err:
            msgs.append({"role": "user", "content": f"你上一次输出无法解析（{last_err}），请重新输出严格 JSON。"})
        raw = _llm_call_eff(msgs, tcfg, max_tokens=2200, temperature=0.2)
        try:
            obj = _parse_answer_obj(raw)
            answer = str(obj.get("answer") or "").strip()
            analysis = str(obj.get("analysis") or "").strip()
            if not answer and not analysis:
                raise ValueError("answer/analysis 均为空")
            return {"answer": answer, "analysis": analysis}
        except ValueError as e:
            last_err = str(e)
    raise ValueError(last_err or "模型输出解析失败")


class MockExamStore:
    def __init__(self, db_path: str | Path | None = None):
        default = Config.data_dir / "mockexam.db"
        self.db_path = Path(db_path or os.environ.get("MOCKEXAM_DB") or default)
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
            CREATE TABLE IF NOT EXISTS mockexam_papers (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          TEXT NOT NULL,
                title            TEXT NOT NULL,
                exam_type        TEXT NOT NULL DEFAULT '',
                source_kind      TEXT NOT NULL DEFAULT '',
                doc_name         TEXT NOT NULL DEFAULT '',
                raw_text         TEXT NOT NULL DEFAULT '',
                status           TEXT NOT NULL DEFAULT 'pending',
                total_questions  INTEGER NOT NULL DEFAULT 0,
                done_questions   INTEGER NOT NULL DEFAULT 0,
                failed_questions INTEGER NOT NULL DEFAULT 0,
                message          TEXT NOT NULL DEFAULT '',
                summary          TEXT NOT NULL DEFAULT '',
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mockexam_user ON mockexam_papers(user_id, id);
            CREATE TABLE IF NOT EXISTS mockexam_questions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id     INTEGER NOT NULL,
                idx          INTEGER NOT NULL DEFAULT 0,
                qtype        TEXT NOT NULL DEFAULT '',
                category     TEXT NOT NULL DEFAULT '',
                question     TEXT NOT NULL,
                options      TEXT NOT NULL DEFAULT '',
                knowledge_point TEXT NOT NULL DEFAULT '',
                teacher_id   TEXT NOT NULL DEFAULT '',
                teacher_name TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL DEFAULT 'pending',
                answer       TEXT NOT NULL DEFAULT '',
                analysis     TEXT NOT NULL DEFAULT '',
                error        TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_mockexam_qpaper ON mockexam_questions(paper_id, idx);
            """)

    def _row_to_paper(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            d["summary"] = json.loads(d.get("summary") or "{}") if d.get("summary") else {}
        except (ValueError, TypeError):
            d["summary"] = {}
        return d

    def _row_to_question(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            d["options"] = json.loads(d.get("options") or "[]") if d.get("options") else []
        except (ValueError, TypeError):
            d["options"] = []
        return d

    def create_paper(self, user_id: str, title: str, exam_type: str, source_kind: str,
                     doc_name: str, raw_text: str) -> int:
        now = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO mockexam_papers(user_id, title, exam_type, source_kind, doc_name, "
                "raw_text, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (user_id, title, exam_type, source_kind, doc_name, raw_text, "pending", now, now))
            return int(cur.lastrowid)

    def get_paper(self, paper_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mockexam_papers WHERE id=?", (paper_id,)).fetchone()
        return self._row_to_paper(row) if row else None

    def get_paper_for_user(self, paper_id: int, user_id: str) -> dict | None:
        p = self.get_paper(paper_id)
        if p is None or p["user_id"] != user_id:
            return None
        return p

    def heal_paper(self, paper_id: int) -> dict | None:
        p = self.get_paper(paper_id)
        if p is None:
            return None
        self._heal_stale_batch([p])
        return p

    def list_papers(self, user_id: str, limit: int = 50, offset: int = 0) -> tuple[int, list[dict]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM mockexam_papers WHERE user_id=?", (user_id,)).fetchone()
            total = int(row["n"]) if row else 0
            rows = conn.execute(
                "SELECT * FROM mockexam_papers WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset)).fetchall()
        papers = [self._row_to_paper(r) for r in rows]
        self._heal_stale_batch(papers)
        return total, papers

    def update_paper(self, paper_id: int, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values())
        with self._connect() as conn:
            conn.execute(
                f"UPDATE mockexam_papers SET {cols}, updated_at=? WHERE id=?",
                (*vals, _now_iso(), paper_id))

    def bump_paper_count(self, paper_id: int, done: bool) -> None:
        col = "done_questions" if done else "failed_questions"
        with self._connect() as conn:
            conn.execute(
                f"UPDATE mockexam_papers SET {col}={col}+1, updated_at=? WHERE id=?",
                (_now_iso(), paper_id))

    def delete_paper(self, paper_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM mockexam_questions WHERE paper_id=?", (paper_id,))
            conn.execute("DELETE FROM mockexam_papers WHERE id=?", (paper_id,))

    def count_questions(self, paper_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM mockexam_questions WHERE paper_id=?",
                (paper_id,)).fetchone()
        return int(row["n"]) if row else 0

    def add_questions(self, paper_id: int, questions: list[dict]) -> int:
        now = _now_iso()
        with self._connect() as conn:
            for i, q in enumerate(questions, 1):
                qtype = q.get("qtype") if q.get("qtype") in ("choice", "judge", "essay") else "essay"
                cat = normalize_category(q.get("category"))
                teacher_id, teacher_name = _route_teacher(cat)
                conn.execute(
                    "INSERT INTO mockexam_questions(paper_id, idx, qtype, category, question, options, "
                    "knowledge_point, teacher_id, teacher_name, status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (paper_id, i, qtype, cat, q.get("question") or "", json.dumps(q.get("options") or [],
                     ensure_ascii=False), q.get("knowledge_point") or "", teacher_id, teacher_name,
                     "pending"))
        return len(questions)

    def list_pending_questions(self, paper_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mockexam_questions WHERE paper_id=? AND status='pending' "
                "ORDER BY idx ASC", (paper_id,)).fetchall()
        return [self._row_to_question(r) for r in rows]

    def list_questions(self, paper_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mockexam_questions WHERE paper_id=? ORDER BY idx ASC",
                (paper_id,)).fetchall()
        return [self._row_to_question(r) for r in rows]

    def set_question_ok(self, qid: int, paper_id: int, answer: str, analysis: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE mockexam_questions SET status='ok', answer=?, analysis=?, error='' WHERE id=?",
                (answer, analysis, qid))
        self.bump_paper_count(paper_id, done=True)

    def set_question_failed(self, qid: int, paper_id: int, err: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE mockexam_questions SET status='failed', error=? WHERE id=?",
                (err[:500], qid))
        self.bump_paper_count(paper_id, done=False)

    def reset_questions(self, paper_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE mockexam_questions SET status='pending', answer='', analysis='', "
                "error='' WHERE paper_id=?", (paper_id,))
            conn.execute(
                "UPDATE mockexam_papers SET done_questions=0, failed_questions=0 WHERE id=?",
                (paper_id,))

    def _heal_stale_batch(self, papers: list[dict]) -> None:
        from datetime import datetime as _dt
        for p in papers:
            if p["status"] in ("pending", "extracting", "answering"):
                if self.is_running(p["id"]):
                    continue
                try:
                    ts = _dt.strptime(p["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue
                age = (datetime.now(timezone.utc) - ts).total_seconds()
                if age > STALE_SECONDS:
                    self.update_paper(p["id"], status="failed",
                                      message="解析任务中断（服务重启或超时），请删除后重新上传")
                    p["status"] = "failed"
                    p["message"] = "解析任务中断（服务重启或超时），请删除后重新上传"

    def is_running(self, paper_id: int) -> bool:
        return paper_id in _active_papers


_running_lock = threading.Lock()
_active_papers: set[int] = set()

_store: MockExamStore | None = None
_store_lock = threading.Lock()


def get_mockexam_store() -> MockExamStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = MockExamStore()
    return _store


def start_paper_job(paper_id: int, reextract: bool = False) -> bool:
    with _running_lock:
        if paper_id in _active_papers:
            return False
        _active_papers.add(paper_id)
    t = threading.Thread(target=_run_paper_job, args=(paper_id, reextract), daemon=True)
    t.start()
    return True


def _finish_paper(store: MockExamStore, paper_id: int, status: str, message: str,
                  summary: dict | None = None) -> None:
    fields: dict = {"status": status, "message": message}
    if summary is not None:
        fields["summary"] = json.dumps(summary, ensure_ascii=False)
    store.update_paper(paper_id, **fields)


def _run_paper_job(paper_id: int, reextract: bool) -> None:
    store = get_mockexam_store()
    try:
        paper = store.get_paper(paper_id)
        if paper is None:
            return
        if reextract or store.count_questions(paper_id) == 0:
            if reextract:
                store.reset_questions(paper_id)
            store.update_paper(paper_id, status="extracting", message="正在识别并拆分题目…")
            text = paper.get("raw_text") or ""
            from . import admin as admin_biz
            try:
                result = admin_biz.extract_questions_ai(text)
            except ValueError as e:
                _finish_paper(store, paper_id, "failed", f"题目识别失败：{str(e)[:160]}")
                return
            questions = result.get("questions") or []
            if not questions:
                _finish_paper(store, paper_id, "failed",
                              "未从试卷内容中识别出题目，请确认上传的是行测/申论试卷")
                return
            n = store.add_questions(paper_id, questions)
            exam_type = paper.get("exam_type") or _infer_exam_type(questions)
            store.update_paper(paper_id, total_questions=n, exam_type=exam_type,
                               status="answering", message="正在按题型分配老师作答解析…")
        else:
            if paper.get("status") not in ("ready", "failed", "answering", "extracting", "pending"):
                store.update_paper(paper_id, status="answering",
                                   message="正在重试作答解析…")
            else:
                store.update_paper(paper_id, status="answering", message="正在作答解析…")

        todo = store.list_pending_questions(paper_id)
        if not todo:
            _finish_paper(store, paper_id, "ready", "全部题目已完成作答解析",
                          _build_summary(store, paper_id))
            return
        with ThreadPoolExecutor(max_workers=ANSWER_WORKERS) as pool:
            futures = [pool.submit(_answer_one, q) for q in todo]
            for fut, q in zip(futures, todo):
                try:
                    r = fut.result()
                    store.set_question_ok(q["id"], paper_id, r.get("answer", ""), r.get("analysis", ""))
                except Exception as e:
                    store.set_question_failed(q["id"], paper_id, str(e))
        _finish_paper(store, paper_id, "ready", "解析完成", _build_summary(store, paper_id))
    except Exception as e:
        try:
            _finish_paper(store, paper_id, "failed", f"解析失败：{str(e)[:160]}")
        except Exception:
            pass
    finally:
        with _running_lock:
            _active_papers.discard(paper_id)


def _build_summary(store: MockExamStore, paper_id: int) -> dict:
    qs = store.list_questions(paper_id)
    cats: dict[str, int] = {}
    teachers: dict[str, dict] = {}
    ok = 0
    fail = 0
    for q in qs:
        c = q.get("category") or "未分类"
        cats[c] = cats.get(c, 0) + 1
        tname = q.get("teacher_name") or (q.get("teacher_id") or "默认老师")
        rec = teachers.setdefault(tname, {"total": 0, "ok": 0, "failed": 0})
        rec["total"] += 1
        if q.get("status") == "ok":
            ok += 1
            rec["ok"] += 1
        elif q.get("status") == "failed":
            fail += 1
            rec["failed"] += 1
    return {"total": len(qs), "ok": ok, "failed": fail,
            "categories": cats, "teachers": teachers}
