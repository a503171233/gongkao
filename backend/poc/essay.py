# -*- coding: utf-8 -*-
"""批次18 · 申论 AI 批改模块。

职责：学员提交申论作答（题型+题目+作文）→ LLM 按阅卷维度评分+逐段批注+改进建议
→ 落库（essay.db，学员隔离）→ 历史/详情查询。

设计：
  - llm_call 依赖注入（同 fenbi_bind 模式）：api 层注入真实 LLM、测试注入假实现，
    规避 poc 模块间循环导入。
  - 评分维度按题型差异化：概括/对策侧重要点与条理，应用文侧重格式与场景，
    大作文侧重立意/结构/论证/语言。
  - 分数映射到题型满分（20/20/40/40），LLM 只出 0-100 相对分 → 按满分折算。
"""
import json
import re
import sqlite3
import time
from pathlib import Path

# 题型 → (名称, 满分, 评分侧重说明)
ESSAY_TYPES = {
    "summary": ("归纳概括", 20, "要点全面性、归类条理性、表述准确性"),
    "countermeasure": ("提出对策", 20, "问题针对性、对策可行性、条理与逻辑"),
    "applied": ("应用文写作", 40, "格式规范、身份场景契合、内容要点、语言得体"),
    "composition": ("文章写作（大作文）", 40, "立意准确、结构完整、论证充分、语言规范"),
}

MIN_ESSAY_LEN = 50     # 作文最短字数（低于此判无效提交）
MAX_ESSAY_LEN = 8000   # 防超长滥用


class EssayStore:
    """essay.db：批改历史（学员隔离，UNIQUE 无需——允许同题多次练）。"""

    def __init__(self, db_path: str | Path | None = None):
        cfg_dir = Path(__file__).resolve().parents[2] / "data"
        base = Path(db_path) if db_path else cfg_dir / "essay.db"
        base.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(base)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS essay_grading (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    essay_type TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    prompt TEXT DEFAULT '',
                    essay TEXT NOT NULL,
                    full_score INTEGER NOT NULL,
                    total REAL NOT NULL,
                    dims_json TEXT DEFAULT '[]',
                    notes_json TEXT DEFAULT '[]',
                    advice_json TEXT DEFAULT '[]',
                    summary TEXT DEFAULT '',
                    word_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_essay_user ON essay_grading(user_id, id DESC)")

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, user_id: str, essay_type: str, title: str, prompt: str,
             essay: str, result: dict) -> int:
        full = ESSAY_TYPES[essay_type][1]
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO essay_grading(user_id,essay_type,title,prompt,essay,"
                "full_score,total,dims_json,notes_json,advice_json,summary,word_count) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (user_id, essay_type, title or "", prompt or "", essay, full,
                 result.get("total", 0),
                 json.dumps(result.get("dims", []), ensure_ascii=False),
                 json.dumps(result.get("notes", []), ensure_ascii=False),
                 json.dumps(result.get("advice", []), ensure_ascii=False),
                 result.get("summary", ""), len(essay)))
            return cur.lastrowid

    def history(self, user_id: str, limit: int = 30) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,essay_type,title,total,full_score,word_count,summary,created_at "
                "FROM essay_grading WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def get(self, user_id: str, gid: int) -> dict | None:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM essay_grading WHERE user_id=? AND id=?",
                (user_id, gid)).fetchone()
        if not r:
            return None
        d = dict(r)
        for k in ("dims_json", "notes_json", "advice_json"):
            try:
                d[k.replace("_json", "")] = json.loads(d.pop(k) or "[]")
            except ValueError:
                d[k.replace("_json", "")] = []
        return d


def _extract_json(text: str) -> dict:
    """LLM 输出 → dict（剥围栏 + 扫描首个对象，同 api._extract_json_obj 简版）。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    dec = json.JSONDecoder()
    for i in range(len(t)):
        if t[i] == "{":
            try:
                obj, _ = dec.raw_decode(t[i:])
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                continue
    raise ValueError("AI 返回格式无法解析，请重试")


def _build_prompt(essay_type: str, title: str, prompt: str, essay: str) -> str:
    name, full, focus = ESSAY_TYPES[essay_type]
    return (
        f"你是公考申论阅卷组长。请批改一道「{name}」（满分 {full} 分），评分侧重：{focus}。\n"
        f"严格要求：只输出 JSON，不要多余文字。\n"
        f'输出结构：{{"total": <0-{full} 数字>, '
        f'"dims": [{{"name": "<维度>", "score": <数字>, "full": <该维度满分>, '
        f'"comment": "<一两句评语>"}}, ...至少4个维度], '
        f'"notes": [{{"quote": "<原文片段≤40字>", "note": "<批注：好在哪/问题在哪>"}}], '
        f'"advice": ["<可执行的改进建议，3-5条>"], "summary": "<总评，两句以内>"}}\n\n'
        f"【题目】{title or '（见材料要求）'}\n"
        f"【作答要求/材料要点】{prompt or '（无）'}\n"
        f"【学员作答】\n{essay}\n\n"
        "评分基准：一类文严谨给高分，有明显套路化/空洞/跑题要扣分并在批注指出；"
        "notes 引用原文片段必须真实出现在作答中。"
    )


def grade_essay(store: EssayStore, user_id: str, payload: dict,
                llm_call) -> dict:
    """批改主流程：校验 → LLM → 解析 → 落库 → 视图。llm_call(messages)->str。"""
    et = (payload.get("essay_type") or "").strip()
    if et not in ESSAY_TYPES:
        raise ValueError(f"题型无效，可选：{'/'.join(ESSAY_TYPES)}")
    essay = (payload.get("essay") or "").strip()
    if len(essay) < MIN_ESSAY_LEN:
        raise ValueError(f"作答内容太短（至少 {MIN_ESSAY_LEN} 字），请粘贴完整作答")
    if len(essay) > MAX_ESSAY_LEN:
        raise ValueError(f"作答超过 {MAX_ESSAY_LEN} 字上限")
    title = (payload.get("title") or "").strip()[:200]
    prompt = (payload.get("prompt") or "").strip()[:3000]

    out = llm_call([{"role": "user",
                     "content": _build_prompt(et, title, prompt, essay)}])
    obj = _extract_json(out)
    name, full, _ = ESSAY_TYPES[et]
    try:
        total = max(0.0, min(float(obj.get("total", 0)), float(full)))
    except (TypeError, ValueError):
        total = 0.0
    dims = []
    for d in (obj.get("dims") or [])[:6]:
        if not isinstance(d, dict):
            continue
        try:
            dims.append({"name": str(d.get("name", ""))[:30],
                         "score": max(0.0, min(float(d.get("score", 0)), 100.0)),
                         "full": max(1, min(int(d.get("full", 25) or 25), 100)),
                         "comment": str(d.get("comment", ""))[:300]})
        except (TypeError, ValueError):
            continue
    notes = [{"quote": str(n.get("quote", ""))[:60],
              "note": str(n.get("note", ""))[:400]}
             for n in (obj.get("notes") or [])[:10] if isinstance(n, dict)]
    advice = [str(a)[:300] for a in (obj.get("advice") or [])[:6] if a]
    summary = str(obj.get("summary", ""))[:400]
    result = {"total": round(total, 1), "dims": dims, "notes": notes,
              "advice": advice, "summary": summary, "essay_type": et,
              "type_name": name, "full_score": full, "word_count": len(essay)}
    gid = store.save(user_id, et, title, prompt, essay, result)
    result["id"] = gid
    return result
