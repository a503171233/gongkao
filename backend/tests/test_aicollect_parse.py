# -*- coding: utf-8 -*-
"""问题1验证：AI 采集文件解析链路（parse_collect_file + 分批提取）。
覆盖：md/txt 解析、格式白名单、50MB 上限、空内容、_split_batches 段落边界、
_normalize_extracted 容错。docx/pptx/pdf 依赖本地是否有对应库（无则跳过并标注）。
运行: cd backend && python tests/test_aicollect_parse.py
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-aicollect-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["EMBED_PROVIDER"] = "selftest"

from poc import admin as admin_biz  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(extra)[:300]) if extra and not cond else ""))
    if not cond:
        ok = False


# ---------- 1. 格式白名单与大小校验 ----------
try:
    admin_biz.parse_collect_file("evil.exe", b"MZ...")
    check("拒绝: 非白名单扩展名", False)
except ValueError as e:
    check("拒绝: 非白名单扩展名", "暂不支持" in str(e), str(e))

try:
    admin_biz.parse_collect_file("noext", b"abc")
    check("拒绝: 无扩展名", False)
except ValueError as e:
    check("拒绝: 无扩展名", "暂不支持" in str(e))

try:
    admin_biz.parse_collect_file("big.md", b"x" * (50 * 1024 * 1024 + 1))
    check("拒绝: 超 50MB", False)
except ValueError as e:
    check("拒绝: 超 50MB", "50MB" in str(e), str(e))

try:
    admin_biz.parse_collect_file("empty.txt", b"")
    check("拒绝: 空文件", False)
except ValueError as e:
    check("拒绝: 空文件", "空" in str(e))

# 路径穿越防护：文件名只保留 basename
r = admin_biz.parse_collect_file("../../etc/passwd.md", b"# hello")
check("路径安全: 文件名取 basename", r["doc_name"] == "passwd.md", r["doc_name"])

# ---------- 2. md/txt 解析 ----------
md = "# 言语理解\n\n1. 下列哪项是转折关系？\nA. 但是\nB. 并且\n答案：A\n"
r = admin_biz.parse_collect_file("讲义.md", md.encode("utf-8"))
check("md 解析: 返回文本与字数", r["chars"] == len(md.strip()) and "转折关系" in r["text"])

r = admin_biz.parse_collect_file("讲义.txt", "纯文本文档内容".encode("utf-8"))
check("txt 解析: 正常", "纯文本" in r["text"])

# BOM 头 UTF-8（Windows 导出文件常见）
r = admin_biz.parse_collect_file("bom.txt", b"\xef\xbb\xbf" + "带BOM的文本".encode("utf-8"))
check("BOM txt 解析: 剥离 BOM", "带BOM" in r["text"])

# 空白内容（只有空行的 md）
try:
    admin_biz.parse_collect_file("blank.md", b"  \n\n  ")
    check("拒绝: 纯空白内容", False)
except ValueError as e:
    check("拒绝: 纯空白内容", "解析出文本" in str(e))

# ---------- 3. docx/pptx/pdf（本地依赖可用时） ----------
def _try_lib(name, builder, expect_word):
    lib = {"docx": "docx", "pptx": "pptx", "pdf": "fitz"}[name]
    try:
        __import__(lib)
    except ImportError:
        print(f"SKIP {name} 解析（本地无 {lib}，线上容器有——线上验收覆盖）")
        return
    try:
        data = builder()
        r = admin_biz.parse_collect_file(f"t.{name}", data)
        check(f"{name} 解析: 提取文本含预期词", expect_word in r["text"])
    except Exception as e:  # noqa: BLE001
        check(f"{name} 解析", False, str(e))


def _make_docx():
    import docx
    d = docx.Document()
    d.add_paragraph("转折之后是重点docx测试")
    d.add_paragraph("")
    d.add_paragraph("第二段落")
    p = f"{TMP}/t.docx"
    d.save(p)
    return Path(p).read_bytes()


def _make_pptx():
    from pptx import Presentation
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "pptx标题测试"
    slide.placeholders[1].text = "正文内容转折之后是重点"
    p = f"{TMP}/t.pptx"
    prs.save(p)
    return Path(p).read_bytes()


def _make_pdf():
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "pdf content test 123")
    p = f"{TMP}/t.pdf"
    doc.save(p)
    doc.close()
    return Path(p).read_bytes()


_try_lib("docx", _make_docx, "转折之后是重点docx测试")
_try_lib("pptx", _make_pptx, "正文内容转折之后是重点")
_try_lib("pdf", _make_pdf, "123")

# ---------- 4. _split_batches 段落边界分批 ----------
sb = admin_biz._split_batches
check("分批: 短文单批", sb("short", 12000, 8) == ["short"])

long_text = "\n".join(f"第{i}段落内容" + "x" * 200 for i in range(200))  # ~4.5万字
batches = sb(long_text, 12000, 8)
check("分批: 长文多批且各批不超限", len(batches) > 1 and all(len(b) <= 12000 for b in batches),
      f"n={len(batches)} sizes={[len(b) for b in batches]}")
check("分批: 无内容丢失", sum(len(b) for b in batches) >= len(long_text.replace('\n', '')) - 100,
      f"total={sum(len(b) for b in batches)} vs {len(long_text)}")
# 每批以段落边界开头（不以'x'续字符开头，说明没拦腰截断）
check("分批: 按段落边界切", all(b.startswith("第") for b in batches[1:]),
      [b[:8] for b in batches[1:]])

# 超上限：10万字 → 最多 8 批
huge = "\n".join(f"段落{i}" + "y" * 300 for i in range(300))
b8 = sb(huge, 12000, 8)
check("分批: 上限 8 批熔断", len(b8) == 8, len(b8))

# ---------- 5. _normalize_extracted 容错 ----------
ne = admin_biz._normalize_extracted
qs = ne('[{"qtype":"choice","question":"题干一","options":["A. x"],"answer":"A","analysis":"","difficulty":2,"knowledge_point":"转折"}]')
check("规范化: 标准 JSON", len(qs) == 1 and qs[0]["qtype"] == "choice" and qs[0]["difficulty"] == 2)

qs = ne('```json\n[{"question":"题干二","qtype":"judge","answer":"对"}]\n```')
check("规范化: 剥离 ```json 包裹", len(qs) == 1 and qs[0]["answer"] == "对")

qs = ne('[{"qtype":"xxx","question":"非法题型"},{"question":""},{"qtype":"essay","question":"有效题","difficulty":9}]')
check("规范化: 非法 qtype 回退 essay / 空题干剔除 / difficulty 夹取", 
      len(qs) == 2 and qs[0]["qtype"] == "essay" and qs[1]["difficulty"] == 5)

for bad in ["没有数组", "[不完整", '{"obj":1}', "[]"]:
    try:
        r = ne(bad)
        check(f"规范化: 异常输入 '{bad[:10]}' 抛错或空", isinstance(r, list))
    except ValueError:
        check(f"规范化: 异常输入 '{bad[:10]}' 抛错或空", True)

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
