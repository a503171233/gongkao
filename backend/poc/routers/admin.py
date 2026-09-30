# -*- coding: utf-8 -*-
"""管理后台路由（C2）：统计、老师 CRUD、用户管理、课件管理、文档注册表、
充值码管理、数据备份 / 恢复、答案反馈工单。

路由按「静态路径先于动态路径」的原声明顺序保留，避免 FastAPI 路径遮蔽。
"""

from fastapi import APIRouter, Body, Header, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from .. import deps as _deps
from .. import store
from .. import admin as admin_biz
from ..config import Config
from ..payment import get_payment_store

router = APIRouter()


# ---------- 统计 ----------
@router.get("/admin/stats")
def admin_stats(authorization: str = Header(default="")):
    """后台首页统计。"""
    _deps._admin_user(authorization)
    return admin_biz.admin_stats()


# ---------- 老师 CRUD ----------
@router.get("/admin/teachers")
def admin_list_teachers(authorization: str = Header(default="")):
    """老师列表（含 enabled）。"""
    _deps._admin_user(authorization)
    return {"teachers": admin_biz.list_teachers_admin()}


@router.post("/admin/teachers")
def admin_create_teacher(
    payload: dict,
    authorization: str = Header(default=""),
):
    """新增老师。"""
    _deps._admin_user(authorization)
    try:
        return admin_biz.create_teacher(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/admin/teachers/sort")
def admin_sort_teachers(
    payload: dict,
    authorization: str = Header(default=""),
):
    """批量保存老师排序。body: {items: [{teacher_id, sort_order}]}。
    静态路由：必须注册在 PUT /admin/teachers/{teacher_id} 动态路由之前（防遮蔽）。"""
    _deps._admin_user(authorization)
    items = (payload or {}).get("items") or []
    try:
        return admin_biz.sort_teachers(items)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/admin/teachers/{teacher_id}")
def admin_update_teacher(
    teacher_id: str,
    payload: dict,
    authorization: str = Header(default=""),
):
    """编辑老师参数。"""
    _deps._admin_user(authorization)
    try:
        return admin_biz.update_teacher(teacher_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/admin/teachers/{teacher_id}")
def admin_disable_teacher(
    teacher_id: str,
    authorization: str = Header(default=""),
):
    """软下线老师（enabled=false）。"""
    _deps._admin_user(authorization)
    try:
        return admin_biz.disable_teacher(teacher_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- 用户管理 ----------
@router.get("/admin/users/stats")
def admin_user_stats(authorization: str = Header(default="")):
    """用户统计：总量 / 角色分布 / 状态分布 / 今日活跃 / 当前会员数。
    静态路由，必须注册在 /admin/users/{user_id} 动态路由之前。"""
    _deps._admin_user(authorization)
    return admin_biz.user_stats_admin()


@router.get("/admin/users")
def admin_list_users(
    q: str = "",
    limit: int = 200,
    offset: int = 0,
    authorization: str = Header(default=""),
):
    """用户列表（含 role/额度/到期）。"""
    _deps._admin_user(authorization)
    users = admin_biz.list_users_admin(limit=limit, offset=offset, q=q)
    return {"users": users, "limit": limit, "offset": offset}


@router.get("/admin/users/{user_id}")
def admin_get_user(
    user_id: str,
    authorization: str = Header(default=""),
):
    """单个用户详情。"""
    _deps._admin_user(authorization)
    u = admin_biz.get_user_admin(user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    return u


@router.post("/admin/users/{user_id}/set_role")
def admin_set_role(
    user_id: str,
    role: str = Body(...),
    days: int = Body(default=30),
    authorization: str = Header(default=""),
):
    """手动开通/回退会员。role=free|member|admin，days 仅 member 生效。"""
    _deps._admin_user(authorization)
    try:
        return admin_biz.set_user_role(user_id, role, days=days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/users/{user_id}/grant")
def admin_grant(
    user_id: str,
    kind: str = Body(...),
    value: int = Body(default=0),
    authorization: str = Header(default=""),
):
    """手动充值：kind='days'(+N天) 或 'reset_today'(重置今日已用)。"""
    _deps._admin_user(authorization)
    try:
        return admin_biz.grant_user(user_id, kind, value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/users/{user_id}/status")
def admin_user_status(
    user_id: str,
    status: str = Body(...),
    authorization: str = Header(default=""),
):
    """启用/禁用用户账号。status ∈ active|disabled，禁用后无法登录。"""
    _deps._admin_user(authorization)
    try:
        return admin_biz.set_user_status(user_id, status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: str,
    new_password: str = Body(...),
    authorization: str = Header(default=""),
):
    """管理员重置用户密码（无邮箱方案）。"""
    _deps._admin_user(authorization)
    try:
        return admin_biz.reset_user_password_admin(user_id, new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class AdminUserCreateReq(BaseModel):
    username: str = ""
    password: str = ""
    role: str = "free"


@router.post("/admin/users")
def admin_create_user(
    req: AdminUserCreateReq = Body(default_factory=AdminUserCreateReq),
    authorization: str = Header(default=""),
):
    """管理员新增用户。"""
    _deps._admin_user(authorization)
    try:
        return admin_biz.create_user_admin(req.username, req.password, req.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/admin/users/{user_id}")
def admin_delete_user(
    user_id: str,
    authorization: str = Header(default=""),
):
    """管理员删除用户。"""
    _deps._admin_user(authorization)
    try:
        return admin_biz.delete_user_admin(user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- 课件管理（后台视角） ----------
@router.get("/admin/documents")
def admin_documents(
    teacher_id: str,
    authorization: str = Header(default=""),
):
    """知识库文档列表（指定老师）。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(teacher_id)
    return {"teacher_id": teacher_id, "documents": store.list_documents(teacher_id)}


@router.delete("/admin/documents/{doc_name}")
def admin_delete_document(
    doc_name: str,
    teacher_id: str = "T001",
    authorization: str = Header(default=""),
):
    """删除指定老师文档（触发缓存失效 + #24 R5 注册表联动清理）。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(teacher_id)
    n = store.delete_document(teacher_id, doc_name)
    reg_deleted = False
    try:  # #24 R5：向量库与注册表保持一致（注册表故障不阻断删除）
        from ..docreg import get_doc_registry
        reg_deleted = get_doc_registry().delete(teacher_id, doc_name)
    except Exception:  # noqa: BLE001
        pass
    return {"deleted": n, "registry_deleted": reg_deleted,
            "teacher_id": teacher_id, "doc_name": doc_name}


@router.get("/admin/documents/preview")
def admin_preview_document(
    teacher_id: str,
    doc_name: str,
    authorization: str = Header(default=""),
):
    """读取指定老师原始课件内容（预览）。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(teacher_id)
    try:
        return admin_biz.preview_document(teacher_id, doc_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


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
    from ..docreg import get_doc_registry
    return get_doc_registry()


@router.get("/admin/documents/registry")
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
    _deps._admin_user(authorization)
    _deps._check_teacher(teacher_id)
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


@router.post("/admin/documents/meta")
def admin_documents_meta(
    req: DocMetaReq = Body(default_factory=DocMetaReq),
    authorization: str = Header(default=""),
):
    """#24 R5 编辑元数据/上下架。下架即时生效（检索过滤 + 缓存失效）。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(req.teacher_id)
    if not req.doc_name.strip():
        raise HTTPException(status_code=400, detail="doc_name 不能为空")
    try:
        return _doc_registry().set_meta(
            req.teacher_id, req.doc_name.strip(),
            req.category, req.tags, req.enabled)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/admin/documents/batch")
def admin_documents_batch(
    req: DocBatchReq = Body(default_factory=DocBatchReq),
    authorization: str = Header(default=""),
):
    """#25 批量上下架/归类：对 doc_names 统一启用/停用或改分类。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(req.teacher_id)
    if req.enabled is None and req.category is None:
        raise HTTPException(status_code=400, detail="未提供批量操作（enabled 或 category）")
    try:
        return _doc_registry().batch_set_meta(
            req.teacher_id, req.doc_names, req.enabled, req.category)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/documents/search")
def admin_documents_search(
    req: dict = Body(default_factory=dict),
    authorization: str = Header(default=""),
):
    """#24 R5 内容检索：语义检索老师全部文档块，按文档聚合返回命中片段。

    body: {teacher_id, query, top_n?}；已下架文档自动过滤。
    """
    _deps._admin_user(authorization)
    teacher_id = (req.get("teacher_id") or "T001").strip()
    query = (req.get("query") or "").strip()
    top_n = max(1, min(int(req.get("top_n") or 12), 30))
    _deps._check_teacher(teacher_id)
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


@router.get("/admin/recharge-codes")
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
    _deps._admin_user(authorization)
    codes = admin_biz.list_recharge_codes(
        limit=limit, offset=offset, used_only=used_only, status=status,
        batch_id=batch_id, channel=channel, keyword=keyword)
    total = admin_biz.count_recharge_codes(
        status=status, batch_id=batch_id, channel=channel, keyword=keyword)
    return {"codes": codes, "total": total, "limit": limit, "offset": offset}


@router.post("/admin/recharge-codes/generate")
def admin_generate_recharge_codes(
    req: RechargeGenReq = Body(default_factory=RechargeGenReq),
    authorization: str = Header(default=""),
):
    """生成充值码（管理端，#24 R4 支持批次/渠道/备注）。"""
    _deps._admin_user(authorization)
    try:
        return admin_biz.generate_recharge_codes_admin(
            req.plan, req.count, req.batch_id, req.channel, req.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/recharge-codes/void")
def admin_void_recharge_codes(
    req: RechargeVoidReq = Body(default_factory=RechargeVoidReq),
    authorization: str = Header(default=""),
):
    """#24 R4 作废：按码列表或整批次（仅未使用码可作废，已使用不可作废）。"""
    _deps._admin_user(authorization)
    if not req.codes and not req.batch_id.strip():
        raise HTTPException(status_code=400, detail="codes 与 batch_id 至少提供一项")
    try:
        return get_payment_store().void_codes(
            codes=[c.strip() for c in req.codes if c.strip()],
            batch_id=req.batch_id.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/recharge-codes/import")
def admin_import_recharge_codes(
    req: RechargeImportReq = Body(default_factory=RechargeImportReq),
    authorization: str = Header(default=""),
):
    """#24 R4 批量导入：items JSON 或 csv 文本（每行 code,plan,amount）。
    重复码跳过并计数，格式错误逐条报明。"""
    _deps._admin_user(authorization)
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


@router.get("/admin/recharge-codes/stats")
def admin_recharge_stats(authorization: str = Header(default="")):
    """#24 R4 统计：总量/使用率 + 按批次 + 按渠道聚合。"""
    _deps._admin_user(authorization)
    return get_payment_store().recharge_stats()


@router.get("/admin/recharge-codes/export")
def admin_export_recharge_codes(
    status: str = "all",
    batch_id: str = "",
    channel: str = "",
    authorization: str = Header(default=""),
):
    """#24 R4 导出 CSV（按当前筛选条件全量导出，非分页）。"""
    _deps._admin_user(authorization)
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


# ---------- 答案反馈（A1 满意度统计 · #26 R3 工单闭环） ----------
@router.get("/admin/stats/feedback")
def admin_feedback_stats(
    teacher_id: str = "",
    status: str = "",
    authorization: str = Header(default=""),
):
    """答案反馈汇总（A1 满意度统计）。#26 R3 支持 status 筛选。"""
    _deps._admin_user(authorization)
    if status and status not in ("new", "resolved"):
        raise HTTPException(status_code=400, detail="status 必须是 new 或 resolved")
    return {
        "stats": _deps.study_store.feedback_stats(teacher_id or None),
        "recent": _deps.study_store.list_feedback(teacher_id or None,
                                                  status=(status or None), limit=20),
    }


class AdminFeedbackStatusReq(BaseModel):
    """#26 R3 反馈处理状态请求体。"""
    status: str = "resolved"
    note: str = ""


@router.post("/admin/feedback/{fid}/status")
def admin_set_feedback_status(
    fid: int,
    req: AdminFeedbackStatusReq = Body(default_factory=AdminFeedbackStatusReq),
    authorization: str = Header(default=""),
):
    """#26 R3 更新反馈处理状态（工单式闭环）。"""
    _deps._admin_user(authorization)
    try:
        return _deps.study_store.set_feedback_status(fid, req.status, req.note)
    except ValueError as e:
        raise HTTPException(status_code=404 if str(e).startswith("反馈不存在") else 400,
                            detail=str(e))


# ---------- 知识库老师状态（前台联动） ----------
@router.post("/admin/teachers/{teacher_id}/enable")
def admin_enable_teacher(
    teacher_id: str,
    authorization: str = Header(default=""),
):
    """恢复上线（enabled=true）。"""
    _deps._admin_user(authorization)
    try:
        return admin_biz.enable_teacher(teacher_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- C4 数据备份 / 恢复 ----------
@router.get("/admin/backups")
def admin_list_backups(authorization: str = Header(default="")):
    """备份归档列表 + 磁盘占用（运维容灾）。"""
    _deps._admin_user(authorization)
    return {
        "backups": admin_biz.list_backups_admin(),
        "usage": admin_biz.backup_disk_usage(),
    }


@router.post("/admin/backups")
def admin_create_backup(authorization: str = Header(default="")):
    """创建数据备份：raw 源文件 + 全部 *.db → backup_*.tar.gz。"""
    _deps._admin_user(authorization)
    return admin_biz.create_backup_admin()


@router.post("/admin/backups/restore")
def admin_restore_backup(payload: dict, authorization: str = Header(default="")):
    """恢复备份中的 raw 源文件（安全边界：不动 db/向量库）。"""
    _deps._admin_user(authorization)
    try:
        return admin_biz.restore_backup_admin((payload or {}).get("name", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/admin/backups/{name}/download")
def admin_download_backup(name: str, authorization: str = Header(default="")):
    """下载备份归档（用于异地容灾留档）。"""
    _deps._admin_user(authorization)
    if ".." in name or not name.startswith("backup_"):
        raise HTTPException(status_code=400, detail="备份名不合法")
    path = Config.data_dir / "backups" / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="备份不存在")
    return FileResponse(str(path), filename=name, media_type="application/gzip")
