# -*- coding: utf-8 -*-
"""#17 智能组卷路由（按分类/题型/难度/知识点自助组卷 → 在线作答 → 自动评分）。"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .. import deps as _deps

router = APIRouter()


class SmartExamCreateReq(BaseModel):
    title: str = ""
    teacher_id: str = "T001"
    category: str = ""          # 课程分类（''=不限）
    qtype: str = ""             # choice/judge/essay（''=不限）
    difficulty: int | None = None   # 1~5（None=不限）
    knowledge_point: str = ""   # 知识点（''=不限）
    count: int = 10             # 目标题数


class SmartExamSubmitReq(BaseModel):
    answers: dict = {}          # {"<question_id>": "答案"}


def _smartexam_owner(paper_id: int, user_id: str) -> dict:
    p = _deps.smartexam_store.get_paper_for_user(paper_id, user_id)
    if p is None:
        raise HTTPException(status_code=404, detail="试卷不存在")
    return p


@router.post("/smartexam/generate")
def smartexam_generate(req: SmartExamCreateReq, authorization: str = Header(default="")):
    """智能组卷：按条件从题库随机抽题生成空白卷（答案快照服务端留存，作答后判分）。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    if req.difficulty is not None and not (1 <= req.difficulty <= 5):
        raise HTTPException(status_code=400, detail="难度必须在 1~5 之间")
    count = max(1, min(100, int(req.count or 10)))
    try:
        _deps.cfg.get_teacher(req.teacher_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"老师不存在: {req.teacher_id}")

    questions = _deps.study_store.sample_questions(
        req.teacher_id,
        category=req.category or "",
        qtype=req.qtype or "",
        difficulty=req.difficulty,
        knowledge_point=req.knowledge_point or "",
        count=count,
    )
    if not questions:
        raise HTTPException(status_code=400, detail="题库中没有符合条件的题目，请放宽筛选条件")

    title = (req.title or "").strip() or f"{req.teacher_id} 智能组卷 · {len(questions)} 题"
    config = {
        "teacher_id": req.teacher_id,
        "category": req.category or "",
        "qtype": req.qtype or "",
        "difficulty": req.difficulty,
        "knowledge_point": req.knowledge_point or "",
        "count": count,
    }
    pid = _deps.smartexam_store.create_paper(user["user_id"], title, req.teacher_id,
                                             config, questions)
    paper = _deps.smartexam_store.get_paper(pid)
    # 下发时剥除答案/解析
    paper["questions"] = [_deps.smartexam_store._strip_answer(q) for q in paper["questions"]]
    return paper


@router.get("/smartexam/papers")
def smartexam_list(limit: int = 50, offset: int = 0, authorization: str = Header(default="")):
    """我的组卷列表。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    total, papers = _deps.smartexam_store.list_papers(user["user_id"], limit, offset)
    return {"total": total, "papers": papers}


@router.get("/smartexam/papers/{paper_id}")
def smartexam_detail(paper_id: int, authorization: str = Header(default="")):
    """组卷详情（作答/已评分视图）。作答中剥答案；已评分返回作答与得分。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    p = _smartexam_owner(paper_id, user["user_id"])
    if p["status"] != "graded":
        p["questions"] = [_deps.smartexam_store._strip_answer(q) for q in p["questions"]]
    return p


@router.post("/smartexam/papers/{paper_id}/submit")
def smartexam_submit(paper_id: int, req: SmartExamSubmitReq, authorization: str = Header(default="")):
    """提交作答 → 客观题自动评分。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    _smartexam_owner(paper_id, user["user_id"])
    try:
        result = _deps.smartexam_store.submit_paper(paper_id, req.answers or {})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.delete("/smartexam/papers/{paper_id}")
def smartexam_delete(paper_id: int, authorization: str = Header(default="")):
    """删除组卷。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    _smartexam_owner(paper_id, user["user_id"])
    _deps.smartexam_store.delete_paper(paper_id)
    return {"deleted": True}