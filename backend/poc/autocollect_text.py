# -*- coding: utf-8 -*-
"""自动采集：网页抓取与 HTML/文本解析、图片提取、内容指纹（纯函数，无状态）。"""
import re


# ============================================================
# 网页抓取文本
# ============================================================

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _html_to_text(html: str) -> str:
    """HTML → 粗提正文文本（去除 script/style/noscript 与全部标签）。"""
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    body = _tables_md(body)            # 采集质量增强4：表格 → markdown
    body = _formula_md(body)           # 公式/上下标/化学式保留
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", body).strip()


# ---- 采集质量增强 4 实现：结构化表格 / 图表题支持 ----
_TABLE_CELL = re.compile(r"(?is)<(?:td|th)\b[^>]*>(.*?)</(?:td|th)>")


def _clean_cell(s: str) -> str:
    """表格单元格提纯：去内嵌标签、还原常见 HTML 实体、压缩空白。"""
    s = re.sub(r"(?is)<br[^>]*>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", "", s or "")
    s = (s or "").replace("&nbsp;", " ").replace("&ensp;", " ").replace("&emsp;", " ")
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def _tables_md(html: str) -> str:
    """把 <table> 数据表转成 markdown 竖线表格，供 LLM 提取资料分析/图表题干时保留表格结构。
    每行输出 `| 单元格1 | 单元格2 … |`，表头行（th）不额外加分隔行（避免干扰提取）。"""
    def _row_md(inner: str) -> str:
        cells = [_clean_cell(c) for c in _TABLE_CELL.findall(inner)]
        if not cells:
            return ""
        return "| " + " | ".join(cells) + " |"
    def _tbl(m):
        rows = [_row_md(rm)
                for rm in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", m.group(1))]
        lines = [ln for ln in rows if ln]
        return "\n" + ("\n".join(lines)) + "\n" if lines else " "
    return re.sub(r"(?is)<table\b[^>]*>(.*?)</table>", _tbl, html)


def _formula_md(html: str) -> str:
    """公式/化学式保留：MathML → 括号标注的纯文本；<sub>/<sup> → _() / ^()。
    保证化学式（如 H₂O、Fe^(3+)）、上下标不出现在抽取时被剥离成粘连乱码。"""
    def _math(m):
        inner = re.sub(r"(?s)<[^>]+>", " ", m.group(1))
        return f"〔公式：{re.sub(chr(92) + r's+', ' ', inner).strip()}〕"
    out = re.sub(r"(?is)<math\b[^>]*>(.*?)</math>", _math, html)
    out = re.sub(r"(?is)<sub>\s*([^<>]+?)\s*</sub>", r"_\1", out)
    out = re.sub(r"(?is)<sup>\s*([^<>]+?)\s*</sup>", r"^\1", out)
    return out


def _html_to_text_wimg(html: str, base_url: str, img_dir: object,
                       client, max_imgs: int = 40) -> str:
    """#60 网页正文文本，同时把 <img> 图片下载托管为 /api/qimg 标记并按原位插入。

    用于图形推理/资料分析图表等需配图的题目：此前 _html_to_text 把图片全部剥掉，
    导致网页图形推理题只有文字没有图。规则：
    - 只下载本页 <img src> 指向的图片，按出现顺序编号（第N张 → 图N）；
    - 绝对/相对路径都用 base_url 归一整链接；下载失败或超链接静默跳过（不阻断文本）；
    - 同一页图片数量上限 max_imgs，防止畸形页面海量图拖慢采集。
    返回含 ![图N](/api/qimg/{tid}/{fname}) 标记的纯文本，供 _split_paper_units / LLM 提取。"""
    from urllib.parse import urljoin

    def _abs(src: str) -> str:
        if src.startswith(("http://", "https://")):
            return src
        try:
            return urljoin(base_url, src)
        except Exception:  # noqa: BLE001
            return src

    img_re = re.compile(r"(?i)<img[^>]*?src\s*=\s*[\"']([^\"' >]+)[\"']")
    counter = [0]

    def _dl(m):
        src = (m.group(1) or "").strip()
        if not src:
            return " "
        counter[0] += 1
        if counter[0] > max_imgs:
            return " "
        try:
            from .ingest import _img_mark, _save_img
            rr = client.get(_abs(src), timeout=8, follow_redirects=True)
            if rr.status_code != 200:
                return " "
            fname = _save_img(img_dir, rr.content)
            from .study import SHARED_TEACHER_ID
            return f" ![图{counter[0]}](/api/qimg/{SHARED_TEACHER_ID}/{fname}) "
        except Exception:  # noqa: BLE001  图片下载失败跳过，不阻断整页文本
            return " "

    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    body = _tables_md(body)          # 采集质量增强4：表格 → markdown
    body = _formula_md(body)         # 公式/上下标/化学式保留
    body = img_re.sub(_dl, body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", body).strip()


def _page_source(page_url: str, page_title: str, questions: list[dict]) -> list[dict]:
    """#60 题目来源标注：题号不进题干，改写入出处（如\"2025年行测资料分析第120题\"）。
    - 有页面标题：压缩掉分隔符后栏目冗余（年/部分/题号保留），再拼\"第N题\"；
    - 无标题：回退用网页 URL 当来源；无题号则不拼。number 用后即弃（不入库）。"""
    base = (page_title or "").strip()
    if base:
        base = re.split(r"(?i)[\-—_|:：]", base, maxsplit=1)[0].strip()
    for q in questions or []:
        no = q.get("number")
        if base:
            q["source"] = (base + (f"第{no}题" if no else "")).strip()
        else:
            q["source"] = page_url or ""
        q.pop("number", None)
    return questions


def _page_title(html: str) -> str:
    """提取 <title> 文本（去标签/空白，截断 120 字）；无 title 返回空串。
    #59 用于撰写可读的来源标注（如 gwy 单题页标题即含“年/科目/部分/题号”）。"""
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    if not m:
        return ""
    t = re.sub(r"(?s)<[^>]+>", "", m.group(1))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:120]


def _decode_resp_text(r) -> str:
    """按响应实际编码解码页面文本。
    老牌中文站常不回 Content-Type charset（或回 gbk/gb2312），若硬按 UTF-8 解会出乱码
    （如 51test.net），导致内容门控把真实题目整页误判成噪音丢弃。
    顺序：响应头 charset → <meta charset=…> → UTF-8；UTF-8 高乱码回退 GB18030。"""
    charset = ""
    ct = (r.headers.get("content-type") or "").lower()
    m = re.search(r"charset=\s*[\"']?([a-zA-Z0-9_\-]+)", ct)
    if m:
        charset = m.group(1)
    if not charset:
        sniff_html = r.text[:3000]
        m = re.search(r"<meta[^>]*(?:charset\s*=\s*[\"']?([a-zA-Z0-9_\-]+))",
                      sniff_html)
        if m:
            charset = m.group(1)
    enc = charset or "utf-8"
    try:
        text = r.content.decode(enc, "replace")
    except LookupError:
        text = r.content.decode("utf-8", "replace")
    if not charset and text and text.count("\ufffd") / max(len(text), 1) > 0.01:
        try:
            text = r.content.decode("gb18030", "replace")
        except Exception:  # noqa: BLE001  解码回退失败保持现状
            pass
    return text


def _extract_site_links(html: str, base_url: str, limit: int = 300) -> list[str]:
    """从 HTML 提取同源站内 <a href>（去锚点/外链/静态跳转，URL 归一化去重，可含 query）。"""
    from urllib.parse import urljoin, urlparse

    base = urlparse(base_url)
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"(?is)<a\b[^>]*href=[\"']([^\"']+)[\"']", html):
        href = m.group(1).strip()
        if not href or href.startswith("#") or href.lower().startswith(
                ("javascript:", "mailto:", "tel:")):
            continue
        full = urljoin(base_url, href)
        u = urlparse(full)
        if u.scheme not in ("http", "https") or u.netloc != base.netloc:
            continue
        clean = f"{u.scheme}://{u.netloc}{u.path}"
        if u.query:
            clean += "?" + u.query
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
            if len(out) >= limit:
                break
    return out




def _text_fp(text: str) -> str:
    """网页/文档内容指纹：内容没变化就不重复提取（增量盯站，省 LLM 成本）。"""
    import hashlib

    return hashlib.sha1((text or "").encode("utf-8", "ignore")).hexdigest()
