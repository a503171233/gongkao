# -*- coding: utf-8 -*-
"""采集质量增强四项功能单测：
  1) 重试退避 + 优先级队列   —— 指数退避窗口、优先级归一化与排序、失败元数据复位
  2) AI 自评抽检            —— JSON 解析、来源健康分汇总
  3) 结果摘要/失败告警通知   —— 通知配置解析、外部触发即静默失败
  4) 结构化表格/图表题支持   —— 表格→markdown、公式/上下标保留
运行: cd backend && python tests/test_autocollect_enhance.py
"""
import os
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-ac-enh-")
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


# ---------- 1. 指数退避窗口 ----------
check("backoff fails=1 → 1h", ac._backoff_window(1) == 3600)
check("backoff fails=2 → 2h", ac._backoff_window(2) == 7200)
check("backoff fails=3 → 4h", ac._backoff_window(3) == 14400)
check("backoff fails=8 → 封顶24h", ac._backoff_window(8) == 86400)
check("backoff fails=0 → 兜底1h", ac._backoff_window(0) == 3600)
check("backoff 超大 fails 封顶", ac._backoff_window(99) == 86400)

# ---------- 1. 失败元数据 复位 ----------
st = {}
cur = ac._mark_source_fail(st, "http://a.com", "404")
check("fail 记录：fails=1", cur["fails"] == 1)
ac._mark_source_fail(st, "http://a.com", "timeout")
check("连续失败累加 fails=2", st["fail_meta"]["http://a.com"]["fails"] == 2)
check("退避窗口内判定", ac._source_in_backoff(st, "http://a.com") is True)
ac._mark_source_ok(st, "http://a.com")
check("成功后退避复位", ac._source_in_backoff(st, "http://a.com") is False)
check("成功清除 fail_meta", "http://a.com" not in st["fail_meta"])
check("getter 失败计数=0", ac._source_fail_count(st, "http://a.com") == 0)

# ---------- 1b. 退避策略唯一判定点（自动轮次含补采一律尊重退避） ----------
# 回归：自动补采曾绕过退避（旧式 `not force and not retry_failed and ...`），导致
# 每轮固定重试永久失败源（如 IP 级 403 死站），持续污染错误统计与连续失败告警。
st2 = {}
ac._mark_source_fail(st2, "http://dead.example", "403")
check("退避中：自动轮 skip",
      ac._should_skip_backoff(st2, "http://dead.example", force=False) is True)
check("退避中：手动 force 不 skip",
      ac._should_skip_backoff(st2, "http://dead.example", force=True) is False)
ac._mark_source_ok(st2, "http://dead.example")
check("成功后：自动轮不 skip",
      ac._should_skip_backoff(st2, "http://dead.example", force=False) is False)
check("无失败记录：不 skip",
      ac._should_skip_backoff(st2, "http://never.example", force=False) is False)

# 退避窗口过期后自动轮恢复尝试（fails=1 → 1h）
st3 = {}
ac._mark_source_fail(st3, "http://slow.example", "timeout")
st3["fail_meta"]["http://slow.example"]["ts"] = ac.time.time() - 3601
check("窗口过期：自动轮恢复尝试",
      ac._should_skip_backoff(st3, "http://slow.example", force=False) is False)

# ---------- 1c. 残留来源记录清理（fail_meta + failed_urls） ----------
st4 = {"fail_meta": {
    "http://gone.example": {"fails": 3, "ts": ac.time.time(), "msg": ""},
    "q:行测真题": {"fails": 2, "ts": ac.time.time(), "msg": ""},
    "T001|a.md": {"fails": 1, "ts": ac.time.time(), "msg": ""},
}, "failed_urls": {"http://gone.example", "q:行测真题"}}
n = ac._prune_stale_sources(st4)
check("fail_meta 孤儿 URL 键被清理", "http://gone.example" not in st4["fail_meta"])
check("failed_urls 孤儿死链被清理", "http://gone.example" not in st4["failed_urls"])
check("清理计数=2（两处各 1）", n == 2)
check("非 URL 键（搜索词）保留", "q:行测真题" in st4["fail_meta"])
check("非 URL 键（文档）保留", "T001|a.md" in st4["fail_meta"])
check("补采队列非 URL 键保留", "q:行测真题" in st4["failed_urls"])
check("重复清理为幂等", ac._prune_stale_sources(st4) == 0)

# ---------- 1. 优先级归一化与队列排序 ----------
prio = ac._normalize_priorities(
    {"http://h.com": "high", "http://l.com": "low", "http://x.com": "weird", 1: "medium"})
check("优先级归一化 high", prio.get("http://h.com") == "high")
check("优先级归一化 low", prio.get("http://l.com") == "low")
check("非法优先级回落 medium", prio.get("http://x.com") == "medium")
check("非字符串 key 归一化(1→medium)", prio.get("1") == "medium")

urls = ["http://c.com", "http://h.com", "http://l.com", "http://m.com"]
with mock.patch.object(ac, "_priorities", return_value={
        "http://h.com": "high", "http://m.com": "medium", "http://l.com": "low"}):
    ordered = ac._ordered_url_targets(urls)
    check("优先级先高后低排序", ordered[0] == "http://h.com" and
          ordered[-1] == "http://l.com")
    check("同优先级保持原序", ordered.index("http://c.com") < ordered.index("http://m.com"))

# ---------- 2. AI 自评抽检：JSON 解析 ----------
sc, err, issues = ac._parse_qc('{"score":88,"has_error":false,"issues":[]}')
check("parse_qc 正常", sc == 88 and err is False and issues == [])
sc, err, issues = ac._parse_qc('{"score":45,"has_error":true,"issues":["答案缺失"]}')
check("parse_qc 低分+异常", sc == 45 and err is True and issues == ["答案缺失"])
sc, err, issues = ac._parse_qc("模型胡言乱语")
check("parse_qc 兜底(珠100无异常)", sc == 100 and err is False)

# _quality_summary 汇总
params_quality = {"http://a.com": [
    {"score": 90, "has_error": False, "issues": []},
    {"score": 40, "has_error": True, "issues": ["题干残缺"]},
]}
qs = ac._quality_summary({"quality": params_quality})
check("quality 汇总存在", "http://a.com" in qs)
check("quality last_score=40", qs["http://a.com"]["last_score"] == 40)
check("quality low_ratio=0.5", qs["http://a.com"]["low_ratio"] == 0.5)
check("quality checks=2", qs["http://a.com"]["checks"] == 2)

# ---------- 3. 通知配置解析（默认） ----------
nc = ac._notify_config()
check("notify 默认无 webhook", nc["webhook_url"] == "")
check("notify 默认事件为空", nc["notify_on"] == [])
check("notify 默认连续失败阈值=3", nc["fail_alert_threshold"] == 3)
res = ac.test_notify()
check("未配置 webhook → ok=False", res.get("ok") is False)

# _post_json 外部异常静默失败（不联网）
sent = ac._post_json("http://127.0.0.1:1/no", {"x": 1}, timeout=0.2)
check("post_json 连接失败返回 False", sent is False)

# ---------- 3. 通知触发分支（summary / fail_alert 逻辑） ----------
st2 = {"consecutive_failures": 0}
nc_post_cfg = {"webhook_url": "http://x/cb", "notify_on": ["fail_alert"],
               "fail_alert_threshold": 3}
with mock.patch.object(ac, "_notify_config", return_value=nc_post_cfg), \
     mock.patch.object(ac, "_post_json", return_value=True) as mock_post:
    ac._notify_result(st2, {"added": 0, "errors": ["e1"]})
    mock_post.assert_not_called()          # 首次失败未达阈值
    st2["consecutive_failures"] = 2
    ac._notify_result(st2, {"added": 0, "errors": ["e1", "e2"]})
    mock_post.assert_called_once()         # 第3轮达阈值 → 发送 fail_alert
    print("  [assert] fail_alert 在连续3轮失败时触发")

# ---------- 4. 结构化表格 / 公式保留 ----------
html_tbl = ("<table><tr><th>年份</th><th>GDP</th></tr>"
            "<tr><td>2024</td><td>100亿</td></tr></table>")
txt = ac._html_to_text(html_tbl)
check("表格转 markdown 竖线", "| 年份 | GDP |" in txt and "| 2024 | 100亿 |" in txt)

html_formula = "水的化学式 H<sub>2</sub>O，离子 Fe<sup>3+</sup>"
ftxt = ac._html_to_text(html_formula)
check("下标转 _()", "_2" in ftxt)
check("上标转 ^()", "^3+" in ftxt)
check("普通文字保留", "水的化学式" in ftxt)

# save_config 持久化新字段
r = ac.save_config({"enabled": False, "urls": ["http://h.com"],
                    "search_queries": [], "autonomous": False,
                    "priorities": {"http://h.com": "high"},
                    "quality_sample_rate": 0.2,
                    "quality_rules": "题干完整、答案清晰",
                    "webhook_url": "http://wb/t", "notify_on": ["summary"],
                    "fail_alert_threshold": 5})
check("save 回显 priorities", r.get("priorities", {}).get("http://h.com") == "high")
check("save 回显 quality_sample_rate", r.get("quality_sample_rate") == 0.2)
check("save 回显 webhook", r.get("notify", {}).get("webhook_url") == "http://wb/t")
check("save 回显 notify_on", "summary" in r.get("notify", {}).get("notify_on", []))
check("save 回显 fail_alert_threshold", r.get("notify", {}).get("fail_alert_threshold") == 5)

# 越界抽样率应被限制在 0~1
r2 = ac.save_config({"enabled": False, "urls": [], "search_queries": [],
                     "autonomous": False, "quality_sample_rate": 5.0})
check("抽样率越界截断到 1", r2.get("quality_sample_rate") == 1.0)

print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
sys.exit(0 if ok else 1)