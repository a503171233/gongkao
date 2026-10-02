# -*- coding: utf-8 -*-
"""C4 · 学员增值功能 — 共享常量与纯函数（从 study.py 拆分）。

限额/共享题库/复习节奏/分类枚举等常量与时间/图片/归一化等纯工具函数；
study.py 继续 re-export，外部 `from poc.study import ...` 用法不变。
"""
import re
from datetime import datetime, timezone


# ---------- 会员限额配置 ----------
FAVORITE_LIMIT_FREE = 50
FAVORITE_LIMIT_MEMBER = -1  # -1 表示不限

# ---------- 共享题库（自动采集的公开题库/联网搜索题目不设老师归属，所有老师可调用） ----------
SHARED_TEACHER_ID = "SHARED"

# ---------- #16 错题强化重练：艾宾浩斯复习节奏 ----------
# 复习间隔（天）：第 n 轮连续答对后，到下一轮复习的间隔。
# 索引 = 已连续答对次数(stage)；stage=1 → 1 天后复习，stage=5 → 30 天后复习，
# stage=6 = 已掌握（移出复习队列）。
REVIEW_INTERVALS_DAYS = [1, 2, 4, 7, 15, 30]
REVIEW_MASTER_STAGE = 6  # 连续答对 6 次即视为已掌握


def _teacher_scope_sql(alias: str = "teacher_id") -> str:
    """按老师查询 + 共享题库并集的 SQL 片段：`(teacher_id=? OR teacher_id='SHARED')`。"""
    return f"({alias}=? OR {alias}='{SHARED_TEACHER_ID}')"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- #33 R2：题目配图（question_bank.images 列） ----------

def _imgs_json(urls) -> str:
    """图片 URL 列表 → images 列 JSON 字符串（仅合法 /api/qimg/ 白名单 URL，去重保序）。"""
    import json
    from .ingest import QIMG_URL_RE

    if isinstance(urls, str):  # 容错：传了单个字符串
        urls = [urls]
    out: list[str] = []
    for u in urls or []:
        if isinstance(u, str) and QIMG_URL_RE.match(u) and u not in out:
            out.append(u)
    return json.dumps(out, ensure_ascii=False) if out else ""


def _parse_images(val) -> list:
    """images 列 JSON → URL 列表（坏值/旧库空值容错为 []）。"""
    if not val:
        return []
    import json
    try:
        v = json.loads(val)
    except (ValueError, TypeError):
        return []
    if not isinstance(v, list):
        return []
    return [u for u in v if isinstance(u, str)]


def _extract_imgs_from_question(question: str) -> list:
    """从题干文本提取合法图片 URL（import 直调未带 images 字段时的兜底）。"""
    from .ingest import extract_img_urls

    return extract_img_urls(question or "")


# #38 R1 申论归一化查重：剔除空白/标点后按全文比较，拦截换行/标点差异造成的近重复
# （仅 essay 主观题使用——题干即全文，选项独立成列不会像 choice 那样误并）。
def _norm_question(text: str) -> str:
    t = re.sub(r"[\s\u3000]+", "", text or "")
    t = re.sub(r"[，。；：、,. ;:,;（）()\"\'‘’“”？！?~～!！【】\[\]「」『』…·-]+", "", t)
    return t.strip()


def _norm_source(s: str) -> str:
    """来源同源归一化：仅去空白，保留 url 结构；同一来源多次采集（含尾斜杠差异）识别为同源。"""
    return "".join((s or "").split())


# #35 R1 课程分类（8 大类，与老师管理 course_category 枚举一致）
QUESTION_CATEGORIES = ["言语理解", "判断推理", "数量关系", "资料分析",
                       "常识判断", "申论", "面试", "综合"]

_CATEGORY_ALIAS = {
    "言语": "言语理解", "言语理解与表达": "言语理解", "片段阅读": "言语理解",
    "逻辑填空": "言语理解", "语句表达": "言语理解",
    "判断": "判断推理", "图形推理": "判断推理", "定义判断": "判断推理",
    "类比推理": "判断推理", "逻辑判断": "判断推理",
    "数量": "数量关系", "数学运算": "数量关系", "数字推理": "数量关系",
    "资料": "资料分析",
    "常识": "常识判断", "公共基础": "常识判断",
    "申论": "申论", "面试": "面试", "综合": "综合", "其他": "综合",
}


def normalize_category(raw) -> str:
    """归一化课程分类：别名映射 → 精确匹配 → 包含匹配 → 兜底空串（不合法返回 ''，由调用方决定兜底值）。"""
    v = str(raw or "").strip()
    if not v:
        return ""
    if v in QUESTION_CATEGORIES:
        return v
    if v in _CATEGORY_ALIAS:
        return _CATEGORY_ALIAS[v]
    for k, tgt in _CATEGORY_ALIAS.items():
        if k in v:
            return tgt
    for c in QUESTION_CATEGORIES:
        if c in v:
            return c
    return ""


# 题目来源（枚举类型 + 可选出处）。source_type 枚举：AI采集 / 用户提供 / 公开题库 / 文档库 / 互联网搜索
QUESTION_SOURCE_TYPES = ["AI采集", "用户提供", "公开题库", "文档库", "互联网搜索"]


def estimate_difficulty(question: str, options=None, qtype: str = "choice",
                        analysis: str = "") -> int:
    """#13 无 LLM 难度标注时的启发式预估（1-5）。
    以题干长度为主、题型/选项数/解析长度为辅，粗略反映题目复杂度（零 LLM 成本）。"""
    q = question or ""
    base = {"judge": 2, "choice": 2, "essay": 3}.get(qtype or "", 2)
    score = base
    qlen = len(q)
    if qlen >= 200:
        score += 2
    elif qlen >= 80:
        score += 1
    if options and isinstance(options, (list, tuple)) and len(options) >= 5:
        score += 1
    if analysis and len(str(analysis)) >= 100:
        score += 1
    return max(1, min(5, score))


def normalize_source_type(raw) -> str:
    """归一化题目来源类型：精确匹配 → 忽略空白匹配 → 兜底空串（由调用方决定默认值）。"""
    v = str(raw or "").strip()
    if not v:
        return ""
    for t in QUESTION_SOURCE_TYPES:
        if v == t:
            return t
    for t in QUESTION_SOURCE_TYPES:
        if v.replace(" ", "") == t.replace(" ", ""):
            return t
    return ""
