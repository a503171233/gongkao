# -*- coding: utf-8 -*-
"""AI 模型管理（#25 问题2）：模型注册表 + 全局默认设置。

- ai_models：候选模型注册表（后台"AI模型"页维护）。
- ai_settings：全局默认模型/温度/输出上限（key-value）。

有效模型解析优先级（llm 调用用）：
    call_llm(model=显式) > 老师.llm_model(问答) > ai_settings.default_model(平台AI) > .env LLM_MODEL

注册表为「管理 + 连通性测试」页面服务；平台级 AI（题库采集/抽取/知识建树）走
ai_settings.default_model 缺省（未配置时回退 .env / 老师配置，兼容现状）。
"""
import os
import sqlite3
import threading
import datetime
from pathlib import Path
from typing import Optional

from .config import Config

_lock = threading.RLock()
_store: Optional["AiModelStore"] = None


class AiModelStore:
    def __init__(self, db_path=None):
        default = Config.data_dir / "aimodels.db"
        self.db_path = Path(db_path or os.environ.get("AIMODELS_DB") or default)
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
            CREATE TABLE IF NOT EXISTS ai_models (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                model_id    TEXT NOT NULL,
                provider    TEXT NOT NULL DEFAULT 'anyyds',
                base_url    TEXT NOT NULL DEFAULT 'https://ai.anyyds.cn/v1',
                api_key     TEXT DEFAULT '',
                enabled     INTEGER NOT NULL DEFAULT 1,
                temperature REAL NOT NULL DEFAULT 0.3,
                max_tokens  INTEGER NOT NULL DEFAULT 2000,
                is_default  INTEGER NOT NULL DEFAULT 0,
                remark      TEXT DEFAULT '',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_aimodel_id ON ai_models(model_id);
            CREATE TABLE IF NOT EXISTS ai_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );
            """)
            # 旧库迁移：ai_models 缺 api_key 列时补上（idempotent）
            cols = {r[1] for r in conn.execute("PRAGMA table_info(ai_models)").fetchall()}
            if "api_key" not in cols:
                conn.execute("ALTER TABLE ai_models ADD COLUMN api_key TEXT DEFAULT ''")

    # ---------- 模型注册表 ----------
    @staticmethod
    def _mask_key(key: str) -> str:
        """API Key 脱敏：只保留前 4 位与末 4 位，中间打码；空值返回空串。"""
        key = (key or "").strip()
        if not key:
            return ""
        if len(key) <= 10:
            return key[:2] + "****" + key[-2:]
        return key[:4] + "****" + key[-4:]

    @staticmethod
    def _norm_base_url(base_url: str) -> str:
        """规范化 Base URL：去空白、去尾部 /models 与斜杠。
        OpenAI 客户端会在 base_url 后自动拼 /chat/completions，
        若入库的是拉列表端点 …/models，测速就会拼成 …/models/chat/completions 而 404/503。
        """
        b = (base_url or "").strip().rstrip("/")
        if b.endswith("/models"):
            b = b[: -len("/models")].rstrip("/")
        return b or "https://ai.anyyds.cn/v1"

    @staticmethod
    def _mask_model(m: dict) -> dict:
        d = dict(m)
        d["api_key"] = AiModelStore._mask_key(d.get("api_key", ""))
        d["enabled"] = bool(d.get("enabled"))
        d["is_default"] = bool(d.get("is_default"))
        return d

    def list_models(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM ai_models ORDER BY id").fetchall()
        return [self._mask_model(dict(r)) for r in rows]

    def get_model(self, model_id: str) -> dict | None:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM ai_models WHERE model_id=?", (model_id,)).fetchone()
        return dict(r) if r else None

    def create_model(self, name: str, model_id: str, provider: str = "anyyds",
                     base_url: str = "", api_key: str = "", enabled: bool = True,
                     temperature: float = 0.3, max_tokens: int = 2000,
                     remark: str = "") -> dict:
        base_url = self._norm_base_url(base_url)
        now = self._now()
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO ai_models(name, model_id, provider, base_url, api_key, enabled, "
                    "temperature, max_tokens, is_default, remark, created_at, updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,0,?,?,?)",
                    (name.strip(), model_id.strip(), provider.strip() or "anyyds",
                     base_url, (api_key or "").strip(), int(bool(enabled)),
                     float(temperature), int(max_tokens),
                     remark.strip(), now, now))
            except sqlite3.IntegrityError:
                raise ValueError(f"模型标识 {model_id} 已存在")
            # 同连接返回，规避 WAL 快照下新连接读不到刚提交行
            rid = cur.lastrowid
            row = conn.execute("SELECT * FROM ai_models WHERE id=?", (rid,)).fetchone()
            raw = dict(row) if row else self.get_model(model_id.strip())
        return self._mask_model(raw or {})

    def update_model(self, model_id: str, **fields) -> dict | None:
        allowed = {"name", "provider", "base_url", "api_key", "enabled",
                   "temperature", "max_tokens", "remark"}
        sets, vals = [], []
        if "name" in fields and fields["name"] is not None:
            sets.append("name=?"); vals.append(fields["name"].strip())
        if "provider" in fields and fields["provider"] is not None:
            sets.append("provider=?"); vals.append(fields["provider"].strip())
        if "base_url" in fields and fields["base_url"] is not None:
            sets.append("base_url=?"); vals.append(self._norm_base_url(fields["base_url"]))
        if "api_key" in fields and fields["api_key"] is not None:
            # PATCH 语义：空串 = 不修改（避免把脱敏回显 sk-**** 存回）；非空 = 替换。
            k = (fields["api_key"] or "").strip()
            if k and "****" not in k:
                sets.append("api_key=?"); vals.append(k)
        if "enabled" in fields:
            sets.append("enabled=?"); vals.append(int(bool(fields["enabled"])))
        if "temperature" in fields and fields["temperature"] is not None:
            sets.append("temperature=?"); vals.append(float(fields["temperature"]))
        if "max_tokens" in fields and fields["max_tokens"] is not None:
            sets.append("max_tokens=?"); vals.append(int(fields["max_tokens"]))
        if "remark" in fields and fields["remark"] is not None:
            sets.append("remark=?"); vals.append(fields["remark"].strip())
        if not sets:
            return self._mask_model(self.get_model(model_id) or {})
        sets.append("updated_at=?"); vals.append(self._now())
        vals.append(model_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE ai_models SET {', '.join(sets)} WHERE model_id=?", vals)
        return self._mask_model(self.get_model(model_id) or {})

    def delete_model(self, model_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM ai_models WHERE model_id=?", (model_id,))
        return cur.rowcount > 0

    def find_by_base_url(self, base_url: str) -> dict | None:
        """按 Base URL 查注册表里最早的一条（含完整 api_key，供采集通道复用密钥）。
        用于：选择已有平台时自动带上该平台保存的 API Key，避免重新手填。"""
        b = self._norm_base_url(base_url)
        if not b:
            return None
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM ai_models WHERE base_url=? "
                "ORDER BY is_default DESC, id LIMIT 1", (b,)).fetchone()
        return dict(r) if r else None

    def set_default(self, model_id: str) -> dict:
        m = self.get_model(model_id)
        if not m:
            raise ValueError(f"模型不存在: {model_id}")
        with self._connect() as conn:
            conn.execute("UPDATE ai_models SET is_default=0, updated_at=? WHERE is_default=1", (self._now(),))
            conn.execute("UPDATE ai_models SET is_default=1, updated_at=? WHERE model_id=?",
                         (self._now(), model_id))
        return self._mask_model(self.get_model(model_id) or {})

    # ---------- 全局设置 ----------
    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO ai_settings(key, value) VALUES(?,?)",
                         (key, value or ""))

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            r = conn.execute("SELECT value FROM ai_settings WHERE key=?", (key,)).fetchone()
        return (r["value"] if r else "") or default

    # ---------- 平台级 AI 有效配置（题库采集/抽取/知识建树等） ----------
    def effective_default(self) -> dict:
        """返回平台级 AI 实际使用的 {model_id, base_url, api_key, temperature, max_tokens}。
        优先级：ai_settings.default_model（若是已启用注册模型）> 全局默认 env > 空。
        api_key 供平台 AI（题库采集/抽取/知识建树）直连该模型通道使用；未配置时用全局 .env 密钥。"""
        dm = self.get_setting("default_model", "")
        if dm:
            m = self.get_model(dm)
            if m and m["enabled"]:
                return {"model_id": m["model_id"],
                        "base_url": self._norm_base_url(m["base_url"]),
                        "api_key": (m.get("api_key") or "").strip() or Config().api_key,
                        "temperature": m["temperature"], "max_tokens": m["max_tokens"]}
        cfg = Config()
        return {"model_id": cfg.llm_model,
                "base_url": self._norm_base_url(cfg.api_base_url),
                "api_key": cfg.api_key,
                "temperature": cfg.temperature, "max_tokens": 2000}

    @staticmethod
    def _now() -> str:
        return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 内置模型目录（OpenAI 兼容协议；兜底：远程拉取失败时展示） ----------
BUILTIN_MODELS = [
    {"model_id": "gpt-5.6-luna", "name": "Luna 主模型", "provider": "anyyds",
     "base_url": "https://ai.anyyds.cn/v1"},
    {"model_id": "gpt-4o-mini", "name": "GPT-4o mini", "provider": "openai",
     "base_url": "https://api.openai.com/v1"},
    {"model_id": "deepseek-chat", "name": "DeepSeek Chat", "provider": "deepseek",
     "base_url": "https://api.deepseek.com/v1"},
    {"model_id": "qwen-plus", "name": "通义千问 Plus", "provider": "aliyun",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    {"model_id": "glm-4-flash", "name": "智谱GLM-4-Flash", "provider": "zhipu",
     "base_url": "https://open.bigmodel.cn/api/paas/v4"},
    {"model_id": "moonshot-v1-8k", "name": "Moonshot (月之暗面)", "provider": "moonshot",
     "base_url": "https://api.moonshot.cn/v1"},
]


def builtin_catalog() -> list[dict]:
    """返回内置模型目录（含已注册状态）。"""
    store = get_ai_model_store()
    reg = {m["model_id"] for m in store.list_models()}
    out = []
    for m in BUILTIN_MODELS:
        d = dict(m)
        d["registered"] = m["model_id"] in reg
        out.append(d)
    return out


def list_remote_models(base_url: str = "", provider: str = "", api_key: str = "") -> dict:
    """按 OpenAI 兼容协议 GET {base_url}/models 拉取模型列表。

    返回: {ok: bool, models: [model_id...], error: str?, used_fallback: bool,
           base_url: <API 根地址（不含 /models），供一键登记复用>,
           key_source: 实际生效密钥来源}
    失败时自动回退内置目录（ok=False, used_fallback=True）。
    使用标准库，避免对 requests 的运行时依赖。

    密钥候选链（按序尝试）：显式传入 > 该平台注册表密钥 > 采集通道已存密钥 > 全局密钥。
    命中 401/403（密钥无效）自动换下一把候选重试；其他错误（网络/5xx/超时）
    换密钥无意义，直接失败兜底，避免无谓请求。
    """
    import json
    import urllib.error
    import urllib.request
    root = (base_url or "").strip().rstrip("/") or "https://ai.anyyds.cn/v1"
    # 去掉 /models 后缀再统一比较，保证注册表/通道匹配时口径一致
    if root.endswith("/models"):
        root = root[: -len("/models")].rstrip("/")
    url = root + "/models"
    # 组装候选密钥链（保序去重）
    cands: list[tuple[str, str]] = []  # [(source, key), ...]
    if (api_key or "").strip():
        cands.append(("explicit", api_key.strip()))
    try:
        reg = get_ai_model_store().find_by_base_url(root)
        rk = (reg.get("api_key") or "").strip() if reg else ""
        if rk:
            cands.append(("registry", rk))
    except Exception:  # noqa: BLE001  注册表异常不阻断
        pass
    try:
        from .autocollect import load_llm_channels
        for ch in load_llm_channels():
            ck = (ch.get("api_key") or "").strip()
            if (ch.get("base_url") or "").strip().rstrip("/") == root and ck \
                    and all(ck != k for _, k in cands):
                cands.append(("channel", ck))
    except Exception:  # noqa: BLE001  通道不可用不阻断
        pass
    gk = (Config().api_key or "").strip()
    if gk and all(gk != k for _, k in cands):
        cands.append(("global", gk))
    if not cands:
        cands.append(("none", ""))

    models_err = ""
    for src, key in cands:
        head = {"Accept": "application/json", "Content-Type": "application/json"}
        if key:
            head["Authorization"] = "Bearer " + key
        try:
            req = urllib.request.Request(url, headers=head)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            raw = data.get("data", data if isinstance(data, list) else [])
            ids = [str(it["id"]) for it in raw if isinstance(it, dict) and it.get("id")]
            if not ids:
                return {"ok": False, "models": [m["model_id"] for m in BUILTIN_MODELS],
                        "error": "接口未返回模型列表(data字段为空)", "used_fallback": True,
                        "base_url": root, "key_source": src}
            return {"ok": True, "models": ids, "error": "", "used_fallback": False,
                    "provider": provider, "base_url": root, "key_source": src}
        except urllib.error.HTTPError as e:
            # 401/403 = 该密钥被拒，若还有候选则换下一把；否则带着真实错误兜底
            if e.code in (401, 403):
                models_err = f"HTTP Error {e.code}: Unauthorized（密钥被拒，来源：{src}）"
                continue
            return {"ok": False, "models": [m["model_id"] for m in BUILTIN_MODELS],
                    "error": f"HTTPError: HTTP Error {e.code}", "used_fallback": True,
                    "base_url": root, "key_source": src}
        except Exception as e:  # noqa: BLE001 网络/协议失败 → 兜底内置目录
            return {"ok": False, "models": [m["model_id"] for m in BUILTIN_MODELS],
                    "error": f"{type(e).__name__}: {e}", "used_fallback": True,
                    "base_url": root, "key_source": src}
    return {"ok": False, "models": [m["model_id"] for m in BUILTIN_MODELS],
            "error": models_err or "所有候选密钥均被拒", "used_fallback": True,
            "base_url": root, "key_source": "none"}


def get_ai_model_store() -> AiModelStore:
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                _store = AiModelStore()
    return _store