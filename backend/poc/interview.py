# -*- coding: utf-8 -*-
"""批次18 · 面试练习模块（v1）。

职责：结构化面试五模块 AI 出题 → 学员文本作答 → AI 按面试评分要点点评。
存储 interview.db（学员隔离）；llm_call 依赖注入（同 essay 模式）。
"""
import json
import re
import sqlite3
from pathlib import Path

# 模块 → (名称, 出题指引)
MODULES = {
    "comprehensive": ("综合分析", "社会现象/政策观点类，考辩证思维与政治站位"),
    "organize": ("组织计划", "活动策划/调研/宣传类，考统筹与落地细节"),
    "emergency": ("应急应变", "突发场景处置类，考轻重缓急与务实解决"),
    "relations": ("人际关系", "同事/领导/群众矛盾协调类，考情商与担当"),
    "expression": ("言语表达", "演讲/串词/情景模拟类，考感染力与逻辑"),
}

MIN_ANS_LEN = 10
MAX_ANS_LEN = 3000


class InterviewStore:
    def __init__(self, db_path: str | Path | None = None):
        cfg_dir = Path(__file__).resolve().parents[2] / "data"
        base = Path(db_path) if db_path else cfg_dir / "interview.db"
        base.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(base)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS interview_practice (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    module TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT DEFAULT '',
                    score REAL,
                    review_json TEXT DEFAULT '[]',
                    advice_json TEXT DEFAULT '[]',
                    summary TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    answered_at TEXT
                )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_iv_user ON interview_practice(user_id, id DESC)")

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def save_question(self, user_id: str, module: str, question: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO interview_practice(user_id,module,question) VALUES(?,?,?)",
                (user_id, module, question))
            return cur.lastrowid

    def get(self, user_id: str, qid: int) -> dict | None:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM interview_practice WHERE user_id=? AND id=?",
                             (user_id, qid)).fetchone()
        return dict(r) if r else None

    def save_answer(self, user_id: str, qid: int, answer: str, result: dict):
        with self._connect() as conn:
            conn.execute(
                "UPDATE interview_practice SET answer=?, score=?, review_json=?, "
                "advice_json=?, summary=?, status='done', answered_at=datetime('now','localtime') "
                "WHERE user_id=? AND id=?",
                (answer, result.get("score", 0),
                 json.dumps(result.get("review", []), ensure_ascii=False),
                 json.dumps(result.get("advice", []), ensure_ascii=False),
                 result.get("summary", ""), user_id, qid))

    def history(self, user_id: str, limit: int = 30) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,module,question,score,status,created_at FROM interview_practice "
                "WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
        return [dict(r) for r in rows]


def _extract_json(text: str) -> dict:
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


def _llm_json(prompt: str, llm_call, tries: int = 3) -> dict:
    """批次27-N P2-13：AI 返回 JSON 容错重试。

    此前 gen_question/review_answer 直接 _extract_json(llm_call(...))，
    模型偶发输出带解释文字/代码块围栏时一次解析失败即 400，用户直接撞错。
    现在解析失败时带纠正提示重试（最多 tries 次），全失败才抛给上层。
    仅重试 ValueError（格式问题）；网络/超时异常由上层原有错误链路处理。
    """
    last_err: ValueError | None = None
    for i in range(tries):
        p = prompt if i == 0 else (
            prompt + "\n\n注意：你上一次的输出无法解析为 JSON。"
            "请严格只输出一个 JSON 对象，不要任何解释、前后缀或代码块围栏。"
        )
        try:
            return _extract_json(llm_call([{"role": "user", "content": p}]))
        except ValueError as e:
            last_err = e
    raise last_err if last_err else ValueError("AI 返回格式无法解析，请重试")


def gen_question(store: InterviewStore, user_id: str, module: str,
                 llm_call, difficulty: str = "standard") -> dict:
    """AI 出一道结构化面试题并落库。"""
    if module not in MODULES:
        raise ValueError(f"模块无效，可选：{'/'.join(MODULES)}")
    name, guide = MODULES[module]
    prompt = (
        f"你是公务员结构化面试考官。请出一道「{name}」模块的面试题（{guide}）。"
        f"难度：{difficulty}。\n"
        "严格要求：只输出 JSON：{\"question\": \"<题干，含场景/背景与提问，80-200字>\"}\n"
        "题干要具体（给出背景细节），避免空泛。"
    )
    obj = _llm_json(prompt, llm_call)
    q = str(obj.get("question", "")).strip()
    if len(q) < 20:
        raise ValueError("AI 出题异常，请重试")
    qid = store.save_question(user_id, module, q[:800])
    return {"id": qid, "module": module, "module_name": name, "question": q}


def review_answer(store: InterviewStore, user_id: str, qid: int,
                  answer: str, llm_call) -> dict:
    """AI 点评作答：要点/结构/语言 + 总分（百分制）+ 参考答题框架。"""
    if not answer or len(answer.strip()) < MIN_ANS_LEN:
        raise ValueError(f"作答太短（至少 {MIN_ANS_LEN} 字）")
    if len(answer) > MAX_ANS_LEN:
        raise ValueError(f"作答超过 {MAX_ANS_LEN} 字上限")
    row = store.get(user_id, qid)
    if not row:
        raise ValueError("题目不存在或无权访问")
    if row["status"] == "done":
        raise ValueError("该题已点评过，请出下一题练习")
    name = MODULES.get(row["module"], ("综合分析",))[0]
    prompt = (
        f"你是公务员结构化面试考官。考生刚作答了一道「{name}」题，请点评。\n"
        "严格要求：只输出 JSON：\n"
        "{\"score\": <0-100 数字>, "
        "\"review\": [{\"dim\": \"<要点覆盖/结构逻辑/语言表达/岗位匹配>\", "
        "\"comment\": \"<两句内评语>\"}], "
        "\"advice\": [\"<可执行改进建议 3-4 条>\"], "
        "\"outline\": \"<参考答题框架，150字内>\", \"summary\": \"<总评一句>\"}\n\n"
        f"【题目】{row['question']}\n【考生作答】\n{answer.strip()}\n\n"
        "点评要具体到考生答题内容，优点与问题并重，不套话。"
    )
    obj = _llm_json(prompt, llm_call)
    try:
        score = max(0.0, min(float(obj.get("score", 0)), 100.0))
    except (TypeError, ValueError):
        score = 0.0
    result = {
        "score": round(score, 1),
        "review": [{"dim": str(r.get("dim", ""))[:20],
                    "comment": str(r.get("comment", ""))[:300]}
                   for r in (obj.get("review") or [])[:5] if isinstance(r, dict)],
        "advice": [str(a)[:300] for a in (obj.get("advice") or [])[:5] if a],
        "outline": str(obj.get("outline", ""))[:600],
        "summary": str(obj.get("summary", ""))[:300],
    }
    store.save_answer(user_id, qid, answer.strip(), result)
    result["id"] = qid
    return result
