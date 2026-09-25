#!/usr/bin/env python3
"""公考真题库（gwy.gkzhenti.cn）题库采集源 —— 批次13。

站方提供官方接口（/api 页面声明「接口调用频次基本不受限」）：
  GET /api/json?cls=行测&province=   →  [{"No":"试卷链接","Title":..,"Source":..}, ...]

每张卷三个页面（服务端渲染、结构规整、纯标准库正则解析）：
  /paper/{id}    题目：#printcontent > div.row（subtitle=节标题 / left=题号 + right=题干p段+选项col）
  /answer/{id}   答案：#printcontent 逐行「1、D」
  /explain/{id}  解析：算式验证码保护 —— v1 不抓（analysis 留空，判分依赖答案页；
                 平台 AI 老师可对无解析题作讲解）

合规与节流：
  - 仅使用站方公开页面与官方 JSON 接口，节流 min_interval_secs（默认 2s）
  - 入库 source_type='公开题库'、source=卷名（管理端可见来源）
  - 图片落盘复用粉笔的 /api/qimg 白名单链路（sha1 内容寻址幂等）

对外契约（与 fenbi.py 同构）：
  - gkzt_config() / save_gkzt_config(p)
  - list_papers()                  官方接口拉卷列表
  - gkzt_test(paper_limit=1)       冒烟：解析 1 卷不入库，返回漏斗
  - gkzhenti_collect(teacher_id, max_papers)  采集入库，返回漏斗
  - maybe_run_gkzhenti()           守候钩子（enabled 才跑，全异常静默）
  - is_running() / start_collect_async()
"""
from __future__ import annotations

import html as _html_mod
import json
import random
import re
import threading
import time
import urllib.request

from .config import Config

_CFG_KEY = "gkzhenti"
_BASE = "https://gwy.gkzhenti.cn"
_API_LIST = _BASE + "/api/json?cls=%E8%A1%8C%E6%B5%8B&province="
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_DEFAULTS = {
    "enabled": False,
    "max_papers_per_run": 3,       # 单轮最多采几张卷（守候/手动共用）
    "min_interval_secs": 2,        # 页面请求最小间隔
    "teacher_id": "T001",          # 入库归属老师
    "with_images": True,           # 公式/图形图下载落盘（false 则降级 [图N] 占位）
    "last_run": None,              # {at, funnel}
}

# 与 fenbi 共用 qimg 落盘目录与 /api/qimg 端点（sha1 内容寻址）
from .fenbi import _download_imgs  # noqa: E402  复用图片下载链路（含节流/降级）

_MODULE_RE = re.compile(r"资料分析|判断推理|常识判断|言语理解|数量关系|政治理论")


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
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def gkzt_config() -> dict:
    d = dict(_load_rt().get(_CFG_KEY) or {})
    out = dict(_DEFAULTS)
    out.update({k: v for k, v in d.items() if k != "last_run"} or {})
    out["last_run"] = d.get("last_run")
    return out


def save_gkzt_config(p: dict) -> dict:
    rt = _load_rt()
    cur = dict(rt.get(_CFG_KEY) or {})
    for k in _DEFAULTS:
        if k in (p or {}) and k != "last_run":
            cur[k] = p[k]
    try:
        cur["max_papers_per_run"] = max(1, min(50, int(cur.get("max_papers_per_run", 3))))
    except (TypeError, ValueError):
        cur["max_papers_per_run"] = 3
    try:
        cur["min_interval_secs"] = max(1, min(30, int(cur.get("min_interval_secs", 2))))
    except (TypeError, ValueError):
        cur["min_interval_secs"] = 2
    cur["enabled"] = bool(cur.get("enabled"))
    cur["with_images"] = bool(cur.get("with_images"))
    rt[_CFG_KEY] = cur
    _save_rt(rt)
    return gkzt_config()


# ---------------- HTTP ----------------

def _get(url: str, interval: float, _deadline: list | None = None) -> str:
    if _deadline is None:
        _deadline = [0.0]
    wait = _deadline[0] - time.time()
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", "replace")
    _deadline[0] = time.time() + interval
    return body


# ---------------- 解析 ----------------

def _content_of(html: str) -> str:
    """截取 #printcontent 正文块（到 <footer 或 <script 为止）。"""
    i = html.find('id="printcontent"')
    if i < 0:
        return ""
    tail = html[i:]
    ends = [j for j in (tail.find("<footer"), tail.find("<script")) if j > -1]
    seg = tail[:min(ends)] if ends else tail
    # 去掉 id= 属性所在标签开头之前的内容已天然满足；再截第一个 '>' 之后
    k = seg.find(">")
    return seg[k + 1:] if k > -1 else seg


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = _html_mod.unescape(s)
    return re.sub(r"[ \t\r]+", " ", s).strip()


def _parse_paper(html: str, paper_id: str, paper_title: str,
                 teacher_id: str, with_images: bool,
                 interval: float, deadline: list) -> list[dict]:
    """真题页 → [{no, question, options, section}]；图片按需下载落盘。"""
    content = _content_of(html)
    if not content:
        return []
    rows = content.split('<div class="row">')
    items: list[dict] = []
    section = ""
    for seg in rows[1:]:
        # 站点形态：<div class="col-xs-12 subtitle">六、资料分析。…</div>
        if 'class="subtitle"' in seg or 'subtitle' in seg[:150]:
            m = _MODULE_RE.search(_clean(seg[:600]))
            section = m.group(0) if m else ""
            continue
        m_no = re.search(r'class="col-xs-1 left">\s*(\d+)\s*<', seg)
        if not m_no:
            continue
        no = int(m_no.group(1))
        i_right = seg.find('class="col-xs-11 right"')
        right = seg[i_right:] if i_right > -1 else seg
        # 图片：下载落盘换 ![图N] 标记（_download_one 内置 // → https: 补全）
        images: list[str] = []
        if with_images and "<img" in right.lower():
            right, images = _download_imgs(right, teacher_id)
        elif "<img" in right.lower():
            right = re.sub(r"<img[^>]*>", "[图]", right)
        # 题干段落（选项 col 之外的 <p>）
        right_no_opts = re.sub(r'<div class="col-xs-(?:3|12)">.*?</div>', "", right,
                               flags=re.S)
        stem = "\n".join(_clean(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", right_no_opts, re.S)
                         if _clean(p))
        if not stem:
            continue
        # 选项：A、xxx（col-xs-3 / col-xs-12 两种布局）
        options: list[str] = []
        for om in re.finditer(r'<div class="col-xs-(?:3|12)">\s*([A-D])\s*[、\.]\s*(.*?)</div>',
                              right, re.S):
            letter, body = om.group(1), om.group(2)
            body = _clean(body)
            options.append(f"{letter}. {body}" if body else f"{letter}.")
        if not options:
            continue
        items.append({
            "no": no, "question": stem, "options": options,
            "section": section, "images": images,
        })
    return items


def _parse_answers(html: str) -> dict[int, str]:
    """答案页 → {题号: 字母/对错}。"""
    content = _content_of(html)
    if not content:
        content = html
    out: dict[int, str] = {}
    for m in re.finditer(r"(\d+)\s*[、\.，]\s*([A-D]|对|错|正确|错误)", _clean(content)):
        no = int(m.group(1))
        ans = m.group(2)
        if ans in ("正确", "对"):
            ans = "对"
        elif ans in ("错误", "错"):
            ans = "错"
        out[no] = ans
    return out


def _to_item(no: int, q: dict, answer: str, paper_id: str, paper_title: str,
             teacher_id: str) -> dict | None:
    """解析结果 → add_questions_batch item（含 ext_id）。"""
    if not answer:
        return None
    from . import study as _study
    category = _study.normalize_category(q.get("section") or "") or ""
    return {
        "qtype": "choice",
        "question": q["question"],
        "options": q["options"],
        "answer": answer,
        "analysis": "",  # 解析页有算式验证码保护，v1 不抓（AI 老师可补讲）
        "difficulty": 2,
        "knowledge_point": "",
        "category": category,
        "images": q.get("images") or [],
        "source_type": "公开题库",
        "source": str(paper_title or "")[:80],
        "source_url": f"{_BASE}/paper/{paper_id}",
        "ext_id": f"gzt:{paper_id}:{no}",
    }


# ---------------- 主流程 ----------------

def list_papers() -> list[dict]:
    """官方接口拉行测卷列表（province 空=全部）。返回 [{No, Title, Source}]。"""
    body = _get(_API_LIST, 0)
    data = json.loads(body or "[]")
    return [d for d in data if isinstance(d, dict) and d.get("No")]


def fetch_paper(paper_id: str, paper_title: str, teacher_id: str,
                with_images: bool, interval: float, deadline: list) -> tuple[list[dict], int]:
    """三页抓取 → (入库 items, 题目总数)。"""
    hp = _get(f"{_BASE}/paper/{paper_id}", interval, deadline)
    ha = _get(f"{_BASE}/answer/{paper_id}", interval, deadline)
    qs = _parse_paper(hp, paper_id, paper_title, teacher_id, with_images, interval, deadline)
    answers = _parse_answers(ha)
    items = [it for q in qs
             if (it := _to_item(q["no"], q, answers.get(q["no"]), paper_id,
                                paper_title, teacher_id)) is not None]
    return items, len(qs)


def gkzhenti_collect(teacher_id: str | None = None,
                     max_papers: int | None = None) -> dict:
    """采集入库（同步）。漏斗：papers_scanned / questions_parsed / with_answer /
    added / skipped / invalid / filtered / dupe_source / missing_answer。"""
    from .study import StudyStore
    cfg = gkzt_config()
    tid = teacher_id or cfg.get("teacher_id") or "T001"
    interval = float(cfg.get("min_interval_secs") or 2)
    limit = int(max_papers or cfg.get("max_papers_per_run") or 3)
    with_images = bool(cfg.get("with_images"))
    deadline = [time.time()]
    funnel = {"papers_scanned": 0, "questions_parsed": 0, "with_answer": 0,
              "missing_answer": 0, "added": 0, "skipped": 0, "invalid": 0,
              "filtered": 0, "dupe_source": 0}
    papers = list_papers()
    if not papers:
        funnel["error"] = "官方接口返回空列表"
        return funnel
    st = StudyStore()
    for p in papers:
        if funnel["papers_scanned"] >= limit:
            break
        url = str(p["No"])
        m = re.search(r"/paper/(\d+)", url)
        if not m:
            continue
        paper_id = m.group(1)
        title = str(p.get("Title") or "")
        try:
            items, parsed_n = fetch_paper(paper_id, title, tid, with_images,
                                          interval, deadline)
        except Exception as e:  # noqa: BLE001
            funnel.setdefault("errors", []).append(f"{title[:40]}: {type(e).__name__}")
            continue
        funnel["papers_scanned"] += 1
        funnel["questions_parsed"] += parsed_n
        funnel["with_answer"] += len(items)
        funnel["missing_answer"] += parsed_n - len(items)
        if not items:
            continue
        r = st.add_questions_batch(tid, items, sensitive_check=False)
        funnel["added"] += r["added"]
        funnel["skipped"] += r["skipped"]
        funnel["invalid"] += r["invalid"]
        funnel["filtered"] += r["filtered"]
        funnel["dupe_source"] += r["dupe_source"]
    return funnel


def gkzt_test(paper_limit: int = 1) -> dict:
    """冒烟：拉列表 + 解析前 N 卷（含答案），不入库。"""
    papers = list_papers()
    out: list[dict] = []
    deadline = [time.time()]
    cfg = gkzt_config()
    interval = float(cfg.get("min_interval_secs") or 2)
    for p in papers[:paper_limit]:
        m = re.search(r"/paper/(\d+)", str(p["No"]))
        if not m:
            continue
        pid, title = m.group(1), str(p.get("Title") or "")
        items, parsed_n = fetch_paper(pid, title, cfg.get("teacher_id") or "T001",
                                      bool(cfg.get("with_images")), interval, deadline)
        with_img = sum(1 for it in items if it["images"])
        out.append({"paper": title, "parsed": parsed_n, "with_answer": len(items),
                    "with_images": with_img,
                    "sample": items[0]["question"][:60] if items else ""})
    return {"papers_total": len(papers), "papers": out}


# ---------------- 守候 / 异步 ----------------

_run_lock = threading.Lock()
_running = {"v": False}


def is_running() -> bool:
    return _running["v"]


def _collect_thread(teacher_id: str | None) -> None:
    try:
        funnel = gkzhenti_collect(teacher_id)
        cfg = gkzt_config()
        cur = dict(_load_rt().get(_CFG_KEY) or {})
        cur["last_run"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "funnel": funnel}
        rt = _load_rt()
        rt[_CFG_KEY] = cur
        _save_rt(rt)
        _ = cfg
    except Exception:  # noqa: BLE001
        pass
    finally:
        _running["v"] = False


def start_collect_async(teacher_id: str | None = None) -> None:
    if is_running():
        return
    _running["v"] = True
    threading.Thread(target=_collect_thread, args=(teacher_id,), daemon=True).start()


def maybe_run_gkzhenti(teacher_id: str | None = None) -> dict | None:
    """守候钩子：enabled 且不在运行中才触发（内部全异常静默）。"""
    try:
        cfg = gkzt_config()
        if not cfg.get("enabled") or is_running():
            return None
        if random.random() < 0.5:
            return None  # 分散负载：守候轮次 50% 概率触发
        start_collect_async(teacher_id)
        return {"started": True}
    except Exception:  # noqa: BLE001
        return None
