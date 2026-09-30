# -*- coding: utf-8 -*-
"""#23 首页 Banner 管理（banner.db：管理端配置轮播，前端展示）。"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .. import deps as _deps
from ..banner import is_safe_url as _banner_safe_url

router = APIRouter()


class BannerReq(BaseModel):
    title: str = ""
    image: str = ""
    link: str = ""
    sort: int = 0
    enabled: int = 1


@router.get("/banners")
def banners_public():
    """公开：仅返回启用且按 sort 排序的轮播列表（首页展示用，无需登录）。"""
    return {"banners": _deps.banner_store.list_enabled()}


@router.get("/admin/banners")
def admin_banners(authorization: str = Header(default="")):
    """管理端：全量轮播（含停用）。"""
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    return {"banners": _deps.banner_store.list_all()}


@router.post("/admin/banners")
def admin_banners_create(req: BannerReq, authorization: str = Header(default="")):
    """新增轮播；image/link 仅接受 http(s)/站内相对路径（防 XSS 协议注入）。"""
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    if not _banner_safe_url(req.image):
        raise HTTPException(status_code=400, detail="图片地址仅支持 http(s) 或站内相对路径")
    if not _banner_safe_url(req.link):
        raise HTTPException(status_code=400, detail="链接地址仅支持 http(s) 或站内相对路径")
    return _deps.banner_store.create(title=req.title, image=req.image, link=req.link,
                                     sort=req.sort, enabled=req.enabled)


@router.put("/admin/banners/{banner_id}")
def admin_banners_update(banner_id: int, req: BannerReq,
                         authorization: str = Header(default="")):
    """更新轮播（全字段覆盖式提交）。"""
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    if not _banner_safe_url(req.image):
        raise HTTPException(status_code=400, detail="图片地址仅支持 http(s) 或站内相对路径")
    if not _banner_safe_url(req.link):
        raise HTTPException(status_code=400, detail="链接地址仅支持 http(s) 或站内相对路径")
    row = _deps.banner_store.update(banner_id, title=req.title, image=req.image, link=req.link,
                                    sort=req.sort, enabled=req.enabled)
    if row is None:
        raise HTTPException(status_code=404, detail="轮播不存在")
    return row


@router.delete("/admin/banners/{banner_id}")
def admin_banners_delete(banner_id: int, authorization: str = Header(default="")):
    """删除轮播。"""
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    if not _deps.banner_store.delete(banner_id):
        raise HTTPException(status_code=404, detail="轮播不存在")
    return {"ok": True}


@router.post("/admin/banners/reorder")
def admin_banners_reorder(req: dict, authorization: str = Header(default="")):
    """批量重排：body={"ids":[1,2,3]} 按顺序覆盖 sort。"""
    _deps._forum_require_admin(_deps._resolve_user(authorization))
    ids = (req or {}).get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(x, int) for x in ids):
        raise HTTPException(status_code=400, detail="ids 必须是整数数组")
    return {"banners": _deps.banner_store.reorder(ids)}