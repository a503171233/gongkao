# -*- coding: utf-8 -*-
"""批次9：粉笔题库采集源（fenbi.py + /admin/fenbi/* 端点）。

运行：cd backend && python tests/test_fenbi.py
覆盖（mock 为主，链路真实结构见 poc/fenbi.py 文档头）：
  1) 配置：默认值 / Cookie 掩码 / 三分支语义 / 数值边界裁剪 / 非法数值；
  2) 解析：_strip_html；to_item（solutions 结构：accessories type=101 选项、
     correctAnswer.choice 0基→字母、多选逗号分隔、keypoints→知识点、
     material→【材料】前缀、异常→None）；
  3) API 客户端：401/453 → FenbiAuthError；list_papers（list 字段+exercise.key）；
     _paper_question_ids（getExercise→static 拼 query）；fetch_paper（≤50/批）；
  4) 采集漏斗：预算充足全拉 / 题量预算提前停 / 单卷异常不中断 / 无 Cookie 报错；
  5) 守候钩子：未配置 → None；enabled+Cookie → 执行；异常静默 → error dict；
  6) 异步：start_collect_async 置 running → 线程收尾复位；
  7) 端点：鉴权 / GET 脱敏 / POST 往返 / teacher 校验 / test 未配 400 /
     Cookie 失效 400 / collect started。
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = Path(tempfile.mkdtemp(prefix="gk_fenbi_"))
os.environ["AUTH_DB"] = str(TMP / "auth.db")
os.environ["CHAT_DB"] = str(TMP / "chat.db")
os.environ["STUDY_DB"] = str(TMP / "study.db")
os.environ["DB_PATH"] = str(TMP / "vec")
os.environ["DATA_DIR"] = str(TMP / "data")
os.environ["MODEL_CACHE"] = str(TMP / "data" / "models")
os.environ["EMBED_PROVIDER"] = "selftest"

ok_count = 0


def check(name: str, cond: bool) -> None:
    global ok_count
    if not cond:
        print(f"  ✗ {name}")
        raise AssertionError(name)
    ok_count += 1
    print(f"  ✓ {name}")


# ================= 1. 配置 =================
print("[1] 配置管理")
from poc import fenbi  # noqa: E402

cfg = fenbi.fenbi_config()
check("默认 enabled=False", cfg["enabled"] is False)
check("默认 max_papers_per_run=3", cfg["max_papers_per_run"] == 3)
check("默认无 cookie（cookie_set=False）", cfg["cookie_set"] is False)
check("响应不含 cookie 原文", "cookie" not in cfg)

fenbi.save_fenbi_config({"cookie": "x=1; y=2; z=3456"})
check("掩码只露末 4 位", fenbi.fenbi_config()["cookie_masked"] == "…3456")
check("掩码响应仍无 cookie 原文", "cookie" not in fenbi.fenbi_config())

fenbi.save_fenbi_config({"cookie": ""})  # 空=不变
check("空串=保持不变", fenbi.fenbi_config()["cookie_masked"] == "…3456")

fenbi.save_fenbi_config({"cookie": "new=abc; ok=9999"})
check("非空=覆盖", fenbi.fenbi_config()["cookie_masked"] == "…9999")

fenbi.save_fenbi_config({"cookie": "__CLEAR__"})
check("__CLEAR__=清除", fenbi.fenbi_config()["cookie_set"] is False)

fenbi.save_fenbi_config({"enabled": True, "cookie": "k=v1234",
                         "max_papers_per_run": 99, "max_questions_per_run": 9999,
                         "min_interval_secs": 0})
cfg = fenbi.fenbi_config()
check("开关保存", cfg["enabled"] is True)
check("卷数上界裁剪 99→50", cfg["max_papers_per_run"] == 50)
check("题量上界裁剪 9999→2000", cfg["max_questions_per_run"] == 2000)
check("间隔下界裁剪 0→1", cfg["min_interval_secs"] == 1)
fenbi.save_fenbi_config({"enabled": False, "max_papers_per_run": 0,
                         "max_questions_per_run": 200, "min_interval_secs": 2})
check("卷数下界裁剪 0→1", fenbi.fenbi_config()["max_papers_per_run"] == 1)

try:
    fenbi.save_fenbi_config({"max_papers_per_run": "abc"})
    check("非法数值抛 ValueError", False)
except ValueError:
    check("非法数值抛 ValueError", True)


# ================= 2. 解析映射 =================
print("[2] 解析映射")
check("img→[图]", "[图]" in fenbi._strip_html('<p>x<img src="a.png"></p>'))
check("p/br→换行", "\n" in fenbi._strip_html("<p>a</p><br>b"))
check("实体解码", fenbi._strip_html("<p>x&nbsp;&amp;&nbsp;y</p>") == "x & y")

# 图片本地化：默认 mock 掉网络下载（HTML 原样、无 images），不拖慢测试
_orig_dl = fenbi._download_imgs
fenbi._download_imgs = lambda html, tid: (html, [])

# solutions 真实结构样本（字段见 poc/fenbi.py 文档头）
raw = {
    "id": 19526238,
    "content": "<p>1+1=?</p>",
    "accessories": [{"type": 101, "options": ["2", "3", "4", "5"]}],
    "correctAnswer": {"choice": "1", "type": 201},
    "solution": "<p>1+1=2</p>",
    "keypoints": [{"name": "数量关系"}, {"name": "基础"}],
    "difficulty": 3,
    "material": None,
}
item = fenbi.to_item(raw, "2026国考行测卷")
check("题干清洗", item["question"] == "1+1=?")
check("选项 0基→A-E 字母", item["options"][1] == "B. 3" and len(item["options"]) == 4)
check("choice 0基→字母", item["answer"] == "B")
check("solution→analysis", item["analysis"] == "1+1=2")
check("keypoints 首个→knowledge_point", item["knowledge_point"] == "数量关系")
# 粉笔 difficulty 实测 1-10 标尺 → 平台 1-5 折算 (d+1)//2
check("difficulty 1-10→1-5 折算", item["difficulty"] == 2)  # 原 d3 → 2
for _d10, _d5 in ((1, 1), (2, 1), (4, 2), (5, 3), (6, 3), (7, 4), (9, 5), (10, 5)):
    _it = fenbi.to_item(dict(raw, difficulty=_d10), "卷")
    check(f"难度折算 d{_d10}→{_d5}", _it["difficulty"] == _d5)
check("难度缺失→默认2", fenbi.to_item(dict(raw, difficulty=None), "卷")["difficulty"] == 2)
check("source=卷名", item["source"] == "2026国考行测卷")
check("source_type=公开题库", item["source_type"] == "公开题库")
check("source_url 拼 paperId", fenbi.to_item(dict(raw, paperId=777), "卷")
      ["source_url"].endswith("paperId=777"))

multi = dict(raw, correctAnswer={"choice": "0,2"})
check("多选 choice→多字母", fenbi.to_item(multi, "卷")["answer"] == "AC")
# 知识点→模块枚举映射（批次10 数据地基）
check("kp映射: 数量规律→判断推理(优先于数量)", fenbi._category_of("数量规律-素") == "判断推理")
check("kp映射: 增长量计算→资料分析", fenbi._category_of("增长量计算") == "资料分析")
check("kp映射: 经济建设→常识(不误配数量)", fenbi._category_of("经济建设") == "常识判断")
check("kp映射: 经济利润问题→数量", fenbi._category_of("经济利润问题") == "数量关系")
check("kp映射: 成语填空→言语", fenbi._category_of("成语填空") == "言语理解")
check("kp映射: 平均数的增长量→资料", fenbi._category_of("平均数的增长量") == "资料分析")
check("kp映射: 民法典→常识", fenbi._category_of("民法典") == "常识判断")
check("kp映射: 统筹规划问题→数量", fenbi._category_of("统筹规划问题") == "数量关系")
check("kp映射: 未知→空串", fenbi._category_of("神秘知识点") == "")
check("kp映射: 空→空串", fenbi._category_of("") == "")
check("item.category 走映射", fenbi.to_item(
    dict(raw, keypoints=[{"name": "增长量计算"}]), "卷")["category"] == "资料分析")
mat = dict(raw, material="<p>阅读材料</p>")
check("材料题拼【材料】前缀", fenbi.to_item(mat, "卷")["question"].startswith("【材料】"))
# material 真实形态是 dict（{id, content, ...}）——不能 str(dict) 产生垃圾前缀
matd = dict(raw, material={"id": 5367969, "content": "<p>某省2025年GDP数据…</p>"})
itemd = fenbi.to_item(matd, "卷")
check("material dict→取 content", itemd["question"].startswith("【材料】某省2025年GDP数据"))
check("material dict 无垃圾前缀", "{'id'" not in itemd["question"])
matd2 = dict(raw, material={"id": 1})  # dict 无 content → 不拼前缀
check("material dict 空 content→无前缀",
      "【材料】" not in fenbi.to_item(matd2, "卷")["question"])
# 纯图守卫 v2：图下载失败（mock 返回降级占位）且模板句 <40 字 → None
fenbi._download_imgs = lambda html, tid: (
    html.replace('<img/>', "[图1]").replace('<img></p>', "[图1]</p>"), [])
graph_tpl = dict(raw, content="<p>从所给的四个选项中，选择最合适的一个"
                              "填入问号处，使之呈现一定的规律性：</p><p><img/></p>")
check("纯图题(图失败+模板句)→None", fenbi.to_item(graph_tpl, "卷") is None)
# 含图但有实质文字（>40 字）→ 保留
rich = dict(raw, content="<p>甲、乙两个工程队单独完成A、B、C三个工程，所需时间如下："
                         "<img/>问两队合作完成这3个工程至少需要多少天？</p>")
check("含图(失败)但有实质文字→保留", fenbi.to_item(rich, "卷") is not None)
# 无图短题干（<40 字）→ 保留（守卫只管含图题，误杀回归）
short = dict(raw, content="<p>中国的首都是哪里？</p>")
check("无图短题干→保留", fenbi.to_item(short, "卷") is not None)
# 图片落盘成功 → ![图N](/api/qimg/…) 标记 + images 透传
fenbi._download_imgs = lambda html, tid: (
    (html.replace("<img/>", "![图1](/api/qimg/T001/aabbccddeeff.png)"),
     ["/api/qimg/T001/aabbccddeeff.png"]) if "<img" in html else (html, []))
itemg = fenbi.to_item(graph_tpl, "卷")
check("图落盘成功→标记保留", itemg is not None
      and "![图1](/api/qimg/T001/aabbccddeeff.png)" in itemg["question"])
check("images 透传合规 URL", itemg["images"] == ["/api/qimg/T001/aabbccddeeff.png"])
fenbi._download_imgs = _orig_dl  # 恢复真函数

# _download_imgs 单元级：落盘命名/幂等/降级/CDN 互备
import tempfile  # noqa: E402
import pathlib  # noqa: E402
import urllib.error  # noqa: E402

_orig_dir = fenbi.Config.data_dir
_tmpdir = tempfile.mkdtemp()
fenbi.Config.data_dir = pathlib.Path(_tmpdir)
_png = b"\x89PNG\r\n\x1a\n" + b"fakepngdata"
_orig_urlopen2 = fenbi.urllib.request.urlopen
try:
    def _ok(req, timeout=10):
        class _R:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self, n=-1):
                return _png
        return _R()
    fenbi.urllib.request.urlopen = _ok
    html = '<p>题</p><img src="https://fb.fbstatic.cn/api/tarzan/images/aaa.png?width=700">'
    out, imgs = fenbi._download_imgs(html, "T001")
    check("下载成功→合规标记", "![图1](/api/qimg/T001/" in out
          and out.split("/api/qimg/T001/")[1][:12].isalnum())
    check("images 返回本地 URL", len(imgs) == 1 and imgs[0].startswith("/api/qimg/T001/"))
    fname = out.split("/api/qimg/T001/")[1].split(")")[0]
    check("文件落盘", (fenbi.Config.data_dir / "qimg" / "T001" / fname).is_file())
    check("sha1 内容寻址命名", fname == __import__("hashlib").sha1(_png).hexdigest()[:12] + ".png")
    # 非图内容（magic 不符）→ 降级 [图N]
    fenbi.urllib.request.urlopen = lambda req, timeout=10: type("R", (), {
        "__enter__": lambda s: s, "__exit__": lambda s, *a: False,
        "read": lambda s, n=-1: b"<html>not-an-image</html>"})()
    out2, imgs2 = fenbi._download_imgs('<p>x<img src="https://fb.fbstatic.cn/a/b.png"></p>', "T001")
    check("非图内容→降级[图N]", "[图1]" in out2 and imgs2 == [])
    # 下载异常（主域+备域全挂）→ 降级
    def _boom(req, timeout=10):
        raise urllib.error.URLError("down")
    fenbi.urllib.request.urlopen = _boom
    out3, imgs3 = fenbi._download_imgs(
        '<img src="https://fb.fbstatic.cn/api/tarzan/images/x.png">', "T001")
    check("双域全挂→降级[图N]", "[图1]" in out3 and imgs3 == [])
    check("CDN 互备换域名", fenbi._alt_cdn(
        "https://fb.fbstatic.cn/api/tarzan/images/x.png").startswith("https://fb.fenbike.cn/"))
    check("无 img 的 html→原样", fenbi._download_imgs("<p>纯文字</p>", "T001") == ("<p>纯文字</p>", []))
    # 自闭合 img 带尾部属性（<img src="…" />）→ 整个标签被替换，无 ' />' 残留
    fenbi.urllib.request.urlopen = _ok
    out4, imgs4 = fenbi._download_imgs(
        '<p>题干</p><p><img src="https://fb.fbstatic.cn/a/b.png" style="width:700px" /></p>',
        "T001")
    check("自闭合img整标签替换无残留", "![图1]" in out4 and "/>" not in out4
          and "<img" not in out4)
    # 属性在 src 之前的形态
    out5, imgs5 = fenbi._download_imgs(
        '<img class="pic" src="https://fb.fbstatic.cn/a/c.png" width="700">', "T001")
    check("src前属性形态→替换", "![图1]" in out5 and "<img" not in out5)
finally:
    fenbi.urllib.request.urlopen = _orig_urlopen2
    fenbi.Config.data_dir = _orig_dir
noacc = dict(raw, accessories=[])
check("无选项组→空 options", fenbi.to_item(noacc, "卷")["options"] == [])
badans = dict(raw, correctAnswer=None)
check("无答案→空串", fenbi.to_item(badans, "卷")["answer"] == "")
check("空题干→None", fenbi.to_item({"content": ""}, "卷") is None)
check("异常输入→None", fenbi.to_item({"accessories": "notalist"}, "卷") is None)


# ================= 3. API 客户端 =================
print("[3] API 客户端")
import json as _json  # noqa: E402
import urllib.error  # noqa: E402

for code in (401, 453):
    def _raise(*a, _code=code, **k):
        raise urllib.error.HTTPError("u", _code, "err", {}, None)
    _orig_urlopen = fenbi.urllib.request.urlopen
    try:
        fenbi.urllib.request.urlopen = _raise
        try:
            fenbi.list_papers("ck", interval=0.01)
            check(f"HTTP {code}→FenbiAuthError", False)
        except fenbi.FenbiAuthError:
            check(f"HTTP {code}→FenbiAuthError", True)
    finally:
        fenbi.urllib.request.urlopen = _orig_urlopen

papers_payload = {"list": [{"id": 5, "name": "2026 模拟卷",
                            "exercise": {"key": "1_1_abc"}}]}
_orig_get = fenbi._get_json
_orig_pqids = fenbi._paper_question_ids
try:
    fenbi._get_json = lambda url, cookie, interval, _deadline=None, referer=None: papers_payload
    papers = fenbi.list_papers("ck", interval=0.01)
    check("list_papers 解析 list+exercise_key", len(papers) == 1
          and papers[0]["id"] == 5 and papers[0]["exercise_key"] == "1_1_abc")

    # _paper_question_ids：getExercise 响应 → 拼 query → static questions
    def fake_ge(url, cookie, interval, _deadline=None, referer=None):
        if "getExercise" in url:
            assert "routecs=xingce" in url
            return {"data": {"staticUrl": {"urls": [
                "https://tiku.fenbi.com/combine/static/exercise?key=KKK"]}}}
        assert "key=KKK" in url and "routecs=xingce" in url, url  # 必须拼参数
        return {"questions": [{"id": 11}, {"id": 12}, {"id": "bad"}]}

    fenbi._get_json = fake_ge
    got_ids = fenbi._paper_question_ids("ck", "1_1_abc", 0.01, [0.0])
    check("getExercise→static→题目 ID", got_ids == [11, 12])

    # fetch_paper：solutions ≤50/批
    sol_calls = {"urls": []}

    def fake_sol(url, cookie, interval, _deadline=None, referer=None):
        sol_calls["urls"].append(url)
        qids = url.split("ids=")[1].split("&")[0].split(",")
        return [dict(raw, id=int(q)) for q in qids]

    fenbi._get_json = fake_sol
    fenbi._paper_question_ids = lambda cookie, key, interval, _deadline: list(range(1000, 1130))
    got = fenbi.fetch_paper("ck", "1_1_abc", interval=0.01)
    check("solutions 分批 50（130→3 请求）", len(sol_calls["urls"]) == 3)
    check("题目数 130", len(got) == 130)
finally:
    fenbi._get_json = _orig_get
    fenbi._paper_question_ids = _orig_pqids


# ================= 4. 采集漏斗（mock） =================
print("[4] 采集漏斗（mock）")
fenbi.save_fenbi_config({"enabled": False, "cookie": "ck=abcd1234",
                         "max_papers_per_run": 3, "max_questions_per_run": 200,
                         "min_interval_secs": 1})


class _FakeStudy:
    def __init__(self):
        self.calls = []

    def add_questions_batch(self, tid, items, sensitive_check=True):
        self.calls.append((tid, len(items)))
        return {"added": len(items) // 2, "skipped": 1, "invalid": 0,
                "filtered": 0, "dupe_source": 2}


from poc import study as study_mod  # noqa: E402

orig_study = study_mod.StudyStore
_orig_lp = fenbi.list_papers
_orig_fp = fenbi.fetch_paper
_orig_sleep = time.sleep
time.sleep = lambda s: None
try:
    study_mod.StudyStore = lambda: _FakeStudy()
    papers_3 = [{"id": i, "name": f"卷{i}", "exercise_key": f"k{i}"}
                for i in range(3)]
    fenbi.list_papers = lambda cookie, **k: papers_3
    fenbi.fetch_paper = lambda cookie, key, *a, **k: [dict(raw)] * 10

    # 场景 A：预算充足 → 3 卷全拉
    funnel = fenbi.fenbi_collect("T001")
    check("papers_scanned=3", funnel["papers_scanned"] == 3)
    check("papers_pulled=3", funnel["papers_pulled"] == 3)
    check("duplicates=skipped+dupe_source=9", funnel["duplicates"] == 9)
    check("questions_added=15", funnel["questions_added"] == 15)
    last = fenbi.fenbi_config().get("last_run") or {}
    check("last_run 写回配置", last.get("papers_scanned") == 3 and last.get("at"))

    # 场景 B：题量预算 4 → 第一卷入库 5 题后提前停
    fenbi.save_fenbi_config({"max_questions_per_run": 4})
    fb = fenbi.fenbi_collect("T001")
    check("预算提前停（1 卷即止）", fb["papers_scanned"] == 1
          and fb["papers_pulled"] == 1 and fb["questions_added"] == 5)

    # 场景 C：单卷异常不中断
    fenbi.save_fenbi_config({"max_questions_per_run": 200})

    def _boom(cookie, key, *a, **k):
        if key == "k1":
            raise ValueError("模拟单卷失败")
        return [dict(raw)]

    fenbi.fetch_paper = _boom
    f2 = fenbi.fenbi_collect("T001")
    check("单卷异常计 papers_error", len(f2["papers_error"]) == 1
          and "卷1" in f2["papers_error"][0])
    check("异常不中断后续卷", f2["papers_pulled"] == 2)

    # 场景 D：无 Cookie
    fenbi.save_fenbi_config({"cookie": "__CLEAR__"})
    try:
        fenbi.fenbi_collect("T001")
        check("无 Cookie→FenbiAuthError", False)
    except fenbi.FenbiAuthError:
        check("无 Cookie→FenbiAuthError", True)
finally:
    time.sleep = _orig_sleep
    fenbi.list_papers = _orig_lp
    fenbi.fetch_paper = _orig_fp
    study_mod.StudyStore = orig_study


# ================= 5. 守候钩子 =================
print("[5] 守候钩子")
check("未配置 → None", fenbi.maybe_run_fenbi("T001") is None)

fenbi.save_fenbi_config({"enabled": True, "cookie": "ck=abcd1234"})
fenbi.list_papers = lambda cookie, **k: []
try:
    r = fenbi.maybe_run_fenbi("T001")
    check("enabled+Cookie → 采集执行", r is not None and r["papers_scanned"] == 0)
finally:
    fenbi.list_papers = _orig_lp
    fenbi.save_fenbi_config({"enabled": False})

fenbi.list_papers = lambda cookie, **k: (_ for _ in ()).throw(RuntimeError("炸了"))
fenbi.save_fenbi_config({"enabled": True})  # 重开开关（上一场景 finally 已关）
try:
    r = fenbi.maybe_run_fenbi("T001")
    check("异常静默 → error dict", isinstance(r, dict) and "炸了" in r.get("error", ""))
finally:
    fenbi.list_papers = _orig_lp
    fenbi.save_fenbi_config({"cookie": "__CLEAR__", "enabled": False})


# ================= 6. 异步采集 =================
print("[6] 异步采集")
fenbi.save_fenbi_config({"cookie": "ck=abcd1234"})
_done = threading.Event()


def _fast_collect(*a, **k):
    time.sleep(0.3)  # 模拟真实采集耗时，避免线程先于断言收尾
    _done.set()
    return {"papers_scanned": 0}


_orig_fc = fenbi.fenbi_collect
fenbi.fenbi_collect = _fast_collect
try:
    fenbi.start_collect_async("T001")
    check("启动即 running", fenbi.is_running() is True)
    check("线程收尾复位 running", _done.wait(5) is True)
    for _ in range(50):
        if not fenbi.is_running():
            break
        time.sleep(0.1)
    check("结束后 flag=False", fenbi.is_running() is False)
    _done2 = threading.Event()
    fenbi.fenbi_collect = lambda *a, **k: (_done2.set(), {})[1]
    fenbi.start_collect_async("T001")
    check("可再次启动", _done2.wait(5) is True)
finally:
    fenbi.fenbi_collect = _orig_fc
    fenbi.save_fenbi_config({"cookie": "__CLEAR__"})
    for _ in range(50):
        if not fenbi.is_running():
            break
        time.sleep(0.1)


# ================= 7. 端点 =================
print("[7] /admin/fenbi/* 端点")
from fastapi.testclient import TestClient  # noqa: E402

from poc import api as api_mod  # noqa: E402

client = TestClient(api_mod.app)

_uid_admin = api_mod.auth_store.register("粉笔自检管理员", "adminpass1")
api_mod.auth_store.set_role(_uid_admin, "admin")
_uid_free = api_mod.auth_store.register("粉笔普通用户", "freepass1")
_tok = api_mod.auth_store.login("粉笔自检管理员", "adminpass1")
_tok_free = api_mod.auth_store.login("粉笔普通用户", "freepass1")
_H = {"Authorization": f"Bearer {_tok}"}

r = client.get("/admin/fenbi/config")
check("无 token → 401", r.status_code == 401)
r = client.get("/admin/fenbi/config", headers={"Authorization": "Bearer bad"})
check("坏 token → 401", r.status_code == 401)
r = client.get("/admin/fenbi/config",
               headers={"Authorization": f"Bearer {_tok_free}"})
check("非 admin → 403", r.status_code == 403)

r = client.get("/admin/fenbi/config", headers=_H)
check("admin GET → 200", r.status_code == 200)
check("GET 返回含 running/last_run", "running" in r.json() and "last_run" in r.json())

r = client.post("/admin/fenbi/config", headers=_H,
                json={"enabled": True, "cookie": "kk=zzzz8888",
                      "teacher_id": "T001"})
check("POST 保存 → 200", r.status_code == 200)
check("保存后 cookie_set", r.json()["cookie_set"] is True)
check("保存响应脱敏", r.json()["cookie_masked"] == "…8888")

r = client.post("/admin/fenbi/config", headers=_H,
                json={"teacher_id": "NOPE", "cookie": ""})
check("teacher 非法 → 404", r.status_code == 404)

r = client.post("/admin/fenbi/test", headers=_H)
check("test 未配 Cookie → 400", r.status_code == 400 and "粉笔" in r.json()["detail"])

fenbi.save_fenbi_config({"cookie": "ck=abcd1234"})
fenbi.list_papers = lambda cookie, **k: (_ for _ in ()).throw(
    fenbi.FenbiAuthError("粉笔 Cookie 缺失或已失效"))
try:
    r = client.post("/admin/fenbi/test", headers=_H)
    check("Cookie 失效 → 400+中文 detail", r.status_code == 400
          and "Cookie" in r.json()["detail"])
finally:
    fenbi.list_papers = _orig_lp
    fenbi.save_fenbi_config({"cookie": "__CLEAR__"})

fenbi.save_fenbi_config({"cookie": "ck=abcd1234"})
_orig_fc2 = fenbi.fenbi_collect
fenbi.fenbi_collect = lambda *a, **k: {"papers_scanned": 0}
try:
    r = client.post("/admin/fenbi/collect", headers=_H)
    check("collect → started=true", r.status_code == 200
          and r.json()["started"] is True)
    for _ in range(50):
        if not fenbi.is_running():
            break
        time.sleep(0.1)
    r2 = client.post("/admin/fenbi/collect", headers=_H)
    check("空闲后可再次触发", r2.status_code == 200
          and r2.json()["started"] is True)
    for _ in range(50):
        if not fenbi.is_running():
            break
        time.sleep(0.1)
finally:
    fenbi.fenbi_collect = _orig_fc2
    fenbi.save_fenbi_config({"cookie": "__CLEAR__", "enabled": False})
    for _ in range(50):
        if not fenbi.is_running():
            break
        time.sleep(0.1)

# Cookie 确不落日志：全源码无 cookie 打印路径
src = (BACKEND / "poc" / "fenbi.py").read_text(encoding="utf-8")
check("源码无 cookie 打印路径", 'print(f"Cookie' not in src
      and "Cookie" in src)  # Cookie 仅出现在请求头构造与注释

print(f"\n通过 {ok_count} 项检查")
sys.exit(0 if ok_count else 1)
