# -*- coding: utf-8 -*-
"""C4 · 学员增值功能 — 收藏/错题本/练习模块。

职责：
  - 收藏：记录问答快照（含 refs），支持列表/取消
  - 错题本：记录答错问题 + 正确讲解，支持复习
  - 练习：出题/提交评分（无题库时兜底随机知识点）

数据存储：独立 study.db（SQLite 标准库），与 auth.db/chat.db/pay.db 隔离。
"""
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

from .config import Config

# ---------- 会员限额配置 ----------
FAVORITE_LIMIT_FREE = 50
FAVORITE_LIMIT_MEMBER = -1  # -1 表示不限

# ---------- 共享题库（自动采集的公开题库/联网搜索题目不设老师归属，所有老师可调用） ----------
SHARED_TEACHER_ID = "SHARED"

# ---------- #16 错题强化重练：艾宾浩斯复习节奏 ----------
# 复习间隔（天）：第 n 轮连续答对后，到下一轮复习的间隔。
# 索引 = 已连续答对次数(stage)；stage=1 → 1 天后复习，stage=5 → 30 天后复习，
# stage=6 = 已掌握（移出复习队列）。
REVIEW_INTERVALS_DAYS = [1, 2, 4, 7, 15, 30]
REVIEW_MASTER_STAGE = 6  # 连续答对 6 次即视为已掌握


def _teacher_scope_sql(alias: str = "teacher_id") -> str:
    """按老师查询 + 共享题库并集的 SQL 片段：`(teacher_id=? OR teacher_id='SHARED')`。"""
    return f"({alias}=? OR {alias}='{SHARED_TEACHER_ID}')"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- #33 R2：题目配图（question_bank.images 列） ----------

def _imgs_json(urls) -> str:
    """图片 URL 列表 → images 列 JSON 字符串（仅合法 /api/qimg/ 白名单 URL，去重保序）。"""
    import json
    from .ingest import QIMG_URL_RE

    if isinstance(urls, str):  # 容错：传了单个字符串
        urls = [urls]
    out: list[str] = []
    for u in urls or []:
        if isinstance(u, str) and QIMG_URL_RE.match(u) and u not in out:
            out.append(u)
    return json.dumps(out, ensure_ascii=False) if out else ""


def _parse_images(val) -> list:
    """images 列 JSON → URL 列表（坏值/旧库空值容错为 []）。"""
    if not val:
        return []
    import json
    try:
        v = json.loads(val)
    except (ValueError, TypeError):
        return []
    if not isinstance(v, list):
        return []
    return [u for u in v if isinstance(u, str)]


def _extract_imgs_from_question(question: str) -> list:
    """从题干文本提取合法图片 URL（import 直调未带 images 字段时的兜底）。"""
    from .ingest import extract_img_urls

    return extract_img_urls(question or "")


# #38 R1 申论归一化查重：剔除空白/标点后按全文比较，拦截换行/标点差异造成的近重复
# （仅 essay 主观题使用——题干即全文，选项独立成列不会像 choice 那样误并）。
def _norm_question(text: str) -> str:
    t = re.sub(r"[\s\u3000]+", "", text or "")
    t = re.sub(r"[，。；：、,. ;:,;（）()\"\'‘’“”？！?~～!！【】\[\]「」『』…·-]+", "", t)
    return t.strip()


def _norm_source(s: str) -> str:
    """来源同源归一化：仅去空白，保留 url 结构；同一来源多次采集（含尾斜杠差异）识别为同源。"""
    return "".join((s or "").split())


# #35 R1 课程分类（8 大类，与老师管理 course_category 枚举一致）
QUESTION_CATEGORIES = ["言语理解", "判断推理", "数量关系", "资料分析",
                       "常识判断", "申论", "面试", "综合"]

_CATEGORY_ALIAS = {
    "言语": "言语理解", "言语理解与表达": "言语理解", "片段阅读": "言语理解",
    "逻辑填空": "言语理解", "语句表达": "言语理解",
    "判断": "判断推理", "图形推理": "判断推理", "定义判断": "判断推理",
    "类比推理": "判断推理", "逻辑判断": "判断推理",
    "数量": "数量关系", "数学运算": "数量关系", "数字推理": "数量关系",
    "资料": "资料分析",
    "常识": "常识判断", "公共基础": "常识判断",
    "政治理论": "常识判断",  # 2026 国考新大纲模块并入常识
    "申论": "申论", "面试": "面试", "综合": "综合", "其他": "综合",
}


def normalize_category(raw) -> str:
    """归一化课程分类：别名映射 → 精确匹配 → 包含匹配 → 兜底空串（不合法返回 ''，由调用方决定兜底值）。"""
    v = str(raw or "").strip()
    if not v:
        return ""
    if v in QUESTION_CATEGORIES:
        return v
    if v in _CATEGORY_ALIAS:
        return _CATEGORY_ALIAS[v]
    for k, tgt in _CATEGORY_ALIAS.items():
        if k in v:
            return tgt
    for c in QUESTION_CATEGORIES:
        if c in v:
            return c
    return ""


# ---------------- 批次19 知识体系 → 练习联动 ----------------

# 知识树模块名 → 题库 category 大类映射（L3 模块级兜底；题库 category 为 6 枚举）
_KB_MODULE_CATEGORY = {
    "言语理解与表达": "言语理解",
    "判断推理": "判断推理",
    "数量关系": "数量关系",
    "资料分析": "资料分析",
    "常识判断": "常识判断",
}

_KP_STRIP_SUFFIXES = ("问题", "策略", "题")  # 词根剥离后缀（链式）


def _kp_terms(name: str) -> list[str]:
    """知识点/章节名 → 题库检索词根序列（批次19 练习联动）。
    规则：原名 → 链式剥离「问题/策略/题」后缀 → 按「与」拆分；
    词根去重保序，长度<2 的碎片丢弃（避免单字误命中）。"""
    t = (name or "").strip()
    if not t:
        return []
    terms = [t]
    cur = t
    for suf in _KP_STRIP_SUFFIXES:
        if cur.endswith(suf) and len(cur) - len(suf) >= 2:
            cur = cur[: -len(suf)]
            terms.append(cur)
    if "与" in t:
        terms.extend(p for p in t.split("与") if len(p) >= 2)
    seen: set[str] = set()
    out: list[str] = []
    for x in terms:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


# 题目来源（枚举类型 + 可选出处）。source_type 枚举：AI采集 / 用户提供 / 公开题库 / 文档库 / 互联网搜索
QUESTION_SOURCE_TYPES = ["AI采集", "用户提供", "公开题库", "文档库", "互联网搜索"]


def normalize_source_type(raw) -> str:
    """归一化题目来源类型：精确匹配 → 忽略空白匹配 → 兜底空串（由调用方决定默认值）。"""
    v = str(raw or "").strip()
    if not v:
        return ""
    for t in QUESTION_SOURCE_TYPES:
        if v == t:
            return t
    for t in QUESTION_SOURCE_TYPES:
        if v.replace(" ", "") == t.replace(" ", ""):
            return t
    return ""


# ============================================================
# 批次26-F · 答案可信度门禁（防 LLM 幻觉答案入判分链路）
# 两道校验，命中任一 → answer_status='pending'（待核，不参与判分）：
#   a) 选项范围：choice 答案字母必须都 ∈ 选项字母集合（防 answer='E' 而选项只有 A-D）
#   b) 解析一致性：解析中的"结论性字母"（故选X/正确答案为X…）必须与答案一致
# 判分层 _answer_trusted 已有 pending 拦截；本函数负责入库时自动打标。
# 设计取向：宽松防误伤——只认明确"结论模式"，排除式引用（A错误/B正确）不参与。
# ============================================================

_ANS_CONCL_PAT = re.compile(
    r"(?:故本题选|故选|因此选|所以选|本题选|应选|答案为|答案是|答案[:：]|"
    r"正确答案为|正确答案是|正确答案[:：])\s*([A-Ha-h])")
_ANS_CONCL_JUDGE_PAT = re.compile(
    r"(?:答案为|答案是|答案[:：]|正确答案为|正确答案是|正确答案[:：]|故选)\s*"
    r"(对|错|正确|错误|√|×)")


def _ans_letters(raw: str) -> list[str]:
    """提取答案中的选项字母（支持单选 A、多选 ABD，容忍括号/标点/空格）。"""
    return sorted({m.upper() for m in re.findall(r"[A-Ha-h]", str(raw or ""))})


def _option_keys(options) -> set[str]:
    """从选项列表解析字母键（"A. xxx"/"A、xxx"/["A xxx"] → {'A','B',...}）。"""
    keys: set[str] = set()
    for o in options or []:
        m = re.match(r"\s*（?([A-Ha-h])）?\s*[.、．:：)）]?", str(o or "").strip())
        if m:
            keys.add(m.group(1).upper())
    return keys


def answer_consistency(qtype: str, answer: str,
                       options=None, analysis: str = "") -> str:
    """判定答案可信度：'' = 可信；'pending' = 待核（答案与选项/解析矛盾）。

    仅 choice/judge 校验；essay 的 answer 是参考答案文本，不校验。
    answer 为空不在此处理（入库层已拒空答案）。"""
    if (qtype or "").strip() != "choice":
        if (qtype or "").strip() == "judge":
            m = _ANS_CONCL_JUDGE_PAT.search(str(analysis or ""))
            if m:
                concl = {"对": "对", "正确": "对", "√": "对",
                         "错": "错", "错误": "错", "×": "错"}.get(m.group(1), "")
                ans = {"对": "对", "正确": "对", "√": "对", "T": "对", "TRUE": "对",
                       "错": "错", "错误": "错", "×": "错", "F": "错", "FALSE": "错",
                       "X": "错"}.get(str(answer or "").strip(), "")
                if concl and ans and concl != ans:
                    return "pending"
        return ""
    ans_letters = _ans_letters(answer)
    if not ans_letters:
        return "pending"          # choice 无可识别答案字母 → 待核
    opt_keys = _option_keys(options)
    if opt_keys and not set(ans_letters) <= opt_keys:
        return "pending"          # 答案字母超出选项范围（如 answer=E 而选项仅 A-D）
    concl = {m.upper() for m in _ANS_CONCL_PAT.findall(str(analysis or ""))}
    concl = {c for c in concl if c in (opt_keys or set(concl))}  # 解析引用不存在的选项不计
    if concl and (set(ans_letters) != concl or len(concl) > 1):
        return "pending"          # 解析结论与答案矛盾 / 解析自身结论互斥
    return ""


class StudyStore:
    """学习数据存储（收藏/错题/练习）。"""

    def __init__(self, db_path: str | Path | None = None):
        default = Config.data_dir / "study.db"
        self.db_path = Path(db_path or os.environ.get("STUDY_DB") or default)
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
            CREATE TABLE IF NOT EXISTS favorites (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                teacher_id  TEXT NOT NULL,
                question    TEXT NOT NULL,
                answer      TEXT NOT NULL,
                refs        TEXT DEFAULT '',   -- JSON 数组 [{"doc_name", "score"}]
                session_id  TEXT DEFAULT '',
                fav_group   TEXT DEFAULT '',   -- 收藏分组（空=未分组；#批次3 平滑迁移）
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fav_user ON favorites(user_id);
            CREATE INDEX IF NOT EXISTS idx_fav_teacher ON favorites(teacher_id);

            CREATE TABLE IF NOT EXISTS mistakes (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       TEXT NOT NULL,
                teacher_id    TEXT NOT NULL,
                question      TEXT NOT NULL,
                user_answer   TEXT DEFAULT '',
                correct_note  TEXT NOT NULL,
                refs          TEXT DEFAULT '',
                session_id    TEXT DEFAULT '',
                created_at    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mistake_user ON mistakes(user_id);
            CREATE INDEX IF NOT EXISTS idx_mistake_teacher ON mistakes(teacher_id);

            CREATE TABLE IF NOT EXISTS practice_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                teacher_id  TEXT NOT NULL,
                question    TEXT NOT NULL,
                answer      TEXT NOT NULL,
                score       REAL DEFAULT 0,   -- 0-1 或 -1 表示待评分
                feedback    TEXT DEFAULT '',
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_practice_user ON practice_logs(user_id);

            CREATE TABLE IF NOT EXISTS answer_feedback (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                teacher_id  TEXT NOT NULL,
                session_id  TEXT DEFAULT '',
                question    TEXT NOT NULL,
                answer      TEXT NOT NULL,
                rating      TEXT NOT NULL,    -- 'up' | 'down'
                reason      TEXT DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'new',  -- #26 R3 处理状态: 'new' | 'resolved'
                handler_note TEXT DEFAULT '',             -- #26 R3 处理备注
                handled_at  TEXT DEFAULT '',              -- #26 R3 处理时间
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_feedback_user ON answer_feedback(user_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_teacher ON answer_feedback(teacher_id);

            -- #13 真实题库：客观题（选择/判断）+ 主观题（简答）
            CREATE TABLE IF NOT EXISTS question_bank (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id   TEXT NOT NULL,
                qtype        TEXT NOT NULL,      -- 'choice' | 'judge' | 'essay'
                question     TEXT NOT NULL,
                options      TEXT DEFAULT '',    -- JSON 数组 ["A. xxx","B. xxx"...]（choice 用）
                answer       TEXT NOT NULL,      -- choice: 'A'/'B'..；judge: '对'/'错'；essay: 参考要点
                analysis     TEXT DEFAULT '',    -- 解析
                difficulty   INTEGER DEFAULT 1,  -- 1~5
                knowledge_point TEXT DEFAULT '', -- #16 知识点维度（AI 采集自动分类）
                enabled      INTEGER DEFAULT 1,
                created_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_qbank_teacher ON question_bank(teacher_id, qtype);
            """)
            # 迁移：#16 新增 knowledge_point 列（向后兼容旧库）
            try:
                conn.execute("ALTER TABLE question_bank ADD COLUMN knowledge_point TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # 迁移：#24 R2 题型树关联（question_bank.category_id → knowledge_nodes.node_id）
            try:
                conn.execute("ALTER TABLE question_bank ADD COLUMN category_id TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # 迁移：#33 R2 题目配图（JSON 数组 ["/api/qimg/T001/xxx.png"]，AI 采集落盘）
            try:
                conn.execute("ALTER TABLE question_bank ADD COLUMN images TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # 迁移：#35 R1 课程分类（8 大类枚举，AI 采集自动打标）
            try:
                conn.execute("ALTER TABLE question_bank ADD COLUMN category TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # 迁移：批次12a 外部题源 ID（如 "fenbi:123456"）——解析图重采/按源回查的对齐键
            try:
                conn.execute("ALTER TABLE question_bank ADD COLUMN ext_id TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # 迁移：题目来源（枚举类型 + 可选出处）
            try:
                conn.execute("ALTER TABLE question_bank ADD COLUMN source_type TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE question_bank ADD COLUMN source TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # 溯源：采集题目记录来源页面 URL（套卷链接），采集问题可直接按页定位题
            try:
                conn.execute("ALTER TABLE question_bank ADD COLUMN source_url TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # 答案可信度：'' = 正常；'pending' = 采集所得答案互相矛盾或为占位符，
            # 待人工核实。判分与练习下发把 pending 视为无答案（不判错、不记错题本）。
            try:
                conn.execute("ALTER TABLE question_bank ADD COLUMN answer_status TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # 迁移：#26 R3 答案反馈处理状态（旧库平滑升级）
            try:
                conn.execute("ALTER TABLE answer_feedback ADD COLUMN status TEXT NOT NULL DEFAULT 'new'")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE answer_feedback ADD COLUMN handler_note TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE answer_feedback ADD COLUMN handled_at TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # 迁移后建索引（status 列为 ALTER 添加，索引必须在列就位后建）
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_status ON answer_feedback(status)")
            # 溯源索引：按采集来源页 URL 查题（采集质量回查/套卷定位）
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qbank_source_url "
                "ON question_bank(source_url)")
            # 迁移：#36 分类专项练习/学情报告/错题重练（practice_logs 关联题目+分类+批改报告；mistakes 关联原题）
            for _ddl in (
                "ALTER TABLE practice_logs ADD COLUMN question_id INTEGER",
                "ALTER TABLE practice_logs ADD COLUMN category TEXT DEFAULT ''",
                "ALTER TABLE practice_logs ADD COLUMN grade_report TEXT DEFAULT ''",
                "ALTER TABLE mistakes ADD COLUMN question_id INTEGER",
            ):
                try:
                    conn.execute(_ddl)
                except sqlite3.OperationalError:
                    pass  # 列已存在
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_practice_qid ON practice_logs(user_id, question_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_practice_cat ON practice_logs(user_id, category)")

            # #16 错题强化重练：艾宾浩斯复习节奏表
            # stage = 已连续答对次数；0 = 尚未开始复习（首次答错后进入）；
            # next_review_at = 下一轮应复习时间（ISO）；全员已掌握则移出（删除行）。
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS review_schedule (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       TEXT NOT NULL,
                question_id   INTEGER NOT NULL,
                stage         INTEGER NOT NULL DEFAULT 0,
                next_review_at TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                UNIQUE(user_id, question_id)
            );
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_review_due ON review_schedule(user_id, next_review_at)")

            # 批次22 个性化学习计划：一用户一老师同时只保留一份 active 计划
            # payload = JSON {"days":[{day,title,focus,items:[{kind,node_id,name,module,level,note,done}]}]}
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS study_plans (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                teacher_id  TEXT NOT NULL,
                days        INTEGER NOT NULL DEFAULT 7,
                payload     TEXT NOT NULL,
                active      INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL
            );
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_study_plan_user "
                "ON study_plans(user_id, teacher_id, active)")

            # 批次26-G AI 多角度解析缓存：按题干签名共享（同题跨用户/老师复用）
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS ai_analysis (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                question_key TEXT NOT NULL,
                content      TEXT NOT NULL,   -- JSON {verdict,reason,sections:[{title,content}]}
                model        TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL,
                UNIQUE(question_key)
            );
            """)

        # 收藏分组平滑迁移：老库补 fav_group 列（列已存在则忽略，不阻断启动）
        try:
            with self._connect() as conn:
                conn.execute(
                    "ALTER TABLE favorites ADD COLUMN fav_group TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # duplicate column name: fav_group → 已迁移

    # ==================== 收藏 ====================
    def add_favorite(self, user_id: str, teacher_id: str, question: str,
                     answer: str, refs: list[dict] | None = None,
                     session_id: str = "", group: str = "") -> dict:
        """收藏一条问答记录。group 为收藏分组（空=未分组，旧客户端不传完全兼容）。"""
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
                """INSERT INTO favorites(user_id, teacher_id, question, answer, refs, session_id, fav_group, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, teacher_id, question, answer, refs_json, session_id,
                 (group or "").strip()[:50], now),
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
            "fav_group": (group or "").strip()[:50],
            "created_at": now,
        }

    def list_favorites(self, user_id: str, teacher_id: str | None = None,
                       limit: int = 50, group: str | None = None) -> list[dict]:
        """收藏列表。group：None=全部；''=仅未分组；其余=按分组精确筛选。"""
        where = "user_id=?"
        args: list = [user_id]
        if teacher_id:
            where += " AND teacher_id=?"
            args.append(teacher_id)
        if group is not None:
            where += " AND fav_group=?"
            args.append(group)
        args.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM favorites WHERE {where} ORDER BY created_at DESC LIMIT ?",
                args,
            ).fetchall()
        return [self._parse_refs(r) for r in rows]

    def set_favorite_group(self, user_id: str, fav_id: int, group: str) -> bool:
        """设置/更改收藏分组（group 传空串=移出分组）。"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE favorites SET fav_group=? WHERE id=? AND user_id=?",
                ((group or "").strip()[:50], fav_id, user_id),
            )
        return cur.rowcount > 0

    def list_favorite_groups(self, user_id: str) -> list[str]:
        """该用户的收藏分组名列表（排除未分组空串，按名称排序）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT fav_group g FROM favorites "
                "WHERE user_id=? AND fav_group!='' ORDER BY g",
                (user_id,),
            ).fetchall()
        return [r["g"] for r in rows]

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
        # 批次27-N P2-11：删除错题时联动移出复习队列。review_schedule 按
        # question_id 排程，错题删掉后残留会产生"幽灵复习项"（题已不在
        # 错题本，复习队列仍推）。有 question_id 才联动；手抄错题无关联不受影响。
        with self._connect() as conn:
            row = conn.execute(
                "SELECT question_id FROM mistakes WHERE id=? AND user_id=?",
                (mist_id, user_id),
            ).fetchone()
            cur = conn.execute(
                "DELETE FROM mistakes WHERE id=? AND user_id=?",
                (mist_id, user_id),
            )
            if row and row["question_id"] is not None:
                conn.execute(
                    "DELETE FROM review_schedule WHERE user_id=? AND question_id=?",
                    (user_id, row["question_id"]),
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

    # ==================== 练习 ====================
    # #13 真实题库：优先题库抽题，题库为空回落知识库知识点兜底
    def _qbank_pool(self, teacher_id: str, qtype: str | None = None,
                    category: str | None = None,
                    min_difficulty: int | None = None,
                    max_difficulty: int | None = None) -> list[dict]:
        """题库可用题目（enabled=1，按老师/共享题库/题型/分类/难度过滤）。
        category 为 #36 分类专项练习；min/max_difficulty 为 #13 难度预估区间。"""
        sql = f"SELECT * FROM question_bank WHERE {_teacher_scope_sql()} AND enabled=1"
        args: list = [teacher_id]
        if qtype:
            sql += " AND qtype=?"
            args.append(qtype)
        if category:
            sql += " AND category=?"
            args.append(category)
        if min_difficulty:
            sql += " AND difficulty>=?"
            args.append(min_difficulty)
        if max_difficulty:
            sql += " AND difficulty<=?"
            args.append(max_difficulty)
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
                     ext_id: str = "",
                     answer_status: str | None = None,
                     sensitive_check: bool = False) -> dict:
        """新增题库题目（管理端/CLI 用）。category_id 关联题型树节点（#24 R2）；
        images 为题干配图 URL 列表（#33 R2，白名单外 URL 自动剔除）；
        category 为课程大类（#35 R1，8 枚举，非法值兜底"综合"）；
        source_type/source 为题目来源（枚举类型 + 可选出处）；
        answer_status：批次26-F 答案可信度标记——None=自动判定（选项范围+
        解析一致性校验，矛盾打 'pending' 待核不参与判分）；显式传 ''/'pending'
        则尊重调用方（可信来源如粉笔官方 API 可强制置可信）；
        sensitive_check=True 时启用 #10 入库敏感词守卫：题干/答案/解析任一
        命中敏感词即抛 ValueError（调用方决定拦截或标记，避免脏题入库）。"""
        if qtype not in ("choice", "judge", "essay"):
            raise ValueError("qtype 必须是 choice/judge/essay")
        if not question or not answer:
            raise ValueError("题目和答案必填")
        if answer_status is None:
            answer_status = answer_consistency(qtype, answer, options, analysis)
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
                    source_type, source, source_url, ext_id, answer_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)""",
                (teacher_id, qtype, question, opts_json, answer, analysis,
                 max(1, min(5, int(difficulty))), knowledge_point or "",
                 (category_id or "").strip(), _imgs_json(images), now, n_cat,
                 n_source_type, (source or "").strip(), (source_url or "").strip(),
                 (ext_id or "").strip(), (answer_status or "").strip()),
            )
            qid = cur.lastrowid
        return {"id": qid, "teacher_id": teacher_id, "qtype": qtype,
                "question": question, "answer": answer,
                "answer_status": (answer_status or "").strip(),
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
            # 归一化查重 + 同源判重：命中重复时再判断来源——来源一致（同一张
            # 试卷被重复采集/同卷补采）计 dupe_source，逐题 continue 跳过；
            # 来源不同不计数（跨站同题可能只是撞题）。不做整卷 break：卷内
            # 模板句相似（如图推题干）曾误触发整卷丢弃 56 题（含全部资料分析）。
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
                continue
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
                    # 批次12a：外部题源 ID（粉笔题 id 等），解析图重采/按源回查对齐键
                    ext_id=(it.get("ext_id") or "").strip(),
                    # 批次26-F：答案可信度（缺省 None=入库时自动判定，矛盾打 pending）
                    answer_status=(it.get("answer_status")
                                   if isinstance(it.get("answer_status"), str) else None),
                    sensitive_check=sensitive_check,
                )
            except ValueError:
                # #10 敏感词命中（或其它校验失败）：整题拒绝，不计入 added
                filtered += 1
                continue
            added += 1
        return {"added": added, "skipped": skipped, "invalid": invalid,
                "filtered": filtered, "dupe_source": dupe_source}

    # ---------------- 批次26-G · AI 多角度解析缓存 ----------------

    def get_ai_analysis(self, question_key: str) -> dict | None:
        """按题干签名取缓存的 AI 解析；无缓存返回 None。"""
        with self._connect() as conn:
            r = conn.execute(
                "SELECT content, model, created_at FROM ai_analysis "
                "WHERE question_key=?", (question_key,)).fetchone()
        if not r:
            return None
        try:
            data = json.loads(r["content"])
        except Exception:  # noqa: BLE001  缓存损坏视为无
            return None
        return {"content": data, "model": r["model"],
                "created_at": r["created_at"]}

    def save_ai_analysis(self, question_key: str, content: dict,
                         model: str = "") -> None:
        """写入/覆盖 AI 解析缓存（同题签名唯一）。"""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ai_analysis(question_key, content, model, created_at) "
                "VALUES (?,?,?,?) ON CONFLICT(question_key) DO UPDATE SET "
                "content=excluded.content, model=excluded.model, "
                "created_at=excluded.created_at",
                (question_key, json.dumps(content, ensure_ascii=False),
                 model or "", _now_iso()))

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

    # ---------- 批次12b 在线模考 ----------

    def mock_source_papers(self, teacher_id: str) -> list[dict]:
        """在线模考：可用的真题卷列表（按 source 卷名分组，含题量与模块分布）。
        仅统计客观题（choice）——在线模考首版只做客观题自动评分。"""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT source, COUNT(*) AS n, "
                f"SUM(CASE WHEN category='判断推理' THEN 1 ELSE 0 END) AS judgement, "
                f"SUM(CASE WHEN category='言语理解' THEN 1 ELSE 0 END) AS verbal, "
                f"SUM(CASE WHEN category='常识判断' THEN 1 ELSE 0 END) AS common, "
                f"SUM(CASE WHEN category='数量关系' THEN 1 ELSE 0 END) AS quant, "
                f"SUM(CASE WHEN category='资料分析' THEN 1 ELSE 0 END) AS data "
                f"FROM question_bank WHERE {_teacher_scope_sql()} AND enabled=1 "
                f"AND qtype='choice' AND source<>'' AND source_type='公开题库' "
                f"GROUP BY source ORDER BY n DESC",
                (teacher_id,),
            ).fetchall()
        return [{
            "source": r["source"], "count": r["n"],
            "mix": {k: r[k] for k in ("judgement", "verbal", "common", "quant", "data") if r[k]},
        } for r in rows]

    def sample_mock_paper(self, teacher_id: str, source: str = "",
                          mix: dict | None = None) -> list[dict]:
        """在线模考抽题。source 非空=真题卷模式（该卷全部客观题，按原卷顺序）；
        source 为空=结构组卷（mix={分类: 题量}，各模块随机抽，不足全给）。"""
        if source:
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT * FROM question_bank WHERE {_teacher_scope_sql()} AND enabled=1 "
                    f"AND qtype='choice' AND source=? ORDER BY id",
                    (teacher_id, source),
                ).fetchall()
            return self._parse_qrows(rows)
        if not mix:
            return []
        picked: list[dict] = []
        seen: set[int] = set()
        for cat, n in mix.items():
            n = int(n or 0)
            if n <= 0:
                continue
            pool = self.sample_questions(teacher_id, category=str(cat), qtype="choice",
                                         count=n, exclude_qids=list(seen))
            for q in pool:
                if q.get("id") not in seen:
                    seen.add(q["id"])
                    picked.append(q)
        return picked

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
        """题库行 → 练习响应（#36 附 category 供前端徽标）。
        批次12c：options 兼容解析——list_due_reviews 直传 sqlite 行（options 为
        JSON 字符串），未走 _parse_qrows；统一在此确保数组形态。"""
        options = q.get("options") or []
        if isinstance(options, str):
            import json as _json
            try:
                options = _json.loads(options)
            except (ValueError, TypeError):
                options = []
        if not isinstance(options, list):
            options = []
        resp = {
            "question": q["question"],
            "type": q["qtype"],
            "options": options,
            "id": q["id"],
            "hint": self._qtype_label(q["qtype"]),
            "category": q.get("category") or "综合",
            "difficulty": q.get("difficulty") or 3,
            # 无答案题（仅题干+选项的练习卷）或答案待核题：前端据此提示"暂无答案"，
            # 不再误走主观题 AI 批改分支（score=-1 与"主观题待评"共用）
            "has_answer": self._answer_trusted(q),
            # 批次26-F/G：answer_status 透传——pending 题前端走"待核+AI解析+搜题"分支
            "answer_status": (q.get("answer_status") or ""),
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

    def _qbank_like_pool(self, teacher_id: str, term: str) -> list[dict]:
        """批次19：按词根模糊检索题库（knowledge_point / category 两列 LIKE）。"""
        like = f"%{term}%"
        sql = (f"SELECT * FROM question_bank WHERE {_teacher_scope_sql()} AND enabled=1 "
               "AND (knowledge_point LIKE ? OR category LIKE ?) ORDER BY id")
        with self._connect() as conn:
            rows = conn.execute(sql, (teacher_id, like, like)).fetchall()
        return self._parse_qrows(rows)

    def get_practice_by_knowledge(self, teacher_id: str, node_id: str,
                                  user_id: str = "anonymous",
                                  ks=None) -> dict:
        """批次19：按知识树节点抽题（知识页「去练习」联动）。
        三级检索逐级回退：
          L1 知识点词根 → knowledge_point/category LIKE（如「比重问题」→ %比重%）
          L2 所属章节名词根 → 同上（如「图形推理」章节）
          L3 模块名 → category 大类精确（题库 6 枚举，保证必有题）
        命中后随机抽一题，payload 附 practice_scope（命中层级/词根/节点完整路径）。
        ks 参数供测试注入临时 KnowledgeStore（默认全局单例）。"""
        import random
        if ks is None:
            from poc.knowledge import get_knowledge_store
            ks = get_knowledge_store()
        node = ks.get_node(node_id) if node_id else None
        if not node or node.get("teacher_id") != teacher_id:
            raise ValueError("知识点不存在")
        if node.get("tree_type") != "knowledge":
            raise ValueError("仅知识体系树节点支持练习联动")
        # 祖先链（自叶向根）
        chain = [node]
        seen_ids = {node["node_id"]}
        cur = node
        while cur.get("parent_id"):
            p = ks.get_node(cur["parent_id"])
            if not p or p["node_id"] in seen_ids:
                break
            chain.append(p)
            seen_ids.add(p["node_id"])
            cur = p
        node_path = [n["name"] for n in reversed(chain)]

        def _hit(pool: list[dict], level: str, term: str) -> dict:
            resp = self._practice_payload(random.choice(pool))
            resp["practice_scope"] = {
                "level": level,
                "term": term,
                "node_path": node_path,
                "note": {"knowledge": "🎯 知识点专项", "chapter": "📂 章节专项",
                         "module": "📚 模块练习"}[level],
            }
            return resp

        # L1 知识点词根
        for term in _kp_terms(node["name"]):
            pool = self._qbank_like_pool(teacher_id, term)
            if pool:
                return _hit(pool, "knowledge", term)
        # L2 章节词根（chain[1] 为直接父节点）
        if len(chain) > 1:
            for term in _kp_terms(chain[1]["name"]):
                pool = self._qbank_like_pool(teacher_id, term)
                if pool:
                    return _hit(pool, "chapter", term)
        # L3 模块兜底（映射大类 → 原名，各试一次）
        module = chain[-1]["name"]
        for cat in dict.fromkeys([_KB_MODULE_CATEGORY.get(module, ""), module]):
            if not cat:
                continue
            pool = self._qbank_pool(teacher_id, category=cat)
            if pool:
                return _hit(pool, "module", cat)
        raise ValueError("该知识点暂无可练习的题目")

    def knowledge_mastery(self, teacher_id: str, user_id: str, ks=None) -> dict:
        """批次20 知识掌握度地图：知识树叶 × 用户练习记录聚合。
        匹配：叶子词根（_kp_terms）命中题目 knowledge_point/category（子串包含），
        聚合该题集的判分练习（口径与 practice_report 一致：score>=0 计入，>0 判对）。
        返回 {node_id: {done, correct, accuracy, level}}；
        level: none(未练) / weak(<60%) / mid(60-79% 或样本<3) / strong(≥80% 且 ≥3 题)。
        章节级由前端子叶聚合，后端只出叶子。"""
        if ks is None:
            from poc.knowledge import get_knowledge_store
            ks = get_knowledge_store()
        tree = ks.get_tree(teacher_id, "", "knowledge")
        # 批次23 四层树：掌握度锚定「知识点层」（模块0/章节1/知识点2），
        # 不再下钻考点层（depth3）——词根按知识点名匹配题库标注
        leaves: list[dict] = []

        def _walk(nodes: list[dict], depth: int = 0) -> None:
            for n in nodes or []:
                if depth == 2:
                    leaves.append(n)
                elif n.get("children"):
                    _walk(n["children"], depth + 1)

        _walk(tree.get("nodes") or [])
        if not leaves:
            return {"mastery": {}, "total_done": 0}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT pl.score, qb.knowledge_point AS kp, qb.category AS cat "
                "FROM practice_logs pl LEFT JOIN question_bank qb ON qb.id = pl.question_id "
                "WHERE pl.user_id=? AND pl.teacher_id=? AND pl.score>=0",
                (user_id, teacher_id),
            ).fetchall()
        recs = [(r["kp"] or "", r["cat"] or "", 1 if r["score"] > 0 else 0) for r in rows]
        out: dict[str, dict] = {}
        for leaf in leaves:
            terms = _kp_terms(leaf["name"])
            done = correct = 0
            for kp, cat, ok in recs:
                if any(t in kp for t in terms) or any(t in cat for t in terms):
                    done += 1
                    correct += ok
            acc = round(correct / done, 4) if done else None
            if not done:
                level = "none"
            elif acc >= 0.8 and done >= 3:
                level = "strong"
            elif acc >= 0.6:
                level = "mid"
            else:
                level = "weak"
            out[leaf["node_id"]] = {"done": done, "correct": correct,
                                    "accuracy": acc, "level": level}
        return {"mastery": out, "total_done": len(recs)}

    # ==================== 批次22 个性化学习计划 ====================
    _PLAN_DAY_TITLES = ["攻弱补短", "持续精进", "稳步推进", "查漏补缺",
                        "冲刺演练", "申论实战", "面试与模考"]
    _PLAN_TTL_DAYS = 7

    def _plan_leaves(self, ks, teacher_id: str) -> list[dict]:
        """收集「知识点层」节点（批次23 四层树：模块0/章节1/知识点2）：
        [{node_id, name, module}]。考点层不进计划。"""
        tree = ks.get_tree(teacher_id, "", "knowledge")
        leaves: list[dict] = []

        def _walk(nodes: list[dict], module: str = "", depth: int = 0) -> None:
            for n in nodes or []:
                mod = module or n["name"]
                if depth == 2:
                    leaves.append({"node_id": n["node_id"], "name": n["name"],
                                   "module": mod})
                elif n.get("children"):
                    _walk(n["children"], mod, depth + 1)

        _walk(tree.get("nodes") or [])
        return leaves

    @staticmethod
    def _plan_item(kind: str, name: str, module: str = "", level: str = "",
                   node_id: str = "", accuracy=None, note: str = "") -> dict:
        return {"kind": kind, "name": name, "module": module, "level": level,
                "node_id": node_id, "accuracy": accuracy, "note": note,
                "done": False}

    def generate_study_plan(self, teacher_id: str, user_id: str, ks=None) -> dict:
        """批次22 个性化学习计划：掌握度驱动生成 7 天冲刺处方（纯规则零 LLM）。
        结构：Day1-5 行测弱项（weak 按正确率升序 → mid → none）每天 3 点；
        Day6 申论日（弱项 2 点 + 写作任务）；Day7 面试 + 本周模考收官。
        生成即作废旧计划（同用户同老师同时仅一份 active）。"""
        if ks is None:
            from poc.knowledge import get_knowledge_store
            ks = get_knowledge_store()
        leaves = self._plan_leaves(ks, teacher_id)
        if not leaves:
            raise ValueError("知识树为空，请先初始化知识体系")
        mastery = self.knowledge_mastery(teacher_id, user_id, ks)["mastery"]

        xingce: dict[str, list[dict]] = {"weak": [], "mid": [], "none": []}
        shenlun: dict[str, list[dict]] = {"weak": [], "mid": [], "none": []}
        mianshi: dict[str, list[dict]] = {"weak": [], "mid": [], "none": []}
        buckets = {"申论": shenlun, "面试": mianshi}
        for lf in leaves:
            m = mastery.get(lf["node_id"]) or {}
            lv = m.get("level") or "none"
            if lv == "strong":
                continue
            pool = buckets.get(lf["module"], xingce)[lv]
            pool.append(self._plan_item("kp", lf["name"], lf["module"], lv,
                                        lf["node_id"], m.get("accuracy")))
        # 行测：weak 按正确率升序（越差越靠前）→ mid 同规则 → none 保持树序
        for pool in (xingce, shenlun, mianshi):
            pool["weak"].sort(key=lambda x: (x["accuracy"] if x["accuracy"] is not None else 0.0))
            pool["mid"].sort(key=lambda x: (x["accuracy"] if x["accuracy"] is not None else 0.0),
                             )
        ordered_xc = xingce["weak"] + xingce["mid"] + xingce["none"]

        def take(pool: dict, n: int) -> list[dict]:
            out = pool["weak"][:n]
            got = len(out)
            if got < n:
                out += pool["mid"][:n - got]
                got = len(out)
            if got < n:
                out += pool["none"][:n - got]
            for it in out:
                for k in ("weak", "mid", "none"):
                    if it in pool[k]:
                        pool[k].remove(it)
                        break
            return out

        # 弱项注脚
        for lv, note in (("weak", "🔴 薄弱优先"), ("mid", "🟡 待巩固"),
                         ("none", "⚪ 新学")):
            for pool in (xingce, shenlun, mianshi):
                for it in pool[lv]:
                    if not it["note"]:
                        it["note"] = note

        days: list[dict] = []
        for i in range(5):  # Day1-5 行测
            picks = take(xingce, 3)
            if not picks:
                break
            days.append({"day": i + 1, "title": f"Day {i + 1} · {self._PLAN_DAY_TITLES[i]}",
                         "focus": "行测", "items": picks})
        # Day6 申论日
        d6 = take(shenlun, 2)
        d6.append(self._plan_item("essay", "完成一篇申论大作文并 AI 批改",
                                  "申论", "task", note="✍️ C11 批改面板"))
        days.append({"day": 6, "title": f"Day 6 · {self._PLAN_DAY_TITLES[5]}",
                     "focus": "申论", "items": d6})
        # Day7 面试 + 模考
        d7 = take(mianshi, 1)
        d7.append(self._plan_item("interview", "AI 面试实战一轮（结构化问答）",
                                  "面试", "task", note="🎤 C12 面试面板"))
        d7.append(self._plan_item("mock", "一场结构组卷模考，检验本周成果",
                                  "综合", "task", note="📝 C6 组卷模考"))
        days.append({"day": 7, "title": f"Day 7 · {self._PLAN_DAY_TITLES[6]}",
                     "focus": "综合", "items": d7})

        total = sum(len(d["items"]) for d in days)
        if not total:
            raise ValueError("暂无可安排的学习项（知识树为空或全部已扎实）")

        now = _now_iso()
        with self._connect() as conn:
            conn.execute("UPDATE study_plans SET active=0 "
                         "WHERE user_id=? AND teacher_id=? AND active=1",
                         (user_id, teacher_id))
            cur = conn.execute(
                "INSERT INTO study_plans(user_id, teacher_id, days, payload, active, created_at) "
                "VALUES(?,?,?,?,1,?)",
                (user_id, teacher_id, len(days), json.dumps({"days": days}, ensure_ascii=False), now))
            plan_id = cur.lastrowid
        return {"plan_id": plan_id, "created_at": now, "days": days}

    def get_active_plan(self, teacher_id: str, user_id: str) -> dict | None:
        """当前生效计划（7 天内），含进度统计；过期懒清理。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, created_at, payload FROM study_plans "
                "WHERE user_id=? AND teacher_id=? AND active=1 "
                "ORDER BY id DESC LIMIT 1",
                (user_id, teacher_id)).fetchone()
        if row is None:
            return None
        created = row["created_at"]
        try:
            from datetime import datetime as _dtm, timezone as _tz
            ct = _dtm.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_tz.utc)
            age_days = (_dtm.now(_tz.utc) - ct).total_seconds() / 86400
        except ValueError:
            age_days = 0
        if age_days > self._PLAN_TTL_DAYS:
            with self._connect() as conn:
                conn.execute("UPDATE study_plans SET active=0 WHERE id=?", (row["id"],))
            return None
        days = json.loads(row["payload"]).get("days") or []
        items = [it for d in days for it in d.get("items") or []]
        done = sum(1 for it in items if it.get("done"))
        return {"plan_id": row["id"], "created_at": created,
                "days": days,
                "progress": {"done": done, "total": len(items),
                             "pct": round(done * 100 / len(items)) if items else 0}}

    def checkin_plan_item(self, user_id: str, plan_id: int, day: int, idx: int) -> dict:
        """打卡：翻转指定天/序位的完成状态，返回最新进度。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, payload, active FROM study_plans "
                "WHERE id=? AND user_id=?", (plan_id, user_id)).fetchone()
        if row is None or not row["active"]:
            raise ValueError("计划不存在或已失效")
        payload = json.loads(row["payload"])
        days = payload.get("days") or []
        target = next((d for d in days if d.get("day") == day), None)
        items = (target or {}).get("items") or []
        if idx < 0 or idx >= len(items):
            raise ValueError("打卡项不存在")
        items[idx]["done"] = not items[idx].get("done")
        with self._connect() as conn:
            conn.execute("UPDATE study_plans SET payload=? WHERE id=?",
                         (json.dumps(payload, ensure_ascii=False), plan_id))
        done = sum(1 for d in days for it in d["items"] if it.get("done"))
        total = sum(len(d.get("items") or []) for d in days)
        return {"done": items[idx]["done"],
                "progress": {"done": done, "total": total,
                             "pct": round(done * 100 / total) if total else 0}}

    def _difficulty_hint(self, user_id: str) -> dict:
        """#13 难度预估：近期客观题按难度档正确率 → 建议抽题难度区间。
        近 60 题：easy(1-2)/mid(3)/hard(4-5) 三桶；整体正确率<40% 降档，
        简单≥80%且中等≥60%（或中等≥85%且样本足）升档，其余匹配当前水平。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT pl.score, qb.difficulty FROM practice_logs pl "
                "JOIN question_bank qb ON qb.id = pl.question_id "
                "WHERE pl.user_id=? AND pl.score IS NOT NULL "
                "AND pl.question_id IS NOT NULL "
                "ORDER BY pl.created_at DESC LIMIT 60",
                (user_id,),
            ).fetchall()
        fit = {"level": "fit", "min_difficulty": None, "max_difficulty": None,
               "note": "⚖️ 匹配当前水平"}
        if not rows:
            fit["reason"] = "暂无答题记录，从标准难度开始"
            return fit
        buckets: dict[str, list[int]] = {}
        for r in rows:
            d = r["difficulty"] or 3
            k = "easy" if d <= 2 else ("mid" if d == 3 else "hard")
            b = buckets.setdefault(k, [0, 0])
            b[0] += 1
            b[1] += 1 if r["score"] >= 0.99 else 0

        def acc(k: str) -> float | None:
            t, c = buckets.get(k, [0, 0])
            return (c / t) if t else None

        total = len(rows)
        overall = sum(1 for r in rows if r["score"] >= 0.99) / total
        easy_a, mid_a = acc("easy"), acc("mid")
        if overall < 0.4 and total >= 5:
            return {"level": "down", "min_difficulty": 1, "max_difficulty": 2,
                    "note": "📉 基础巩固",
                    "reason": f"近期正确率 {int(overall * 100)}%，先夯实简单题"}
        high_ready = (easy_a is not None and easy_a >= 0.8
                      and (mid_a is None or mid_a >= 0.6)) or \
                     (mid_a is not None and mid_a >= 0.85 and total >= 10)
        if high_ready:
            return {"level": "up", "min_difficulty": 4, "max_difficulty": 5,
                    "note": "📈 难度提升",
                    "reason": "近期正确率优秀，推荐挑战高难度题"}
        fit["reason"] = f"近期正确率 {int(overall * 100)}%，保持当前难度"
        return fit

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

        # 1.5) 批次12c 今日练习：到期错题 → 薄弱分类未做题 → 普通抽题
        #      批次18 #13 难度预估：按近期正确率动态升降推荐难度档
        if mode == "today":
            due = self.list_due_reviews(user_id, teacher_id=teacher_id, limit=1)
            if due["due"]:
                item = dict(due["due"][0])
                item["today_kind"] = "due_review"
                item["today_note"] = "📅 今日巩固 · 到期错题（艾宾浩斯）"
                return item
            hint = self._difficulty_hint(user_id)
            dmin, dmax = hint.get("min_difficulty"), hint.get("max_difficulty")
            try:
                rep = self.practice_report(user_id, teacher_id)
                # practice_report 的 weak 在顶层；learning_report 才包 weekly 层
                weak = rep.get("weak") or (rep.get("weekly") or {}).get("weak") or []
            except Exception:  # noqa: BLE001
                weak = []
            if weak:
                wpool = self._qbank_pool(teacher_id, category=random.choice(weak),
                                         min_difficulty=dmin, max_difficulty=dmax) \
                    if (dmin or dmax) else \
                    self._qbank_pool(teacher_id, category=random.choice(weak))
                if wpool:
                    q = random.choice(wpool)
                    resp = self._practice_payload(q)
                    resp["today_kind"] = "weak"
                    resp["today_note"] = f"🎯 薄弱专项 · {q.get('category') or ''} · {hint['note']}"
                    resp["difficulty_hint"] = hint
                    return resp
            # 薄弱池无题 → 落到普通抽题（resp 在下方返回点打标）

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
            # #13 难度预估：today 模式按建议难度档收敛候选（无题则放宽）
            hint = self._difficulty_hint(user_id) if mode == "today" else None
            if hint and (hint.get("min_difficulty") or hint.get("max_difficulty")):
                dcand = [q for q in candidates
                         if (hint.get("min_difficulty") or 1) <= (q.get("difficulty") or 3)
                         <= (hint.get("max_difficulty") or 5)]
                if dcand:
                    candidates = dcand
            q = random.choice(candidates)
            resp = self._practice_payload(q)
            if mode == "wrong":
                resp["mode_fallback"] = "no_wrong"  # 错题池空，已回落普通抽题
            if mode == "today":
                resp["today_kind"] = "fresh"
                resp["today_note"] = "✨ 智能新题 · " + hint["note"] if hint else "✨ 智能新题"
                if hint:
                    resp["difficulty_hint"] = hint
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
        q_analysis = ""
        if question_id:
            with self._connect() as conn:
                qrow = conn.execute(
                    "SELECT * FROM question_bank WHERE id=?", (question_id,)
                ).fetchone()
            if qrow:
                q = dict(qrow)
                qcat = q.get("category") or ""
                q_analysis = q.get("analysis") or ""
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
            # #4 学习闭环：判分即返回解析，学员不用跑去错题本才能看「为什么错」
            "analysis": q_analysis,
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


# 模块级单例
_study_store: Optional[StudyStore] = None


def get_study_store() -> StudyStore:
    global _study_store
    if _study_store is None:
        _study_store = StudyStore()
    return _study_store
