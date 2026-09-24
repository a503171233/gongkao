# -*- coding: utf-8 -*-
"""问题3+4 后端验证：知识体系 KnowledgeStore（CRUD/树检索/批量/环检测/导图转换）。
运行: cd backend && python tests/test_knowledge.py
"""
import os
import sys
import tempfile
from pathlib import Path
import xml.etree.ElementTree as ET

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk-kn-")
os.environ["CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["DB_PATH"] = os.path.join(TMP, "vec")
os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["MODEL_CACHE"] = os.path.join(TMP, "data", "models")
os.environ["STUDY_DB"] = os.path.join(TMP, "study.db")
os.environ["EMBED_PROVIDER"] = "selftest"

from poc.knowledge import KnowledgeStore  # noqa: E402

ks = KnowledgeStore()
ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(extra)[:250]) if extra and not cond else ""))
    if not cond:
        ok = False


def _flatten(nodes):
    for n in nodes:
        yield n
        yield from _flatten(n.get("children", []))


# ---------- 1. 创建与树结构 ----------
a = ks.create_node("T001", "言语理解", category="行测")
b = ks.create_node("T001", "判断推理", category="行测")
a1 = ks.create_node("T001", "主旨概括", parent_id=a["node_id"])
a2 = ks.create_node("T001", "意图判断", parent_id=a["node_id"])
a11 = ks.create_node("T001", "转折关系", parent_id=a1["node_id"], description="转折之后是重点")
check("创建: 返回 node_id 前缀 KN", all(x["node_id"].startswith("KN") for x in (a, b, a1, a2, a11)))

tree = ks.get_tree("T001")
check("树: 2 个根节点", len(tree["nodes"]) == 2 and tree["total"] == 5, str(tree)[:200])
node_a = next(n for n in tree["nodes"] if n["node_id"] == a["node_id"])
check("树: 层级嵌套正确（a→a1→a11）",
      len(node_a["children"]) == 2
      and node_a["children"][0]["children"][0]["name"] == "转折关系")

# ---------- 2. 老师隔离 ----------
c_t2 = ks.create_node("T002", "T002根节点")
tree_t2 = ks.get_tree("T002")
check("隔离: T002 只见自己节点", tree_t2["total"] == 1 and tree_t2["nodes"][0]["name"] == "T002根节点")
try:
    ks.create_node("T002", "坏节点", parent_id=a["node_id"])
    check("隔离: 跨老师父节点拒绝", False)
except ValueError as e:
    check("隔离: 跨老师父节点拒绝", "父节点不属于该老师" in str(e))

# ---------- 3. 编辑 ----------
upd = ks.update_node(a11["node_id"], name="转折关系与重点识别", description="更新后的说明")
check("编辑: name/description 生效", upd["name"] == "转折关系与重点识别" and upd["description"] == "更新后的说明")
try:
    ks.update_node(a11["node_id"], name="  ")
    check("编辑: 空名称拒绝", False)
except ValueError:
    check("编辑: 空名称拒绝", True)

# ---------- 4. 移动与环检测 ----------
ks.update_node(a2["node_id"], parent_id=a1["node_id"])  # a2 移到 a1 下
tree = ks.get_tree("T001")
node_a = next(n for n in tree["nodes"] if n["node_id"] == a["node_id"])
check("移动: a2 变 a1 子节点", len(node_a["children"]) == 1 and len(node_a["children"][0]["children"]) == 2)

try:
    ks.update_node(a["node_id"], parent_id=a11["node_id"])
    check("环检测: 祖先移到子孙下拒绝", False)
except ValueError as e:
    check("环检测: 祖先移到子孙下拒绝", "环" in str(e))
try:
    ks.update_node(a["node_id"], parent_id=a["node_id"])
    check("环检测: 移到自身拒绝", False)
except ValueError:
    check("环检测: 移到自身拒绝", True)

# ---------- 5. 删除（级联） ----------
d1 = ks.create_node("T001", "待删父", parent_id=b["node_id"])
d2 = ks.create_node("T001", "待删子", parent_id=d1["node_id"])
d3 = ks.create_node("T001", "待删孙", parent_id=d2["node_id"])
r = ks.delete_node(d1["node_id"])
check("删除: 级联删子树3节点", r["deleted"] == 3, r)
tree = ks.get_tree("T001")
all_names = [n["name"] for n in _flatten(tree["nodes"])]
check("删除: 子树节点消失", "待删父" not in all_names and "待删孙" not in all_names)
try:
    ks.delete_node("KNnotexist")
    check("删除: 不存在节点 404", False)
except ValueError:
    check("删除: 不存在节点 404", True)

# ---------- 6. 检索 ----------
ks.create_node("T001", "加强削弱", parent_id=b["node_id"], description="论证类题目核心考点")
tree_kw = ks.get_tree("T001", keyword="削弱")
names = [n["name"] for n in _flatten(tree_kw["nodes"])]
check("检索: 命中+祖先链保留（加强削弱→判断推理）",
      "加强削弱" in names and "判断推理" in names and "言语理解" not in names, names)
tree_kw2 = ks.get_tree("T001", keyword="不存在的关键词xyz")
check("检索: 无命中返回空树", tree_kw2["total"] == 0 and tree_kw2["nodes"] == [])

# ---------- 7. 批量 ----------
m1 = ks.create_node("T001", "批量1", parent_id=b["node_id"])
m2 = ks.create_node("T001", "批量2", parent_id=b["node_id"])
m1c = ks.create_node("T001", "批量1子", parent_id=m1["node_id"])
r = ks.batch_delete([m1["node_id"], m1c["node_id"]])  # 含父子关系（子树重复）
check("批量删除: 父子重叠去重（删2不删4）", r["deleted"] == 2, r)

r = ks.batch_move([m2["node_id"], a2["node_id"]], a1["node_id"])
check("批量移动: 2节点移入a1", r["moved"] == 2, r)
tree = ks.get_tree("T001")
node_a1 = next(n for n in _flatten(tree["nodes"]) if n["node_id"] == a1["node_id"])
kids = [c["name"] for c in node_a1["children"]]
check("批量移动: 结果正确", "批量2" in kids and "意图判断" in kids, kids)

try:
    ks.batch_move([a1["node_id"]], a11["node_id"])
    check("批量移动: 环检测拒绝", False)
except ValueError:
    check("批量移动: 环检测拒绝", True)
try:
    ks.batch_delete([])
    check("批量: 空列表拒绝", False)
except ValueError:
    check("批量: 空列表拒绝", True)

# ---------- 8. 统计 ----------
st = ks.stats("T001")
check("统计: total 与分类", st["total"] == len(list(_flatten(ks.get_tree('T001')['nodes'])))
      and any(c["category"] == "行测" for c in st["categories"]), st)

# ---------- 9. 导图转换 ----------
md = ks.to_markdown("T001", title="言语知识体系")
check("Markdown: 标题+缩进层级", md.startswith("# 言语知识体系")
      and "- 主旨概括" in md and "  - " in md and "- **转折关系与重点识别**" in md, md[:200])
check("Markdown: description 附注", "更新后的说明" in md)

mm = ks.to_freemind("T001", title="言语知识体系")
try:
    root = ET.fromstring(mm)
    check("FreeMind: 合法 XML 且根节点正确",
          root.tag == "map" and root[0].attrib.get("TEXT") == "言语知识体系", mm[:150])
    texts = [n.attrib.get("TEXT") for n in root[0].iter("node")]
    check("FreeMind: 全部节点导出", "主旨概括" in texts and "批量2" in texts, texts)
except ET.ParseError as e:
    check("FreeMind: 合法 XML", False, str(e))

# 子树导出（root_id=a 的子树）
md_sub = ks.to_markdown("T001", root_id=a["node_id"], title="言语理解子树")
check("子树导出: 只含子树节点", "主旨概括" in md_sub and "判断推理" not in md_sub, md_sub[:200])

# XML 注入安全
ks.create_node("T001", '带"引号"<标签>&', parent_id=b["node_id"])
mm2 = ks.to_freemind("T001")
try:
    ET.fromstring(mm2)
    check("FreeMind: 特殊字符转义安全", True)
except ET.ParseError as e:
    check("FreeMind: 特殊字符转义安全", False, str(e))

# 空树导出
md_empty = ks.to_markdown("T999", title="空树")
check("空树导出: 不崩溃只含标题", md_empty.strip() == "# 空树", md_empty)

# ---------- 10. AI 增强编排（mock LLM，不实际调用） ----------
from unittest.mock import patch  # noqa: E402
from poc import admin as admin_biz  # noqa: E402

# mock 指向无 description 的节点（a"言语理解" description 为空 → 在 pending 内）
fake_llm_resp = '[{"node_id": "%s", "description": "考查行测言语核心题型方法"}]' % a["node_id"]
with patch("poc.llm.call_llm", return_value=fake_llm_resp):
    r = admin_biz.enhance_mindmap("T001", save_back=False)
check("AI增强: 返回三格式且 markdown 含增强说明",
      "考查行测言语核心题型方法" in r["markdown"]
      and r["tree"]["children"] and "<map" in r["freemind"],
      str(r)[:200])
check("AI增强: enhanced 计数>0（存在无 description 节点）",
      r["enhanced"] >= 1, r["enhanced"])

# save_back=False 不回写库
node_after = ks.get_node(a["node_id"])
check("AI增强: save_back=False 不回写", node_after["description"] == "",
      node_after["description"])

with patch("poc.llm.call_llm", return_value=fake_llm_resp):
    r2 = admin_biz.enhance_mindmap("T001", save_back=True)
node_after2 = ks.get_node(a["node_id"])
check("AI增强: save_back=True 回写库", node_after2["description"] == "考查行测言语核心题型方法",
      node_after2["description"])

# LLM 解析失败容错（导图仍可纯结构生成）
with patch("poc.llm.call_llm", return_value="这不是JSON"):
    r3 = admin_biz.enhance_mindmap("T001")
check("AI增强: LLM输出异常时纯结构降级生成",
      "# " in r3["markdown"] and "<map" in r3["freemind"], str(r3)[:150])

# _parse_mindmap_enhance 容错
check("AI增强: ```json 包裹解析", admin_biz._parse_mindmap_enhance(
    '```json\n[{"node_id":"KN1","description":"x"}]\n```') == {"KN1": "x"})
check("AI增强: 垃圾输入返回空dict", admin_biz._parse_mindmap_enhance("垃圾") == {})

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
