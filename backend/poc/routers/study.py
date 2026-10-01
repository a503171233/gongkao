# -*- coding: utf-8 -*-
"""学员增值功能路由：收藏、错题、复习、练习、激励、学习报告、主观题 AI 批改。"""

import json
import re as _re

from fastapi import APIRouter, Body, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .. import deps as _deps

router = APIRouter()


# ---------- 收藏 ----------
@router.post("/favorites")
def add_favorite(
    question: str = Body(...),
    answer: str = Body(...),
    teacher_id: str = Body("T001"),
    refs: list[dict] = Body([]),
    session_id: str = Body(""),
    authorization: str = Header(default=""),
):
    """收藏当前问答。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    fav = _deps.study_store.add_favorite(user["user_id"], teacher_id, question, answer, refs, session_id)
    return fav


@router.get("/favorites")
def list_favorites(
    teacher_id: str = "",
    authorization: str = Header(default=""),
):
    """我的收藏列表。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    tid = teacher_id or None
    return {"favorites": _deps.study_store.list_favorites(user["user_id"], tid)}


@router.delete("/favorites/{fav_id}")
def delete_favorite(
    fav_id: int,
    authorization: str = Header(default=""),
):
    """取消收藏。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    ok = _deps.study_store.delete_favorite(user["user_id"], fav_id)
    return {"deleted": fav_id, "success": ok}


@router.get("/favorites/export")
def export_favorites(authorization: str = Header(default="")):
    """收藏导出 CSV（含 BOM，Excel 直接打开）。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    csv = _deps.study_store.export_favorites_csv(user["user_id"])
    return PlainTextResponse(csv, media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=favorites.csv"})


# ---------- 错题 ----------
@router.post("/mistakes")
def add_mistake(
    question: str = Body(...),
    user_answer: str = Body(""),
    correct_note: str = Body(...),
    teacher_id: str = Body("T001"),
    refs: list[dict] = Body([]),
    session_id: str = Body(""),
    question_id: int | None = Body(None),
    authorization: str = Header(default=""),
):
    """记录错题。question_id 关联题库原题（#36 错题重练）。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")

    mist = _deps.study_store.add_mistake(user["user_id"], teacher_id, question,
                                    user_answer, correct_note, refs, session_id,
                                    question_id=question_id)
    return mist


@router.get("/mistakes")
def list_mistakes(
    teacher_id: str = "",
    authorization: str = Header(default=""),
):
    """错题本。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    tid = teacher_id or None
    return {"mistakes": _deps.study_store.list_mistakes(user["user_id"], tid)}


@router.delete("/mistakes/{mist_id}")
def delete_mistake(
    mist_id: int,
    authorization: str = Header(default=""),
):
    """删除错题。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    ok = _deps.study_store.delete_mistake(user["user_id"], mist_id)
    return {"deleted": mist_id, "success": ok}


@router.get("/mistakes/export")
def export_mistakes(authorization: str = Header(default="")):
    """错题本导出 CSV（含 BOM，Excel 直接打开）。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    csv = _deps.study_store.export_mistakes_csv(user["user_id"])
    return PlainTextResponse(csv, media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=mistakes.csv"})


# ---------- #16 错题强化重练：艾宾浩斯复习节奏 ----------
@router.get("/review/due")
def list_review_due(
    teacher_id: str = "",
    authorization: str = Header(default=""),
):
    """今日待巩固错题队列（艾宾浩斯到期题）。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    tid = teacher_id or None
    return _deps.study_store.list_due_reviews(user["user_id"], tid)


@router.get("/review/stats")
def review_stats(authorization: str = Header(default="")):
    """错题复习概览：待巩固数 / 队列总数 / 下次复习时间。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return _deps.study_store.review_stats(user["user_id"])


@router.get("/mistakes/groups")
def mistake_groups(
    teacher_id: str = "",
    authorization: str = Header(default=""),
):
    """#16 按知识点归组错题（knowledge_point/category）。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    tid = teacher_id or None
    return {"groups": _deps.study_store.mistake_knowledge_groups(user["user_id"], tid)}


# ---------- 练习 ----------
@router.get("/practice/next")
def get_next_practice(
    teacher_id: str = "T001",
    category: str = "",
    mode: str = "",
    question_id: int | None = None,
    authorization: str = Header(default=""),
):
    """抽一道练习题。匿名也可访问（只读）。
    #36 增强：category 分类专项 / mode=wrong 只刷错题 / question_id 指定题重练。"""
    user = _deps._resolve_user(authorization)
    user_id = user["user_id"] if user else "anonymous"

    practice = _deps.study_store.get_next_practice(
        teacher_id, user_id,
        category=category or None,
        mode=mode or None,
        question_id=question_id,
    )
    return practice


@router.get("/practice/categories")
def practice_categories(teacher_id: str = "T001"):
    """#36 老师题库分类清单（含题量），供练习面板下拉。匿名可访问（只读）。"""
    return {"categories": _deps.study_store.practice_categories(teacher_id)}


@router.get("/practice/report")
def practice_report(
    teacher_id: str = "",
    authorization: str = Header(default=""),
):
    """#36 学情报告：总览 / 分类正确率 / 薄弱点 / 近 7 天趋势 / 主观题批改概况。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return _deps.study_store.practice_report(user["user_id"], teacher_id or None)


@router.post("/practice/submit")
def submit_practice(
    question: str = Body(...),
    answer: str = Body(...),
    score: float | None = Body(None),
    feedback: str = Body(""),
    teacher_id: str = Body("T001"),
    question_id: int | None = Body(None),
    authorization: str = Header(default=""),
):
    """提交练习答案并评分。question_id 命中题库 → 客观题自动判分。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    log = _deps.study_store.submit_practice(
        user["user_id"], teacher_id, question, answer, score, feedback, question_id)
    # #18 激励记账：按 log.created_at 增量积分/连续/成就；异常不阻断练习主流程
    try:
        log["incentive"] = _deps.incentive_store.record_practice(
            user["user_id"], log, _deps.study_store)
    except Exception:  # noqa: BLE001
        log["incentive"] = None
    return log


@router.get("/practice/logs")
def list_practice_logs(
    teacher_id: str = "",
    authorization: str = Header(default=""),
):
    """练习记录列表。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    tid = teacher_id or None
    return {"logs": _deps.study_store.list_practice_logs(user["user_id"], tid)}


@router.get("/practice/stats")
def practice_stats(authorization: str = Header(default="")):
    """我的练习统计（C1 拓展）：总量/客观正确率/按老师/近 7 天趋势。"""
    user = _deps._resolve_user(authorization)
    uid = user["user_id"] if user else "anonymous"
    return _deps.study_store.practice_stats(uid)


# ---------- 激励 ----------
@router.get("/me/incentive")
def my_incentive(authorization: str = Header(default="")):
    """#18 我的激励面板：积分/等级/连续学习/成就/最近流水。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return _deps.incentive_store.summary(user["user_id"], _deps.study_store)


@router.post("/me/checkin")
def daily_checkin(authorization: str = Header(default="")):
    """#18 每日签到：去重、发积分、更新连续天数。已签返回提示不扣积分。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return _deps.incentive_store.daily_checkin(user["user_id"])


@router.get("/me/incentive/board")
def incentive_board(authorization: str = Header(default="")):
    """#18 积分排行榜（前 50 名，附用户名）。"""
    _deps._resolve_user(authorization)
    board = _deps.incentive_store.board(limit=50)
    for row in board:
        u = _deps.auth_store.get_user(row["user_id"])
        row["username"] = u["username"] if u else ""
    return {"board": board, "updated_at": _deps._now_iso()}


# ---------- 学习报告 ----------
@router.get("/me/learning-report")
def my_learning_report(authorization: str = Header(default="")):
    """#20 学习报告：模考成绩趋势（smartexam）+ 学情周报 + 复习/收藏/错题概览。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    uid = user["user_id"]
    result = _deps.study_store.learning_report(uid)
    result["exam"] = _deps.smartexam_store.score_trend(uid)
    return result


# ---------- #36 主观题 AI 批改 ----------
class PracticeGradeReq(BaseModel):
    log_id: int


_ESSAY_GRADE_PROMPT = """你是公务员考试资深阅卷专家。请对学生的主观题作答进行多维度批改。

【题目】
{question}
{ref_block}【学生作答】
{answer}

评分维度与要求：
- 立意：是否准确把握题意、观点是否明确
- 结构：层次是否清晰、逻辑是否完整
- 论证：论据是否充分、说理是否有力
- 语言：表达是否流畅、用词是否规范

请严格按以下 JSON 格式输出，不要输出 JSON 以外的任何内容：
{{
  "scores": {{"立意": 0到100的整数, "结构": 0到100的整数, "论证": 0到100的整数, "语言": 0到100的整数}},
  "total": 0到100的整数,
  "comment": "总评，80字以内",
  "suggestions": ["具体改进建议1", "改进建议2", "改进建议3"],
  "model_answer": "参考范文要点，150字以内"
}}"""


@router.post("/practice/grade")
def grade_practice(req: PracticeGradeReq, authorization: str = Header(default="")):
    """#36 主观题 AI 批改：四维评分（立意/结构/论证/语言）+ 总评 + 建议 + 参考范文。
    幂等：已有批改报告直接返回（cached=true）。"""
    user = _deps._resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    log = _deps.study_store.get_practice_log(user["user_id"], req.log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="练习记录不存在")
    if log.get("grade_report"):
        return {"log_id": req.log_id, "score": log["score"],
                "feedback": log["feedback"], "report": log["grade_report"],
                "cached": True}
    if (log.get("score") is not None and log["score"] >= 0):
        raise HTTPException(status_code=400, detail="客观题自动判分，无需 AI 批改")

    # 题库参考（有则喂给 LLM 提升批改质量）
    ref_block = ""
    if log.get("question_id"):
        q = _deps.study_store.get_question_by_id(log["teacher_id"], log["question_id"])
        if q:
            ref = ""
            if q.get("answer"):
                ref += f"\n【参考答案要点】\n{q['answer']}\n"
            if q.get("analysis"):
                ref += f"\n【参考解析】\n{q['analysis']}\n"
            if ref:
                ref_block = ref

    prompt = _ESSAY_GRADE_PROMPT.format(
        question=log["question"], ref_block=ref_block, answer=log["answer"])
    tcfg = _deps.cfg.get_teacher(log["teacher_id"])
    try:
        out = _deps._grade_llm_call([{"role": "user", "content": prompt}], tcfg)
        try:
            report = _deps._extract_json_obj(out)
        except ValueError:
            # LLM 输出偶发不可解析：自动重试一次（低频高价值操作，双倍成本可接受）
            out = _deps._grade_llm_call([{"role": "user", "content": prompt}], tcfg)
            report = _deps._extract_json_obj(out)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 结构校验与兜底
    scores = report.get("scores") or {}
    dims = {}
    for k in ("立意", "结构", "论证", "语言"):
        v = scores.get(k)
        dims[k] = max(0, min(100, int(v))) if isinstance(v, (int, float)) else 60
    report["scores"] = dims
    total = report.get("total")
    if not isinstance(total, (int, float)):
        total = sum(dims.values()) / 4
    total = max(0, min(100, int(total)))
    report["total"] = total
    if not isinstance(report.get("suggestions"), list) or not report["suggestions"]:
        report["suggestions"] = ["建议结合题目要点分层作答"]
    if not isinstance(report.get("comment"), str) or not report["comment"]:
        report["comment"] = "已完成 AI 批改，详见分项评分。"

    score01 = round(total / 100, 3)
    feedback = f"[AI批改 {total}分] {report['comment']}"
    _deps.study_store.save_grade(user["user_id"], req.log_id, score01, feedback, report)
    return {"log_id": req.log_id, "score": score01, "feedback": feedback,
            "report": report}