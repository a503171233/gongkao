# -*- coding: utf-8 -*-
"""配置加载：多老师注册表 + 全局 API/embedding 配置。
对应说明书 V3.0 §11 teachers 表：新增老师在此注册即可，无需改代码。
"""
import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 项目根 = gongkao/（本文件在 gongkao/backend/poc/ 下，向上 3 层）
ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"

# 依次尝试加载 .env：项目根(compose约定) → backend/.env → backend/.env.example
load_dotenv(ROOT / ".env")
load_dotenv(BACKEND / ".env")
load_dotenv(BACKEND / ".env.example")

DATA_DIR = ROOT / "data"


def get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# ---------- 多老师注册表：DB 运行时化（C2 改造） ----------
# 内置默认老师：DB 初始化时回退（缺省时自动 seed）
_BUILTIN_TEACHERS: dict[str, dict] = {
    "T001": {
        "teacher_id": "T001",
        "teacher_name": "星辰老师",
        "teacher_subject": "言语理解",
        "llm_model": "gpt-5.6-luna",
        "temperature": 0.3,
        "top_n": 6,
        "threshold": 0.5,
        "prompt_style": "classroom",
    },
    "T002": {
        "teacher_id": "T002",
        "teacher_name": "云舟老师",
        "teacher_subject": "逻辑判断",
        "llm_model": "gpt-5.6-luna",
        "temperature": 0.3,
        "top_n": 6,
        "threshold": 0.5,
        "prompt_style": "classroom",
    },
}


@dataclass
class TeacherCfg:
    """某位老师独立的运行配置（知识库/模型参数/风格参数）。"""
    teacher_id: str
    teacher_name: str
    teacher_subject: str
    llm_model: str
    temperature: float
    top_n: int
    threshold: float
    chunk_size: int = 600
    chunk_overlap: int = 120
    prompt_style: str = "classroom"   # A4 模板变体：classroom/concise/exam 等
    # 幻觉守卫老师级覆盖（B8 v2）：teachers.guard_config JSON 解析结果；
    # None/空 = 用学科预设+场景默认策略。字段与取值范围见 guard.POLICY_FIELDS
    guard_policy: dict | None = field(default=None, repr=False, compare=False)

    def collection(self) -> str:
        # 多老师隔离：一个老师一个 Chroma Collection（说明书 §6.3）
        return f"teacher_{self.teacher_id}"


class Config:
    # ---------- 全局：API / Embedding / 路径 ----------
    api_base_url = get("API_BASE_URL", "https://ai.anyyds.cn/v1")
    api_key = get("API_KEY", "")
    llm_model = get("LLM_MODEL", "gpt-5.6-luna")

    # ---------- LLM 调用（B7 重试降级） ----------
    # 单次 LLM 调用超时（秒）。LLM 实测最慢 121s，默认 180s 保证不误杀尾部响应。
    llm_timeout = float(get("LLM_TIMEOUT", "180"))
    # 网络类/5xx/超时失败最多重试次数（4xx/鉴权/参数错误不重试，见 llm.py）。0=不重试。
    llm_max_retries = int(get("LLM_MAX_RETRIES", "2"))
    # 备用模型列表（逗号分隔）：主模型重试耗尽后按序降级，每个仅尝试 1 次。
    # ⚠️ 需实测可用后再写入（当前中转站 21 模型仅 gpt-5.6-luna 稳定），
    #    未实测前保持空列表 = 不降级，主模型失败直接抛错。
    fallback_models = [m.strip() for m in get("FALLBACK_MODELS", "").split(",") if m.strip()]

    # embedding: local=fastembed(bge-small-zh, ONNX,推荐)；api=中转站；selftest=离线哈希(仅供自测)
    embed_provider = get("EMBED_PROVIDER", "local")
    embed_model = get("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")

    # 上传鉴权密钥（可选；设置后 /upload 必须携带 X-Upload-Secret 匹配，防公网滥用）
    upload_secret = get("UPLOAD_SECRET", "")
    # 开放 API 密钥（/api/v1/* 强制 X-API-Key；未配置时开放端点返回 503 未启用）
    openapi_key = get("OPENAPI_KEY", "")

    db_path = Path(get("DB_PATH", str(DATA_DIR / "vector")))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    # 原始课件目录（网站二上传/读取用）
    data_dir = Path(get("DATA_DIR", str(ROOT / "data")))
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    # 本地模型缓存目录（fastembed ONNX 模型）
    model_cache = Path(get("MODEL_CACHE", str(data_dir / "models")))
    if not model_cache.is_absolute():
        model_cache = ROOT / model_cache

    # ---------- 兼容旧 CLI/selftest：默认老师（TEACHER_ID 可覆盖） ----------
    teacher_id = get("TEACHER_ID", "T001")
    _t = _BUILTIN_TEACHERS.get(teacher_id, {})
    teacher_name = get("TEACHER_NAME", _t.get("teacher_name", "星辰老师"))
    teacher_subject = get("TEACHER_SUBJECT", _t.get("teacher_subject", "言语理解"))

    chunk_size = int(get("CHUNK_SIZE", "600"))
    chunk_overlap = int(get("CHUNK_OVERLAP", "120"))
    top_n = int(get("TOP_N", "6"))
    threshold = float(get("SIMILARITY_THRESHOLD", "0.5"))
    temperature = float(get("TEMPERATURE", "0.3"))

    # ---------- C2 老师注册表运行时化 ----------
    # DB 路径（与 auth.db 同源 data_dir）；内存缓存
    _teachers_lock = threading.RLock()
    _teachers_cache: dict[str, dict] = {}  # 运行时缓存，启用+禁用都包

    @classmethod
    def _teachers_db_path(cls) -> Path:
        """teacher 注册表 DB 路径 = data_dir/teachers.db（与 auth.db 同目录）。"""
        return cls.data_dir / "teachers.db"

    @classmethod
    def _teachers_connect(cls) -> sqlite3.Connection:
        p = cls._teachers_db_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn
    @classmethod
    def _init_teachers_schema(cls) -> None:
        """初始化 teachers 表 + 内置 seed。"""
        with cls._teachers_connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS teachers (
                teacher_id       TEXT PRIMARY KEY,
                teacher_name     TEXT NOT NULL,
                teacher_subject  TEXT NOT NULL,
                llm_model        TEXT NOT NULL DEFAULT 'gpt-5.6-luna',
                temperature      REAL NOT NULL DEFAULT 0.3,
                top_n            INTEGER NOT NULL DEFAULT 6,
                threshold        REAL NOT NULL DEFAULT 0.5,
                prompt_style     TEXT NOT NULL DEFAULT 'classroom',
                enabled          INTEGER NOT NULL DEFAULT 1,
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL
            );
            """)
            # 既有库平滑升级：guard_config（B8 v2 幻觉守卫老师级覆盖，JSON）
            try:
                conn.execute("ALTER TABLE teachers ADD COLUMN guard_config TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # 列已存在
            # 既有库平滑升级：课程分类/公司分类/排序权重（老师管理扩展）
            for ddl in (
                "ALTER TABLE teachers ADD COLUMN course_category TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE teachers ADD COLUMN company TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE teachers ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass  # 列已存在
            # 缺省 seed：内置 T001/T002（仅当表为空时）
            row = conn.execute("SELECT COUNT(*) AS n FROM teachers").fetchone()
            if row["n"] == 0:
                now = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                for tid, t in _BUILTIN_TEACHERS.items():
                    conn.execute(
                        "INSERT OR IGNORE INTO teachers(teacher_id, teacher_name, teacher_subject, "
                        "llm_model, temperature, top_n, threshold, prompt_style, enabled, created_at, updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (tid, t["teacher_name"], t["teacher_subject"],
                         t["llm_model"], t["temperature"], t["top_n"], t["threshold"],
                         t.get("prompt_style", "classroom"), 1, now, now),
                    )

    @staticmethod
    def _parse_guard_config(raw) -> dict | None:
        """guard_config JSON 列 → dict。空/非法 → None（回退学科预设，不炸主链路）。"""
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    @classmethod
    def reload_teachers(cls) -> None:
        """从 DB 加载到内存缓存。"""
        cls._init_teachers_schema()
        with cls._teachers_lock:
            with cls._teachers_connect() as conn:
                rows = conn.execute("SELECT * FROM teachers").fetchall()
            cls._teachers_cache = {
                r["teacher_id"]: {
                    "teacher_id": r["teacher_id"],
                    "teacher_name": r["teacher_name"],
                    "teacher_subject": r["teacher_subject"],
                    "llm_model": r["llm_model"],
                    "temperature": float(r["temperature"]),
                    "top_n": int(r["top_n"]),
                    "threshold": float(r["threshold"]),
                    "prompt_style": r["prompt_style"] if "prompt_style" in r.keys() else "classroom",
                    "enabled": bool(r["enabled"]),
                    "course_category": r["course_category"] if "course_category" in r.keys() else "",
                    "company": r["company"] if "company" in r.keys() else "",
                    "sort_order": int(r["sort_order"]) if "sort_order" in r.keys() and r["sort_order"] is not None else 0,
                    "guard_policy": cls._parse_guard_config(
                        r["guard_config"] if "guard_config" in r.keys() else ""),
                }
                for r in rows
            }

    def __init__(self):
        # 构造时保证 cache 加载（保证 B3/B4 在 Config() 实例化时即可用）
        if not Config._teachers_cache:
            Config.reload_teachers()

    # ---------- 多老师查询（签名与返回结构 100% 兼容） ----------
    def get_teacher(self, teacher_id: str) -> TeacherCfg:
        """按 ID 取老师独立配置；不存在抛 ValueError。
        说明：enabled=false 的老师也能取到（让历史会话可读、B3/B4 不崩）。
        """
        with Config._teachers_lock:
            t = Config._teachers_cache.get(teacher_id)
        if t is None:
            # 缓存 miss → 再查一次 DB（并发场景兜底）
            self.reload_teachers()
            with Config._teachers_lock:
                t = Config._teachers_cache.get(teacher_id)
        if t is None:
            raise ValueError(f"老师不存在: {teacher_id}")
        return TeacherCfg(
            teacher_id=teacher_id,
            teacher_name=t.get("teacher_name", teacher_id),
            teacher_subject=t.get("teacher_subject", ""),
            llm_model=t.get("llm_model", self.llm_model),
            temperature=float(t.get("temperature", self.temperature)),
            top_n=int(t.get("top_n", self.top_n)),
            threshold=float(t.get("threshold", self.threshold)),
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            prompt_style=t.get("prompt_style", "classroom"),
            guard_policy=t.get("guard_policy"),
        )

    def list_teachers(self) -> list[dict]:
        """前台用：仅返回 enabled=true 的老师（sort_order 升序，同序按 ID）。"""
        with Config._teachers_lock:
            items = [
                {
                    "teacher_id": tid,
                    "teacher_name": t.get("teacher_name", tid),
                    "teacher_subject": t.get("teacher_subject", ""),
                    "course_category": t.get("course_category", ""),
                    "company": t.get("company", ""),
                }
                for tid, t in sorted(
                    Config._teachers_cache.items(),
                    key=lambda kv: (kv[1].get("sort_order", 0), kv[0]),
                )
                if t.get("enabled", True)
            ]
        return items

    def list_teachers_all(self) -> list[dict]:
        """后台用：含 enabled 全量（sort_order 升序，同序按 ID）。"""
        with Config._teachers_lock:
            return [
                {
                    "teacher_id": tid,
                    "teacher_name": t.get("teacher_name", tid),
                    "teacher_subject": t.get("teacher_subject", ""),
                    "llm_model": t.get("llm_model"),
                    "temperature": t.get("temperature"),
                    "top_n": t.get("top_n"),
                    "threshold": t.get("threshold"),
                    "prompt_style": t.get("prompt_style", "classroom"),
                    "enabled": t.get("enabled", True),
                    "course_category": t.get("course_category", ""),
                    "company": t.get("company", ""),
                    "sort_order": t.get("sort_order", 0),
                    "guard_policy": t.get("guard_policy"),
                }
                for tid, t in sorted(
                    Config._teachers_cache.items(),
                    key=lambda kv: (kv[1].get("sort_order", 0), kv[0]),
                )
            ]

    def upload_dir(self, teacher_id: str) -> Path:
        return self.data_dir / "raw" / teacher_id

    def teacher_collection(self) -> str:
        # 兼容旧代码（默认老师）
        return f"teacher_{self.teacher_id}"