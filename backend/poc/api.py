# -*- coding: utf-8 -*-
"""网站二（RAG 内核服务）FastAPI 入口。
多老师支持：所有接口带 teacher_id；SSE 流式输出。
对应说明书 V3.0：网站二职责 = 知识库管理/检索/调度/推理/输出校验。

契约：store/answer 均为 teacher_id 签名（见各模块 docstring）。
"""
import json
import hmac
import os as _os
import re as _re
import secrets
import threading
import time as _time
import uuid
from collections import deque
from pathlib import Path as _Path

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Header, Request, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse, StreamingResponse, JSONResponse, FileResponse
from pydantic import BaseModel, Field

from .auth import AuthStore
from .config import Config
from .gitee import GiteeOAuth
from .ingest import ingest_file
from . import store
from . import admin as admin_biz
from .answer import ask as rag_ask
from .chat import ChatStore
from .guard import guard_counter
from .metrics import metrics
from .models import (
    get_ai_model_store as _get_aimodel,
    builtin_catalog,
    list_remote_models,
)
from .payment import get_payment_store, PLANS
from .sensitive import check_sensitive, SENSITIVE_ENABLED
from .study import get_study_store
from .incentive import get_incentive_store as _get_incentive
from .mockexam import get_mockexam_store as _get_mockexam
from .mockexam import start_paper_job as _start_mockexam_job
from .smartexam import get_smartexam_store as _get_smartexam
from .forum import get_forum_store as _get_forum
from .forum import check_forum_text as _check_forum_text
from .tracking import get_tracking_store as _get_tracking
from .tracking import PROMO_TARGETS as _PROMO_TARGETS
from .banner import get_banner_store as _get_banner
from .banner import is_safe_url as _banner_safe_url
from .messages import get_message_store as _get_messages
from . import stats as stats_biz
from . import autocollect as autocollect_mod

# 共享状态与工厂（由 deps/app 模块集中管理）
from .deps import (
    cfg, chat_store, auth_store, gitee_oauth, payment_store, study_store,
    incentive_store, mockexam_store, smartexam_store, forum_store, tracking_store,
    banner_store, message_store,
    _oauth_states, _STATE_TTL, HISTORY_LIMIT,
    _login_rate_limit, _login_fail_record, _client_ip,
    _CSRF_ALLOWED_ORIGINS, _CSRF_MUTATING,
    _security_rate_limit,
    _sse, _check_teacher, _safe_count,
    _resolve_user, _session_owner_guard, _quota_guard,
    AskReq, RegisterReq, LoginReq,
    ForgotQuestionReq, ForgotAnswerReq, ForgotResetReq, SetSecurityReq,
)
from .app import create_app
app = create_app()


# ========== C1 · 会员付费闭环路由 ==========

class OrderReq(BaseModel):
    """建单请求体（C1 契约：{plan: month|quarter|year}）。"""
    plan: str = "month"


class CodeReq(BaseModel):
    """充值码激活请求体（C1 契约：{code: "..."}）。"""
    code: str = ""


@app.get("/plans")
def plans():
    """定价方案列表（公开接口）。"""
    # 注意：必须转 list，dict.values() 视图对象 FastAPI 无法序列化（jsonable_encoder 会报 vars() 错）
    return {"plans": list(PLANS.values())}


@app.get("/orders/user")
def list_user_orders(authorization: str = Header(default="")):
    """查询当前用户的订单列表。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")

    return {"orders": payment_store.list_orders(user["user_id"])}


@app.post("/orders")
def create_order(req: OrderReq = Body(default_factory=OrderReq),
                 authorization: str = Header(default="")):
    """新建订单（需登录）。阶段一返回充值码激活入口。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")

    try:
        order = payment_store.create_order(user["user_id"], req.plan)
        return order
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/orders/{order_id}")
def get_order(order_id: str, authorization: str = Header(default="")):
    """查询订单状态。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    order = payment_store.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"订单不存在: {order_id}")
    if order["user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权查看此订单")
    
    return order


@app.post("/orders/notify")
async def notify_payment(request: Request):
    """支付回调（验签、幂等、开通会员）。
    #15：验签实现在 payment.handle_payment_notify（PAY_SIGN_MODE/PAY_SIGN_KEY 可配；
    默认 none 放行，配置后校验失败抛 ValueError → 400）。
    """
    # 解析请求体
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体解析失败")

    try:
        result = payment_store.handle_payment_notify(body)
    except ValueError as e:
        # 验签失败/订单异常：统一 400（detail 透出）
        raise HTTPException(status_code=400, detail=str(e))

    # 若订单刚变为 paid，开通会员（pay_channel 非 recharge_code 的第三方单同样处理）
    order = payment_store.get_order(result["order_id"])
    if order and order.get("status") == "paid":
        try:
            auth_store.activate_membership(order["user_id"], 30)
        except Exception:  # noqa: BLE001  激活失败不阻断回调确认
            pass

    return result


@app.get("/me/membership")
def membership_status(authorization: str = Header(default="")):
    """当前用户会员状态。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    return auth_store.get_membership_info(user["user_id"])


@app.post("/orders/recharge-code/activate")
def activate_recharge_code(
    req: CodeReq = Body(default_factory=CodeReq),
    authorization: str = Header(default=""),
):
    """使用充值码激活订单（阶段一主要入口）。
    C1 契约：body 为 JSON 对象 {"code": "..."}（与前端 GK.api JSON 序列化一致）。
    """
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")

    code = (req.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="缺少充值码")

    try:
        order = payment_store.activate_recharge_code(code, user["user_id"], auth_store)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if order is None:
        raise HTTPException(status_code=404, detail="充值码无效或已被使用")

    return {
        **order,
        "membership": auth_store.get_membership_info(user["user_id"]),
    }


# ========== C4 · 学员增值功能路由 ==========

@app.post("/favorites")
def add_favorite(
    question: str = Body(...),
    answer: str = Body(...),
    teacher_id: str = Body("T001"),
    refs: list[dict] = Body([]),
    session_id: str = Body(""),
    authorization: str = Header(default=""),
):
    """收藏当前问答。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    fav = study_store.add_favorite(user["user_id"], teacher_id, question, answer, refs, session_id)
    return fav


@app.get("/favorites")
def list_favorites(
    teacher_id: str = "",
    authorization: str = Header(default=""),
):
    """我的收藏列表。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    tid = teacher_id or None
    return {"favorites": study_store.list_favorites(user["user_id"], tid)}


@app.delete("/favorites/{fav_id}")
def delete_favorite(
    fav_id: int,
    authorization: str = Header(default=""),
):
    """取消收藏。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    ok = study_store.delete_favorite(user["user_id"], fav_id)
    return {"deleted": fav_id, "success": ok}


@app.post("/mistakes")
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
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")

    mist = study_store.add_mistake(user["user_id"], teacher_id, question,
                                   user_answer, correct_note, refs, session_id,
                                   question_id=question_id)
    return mist


@app.get("/mistakes")
def list_mistakes(
    teacher_id: str = "",
    authorization: str = Header(default=""),
):
    """错题本。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    tid = teacher_id or None
    return {"mistakes": study_store.list_mistakes(user["user_id"], tid)}


@app.delete("/mistakes/{mist_id}")
def delete_mistake(
    mist_id: int,
    authorization: str = Header(default=""),
):
    """删除错题。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    ok = study_store.delete_mistake(user["user_id"], mist_id)
    return {"deleted": mist_id, "success": ok}


# ---------- #16 错题强化重练：艾宾浩斯复习节奏 ----------
@app.get("/review/due")
def list_review_due(
    teacher_id: str = "",
    authorization: str = Header(default=""),
):
    """今日待巩固错题队列（艾宾浩斯到期题）。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    tid = teacher_id or None
    return study_store.list_due_reviews(user["user_id"], tid)


@app.get("/review/stats")
def review_stats(authorization: str = Header(default="")):
    """错题复习概览：待巩固数 / 队列总数 / 下次复习时间。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return study_store.review_stats(user["user_id"])


@app.get("/mistakes/groups")
def mistake_groups(
    teacher_id: str = "",
    authorization: str = Header(default=""),
):
    """#16 按知识点归组错题（knowledge_point/category）。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    tid = teacher_id or None
    return {"groups": study_store.mistake_knowledge_groups(user["user_id"], tid)}


@app.get("/practice/next")
def get_next_practice(
    teacher_id: str = "T001",
    category: str = "",
    mode: str = "",
    question_id: int | None = None,
    authorization: str = Header(default=""),
):
    """抽一道练习题。匿名也可访问（只读）。
    #36 增强：category 分类专项 / mode=wrong 只刷错题 / question_id 指定题重练。"""
    user = _resolve_user(authorization)
    user_id = user["user_id"] if user else "anonymous"

    practice = study_store.get_next_practice(
        teacher_id, user_id,
        category=category or None,
        mode=mode or None,
        question_id=question_id,
    )
    return practice


@app.get("/practice/categories")
def practice_categories(teacher_id: str = "T001"):
    """#36 老师题库分类清单（含题量），供练习面板下拉。匿名可访问（只读）。"""
    return {"categories": study_store.practice_categories(teacher_id)}


@app.get("/practice/report")
def practice_report(
    teacher_id: str = "",
    authorization: str = Header(default=""),
):
    """#36 学情报告：总览 / 分类正确率 / 薄弱点 / 近 7 天趋势 / 主观题批改概况。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return study_store.practice_report(user["user_id"], teacher_id or None)


@app.post("/practice/submit")
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
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    log = study_store.submit_practice(
        user["user_id"], teacher_id, question, answer, score, feedback, question_id)
    # #18 激励记账：按 log.created_at 增量积分/连续/成就；异常不阻断练习主流程
    try:
        log["incentive"] = incentive_store.record_practice(
            user["user_id"], log, study_store)
    except Exception:  # noqa: BLE001
        log["incentive"] = None
    return log


@app.get("/practice/logs")
def list_practice_logs(
    teacher_id: str = "",
    authorization: str = Header(default=""),
):
    """练习记录列表。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    
    tid = teacher_id or None
    return {"logs": study_store.list_practice_logs(user["user_id"], tid)}


@app.get("/practice/stats")
def practice_stats(authorization: str = Header(default="")):
    """我的练习统计（C1 拓展）：总量/客观正确率/按老师/近 7 天趋势。"""
    user = _resolve_user(authorization)
    uid = user["user_id"] if user else "anonymous"
    return study_store.practice_stats(uid)


@app.get("/me/incentive")
def my_incentive(authorization: str = Header(default="")):
    """#18 我的激励面板：积分/等级/连续学习/成就/最近流水。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return incentive_store.summary(user["user_id"], study_store)


@app.get("/me/learning-report")
def my_learning_report(authorization: str = Header(default="")):
    """#20 学习报告：模考成绩趋势（smartexam）+ 学情周报 + 复习/收藏/错题概览。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    uid = user["user_id"]
    result = study_store.learning_report(uid)
    result["exam"] = smartexam_store.score_trend(uid)
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


def _grade_llm_call(messages, tcfg, max_tokens: int = 1500) -> str:
    """#36 批改 LLM 调用：注册表默认模型优先，失败回退全局主模型（仿 admin._llm_call）。"""
    from . import llm as llm_biz
    try:
        eff = _get_aimodel().effective_default()
    except Exception:  # noqa: BLE001  注册表异常不阻断
        eff = {}
    if eff:
        try:
            return llm_biz.call_llm(
                messages, tcfg, max_tokens=max_tokens,
                model=eff.get("model_id"), base_url=eff.get("base_url"),
                api_key=eff.get("api_key"), temperature=0.2)
        except ValueError:
            raise
        except Exception:  # noqa: BLE001  eff 通道失败 → 回退全局
            pass
    try:
        return llm_biz.call_llm(messages, tcfg, max_tokens=max_tokens, temperature=0.2)
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"AI 服务暂不可用（{str(e)[:80]}），请稍后重试")


def _extract_json_obj(text: str) -> dict:
    """LLM 输出 → dict：整体解析 → 剥围栏 → raw_decode 扫描首个可解析对象（优先含 scores/total 键）。
    #36 加固：思考型模型输出前后杂文/多 JSON 块场景。"""
    t = text.strip()
    if t.startswith("```"):
        t = _re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = _re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    dec = json.JSONDecoder()
    found = None
    for i, ch in enumerate(t):
        if ch != "{":
            continue
        try:
            obj, _end = dec.raw_decode(t[i:])
        except ValueError:
            continue
        if isinstance(obj, dict):
            if "scores" in obj or "total" in obj:
                return obj
            if found is None:
                found = obj
    if found is not None:
        return found
    raise ValueError("AI 批改返回格式异常，请重试")


@app.post("/practice/grade")
def grade_practice(req: PracticeGradeReq, authorization: str = Header(default="")):
    """#36 主观题 AI 批改：四维评分（立意/结构/论证/语言）+ 总评 + 建议 + 参考范文。
    幂等：已有批改报告直接返回（cached=true）。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    log = study_store.get_practice_log(user["user_id"], req.log_id)
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
        q = study_store.get_question_by_id(log["teacher_id"], log["question_id"])
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
    tcfg = cfg.get_teacher(log["teacher_id"])
    try:
        out = _grade_llm_call([{"role": "user", "content": prompt}], tcfg)
        try:
            report = _extract_json_obj(out)
        except ValueError:
            # LLM 输出偶发不可解析：自动重试一次（低频高价值操作，双倍成本可接受）
            out = _grade_llm_call([{"role": "user", "content": prompt}], tcfg)
            report = _extract_json_obj(out)
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
    study_store.save_grade(user["user_id"], req.log_id, score01, feedback, report)
    return {"log_id": req.log_id, "score": score01, "feedback": feedback,
            "report": report}


# ---------- F1 模考解析（整卷上传 → 按题型路由老师作答解析） ----------
class MockExamTextReq(BaseModel):
    title: str = ""
    exam_type: str = ""
    text: str = Field(..., min_length=10)


def _mockexam_owner_paper(paper_id: int, user_id: str) -> dict:
    paper = mockexam_store.heal_paper(paper_id)
    if paper is None or paper["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="试卷不存在")
    return paper


@app.post("/mockexam/papers/from-text")
def mockexam_create_from_text(req: MockExamTextReq, authorization: str = Header(default="")):
    """粘贴整卷文本创建模考解析任务（登录用户）。后台线程完成 识别拆题 → 路由老师作答解析。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    text = (req.text or "").strip()
    exam_type = (req.exam_type or "").strip()
    if exam_type and exam_type not in ("行测", "申论"):
        raise HTTPException(status_code=400, detail="exam_type 仅支持：行测 / 申论")
    if len(text) < 20:
        raise HTTPException(status_code=400, detail="试卷内容过短，请粘贴完整的行测/申论试卷")
    title = (req.title or "").strip() or ("行测模考" if exam_type == "行测" else "申论模考")
    pid = mockexam_store.create_paper(user["user_id"], title, exam_type, "text", "", text)
    _start_mockexam_job(pid)
    return mockexam_store.heal_paper(pid)


@app.post("/mockexam/papers")
async def mockexam_create_from_file(
    file: UploadFile = File(...),
    title: str = Form(""),
    exam_type: str = Form(""),
    authorization: str = Header(default=""),
):
    """上传整卷文件创建模考解析任务（登录用户）。支持 md/txt/docx/pptx/pdf。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    exam_type = (exam_type or "").strip()
    if exam_type and exam_type not in ("行测", "申论"):
        raise HTTPException(status_code=400, detail="exam_type 仅支持：行测 / 申论")
    try:
        data = await file.read()
        parsed = admin_biz.parse_collect_file(file.filename, data, cfg.teacher_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    text = parsed["text"]
    if not text:
        raise HTTPException(status_code=400, detail="未能从文件中解析出文本内容")
    doc = parsed.get("doc_name") or "试卷"
    pid = mockexam_store.create_paper(
        user["user_id"], (title or "").strip() or doc, exam_type, "file", doc, text)
    _start_mockexam_job(pid)
    return mockexam_store.heal_paper(pid)


@app.get("/mockexam/papers")
def mockexam_list_papers(
    limit: int = 50,
    offset: int = 0,
    authorization: str = Header(default=""),
):
    """我的模考试卷列表（含解析进度）。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    total, papers = mockexam_store.list_papers(user["user_id"], limit, offset)
    return {"total": total, "papers": papers}


@app.get("/mockexam/papers/{paper_id}")
def mockexam_paper_detail(paper_id: int, authorization: str = Header(default="")):
    """模考试卷详情（状态/进度/汇总）。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return _mockexam_owner_paper(paper_id, user["user_id"])


@app.get("/mockexam/papers/{paper_id}/questions")
def mockexam_paper_questions(paper_id: int, authorization: str = Header(default="")):
    """模考试卷逐题作答解析结果。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    _mockexam_owner_paper(paper_id, user["user_id"])
    return {"questions": mockexam_store.list_questions(paper_id)}


@app.post("/mockexam/papers/{paper_id}/retry")
def mockexam_retry_paper(paper_id: int, authorization: str = Header(default="")):
    """重新解析：提取失败的试卷重跑拆题；已完成/部分失败的试卷重跑全部作答。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    _mockexam_owner_paper(paper_id, user["user_id"])
    if mockexam_store.is_running(paper_id):
        raise HTTPException(status_code=409, detail="解析任务正在运行中，请稍后再试")
    if mockexam_store.count_questions(paper_id) == 0:
        _start_mockexam_job(paper_id, reextract=True)
    else:
        mockexam_store.reset_questions(paper_id)
        _start_mockexam_job(paper_id)
    return mockexam_store.heal_paper(paper_id)


@app.delete("/mockexam/papers/{paper_id}")
def mockexam_delete_paper(paper_id: int, authorization: str = Header(default="")):
    """删除模考试卷及其题目解析。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    _mockexam_owner_paper(paper_id, user["user_id"])
    mockexam_store.delete_paper(paper_id)
    return {"deleted": True}


# ---------- #17 智能组卷（按分类/题型/难度/知识点自助组卷 → 在线作答 → 自动评分） ----------
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
    p = smartexam_store.get_paper_for_user(paper_id, user_id)
    if p is None:
        raise HTTPException(status_code=404, detail="试卷不存在")
    return p


@app.post("/smartexam/generate")
def smartexam_generate(req: SmartExamCreateReq, authorization: str = Header(default="")):
    """智能组卷：按条件从题库随机抽题生成空白卷（答案快照服务端留存，作答后判分）。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    if req.difficulty is not None and not (1 <= req.difficulty <= 5):
        raise HTTPException(status_code=400, detail="难度必须在 1~5 之间")
    count = max(1, min(100, int(req.count or 10)))
    try:
        cfg.get_teacher(req.teacher_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"老师不存在: {req.teacher_id}")

    questions = study_store.sample_questions(
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
    pid = smartexam_store.create_paper(user["user_id"], title, req.teacher_id,
                                       config, questions)
    paper = smartexam_store.get_paper(pid)
    # 下发时剥除答案/解析
    paper["questions"] = [smartexam_store._strip_answer(q) for q in paper["questions"]]
    return paper


@app.get("/smartexam/papers")
def smartexam_list(limit: int = 50, offset: int = 0, authorization: str = Header(default="")):
    """我的组卷列表。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    total, papers = smartexam_store.list_papers(user["user_id"], limit, offset)
    return {"total": total, "papers": papers}


@app.get("/smartexam/papers/{paper_id}")
def smartexam_detail(paper_id: int, authorization: str = Header(default="")):
    """组卷详情（作答/已评分视图）。作答中剥答案；已评分返回作答与得分。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    p = _smartexam_owner(paper_id, user["user_id"])
    if p["status"] != "graded":
        p["questions"] = [smartexam_store._strip_answer(q) for q in p["questions"]]
    return p


@app.post("/smartexam/papers/{paper_id}/submit")
def smartexam_submit(paper_id: int, req: SmartExamSubmitReq, authorization: str = Header(default="")):
    """提交作答 → 客观题自动评分。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    _smartexam_owner(paper_id, user["user_id"])
    try:
        result = smartexam_store.submit_paper(paper_id, req.answers or {})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.delete("/smartexam/papers/{paper_id}")
def smartexam_delete(paper_id: int, authorization: str = Header(default="")):
    """删除组卷。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    _smartexam_owner(paper_id, user["user_id"])
    smartexam_store.delete_paper(paper_id)
    return {"deleted": True}


@app.get("/favorites/export")
def export_favorites(authorization: str = Header(default="")):
    """收藏导出 CSV（含 BOM，Excel 直接打开）。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    csv = study_store.export_favorites_csv(user["user_id"])
    return PlainTextResponse(csv, media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=favorites.csv"})


@app.get("/mistakes/export")
def export_mistakes(authorization: str = Header(default="")):
    """错题本导出 CSV（含 BOM，Excel 直接打开）。"""
    user = _resolve_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    csv = study_store.export_mistakes_csv(user["user_id"])
    return PlainTextResponse(csv, media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=mistakes.csv"})


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str, authorization: str = Header(default="")):
    """删除会话及其消息。越权防护：归属校验（防他人会话被匿名删除）。"""
    sess = chat_store.get_session(session_id)
    if sess is None:
        return {"deleted": session_id, "success": False}
    _session_owner_guard(sess, _resolve_user(authorization))
    chat_store.delete_session(session_id)
    return {"deleted": session_id, "success": True}


# ---------- A1 答案反馈 ----------
class FeedbackReq(BaseModel):
    """答案反馈请求体（A1 契约：{rating: up|down, question, answer, reason?, session_id?, teacher_id?}）。"""
    rating: str = "up"
    question: str = ""
    answer: str = ""
    reason: str = ""
    session_id: str = ""
    teacher_id: str = "T001"


@app.post("/feedback")
def submit_feedback(
    req: FeedbackReq = Body(default_factory=FeedbackReq),
    authorization: str = Header(default=""),
):
    """提交答案反馈（赞/踩）。匿名用户也可反馈（user_id=anonymous）。"""
    user = _resolve_user(authorization)
    uid = user["user_id"] if user else "anonymous"
    try:
        return study_store.add_feedback(
            uid, req.teacher_id, req.session_id,
            req.question[:500], req.answer[:2000],
            req.rating, req.reason[:200],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/documents")
def documents(teacher_id: str = "T001"):
    """知识库文档列表（按来源聚合块数）。"""
    _check_teacher(teacher_id)
    return {"teacher_id": teacher_id, "documents": store.list_documents(teacher_id)}


@app.delete("/documents/{doc_name}")
def delete_document(doc_name: str, teacher_id: str = "T001",
                    authorization: str = Header(default="")):
    """删除指定文档全部块（安全加固：删除属管理操作，仅管理员可调用）。"""
    _admin_user(authorization)
    _check_teacher(teacher_id)
    n = store.delete_document(teacher_id, doc_name)
    return {"deleted": n, "remaining": _safe_count(teacher_id)}


# ---------- 上传公共加固（/upload 与 /v1/documents/upload 共用） ----------
_UPLOAD_EXTS = {".md", ".txt", ".docx", ".pptx", ".pdf", ".xlsx"}  # 与 ingest.py 一致，#24 R3 含 .xlsx


def _validate_upload(file_name: str | None, data: bytes) -> str:
    """文件名清洗（防路径穿越）→ 扩展名白名单 → 大小/空文件校验，返回 clean_name。"""
    raw_name = (file_name or "").replace("\\", "/")
    clean_name = raw_name.rsplit("/", 1)[-1].strip()
    # 去掉控制字符/换行（防 HTTP 头注入与文件名欺骗）
    clean_name = "".join(ch for ch in clean_name if ch.isprintable() and ch not in "\r\n\t")
    if not clean_name or clean_name in (".", ".."):
        raise HTTPException(status_code=400, detail="文件名非法")
    ext = "." + clean_name.rsplit(".", 1)[-1].lower() if "." in clean_name else ""
    if ext not in _UPLOAD_EXTS:
        raise HTTPException(status_code=400,
                            detail=f"不支持的文件类型: {ext or '无扩展名'}（支持 {sorted(_UPLOAD_EXTS)}）")
    # 应用层大小二次校验（nginx 50MB 是代理层，这里兜底防绕行）
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件超过 50MB 上限")
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="空文件不可入库")
    return clean_name


def _save_and_ingest(data: bytes, clean_name: str, teacher_id: str,
                     tcfg: dict, category: str = "") -> dict:
    """写盘 → 解析入库 → 文档注册表登记，返回 {chunks, stored, total}。"""
    upload_dir = cfg.upload_dir(teacher_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / clean_name
    dest.write_bytes(data)
    try:
        chunks = ingest_file(dest, tcfg)
        n = store.upsert_chunks(chunks, teacher_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"入库失败: {e}")
    # #24 R5：登记文档注册表（分类/标签/启停管理；重复上传幂等，保留既有元数据）
    try:
        from .docreg import get_doc_registry
        get_doc_registry().register(teacher_id, clean_name, category=category,
                                    size_bytes=len(data),
                                    chars=sum(len(c.content) for c in chunks))
    except Exception:  # noqa: BLE001 — 注册表故障不阻断入库主链路
        pass
    return {"chunks": len(chunks), "stored": n, "total": _safe_count(teacher_id)}


@app.post("/upload")
async def upload(teacher_id: str = "T001", x_upload_secret: str = Header(default=""),
                 file: UploadFile = File(...)):
    """上传课件入库（md/txt/docx/pptx/pdf/xlsx）。
    若配置了 UPLOAD_SECRET，则必须携带 X-Upload-Secret 匹配，否则 403（防公网滥用）。
    C5 安全加固：扩展名白名单二次校验 + 应用层大小上限 + 文件名清洗（防路径穿越）。
    """
    if cfg.upload_secret and x_upload_secret != cfg.upload_secret:
        raise HTTPException(status_code=403, detail="无上传权限")
    tcfg = _check_teacher(teacher_id)
    data = await file.read()
    clean_name = _validate_upload(file.filename, data)
    res = _save_and_ingest(data, clean_name, teacher_id, tcfg)
    return {
        "filename": clean_name,
        "teacher_id": teacher_id,
        "chunks": res["chunks"],
        "stored": res["stored"],
        "total": res["total"],
    }


# ---------- 文档按服务器路径导入（#36 自动采集·文档采集：路径上传 + 目录浏览） ----------
class DocImportPathReq(BaseModel):
    teacher_id: str = "T001"
    path: str = ""          # 服务器（容器内）文件绝对路径
    category: str = ""      # 可选：登记分类


def _import_roots() -> list[_Path]:
    """路径导入白名单根（DOC_IMPORT_DIRS 环境变量，容器内路径，冒号分隔）。
    默认 [DATA_DIR/import]——compose 已把宿主 ./data 挂到 /data，
    因此宿主 /home/ubuntu/gongkao/data/import = 容器 /data/import。"""
    raw = (_os.environ.get("DOC_IMPORT_DIRS") or "").strip()
    roots = [p.strip() for p in raw.split(":") if p.strip()] if raw else [str(cfg.data_dir / "import")]
    out = []
    for r in roots:
        try:
            out.append(_Path(r).expanduser().resolve())
        except OSError:
            continue
    return out


def _under_roots(rp: _Path, roots: list[_Path]) -> bool:
    return any(rp == r or str(rp).startswith(str(r) + _os.sep) for r in roots)


@app.get("/admin/documents/browse")
def admin_browse_documents(path: str = "", authorization: str = Header(default="")):
    """浏览路径导入白名单根下的目录（供「选择上传内容的路径」弹窗用）。
    仅管理员；只列目录与受支持扩展名的文件；隐藏文件不展示。"""
    _admin_user(authorization)
    roots = _import_roots()
    raw = (path or "").strip()
    if not raw:
        for r in roots:  # 首次浏览自动建好导入根（宿主 ./data/import），避免「目录不存在」
            try:
                r.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        return {"current": "", "parent": None, "at_root": True,
                "roots": [str(r) for r in roots],
                "entries": [{"name": str(r), "path": str(r), "is_dir": r.is_dir(),
                             "size": 0, "mtime": ""} for r in roots]}
    try:
        rp = _Path(raw).expanduser().resolve()
    except OSError:
        raise HTTPException(status_code=400, detail="路径非法")
    if not _under_roots(rp, roots):
        raise HTTPException(status_code=403, detail="该路径不在允许的导入目录内")
    if not rp.is_dir():
        raise HTTPException(status_code=404, detail="目录不存在")
    entries = []
    try:
        for p in sorted(rp.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if p.name.startswith("."):
                continue
            is_dir = p.is_dir()
            if not is_dir and p.suffix.lower() not in _UPLOAD_EXTS:
                continue
            try:
                st = p.stat()
                entries.append({"name": p.name, "path": str(p), "is_dir": is_dir,
                                "size": 0 if is_dir else st.st_size,
                                "mtime": _time.strftime("%Y-%m-%d %H:%M",
                                                        _time.localtime(st.st_mtime))})
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权限读取该目录")
    parent = str(rp.parent) if rp.parent != rp and _under_roots(rp.parent, roots) else ""
    return {"current": str(rp), "parent": parent,
            "at_root": False, "roots": [str(r) for r in roots], "entries": entries}


@app.post("/admin/documents/import-path")
def admin_import_document_by_path(
    req: DocImportPathReq = Body(default_factory=DocImportPathReq),
    authorization: str = Header(default=""),
):
    """从服务器路径导入文档入库（复用 /upload 同一套校验与入库链路）。
    仅管理员；路径必须在 DOC_IMPORT_DIRS 白名单根下；扩展名/大小/空文件同 /upload 约束。"""
    _admin_user(authorization)
    tcfg = _check_teacher(req.teacher_id)
    raw = (req.path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="路径不能为空")
    roots = _import_roots()
    try:
        rp = _Path(raw).expanduser().resolve()
    except OSError:
        raise HTTPException(status_code=400, detail="路径非法")
    if not _under_roots(rp, roots):
        raise HTTPException(status_code=403, detail="该路径不在允许的导入目录内")
    if not rp.is_file():
        raise HTTPException(status_code=404, detail="文件不存在或不是普通文件")
    try:
        size = rp.stat().st_size
        if size > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="文件超过 50MB 上限")
        data = rp.read_bytes()
    except HTTPException:
        raise
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"读取文件失败: {e}")
    clean_name = _validate_upload(rp.name, data)
    res = _save_and_ingest(data, clean_name, req.teacher_id, tcfg,
                           category=(req.category or "").strip())
    return {"filename": clean_name, "teacher_id": req.teacher_id,
            "source_path": str(rp), **res}


# ================================================================
# Open API v1 · 对外开放接口（X-API-Key 鉴权 + per-IP 限流）
# 规格：docs/后台模块拓展指南.md 第二部分
# 注：路由前缀 /v1（不带 /api）——nginx 的 /api/ 反代剥掉首段前缀，
#     第三方实际访问 http://<host>/api/v1/...
# ================================================================
class _RateWindow:
    """内存滑动窗口限流（per-key；重启清零，看板类内部接口可接受）。"""

    def __init__(self, limit: int = 10, window: float = 60.0):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """放行返回 (True, 0)；超限返回 (False, 建议等待秒数)。"""
        now = _time.time()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, max(int(self.window - (now - q[0])) + 1, 1)
            q.append(now)
            if len(self._hits) > 4096:  # 防键集无限增长
                for k in [k for k, v in self._hits.items() if not v]:
                    self._hits.pop(k, None)
            return True, 0


_openapi_rl = _RateWindow(limit=10, window=60.0)


def _client_ip(request: Request) -> str:
    """取真实客户端 IP（nginx 反代场景优先 X-Forwarded-For 首段）。"""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _v1_err(status: int, message: str, headers: dict | None = None,
            data: dict | list | None = None):
    """开放 API 统一错误信封 {code, message, data}。
    #35 R4：data 可携带结构化补充信息（如可用老师列表）供调用方自助纠错。"""
    return JSONResponse(status_code=status, headers=headers or {},
                        content={"code": status, "message": message, "data": data})


@app.get("/v1/documents/health")
def openapi_documents_health():
    """开放 API 能力探测（公开）：第三方对接前先探此端点确认能力与限制。"""
    return {"enabled": bool(cfg.openapi_key), "formats": sorted(_UPLOAD_EXTS),
            "max_mb": 50, "rate_limit": "10/min per IP", "auth": "X-API-Key header"}


@app.post("/v1/documents/upload")
async def openapi_documents_upload(
    request: Request,
    x_api_key: str = Header(default=""),
    file: UploadFile = File(...),
    teacher_id: str = Form(""),
    category: str = Form(""),
    tags: str = Form(""),
):
    """开放 API 文档上传（非登录用户，X-API-Key 鉴权）。

    multipart/form-data 字段：
      file(必) / teacher_id(必，须为已启用老师) / category(可选，默认 API上传)
      / tags(可选，逗号分隔，仅新登记文档生效)
    统一信封 {code, message, data}；错误码 401/404/400/413/429/503。
    示例：curl -H "X-API-Key: <key>" -F file=@讲义.md -F teacher_id=T001
          -F category=API上传 -F tags=申论,讲义 http://<host>/api/v1/documents/upload
    """
    if not cfg.openapi_key:
        return _v1_err(503, "开放 API 未启用：服务端未配置 OPENAPI_KEY")
    if not x_api_key or not hmac.compare_digest(x_api_key, cfg.openapi_key):
        return _v1_err(401, "无效 X-API-Key（请在请求头 X-API-Key 携带开放密钥）")
    allowed, retry = _openapi_rl.check(_client_ip(request))
    if not allowed:
        return _v1_err(429, f"请求过于频繁（限 10 次/分钟/IP），请 {retry}s 后重试",
                       {"Retry-After": str(retry)})
    tid = (teacher_id or "").strip()
    if not tid:
        return _v1_err(400, "缺少 teacher_id（multipart 表单字段，必填）")
    try:
        tcfg = _check_teacher(tid)
    except HTTPException:
        return _v1_err(404, f"老师不存在或已停用: {tid}")
    data = await file.read()
    try:
        clean_name = _validate_upload(file.filename, data)
    except HTTPException as e:
        return _v1_err(e.status_code, str(e.detail))
    res = _save_and_ingest(data, clean_name, tid, tcfg,
                           category=(category or "API上传").strip()[:30])
    # tags 仅对新登记文档生效（重传幂等保留人工维护的标签）
    if (tags or "").strip():
        try:
            from .docreg import get_doc_registry
            reg = get_doc_registry()
            if not reg.get(tid, clean_name):
                reg.set_meta(tid, clean_name, tags=tags.strip()[:100])
        except Exception:  # noqa: BLE001 — 标签失败不阻断上传主链路
            pass
    return {"code": 0, "message": "ok", "data": {
        "doc_name": clean_name, "teacher_id": tid,
        "category": (category or "API上传").strip()[:30],
        "chunks": res["chunks"], "stored": res["stored"], "total": res["total"],
    }}


# ================================================================
# Open API v1 · 知识体系上传（#33 R3：X-API-Key + Markdown 建树）
# 沿用 v1 规范：X-API-Key 鉴权（OPENAPI_KEY 同一把密钥）+ per-IP 限流
# + 统一信封 {code, message, data}；错误码 401/404/400/413/429/503
# ================================================================
_kn_rl = _RateWindow(limit=6, window=60.0)   # 建树较轻，但防滥用：6 次/分钟/IP

_KN_EXTS = {".md", ".txt"}
_KN_MAX_BYTES = 5 * 1024 * 1024              # 5MB（纯文本 Markdown 足够大）
_KN_MAX_NODES = 300                          # 单次上传节点上限
_KN_MAX_DEPTH = 6                            # 树深上限（# ~ ######）

_MD_HEAD_RE = _re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _md_to_tree(text: str, doc_name: str = "") -> list[dict]:
    """Markdown → 知识树嵌套结构（#33 R3 私有解析器）。

    - 逐行扫描 ^#{1,6} 标题；层级栈维护父子关系
    - 标题 → 节点（name=标题文本）；标题下正文合并为 description（截 500 字）
    - 层级跳跃（如 # 后直接 ###）→ 降级为"栈顶级别 +1"（树不允许跳级孤儿）
    - 无任何标题 → 单节点兜底（name=文件名去扩展名，description=全文截 500）
    返回 [{name, description, children: [...]}]。
    """
    lines = (text or "").replace("\r\n", "\n").split("\n")
    roots: list[dict] = []
    stack: list[tuple[int, dict]] = []   # [(标题级别, 节点)]
    n_heads = 0
    for ln in lines:
        m = _MD_HEAD_RE.match(ln)
        if m:
            title = m.group(2).strip()
            if not title:
                continue
            n_heads += 1
            level = len(m.group(1))
            if stack and level > stack[-1][0] + 1:   # 跳级降级挂载
                level = stack[-1][0] + 1
            while stack and stack[-1][0] >= level:   # 弹出同级/更浅祖先
                stack.pop()
            node = {"name": title, "description": "", "children": []}
            (stack[-1][1]["children"] if stack else roots).append(node)
            stack.append((level, node))
        else:
            t = ln.strip()
            if t and stack:
                cur = stack[-1][1]
                cur["description"] = ((cur["description"] + "\n" + t)
                                      if cur["description"] else t)[:500]
    if n_heads == 0:
        base = (_Path(doc_name or "知识导入").stem or "知识导入")[:60]
        body = "\n".join(l.strip() for l in lines if l.strip())[:500]
        return [{"name": base, "description": body, "children": []}]
    return roots


def _kn_count(tree: list[dict]) -> tuple[int, int]:
    """统计树节点总数与最大深度（上限校验用）。"""
    n = 0
    max_d = 0
    stack = [(t, 1) for t in tree or []]
    while stack:
        node, d = stack.pop()
        n += 1
        max_d = max(max_d, d)
        for c in node.get("children") or []:
            stack.append((c, d + 1))
    return n, max_d


def _kn_create_recursive(ks, teacher_id: str, nodes: list[dict], parent_id: str,
                         counter: dict) -> None:
    """递归建树：同级同名跳过（幂等）；同名已存在且描述为空时补写描述。"""
    for it in nodes or []:
        name = str(it.get("name") or "").strip()[:60]
        if not name:
            continue
        desc = str(it.get("description") or "").strip()[:500]
        existing = ks.find_child(teacher_id, parent_id, name, "knowledge")
        if existing:
            counter["skipped"] += 1
            node_id = existing["node_id"]
            if not (existing.get("description") or "").strip() and desc:
                try:
                    ks.update_node(node_id, description=desc)
                except ValueError:
                    pass
        else:
            counter["created"] += 1
            node_id = ks.create_node(
                teacher_id, name, parent_id=parent_id,
                description=desc, tree_type="knowledge")["node_id"]
        _kn_create_recursive(ks, teacher_id, it.get("children") or [],
                             node_id, counter)


@app.get("/v1/knowledge/health")
def openapi_knowledge_health():
    """知识上传 API 能力探测（公开）。"""
    return {"enabled": bool(cfg.openapi_key), "formats": [".md", ".txt"],
            "max_mb": 5, "rate_limit": "6/min per IP",
            "limits": "300 节点/次，层级≤6", "auth": "X-API-Key header"}


def _kn_enabled_teachers(limit: int = 20) -> list[dict]:
    """(#35 R4) 当前启用的老师列表（开放API错误提示/自查用，≤limit 条）。"""
    out = []
    with Config._teachers_lock:
        for tid, v in (Config._teachers_cache or {}).items():
            if tid == "__sys__" or not (v or {}).get("enabled"):
                continue
            out.append({"teacher_id": tid,
                        "name": (v or {}).get("teacher_name") or tid})
    return out[:limit]


@app.get("/v1/knowledge/teachers")
def openapi_knowledge_teachers(request: Request,
                               x_api_key: str = Header(default="")):
    """(#35 R4) 可用老师列表（供外部上传方自查 teacher_id；鉴权与限流同 upload）。"""
    if not cfg.openapi_key:
        return _v1_err(503, "开放 API 未启用：服务端未配置 OPENAPI_KEY")
    if not x_api_key or not hmac.compare_digest(x_api_key, cfg.openapi_key):
        return _v1_err(401, "无效 X-API-Key（请在请求头 X-API-Key 携带开放密钥）")
    allowed, retry = _kn_rl.check(_client_ip(request))
    if not allowed:
        return _v1_err(429, f"请求过于频繁（限 6 次/分钟/IP），请 {retry}s 后重试",
                       {"Retry-After": str(retry)})
    items = _kn_enabled_teachers()
    return {"code": 0, "message": "ok",
            "data": {"teachers": items, "total": len(items)}}


@app.post("/v1/knowledge/upload")
async def openapi_knowledge_upload(
    request: Request,
    x_api_key: str = Header(default=""),
    file: UploadFile = File(...),
    teacher_id: str = Form(""),
):
    """知识体系上传（非登录用户，X-API-Key 鉴权，#33 R3）。
    multipart/form-data：file(必，.md/.txt ≤5MB UTF-8) / teacher_id(必，已启用老师)。
    #35 R4 防呆：teacher_id 也接受 URL query 传参（Form 优先）；teacher 无效时
    data.available_teachers 附可用老师列表便于自查。
    Markdown 格式约定：# ~ ###### 六级标题映射为树层级；标题下正文为节点
    描述（≤500 字）；层级跳跃自动降级挂载；无标题 → 单节点兜底。
    幂等：同级同名节点跳过不重建（重传同文档安全）。
    统一信封 {code, message, data}；错误码 401/404/400/413/429/503。
    """
    if not cfg.openapi_key:
        return _v1_err(503, "开放 API 未启用：服务端未配置 OPENAPI_KEY")
    if not x_api_key or not hmac.compare_digest(x_api_key, cfg.openapi_key):
        return _v1_err(401, "无效 X-API-Key（请在请求头 X-API-Key 携带开放密钥）")
    allowed, retry = _kn_rl.check(_client_ip(request))
    if not allowed:
        return _v1_err(429, f"请求过于频繁（限 6 次/分钟/IP），请 {retry}s 后重试",
                       {"Retry-After": str(retry)})
    tid = (teacher_id or "").strip()
    if not tid:
        # #35 R4：兼容 teacher_id 放 URL query 的常见误用形态
        tid = (request.query_params.get("teacher_id") or "").strip()
    if not tid:
        return _v1_err(400, "缺少 teacher_id（multipart 表单字段或 URL query 参数，必填）")
    try:
        _check_teacher(tid)
    except HTTPException:
        return _v1_err(404, f"老师不存在或已停用: {tid}",
                       data={"available_teachers": _kn_enabled_teachers()})
    # #33 R3.3：须为"存在且启用"（禁用老师拒收；TeacherCfg 不带 enabled，直查注册表缓存）
    with Config._teachers_lock:
        _tinfo = Config._teachers_cache.get(tid)
    if not _tinfo or not _tinfo.get("enabled"):
        return _v1_err(404, f"老师不存在或已停用: {tid}",
                       data={"available_teachers": _kn_enabled_teachers()})

    name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    ext = _Path(name).suffix.lower()
    if ext not in _KN_EXTS:
        return _v1_err(400, f"文件类型必须是 .md 或 .txt（当前 {ext or '无扩展名'}）")
    data = await file.read()
    if not data:
        return _v1_err(400, "文件内容为空")
    if len(data) > _KN_MAX_BYTES:
        return _v1_err(413, f"文件超过 5MB 上限（当前 {len(data) / 1024 / 1024:.1f}MB）")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _v1_err(400, "文件不是有效 UTF-8 编码（请转存为 UTF-8 后重传）")

    tree = _md_to_tree(text, name)
    n_nodes, depth = _kn_count(tree)
    if n_nodes > _KN_MAX_NODES:
        return _v1_err(400, f"解析出 {n_nodes} 个节点，超过单次 300 节点上限（请拆分文档）")
    if depth > _KN_MAX_DEPTH:
        return _v1_err(400, f"树层级深度 {depth} 超过 6 级上限")

    from .knowledge import get_knowledge_store
    ks = get_knowledge_store()
    counter = {"created": 0, "skipped": 0}
    try:
        _kn_create_recursive(ks, tid, tree, "", counter)
    except ValueError as e:
        return _v1_err(400, f"建树失败：{e}")
    if counter["created"] == 0 and counter["skipped"] == 0:
        return _v1_err(400, "未解析出任何有效节点（Markdown 需含标题行，如 '# 章节名'）")
    return {"code": 0, "message": "ok", "data": {
        "doc_name": name, "teacher_id": tid,
        "nodes_total": n_nodes, "tree_depth": depth,
        "created": counter["created"], "skipped": counter["skipped"],
        "hint": f"已导入 {tid} 的知识体系树（{counter['created']} 新建 / "
                f"{counter['skipped']} 跳过）；登录管理后台 → 知识管理 → 切换到该老师即可查看",
    }}


# ================================================================
# C2 · 管理后台路由（均需 admin Bearer）
# ================================================================

def _admin_user(authorization: str) -> dict:
    """解析 Bearer token，返回 admin 用户 dict；非 admin → 403。"""
    if not authorization:
        raise HTTPException(status_code=401, detail="请先登录")
    token = authorization.removeprefix("Bearer ").strip()
    user = auth_store.user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="无效 Token")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


# ---------- 统计 ----------
@app.get("/admin/stats")
def admin_stats(authorization: str = Header(default="")):
    """后台首页统计。"""
    _admin_user(authorization)
    return admin_biz.admin_stats()


# ---------- 老师 CRUD ----------
@app.get("/admin/teachers")
def admin_list_teachers(authorization: str = Header(default="")):
    """老师列表（含 enabled）。"""
    _admin_user(authorization)
    return {"teachers": admin_biz.list_teachers_admin()}


@app.post("/admin/teachers")
def admin_create_teacher(
    payload: dict,
    authorization: str = Header(default=""),
):
    """新增老师。"""
    _admin_user(authorization)
    try:
        return admin_biz.create_teacher(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/admin/teachers/sort")
def admin_sort_teachers(
    payload: dict,
    authorization: str = Header(default=""),
):
    """批量保存老师排序。body: {items: [{teacher_id, sort_order}]}。
    静态路由：必须注册在 PUT /admin/teachers/{teacher_id} 动态路由之前（防遮蔽）。"""
    _admin_user(authorization)
    items = (payload or {}).get("items") or []
    try:
        return admin_biz.sort_teachers(items)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/admin/teachers/{teacher_id}")
def admin_update_teacher(
    teacher_id: str,
    payload: dict,
    authorization: str = Header(default=""),
):
    """编辑老师参数。"""
    _admin_user(authorization)
    try:
        return admin_biz.update_teacher(teacher_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/admin/teachers/{teacher_id}")
def admin_disable_teacher(
    teacher_id: str,
    authorization: str = Header(default=""),
):
    """软下线老师（enabled=false）。"""
    _admin_user(authorization)
    try:
        return admin_biz.disable_teacher(teacher_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- 用户管理 ----------
@app.get("/admin/users/stats")
def admin_user_stats(authorization: str = Header(default="")):
    """用户统计：总量 / 角色分布 / 状态分布 / 今日活跃 / 当前会员数。
    静态路由，必须注册在 /admin/users/{user_id} 动态路由之前。"""
    _admin_user(authorization)
    return admin_biz.user_stats_admin()


@app.get("/admin/users")
def admin_list_users(
    q: str = "",
    limit: int = 200,
    offset: int = 0,
    authorization: str = Header(default=""),
):
    """用户列表（含 role/额度/到期）。"""
    _admin_user(authorization)
    users = admin_biz.list_users_admin(limit=limit, offset=offset, q=q)
    return {"users": users, "limit": limit, "offset": offset}


@app.get("/admin/users/{user_id}")
def admin_get_user(
    user_id: str,
    authorization: str = Header(default=""),
):
    """单个用户详情。"""
    _admin_user(authorization)
    u = admin_biz.get_user_admin(user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    return u


@app.post("/admin/users/{user_id}/set_role")
def admin_set_role(
    user_id: str,
    role: str = Body(...),
    days: int = Body(default=30),
    authorization: str = Header(default=""),
):
    """手动开通/回退会员。role=free|member|admin，days 仅 member 生效。"""
    _admin_user(authorization)
    try:
        return admin_biz.set_user_role(user_id, role, days=days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/users/{user_id}/grant")
def admin_grant(
    user_id: str,
    kind: str = Body(...),
    value: int = Body(default=0),
    authorization: str = Header(default=""),
):
    """手动充值：kind='days'(+N天) 或 'reset_today'(重置今日已用)。"""
    _admin_user(authorization)
    try:
        return admin_biz.grant_user(user_id, kind, value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/users/{user_id}/status")
def admin_user_status(
    user_id: str,
    status: str = Body(...),
    authorization: str = Header(default=""),
):
    """启用/禁用用户账号。status ∈ active|disabled，禁用后无法登录。"""
    _admin_user(authorization)
    try:
        return admin_biz.set_user_status(user_id, status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- 课件管理（后台视角） ----------
@app.get("/admin/documents")
def admin_documents(
    teacher_id: str,
    authorization: str = Header(default=""),
):
    """知识库文档列表（指定老师）。"""
    _admin_user(authorization)
    _check_teacher(teacher_id)
    return {"teacher_id": teacher_id, "documents": store.list_documents(teacher_id)}


@app.delete("/admin/documents/{doc_name}")
def admin_delete_document(
    doc_name: str,
    teacher_id: str = "T001",
    authorization: str = Header(default=""),
):
    """删除指定老师文档（触发缓存失效 + #24 R5 注册表联动清理）。"""
    _admin_user(authorization)
    _check_teacher(teacher_id)
    n = store.delete_document(teacher_id, doc_name)
    reg_deleted = False
    try:  # #24 R5：向量库与注册表保持一致（注册表故障不阻断删除）
        from .docreg import get_doc_registry
        reg_deleted = get_doc_registry().delete(teacher_id, doc_name)
    except Exception:  # noqa: BLE001
        pass
    return {"deleted": n, "registry_deleted": reg_deleted,
            "teacher_id": teacher_id, "doc_name": doc_name}


# ---------- 文档注册表管理（#24 R5：分类浏览/元数据/上下架/内容检索） ----------
class DocMetaReq(BaseModel):
    """文档元数据编辑请求体。"""
    teacher_id: str = "T001"
    doc_name: str = ""
    category: str | None = None
    tags: str | None = None
    enabled: bool | None = None


class DocBatchReq(BaseModel):
    """#25 批量文档操作请求体。"""
    teacher_id: str = "T001"
    doc_names: list[str] = []
    enabled: bool | None = None
    category: str | None = None


def _doc_registry():
    from .docreg import get_doc_registry
    return get_doc_registry()


@app.get("/admin/documents/registry")
def admin_documents_registry(
    teacher_id: str = "T001",
    category: str = "",
    keyword: str = "",
    with_chunks: bool = True,
    authorization: str = Header(default=""),
):
    """#24 R5 分类浏览：注册表元数据（分类/标签/启停/大小/字数），合并向量库块数。

    历史文档（注册表未登记）自动补登记，保证列表完整。
    """
    _admin_user(authorization)
    _check_teacher(teacher_id)
    reg = _doc_registry()
    # 历史数据自愈：向量库有、注册表无 → 补登记（size/chars 未知填 0）
    try:
        known = {d["doc_name"] for d in reg.list_meta(teacher_id)}
        for d in store.list_documents(teacher_id):
            if d["doc_name"] not in known:
                reg.register(teacher_id, d["doc_name"])
    except Exception:  # noqa: BLE001
        pass
    chunks = {d["doc_name"]: d["chunks"] for d in store.list_documents(teacher_id)}
    docs = reg.list_meta(teacher_id, category=category, keyword=keyword)
    # 归一化：enabled 转布尔、file_ext 兜底、块数合并
    for d in docs:
        d["enabled"] = bool(d.get("enabled"))
        d["file_ext"] = d.get("file_ext") or _ext_of(d.get("doc_name", ""))
        if with_chunks:
            d["chunks"] = chunks.get(d["doc_name"], 0)
    return {"teacher_id": teacher_id, "documents": docs,
            "categories": [{"category": c["category"], "count": c["docs"]}
                           for c in reg.categories(teacher_id)]}


def _ext_of(doc_name: str) -> str:
    return ("." + doc_name.rsplit(".", 1)[-1]) if "." in doc_name else ""


@app.post("/admin/documents/meta")
def admin_documents_meta(
    req: DocMetaReq = Body(default_factory=DocMetaReq),
    authorization: str = Header(default=""),
):
    """#24 R5 编辑元数据/上下架。下架即时生效（检索过滤 + 缓存失效）。"""
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    if not req.doc_name.strip():
        raise HTTPException(status_code=400, detail="doc_name 不能为空")
    try:
        return _doc_registry().set_meta(
            req.teacher_id, req.doc_name.strip(),
            req.category, req.tags, req.enabled)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/admin/documents/batch")
def admin_documents_batch(
    req: DocBatchReq = Body(default_factory=DocBatchReq),
    authorization: str = Header(default=""),
):
    """#25 批量上下架/归类：对 doc_names 统一启用/停用或改分类。"""
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    if req.enabled is None and req.category is None:
        raise HTTPException(status_code=400, detail="未提供批量操作（enabled 或 category）")
    try:
        return _doc_registry().batch_set_meta(
            req.teacher_id, req.doc_names, req.enabled, req.category)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/documents/search")
def admin_documents_search(
    req: dict = Body(default_factory=dict),
    authorization: str = Header(default=""),
):
    """#24 R5 内容检索：语义检索老师全部文档块，按文档聚合返回命中片段。

    body: {teacher_id, query, top_n?}；已下架文档自动过滤。
    """
    _admin_user(authorization)
    teacher_id = (req.get("teacher_id") or "T001").strip()
    query = (req.get("query") or "").strip()
    top_n = max(1, min(int(req.get("top_n") or 12), 30))
    _check_teacher(teacher_id)
    if not query:
        raise HTTPException(status_code=400, detail="检索关键词不能为空")
    hits = store.retrieve(query, teacher_id, top_n=top_n)
    by_doc: dict[str, list] = {}
    for h in hits:
        name = (h.get("meta") or {}).get("doc_name", "未知")
        by_doc.setdefault(name, []).append({
            "content": (h.get("content") or "")[:200],
            "score": round(float(h.get("score") or 0), 3)})
    return {"teacher_id": teacher_id, "query": query,
            "results": [{"doc_name": k, "hits": v} for k, v in by_doc.items()]}


# ---------- 充值码管理（A3 后台 · #24 R4 扩展） ----------
class RechargeGenReq(BaseModel):
    """充值码生成请求体（#24 R4：批次/渠道/备注）。"""
    plan: str = "month"
    count: int = 1
    batch_id: str = ""
    channel: str = ""
    note: str = ""


class RechargeVoidReq(BaseModel):
    """作废请求体：codes 与 batch_id 二选一（仅未使用码可作废）。"""
    codes: list[str] = []
    batch_id: str = ""


class RechargeImportReq(BaseModel):
    """批量导入请求体。items=[{code, plan, amount?}] 或 csv 文本（code,plan,amount）。"""
    items: list[dict] = []
    csv: str = ""
    batch_id: str = ""


@app.get("/admin/recharge-codes")
def admin_list_recharge_codes(
    limit: int = 100,
    offset: int = 0,
    status: str = "all",
    batch_id: str = "",
    channel: str = "",
    keyword: str = "",
    used_only: bool = False,
    authorization: str = Header(default=""),
):
    """充值码列表（管理端）。#24 R4：状态(all/unused/used/voided)/批次/渠道/关键词筛选 + 总数。"""
    _admin_user(authorization)
    codes = admin_biz.list_recharge_codes(
        limit=limit, offset=offset, used_only=used_only, status=status,
        batch_id=batch_id, channel=channel, keyword=keyword)
    total = admin_biz.count_recharge_codes(
        status=status, batch_id=batch_id, channel=channel, keyword=keyword)
    return {"codes": codes, "total": total, "limit": limit, "offset": offset}


@app.post("/admin/recharge-codes/generate")
def admin_generate_recharge_codes(
    req: RechargeGenReq = Body(default_factory=RechargeGenReq),
    authorization: str = Header(default=""),
):
    """生成充值码（管理端，#24 R4 支持批次/渠道/备注）。"""
    _admin_user(authorization)
    try:
        return admin_biz.generate_recharge_codes_admin(
            req.plan, req.count, req.batch_id, req.channel, req.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/recharge-codes/void")
def admin_void_recharge_codes(
    req: RechargeVoidReq = Body(default_factory=RechargeVoidReq),
    authorization: str = Header(default=""),
):
    """#24 R4 作废：按码列表或整批次（仅未使用码可作废，已使用不可作废）。"""
    _admin_user(authorization)
    if not req.codes and not req.batch_id.strip():
        raise HTTPException(status_code=400, detail="codes 与 batch_id 至少提供一项")
    try:
        return get_payment_store().void_codes(
            codes=[c.strip() for c in req.codes if c.strip()],
            batch_id=req.batch_id.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/recharge-codes/import")
def admin_import_recharge_codes(
    req: RechargeImportReq = Body(default_factory=RechargeImportReq),
    authorization: str = Header(default=""),
):
    """#24 R4 批量导入：items JSON 或 csv 文本（每行 code,plan,amount）。
    重复码跳过并计数，格式错误逐条报明。"""
    _admin_user(authorization)
    items: list[dict] = []
    if req.items:
        items = req.items
    elif req.csv.strip():
        for ln, line in enumerate(req.csv.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [x.strip() for x in line.split(",")]
            if len(parts) < 2:
                raise HTTPException(status_code=400,
                                    detail=f"csv 第 {ln} 行格式错误（需 code,plan[,amount]）: {line[:50]}")
            items.append({"code": parts[0], "plan": parts[1],
                          **({"amount": parts[2]} if len(parts) > 2 and parts[2] else {})})
    if not items:
        raise HTTPException(status_code=400, detail="导入列表为空")
    if len(items) > 1000:
        raise HTTPException(status_code=400, detail="单次导入上限 1000 条")
    try:
        return get_payment_store().import_codes(items, batch_id=req.batch_id.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/admin/recharge-codes/stats")
def admin_recharge_stats(authorization: str = Header(default="")):
    """#24 R4 统计：总量/使用率 + 按批次 + 按渠道聚合。"""
    _admin_user(authorization)
    return get_payment_store().recharge_stats()


@app.get("/admin/recharge-codes/export")
def admin_export_recharge_codes(
    status: str = "all",
    batch_id: str = "",
    channel: str = "",
    authorization: str = Header(default=""),
):
    """#24 R4 导出 CSV（按当前筛选条件全量导出，非分页）。"""
    _admin_user(authorization)
    codes = admin_biz.list_recharge_codes(
        limit=100000, offset=0, status=status,
        batch_id=batch_id, channel=channel, keyword="")
    lines = ["code,plan,amount,status,used_by,used_at,batch_id,channel,note,created_at"]
    st = {"unused": "未使用", "used": "已使用", "voided": "已作废"}
    for c in codes:
        s = "used" if c.get("used_by") else ("voided" if c.get("voided") else "unused")
        row = [str(c.get("code", "")), str(c.get("plan", "")), str(c.get("amount", "")),
               st.get(s, s), str(c.get("used_by") or ""), str(c.get("used_at") or ""),
               str(c.get("batch_id") or ""), str(c.get("channel") or ""),
               str(c.get("note") or "").replace(",", "，"), str(c.get("created_at", ""))]
        lines.append(",".join(row))
    return PlainTextResponse(
        "\n".join(lines), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 "attachment; filename=recharge_codes.csv"})


@app.post("/admin/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: str,
    new_password: str = Body(...),
    authorization: str = Header(default=""),
):
    """管理员重置用户密码（无邮箱方案）。"""
    _admin_user(authorization)
    try:
        return admin_biz.reset_user_password_admin(user_id, new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/admin/stats/feedback")
def admin_feedback_stats(
    teacher_id: str = "",
    status: str = "",
    authorization: str = Header(default=""),
):
    """答案反馈汇总（A1 满意度统计）。#26 R3 支持 status 筛选。"""
    _admin_user(authorization)
    if status and status not in ("new", "resolved"):
        raise HTTPException(status_code=400, detail="status 必须是 new 或 resolved")
    return {
        "stats": study_store.feedback_stats(teacher_id or None),
        "recent": study_store.list_feedback(teacher_id or None,
                                            status=(status or None), limit=20),
    }


# ---------- C4 数据备份 / 恢复 ----------
@app.get("/admin/backups")
def admin_list_backups(authorization: str = Header(default="")):
    """备份归档列表 + 磁盘占用（运维容灾）。"""
    _admin_user(authorization)
    return {
        "backups": admin_biz.list_backups_admin(),
        "usage": admin_biz.backup_disk_usage(),
    }


@app.post("/admin/backups")
def admin_create_backup(authorization: str = Header(default="")):
    """创建数据备份：raw 源文件 + 全部 *.db → backup_*.tar.gz。"""
    _admin_user(authorization)
    return admin_biz.create_backup_admin()


@app.post("/admin/backups/restore")
def admin_restore_backup(payload: dict, authorization: str = Header(default="")):
    """恢复备份中的 raw 源文件（安全边界：不动 db/向量库）。"""
    _admin_user(authorization)
    try:
        return admin_biz.restore_backup_admin((payload or {}).get("name", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/admin/backups/{name}/download")
def admin_download_backup(name: str, authorization: str = Header(default="")):
    """下载备份归档（用于异地容灾留档）。"""
    _admin_user(authorization)
    if ".." in name or not name.startswith("backup_"):
        raise HTTPException(status_code=400, detail="备份名不合法")
    path = Config.data_dir / "backups" / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="备份不存在")
    return FileResponse(str(path), filename=name, media_type="application/gzip")


class AdminFeedbackStatusReq(BaseModel):
    """#26 R3 反馈处理状态请求体。"""
    status: str = "resolved"
    note: str = ""


@app.post("/admin/feedback/{fid}/status")
def admin_set_feedback_status(
    fid: int,
    req: AdminFeedbackStatusReq = Body(default_factory=AdminFeedbackStatusReq),
    authorization: str = Header(default=""),
):
    """#26 R3 更新反馈处理状态（工单式闭环）。"""
    _admin_user(authorization)
    try:
        return study_store.set_feedback_status(fid, req.status, req.note)
    except ValueError as e:
        raise HTTPException(status_code=404 if str(e).startswith("反馈不存在") else 400,
                            detail=str(e))


# ---------- 题库管理（#13 · #24 R2 分类关联） ----------
@app.get("/admin/teachers/qb-stats")
def admin_teachers_qb_stats(authorization: str = Header(default="")):
    """#26 R3 每老师题库题目数汇总（老师管理速览列）。"""
    _admin_user(authorization)
    return {"counts": study_store.question_count_by_teacher()}


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


@app.get("/admin/questions")
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
    _admin_user(authorization)
    category_ids: list[str] | None = None
    if uncategorized:
        category_ids = [""]
    elif category_id:
        try:
            category_ids = _knowledge_store().subtree_ids(teacher_id, category_id)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"分类节点不存在: {category_id}")
    return {
        "questions": study_store.list_questions(
            teacher_id, qtype or None, limit, offset,
            difficulty, knowledge_point, keyword, category_ids=category_ids,
            category=category, source_type=source_type),
        "limit": limit,
        "offset": offset,
    }


@app.post("/admin/questions")
def admin_add_question(
    req: QuestionReq = Body(default_factory=QuestionReq),
    authorization: str = Header(default=""),
):
    """新增题库题目（管理端）。#24 R2：可关联题型树节点（category_id）。
    #35 R1：category 课程大类（8 枚举；空/非法兜底"综合"）。"""
    _admin_user(authorization)
    try:
        return study_store.add_question(
            req.teacher_id, req.qtype, req.question, req.answer,
            req.options, req.analysis, req.difficulty, req.knowledge_point,
            req.category_id, category=req.category,
            source_type=req.source_type, source=req.source,
            sensitive_check=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/admin/questions/{qid:int}")
def admin_delete_question(
    qid: int,
    authorization: str = Header(default=""),
):
    """删除题库题目（管理端）。"""
    _admin_user(authorization)
    ok = study_store.delete_question(qid)
    if not ok:
        raise HTTPException(status_code=404, detail=f"题目不存在: {qid}")
    return {"deleted": qid}


@app.get("/admin/questions/dup")
def admin_check_duplicates(
    teacher_id: str = "T001",
    text: str = "",
    authorization: str = Header(default=""),
):
    """#26 R2 题干查重：同老师下精确/规范化等值重复题列表（查重/录入预警用）。"""
    _admin_user(authorization)
    dups = study_store.find_duplicates(teacher_id, text)
    return {"duplicates": dups, "count": len(dups)}


@app.get("/admin/questions/{qid:int}")
def admin_get_question(
    qid: int,
    authorization: str = Header(default=""),
):
    """#26 R2 单题详情（管理端编辑预填用）。"""
    _admin_user(authorization)
    try:
        return study_store.get_question(qid)
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


@app.put("/admin/questions/{qid:int}")
def admin_update_question(
    qid: int,
    req: AdminQuestionUpdateReq = Body(default_factory=AdminQuestionUpdateReq),
    authorization: str = Header(default=""),
):
    """#26 R2 编辑题目：仅更新传入字段。"""
    _admin_user(authorization)
    try:
        return study_store.update_question(qid, **req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/questions/{qid:int}/copy")
def admin_copy_question(
    qid: int,
    authorization: str = Header(default=""),
):
    """#26 R2 复制题目：按原题内容新增一条。"""
    _admin_user(authorization)
    try:
        return study_store.copy_question(qid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class AdminQuestionsBatchReq(BaseModel):
    qids: list[int] = []


@app.post("/admin/questions/batch")
def admin_delete_questions_batch(
    req: AdminQuestionsBatchReq = Body(default_factory=AdminQuestionsBatchReq),
    authorization: str = Header(default=""),
):
    """#25 批量删除题库题目（管理端）：qids 数组，返回 {deleted, missing}。"""
    _admin_user(authorization)
    try:
        return study_store.delete_questions_batch(req.qids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- 知识库老师状态（前台联动） ----------
@app.post("/admin/teachers/{teacher_id}/enable")
def admin_enable_teacher(
    teacher_id: str,
    authorization: str = Header(default=""),
):
    """恢复上线（enabled=true）。"""
    _admin_user(authorization)
    try:
        return admin_biz.enable_teacher(teacher_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ================================================================
# #16 后台重构 · 新增端点（用户管理扩展 / 文档预览 / 题库 AI 采集）
# ================================================================

class AdminUserCreateReq(BaseModel):
    username: str = ""
    password: str = ""
    role: str = "free"


class AdminQuestionExtractReq(BaseModel):
    teacher_id: str = "T001"
    text: str = ""
    doc_name: str = ""


@app.get("/admin/questions/stats")
def admin_questions_stats(
    teacher_id: str = "",
    authorization: str = Header(default=""),
):
    """题库维度统计（题型/难度/知识点分布）。"""
    _admin_user(authorization)
    return study_store.question_stats(teacher_id or None)


@app.post("/admin/users")
def admin_create_user(
    req: AdminUserCreateReq = Body(default_factory=AdminUserCreateReq),
    authorization: str = Header(default=""),
):
    """管理员新增用户。"""
    _admin_user(authorization)
    try:
        return admin_biz.create_user_admin(req.username, req.password, req.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/admin/users/{user_id}")
def admin_delete_user(
    user_id: str,
    authorization: str = Header(default=""),
):
    """管理员删除用户。"""
    _admin_user(authorization)
    try:
        return admin_biz.delete_user_admin(user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/admin/documents/preview")
def admin_preview_document(
    teacher_id: str,
    doc_name: str,
    authorization: str = Header(default=""),
):
    """读取指定老师原始课件内容（预览）。"""
    _admin_user(authorization)
    _check_teacher(teacher_id)
    try:
        return admin_biz.preview_document(teacher_id, doc_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------- #33 R2：题目配图静态服务（公开只读，内容寻址不可变） ----------

_QIMG_FNAME_RE = _re.compile(r"^[a-f0-9]{12}\.(png|jpe?g|gif|webp)$")
_QIMG_TID_RE = _re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_QIMG_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp"}


@app.get("/qimg/{teacher_id}/{fname}")
def get_qimg(teacher_id: str, fname: str):
    """题目配图（#33 R2）：公开只读。前端/学生端通过 /api/qimg/... 访问。

    双防穿越：① fname 正则白名单（sha1 前 12 位十六进制 + 受限扩展名）
              ② teacher_id 字符白名单 + resolve() 后前缀校验。
    图片内容寻址（同名必同内容）→ 长缓存 immutable。"""
    tid = (teacher_id or "").strip()
    name = (fname or "").strip()
    if not _QIMG_TID_RE.match(tid) or not _QIMG_FNAME_RE.match(name):
        raise HTTPException(status_code=404, detail="图片不存在")
    base = (cfg.data_dir / "qimg").resolve()
    fp = (base / tid / name).resolve()
    if not str(fp).startswith(str(base) + _os.sep) or not fp.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(
        fp, media_type=_QIMG_MIME.get(fp.suffix.lower(), "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.post("/admin/questions/parse-file")
async def admin_parse_collect_file(
    file: UploadFile = File(...),
    teacher_id: str = Form(default=""),
    authorization: str = Header(default=""),
):
    """AI 采集文件上传解析（#19）：md/txt/docx/pptx/pdf → 文本 + 内联图片标记。
    仅解析不入库；返回 {doc_name, chars, text, n_img, images}，前端填充后走 /extract 提取。
    #33 R2：teacher_id 用于图片落盘目录 data/qimg/{teacher_id}/（内容寻址幂等）。"""
    _admin_user(authorization)
    if teacher_id:
        _check_teacher(teacher_id)
    try:
        data = await file.read()
        return admin_biz.parse_collect_file(file.filename, data, teacher_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/questions/extract")
def admin_extract_questions(
    req: AdminQuestionExtractReq = Body(default_factory=AdminQuestionExtractReq),
    authorization: str = Header(default=""),
):
    """AI 提取题目（仅返回结构化结果，不落库；由前端确认后走批量入库）。"""
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    subject = ""
    try:
        tcfg = _check_teacher(req.teacher_id)
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


@app.post("/admin/questions/import")
def admin_import_questions(
    req: AdminQuestionsImportReq = Body(default_factory=AdminQuestionsImportReq),
    authorization: str = Header(default=""),
):
    """批量导入题目（AI 采集确认后调用）。#10 入库敏感词守卫：命中即过滤不入库。"""
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    result = study_store.add_questions_batch(
        req.teacher_id, req.questions, sensitive_check=True)
    return result


@app.get("/admin/autocollect/status")
def admin_autocollect_status(authorization: str = Header(default="")):
    """自动采集守候状态：是否启用 / 上次运行时间与结果摘要。"""
    _admin_user(authorization)
    return autocollect_mod.status()


@app.post("/admin/autocollect/run")
def admin_autocollect_run(authorization: str = Header(default="")):
    """P0 异步触发一轮自动采集（后台线程执行，立即返回 started，结果轮询 status）。"""
    _admin_user(authorization)
    return autocollect_mod.trigger_run(retry_failed=False)


@app.post("/admin/autocollect/retry-failed")
def admin_autocollect_retry_failed(authorization: str = Header(default="")):
    """#12 一键补采：异步仅重跑上一轮失败的采集来源（失败 URL/文档/搜索词），
    跳过正常增量，用于失败后补采。"""
    _admin_user(authorization)
    return autocollect_mod.trigger_run(retry_failed=True)


class AutocollectLlmReq(BaseModel):
    model: str = ""
    base_url: str = ""
    api_key: str = ""


@app.get("/admin/autocollect/llm")
def admin_autocollect_llm_get(authorization: str = Header(default="")):
    """自动采集专用模型通道只读视图（API_KEY 脱敏）。"""
    _admin_user(authorization)
    return autocollect_mod.llm_channel_info()


@app.post("/admin/autocollect/llm")
def admin_autocollect_llm_set(
    req: AutocollectLlmReq = Body(default_factory=AutocollectLlmReq),
    authorization: str = Header(default=""),
):
    """配置自动采集专用模型通道；api_key 留空 = 保持已存密钥。"""
    _admin_user(authorization)
    try:
        ch = autocollect_mod.save_llm_channel(req.model, req.base_url, req.api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, **ch}


@app.post("/admin/autocollect/llm/clear")
def admin_autocollect_llm_clear(authorization: str = Header(default="")):
    """清除专用通道（回落 AUTOCOLLECT_* / 平台默认模型）。"""
    _admin_user(authorization)
    autocollect_mod.clear_llm_channel()
    return {"ok": True}


@app.post("/admin/autocollect/llm/models")
def admin_autocollect_llm_models(authorization: str = Header(default="")):
    """用已配置的采集专用通道拉取远端模型列表（服务端持有完整 API Key，前端无需回传）。

    未配置专用通道时返回 ok=False 且不兜底；通道网络失败时由 list_remote_models
    自动兜底内置目录（used_fallback=true）。
    """
    _admin_user(authorization)
    ch = autocollect_mod.load_llm_channel()
    base_url = (ch.get("base_url") or "").strip()
    api_key = (ch.get("api_key") or "").strip()
    if not base_url:
        return {
            "ok": False,
            "models": [],
            "error": "尚未配置自动采集专用通道；请先在「AI 模型管理」面板配置 Base URL 与模型后再拉取",
            "used_fallback": False,
            "base_url": "",
        }
    return list_remote_models(base_url=base_url, provider="", api_key=api_key)


# ---- #40 自动采集多通道（多个平台/模型共存） ----

@app.get("/admin/autocollect/llm/channels")
def admin_autocollect_llm_channels_list(authorization: str = Header(default="")):
    """多平台专用通道列表（每条 API_KEY 脱敏）。"""
    _admin_user(authorization)
    return autocollect_mod.llm_channels_info()


@app.get("/admin/autocollect/llm/channels/stats")
def admin_autocollect_llm_channels_stats(authorization: str = Header(default="")):
    """通道使用统计：每条通道成功/失败/平均延迟/最近成功时间 + 失活标红提示。"""
    _admin_user(authorization)
    return autocollect_mod.channel_stats()


class AutocollectChannelReq(BaseModel):
    model: str = ""
    base_url: str = ""
    api_key: str = ""


@app.post("/admin/autocollect/llm/channels")
def admin_autocollect_llm_channels_add(
    req: AutocollectChannelReq = Body(default_factory=AutocollectChannelReq),
    authorization: str = Header(default=""),
):
    """追加一个专用通道（多模型共存）；api_key 空用全局密钥。"""
    _admin_user(authorization)
    try:
        chs = autocollect_mod.add_llm_channel(req.model, req.base_url, req.api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return autocollect_mod.llm_channels_info()


@app.put("/admin/autocollect/llm/channels/{idx}")
def admin_autocollect_llm_channels_update(
    idx: int,
    req: AutocollectChannelReq = Body(default_factory=AutocollectChannelReq),
    authorization: str = Header(default=""),
):
    """更新第 idx 个专用通道；字段留空保持原值（api_key 留空保持原密钥）。"""
    _admin_user(authorization)
    try:
        autocollect_mod.update_llm_channel(
            idx, req.model, req.base_url, req.api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return autocollect_mod.llm_channels_info()


@app.delete("/admin/autocollect/llm/channels/{idx}")
def admin_autocollect_llm_channels_delete(
    idx: int,
    authorization: str = Header(default=""),
):
    """删除第 idx 个专用通道。"""
    _admin_user(authorization)
    try:
        autocollect_mod.delete_llm_channel(idx)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return autocollect_mod.llm_channels_info()


@app.post("/admin/autocollect/llm/channels/{idx}/test")
def admin_autocollect_llm_channels_test(
    idx: int,
    authorization: str = Header(default=""),
):
    """一键测活第 idx 个专用通道（最小 chat 请求，验证连通性与密钥）。"""
    _admin_user(authorization)
    try:
        return autocollect_mod.test_llm_channel(idx)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/admin/autocollect/llm/channels/test")
def admin_autocollect_llm_channels_test_all(authorization: str = Header(default="")):
    """一键测活全部专用通道，返回逐条结果与汇总（路由须注册在 /{idx} 语义之外，
    FastAPI 按声明顺序匹配，"test" 不会与 int 路径参数冲突）。"""
    _admin_user(authorization)
    return autocollect_mod.test_llm_channels_all()


@app.get("/admin/autocollect/logs")
def admin_autocollect_logs(
    limit: int = Query(80, ge=1, le=300),
    authorization: str = Header(default=""),
):
    """实时任务日志（进程内存环形缓冲，最新在前）；模型做的每个采集任务都实时滚动展示。"""
    _admin_user(authorization)
    return {"logs": autocollect_mod.recent_task_logs(limit)}


class AutocollectConfigReq(BaseModel):
    enabled: bool | None = None
    interval_secs: int | None = None
    cpu_max: int | None = None
    mem_max: int | None = None
    max_per_cycle: int | None = None
    max_site_pages: int | None = None
    threads: int | None = None
    teachers: list[str] = []
    urls: list[str] = []
    search_queries: list[str] = []
    site_whitelist: list[str] = []   # saA 站点白名单信任域（直采 + 搜索豁免阻断）
    site_pages_map: dict[str, int] = {}   # saE 逐站页数额度差异化：{url或域名: 页数}
    fallback_models: list[str] = []
    autonomous: bool | None = None
    autonomous_queries: int | None = None
    schedule_enabled: bool | None = None
    schedule_start: int | None = None
    schedule_end: int | None = None
    retry_failed_auto: bool | None = None
    site_rules: list[dict] = []
    llm_budget_chars: int | None = None
    llm_price_per_1m: float | None = None
    round_budget_secs: int | None = None   # 轮次墙钟上限（秒，0=不限）
    # 采集质量增强配置：来源优先级 + AI 自评抽样 + Webhook 通知
    priorities: dict[str, str] = {}
    quality_sample_rate: float | None = None
    quality_rules: str = ""
    webhook_url: str = ""
    notify_on: list[str] = []
    fail_alert_threshold: int | None = None


@app.get("/admin/autocollect/config")
def admin_autocollect_config_get(authorization: str = Header(default="")):
    """自动采集 runtime 配置视图（生效值 + 老师候选 + 通道脱敏）。"""
    _admin_user(authorization)
    return autocollect_mod.config_admin()


@app.put("/admin/autocollect/config")
def admin_autocollect_config_put(
    req: AutocollectConfigReq = Body(default_factory=AutocollectConfigReq),
    authorization: str = Header(default=""),
):
    """保存自动采集 runtime 配置（实时生效，无需重建容器）。"""
    _admin_user(authorization)
    try:
        return autocollect_mod.save_config(req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/admin/autocollect/history")
def admin_autocollect_history(limit: int = 50,
                              authorization: str = Header(default="")):
    """自动采集运行历史（时间倒序，仅真实执行轮次）。"""
    _admin_user(authorization)
    return {"runs": autocollect_mod.history(limit)}


@app.post("/admin/autocollect/notify-test")
def admin_autocollect_notify_test(authorization: str = Header(default="")):
    """向已配置的 webhook 发送一条测试通知，验证连通性。"""
    _admin_user(authorization)
    return autocollect_mod.test_notify()


@app.get("/admin/autocollect/papers")
def admin_autocollect_papers(limit: int = Query(100, ge=1, le=2000),
                             authorization: str = Header(default="")):
    """试卷作业队列：当前在采的那张卷 + 待采明细 + 已阻塞明细。

    整卷串行采集的主视图：一张试卷片段全部跑完才入库，未采完的下轮从游标续采；
    连续多轮无进展的卷标 blocked（cursor/total/stall 说明卡在哪里）。"""
    _admin_user(authorization)
    return autocollect_mod.paper_jobs_admin(limit=limit)


@app.get("/admin/autocollect/sets")
def admin_autocollect_sets(limit: int = Query(200, ge=1, le=2000),
                           url: str = Query(""),
                           authorization: str = Header(default="")):
    """套卷采集标记列表：每套题目链接的采集状态（complete/empty/incomplete）。
    传 url 精确查单页标记；否则返回最近 limit 条 + 汇总。"""
    _admin_user(authorization)
    return autocollect_mod.collected_sets_admin(url=url, limit=limit)


@app.get("/admin/autocollect/sets/questions")
def admin_autocollect_set_questions(url: str = Query(..., min_length=8),
                                    authorization: str = Header(default="")):
    """套卷溯源回查：按采集来源页 URL 列出本站已入库的同套题目。
    采集题出现问题时直接调本网站定位原因（看题干/答案/解析/配图入库结果）。"""
    _admin_user(authorization)
    rows = study_store.find_questions_by_source_url(url)
    mark = (autocollect_mod.collected_sets_admin(url=url) or {}).get("mark")
    return {"url": url, "mark": mark, "count": len(rows), "questions": rows}


class AutocollectSetsResetReq(BaseModel):
    urls: list[str] = []   # 空 = 清空全部套卷标记


@app.post("/admin/autocollect/sets/reset")
def admin_autocollect_sets_reset(req: AutocollectSetsResetReq = Body(
        default_factory=AutocollectSetsResetReq),
        authorization: str = Header(default="")):
    """重置套卷「已采集完整」标记，使对应链接下次采集重新提取（修复后重采入口）。"""
    _admin_user(authorization)
    return autocollect_mod.reset_collected_sets(req.urls or None)


class AdminAiCategorizeReq(BaseModel):
    teacher_id: str = "T001"
    qids: list[int] = []
    limit: int = 50


@app.post("/admin/questions/ai-categorize")
def admin_ai_categorize(
    req: AdminAiCategorizeReq = Body(default_factory=AdminAiCategorizeReq),
    authorization: str = Header(default=""),
):
    """#35 R1 批量 AI 补充课程分类：qids 为空时自动取未分类题目（≤50/次）。"""
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    try:
        return admin_biz.categorize_questions_ai(
            req.teacher_id, req.qids or None, req.limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/questions/{qid:int}/ai-analysis")
def admin_ai_analysis(qid: int, authorization: str = Header(default="")):
    """#35 R3 单题生成多角度 AI 解析并覆盖落库。返回 {analysis}。"""
    _admin_user(authorization)
    try:
        q = study_store.get_question(qid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    try:
        text = admin_biz.generate_ai_analysis(q)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    study_store.update_question(qid, analysis=text)
    return {"id": qid, "analysis": text}


class AdminAiAnalysisBatchReq(BaseModel):
    teacher_id: str = "T001"
    limit: int = 20


@app.post("/admin/questions/ai-analysis-batch")
def admin_ai_analysis_batch(
    req: AdminAiAnalysisBatchReq = Body(default_factory=AdminAiAnalysisBatchReq),
    authorization: str = Header(default=""),
):
    """#35 R3 批量给无解析题目生成 AI 解析（≤20/次，防超时；可多次点击分批补齐）。"""
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    limit = max(1, min(20, int(req.limit or 20)))
    with study_store._connect() as conn:
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
            study_store.update_question(int(q["id"]), analysis=text)
            generated += 1
        except Exception:  # noqa: BLE001  单题失败不中断批量
            failed += 1
    return {"generated": generated, "failed": failed, "total": len(qs)}


@app.get("/admin/questions/knowledge-points")
def admin_question_knowledge_points(
    teacher_id: str = "T001",
    authorization: str = Header(default=""),
):
    """某老师已有点知识清单（题库筛选下拉用）。"""
    _admin_user(authorization)
    from .study import get_study_store as _gss
    st = _gss()
    stats = st.question_stats(teacher_id)
    return {"points": [x["kp"] for x in stats["by_knowledge_point"]]}


# ================================================================
# #20 知识体系管理 · #21 AI 思维导图
# ================================================================
class AdminKnowledgeNodeReq(BaseModel):
    teacher_id: str = "T001"
    name: str = ""
    parent_id: str = ""
    category: str = ""
    description: str = ""
    tree_type: str = "knowledge"  # #24 R2：knowledge=知识树 | qtype=题型树


class AdminKnowledgeUpdateReq(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    sort_order: int | None = None
    parent_id: str | None = None


class AdminKnowledgeBatchReq(BaseModel):
    node_ids: list[str] = []
    target_parent_id: str = ""


class AdminMindmapReq(BaseModel):
    teacher_id: str = "T001"
    root_id: str = ""
    save_back: bool = False


def _knowledge_store():
    from .knowledge import get_knowledge_store
    return get_knowledge_store()


# ---------- F3 论坛（前台 forum.html：公开只读；发帖/回帖/点赞/删除需登录） ----------
class ForumPostReq(BaseModel):
    title: str = ""
    category: str = ""
    content: str = ""


class ForumReplyReq(BaseModel):
    content: str = ""


def _forum_optional_user(authorization: str) -> dict | None:
    if not authorization or not str(authorization).lower().startswith("bearer "):
        return None
    try:
        return _resolve_user(authorization)
    except HTTPException:
        return None


@app.get("/forum/categories")
def forum_categories():
    return {"categories": forum_store.categories()}


@app.get("/forum/posts")
def forum_list(category: str = "", sort: str = "new", keyword: str = "",
               page: int = 1, size: int = 20):
    items, total = forum_store.list_posts(category, sort, keyword, page, size)
    short = []
    for p in items:
        content = p.pop("content", "") or ""
        excerpt = content[:120] + ("…" if len(content) > 120 else "")
        short.append({**p, "excerpt": excerpt})
    return {"items": short, "total": total, "page": page, "size": size}


@app.post("/forum/posts")
def forum_create(req: ForumPostReq, authorization: str = Header(default="")):
    user = _forum_require_user(_resolve_user(authorization))
    title = req.title.strip()
    content = req.content.strip()
    if len(title) < 3 or len(title) > 80:
        raise HTTPException(status_code=400, detail="标题长度需在 3-80 字")
    if len(content) < 5 or len(content) > 8000:
        raise HTTPException(status_code=400, detail="内容长度需在 5-8000 字")
    if not _forum_write_ok(user["user_id"], 15.0):
        raise HTTPException(status_code=429, detail="发帖太快了，请 15 秒后再试")
    bad = _check_forum_text(title, content)
    if bad:
        raise HTTPException(status_code=400, detail=bad)
    return forum_store.create_post(
        user["user_id"], user.get("username") or "", title, content, req.category)


@app.get("/forum/posts/{post_id}")
def forum_detail(post_id: str, authorization: str = Header(default="")):
    forum_store.inc_view(post_id)
    p = forum_store.get_post(post_id)
    if p is None:
        raise HTTPException(status_code=404, detail="帖子不存在")
    u = _forum_optional_user(authorization)
    p["liked"] = bool(u) and forum_store.liked_by(post_id, u["user_id"])
    return p


@app.get("/forum/posts/{post_id}/replies")
def forum_replies(post_id: str, page: int = 1, size: int = 50):
    if forum_store.get_post(post_id) is None:
        raise HTTPException(status_code=404, detail="帖子不存在")
    items, total = forum_store.list_replies(post_id, page, size)
    return {"items": items, "total": total, "page": page, "size": size}


@app.post("/forum/posts/{post_id}/replies")
def forum_reply_create(post_id: str, req: ForumReplyReq,
                       authorization: str = Header(default="")):
    user = _forum_require_user(_resolve_user(authorization))
    content = req.content.strip()
    if not content or len(content) > 2000:
        raise HTTPException(status_code=400, detail="回复长度需在 1-2000 字")
    if not _forum_write_ok(user["user_id"], 5.0):
        raise HTTPException(status_code=429, detail="回复太快了，请稍后再试")
    bad = _check_forum_text("", content)
    if bad:
        raise HTTPException(status_code=400, detail=bad)
    try:
        return forum_store.add_reply(post_id, user["user_id"],
                                     user.get("username") or "", content)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/forum/posts/{post_id}/like")
def forum_like(post_id: str, authorization: str = Header(default="")):
    user = _forum_require_user(_resolve_user(authorization))
    try:
        return forum_store.toggle_like(post_id, user["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/forum/posts/{post_id}")
def forum_post_delete(post_id: str, authorization: str = Header(default="")):
    user = _forum_require_user(_resolve_user(authorization))
    try:
        forum_store.delete_post(post_id, user["user_id"], user.get("role") or "")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": True}


@app.delete("/forum/replies/{reply_id}")
def forum_reply_delete(reply_id: str, authorization: str = Header(default="")):
    user = _forum_require_user(_resolve_user(authorization))
    try:
        forum_store.delete_reply(reply_id, user["user_id"], user.get("role") or "")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": True}


# ---------- F3 论坛治理（举报/隐藏/置顶/公告；频控与违禁词前置拦截） ----------
class ForumReportReq(BaseModel):
    target_kind: str = "post"
    target_id: str = ""
    reason: str = ""


class ForumModStateReq(BaseModel):
    status: str = ""


class ForumPinReq(BaseModel):
    pinned: bool = False


class ForumNoticeReq(BaseModel):
    content: str = ""


_forum_write_cooldown: dict[str, float] = {}
_forum_write_lock = threading.Lock()


def _forum_write_ok(user_id: str, gap: float) -> bool:
    with _forum_write_lock:
        last = _forum_write_cooldown.get(user_id, 0.0)
        if _time.time() - last < gap:
            return False
        _forum_write_cooldown[user_id] = _time.time()
        return True


def _forum_require_admin(user: dict) -> None:
    if (user or {}).get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")


def _forum_require_user(user: dict) -> dict:
    """论坛写操作统一鉴权：匿名（无 token）返回 401，避免下层 user["user_id"] 触发 500。"""
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


@app.get("/public/forum/notice")
def forum_notice_public():
    return forum_store.get_notice()


@app.post("/forum/reports")
def forum_report_create(req: ForumReportReq,
                        authorization: str = Header(default="")):
    user = _forum_require_user(_resolve_user(authorization))
    reason = (req.reason or "").strip()[:200] or "违反社区规范"
    try:
        return forum_store.add_report(req.target_kind, req.target_id.strip(),
                                      user["user_id"], reason)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/forum/mod/posts")
def forum_mod_posts(status: str = "", category: str = "", keyword: str = "",
                    page: int = 1, size: int = 20,
                    authorization: str = Header(default="")):
    _forum_require_admin(_resolve_user(authorization))
    items, total = forum_store.mod_posts(status, category, keyword, page, size)
    return {"items": items, "total": total, "page": page, "size": size}


@app.post("/forum/mod/posts/{post_id}/state")
def forum_mod_state(post_id: str, req: ForumModStateReq,
                    authorization: str = Header(default="")):
    _forum_require_admin(_resolve_user(authorization))
    try:
        p = forum_store.set_post_state(post_id, status=req.status or None)
        if req.status == "hidden":
            forum_store.resolve_reports("post", post_id)
        return p
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/forum/mod/posts/{post_id}/pin")
def forum_mod_pin(post_id: str, req: ForumPinReq,
                  authorization: str = Header(default="")):
    _forum_require_admin(_resolve_user(authorization))
    try:
        return forum_store.set_post_state(post_id, pinned=bool(req.pinned))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/forum/mod/reports")
def forum_mod_reports(status: str = "pending", page: int = 1, size: int = 50,
                      authorization: str = Header(default="")):
    _forum_require_admin(_resolve_user(authorization))
    items, total = forum_store.list_reports(status, page, size)
    return {"items": items, "total": total, "page": page, "size": size}


@app.post("/forum/mod/reports/{target_kind}/{target_id}/resolve")
def forum_mod_report_resolve(target_kind: str, target_id: str,
                             authorization: str = Header(default="")):
    _forum_require_admin(_resolve_user(authorization))
    n = forum_store.resolve_reports(target_kind, target_id)
    return {"ok": True, "resolved": n}


@app.post("/forum/mod/notice")
def forum_mod_notice(req: ForumNoticeReq, authorization: str = Header(default="")):
    _forum_require_admin(_resolve_user(authorization))
    return forum_store.set_notice(req.content)


# ---------- 行为埋点（首页宣传入口点击等，匿名可上报；管理端聚合统计） ----------
class TrackPromoReq(BaseModel):
    target: str = ""
    page: str = ""


_track_limiter: dict[str, list[float]] = {}
_track_lock = threading.Lock()
_TRACK_WINDOW = 60
_TRACK_MAX = 20


def _track_allowed(ip: str) -> bool:
    now = _time.time()
    with _track_lock:
        if len(_track_limiter) > 4000:
            _track_limiter.clear()
        arr = [t for t in _track_limiter.get(ip, []) if now - t < _TRACK_WINDOW]
        if len(arr) >= _TRACK_MAX:
            _track_limiter[ip] = arr
            return False
        arr.append(now)
        _track_limiter[ip] = arr
        return True


@app.post("/track/promo")
def track_promo(req: TrackPromoReq, request: Request,
                user_agent: str = Header(default="")):
    """前台宣传卡点击上报（无登录要求，低频）：target ∈ mock/mind/forum/articles。"""
    target = (req.target or "").strip().lower()
    if target not in _PROMO_TARGETS:
        raise HTTPException(status_code=400,
                            detail="target 必须是 mock / mind / forum / articles 之一")
    ip = _client_ip(request)
    if not _track_allowed(ip):
        raise HTTPException(status_code=429, detail="上报过于频繁，请稍后再试")
    tracking_store.log("promo_click", target=target,
                       page=(req.page or "").strip()[:40], ip=ip, ua=user_agent)
    return {"ok": True}


@app.get("/admin/tracking/promo")
def admin_tracking_promo(authorization: str = Header(default="")):
    """宣传入口点击统计：total / 按 target 汇总 / 近 14 天趋势。"""
    _forum_require_admin(_resolve_user(authorization))
    return tracking_store.promo_stats()


# ---------- #23 首页 Banner 管理（banner.db：管理端配置轮播，前端展示） ----------
class BannerReq(BaseModel):
    title: str = ""
    image: str = ""
    link: str = ""
    sort: int = 0
    enabled: int = 1


@app.get("/banners")
def banners_public():
    """公开：仅返回启用且按 sort 排序的轮播列表（首页展示用，无需登录）。"""
    return {"banners": banner_store.list_enabled()}


@app.get("/admin/banners")
def admin_banners(authorization: str = Header(default="")):
    """管理端：全量轮播（含停用）。"""
    _forum_require_admin(_resolve_user(authorization))
    return {"banners": banner_store.list_all()}


@app.post("/admin/banners")
def admin_banners_create(req: BannerReq, authorization: str = Header(default="")):
    """新增轮播；image/link 仅接受 http(s)/站内相对路径（防 XSS 协议注入）。"""
    _forum_require_admin(_resolve_user(authorization))
    if not _banner_safe_url(req.image):
        raise HTTPException(status_code=400, detail="图片地址仅支持 http(s) 或站内相对路径")
    if not _banner_safe_url(req.link):
        raise HTTPException(status_code=400, detail="链接地址仅支持 http(s) 或站内相对路径")
    return banner_store.create(title=req.title, image=req.image, link=req.link,
                               sort=req.sort, enabled=req.enabled)


@app.put("/admin/banners/{banner_id}")
def admin_banners_update(banner_id: int, req: BannerReq,
                         authorization: str = Header(default="")):
    """更新轮播（全字段覆盖式提交）。"""
    _forum_require_admin(_resolve_user(authorization))
    if not _banner_safe_url(req.image):
        raise HTTPException(status_code=400, detail="图片地址仅支持 http(s) 或站内相对路径")
    if not _banner_safe_url(req.link):
        raise HTTPException(status_code=400, detail="链接地址仅支持 http(s) 或站内相对路径")
    row = banner_store.update(banner_id, title=req.title, image=req.image, link=req.link,
                              sort=req.sort, enabled=req.enabled)
    if row is None:
        raise HTTPException(status_code=404, detail="轮播不存在")
    return row


@app.delete("/admin/banners/{banner_id}")
def admin_banners_delete(banner_id: int, authorization: str = Header(default="")):
    """删除轮播。"""
    _forum_require_admin(_resolve_user(authorization))
    if not banner_store.delete(banner_id):
        raise HTTPException(status_code=404, detail="轮播不存在")
    return {"ok": True}


@app.post("/admin/banners/reorder")
def admin_banners_reorder(req: dict, authorization: str = Header(default="")):
    """批量重排：body={"ids":[1,2,3]} 按顺序覆盖 sort。"""
    _forum_require_admin(_resolve_user(authorization))
    ids = (req or {}).get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(x, int) for x in ids):
        raise HTTPException(status_code=400, detail="ids 必须是整数数组")
    return {"banners": banner_store.reorder(ids)}


# ---------- #19 消息中心（message.db：站内信/系统通知，未读角标 + 已读 + 管理端群发） ----------
class MessageReq(BaseModel):
    title: str = ""
    content: str = ""
    target: str = "all"        # all 全员 | user 定向
    user_id: str = ""          # target=user 时必填


def _message_user(authorization: str) -> dict:
    """消息中心用户守卫：个人消息需登录（匿名不产生消息数据）。"""
    user = _resolve_user(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


@app.get("/messages/unread")
def messages_unread(authorization: str = Header(default="")):
    """未读数（学习中心顶部角标轮询用）。"""
    user = _message_user(authorization)
    return {"count": message_store.unread_count(user["user_id"])}


@app.get("/messages")
def messages_list(page: int = 1, size: int = 20,
                  authorization: str = Header(default="")):
    """用户消息列表（倒序，含 read 状态）+ 未读总数。"""
    user = _message_user(authorization)
    data = message_store.list_for_user(user["user_id"], page=page, size=size)
    data["unread"] = message_store.unread_count(user["user_id"])
    return data


@app.post("/messages/{message_id}/read")
def messages_read(message_id: int, authorization: str = Header(default="")):
    """标记单条已读。"""
    user = _message_user(authorization)
    if not message_store.mark_read(user["user_id"], message_id):
        raise HTTPException(status_code=404, detail="消息不存在")
    return {"ok": True}


@app.post("/messages/read_all")
def messages_read_all(authorization: str = Header(default="")):
    """全部已读。"""
    user = _message_user(authorization)
    marked = message_store.mark_all_read(user["user_id"])
    return {"ok": True, "marked": marked}


@app.get("/admin/messages")
def admin_messages_list(authorization: str = Header(default="")):
    """管理端：全部消息 + 送达/已读统计。"""
    _forum_require_admin(_resolve_user(authorization))
    total_users = admin_biz.user_stats_admin().get("total", 0)
    items = []
    for m in message_store.list_all():
        delivered = 1 if m["target"] == "user" else total_users
        items.append({
            "id": m["id"], "title": m["title"], "content": m["content"],
            "target": m["target"], "target_user_id": m["target_user_id"],
            "created_at": m["created_at"], "delivered": delivered,
            "read": m.get("read_count", 0),
        })
    return {"messages": items}


@app.post("/admin/messages")
def admin_messages_create(req: MessageReq, authorization: str = Header(default="")):
    """群发/定向：target=all 全员；target=user 需 user_id。"""
    _forum_require_admin(_resolve_user(authorization))
    title = (req.title or "").strip()
    content = (req.content or "").strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="标题与内容不能为空")
    if req.target == "user" and not (req.user_id or "").strip():
        raise HTTPException(status_code=400, detail="定向消息必须指定用户 ID")
    return message_store.create(title=title, content=content,
                                target=req.target, target_user_id=req.user_id)


@app.delete("/admin/messages/{message_id}")
def admin_messages_delete(message_id: int, authorization: str = Header(default="")):
    """删除消息（连带已读记录）。"""
    _forum_require_admin(_resolve_user(authorization))
    if not message_store.delete(message_id):
        raise HTTPException(status_code=404, detail="消息不存在")
    return {"ok": True}


# ---------- F4 文章/经验帖（articles.html：热帖精选 + AI 要点提炼，AI 触发需登录） ----------
class ArticleAnalyzeReq(BaseModel):
    post_id: str = ""


_ARTICLE_PROMPT = """你是书山公考的资深备考内容编辑。阅读下面这篇学员经验帖（分类：{category}）与部分回帖，\
提炼对备考真正有用的干货。
要求：只说帖子中实际出现的观点，不要编造；输出严格 JSON（不要多余文字）：
{{
  "summary": "一句话总评（120 字内，概括帖主经验）",
  "key_points": ["要点1", "要点2", "要点3"],
  "advice": "给其他学员的一两句行动建议",
  "suggest_categories": ["建议关联的备考分类，最多 3 个"]
}}
标题：{title}
正文：
{body}"""

_article_cooldown: dict[str, float] = {}
_article_lock = threading.Lock()


def _article_cooldown_ok(user_id: str) -> bool:
    with _article_lock:
        last = _article_cooldown.get(user_id, 0.0)
        if _time.time() - last < 20:
            return False
        _article_cooldown[user_id] = _time.time()
        return True


@app.get("/articles/home")
def articles_home(category: str = "", size: int = 10):
    items, total = forum_store.list_posts(category, "hot", "", 1, min(max(size or 10, 1), 20))
    short = []
    for p in items:
        content = p.pop("content", "") or ""
        excerpt = content[:150] + ("…" if len(content) > 150 else "")
        short.append({**p, "excerpt": excerpt})
    amap = forum_store.analyses_map([x["post_id"] for x in short])
    for x in short:
        x["analyzed"] = x["post_id"] in amap
        x["analysis_summary"] = (amap.get(x["post_id"]) or "")[:120]
    return {"items": short, "total": total, "size": size}


@app.get("/articles/{post_id}")
def article_detail(post_id: str):
    forum_store.inc_view(post_id)
    post = forum_store.get_post(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    replies, _total = forum_store.list_replies(post_id, 1, 3)
    analysis = forum_store.get_analysis(post_id)
    return {**post, "replies": replies, "analysis": analysis}


@app.post("/articles/analyze")
def article_analyze(req: ArticleAnalyzeReq, authorization: str = Header(default="")):
    user = _resolve_user(authorization)
    post = forum_store.get_post(req.post_id.strip())
    if post is None:
        raise HTTPException(status_code=404, detail="帖子不存在")
    cached = forum_store.get_analysis(post["post_id"])
    if cached:
        return {"analysis": cached, "cached": True}
    if not _article_cooldown_ok(user["user_id"]):
        raise HTTPException(status_code=429, detail="AI 提炼太频繁，请 20 秒后再试")
    replies, _total = forum_store.list_replies(post["post_id"], 1, 5)
    body = post.get("content") or ""
    if replies:
        add = "\n".join(f"{r['username']}：{(r.get('content') or '')[:400]}"
                        for r in replies[:5])
        body = body[:6000] + "\n\n【部分回帖】\n" + add[:2000]
    prompt = _ARTICLE_PROMPT.format(
        category=post.get("category") or "", title=post.get("title") or "", body=body)
    tcfg = cfg.get_teacher("T001")
    try:
        out = _grade_llm_call([{"role": "user", "content": prompt}], tcfg, max_tokens=1800)
        try:
            obj = _extract_json_obj(out)
        except ValueError:
            out = _grade_llm_call([{"role": "user", "content": prompt}], tcfg, max_tokens=1800)
            obj = _extract_json_obj(out)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    summary = str(obj.get("summary") or "").strip() or "该帖暂未提炼出要点，建议阅读全文。"
    kps = obj.get("key_points")
    key_points = [str(x).strip() for x in kps if str(x).strip()][:6] if isinstance(kps, list) else []
    advice = str(obj.get("advice") or "").strip()
    cats = obj.get("suggest_categories")
    suggest = [str(x).strip() for x in cats if str(x).strip()][:4] if isinstance(cats, list) else []
    saved = forum_store.save_analysis(
        post["post_id"], user.get("username") or "", summary, key_points, advice, suggest)
    return {"analysis": saved, "cached": False}


# ---------- 公开只读知识体系接口（前台 knowledge.html 使用，无需鉴权） ----------
@app.get("/public/knowledge/tree")
def public_knowledge_tree(
    teacher_id: str = "T001",
    keyword: str = "",
    tree_type: str = "knowledge",
):
    """前台只读：知识体系/题型分类树。返回 {nodes: 树, total}。"""
    _check_teacher(teacher_id)
    try:
        data = _knowledge_store().get_tree(teacher_id, keyword, tree_type=tree_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"teacher_id": teacher_id, **data}


@app.get("/public/knowledge/stats")
def public_knowledge_stats(teacher_id: str = "T001", tree_type: str = "knowledge"):
    """前台只读：知识体系统计（节点总数 / 分类分布）。"""
    _check_teacher(teacher_id)
    try:
        return {"teacher_id": teacher_id, "tree_type": tree_type,
                **(_knowledge_store().stats(teacher_id, tree_type))}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/public/knowledge/export")
def public_knowledge_export(
    teacher_id: str = "T001",
    tree_type: str = "knowledge",
    fmt: str = "md",
    title: str = "",
):
    """前台只读：知识树导出内容（md=Markdown 大纲 / mm=FreeMind）。按当前老师+树类型取整树。"""
    _check_teacher(teacher_id)
    if fmt not in ("md", "mm"):
        raise HTTPException(status_code=400, detail="fmt 仅支持：md / mm")
    try:
        ks = _knowledge_store()
        tcfg = cfg.get_teacher(teacher_id)
        root_title = (title or "").strip() or f"{tcfg.teacher_name}-{tree_type}"
        data = ks.get_tree(teacher_id, "", tree_type=tree_type)
        pseudo = {"node_id": "", "name": root_title, "children": data["nodes"]}
        if fmt == "md":
            content = ks.to_markdown(teacher_id, title=root_title, tree=pseudo)
        else:
            content = ks.to_freemind(teacher_id, title=root_title, tree=pseudo)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ext = "md" if fmt == "md" else "mm"
    return {"teacher_id": teacher_id, "tree_type": tree_type,
            "filename": f"{root_title}.{ext}", "content": content}


# ---------- 题库分类管理（独立 question_category 表） ----------
class QCatCreateReq(BaseModel):
    teacher_id: str = "T001"
    category_id: str = ""
    parent_id: str = ""
    name: str = ""
    description: str = ""
    sort_order: int = 0


class QCatUpdateReq(BaseModel):
    name: str | None = None
    parent_id: str | None = None
    description: str | None = None
    sort_order: int | None = None
    enabled: bool | None = None


class QCatReorderReq(BaseModel):
    teacher_id: str = "T001"
    ordered: list[dict] = []


def _qcategory_store():
    from .qcategory import get_qcategory_store
    return get_qcategory_store()


@app.get("/admin/questions/categories")
def admin_qcat_list(teacher_id: str = "T001", authorization: str = Header(default="")):
    """题库分类树/列表（含 path 标注）。"""
    _admin_user(authorization)
    _check_teacher(teacher_id)
    cats = _qcategory_store().list_categories(teacher_id)
    return {"teacher_id": teacher_id, "categories": cats,
            "stats": _qcategory_store().stats(teacher_id)}


@app.post("/admin/questions/categories")
def admin_qcat_create(req: QCatCreateReq, authorization: str = Header(default="")):
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    try:
        cat = _qcategory_store().create_category(
            req.teacher_id, req.category_id, req.parent_id, req.name,
            req.description, req.sort_order)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return cat


@app.patch("/admin/questions/categories/{category_id}")
def admin_qcat_update(category_id: str, req: QCatUpdateReq, teacher_id: str = "T001",
                      authorization: str = Header(default="")):
    _admin_user(authorization)
    _check_teacher(teacher_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        cat = _qcategory_store().update_category(teacher_id, category_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if cat is None:
        raise HTTPException(status_code=404, detail=f"分类不存在: {category_id}")
    return cat


@app.delete("/admin/questions/categories/{category_id}")
def admin_qcat_delete(category_id: str, teacher_id: str = "T001",
                      cascade: bool = False, authorization: str = Header(default="")):
    _admin_user(authorization)
    _check_teacher(teacher_id)
    try:
        return _qcategory_store().delete_category(teacher_id, category_id, cascade=cascade)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/questions/categories/reorder")
def admin_qcat_reorder(req: QCatReorderReq, authorization: str = Header(default="")):
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    try:
        n = _qcategory_store().reorder(req.teacher_id, req.ordered)
        return {"updated": n}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/admin/knowledge/tree")
def admin_knowledge_tree(
    teacher_id: str = "T001",
    keyword: str = "",
    tree_type: str = "knowledge",
    authorization: str = Header(default=""),
):
    """知识体系树（keyword 检索时保留命中节点+祖先链）。
    #24 R2：tree_type=qtype 返回题型细分树（如 行测-判断推理-图形推理-黑白块）。"""
    _admin_user(authorization)
    _check_teacher(teacher_id)
    try:
        return _knowledge_store().get_tree(teacher_id, keyword, tree_type=tree_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/admin/knowledge/paths")
def admin_knowledge_paths(
    teacher_id: str = "T001",
    tree_type: str = "knowledge",
    authorization: str = Header(default=""),
):
    """#24 R2 全量节点路径映射 {node_id: '根/子/孙'}（题目列表展示分类路径用）。"""
    _admin_user(authorization)
    _check_teacher(teacher_id)
    return {"teacher_id": teacher_id, "tree_type": tree_type,
            "paths": _knowledge_store().node_paths(teacher_id, tree_type)}


@app.get("/admin/knowledge/stats")
def admin_knowledge_stats(
    teacher_id: str = "T001",
    tree_type: str = "knowledge",
    authorization: str = Header(default=""),
):
    _admin_user(authorization)
    _check_teacher(teacher_id)
    return _knowledge_store().stats(teacher_id, tree_type=tree_type)


@app.post("/admin/knowledge/nodes")
def admin_knowledge_create(
    req: AdminKnowledgeNodeReq = Body(default_factory=AdminKnowledgeNodeReq),
    authorization: str = Header(default=""),
):
    """创建知识节点（parent_id 空 = 根级）。#24 R2：tree_type 区分知识树/题型树。"""
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    try:
        return _knowledge_store().create_node(
            req.teacher_id, req.name, req.parent_id, req.category,
            req.description, req.tree_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/admin/knowledge/nodes/{node_id}")
def admin_knowledge_update(
    node_id: str,
    req: AdminKnowledgeUpdateReq = Body(default_factory=AdminKnowledgeUpdateReq),
    authorization: str = Header(default=""),
):
    """编辑节点（name/category/description/sort_order/parent_id 移动，含环检测）。"""
    _admin_user(authorization)
    try:
        return _knowledge_store().update_node(
            node_id, req.name, req.category, req.description,
            req.sort_order, req.parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/admin/knowledge/nodes/{node_id}")
def admin_knowledge_delete(
    node_id: str,
    authorization: str = Header(default=""),
):
    """删除节点及整个子树（级联）。"""
    _admin_user(authorization)
    try:
        return _knowledge_store().delete_node(node_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/admin/knowledge/batch-delete")
def admin_knowledge_batch_delete(
    req: AdminKnowledgeBatchReq = Body(default_factory=AdminKnowledgeBatchReq),
    authorization: str = Header(default=""),
):
    """批量删除（子树展开去重，幂等）。"""
    _admin_user(authorization)
    try:
        return _knowledge_store().batch_delete(req.node_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/knowledge/batch-move")
def admin_knowledge_batch_move(
    req: AdminKnowledgeBatchReq = Body(default_factory=AdminKnowledgeBatchReq),
    authorization: str = Header(default=""),
):
    """批量移动到目标父节点（环检测，非法整体失败）。"""
    _admin_user(authorization)
    try:
        return _knowledge_store().batch_move(req.node_ids, req.target_parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/knowledge/mindmap")
def admin_knowledge_mindmap(
    req: AdminMindmapReq = Body(default_factory=AdminMindmapReq),
    authorization: str = Header(default=""),
):
    """AI 思维导图：知识树 → LLM 增强节点说明 → 返回 tree/markdown/freemind 三格式。
    save_back=True 时增强结果回写节点 description（数据联动）。"""
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    try:
        return admin_biz.enhance_mindmap(req.teacher_id, req.root_id, req.save_back)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- #24 R3：文档建树 / 联网搜索建树 ----------
class BuildFromDocReq(BaseModel):
    """文档建树请求体。save=False 预演（只返回 AI 生成的树，不落库）。"""
    teacher_id: str = "T001"
    doc_name: str = ""
    root_name: str = ""
    parent_id: str = ""
    tree_type: str = "knowledge"
    save: bool = True


class BuildFromSearchReq(BaseModel):
    """联网搜索建树请求体。allow_fallback=True 搜索失败降级为纯 LLM 生成。"""
    teacher_id: str = "T001"
    query: str = ""
    root_id: str = ""
    root_name: str = ""
    tree_type: str = "knowledge"
    save: bool = True
    allow_fallback: bool = True


@app.get("/admin/knowledge/search-status")
def admin_knowledge_search_status(authorization: str = Header(default="")):
    """#24 R3 联网搜索是否可用（前端据此启停入口）。"""
    _admin_user(authorization)
    from .search import search_available, _provider
    return {"available": search_available(), "provider": _provider()}


@app.post("/admin/knowledge/build-from-doc")
def admin_knowledge_build_from_doc(
    req: BuildFromDocReq = Body(default_factory=BuildFromDocReq),
    authorization: str = Header(default=""),
):
    """#24 R3 文档建树：读取已上传文档（Word/PDF/Excel/md/txt/pptx）全文 →
    AI 整理知识点清单 → 落库成多级知识树（save=False 预演）。"""
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    if not req.doc_name.strip():
        raise HTTPException(status_code=400, detail="doc_name 不能为空")
    try:
        return admin_biz.build_tree_from_document(
            req.teacher_id, req.doc_name.strip(), req.root_name.strip(),
            req.parent_id.strip(), req.tree_type, req.save)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/knowledge/build-from-search")
def admin_knowledge_build_from_search(
    req: BuildFromSearchReq = Body(default_factory=BuildFromSearchReq),
    authorization: str = Header(default=""),
):
    """#24 R3 联网搜索建树：搜索公考资料 → AI 归纳知识点 → 落库（自动编写完善知识体系）。"""
    _admin_user(authorization)
    _check_teacher(req.teacher_id)
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")
    try:
        return admin_biz.enhance_tree_from_search(
            req.teacher_id, req.query.strip(), req.root_id.strip(),
            req.root_name.strip(), req.tree_type, req.save, req.allow_fallback)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ================================================================
# #25 问题2：AI 模型管理（注册表 + 默认模型 + 连通性测试）
# ================================================================
class AiModelCreateReq(BaseModel):
    name: str = ""
    model_id: str = ""
    provider: str = "anyyds"
    base_url: str = ""
    api_key: str = ""
    enabled: bool = True
    temperature: float = 0.3
    max_tokens: int = 2000
    remark: str = ""

class AiModelUpdateReq(BaseModel):
    name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    remark: str | None = None

class AiModelTestReq(BaseModel):
    model_id: str = ""
    base_url: str = ""
    api_key: str = ""


@app.get("/admin/ai-models")
def admin_ai_models(authorization: str = Header(default="")):
    """AI 模型注册表列表。"""
    _admin_user(authorization)
    return {"models": _get_aimodel().list_models()}


@app.post("/admin/ai-models")
def admin_ai_model_create(req: AiModelCreateReq = Body(default_factory=AiModelCreateReq),
                          authorization: str = Header(default="")):
    """新增模型。"""
    _admin_user(authorization)
    if not req.name.strip() or not req.model_id.strip():
        raise HTTPException(status_code=400, detail="名称与模型标识不能为空")
    try:
        m = _get_aimodel().create_model(
            req.name, req.model_id, req.provider, req.base_url, req.api_key,
            req.enabled, req.temperature, int(req.max_tokens or 2000), req.remark)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"model": m}


@app.get("/admin/ai-models/builtin")
def admin_ai_models_builtin(authorization: str = Header(default="")):
    """内置模型目录（OpenAI 兼容协议；含已注册标记，兜底展示用）。"""
    _admin_user(authorization)
    return {"models": builtin_catalog()}


@app.post("/admin/ai-models/list-remote")
def admin_ai_models_list_remote(
    base_url: str = Body(default=""),
    provider: str = Body(default=""),
    api_key: str = Body(default=""),
    authorization: str = Header(default=""),
):
    """获取远程模型列表（GET {base_url}/models，OpenAI 兼容协议）。

    失败自动兜底内置目录：used_fallback=true + error 说明；前端据此提示并降级。
    """
    _admin_user(authorization)
    return list_remote_models(base_url=base_url, provider=provider, api_key=api_key)


@app.patch("/admin/ai-models/{model_id:path}")
def admin_ai_model_update(model_id: str,
                          req: AiModelUpdateReq = Body(default_factory=AiModelUpdateReq),
                          authorization: str = Header(default="")):
    """更新模型字段。"""
    _admin_user(authorization)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    st = _get_aimodel()
    m = st.update_model(model_id, **fields)
    if not m:
        raise HTTPException(status_code=404, detail="模型不存在")
    return {"model": m}


@app.delete("/admin/ai-models/{model_id:path}")
def admin_ai_model_delete(model_id: str, authorization: str = Header(default="")):
    """删除模型（默认模型同时被清除，effective 回退 .env）。"""
    _admin_user(authorization)
    st = _get_aimodel()
    m = st.get_model(model_id)
    if not m:
        raise HTTPException(status_code=404, detail="模型不存在")
    if m.get("is_default"):
        st.set_setting("default_model", "")
    st.delete_model(model_id)
    return {"deleted": model_id}


@app.post("/admin/ai-models/{model_id:path}/default")
def admin_ai_model_set_default(model_id: str, authorization: str = Header(default="")):
    """设为全局默认模型（平台级 AI：题库采集/抽取/知识建树等）。"""
    _admin_user(authorization)
    st = _get_aimodel()
    try:
        m = st.set_default(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    st.set_setting("default_model", model_id)
    return {"default": m}


@app.get("/admin/ai-models/effective")
def admin_ai_model_effective(authorization: str = Header(default="")):
    """当前平台级 AI 生效配置（AI模型管理页顶部卡片）。api_key 脱敏后返回。"""
    _admin_user(authorization)
    eff = dict(_get_aimodel().effective_default())
    eff["api_key"] = _get_aimodel()._mask_key(eff.get("api_key", ""))
    return {"effective": eff}


@app.get("/admin/ai-models/{model_id:path}")
def admin_ai_model_detail(model_id: str, authorization: str = Header(default="")):
    """模型详情（含完整 api_key，供编辑弹窗回显）。"""
    _admin_user(authorization)
    st = _get_aimodel()
    m = st.get_model(model_id)
    if not m:
        raise HTTPException(status_code=404, detail="模型不存在")
    d = dict(m)
    d["enabled"] = bool(d["enabled"])
    d["is_default"] = bool(d["is_default"])
    return {"model": d}


def _aim_error_msg(e: Exception, model_id: str) -> str:
    """把网关/OpenAI SDK 异常提炼为可操作的中文提示（/test 与 /test-all 共用）。"""
    s = str(e)
    if "No available channel" in s or "无可用渠道" in s:
        return ("密钥分组下无此模型通道(503)：模型可能已从网关下线，或当前 API Key 无权使用；"
                "若此前测速可用，多为网关通道临时禁用（稍后自动恢复）。"
                "可重新「获取模型」查看最新列表，或在模型里配置有权限的独立密钥")
    if "尚未由管理员配置" in s or "has not been priced" in s:
        return "模型价格未配置(400)：站点管理员尚未为该模型定价，暂时无法使用"
    if "is an image model" in s or "is a video model" in s:
        return "该模型为图片/视频生成模型，不支持对话接口，无法用于问答"
    if "model_not_found" in s:
        return f"模型不存在(503)：{model_id} 在该 Base URL 下不存在，请核对模型标识"
    if "429" in s or "rpm exhausted" in s or "rate limit" in s.lower():
        return "限流(429)：该密钥请求频率超限（一键测速并发较高时易触发），稍后重试"
    if "401" in s or "authentication" in s.lower() or "Invalid API" in s:
        return "鉴权失败(401)：API Key 对该通道无效"
    if "403" in s:
        return "无权限(403)：上游通道拒绝（密钥无该模型权限或站点余额不足）"
    return f"{type(e).__name__}: {s[:140]}"


@app.post("/admin/ai-models/test")
def admin_ai_model_test(req: AiModelTestReq = Body(default_factory=AiModelTestReq),
                        authorization: str = Header(default="")):
    """连通性测试：对给定模型做一次极短补全，返回状态/延迟/内容摘要。
    密钥解析优先级：请求体显式 api_key > 注册表保存的 api_key > 全局 .env。
    base_url 规范化：自动去除尾部 /models（OpenAI 客户端自动拼 /chat/completions）。"""
    _admin_user(authorization)
    st = _get_aimodel()
    import time
    from openai import OpenAI

    m = None
    base_url = (req.base_url or "").strip()
    api_key = (req.api_key or "").strip()
    if req.model_id and not base_url:
        m = st.get_model(req.model_id)
        if not m:
            raise HTTPException(status_code=400, detail="模型不存在，且未提供 base_url")
        base_url = m.get("base_url", "")
    if not base_url:
        raise HTTPException(status_code=400, detail="未提供 Base URL，无法测试")
    base_url = st._norm_base_url(base_url)
    if not api_key:
        # 依次：注册表保存的明文密钥 → 全局 .env
        if m and m.get("api_key"):
            api_key = m["api_key"]
        else:
            api_key = store._GLOBAL.api_key
    if not api_key or api_key.startswith("sk-请") or "****" in api_key:
        raise HTTPException(status_code=400, detail="未配置有效 API_KEY，无法测试（请在模型里填写该通道的密钥）")
    key_source = ("表单密钥" if (req.api_key or "").strip()
                  else ("模型密钥" if (m and m.get("api_key")) else "全局密钥"))

    client = OpenAI(api_key=api_key, base_url=base_url,
                    timeout=min(store._GLOBAL.llm_timeout, 60), max_retries=0)
    t0 = time.time()
    try:
        # max_tokens=128：思考型模型的推理会消耗额度，太小正文为空
        resp = client.chat.completions.create(
            model=req.model_id, max_tokens=128,
            messages=[{"role": "user", "content": "请只回复：OK"}])
        lat = round(time.time() - t0, 2)
        # 兼容部分代理返回字符串/空结构而非标准对象
        try:
            content = (resp.choices[0].message.content or "")[:40]
        except (AttributeError, TypeError, IndexError):
            content = str(resp)[:40]
        # 反假阳性1：网关 200 却返回网页（Base URL 指向网站而非 API）
        if content.lstrip().lower().startswith(("<!doctype", "<html")):
            return {"ok": False, "model": req.model_id, "latency": lat,
                    "error": "通道返回网页而非模型回复：Base URL 可能指向网站页面而非 OpenAI 兼容 API",
                    "base_url": base_url, "key_source": key_source}
        # 反假阳性2：通道把错误提示当正文返回（限流/上游故障）
        if content.lstrip().startswith("⚠") or "所有模型均不可用" in content:
            return {"ok": False, "model": req.model_id, "latency": lat,
                    "error": f"通道返回错误提示而非模型回复（多为限流/上游故障，稍后重试）：{content.strip()[:60]}",
                    "base_url": base_url, "key_source": key_source}
        return {"ok": True, "model": req.model_id, "latency": lat,
                "reply": content, "base_url": base_url,
                "key_source": key_source}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "model": req.model_id,
                "latency": round(time.time() - t0, 2),
                "error": _aim_error_msg(e, req.model_id), "base_url": base_url,
                "key_source": key_source}


@app.post("/admin/ai-models/test-all")
async def admin_ai_model_test_all(authorization: str = Header(default="")):
    """批量测速全部启用模型（并发 4），用于"一键全部测速"。
    返回 [{model_id, ok, latency, error}]，按延迟升序。"""
    _admin_user(authorization)
    import asyncio
    import time
    from openai import OpenAI

    st = _get_aimodel()
    mods = st.list_models()
    enabled = [m for m in mods if m["enabled"]]
    if not enabled:
        raise HTTPException(status_code=400, detail="没有已启用的模型")

    import threading
    sem = threading.Semaphore(4)  # 并发上限 4：网关按密钥限 RPM，全量并发易触发 429

    def _one(m: dict) -> dict:
        # ⚠️ list_models 返回脱敏密钥，必须 get_model 取明文，否则含 **** 被判未配置
        raw = st.get_model(m["model_id"]) or m
        base_url = st._norm_base_url(raw.get("base_url") or "")
        api_key = (raw.get("api_key") or "").strip() or store._GLOBAL.api_key
        if not base_url or not api_key or api_key.startswith("sk-请"):
            return {"model_id": m["model_id"], "ok": False, "latency": 0,
                    "error": "未配置 Base URL / API Key"}
        client = OpenAI(api_key=api_key, base_url=base_url,
                        timeout=45, max_retries=0)
        t0 = time.time()
        try:
            with sem:
                # max_tokens=128：思考型模型推理耗额度，太小正文为空
                resp = client.chat.completions.create(
                    model=m["model_id"], max_tokens=128,
                    messages=[{"role": "user", "content": "请只回复：OK"}])
            lat = round(time.time() - t0, 2)
            try:
                content = (resp.choices[0].message.content or "")[:40]
            except (AttributeError, TypeError, IndexError):
                content = str(resp)[:40]
            # 反假阳性：200 但返回网页 = Base URL 指向网站而非 API
            if content.lstrip().lower().startswith(("<!doctype", "<html")):
                return {"model_id": m["model_id"], "ok": False, "latency": lat,
                        "error": "通道返回网页而非模型回复（Base URL 非 API 端点）"}
            # 反假阳性：通道把错误提示当正文返回（限流/上游故障）
            if content.lstrip().startswith("⚠") or "所有模型均不可用" in content:
                return {"model_id": m["model_id"], "ok": False, "latency": lat,
                        "error": f"通道返回错误提示（多为限流/上游故障，稍后重试）：{content.strip()[:60]}"}
            return {"model_id": m["model_id"], "ok": True,
                    "latency": lat, "error": "", "reply": content}
        except Exception as e:  # noqa: BLE001
            return {"model_id": m["model_id"], "ok": False,
                    "latency": round(time.time() - t0, 2),
                    "error": _aim_error_msg(e, m["model_id"])}

    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, _one, m) for m in enabled]
    results = await asyncio.gather(*tasks)
    results.sort(key=lambda r: (not r["ok"], r["latency"]))
    ok_n = sum(1 for r in results if r["ok"])
    return {"results": results, "ok_count": ok_n, "total": len(results)}


# ================================================================
# FastAPI startup：确保 admin 账号存在
# ================================================================
@app.on_event("startup")
def on_startup():
    import os
    if os.environ.get("ADMIN_USERNAME"):
        uid = auth_store.ensure_admin()
        if uid:
            print(f"[C2] admin 账号已初始化: {os.environ.get('ADMIN_USERNAME')}")
        else:
            print("[C2] ADMIN_USERNAME 已配置但密码为空，跳过 admin 初始化")
    autocollect_mod.start()


# ================================================================
# C3 · 运营数据看板路由（只读聚合，复用 stats_biz）
# 权限：双通道 —— ① X-Admin-Secret 头匹配 ADMIN_SECRET（任务书硬性契约）
#                  ② admin Bearer（C2 已上线，无缝切换，接口语义不变）
# 匿名/普通用户一律 401/403。
# ================================================================

def _admin_secret() -> str:
    """读取 ADMIN_SECRET 环境变量（未配置返回空串=关闭 X-Admin-Secret 通道）。"""
    import os
    return os.environ.get("ADMIN_SECRET", "").strip()


def _require_admin(authorization: str = "", x_admin_secret: str = ""):
    """运营看板鉴权：任一通道通过即可。
    - 通道① X-Admin-Secret == ADMIN_SECRET（任务书最简自洽方案）
    - 通道② Bearer token 为 admin 角色（C2 已上线，复用 _admin_user）
    两通道都失败 → 401/403。
    """
    secret = _admin_secret()
    if secret and x_admin_secret and hmac.compare_digest(x_admin_secret, secret):
        return {"source": "secret"}
    return _admin_user(authorization)


@app.get("/admin/stats/overview")
def admin_stats_overview(authorization: str = Header(default=""),
                         x_admin_secret: str = Header(default="")):
    """运营总览：总用户/会员数/今日新增/今日 ask/拦截/耗时。"""
    _require_admin(authorization, x_admin_secret)
    return stats_biz.overview(
        auth_store.db_path, chat_store.db_path,
        metrics.snapshot(), guard_counter.stats())


@app.get("/admin/stats/quota")
def admin_stats_quota(authorization: str = Header(default=""),
                      x_admin_secret: str = Header(default="")):
    """额度消耗分布（按日/用户）。"""
    _require_admin(authorization, x_admin_secret)
    return stats_biz.quota_dist(auth_store.db_path)


@app.get("/admin/stats/revenue")
def admin_stats_revenue(authorization: str = Header(default=""),
                        x_admin_secret: str = Header(default="")):
    """营收聚合（依赖 C1 pay.db；C1 未上线 → enabled=false 优雅降级）。"""
    _require_admin(authorization, x_admin_secret)
    return stats_biz.revenue(payment_store.db_path)


@app.get("/admin/stats/teachers")
def admin_stats_teachers(authorization: str = Header(default=""),
                         x_admin_secret: str = Header(default="")):
    """老师维度请求/拦截/满意度（热度与 /metrics by_teacher 一致）。"""
    _require_admin(authorization, x_admin_secret)
    return stats_biz.teacher_stats(
        metrics.snapshot(), guard_counter.stats(), cfg.list_teachers_all())


@app.get("/admin/stats/export")
def admin_stats_export(authorization: str = Header(default=""),
                       x_admin_secret: str = Header(default="")):
    """核心指标 CSV 导出（UTF-8 with BOM，Excel 打开不乱码）。"""
    _require_admin(authorization, x_admin_secret)
    csv_text = stats_biz.export_csv(
        auth_store.db_path, chat_store.db_path, payment_store.db_path,
        metrics.snapshot(), guard_counter.stats(), cfg.list_teachers_all())
    import time as _t
    return PlainTextResponse(
        csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="gk-stats-{_t.strftime("%Y%m%d-%H%M%S")}.csv"',
        },
    )


# ---------- #18 激励体系管理（配置/统计/排行榜/重算） ----------
class IncentiveConfigReq(BaseModel):
    """激励配置请求体：总开关 + 各事件积分（非法键/越界值自动忽略）。"""
    enabled: bool = True
    event_points: dict = {}


class IncentiveRecomputeReq(BaseModel):
    """激励重算请求体：user_id 留空 = 全部用户。"""
    user_id: str = ""


@app.get("/admin/incentive/stats")
def admin_incentive_stats(authorization: str = Header(default="")):
    """激励体系总览：配置 + 全局统计 + 积分榜 TOP20。"""
    _admin_user(authorization)
    return {
        "config": incentive_store.get_config(),
        "stats": incentive_store.admin_stats(),
        "board": incentive_store.board(limit=20),
    }


@app.post("/admin/incentive/config")
def admin_incentive_config(
    req: IncentiveConfigReq = Body(default_factory=IncentiveConfigReq),
    authorization: str = Header(default=""),
):
    """更新激励配置：总开关 + 各事件积分。"""
    _admin_user(authorization)
    return incentive_store.set_config(req.enabled, req.event_points)


@app.post("/admin/incentive/recompute")
def admin_incentive_recompute(
    req: IncentiveRecomputeReq = Body(default_factory=IncentiveRecomputeReq),
    authorization: str = Header(default=""),
):
    """激励数据全量重算：按 practice_logs 时间序幂等重放（管理员手动纠偏）。
    user_id 非空 = 仅重算该用户；留空 = 全部用户。"""
    _admin_user(authorization)
    logs = study_store.all_practice_logs()
    by_user: dict[str, list[dict]] = {}
    for log in logs:
        by_user.setdefault(log["user_id"], []).append(log)
    results = []
    for uid, ulogs in by_user.items():
        if req.user_id and uid != req.user_id:
            continue
        results.append(incentive_store.recompute_user(uid, ulogs, study_store))
    return {"recomputed": len(results), "results": results}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("poc.api:app", host="0.0.0.0", port=9000, reload=False)


# ================================================================
# C5 · P3 视频转写（预留接口，阻塞中不实现）
# 现状（docs/项目状态总览.md §六）：中转站无 whisper/audio 通道、服务器无
# ffmpeg、2C/3.6G 跑不动本地 ASR → 死胡同。此处仅预留 OpenAI 兼容 audio 接口
# 契约，待上游（中转站）暴露 whisper 后只需填 base_url/模型名即可启用。
# 启用条件：环境变量 WHISPER_ENABLED=1 + WHISPER_BASE_URL/WHISPER_API_KEY/WHISPER_MODEL。
# ================================================================
import os as _os_whisper


@app.get("/audio/transcriptions/status")
def audio_status():
    """转写能力状态（前端据此显示/隐藏视频上传入口）。"""
    enabled = (
        _os_whisper.getenv("WHISPER_ENABLED", "").strip() == "1"
        and bool(_os_whisper.getenv("WHISPER_API_KEY", "").strip())
    )
    return {
        "enabled": enabled,
        "note": "上游中转站暴露 whisper 后可启用；当前 2C/3.6G 不做本地 ASR（OOM 风险）",
    }


@app.post("/audio/transcriptions")
async def audio_transcriptions(request: Request):
    """OpenAI 兼容 audio/transcriptions 预留桩（multipart: file + model）。
    未启用 → 501 明确报错；启用 → 转发上游 OpenAI 兼容端点。
    """
    if not (_os_whisper.getenv("WHISPER_ENABLED", "").strip() == "1"):
        raise HTTPException(
            status_code=501,
            detail="视频转写未启用：上游无 whisper 通道且服务器资源不足。"
                   "详见 docs/项目状态总览.md §六（P3 阻塞项）。",
        )
    # ── 启用分支（上游就绪后无需改此桩，填环境变量即可） ──
    from openai import OpenAI
    wc = OpenAI(
        api_key=_os_whisper.getenv("WHISPER_API_KEY", ""),
        base_url=_os_whisper.getenv("WHISPER_BASE_URL", store._GLOBAL.api_base_url),
        timeout=float(_os_whisper.getenv("WHISPER_TIMEOUT", "300")),
        max_retries=2,
    )
    form = await request.form()
    f = form.get("file")
    model = form.get("model") or _os_whisper.getenv("WHISPER_MODEL", "whisper-1")
    if f is None:
        raise HTTPException(status_code=400, detail="缺少 file 字段")
    raw = await f.read()
    if len(raw) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="视频文件超过 100MB 上限")
    resp = wc.audio.transcriptions.create(model=model, file=("audio", raw, f.content_type or "audio/mpeg"))
    return {"text": resp.text}
