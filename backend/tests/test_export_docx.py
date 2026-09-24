# -*- coding: utf-8 -*-
"""批次10：#21 DOCX 导出（POST /me/export/docx）。

运行：cd backend && python tests/test_export_docx.py
覆盖：
  1) HTMLParser 结构提取：标题/段落/列表/表格/实体解码/坏 HTML 退化/空输入；
  2) 端点鉴权：无 token 401；
  3) 端点生成：登录后 POST 渲染 HTML → 200 + OOXML magic（PK）+ python-docx
     回读标题/段落/表格一致；空内容 400。
"""
import io
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = Path(tempfile.mkdtemp(prefix="gk_docx_export_"))
os.environ["AUTH_DB"] = str(TMP / "auth.db")
os.environ["CHAT_DB"] = str(TMP / "chat.db")
os.environ["STUDY_DB"] = str(TMP / "study.db")
os.environ["DB_PATH"] = str(TMP / "vec")
os.environ["DATA_DIR"] = str(TMP / "data")
os.environ["MODEL_CACHE"] = str(TMP / "data" / "models")
os.environ["EMBED_PROVIDER"] = "selftest"

ok_count = 0
fail_count = 0


def check(name, cond, detail=""):
    global ok_count, fail_count
    if cond:
        print(f"  ✅ {name}")
        ok_count += 1
    else:
        print(f"  ❌ {name}" + (f"  | {detail}" if detail else ""))
        fail_count += 1


from fastapi.testclient import TestClient  # noqa: E402

from poc import api  # noqa: E402
from poc.api import _html_to_blocks  # noqa: E402

client = TestClient(api.app)

print("[1] HTMLParser 结构提取")
html = ('<h2>模考成绩趋势</h2><div class="item"><p><b>09-01</b> 行测 85 分</p>'
        '<ul><li>判断推理 90%</li><li>资料分析 80%</li></ul></div>'
        '<table><tr><th>日期</th><th>得分</th></tr><tr><td>09-01</td><td>85</td></tr></table>'
        '<p>结论：稳中有升&nbsp;&amp; 持续进步<script>evil()</script></p>')
blocks = _html_to_blocks(html)
check("标题提取", ("h", 2, "模考成绩趋势") in blocks)
check("段落提取", ("p", "09-01 行测 85 分") in blocks)
check("列表项×2", ("li", "判断推理 90%") in blocks and ("li", "资料分析 80%") in blocks)
check("表格 2×2", ("table", [["日期", "得分"], ["09-01", "85"]]) in blocks)
check("实体解码+script 剔除",
      ("p", "结论：稳中有升 & 持续进步") in blocks
      and all("evil" not in str(b) for b in blocks))
check("坏 HTML 退化不抛异常",
      _html_to_blocks('<p>未闭合 <div>混<BAD TAG>乱</p></BAD>')
      and len(_html_to_blocks('<p>未闭合 <div>混<BAD TAG>乱</p></BAD>')) >= 1)
check("空输入→空块", _html_to_blocks("") == [])
check("纯文本→单段", _html_to_blocks("只有一段文字") == [("p", "只有一段文字")])

print("[2] 端点鉴权")
r = client.post("/me/export/docx", json={"title": "测试", "html": "<p>x</p>"})
check("无 token → 401", r.status_code == 401)

print("[3] 登录后生成 DOCX")
uid = api.auth_store.register("导出用户", "userpass1")
tok = api.auth_store.login("导出用户", "userpass1")
H = {"Authorization": "Bearer " + tok, "X-Auth-Token": tok}
r = client.post("/me/export/docx", headers=H,
                json={"title": "学习报告", "html": html})
check("HTTP 200", r.status_code == 200)
check("OOXML magic(PK)", r.content[:2] == b"PK")
check("Content-Type docx",
      "wordprocessingml" in r.headers.get("content-type", ""))
check("文件名头", "export.docx" in r.headers.get("content-disposition", ""))

from docx import Document  # noqa: E402
d2 = Document(io.BytesIO(r.content))
paras = [p.text for p in d2.paragraphs if p.text.strip()]
check("标题在文档中", any("学习报告" in p for p in paras))
check("小节标题在文档中", any("模考成绩趋势" in p for p in paras))
check("表格回读一致", len(d2.tables) == 1
      and d2.tables[0].rows[1].cells[1].text == "85")
check("列表项回读", any("判断推理 90%" in p for p in paras))

r2 = client.post("/me/export/docx", headers=H, json={"title": "空", "html": "  "})
check("空内容 → 400", r2.status_code == 400)

print(f"\n{'=' * 24}\n通过 {ok_count} · 失败 {fail_count}")
sys.exit(1 if fail_count else 0)
