# -*- coding: utf-8 -*-
"""学习激励体系（incentive.db）：积分 / 等级 / 成就 / 连续学习。

设计（#18 P1）：
  - 独立 incentive.db（SQLite 标准库），四张表：
      config        单行配置（enabled + 各事件积分，管理员可调）
      user_points   用户积分/等级/连续天数/累计统计（user_id 主键）
      point_ledger  积分流水（append-only，审计/前端最近动态）
      achievements  已获得成就（user_id+code 联合主键，幂等）
  - 事件源：practice_logs（答题量/正确率）、mistakes（错题攻克）、
    review_schedule（复习巩固）——均在 study.db。api.submit_practice 提交
    成功后调用 record_practice() 增量记账；管理端 recompute 全量重建保证
    与既有历史数据一致（幂等）。
  - 时间口径：所有日期/时间戳从 practice_logs.created_at 推导，重放历史
    记录时连续学习/每日首答仍能忠实还原。
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .config import Config


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date_of(iso: str) -> str:
    """ISO 时间戳 → 本地 YYYY-MM-DD（直接取前 10 位，UTC 存储即 UTC 日）。"""
    return (iso or "")[:10]


def _yesterday(day: str) -> str:
    try:
        base = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        base = datetime.now(timezone.utc)
    return (base - timedelta(days=1)).strftime("%Y-%m-%d")


DEFAULT_EVENT_POINTS = {
    "answer": 2,          # 每次答题
    "correct": 3,         # 答对（score>0）额外奖励
    "daily_first": 5,     # 每日首次答题
    "review_item": 5,     # 完成一轮复习巩固（排程题答对）
    "mistake_clear": 8,   # 攻克一道错题（首次答对原错题）
    "streak_3": 10,       # 连续学习 3 天
    "streak_7": 20,       # 连续学习 7 天
    "streak_30": 50,      # 连续学习 30 天
}

# 等级段：[达到该积分, 等级名]
LEVELS = [
    (0, "青铜学徒"),
    (100, "白银学员"),
    (300, "黄金选手"),
    (700, "铂金学霸"),
    (1500, "钻石大神"),
    (3000, "王者学神"),
]

# 成就目录：code → 标题/图标/描述（检查逻辑见 _check_achievements）
ACHIEVEMENTS = {
    "first_practice":  {"title": "初出茅庐", "icon": "🚀", "desc": "完成第一次练习"},
    "practice_50":     {"title": "崭露头角", "icon": "📈", "desc": "累计答题 50 次"},
    "practice_200":    {"title": "题海达人", "icon": "🏆", "desc": "累计答题 200 次"},
    "practice_500":    {"title": "题海战神", "icon": "👑", "desc": "累计答题 500 次"},
    "accuracy_80":     {"title": "精准学霸", "icon": "🎯", "desc": "客观题正确率 ≥ 80%（≥20 题）"},
    "streak_3":        {"title": "三日之约", "icon": "🔥", "desc": "连续学习 3 天"},
    "streak_7":        {"title": "七日养成", "icon": "⚡", "desc": "连续学习 7 天"},
    "streak_30":       {"title": "持之以恒", "icon": "🌟", "desc": "连续学习 30 天"},
    "mistake_10":      {"title": "错题克星", "icon": "🛡️", "desc": "攻克错题 10 道"},
    "category_master": {"title": "分类大师", "icon": "🧠", "desc": "任一分类答题 ≥30 且正确率 ≥70%"},
}


class IncentiveStore:
    def __init__(self, db_path: str | Path | None = None):
        default = Config.data_dir / "incentive.db"
        self.db_path = Path(db_path or default)
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
            CREATE TABLE IF NOT EXISTS config (
                id           INTEGER PRIMARY KEY CHECK (id=1),
                enabled      INTEGER NOT NULL DEFAULT 1,
                event_points TEXT NOT NULL DEFAULT '{}',
                updated_at   TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_points (
                user_id          TEXT PRIMARY KEY,
                points           INTEGER NOT NULL DEFAULT 0,
                level            INTEGER NOT NULL DEFAULT 1,
                streak_days      INTEGER NOT NULL DEFAULT 0,
                last_active_date TEXT NOT NULL DEFAULT '',
                total_answers    INTEGER NOT NULL DEFAULT 0,
                correct_count    INTEGER NOT NULL DEFAULT 0,
                solved_mistakes  INTEGER NOT NULL DEFAULT 0,
                updated_at       TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS point_ledger (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                event      TEXT NOT NULL,
                points     INTEGER NOT NULL DEFAULT 0,
                note       TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ledger_user ON point_ledger(user_id, id);
            CREATE TABLE IF NOT EXISTS achievements (
                user_id     TEXT NOT NULL,
                code        TEXT NOT NULL,
                achieved_at TEXT NOT NULL,
                PRIMARY KEY (user_id, code)
            );
            """)
            if not conn.execute("SELECT 1 FROM config WHERE id=1").fetchone():
                conn.execute(
                    "INSERT INTO config(id, enabled, event_points, updated_at) VALUES (1, 1, ?, ?)",
                    (json.dumps(DEFAULT_EVENT_POINTS, ensure_ascii=False), _now_iso()))

    # ==================== 配置 ====================
    def get_config(self) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM config WHERE id=1").fetchone()
        events: dict = {}
        if row:
            try:
                events = json.loads(row["event_points"] or "{}")
            except (ValueError, TypeError):
                events = {}
        merged = dict(DEFAULT_EVENT_POINTS)
        merged.update({k: int(v) for k, v in events.items()
                       if isinstance(v, (int, float)) and 0 <= v <= 1000})
        return {
            "enabled": bool(row and row["enabled"]),
            "events": merged,
            "updated_at": row["updated_at"] if row else "",
        }

    def set_config(self, enabled: bool, event_points: dict | None = None) -> dict:
        events = dict(DEFAULT_EVENT_POINTS)
        if isinstance(event_points, dict):
            for k, v in event_points.items():
                if k in DEFAULT_EVENT_POINTS and isinstance(v, (int, float)) and 0 <= v <= 1000:
                    events[k] = int(v)
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO config(id, enabled, event_points, updated_at) VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled, "
                "event_points=excluded.event_points, updated_at=excluded.updated_at",
                (1 if enabled else 0, json.dumps(events, ensure_ascii=False), now))
        return self.get_config()

    # ==================== 等级 ====================
    @staticmethod
    def _level_for(points: int) -> tuple[int, str, int | None, str | None]:
        """返回 (当前等级, 当前名称, 下一级门槛积分, 下一级名称)。"""
        cur_lv, cur_name = 1, LEVELS[0][1]
        next_th, next_name = None, None
        for i, (th, name) in enumerate(LEVELS):
            if points >= th:
                cur_lv, cur_name = i + 1, name
            else:
                next_th, next_name = th, name
                break
        return cur_lv, cur_name, next_th, next_name

    # ==================== 流水 ====================
    @staticmethod
    def _ledger_has(conn, user_id: str, event: str, note_sub: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM point_ledger WHERE user_id=? AND event=? AND note LIKE ? LIMIT 1",
            (user_id, event, "%" + note_sub + "%")).fetchone()
        return row is not None

    @staticmethod
    def _award(conn, user_id: str, event: str, points: int,
               note: str = "", now: str | None = None) -> None:
        now = now or _now_iso()
        if points <= 0:
            return
        conn.execute(
            "INSERT INTO point_ledger(user_id, event, points, note, created_at) "
            "VALUES (?, ?, ?, ?, ?)", (user_id, event, points, note, now))
        conn.execute(
            "INSERT INTO user_points(user_id, points, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET points=points+excluded.points, "
            "updated_at=excluded.updated_at", (user_id, points, now))

    # ==================== 增量记账（练习提交后） ====================
    def record_practice(self, user_id: str, log: dict, study_store) -> dict:
        """练习后增量记账。log 为 study.submit_practice 返回值（含 score/question_id/created_at）。
        study_store：StudyStore 实例，用于查询错题本/复习排程/分类统计。
        按 log.created_at 推导日期 → 重放历史时连续/首答仍忠实。"""
        cfg = self.get_config()
        if not cfg["enabled"]:
            return {"applied": False, "events": [], "points_gained": 0,
                    "streak_days": 0}
        ev = cfg["events"]
        qid = log.get("question_id")
        score = log.get("score")
        correct = isinstance(score, (int, float)) and score > 0
        day = _date_of(log.get("created_at") or "")
        if not day:
            day = _date_of(_now_iso())
        now = log.get("created_at") or _now_iso()
        events: list[dict] = []
        gained = 0

        def add(event: str, points: int, note: str = "") -> None:
            nonlocal gained
            if points > 0:
                events.append({"event": event, "points": points, "note": note})
                gained += points

        with self._connect() as conn:
            row = conn.execute(
                "SELECT streak_days, last_active_date FROM user_points WHERE user_id=?",
                (user_id,)).fetchone()
            last_date = row["last_active_date"] if row else ""
            streak = int(row["streak_days"]) if row else 0
            if last_date == day:
                pass
            elif last_date == _yesterday(day):
                streak += 1
            else:
                streak = 1

            add("answer", ev["answer"], "完成一次练习")
            if last_date != day:
                add("daily_first", ev["daily_first"], "今日首次练习")
            if correct:
                add("correct", ev["correct"], "客观题答对")
            # 复习巩固：排程中的题答对 → 推进复习一轮
            if qid and correct and study_store.review_stage_of(user_id, qid) is not None:
                add("review_item", ev["review_item"], f"完成一轮复习 qid={qid}")
            # 错题攻克：题在错题本且此前未因攻克该题得过分（一次）
            if qid and correct and study_store.is_mistake(user_id, qid):
                if not self._ledger_has(conn, user_id, "mistake_clear", f"qid={qid}"):
                    add("mistake_clear", ev["mistake_clear"], f"攻克错题 qid={qid}")
            # 连续天数里程碑（终身一次，note 内嵌 machine 片段防重复发放）
            if streak in (3, 7, 30):
                key = f"streak_{streak}"
                if not self._ledger_has(conn, user_id, key, f"streak={streak}"):
                    add(key, ev[key], f"streak={streak} 连续学习 {streak} 天")

            for e in events:
                self._award(conn, user_id, e["event"], e["points"], e["note"], now)

            conn.execute(
                """INSERT INTO user_points(user_id, points, level, streak_days, last_active_date,
                   total_answers, correct_count, updated_at)
                   VALUES (?, 0, 1, ?, ?, 1, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     points=points+excluded.points,
                     level=excluded.level,
                     streak_days=excluded.streak_days,
                     last_active_date=excluded.last_active_date,
                     total_answers=total_answers+excluded.total_answers,
                     correct_count=correct_count+excluded.correct_count,
                     updated_at=excluded.updated_at""",
                (user_id, streak, day, 1 if correct else 0, now))
            p = conn.execute(
                "SELECT points FROM user_points WHERE user_id=?", (user_id,)).fetchone()
            lv, _, _, _ = self._level_for(int(p["points"]))
            conn.execute("UPDATE user_points SET level=? WHERE user_id=?",
                         (lv, user_id))
            if any(x["event"] == "mistake_clear" for x in events):
                conn.execute(
                    "UPDATE user_points SET solved_mistakes=solved_mistakes+1 "
                    "WHERE user_id=?", (user_id,))
        self._check_achievements(user_id, study_store)
        return {"applied": True, "events": events, "points_gained": gained,
                "streak_days": streak}

    # ==================== 成就 ====================
    def _check_achievements(self, user_id: str, study_store) -> list[str]:
        """按当前统计评估并颁发成就（幂等）。返回本次新获得 code 列表。"""
        with self._connect() as conn:
            up = conn.execute("SELECT * FROM user_points WHERE user_id=?",
                              (user_id,)).fetchone()
            got = {r["code"]: r["achieved_at"] for r in conn.execute(
                "SELECT code, achieved_at FROM achievements WHERE user_id=?",
                (user_id,)).fetchall()}
        if not up:
            return []
        up = dict(up)
        stats = study_store.practice_stats(user_id)
        acc = stats.get("accuracy")
        graded = stats.get("graded") or 0
        total = int(up.get("total_answers") or stats.get("total") or 0)
        category_ok = False
        try:
            report = study_store.practice_report(user_id)
            for c in report.get("by_category", []):
                if c.get("total", 0) >= 30 and c.get("accuracy", 0) >= 0.7:
                    category_ok = True
                    break
        except Exception:
            category_ok = False
        checks = {
            "first_practice": total >= 1,
            "practice_50": total >= 50,
            "practice_200": total >= 200,
            "practice_500": total >= 500,
            "accuracy_80": graded >= 20 and acc is not None and acc >= 0.8,
            "streak_3": int(up.get("streak_days") or 0) >= 3,
            "streak_7": int(up.get("streak_days") or 0) >= 7,
            "streak_30": int(up.get("streak_days") or 0) >= 30,
            "mistake_10": int(up.get("solved_mistakes") or 0) >= 10,
            "category_master": category_ok,
        }
        awarded: list[str] = []
        now = _now_iso()
        with self._connect() as conn:
            for code, ok in checks.items():
                if ok and code not in got:
                    conn.execute(
                        "INSERT OR IGNORE INTO achievements(user_id, code, achieved_at) "
                        "VALUES (?, ?, ?)", (user_id, code, now))
                    awarded.append(code)
        return awarded

    # ==================== 全量重算（幂等重建） ====================
    def reset_user(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM point_ledger WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM achievements WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM user_points WHERE user_id=?", (user_id,))

    def recompute_user(self, user_id: str, logs: list[dict], study_store) -> dict:
        """清空该用户数据后，按 created_at 升序重放全部练习记录。"""
        self.reset_user(user_id)
        if not logs:
            return {"user_id": user_id, "points": 0, "events": 0}
        for log in sorted(logs, key=lambda x: x.get("created_at") or ""):
            try:
                self.record_practice(user_id, log, study_store)
            except Exception:
                continue
        with self._connect() as conn:
            row = conn.execute(
                "SELECT points FROM user_points WHERE user_id=?", (user_id,)).fetchone()
        return {"user_id": user_id,
                "points": int(row["points"]) if row else 0,
                "events": len(logs)}

    # ==================== 查询 ====================
    def summary(self, user_id: str, study_store) -> dict:
        """我的激励面板：积分/等级/连续/统计/成就/最近流水。"""
        cfg = self.get_config()
        with self._connect() as conn:
            up = conn.execute("SELECT * FROM user_points WHERE user_id=?",
                              (user_id,)).fetchone()
            got = {r["code"]: r["achieved_at"] for r in conn.execute(
                "SELECT code, achieved_at FROM achievements WHERE user_id=?",
                (user_id,)).fetchall()}
            ledger = [dict(r) for r in conn.execute(
                "SELECT id, event, points, note, created_at FROM point_ledger "
                "WHERE user_id=? ORDER BY id DESC LIMIT 20", (user_id,)).fetchall()]
        up = dict(up) if up else {}
        points = int(up.get("points") or 0)
        level, level_name, next_th, next_name = self._level_for(points)
        stats = study_store.practice_stats(user_id)
        stats["solved_mistakes"] = int(up.get("solved_mistakes") or 0)
        achievements = []
        for code, meta in ACHIEVEMENTS.items():
            achievements.append({
                "code": code, "title": meta["title"], "icon": meta["icon"],
                "desc": meta["desc"], "achieved_at": got.get(code) or None,
            })
        return {
            "enabled": cfg["enabled"],
            "user_id": user_id,
            "points": points,
            "level": level,
            "level_name": level_name,
            "next_level": next_th,
            "next_level_name": next_name,
            "streak_days": int(up.get("streak_days") or 0),
            "last_active_date": up.get("last_active_date") or None,
            "stats": stats,
            "achievements": achievements,
            "ledger": ledger,
        }

    def board(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id, points, level, streak_days, total_answers, "
                "correct_count, solved_mistakes, updated_at FROM user_points "
                "ORDER BY points DESC, updated_at ASC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def admin_stats(self) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(points),0) pts, "
                "COALESCE(AVG(points),0) avg_pts FROM user_points").fetchone()
            total_ach = conn.execute(
                "SELECT COUNT(*) n FROM achievements").fetchone()["n"]
            top = conn.execute(
                "SELECT user_id, points, level, streak_days, total_answers "
                "FROM user_points ORDER BY points DESC LIMIT 5").fetchall()
        return {
            "users_with_points": row["n"],
            "total_points": row["pts"],
            "avg_points": round(row["avg_pts"], 1),
            "achievements_granted": total_ach,
            "top": [dict(r) for r in top],
        }


_store: IncentiveStore | None = None
_store_lock = threading.Lock()


def get_incentive_store() -> IncentiveStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = IncentiveStore()
    return _store
