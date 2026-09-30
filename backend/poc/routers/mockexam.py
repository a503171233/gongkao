# -*- coding: utf-8 -*-
"""F1 模考解析路由（整卷上传 → 按题型路由老师作答解析）。"""
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import deps as _deps
from .. import admin as admin_biz
from ..mockexam import start_paper_job as _start_mockexam_job

router = APIRouter()


class MockExamTextReq(BaseModel):
    title: str = ""
    exam_type: str = ""
    text: str = Field(..., min_length=10)


def _mockexam_owner_paper(paper_id: int, user_id: str) -> dict:
    paper = _deps.mockexam_store.heal_paper(paper_id)
    if paper is None or paper["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="试卷不存在")
    return paper


@router.post("/mockexam/papers/from-text")
def mockexam_create_from_text(req: MockExamTextReq, authorization: str = Header(default="")):
    """粘贴整卷文本创建模考解析任务（登录用户）。后台线程完成 识别拆题 → 路由老师作答解析。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    text = (req.text or "").strip()
    exam_type = (req.exam_type or "").strip()
    if exam_type and exam_type not in ("行测", "申论"):
        raise HTTPException(status_code=400, detail="exam_type 仅支持：行测 / 申论")
    if len(text) < 20:
        raise HTTPException(status_code=400, detail="试卷内容过短，请粘贴完整的行测/申论试卷")
    title = (req.title or "").strip() or ("行测模考" if exam_type == "行测" else "申论模考")
    pid = _deps.mockexam_store.create_paper(user["user_id"], title, exam_type, "text", "", text)
    _start_mockexam_job(pid)
    return _deps.mockexam_store.heal_paper(pid)


@router.post("/mockexam/papers")
async def mockexam_create_from_file(
    file: UploadFile = File(...),
    title: str = Form(""),
    exam_type: str = Form(""),
    authorization: str = Header(default=""),
):
    """上传整卷文件创建模考解析任务（登录用户）。支持 md/txt/docx/pptx/pdf。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    exam_type = (exam_type or "").strip()
    if exam_type and exam_type not in ("行测", "申论"):
        raise HTTPException(status_code=400, detail="exam_type 仅支持：行测 / 申论")
    try:
        data = await file.read()
        parsed = admin_biz.parse_collect_file(file.filename, data, _deps.cfg.teacher_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    text = parsed["text"]
    if not text:
        raise HTTPException(status_code=400, detail="未能从文件中解析出文本内容")
    doc = parsed.get("doc_name") or "试卷"
    pid = _deps.mockexam_store.create_paper(
        user["user_id"], (title or "").strip() or doc, exam_type, "file", doc, text)
    _start_mockexam_job(pid)
    return _deps.mockexam_store.heal_paper(pid)


@router.get("/mockexam/papers")
def mockexam_list_papers(
    limit: int = 50,
    offset: int = 0,
    authorization: str = Header(default=""),
):
    """我的模考试卷列表（含解析进度）。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    total, papers = _deps.mockexam_store.list_papers(user["user_id"], limit, offset)
    return {"total": total, "papers": papers}


@router.get("/mockexam/papers/{paper_id}")
def mockexam_paper_detail(paper_id: int, authorization: str = Header(default="")):
    """模考试卷详情（状态/进度/汇总）。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return _mockexam_owner_paper(paper_id, user["user_id"])


@router.get("/mockexam/papers/{paper_id}/questions")
def mockexam_paper_questions(paper_id: int, authorization: str = Header(default="")):
    """模考试卷逐题作答解析结果。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    _mockexam_owner_paper(paper_id, user["user_id"])
    return {"questions": _deps.mockexam_store.list_questions(paper_id)}


@router.post("/mockexam/papers/{paper_id}/retry")
def mockexam_retry_paper(paper_id: int, authorization: str = Header(default="")):
    """重新解析：提取失败的试卷重跑拆题；已完成/部分失败的试卷重跑全部作答。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    _mockexam_owner_paper(paper_id, user["user_id"])
    if _deps.mockexam_store.is_running(paper_id):
        raise HTTPException(status_code=409, detail="解析任务正在运行中，请稍后再试")
    if _deps.mockexam_store.count_questions(paper_id) == 0:
        _start_mockexam_job(paper_id, reextract=True)
    else:
        _deps.mockexam_store.reset_questions(paper_id)
        _start_mockexam_job(paper_id)
    return _deps.mockexam_store.heal_paper(paper_id)


@router.delete("/mockexam/papers/{paper_id}")
def mockexam_delete_paper(paper_id: int, authorization: str = Header(default="")):
    """删除模考试卷及其题目解析。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    _mockexam_owner_paper(paper_id, user["user_id"])
    _deps.mockexam_store.delete_paper(paper_id)
    return {"deleted": True}