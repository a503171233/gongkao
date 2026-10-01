# -*- coding: utf-8 -*-
"""F3 论坛路由（前台 + 治理）"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .. import deps as _deps
from ..forum import check_forum_text as _check_forum_text

router = APIRouter()


# ---------- 请求体 ----------
class ForumPostReq(BaseModel):
    title: str = ""
    category: str = ""
    content: str = ""


class ForumReplyReq(BaseModel):
    content: str = ""


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


# ---------- 鉴权 helpers ----------
def _forum_optional_user(authorization: str) -> dict | None:
    if not authorization or not str(authorization).lower().startswith("bearer "):
        return None
    try:
        return _deps._resolve_user(authorization)
    except HTTPException:
        return None


def _forum_require_user(user: dict) -> dict:
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


# ========== 前台论坛 ==========

@router.get("/forum/categories")
def forum_categories():
    return {"categories": _deps.forum_store.categories()}


@router.get("/forum/posts")
def forum_list(category: str = "", sort: str = "new", keyword: str = "",
               page: int = 1, size: int = 20):
    items, total = _deps.forum_store.list_posts(category, sort, keyword, page, size)
    short = []
    for p in items:
        content = p.pop("content", "") or ""
        excerpt = content[:120] + ("…" if len(content) > 120 else "")
        short.append({**p, "excerpt": excerpt})
    return {"items": short, "total": total, "page": page, "size": size}


@router.post("/forum/posts")
def forum_create(req: ForumPostReq, authorization: str = Header(default="")):
    user = _forum_require_user(_deps._resolve_user(authorization))
    title = req.title.strip()
    content = req.content.strip()
    if len(title) < 3 or len(title) > 80:
        raise HTTPException(status_code=400, detail="标题长度需在 3-80 字")
    if len(content) < 5 or len(content) > 8000:
        raise HTTPException(status_code=400, detail="内容长度需在 5-8000 字")
    if not _deps._forum_write_ok(user["user_id"], 15.0):
        raise HTTPException(status_code=429, detail="发帖太快了，请 15 秒后再试")
    bad = _check_forum_text(title, content)
    if bad:
        raise HTTPException(status_code=400, detail=bad)
    return _deps.forum_store.create_post(
        user["user_id"], user.get("username") or "", title, content, req.category)


@router.get("/forum/posts/{post_id}")
def forum_detail(post_id: str, authorization: str = Header(default="")):
    _deps.forum_store.inc_view(post_id)
    p = _deps.forum_store.get_post(post_id)
    if p is None:
        raise HTTPException(status_code=404, detail="帖子不存在")
    u = _forum_optional_user(authorization)
    p["liked"] = bool(u) and _deps.forum_store.liked_by(post_id, u["user_id"])
    return p


@router.get("/forum/posts/{post_id}/replies")
def forum_replies(post_id: str, page: int = 1, size: int = 50):
    if _deps.forum_store.get_post(post_id) is None:
        raise HTTPException(status_code=404, detail="帖子不存在")
    items, total = _deps.forum_store.list_replies(post_id, page, size)
    return {"items": items, "total": total, "page": page, "size": size}


@router.post("/forum/posts/{post_id}/replies")
def forum_reply_create(post_id: str, req: ForumReplyReq,
                       authorization: str = Header(default="")):
    user = _forum_require_user(_deps._resolve_user(authorization))
    content = req.content.strip()
    if not content or len(content) > 2000:
        raise HTTPException(status_code=400, detail="回复长度需在 1-2000 字")
    if not _deps._forum_write_ok(user["user_id"], 5.0):
        raise HTTPException(status_code=429, detail="回复太快了，请稍后再试")
    bad = _check_forum_text("", content)
    if bad:
        raise HTTPException(status_code=400, detail=bad)
    try:
        reply = _deps.forum_store.add_reply(post_id, user["user_id"],
                                             user.get("username") or "", content)
        post = _deps.forum_store.get_post(post_id)
        if post and post.get("user_id") and post["user_id"] != user["user_id"]:
            try:
                title = reply.get("username", "")[:16] + " 回复了你的帖子"
                body = (reply.get("content", "") or "")[:60]
                if len(reply.get("content", "") or "") > 60:
                    body += "..."
                _deps.message_store.create(
                    title=title, content=body,
                    target="user", target_user_id=post["user_id"])
            except Exception:
                pass
        return reply
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/forum/posts/{post_id}/like")
def forum_like(post_id: str, authorization: str = Header(default="")):
    user = _forum_require_user(_deps._resolve_user(authorization))
    try:
        return _deps.forum_store.toggle_like(post_id, user["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/forum/posts/{post_id}")
def forum_post_delete(post_id: str, authorization: str = Header(default="")):
    user = _forum_require_user(_deps._resolve_user(authorization))
    try:
        _deps.forum_store.delete_post(post_id, user["user_id"], user.get("role") or "")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": True}


@router.delete("/forum/replies/{reply_id}")
def forum_reply_delete(reply_id: str, authorization: str = Header(default="")):
    user = _forum_require_user(_deps._resolve_user(authorization))
    try:
        _deps.forum_store.delete_reply(reply_id, user["user_id"], user.get("role") or "")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": True}


# ========== 论坛治理 ==========

@router.get("/public/forum/notice")
def forum_notice_public():
    return _deps.forum_store.get_notice()


@router.post("/forum/reports")
def forum_report_create(req: ForumReportReq,
                        authorization: str = Header(default="")):
    user = _forum_require_user(_deps._resolve_user(authorization))
    reason = (req.reason or "").strip()[:200] or "违反社区规范"
    try:
        return _deps.forum_store.add_report(req.target_kind, req.target_id.strip(),
                                            user["user_id"], reason)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/forum/mod/posts")
def forum_mod_posts(status: str = "", category: str = "", keyword: str = "",
                    page: int = 1, size: int = 20,
                    authorization: str = Header(default="")):
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    items, total = _deps.forum_store.mod_posts(status, category, keyword, page, size)
    return {"items": items, "total": total, "page": page, "size": size}


@router.post("/forum/mod/posts/{post_id}/state")
def forum_mod_state(post_id: str, req: ForumModStateReq,
                    authorization: str = Header(default="")):
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    try:
        p = _deps.forum_store.set_post_state(post_id, status=req.status or None)
        if req.status == "hidden":
            _deps.forum_store.resolve_reports("post", post_id)
        return p
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/forum/mod/posts/{post_id}/pin")
def forum_mod_pin(post_id: str, req: ForumPinReq,
                  authorization: str = Header(default="")):
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    try:
        return _deps.forum_store.set_post_state(post_id, pinned=bool(req.pinned))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/forum/mod/reports")
def forum_mod_reports(status: str = "pending", page: int = 1, size: int = 50,
                      authorization: str = Header(default="")):
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    items, total = _deps.forum_store.list_reports(status, page, size)
    return {"items": items, "total": total, "page": page, "size": size}


@router.post("/forum/mod/reports/{target_kind}/{target_id}/resolve")
def forum_mod_report_resolve(target_kind: str, target_id: str,
                             authorization: str = Header(default="")):
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    n = _deps.forum_store.resolve_reports(target_kind, target_id)
    return {"ok": True, "resolved": n}


@router.post("/forum/mod/notice")
def forum_mod_notice(req: ForumNoticeReq, authorization: str = Header(default="")):
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    return _deps.forum_store.set_notice(req.content)