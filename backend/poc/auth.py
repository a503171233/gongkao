# -*- coding: utf-8 -*-
"""用户账号/会员额度（说明书 §6 用户体系）。
SQLite 零依赖（标准库 sqlite3 + hashlib + hmac + secrets + time）。

表 users:
    user_id       TEXT PRIMARY KEY  (生成 uuid hex)
    username      TEXT UNIQUE NOT NULL
    password_hash TEXT NOT NULL     (pbkdf2_hmac sha256, 16B salt, 120k 迭代)
    role          TEXT DEFAULT 'free'   ('free' | 'member')
    today_count   INTEGER DEFAULT 0     (今日已用提问次数)
    quota_date    TEXT DEFAULT ''       ('YYYY-MM-DD'，跨天自动重置)
    created_at    TEXT

表 oauth_links（第三方 OAuth 绑定）:
    provider     TEXT NOT NULL   ('gitee' 等)
    provider_uid TEXT NOT NULL   (第三方用户唯一 ID)
    user_id      TEXT NOT NULL   (绑定到本平台用户)
    created_at   TEXT
    主键 (provider, provider_uid)

额度（免费/会员每日提问上限）：
    FREE_DAILY   = 20
    MEMBER_DAILY = 200
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import Config

FREE_DAILY = 20
MEMBER_DAILY = 200
ADMIN_DAILY = -1   # 管理员不限额度（-1 = 无限）
_SALT_LEN = 16
_ITER = 120_000

# B4 匿名限额开关（环境变量可配置）：
#   ANON_DAILY_LIMIT  匿名用户每日提问上限（默认 20，与 FREE_DAILY 一致）
#   ANON_DAILY_ENABLED 匿名限额是否启用（默认 true；false = 匿名不限额度）
ANON_DAILY_LIMIT = int(os.environ.get("ANON_DAILY_LIMIT", "20").strip() or "20")
ANON_DAILY_ENABLED = os.environ.get("ANON_DAILY_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """返回 (salt_hex, password_hash_hex)。"""
    salt = salt or secrets.token_bytes(_SALT_LEN)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITER)
    return salt.hex(), dk.hex()


class AuthStore:
    def __init__(self, db_path: str | Path | None = None):
        # 默认与 chat.db 同源落卷（Config.data_dir 尊重 DATA_DIR=/data），AUTH_DB 可覆盖
        default = Config.data_dir / "auth.db"
        self.db_path = Path(db_path or os.environ.get("AUTH_DB") or default)
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
            CREATE TABLE IF NOT EXISTS users (
                user_id       TEXT PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'free',
                today_count   INTEGER NOT NULL DEFAULT 0,
                quota_date    TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_links (
                provider     TEXT NOT NULL,
                provider_uid TEXT NOT NULL,
                user_id      TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                PRIMARY KEY (provider, provider_uid)
            );
            """)
            # 数据库迁移：新增 member_expire_at 字段
            self._migrate_schema(conn)

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """检查并迁移 schema（向后兼容旧数据库）。"""
        try:
            conn.execute("ALTER TABLE users ADD COLUMN member_expire_at TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # 字段已存在
        try:
            conn.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")
        except sqlite3.OperationalError:
            pass  # 字段已存在
        try:
            conn.execute("ALTER TABLE users ADD COLUMN token_ver INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # 字段已存在
        # #15 密保问题找回：密保问题明文 + 答案哈希（低熵答案用 pbkdf2 加盐拉伸）
        try:
            conn.execute("ALTER TABLE users ADD COLUMN security_question TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # 字段已存在
        try:
            conn.execute("ALTER TABLE users ADD COLUMN security_answer TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # 字段已存在
        # #25 用户分群标签
        try:
            conn.execute("ALTER TABLE users ADD COLUMN tag TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # 字段已存在

    # ---------- OAuth 绑定 ----------
    def link_oauth(self, user_id: str, provider: str, provider_uid: str) -> None:
        """绑定用户到第三方账号（幂等）。"""
        if self.get_user(user_id) is None:
            raise ValueError("用户不存在")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO oauth_links(provider, provider_uid, user_id, created_at) "
                "VALUES(?,?,?,?)",
                (provider, str(provider_uid), user_id, _now()),
            )

    def find_user_by_oauth(self, provider: str, provider_uid: str) -> dict | None:
        """按第三方账号查绑定用户；未绑定返回 None。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id FROM oauth_links WHERE provider=? AND provider_uid=?",
                (provider, str(provider_uid)),
            ).fetchone()
        return self.get_user(row["user_id"]) if row else None

    # ---------- 注册 / 登录 ----------
    def register(self, username: str, password: str) -> str:
        """注册用户，返回 user_id。用户名重复抛 ValueError。"""
        username = (username or "").strip()
        if not username or not password:
            raise ValueError("用户名和密码不能为空")
        if len(password) < 6:
            raise ValueError("密码至少 6 位")
        # 防冒名注册 admin 账号
        admin_user = os.environ.get("ADMIN_USERNAME", "").strip()
        if admin_user and username.lower() == admin_user.lower():
            raise ValueError("该用户名不可注册")
        user_id = uuid.uuid4().hex[:16]
        salt_hex, hash_hex = _hash_password(password)
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO users(user_id, username, password_hash, role, created_at) "
                    "VALUES(?,?,?,?,?)",
                    (user_id, username, f"{salt_hex}${hash_hex}", "free", _now()),
                )
            except sqlite3.IntegrityError:
                raise ValueError("用户名已存在")
        return user_id

    def register_oauth(self, provider: str, provider_uid: str, name: str = "") -> str:
        """第三方 OAuth 首次登录：自动建号并绑定。
        用户名取 gitee 昵称@provider，冲突则加序号；密码随机（无法密码登录，仅 OAuth）。
        返回 user_id。
        """
        base = f"{name or provider_uid}@{provider}"[:40] or f"{provider}_{provider_uid}"
        username = base
        n = 0  # 从 0 开始：先试无后缀 base，冲突才依次 base1、base2…
        while self.get_user_by_username(username):
            n += 1
            username = f"{base}{n}"[:40]
        user_id = uuid.uuid4().hex[:16]
        random_pass = secrets.token_urlsafe(16)  # 不可用于登录，占位防空
        salt_hex, hash_hex = _hash_password(random_pass)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users(user_id, username, password_hash, role, created_at) "
                "VALUES(?,?,?,?,?)",
                (user_id, username, f"{salt_hex}${hash_hex}", "free", _now()),
            )
            conn.execute(
                "INSERT INTO oauth_links(provider, provider_uid, user_id, created_at) "
                "VALUES(?,?,?,?)",
                (provider, str(provider_uid), user_id, _now()),
            )
        return user_id

    def get_user_by_username(self, username: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def login(self, username: str, password: str) -> str:
        """登录成功返回 token（user_id 签名）。密码错抛 ValueError。"""
        username = (username or "").strip()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row is None:
            raise ValueError("用户名或密码错误")
        if (row["status"] or "active") != "active":
            raise ValueError("该账号已被禁用，请联系管理员")
        salt_hex, _, stored_hash = row["password_hash"].partition("$")
        _, calc_hash = _hash_password(password, bytes.fromhex(salt_hex))
        if not hmac.compare_digest(calc_hash, stored_hash):
            raise ValueError("用户名或密码错误")
        return self._sign(row["user_id"])

    def set_password(self, user_id: str, new_password: str) -> None:
        """重置用户密码（管理员/密码找回用）。new_password 长度校验由调用方保证。"""
        if not new_password or len(new_password) < 6:
            raise ValueError("新密码至少 6 位")
        salt_hex, hash_hex = _hash_password(new_password)
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash=? WHERE user_id=?",
                (f"{salt_hex}${hash_hex}", user_id),
            )

    # ---------- #15 密保问题找回 ----------
    PREMADE_QUESTIONS = [
        "你的小学班主任叫什么名字？",
        "你母亲的姓名是？",
        "你的出生地是？",
        "你最喜欢的老师的姓名是？",
        "你第一只宠物的名字是？",
    ]

    def _set_security(self, user_id: str, question: str, answer: str) -> None:
        """设置/覆盖密保（注册引导或登录后设置）。答案低熵，用独立盐 pbkdf2 拉伸。"""
        q = (question or "").strip()
        a = (answer or "").strip()
        if not q or not a:
            raise ValueError("密保问题和答案不能为空")
        if len(a) < 2:
            raise ValueError("密保答案至少 2 个字符")
        salt_hex, hash_hex = _hash_password(a)
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET security_question=?, security_answer=? WHERE user_id=?",
                (q, f"{salt_hex}${hash_hex}", user_id),
            )

    def get_security_question(self, username: str) -> dict | None:
        """按用户名取密保问题（找回第一步）。不泄露是否存在用户：统一返回结构。"""
        row = self.get_user_by_username(username)
        if row is None or not (row.get("security_question") or "").strip():
            return None
        return {"username": username, "security_question": row["security_question"]}

    def verify_security_answer(self, username: str, answer: str) -> bool:
        """校验密保答案（常量时间比较）。连续答错应由上层限流。"""
        row = self.get_user_by_username(username)
        if row is None:
            return False
        stored = (row.get("security_answer") or "")
        salt_hex, _, stored_hash = stored.partition("$")
        if not salt_hex or not stored_hash:
            return False
        try:
            _, calc_hash = _hash_password((answer or "").strip(),
                                          bytes.fromhex(salt_hex))
        except ValueError:
            return False
        return hmac.compare_digest(calc_hash, stored_hash)

    # ---------- #15 重置票据（答对密保后签发，10 分钟单次有效） ----------
    def issue_reset_ticket(self, username: str) -> str:
        """答对密保后签发重置票据。格式：reset.user_id.ts.nonce.sig（HMAC 独立密钥）。"""
        row = self.get_user_by_username(username)
        if row is None:
            raise ValueError("用户不存在")
        nonce = secrets.token_hex(4)
        payload = f"reset.{row['user_id']}.{int(time.time())}.{nonce}"
        sig = hmac.new(b"gk-reset", payload.encode(), hashlib.sha256).hexdigest()[:16]
        return f"{payload}.{sig}"

    def consume_reset_ticket(self, ticket: str) -> str | None:
        """校验重置票据 → 返回 user_id；无效/过期返回 None。10 分钟单次有效，
        消费后票据的 user_id 无权再次使用（无状态：靠短时效 + 独立密钥约束）。"""
        if not ticket:
            return None
        parts = ticket.split(".")
        if len(parts) != 5 or parts[0] != "reset":
            return None
        user_id, ts, nonce, sig = parts[1], parts[2], parts[3], parts[4]
        payload = ".".join(parts[:4])
        expect = hmac.new(b"gk-reset", payload.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expect):
            return None
        try:
            if abs(int(time.time()) - int(ts)) > 600:  # 10 分钟
                return None
        except ValueError:
            return None
        return user_id

    # ---------- token ----------
    def _sign_key(self, ver: int) -> bytes:
        """签名密钥：ver=0 沿用历史全局密钥 b"gk-auth"（与存量 token 完全兼容）；
        ver≥1 则掺入版本号，使 logout-all（ver+1）后旧 token 全部失效。"""
        return b"gk-auth" if ver <= 0 else f"gk-auth:{ver}".encode("utf-8")

    def _token_ver(self, user_id: str) -> int:
        """读取用户 token 版本号（无记录/缺列时回退 0，平滑兼容旧库）。"""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT token_ver FROM users WHERE user_id=?", (user_id,)).fetchone()
            return int(row["token_ver"]) if row and row["token_ver"] is not None else 0
        except sqlite3.OperationalError:
            return 0

    def _sign(self, user_id: str, ver: int | None = None) -> str:
        """签发 token。ver=0 → 4 段（user.ts.nonce.sig，与存量兼容）；
        ver≥1 → 5 段（user.ts.nonce.ver.sig，用于 logout-all 版本化）。
        - 非确定性签名：同秒内多次签发得到不同 token（否则登出后同秒再登录
          会拿到同一个已吊销 token，导致登录后立即失效——曾踩坑）。
        - ver 缺省则读库当前 token_ver。"""
        if ver is None:
            ver = self._token_ver(user_id)
        nonce = secrets.token_hex(4)
        if ver >= 1:
            payload = f"{user_id}.{int(time.time())}.{nonce}.{ver}"
        else:
            payload = f"{user_id}.{int(time.time())}.{nonce}"
        sig = hmac.new(self._sign_key(ver), payload.encode(), hashlib.sha256).hexdigest()[:16]
        return f"{payload}.{sig}"

    def revoke_all_tokens(self, user_id: str) -> int:
        """全量吊销某用户所有已签发 token：token_ver+1。
        旧 token 因签名密钥版本不符在 user_from_token 校验失败，全部失效；
        重新登录后签发新版本 token。返回新版本号。"""
        ver = self._token_ver(user_id) + 1
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET token_ver=? WHERE user_id=?", (ver, user_id))
        return ver

    # ---------- token 吊销（C5 安全加固：登出/泄漏吊销，持久化落 /data） ----------
    def _revoked_path(self) -> Path:
        """吊销列表文件路径：与 auth.db 同目录（/data 挂载卷，重启不丢）。"""
        return self.db_path.parent / "revoked_tokens.txt"

    def revoke_token(self, token: str) -> None:
        """吊销指定 token（写入持久化黑名单）。幂等。附带惰性清理过期记录。"""
        if not token:
            return
        p = self._revoked_path()
        existing: set[str] = set()
        if p.exists():
            existing = {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}
        # 惰性清理：token 本身 7 天过期，超过 7 天的吊销记录无保留价值（避免无限增长）
        cutoff = int(time.time()) - 7 * 86400
        keep: set[str] = set()
        for t in existing:
            parts = t.split(".")
            if len(parts) in (4, 5):  # 兼容 4 段（user.ts.nonce.sig）与 5 段（含 ver）token
                try:
                    if int(parts[1]) >= cutoff:
                        keep.add(t)
                    continue
                except ValueError:
                    pass
            keep.add(t)  # 格式异常保守保留
        keep.add(token)
        p.write_text("\n".join(sorted(keep)) + "\n", encoding="utf-8")

    def _is_revoked(self, token: str) -> bool:
        """token 是否在黑名单。黑名单文件不存在 → False。"""
        p = self._revoked_path()
        if not p.exists():
            return False
        # 逐行查找（文件通常很小，直接读入）
        with p.open(encoding="utf-8") as f:
            for ln in f:
                if ln.strip() == token:
                    return True
        return False

    def user_from_token(self, token: str) -> dict | None:
        """解析 token → 用户 dict；无效/已吊销返回 None。兼容旧 3 段 token。
        - 签名密钥按「签发时的版本」取：解析 token 内嵌的 ver 段（5 段格式）
          或回退历史全局密钥（4 段/3 段存量 token，ver=0）。
        - logout-all 使 token_ver+1 后，旧 token 因 ver 落后于当前版本而失效。"""
        if not token:
            return None
        parts = token.split(".")
        if len(parts) not in (4, 5):
            return None
        user_id, ts = parts[0], parts[1]
        ver = 0
        if len(parts) == 5:
            # 新格式：user.ts.nonce.ver.sig
            try:
                ver = int(parts[3])
            except ValueError:
                return None
            payload = ".".join(parts[:4])
        else:
            # 存量 4 段：user.ts.nonce.sig（ver=0）
            payload = ".".join(parts[:-1])
        sig = parts[-1]
        expect = hmac.new(self._sign_key(ver), payload.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expect):
            return None
        # token 7 天有效
        if abs(int(time.time()) - int(ts)) > 7 * 86400:
            return None
        # C5：已吊销 token 直接失效（登出后不可再用）
        if self._is_revoked(token):
            return None
        # logout-all 版本化：token 版本落后于当前用户版本 → 已全量吊销
        try:
            if ver != self._token_ver(user_id):
                return None
        except sqlite3.OperationalError:
            pass  # 旧库无 token_ver 列，跳过版本校验（向后兼容）
        return self.get_user(user_id)

    # ---------- 查询 / 额度 ----------
    def get_user(self, user_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return None
        u = dict(row)
        # 惰性到期判定：read-through，避免每次写库
        expired = self._is_member_expired(conn, u)
        if expired and u.get("role") == "member":
            u["role"] = "free"
            # 注意：这里在 with 块外执行 UPDATE，需要重新获取连接
            with self._connect() as conn2:
                conn2.execute("UPDATE users SET role='free' WHERE user_id=?", (user_id,))
        return u

    def _is_member_expired(self, conn: sqlite3.Connection, user: dict) -> bool:
        """
        判定会员是否已到期（惰性，不阻塞写操作）。
        返回 True 表示已过期或不是会员。
        """
        if user.get("role") != "member":
            return False
        expire_at = user.get("member_expire_at", "")
        if not expire_at:
            # 没有到期时间视为已过期（保护性回退）
            return True
        try:
            # 支持 ISO 格式（YYYY-MM-DDTHH:MM:SSZ 或 YYYY-MM-DD）
            from datetime import datetime as _dt
            if isinstance(expire_at, str):
                expire_at = expire_at.replace("Z", "+00:00")
                dt = _dt.fromisoformat(expire_at)
            else:
                dt = expire_at
            now = _dt.now(timezone.utc).replace(tzinfo=None) if hasattr(dt, 'tzinfo') and dt.tzinfo else _dt.utcnow()
            # 关键修复：比较时间，而非返回 naive datetime
            dt_naive = dt.replace(tzinfo=None) if hasattr(dt, 'tzinfo') and dt.tzinfo else dt
            return dt_naive < now
        except (ValueError, TypeError):
            # 格式异常视为已过期
            return True

    def set_role(self, user_id: str, role: str) -> None:
        if role not in ("free", "member", "admin"):
            raise ValueError("角色非法")
        with self._connect() as conn:
            if role == "member":
                # 设置会员时写入到期时间（默认30天，可由调用方覆盖）
                expire_at = self._calculate_expire_at(conn, user_id, 30)
                conn.execute(
                    "UPDATE users SET role=?, member_expire_at=? WHERE user_id=?",
                    (role, expire_at, user_id),
                )
            else:
                # free / admin 都清空到期时间（admin 无到期概念）
                conn.execute(
                    "UPDATE users SET role=?, member_expire_at='' WHERE user_id=?",
                    (role, user_id),
                )

    def ensure_admin(self, username: str | None = None, password: str | None = None) -> str | None:
        """
        启动时保证至少有一个 admin（环境变量 ADMIN_USERNAME 引导）。
        若 username/password 为空，读取环境变量；若仍为空则跳过。
        返回 admin user_id；跳过返回 None。
        """
        username = (username or os.environ.get("ADMIN_USERNAME", "")).strip()
        password = password or os.environ.get("ADMIN_PASSWORD", "")
        if not username or not password:
            return None
        existing = self.get_user_by_username(username)
        if existing:
            # 升级/保持 admin 角色
            if existing.get("role") != "admin":
                self.set_role(existing["user_id"], "admin")
            return existing["user_id"]
        # 创建 admin
        salt_hex, hash_hex = _hash_password(password)
        user_id = uuid.uuid4().hex[:16]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users(user_id, username, password_hash, role, created_at) "
                "VALUES(?,?,?,?,?)",
                (user_id, username, f"{salt_hex}${hash_hex}", "admin", _now()),
            )
        return user_id

    def _calculate_expire_at(self, conn: sqlite3.Connection, user_id: str, duration_days: int) -> str:
        """计算会员到期时间（基于当前时间 + duration_days）。"""
        from datetime import timedelta as _td
        from datetime import datetime as _dt
        expire = _dt.utcnow() + _td(days=duration_days)
        return expire.strftime("%Y-%m-%dT%H:%M:%SZ")

    def activate_membership(self, user_id: str, duration_days: int = 30) -> dict:
        """
        开通会员（供订单支付/充值码激活后调用）。
        返回 {user_id, role, member_expire_at, daily_limit}。
        """
        expire_at = self._calculate_expire_at(None, user_id, duration_days)
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET role='member', member_expire_at=? WHERE user_id=?",
                (expire_at, user_id),
            )
        # 重新读取并返回
        user = self.get_user(user_id)
        return {
            "user_id": user_id,
            "role": user["role"],
            "member_expire_at": user.get("member_expire_at", ""),
            "daily_limit": MEMBER_DAILY,
        }

    def get_membership_info(self, user_id: str) -> dict:
        """返回会员状态信息（含到期时间）。"""
        user = self.get_user(user_id)
        if not user or user.get("role") != "member":
            return {
                "user_id": user_id,
                "role": "free",
                "is_member": False,
                "member_expire_at": None,
                "days_remaining": 0,
            }
        expire_at = user.get("member_expire_at", "")
        days_remaining = 0
        if expire_at:
            from datetime import datetime as _dt
            try:
                exp = _dt.strptime(expire_at[:19], "%Y-%m-%dT%H:%M:%S")
                days_remaining = max(0, (_dt.utcnow() - exp).days * -1)
            except (ValueError, TypeError):
                pass
        return {
            "user_id": user_id,
            "role": "member",
            "is_member": True,
            "member_expire_at": expire_at,
            "days_remaining": days_remaining,
            "daily_limit": MEMBER_DAILY,
        }

    def _ensure_today(self, conn: sqlite3.Connection, user_id: str, role: str) -> None:
        """若 quota_date 非今日 → 重置今日计数。"""
        row = conn.execute("SELECT quota_date FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row is None or row["quota_date"] != _today():
            conn.execute(
                "UPDATE users SET quota_date=?, today_count=0 WHERE user_id=?",
                (_today(), user_id),
            )

    def check_quota(self, user_id: str, role: str | None = None) -> bool:
        """今日额度是否未超限。admin 永远返回 True（不限额度）。"""
        user = self.get_user(user_id)
        if not user:
            return False
        actual_role = role or user.get("role", "free")
        if actual_role == "admin":
            return True
        # B4：匿名用户且匿名限额关闭 → 不限额
        if user_id == "anonymous" and not ANON_DAILY_ENABLED:
            return True
        daily = MEMBER_DAILY if actual_role == "member" else FREE_DAILY
        # B4：匿名限额可独立配置
        if user_id == "anonymous":
            daily = ANON_DAILY_LIMIT
        with self._connect() as conn:
            self._ensure_today(conn, user_id, actual_role)
            row = conn.execute(
                "SELECT today_count FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            return bool(row) and row["today_count"] < daily

    def quota_left(self, user_id: str, role: str | None = None) -> int:
        user = self.get_user(user_id)
        if not user:
            return 0
        actual_role = role or user.get("role", "free")
        if actual_role == "admin":
            return -1   # -1 = 无限（前端可据此显示"无限"）
        # B4：匿名限额关闭 → 返回 -1（无限）
        if user_id == "anonymous" and not ANON_DAILY_ENABLED:
            return -1
        daily = MEMBER_DAILY if actual_role == "member" else FREE_DAILY
        if user_id == "anonymous":
            daily = ANON_DAILY_LIMIT
        with self._connect() as conn:
            self._ensure_today(conn, user_id, actual_role)
            row = conn.execute(
                "SELECT today_count FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            return max(0, daily - (row["today_count"] if row else 0))

    def consume(self, user_id: str) -> None:
        """消耗一次提问额度（计数+1）。B4：匿名限额关闭时不计数。"""
        if user_id == "anonymous" and not ANON_DAILY_ENABLED:
            return
        with self._connect() as conn:
            self._ensure_today(conn, user_id, "free")
            conn.execute(
                "UPDATE users SET today_count=today_count+1 WHERE user_id=?", (user_id,))
