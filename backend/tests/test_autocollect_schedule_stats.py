# -*- coding: utf-8 -*-
"""采集调度增强（时间窗口 / 自动补采）+ 通道使用统计（成功/失败/延迟/失活）单测。

覆盖：
  调度 —— schedule 配置保存/回显、_in_schedule_window 普通窗口与跨夜窗口、
          关闭窗口恒真、retry_failed_auto 回读。
  统计 —— _record_channel_use 累计成功/失败/耗时、avg_ms 正确、无 base_url 忽略、
          channel_stats 的 flag 判定（none/high_fail/ok/inactive，含 14 天失活阈值）。
运行: cd backend && python tests/test_autocollect_schedule_stats.py
"""
import os
import sys
import tempfile
import time as _time
import unittest.mock as mock
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-ac-sched-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["STUDY_DB"] = os.path.join(TMP, "study.db")
os.environ["SEARCH_PROVIDER"] = "none"  # 测试不联网

from poc import autocollect as ac  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(extra)[:300]) if extra and not cond else ""))
    if not cond:
        ok = False


def set_hour(h):
    """把 ac.time.localtime 固定到某小时，用于测试时间窗口判定。"""
    fake = mock.MagicMock()
    fake.localtime.return_value = _time.struct_time((2026, 1, 1, h, 0, 0, 3, 1, -1))
    return mock.patch.object(ac, "time", fake)


# ---------- 1. 调度配置默认 + 保存/回显 ----------
r = ac.save_config({"enabled": False, "urls": [], "search_queries": [],
                    "autonomous": False})
check("schedule 默认关闭", r.get("schedule_enabled") is False)
check("schedule 默认窗口 2→6", r.get("schedule_start") == 2 and r.get("schedule_end") == 6)
check("retry_failed_auto 默认关闭", r.get("retry_failed_auto") is False)

r = ac.save_config({"enabled": False, "urls": [], "search_queries": [],
                    "autonomous": False, "schedule_enabled": True,
                    "schedule_start": 23, "schedule_end": 6,
                    "retry_failed_auto": True})
check("schedule 保存/回显: 启用", r.get("schedule_enabled") is True)
check("schedule 保存/回显: 跨夜窗口 23→6", r.get("schedule_start") == 23 and r.get("schedule_end") == 6)
check("schedule 保存/回显: 自动补采开启", r.get("retry_failed_auto") is True)

# 越界小时应被拒绝（ValueError）
try:
    ac.save_config({"schedule_start": 24})
    check("schedule 越界拒绝", False)
except ValueError:
    check("schedule 越界拒绝", True)


# ---------- 2. 时间窗口判定 ----------
# 先回到「关闭窗口」态（上方 23→6 跨夜配置仍生效）
ac.save_config({"enabled": False, "urls": [], "search_queries": [],
                "autonomous": False, "schedule_enabled": False,
                "schedule_start": 2, "schedule_end": 6})
# 关闭窗口恒真
with set_hour(12):
    check("窗口关闭恒真(12时)", ac._in_schedule_window())

# 普通窗口 2→6
ac.save_config({"schedule_enabled": True, "schedule_start": 2, "schedule_end": 6})
with set_hour(3):
    check("普通窗口内(3时)", ac._in_schedule_window())
with set_hour(6):
    check("普通窗口边界(6时)", ac._in_schedule_window())
with set_hour(1):
    check("普通窗口外(1时)", not ac._in_schedule_window())
with set_hour(12):
    check("普通窗口外(12时)", not ac._in_schedule_window())

# 跨夜窗口 22→6
ac.save_config({"schedule_enabled": True, "schedule_start": 22, "schedule_end": 6})
with set_hour(23):
    check("跨夜窗口内(23时)", ac._in_schedule_window())
with set_hour(0):
    check("跨夜窗口内(0时)", ac._in_schedule_window())
with set_hour(6):
    check("跨夜窗口边界(6时)", ac._in_schedule_window())
with set_hour(12):
    check("跨夜窗口外(12时)", not ac._in_schedule_window())

# 还原为不限时段，避免干扰后续
ac.save_config({"schedule_enabled": False, "schedule_start": 2, "schedule_end": 6})


# ---------- 3. 通道使用统计 ----------
# 清空统计文件，保持干净起点
if ac._CH_STATS_PATH.exists():
    ac._CH_STATS_PATH.unlink()

ac.save_llm_channels([
    {"model": "m-ok",    "base_url": "https://ok.example/v1",    "api_key": "k1"},
    {"model": "m-fail",  "base_url": "https://fail.example/v1",  "api_key": "k2"},
    {"model": "m-clean", "base_url": "https://clean.example/v1", "api_key": "k3"},
])
chs = ac.load_llm_channels()
ch_ok, ch_fail, ch_clean = chs[0], chs[1], chs[2]

# 初始均为 none
rows = {r["model"]: r for r in ac.channel_stats()["rows"]}
check("初始 flag=none", all(v["flag"] == "none" and v["success_count"] == 0 for v in rows.values()))

# 无 base_url 的通道不统计（平台默认兜底忽略）
ac._record_channel_use({"model": None, "base_url": None, "api_key": None}, True, 100)
rows = {r["model"]: r for r in ac.channel_stats()["rows"]}
check("无 base_url 忽略", rows.get(None) is None and all(v["success_count"] == 0 for v in rows.values()))

# 失败通道：记录两次失败
ac._record_channel_use(ch_fail, False, 1500, error="HTTP Error 401: Unauthorized")
ac._record_channel_use(ch_fail, False, 900, error="HTTP Error 401: Unauthorized")
st_fail = ac._load_channel_stats()["channels"][ac._channel_key(ch_fail)]
check("失败计数=2", st_fail["fail_count"] == 2)
check("失败记录错误", st_fail["last_error"].startswith("HTTP Error 401"))
rows = {r["model"]: r for r in ac.channel_stats()["rows"]}
check("未成功 → high_fail", rows["m-fail"]["flag"] == "high_fail")

# 成功通道：两次不同耗时 → avg 应=1000ms
ac._record_channel_use(ch_ok, True, 500)
ac._record_channel_use(ch_ok, True, 1500)
st_ok = ac._load_channel_stats()["channels"][ac._channel_key(ch_ok)]
check("成功计数=2", st_ok["success_count"] == 2)
rows = {r["model"]: r for r in ac.channel_stats()["rows"]}
check("成功 → flag=ok", rows["m-ok"]["flag"] == "ok")
check("avg_ms=(500+1500)/2=1000", rows["m-ok"]["avg_ms"] == 1000)
check("有最近成功时间", bool(rows["m-ok"]["last_success_ts"]))
check("失败通道 avg_ms=0(无成功)", rows["m-fail"]["avg_ms"] == 0)

# 成功通道有成功但手动把最近成功时间改到 15 天前 → inactive
data = ac._load_channel_stats()
old = (_time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(_time.time() - 15 * 86400)))
data["channels"][ac._channel_key(ch_ok)]["last_success_ts"] = old
ac._save_channel_stats(data)
rows = {r["model"]: r for r in ac.channel_stats()["rows"]}
check("15天无成功 → inactive", rows["m-ok"]["flag"] == "inactive")

# 从未成功且最近尝试也早已过期 → inactive（先记录一次失败，创建统计条目）
ac._record_channel_use(ch_clean, False, 800, error="timeout")
data = ac._load_channel_stats()
data["channels"][ac._channel_key(ch_clean)]["last_attempt_ts"] = old
ac._save_channel_stats(data)
rows = {r["model"]: r for r in ac.channel_stats()["rows"]}
check("从未成功+尝试过期 → inactive", rows["m-clean"]["flag"] == "inactive")

# inactive_days 透出
check("inactive_days=14", ac.channel_stats()["inactive_days"] == 14)


# ---------- 4. LLM 周期字数配额（预算墙）+ 成本/字数估算 ----------
ac._reset_llm_usage()
check("usage 复位为 0", ac._llm_usage_snapshot()["chars"] == 0)
# 配额为 0（不限）时不触发
check("无配额不超限", ac._llm_budget_exceeded() is False)

ac.save_config({"llm_budget_chars": 2000, "llm_price_per_1m": 2.0})
check("预算配额保存/回显=2000", ac._llm_budget_chars() == 2000)

ac._add_llm_usage(1000)
s = ac._llm_usage_snapshot()
check("用量累计 chars=1000", s["chars"] == 1000)
check("用量 tokens≈700(=1000*0.7)", s["tokens"] == 700)
check("超出 2000 配额? 否(1000<2000)", ac._llm_budget_exceeded() is False)
ac._add_llm_usage(1500)
check("累计 chars=2500", ac._llm_usage_snapshot()["chars"] == 2500)
check("超 2000 配额 → 触发", ac._llm_budget_exceeded() is True)
check("剩余 tokens_left=0", ac._llm_usage_snapshot()["tokens_left"] == 0)

# 成本：price=2 元/百万token，tokens=2500*0.7=1750 → ≈0.0035 元
s = ac._llm_usage_snapshot()
check("成本≈0.0035", abs(s["cost"] - 1750 / 1_000_000 * 2.0) < 1e-6)


# ---------- 4a. 轮次墙钟预算（防单轮跑数小时阻塞调度） ----------
# 回归：_LLM_CALL_TIMEOUT_S 只管「单次调用不挂死」，管不住「一轮内正常调用累积过多」。
# 实测某轮跑满 6.5 小时仍未结束，期间 running 恒为 True，把 interval_secs=3600
# 的每小时调度全部 skip。此处锁定轮级墙钟收尾语义。
check("轮次预算默认 45 分钟", ac._ROUND_BUDGET_DEFAULT_S == 2700)
check("轮次预算回显", ac._round_budget_secs() == 2700)

# 隔离：上段已把 chars 用到 2500 触发 LLM 配额，这里清掉，
# 否则 _round_should_stop 会因配额命中而干扰墙钟维度的断言。
ac._reset_llm_usage()
ac.save_config({"llm_budget_chars": 0})
check("隔离：LLM 配额已不触发", ac._llm_budget_exceeded() is False)

# 无进行中轮次（_round_started_at<=0）→ 永不判超时，避免误伤
ac._round_started_at = 0.0
check("未开始计时不判超时", ac._round_expired() is False)
check("未开始计时 elapsed=0", ac._round_elapsed() == 0.0)
check("未开始计时 should_stop(0)=False", ac._round_should_stop(0) is False)

# 刚开始 → 未超时
ac._round_started_at = _time.monotonic()
check("刚开始不判超时", ac._round_expired() is False)
check("刚开始 should_stop(0)=False", ac._round_should_stop(0) is False)

# 已超过预算 → 到点收尾
ac._round_started_at = _time.monotonic() - (ac._round_budget_secs() + 5)
check("超预算判超时", ac._round_expired() is True)
check("超预算 elapsed>预算", ac._round_elapsed() > ac._round_budget_secs())
check("超预算 should_stop(0)=True", ac._round_should_stop(0) is True)

# 入库安全网仍然独立生效（不受墙钟影响）
ac._round_started_at = _time.monotonic()
check("入库达安全网仍收尾", ac._round_should_stop(ac._RUN_ADD_LIMIT) is True)

# 0 = 不限：即使跑很久也不因墙钟收尾
ac.save_config({"round_budget_secs": 0})
check("预算可配 0(不限)", ac._round_budget_secs() == 0)
ac._round_started_at = _time.monotonic() - 99999
check("0=不限 不判超时", ac._round_expired() is False)
check("0=不限 should_stop(0)=False", ac._round_should_stop(0) is False)

# 自定义预算回显
ac.save_config({"round_budget_secs": 600})
check("预算可配 600s", ac._round_budget_secs() == 600)
ac._round_started_at = _time.monotonic() - 601
check("600s 到点收尾", ac._round_expired() is True)

# 收尾：还原默认并复位计时
ac.save_config({"round_budget_secs": ac._ROUND_BUDGET_DEFAULT_S})
ac._round_started_at = 0.0
check("还原默认预算", ac._round_budget_secs() == ac._ROUND_BUDGET_DEFAULT_S)
# 还原上段隔离掉的 LLM 配额，避免影响后续断言
ac.save_config({"llm_budget_chars": 2000})


# ---------- 4b. 每通道字/成本累计（_record_channel_use 带 chars） ----------
ac._record_channel_use(ch_ok, True, 100, chars=1000)
st = ac._load_channel_stats()["channels"][ac._channel_key(ch_ok)]
check("通道累计 chars=1000", int(st["chars"]) == 1000)
check("通道累计 tokens=700", int(st["tokens"]) == 700)
check("通道 cost=0.0014(700/1e6*2)", abs(float(st["cost"]) - 700 / 1_000_000 * 2.0) < 1e-6)


# ---------- 5. 手动站点规则（详情页正则可配置） ----------
ac.save_config({
    "site_rules": [
        {"domain": "examtest.cn", "name": "示例题库",
         "detail_regex": r"/paper/\d+$", "max_pages": 25, "enabled": True},
        {"domain": "badregex.cn", "name": "非法正则",
         "detail_regex": r"[unclosed", "max_pages": 10, "enabled": True},
    ],
})
check("site_rules 保存/回显=2", len(ac._site_rules_config()) == 2)
r = ac._site_rule("https://examtest.cn/list")
check("命中自定义站点(带www前缀部分匹配)",
      r is not None and r["name"] == "示例题库" and r["max_pages"] == 25)
# 非法正则的规则被编译跳过（不命中）
check("非法正则规则不命中", ac._site_rule("https://badregex.cn/list") is None)
# 命中详情页判定
rd = ac._site_rule("https://www.examtest.cn/paper/123")
check("命中自定义详情页判定", rd is not None and rd["detail"].match("/paper/123"))
# 未命中任何规则 → None（通用态）
check("未命中域名 → None", ac._site_rule("https://unknown.org/x") is None)
# 内建规则仍可用（gkzhenti.cn）
check("内建 gkzhenti 仍命中", ac._site_rule("https://sz.gkzhenti.cn/paper/9") is not None)


# ---------- 6. 失败来源错误详情明细 ----------
state = ac._load_state()
state["source_errors"] = {
    "公开题库网址 https://x.cn 处理失败": {"msg": "HTTP 503", "ts": "2026-09-10 00:00:00", "cat": "network"},
}
state["failed_urls"] = ["https://x.cn"]
state["docs_failed"] = {"1|d.docx": 1.0}
state["failed_queries"] = ["国考 真题"]
ac._save_state(state)
fs = ac.failed_sources()
check("失败来源计数=3", fs["count"] == 3)
check("errors_detail 透出错误 msg/ts/cat",
      fs["errors_detail"]["公开题库网址 https://x.cn 处理失败"]["msg"] == "HTTP 503"
      and fs["errors_detail"]["公开题库网址 https://x.cn 处理失败"]["cat"] == "network")
check("错误时间含 ts 字段", bool(fs["errors_detail"]["公开题库网址 https://x.cn 处理失败"]["ts"]))

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)