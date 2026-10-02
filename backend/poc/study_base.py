# -*- coding: utf-8 -*-
"""C4 · 学员增值功能 — StudyStore 基础设施（DB 连接 / 建表）。"""
import os
import sqlite3
from pathlib import Path

from .config import Config


class StudyStoreBase:
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
            # 迁移：#22 收藏分组（favorites.group_name，默认空串=未分组）
            try:
                conn.execute("ALTER TABLE favorites ADD COLUMN group_name TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
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
