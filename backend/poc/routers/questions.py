# -*- coding: utf-8 -*-
"""题库管理路由：题目 CRUD / 查重 / 批量、题库维度统计、AI 采集（解析/提取/入库）、
AI 补分类与解析、知识点清单、题目配图静态服务（公开只读）。

静态路径（/stats、/dup、/knowledge-points、/ai-categorize、/ai-analysis-batch）
与动态路径（/{qid:int}）由 int 转换器天然区分，顺序不影响匹配。
"""

import os as _os
import re as _re

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import deps as _deps
from .. import admin as admin_biz

router = APIRouter()


def _knowledge_store():
    from ..knowledge import get_knowledge_store
    return get_knowledge_store()


# ---------- 题库管理（#13 · #24 R2 分类关联） ----------
@router.get("/admin/teachers/qb-stats")
def admin_teachers_qb_stats(authorization: str = Header(default="")):
    """#26 R3 每老师题库题目数汇总（老师管理速览列）。"""
    _deps._admin_user(authorization)
    return {"counts": _deps.study_store.question_count_by_teacher()}


class QuestionReq(BaseModel):
    """题库题目请求体。"""
    teacher_id: str = "T001"
    qtype: str = "choice"      # choice | judge | essay
    question: str = ""
    options: list[str] = []
    answer: str = ""
    analysis: str = ""
    difficulty: int = 1
    knowledge_point: str = ""
    category_id: str = ""      # #24 R2：关联题型树节点
    category: str = ""         # #35 R1：课程大类（8 枚举，空=未分类）
    source_type: str = ""      # 来源类型（枚举：AI采集/用户提供/公开题库/文档库/互联网搜索）
    source: str = ""           # 来源出处（如"2025年国考行测执法卷第25题"）


@router.get("/admin/questions")
def admin_list_questions(
    teacher_id: str = "T001",
    qtype: str = "",
    difficulty: int | None = None,
    knowledge_point: str = "",
    keyword: str = "",
    category_id: str = "",
    uncategorized: bool = False,
    category: str = "",
    source_type: str = "",
    limit: int = 100,
    offset: int = 0,
    authorization: str = Header(default=""),
):
    """题库列表（管理端）。题型/难度/知识点/关键词/题型分类/课程分类/来源筛选 + 分页。
    #24 R2：category_id 自动展开子树（父类包含全部子孙类题目）；uncategorized=true 筛选未关联分类。
    #35 R1：category 为课程大类筛选（''=全部；'__uncat__'=未分类）；source_type 为来源筛选。"""
    _deps._admin_user(authorization)
    category_ids: list[str] | None = None
    if uncategorized:
        category_ids = [""]
    elif category_id:
        try:
            category_ids = _knowledge_store().subtree_ids(teacher_id, category_id)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"分类节点不存在: {category_id}")
    return {
        "questions": _deps.study_store.list_questions(
            teacher_id, qtype or None, limit, offset,
            difficulty, knowledge_point, keyword, category_ids=category_ids,
            category=category, source_type=source_type),
        "limit": limit,
        "offset": offset,
    }


@router.post("/admin/questions")
def admin_add_question(
    req: QuestionReq = Body(default_factory=QuestionReq),
    authorization: str = Header(default=""),
):
    """新增题库题目（管理端）。#24 R2：可关联题型树节点（category_id）。
    #35 R1：category 课程大类（8 枚举；空/非法兜底"综合"）。"""
    _deps._admin_user(authorization)
    try:
        return _deps.study_store.add_question(
            req.teacher_id, req.qtype, req.question, req.answer,
            req.options, req.analysis, req.difficulty, req.knowledge_point,
            req.category_id, category=req.category,
            source_type=req.source_type, source=req.source,
            sensitive_check=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/admin/questions/{qid:int}")
def admin_delete_question(
    qid: int,
    authorization: str = Header(default=""),
):
    """删除题库题目（管理端）。"""
    _deps._admin_user(authorization)
    ok = _deps.study_store.delete_question(qid)
    if not ok:
        raise HTTPException(status_code=404, detail=f"题目不存在: {qid}")
    return {"deleted": qid}


@router.get("/admin/questions/dup")
def admin_check_duplicates(
    teacher_id: str = "T001",
    text: str = "",
    authorization: str = Header(default=""),
):
    """#26 R2 题干查重：同老师下精确/规范化等值重复题列表（查重/录入预警用）。"""
    _deps._admin_user(authorization)
    dups = _deps.study_store.find_duplicates(teacher_id, text)
    return {"duplicates": dups, "count": len(dups)}


@router.get("/admin/questions/{qid:int}")
def admin_get_question(
    qid: int,
    authorization: str = Header(default=""),
):
    """#26 R2 单题详情（管理端编辑预填用）。"""
    _deps._admin_user(authorization)
    try:
        return _deps.study_store.get_question(qid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class AdminQuestionUpdateReq(BaseModel):
    qtype: str | None = None
    question: str | None = None
    options: list[str] | None = None
    answer: str | None = None
    analysis: str | None = None
    difficulty: int | None = None
    knowledge_point: str | None = None
    category_id: str | None = None
    category: str | None = None   # #35 R1：课程大类（''=清除，非法值 400）
    source_type: str | None = None  # 来源类型（枚举）
    source: str | None = None       # 来源出处


@router.put("/admin/questions/{qid:int}")
def admin_update_question(
    qid: int,
    req: AdminQuestionUpdateReq = Body(default_factory=AdminQuestionUpdateReq),
    authorization: str = Header(default=""),
):
    """#26 R2 编辑题目：仅更新传入字段。"""
    _deps._admin_user(authorization)
    try:
        return _deps.study_store.update_question(qid, **req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/questions/{qid:int}/copy")
def admin_copy_question(
    qid: int,
    authorization: str = Header(default=""),
):
    """#26 R2 复制题目：按原题内容新增一条。"""
    _deps._admin_user(authorization)
    try:
        return _deps.study_store.copy_question(qid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class AdminQuestionsBatchReq(BaseModel):
    qids: list[int] = []


@router.post("/admin/questions/batch")
def admin_delete_questions_batch(
    req: AdminQuestionsBatchReq = Body(default_factory=AdminQuestionsBatchReq),
    authorization: str = Header(default=""),
):
    """#25 批量删除题库题目（管理端）：qids 数组，返回 {deleted, missing}。"""
    _deps._admin_user(authorization)
    try:
        return _deps.study_store.delete_questions_batch(req.qids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ================================================================
# #16 后台重构 · 新增端点（题库 AI 采集）
# ================================================================

class AdminQuestionExtractReq(BaseModel):
    teacher_id: str = "T001"
    text: str = ""
    doc_name: str = ""


@router.get("/admin/questions/stats")
def admin_questions_stats(
    teacher_id: str = "",
    authorization: str = Header(default=""),
):
    """题库维度统计（题型/难度/知识点分布）。"""
    _deps._admin_user(authorization)
    return _deps.study_store.question_stats(teacher_id or None)


# ---------- #33 R2：题目配图静态服务（公开只读，内容寻址不可变） ----------

_QIMG_FNAME_RE = _re.compile(r"^[a-f0-9]{12}\.(png|jpe?g|gif|webp)$")
_QIMG_TID_RE = _re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_QIMG_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp"}


@router.get("/qimg/{teacher_id}/{fname}")
def get_qimg(teacher_id: str, fname: str):
    """题目配图（#33 R2）：公开只读。前端/学生端通过 /api/qimg/... 访问。

    双防穿越：① fname 正则白名单（sha1 前 12 位十六进制 + 受限扩展名）
              ② teacher_id 字符白名单 + resolve() 后前缀校验。
    图片内容寻址（同名必同内容）→ 长缓存 immutable。"""
    tid = (teacher_id or "").strip()
    name = (fname or "").strip()
    if not _QIMG_TID_RE.match(tid) or not _QIMG_FNAME_RE.match(name):
        raise HTTPException(status_code=404, detail="图片不存在")
    base = (_deps.cfg.data_dir / "qimg").resolve()
    fp = (base / tid / name).resolve()
    if not str(fp).startswith(str(base) + _os.sep) or not fp.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(
        fp, media_type=_QIMG_MIME.get(fp.suffix.lower(), "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.post("/admin/questions/parse-file")
async def admin_parse_collect_file(
    file: UploadFile = File(...),
    teacher_id: str = Form(default=""),
    authorization: str = Header(default=""),
):
    """AI 采集文件上传解析（#19）：md/txt/docx/pptx/pdf → 文本 + 内联图片标记。
    仅解析不入库；返回 {doc_name, chars, text, n_img, images}，前端填充后走 /extract 提取。
    #33 R2：teacher_id 用于图片落盘目录 data/qimg/{teacher_id}/（内容寻址幂等）。"""
    _deps._admin_user(authorization)
    if teacher_id:
        _deps._check_teacher(teacher_id)
    try:
        data = await file.read()
        return admin_biz.parse_collect_file(file.filename, data, teacher_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/questions/extract")
def admin_extract_questions(
    req: AdminQuestionExtractReq = Body(default_factory=AdminQuestionExtractReq),
    authorization: str = Header(default=""),
):
    """AI 提取题目（仅返回结构化结果，不落库；由前端确认后走批量入库）。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(req.teacher_id)
    subject = ""
    try:
        tcfg = _deps._check_teacher(req.teacher_id)
        subject = getattr(tcfg, "teacher_subject", "")
    except Exception:
        pass
    try:
        return admin_biz.extract_questions_ai(req.text, subject)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class AdminQuestionsImportReq(BaseModel):
    teacher_id: str = "T001"
    questions: list[dict] = []


@router.post("/admin/questions/import")
def admin_import_questions(
    req: AdminQuestionsImportReq = Body(default_factory=AdminQuestionsImportReq),
    authorization: str = Header(default=""),
):
    """批量导入题目（AI 采集确认后调用）。#10 入库敏感词守卫：命中即过滤不入库。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(req.teacher_id)
    result = _deps.study_store.add_questions_batch(
        req.teacher_id, req.questions, sensitive_check=True)
    return result


class AdminAiCategorizeReq(BaseModel):
    teacher_id: str = "T001"
    qids: list[int] = []
    limit: int = 50


@router.post("/admin/questions/ai-categorize")
def admin_ai_categorize(
    req: AdminAiCategorizeReq = Body(default_factory=AdminAiCategorizeReq),
    authorization: str = Header(default=""),
):
    """#35 R1 批量 AI 补充课程分类：qids 为空时自动取未分类题目（≤50/次）。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(req.teacher_id)
    try:
        return admin_biz.categorize_questions_ai(
            req.teacher_id, req.qids or None, req.limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/questions/{qid:int}/ai-analysis")
def admin_ai_analysis(qid: int, authorization: str = Header(default="")):
    """#35 R3 单题生成多角度 AI 解析并覆盖落库。返回 {analysis}。"""
    _deps._admin_user(authorization)
    try:
        q = _deps.study_store.get_question(qid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    try:
        text = admin_biz.generate_ai_analysis(q)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _deps.study_store.update_question(qid, analysis=text)
    return {"id": qid, "analysis": text}


class AdminAiAnalysisBatchReq(BaseModel):
    teacher_id: str = "T001"
    limit: int = 20


@router.post("/admin/questions/ai-analysis-batch")
def admin_ai_analysis_batch(
    req: AdminAiAnalysisBatchReq = Body(default_factory=AdminAiAnalysisBatchReq),
    authorization: str = Header(default=""),
):
    """#35 R3 批量给无解析题目生成 AI 解析（≤20/次，防超时；可多次点击分批补齐）。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(req.teacher_id)
    limit = max(1, min(20, int(req.limit or 20)))
    with _deps.study_store._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM question_bank WHERE (teacher_id=? OR teacher_id='SHARED') "
            "AND (analysis='' OR analysis IS NULL) "
            # 无答案题跳过：缺答案时 LLM 只能猜，生成解析等于伪造标准答案；
            # 答案待核（answer_status='pending'）同理，避免把错答案写进解析
            "AND TRIM(COALESCE(answer,''))<>'' "
            "AND COALESCE(answer_status,'')<>'pending' ORDER BY id LIMIT ?",
            (req.teacher_id, limit)).fetchall()
    qs = [dict(r) for r in rows]
    if not qs:
        return {"generated": 0, "failed": 0, "total": 0,
                "message": "没有待生成解析的题目（全部题目已有解析）"}
    generated = 0
    failed = 0
    for q in qs:
        try:
            text = admin_biz.generate_ai_analysis(q)
            _deps.study_store.update_question(int(q["id"]), analysis=text)
            generated += 1
        except Exception:  # noqa: BLE001  单题失败不中断批量
            failed += 1
    return {"generated": generated, "failed": failed, "total": len(qs)}


@router.get("/admin/questions/knowledge-points")
def admin_question_knowledge_points(
    teacher_id: str = "T001",
    authorization: str = Header(default=""),
):
    """某老师已有点知识清单（题库筛选下拉用）。"""
    _deps._admin_user(authorization)
    from ..study import get_study_store as _gss
    st = _gss()
    stats = st.question_stats(teacher_id)
    return {"points": [x["kp"] for x in stats["by_knowledge_point"]]}
