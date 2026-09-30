# -*- coding: utf-8 -*-
"""F4 文章/经验帖（articles.html：热帖精选 + AI 要点提炼）。"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .. import deps as _deps

router = APIRouter()


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


@router.get("/articles/home")
def articles_home(category: str = "", size: int = 10):
    items, total = _deps.forum_store.list_posts(category, "hot", "", 1, min(max(size or 10, 1), 20))
    short = []
    for p in items:
        content = p.pop("content", "") or ""
        excerpt = content[:150] + ("…" if len(content) > 150 else "")
        short.append({**p, "excerpt": excerpt})
    amap = _deps.forum_store.analyses_map([x["post_id"] for x in short])
    for x in short:
        x["analyzed"] = x["post_id"] in amap
        x["analysis_summary"] = (amap.get(x["post_id"]) or "")[:120]
    return {"items": short, "total": total, "size": size}


@router.get("/articles/{post_id}")
def article_detail(post_id: str):
    _deps.forum_store.inc_view(post_id)
    post = _deps.forum_store.get_post(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    replies, _total = _deps.forum_store.list_replies(post_id, 1, 3)
    analysis = _deps.forum_store.get_analysis(post_id)
    return {**post, "replies": replies, "analysis": analysis}


@router.post("/articles/analyze")
def article_analyze(req: ArticleAnalyzeReq, authorization: str = Header(default="")):
    user = _deps._resolve_user(authorization)
    post = _deps.forum_store.get_post(req.post_id.strip())
    if post is None:
        raise HTTPException(status_code=404, detail="帖子不存在")
    cached = _deps.forum_store.get_analysis(post["post_id"])
    if cached:
        return {"analysis": cached, "cached": True}
    if not _deps._article_cooldown_ok(user["user_id"]):
        raise HTTPException(status_code=429, detail="AI 提炼太频繁，请 20 秒后再试")
    replies, _total = _deps.forum_store.list_replies(post["post_id"], 1, 5)
    body = post.get("content") or ""
    if replies:
        add = "\n".join(f"{r['username']}：{(r.get('content') or '')[:400]}"
                        for r in replies[:5])
        body = body[:6000] + "\n\n【部分回帖】\n" + add[:2000]
    prompt = _ARTICLE_PROMPT.format(
        category=post.get("category") or "", title=post.get("title") or "", body=body)
    tcfg = _deps.cfg.get_teacher("T001")
    try:
        out = _deps._grade_llm_call([{"role": "user", "content": prompt}], tcfg, max_tokens=1800)
        try:
            obj = _deps._extract_json_obj(out)
        except ValueError:
            out = _deps._grade_llm_call([{"role": "user", "content": prompt}], tcfg, max_tokens=1800)
            obj = _deps._extract_json_obj(out)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    summary = str(obj.get("summary") or "").strip() or "该帖暂未提炼出要点，建议阅读全文。"
    kps = obj.get("key_points")
    key_points = [str(x).strip() for x in kps if str(x).strip()][:6] if isinstance(kps, list) else []
    advice = str(obj.get("advice") or "").strip()
    cats = obj.get("suggest_categories")
    suggest = [str(x).strip() for x in cats if str(x).strip()][:4] if isinstance(cats, list) else []
    saved = _deps.forum_store.save_analysis(
        post["post_id"], user.get("username") or "", summary, key_points, advice, suggest)
    return {"analysis": saved, "cached": False}