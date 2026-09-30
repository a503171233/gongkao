# -*- coding: utf-8 -*-
"""#19 消息中心（message.db：站内信/系统通知，未读角标 + 已读 + 管理端群发）。"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .. import deps as _deps
from .. import admin as admin_biz

router = APIRouter()


class MessageReq(BaseModel):
    title: str = ""
    content: str = ""
    target: str = "all"        # all 全员 | user 定向
    user_id: str = ""          # target=user 时必填


def _message_user(authorization: str) -> dict:
    """消息中心用户守卫：个人消息需登录（匿名不产生消息数据）。"""
    user = _deps._resolve_user(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


@router.get("/messages/unread")
def messages_unread(authorization: str = Header(default="")):
    """未读数（学习中心顶部角标轮询用）。"""
    user = _message_user(authorization)
    return {"count": _deps.message_store.unread_count(user["user_id"])}


@router.get("/messages")
def messages_list(page: int = 1, size: int = 20,
                  authorization: str = Header(default="")):
    """用户消息列表（倒序，含 read 状态）+ 未读总数。"""
    user = _message_user(authorization)
    data = _deps.message_store.list_for_user(user["user_id"], page=page, size=size)
    data["unread"] = _deps.message_store.unread_count(user["user_id"])
    return data


@router.post("/messages/{message_id}/read")
def messages_read(message_id: int, authorization: str = Header(default="")):
    """标记单条已读。"""
    user = _message_user(authorization)
    if not _deps.message_store.mark_read(user["user_id"], message_id):
        raise HTTPException(status_code=404, detail="消息不存在")
    return {"ok": True}


@router.post("/messages/read_all")
def messages_read_all(authorization: str = Header(default="")):
    """全部已读。"""
    user = _message_user(authorization)
    marked = _deps.message_store.mark_all_read(user["user_id"])
    return {"ok": True, "marked": marked}


@router.get("/admin/messages")
def admin_messages_list(authorization: str = Header(default="")):
    """管理端：全部消息 + 送达/已读统计。"""
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    total_users = admin_biz.user_stats_admin().get("total", 0)
    items = []
    for m in _deps.message_store.list_all():
        delivered = 1 if m["target"] == "user" else total_users
        items.append({
            "id": m["id"], "title": m["title"], "content": m["content"],
            "target": m["target"], "target_user_id": m["target_user_id"],
            "created_at": m["created_at"], "delivered": delivered,
            "read": m.get("read_count", 0),
        })
    return {"messages": items}


@router.post("/admin/messages")
def admin_messages_create(req: MessageReq, authorization: str = Header(default="")):
    """群发/定向：target=all 全员；target=user 需 user_id。"""
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    title = (req.title or "").strip()
    content = (req.content or "").strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="标题与内容不能为空")
    if req.target == "user" and not (req.user_id or "").strip():
        raise HTTPException(status_code=400, detail="定向消息必须指定用户 ID")
    return _deps.message_store.create(title=title, content=content,
                                      target=req.target, target_user_id=req.user_id)


@router.delete("/admin/messages/{message_id}")
def admin_messages_delete(message_id: int, authorization: str = Header(default="")):
    """删除消息（连带已读记录）。"""
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    if not _deps.message_store.delete(message_id):
        raise HTTPException(status_code=404, detail="消息不存在")
    return {"ok": True}