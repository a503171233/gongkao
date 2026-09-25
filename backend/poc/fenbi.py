# -*- coding: utf-8 -*-
"""粉笔题库采集源（fenbi.com）。

背景：fenbi.com 为 SPA + 强风控站点——页面 HTML 是 3.3KB 空壳（无题目数据），
通用 HTML 采集链路无效；题库 API 未登录 401。本模块走粉笔 web 端同款接口
（登录态 Cookie 鉴权，全部标准库 urllib 实现，无需浏览器）：

实测打通的链路（2026-09，接口以实际响应为准，字段 camelCase 为主）：
  1. GET  /api/xingce/papers                         试卷列表（含 exercise.key）
  2. GET  /combine/exercise/getExercise?format=html  → data.staticUrl.urls[0]
     &key={exercise.key}&routecs=xingce&app=web...      （裸 key URL）
  3. 裸 URL 拼上 &routecs=xingce&type=1&kav=...&gav=2  → static/exercise JSON
     ⚠️ 缺拼参数服务端返回 404（key 相同也 404）——query 参数是定位必需要求
     → questions[]（仅 id/type，题目 ID 序列）
  4. GET  /api/xingce/solutions?ids=a,b,c（≤50/批）  完整题目：
     content / correctAnswer.choice（0 基序号串，多选逗号分隔）/
     accessories[].options（type=101 选项组）/ solution / keypoints /
     difficulty / material（材料题才有文本）

职责边界：
  - 配置：cookie/enabled/限额 存 autocollect_config.json 的 fenbi 子对象
    （复用 autocollect 的 runtime JSON，管理端脱敏展示）
  - 映射：粉笔题目 JSON → 平台题目 dict（题干/选项/答案/解析/来源）
  - 入库：study.add_questions_batch（敏感词守卫 + 归一化查重 + 同源整卷跳过自动生效）
  - 纪律：请求间隔 ≥2s（可配）、429/5xx 退避一次、Cookie 不进日志/不回显

对外（api.py 管理端挂接）：
  - fenbi_config()      配置只读（Cookie 脱敏 + 掩码计数）
  - save_fenbi_config(p) 配置保存（Cookie 非空才覆盖，支持留空不改）
  - fenbi_test()        试拉：验证 Cookie + 返回最近试卷样本（前 2 题结构）
  - fenbi_collect(teacher_id, max_papers)  采集入库，返回漏斗统计
  - maybe_run_fenbi(teacher_id)  守候钩子（enabled 才跑，全异常静默）
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import Config

_CFG_KEY = "fenbi"
_API_BASE = "https://tiku.fenbi.com/api"
_TIKU_BASE = "https://tiku.fenbi.com"
_SPA_BASE = "https://www.fenbi.com/spa/tiku/exam/paperInfo?paperId="
_STATIC_QUERY = "&routecs=xingce&type=1&kav=125&av=127&hav=125&app=web&apcid=0&deviceId=&gav=2"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_DEFAULTS = {
    "enabled": False,
    "cookie": "",
    "max_papers_per_run": 3,       # 单轮最多拉几张卷（守候/手动共用）
    "max_questions_per_run": 200,  # 单轮题目入库上限（预算保护）
    "min_interval_secs": 2,        # API 请求最小间隔
    "teacher_id": "T001",          # 入库归属老师
    "last_run": None,              # {at, funnel} 最近一轮漏斗
}


# ---------------- 配置 ----------------

def _load_rt() -> dict:
    p = Config.data_dir / "autocollect_config.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_rt(d: dict) -> None:
    p = Config.data_dir / "autocollect_config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def fenbi_config() -> dict:
    """管理端只读：Cookie 脱敏（只露末 4 位）。"""
    rt = (_load_rt().get(_CFG_KEY) or {})
    out = dict(_DEFAULTS)
    out.update({k: v for k, v in rt.items() if k in _DEFAULTS})
    cookie = str(out.get("cookie") or "")
    out["cookie_masked"] = (f"…{cookie[-4:]}" if len(cookie) > 8
                            else ("已配置" if cookie else ""))
    out["cookie_set"] = bool(cookie)
    out.pop("cookie", None)
    return out


def save_fenbi_config(p: dict) -> dict:
    """保存配置。cookie 语义：非空串=覆盖（可粘贴整段 document.cookie）；
    空串/缺省=保持不变；"__CLEAR__"=清除。"""
    rt = _load_rt()
    cur = dict(rt.get(_CFG_KEY) or {})
    incoming_cookie = str((p or {}).get("cookie") or "").strip()
    for k in _DEFAULTS:
        if k in (p or {}) and k not in ("cookie", "last_run"):
            cur[k] = p[k]
    if incoming_cookie == "__CLEAR__":
        cur["cookie"] = ""
    elif incoming_cookie:
        cur["cookie"] = incoming_cookie

    def _num(key: str, default: int, lo: int, hi: int) -> None:
        # 注意 0 是合法输入（应裁到下界），不能走 `or default`（0 是 falsy）
        v = cur.get(key, default)
        if v is None:
            v = default
        try:
            cur[key] = max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            raise ValueError("数值配置非法") from None

    _num("max_papers_per_run", 3, 1, 50)
    _num("max_questions_per_run", 200, 1, 2000)
    _num("min_interval_secs", 2, 1, 30)
    cur["enabled"] = bool(cur.get("enabled"))
    rt[_CFG_KEY] = cur
    _save_rt(rt)
    return fenbi_config()


# ---------------- HTTP 客户端 ----------------

class FenbiAuthError(Exception):
    """Cookie 缺失/失效（401/453 无效DeviceSid）。"""


def _headers(cookie: str, referer: str | None = None) -> dict:
    return {
        "User-Agent": _UA,
        "Referer": referer or "https://www.fenbi.com/",
        "Cookie": cookie,
    }


def _throttle(_deadline: list | None, interval: float) -> None:
    """跨调用维持最小请求间隔（_deadline=[最近请求时间戳]）。"""
    if not _deadline:
        return
    wait = interval - (time.monotonic() - _deadline[0])
    if wait > 0:
        time.sleep(wait)


def _get_json(url: str, cookie: str, interval: float,
              _deadline: list | None = None,
              referer: str | None = None) -> dict | list:
    """GET → JSON。401/453 抛 FenbiAuthError；429/5xx 退避 5s 重试一次。
    _deadline=[最近请求时间戳] 用于跨调用维持最小间隔（节流）。"""
    for attempt in (1, 2):
        _throttle(_deadline, interval)
        req = urllib.request.Request(url, headers=_headers(cookie, referer))
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if _deadline is not None:
                    _deadline[0] = time.monotonic()
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if _deadline is not None:
                _deadline[0] = time.monotonic()
            if e.code in (401, 453):
                raise FenbiAuthError("粉笔 Cookie 缺失或已失效") from None
            if e.code in (429, 500, 502, 503) and attempt == 1:
                time.sleep(5)
                continue
            raise ValueError(f"粉笔 API HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt == 1:
                time.sleep(3)
                continue
            raise ValueError(f"粉笔 API 网络失败：{type(e).__name__}") from None
    raise ValueError("粉笔 API 请求失败")


# ---------------- 拉取与解析 ----------------

def list_papers(cookie: str, page_len: int = 20, cursor: int = 0,
                interval: float = 2.0) -> list[dict]:
    """行测真题试卷列表（含历史卷）。
    返回 [{id, name, exercise_key}]；exercise.key 用于题目 ID 获取。"""
    url = (f"{_API_BASE}/xingce/papers?app=web&cat=1&kav=12&ver=3.88"
           f"&len={int(page_len)}&cursor={int(cursor)}&filters=%7B%7D")
    d = _get_json(url, cookie, interval)
    out = []
    for p in d.get("papers") or d.get("list") or []:
        pid = p.get("id")
        name = (p.get("name") or "").strip()
        ex = p.get("exercise") or {}
        ekey = (ex.get("key") or "").strip()
        if pid and name and ekey:
            out.append({"id": int(pid), "name": name,
                        "exercise_key": ekey})
    return out


def _paper_question_ids(cookie: str, exercise_key: str,
                        interval: float, _deadline: list) -> list[int]:
    """exercise.key → 题目 ID 列表。
    链路：getExercise（拿 staticUrl 裸 key URL）→ 拼 _STATIC_QUERY
    → static/exercise JSON → questions[].id。
    注意：裸 URL 直接请求必 404，必须拼上 routecs 等定位参数。"""
    q = urllib.parse.urlencode({
        "format": "html", "key": exercise_key, "routecs": "xingce",
        "kav": "125", "av": "127", "hav": "125", "app": "web",
        "apcid": "0", "deviceId": "", "gav": "2"})
    ge_url = f"{_TIKU_BASE}/combine/exercise/getExercise?{q}"
    d = _get_json(ge_url, cookie, interval, _deadline,
                  referer="https://spa.fenbi.com/")
    data = d.get("data") if isinstance(d, dict) else None
    urls = ((data or {}).get("staticUrl") or {}).get("urls") or []
    if not urls:
        raise ValueError("getExercise 未返回 staticUrl（练习数据缺失）")
    static_d = _get_json(urls[0] + _STATIC_QUERY, cookie, interval,
                         _deadline, referer="https://spa.fenbi.com/")
    top = static_d if not isinstance(static_d, dict) else (
        static_d.get("data") if isinstance(static_d.get("data"), dict)
        else static_d)
    qs = top.get("questions") or []
    return [int(q["id"]) for q in qs if str(q.get("id") or "").strip().isdigit()]


def fetch_paper(cookie: str, exercise_key: str, interval: float = 2.0
                ) -> list[dict]:
    """按 exercise.key 拉取一卷全部题目详情（solutions 端点 ≤50/批）。"""
    _deadline = [0.0]
    qids = _paper_question_ids(cookie, exercise_key, interval, _deadline)
    if not qids:
        return []
    out: list[dict] = []
    step = 50
    for i in range(0, len(qids), step):
        chunk = qids[i:i + step]
        # ids 用裸逗号拼接（与 web 端一致），逗号不编码
        ids_q = (f"app=web&kav=12&ver=3.88&ids="
                 f"{','.join(map(str, chunk))}")
        d = _get_json(f"{_API_BASE}/xingce/solutions?{ids_q}", cookie,
                      interval, _deadline)
        if isinstance(d, list):
            out.extend(d)
        elif isinstance(d, dict):
            out.extend(d.get("solutions") or [])
    return out


def _strip_html(s: str) -> str:
    """题干/选项 HTML → 纯文本（图片以 [图] 占位；不走重型清洗链路）。"""
    if not s:
        return ""
    t = re.sub(r"<img[^>]*>", "[图]", s, flags=re.I)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</p\s*>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
         .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _options_of(raw: dict) -> list[str]:
    """accessories 中 type=101 的选项组 → 选项文本列表（0 基序对应 A/B/C/D）。"""
    for acc in raw.get("accessories") or []:
        if isinstance(acc, dict) and acc.get("type") == 101 \
                and isinstance(acc.get("options"), list):
            return [str(o) for o in acc["options"]]
    return []


def _answer_letters(raw: dict) -> str:
    """correctAnswer.choice（0 基序号串，多选逗号分隔）→ 'A'/'AC'。"""
    ca = raw.get("correctAnswer")
    choice = ca.get("choice") if isinstance(ca, dict) else None
    if choice is None:
        return ""
    letters = []
    for tok in str(choice).split(","):
        tok = tok.strip()
        if tok.isdigit():
            letters.append(chr(ord("A") + int(tok)))
    return "".join(letters)


# ---------------- 粉笔知识点 → 平台模块枚举（批次10 数据地基） ----------------
# 粉笔细分知识点（如"增长量计算"）不含模块词，走 study.normalize_category 会兜底
# "综合"（90% 题目失效模块维度）。按粉笔行测知识体系保守映射；规则顺序即优先级
# （图形推理的"数量规律"须先于数量关系判定、"经济建设"不含"经济利润"不误配数量）。
_KP_CATEGORY_RULES = [
    ("判断推理", ("数量规律", "位置规律", "样式规律", "属性规律", "特殊规律",
                 "空间", "黑白块", "拼合", "立体", "截面", "三视图",
                 "翻译", "真假推理", "组合排列", "归纳", "原因解释",
                 "论证", "搭桥", "拆桥", "加强", "削弱", "前提", "假设",
                 "补充论据", "定义", "主客体", "问法", "方式目的",
                 "语义", "逻辑关系", "对应关系", "关联关系", "类比",
                 "最大信息")),
    ("资料分析", ("增长", "现期", "基期", "比重", "平均数", "倍数",
                 "资料", "统计", "比值", "综合分析")),
    ("言语理解", ("成语", "实词", "虚词", "词的辨析", "关联词", "拆词",
                 "中心", "主题词", "分述", "细节", "标题", "接语",
                 "语句", "横线", "首句", "尾句", "捆绑", "顺序", "篇章",
                 "病句", "歧义")),
    ("数量关系", ("方程", "工程", "效率", "行程", "相遇", "追及", "利润",
                 "排列", "组合", "概率", "几何", "浓度", "容斥", "集合",
                 "周期", "年龄", "星期", "日期", "牛吃草", "鸡兔",
                 "统筹", "最值", "和差倍比", "数列", "植树", "方阵")),
    ("常识判断", ("政治", "经济", "法律", "历史", "地理", "科技", "文化",
                 "文学", "生物", "物理", "化学", "天文", "生活常识",
                 "哲学", "唯物", "认识论", "党", "宪法", "刑法", "民法",
                 "行政法", "会议", "讲话", "文件", "事件", "建设",
                 "常识", "管理", "公文")),
]


def _category_of(kp: str) -> str:
    """粉笔细分知识点 → 平台 category 枚举（QUESTION_CATEGORIES）；
    不确定返回空串（由 add_question 兜底"综合"），宁可漏不可错。"""
    v = str(kp or "").strip()
    if not v:
        return ""
    for cat, kws in _KP_CATEGORY_RULES:
        for kw in kws:
            if kw in v:
                return cat
    return ""


# ---------------- 题干图片本地化（图形推理等，批次9.2） ----------------
# 平台图片链路（#33 R2）已闭环：落盘 data/qimg/{tid}/ + GET /api/qimg/{tid}/{fname}
# 公开只读 + 前端 ![图N] 标记渲染（练习/模考/智能练习三处）。采集侧只负责
# 下载落盘并生成合规标记：fname=sha1 前 12 位 hex（与端点 _QIMG_FNAME_RE 一致）。

_IMG_SRC_RE = re.compile(r'<img[^>]*?src="([^"]+)"[^>]*>', re.I)


def _img_ext(data: bytes) -> str:
    """按 magic bytes 判图片扩展名（非图返回空串）。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def _alt_cdn(src: str) -> str:
    """粉笔双 CDN 互备：fb.fbstatic.cn ↔ fb.fenbike.cn（同路径）。"""
    if "fb.fbstatic.cn/" in src:
        return src.replace("fb.fbstatic.cn/", "fb.fenbike.cn/")
    if "fb.fenbike.cn/" in src:
        return src.replace("fb.fenbike.cn/", "fb.fbstatic.cn/")
    return ""


def _download_one(src: str, timeout: float = 10.0) -> bytes | None:
    """下载图片并校验 magic（无防盗链实测 2026-09）；失败返回 None。"""
    if src.startswith("//"):
        src = "https:" + src
    if not src.startswith("http"):
        return None
    try:
        req = urllib.request.Request(src, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(2 * 1024 * 1024)  # 单图 2MB 上限
    except Exception:  # noqa: BLE001
        return None
    return data if _img_ext(data) else None


def _qimg_dir(teacher_id: str) -> tuple[str, "Path"]:
    from pathlib import Path
    tid = re.sub(r"[^A-Za-z0-9_-]", "", str(teacher_id or ""))[:64] or "T001"
    d: Path = Config.data_dir / "qimg" / tid
    d.mkdir(parents=True, exist_ok=True)
    return tid, d


def _download_imgs(html: str, teacher_id: str) -> tuple[str, list[str]]:
    """HTML 内 <img> → 下载落盘（sha1 内容寻址幂等）→ 替换为
    ![图N](/api/qimg/{tid}/{fname}) 合规标记；单张失败降级 [图N] 占位文本。
    返回 (新html, 本地URL列表)（按出现顺序）。"""
    if not html or "<img" not in html.lower():
        return html, []
    tid, d = _qimg_dir(teacher_id)
    images: list[str] = []
    seq = [0]

    def _repl(m: "re.Match[str]") -> str:
        seq[0] += 1
        n = seq[0]
        time.sleep(0.15)  # CDN 轻节流（图小量少，固定间隔即可）
        src = m.group(1)
        data = _download_one(src)
        if data is None:  # 双域名互备重试
            alt = _alt_cdn(src)
            if alt:
                data = _download_one(alt)
        if data is None:
            return f"[图{n}]"  # 降级占位（题仍可能因守卫拦截）
        import hashlib
        fname = hashlib.sha1(data).hexdigest()[:12] + _img_ext(data)
        fp = d / fname
        if not fp.exists():  # 内容寻址幂等
            fp.write_bytes(data)
        url = f"/api/qimg/{tid}/{fname}"
        images.append(url)
        return f"![图{n}]({url})"

    return _IMG_SRC_RE.sub(_repl, html), images


def to_item(raw: dict, paper_name: str, teacher_id: str = "T001") -> dict | None:
    """粉笔 solutions 题目 JSON → 平台 batch item；解析失败返回 None。

    - material 实测为 dict（{id, content, ...}），取 content 字段；兼容字符串形态。
    - 题干/材料/解析图片 → 本地落盘 + ![图N](/api/qimg/…) 合规标记（批次9.2 题干；
      批次11b 补解析：此前 solution 直接 strip 导致解析配图丢失、只剩「[图]」占位），
      单张失败降级 [图N] 占位。
    - 纯图守卫（v2）：含图占位但**全部下载失败**（无 ![图 标记）且实质文字
      <40 字（图形推理模板句形态）→ 废题不入库；图片落盘成功即保留。"""
    try:
        raw_content = str(raw.get("content") or "")
        m = raw.get("material")
        raw_material = (str(m.get("content") or "") if isinstance(m, dict)
                        else str(m or ""))
        raw_content, imgs = _download_imgs(raw_content, teacher_id)
        raw_material, imgs_m = _download_imgs(raw_material, teacher_id)
        imgs = imgs + imgs_m
        content = _strip_html(raw_content)
        if not content:
            return None
        # 纯图守卫 v2：只对"含图但图全失败"的题应用 <40 字拦截（无图题不受限）
        if "![图" not in content and "[图" in content:
            core = re.sub(r"\s", "", content.replace("[图]", ""))
            if len(core) < 40:
                return None
        material = _strip_html(raw_material)
        if material:
            content = f"【材料】{material}\n{content}"
        opts = _options_of(raw)
        opt_texts = [f"{chr(ord('A') + i)}. {o}" for i, o in enumerate(opts)]
        answer = _answer_letters(raw)
        # #5 解析图采集：solution 与题干同路（下载→![图N]标记→strip），解析配图不再丢失
        raw_solution = str(raw.get("solution") or "")
        raw_solution, imgs_a = _download_imgs(raw_solution, teacher_id)
        imgs = imgs + imgs_a
        analysis = _strip_html(raw_solution)
        cat = ""
        for kp in raw.get("keypoints") or []:
            if isinstance(kp, dict) and kp.get("name"):
                cat = str(kp["name"]).strip()
                break
        try:
            # 粉笔 difficulty 实测 1-10 标尺（3-7 集中），线性折算平台 1-5
            d10 = int(raw.get("difficulty") or 0)
        except (TypeError, ValueError):
            d10 = 0
        diff = ((d10 + 1) // 2) if d10 else 2
        return {
            "qtype": "choice",
            "question": content,
            "options": opt_texts,
            "answer": answer,
            "analysis": analysis,
            "difficulty": max(1, min(5, diff)) or 2,
            "knowledge_point": cat[:40],
            "category": _category_of(cat),  # 知识点→模块枚举；映射不到由入库兜底"综合"
            "images": imgs,  # /api/qimg/ 本地图 URL（白名单校验在 add_question）
            "source_type": "公开题库",
            "source": str(paper_name or "")[:80],
            "source_url": _SPA_BASE + str(raw.get("paperId") or ""),
            # 批次12a：粉笔题 id（解析图重采/按源回查的对齐键）
            "ext_id": f"fenbi:{raw.get('id') or ''}".rstrip(":"),
        }
    except Exception:  # noqa: BLE001
        return None


# ---------------- 主流程 ----------------

def fenbi_test(paper_limit: int = 1) -> dict:
    """试拉：验证 Cookie；返回最近试卷样本（前 2 题映射结果）。"""
    cfg = fenbi_config()
    cookie = (_load_rt().get(_CFG_KEY) or {}).get("cookie") or ""
    if not cookie:
        raise FenbiAuthError("未配置粉笔 Cookie")
    interval = float(cfg.get("min_interval_secs") or 2)
    papers = list_papers(cookie, page_len=paper_limit, interval=interval)
    if not papers:
        raise ValueError("试卷列表为空（Cookie 可能受限于游客额度）")
    sample_paper = papers[0]
    raws = fetch_paper(cookie, sample_paper["exercise_key"], interval)[:2]
    return {
        "cookie_ok": True,
        "papers_found": len(papers),
        "sample_paper": {"id": sample_paper["id"], "name": sample_paper["name"]},
        "sample_items": [to_item(r, sample_paper["name"]) for r in raws],
    }


def fenbi_collect(teacher_id: str | None = None,
                  max_papers: int | None = None) -> dict:
    """采集入库主流程。漏斗：papers_scanned/papers_pulled/questions_added/
    duplicates/invalid/filtered/papers_error[]。Cookie 失效抛 FenbiAuthError。"""
    rt = _load_rt().get(_CFG_KEY) or {}
    cfg = fenbi_config()
    cookie = rt.get("cookie") or ""
    if not cookie:
        raise FenbiAuthError("未配置粉笔 Cookie")
    from .study import StudyStore
    st = StudyStore()
    tid = teacher_id or cfg.get("teacher_id") or "T001"
    limit_papers = int(max_papers or cfg.get("max_papers_per_run") or 3)
    limit_qs = int(cfg.get("max_questions_per_run") or 200)
    interval = float(cfg.get("min_interval_secs") or 2)

    funnel = {"papers_scanned": 0, "papers_pulled": 0, "questions_added": 0,
              "duplicates": 0, "invalid": 0, "filtered": 0, "papers_error": []}
    added_total = 0
    for p in list_papers(cookie, page_len=20, interval=interval):
        if added_total >= limit_qs or funnel["papers_scanned"] >= limit_papers:
            break
        funnel["papers_scanned"] += 1
        try:
            raws = fetch_paper(cookie, p["exercise_key"], interval)
        except Exception as e:  # noqa: BLE001
            funnel["papers_error"].append(f"{p['name']}: {e}")
            continue
        funnel["papers_pulled"] += 1
        items = [x for x in (to_item(r, p["name"], tid) for r in raws) if x]
        invalid_here = len(raws) - len(items)
        funnel["invalid"] += invalid_here
        if items:
            r = st.add_questions_batch(tid, items, sensitive_check=True)
            funnel["questions_added"] += int(r.get("added", 0))
            # skipped=普通重复；dupe_source=同源判重（同卷补采时旧题逐题计入）
            funnel["duplicates"] += (int(r.get("skipped", 0))
                                     + int(r.get("dupe_source", 0)))
            funnel["invalid"] += int(r.get("invalid", 0))
            funnel["filtered"] += int(r.get("filtered", 0))
            added_total += int(r.get("added", 0))
        time.sleep(interval)
    funnel["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    rt2 = _load_rt()
    fcfg = rt2.get(_CFG_KEY) or {}
    fcfg["last_run"] = {k: v for k, v in funnel.items()}
    rt2[_CFG_KEY] = fcfg
    _save_rt(rt2)
    return funnel


def maybe_run_fenbi(teacher_id: str | None = None) -> dict | None:
    """守候钩子：enabled 且配了 Cookie 才采集；任何异常静默（不伤主守候）。"""
    try:
        rt = (_load_rt().get(_CFG_KEY) or {})
        if not rt.get("enabled") or not (rt.get("cookie") or "").strip():
            return None
        return fenbi_collect(teacher_id)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


# ---------------- 异步采集（管理端手动触发） ----------------

_RUNNING = {"flag": False}


def is_running() -> bool:
    return _RUNNING["flag"]


def start_collect_async(teacher_id: str | None = None) -> None:
    """后台线程跑一轮采集（漏斗写 last_run，管理端轮询 config 可见）。"""
    if _RUNNING["flag"]:
        return

    def _run():
        try:
            fenbi_collect(teacher_id)
        except Exception:  # noqa: BLE001  失败详情由 last_run/日志承载，线程必须退出
            pass
        finally:
            _RUNNING["flag"] = False

    _RUNNING["flag"] = True
    threading.Thread(target=_run, daemon=True).start()
