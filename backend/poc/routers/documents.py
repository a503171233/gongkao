# -*- coding: utf-8 -*-
"""文档管理与开放 API 路由：上传、文档管理、服务器路径导入、Open API v1 文档/知识上传。

本模块包含:
- A1 答案反馈 (/feedback)
- 文档管理 (/documents, /documents/{doc_name})
- 上传公共加固 (/upload)
- 文档按服务器路径导入 (/admin/documents/browse, /admin/documents/import-path)
- Open API v1 文档 (/v1/documents/health, /v1/documents/upload)
- Open API v1 知识 (/v1/knowledge/health, /v1/knowledge/teachers, /v1/knowledge/upload)
"""

import hmac
import os as _os
import re as _re
import threading
import time as _time
from collections import deque
from pathlib import Path as _Path

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import deps as _deps
from .. import store
from ..config import Config
from ..ingest import ingest_file

router = APIRouter()


# _admin_user 已在 deps.py（管理端鉴权点，测试可 patch api._admin_user）

# ---------- A1 答案反馈 ----------
class FeedbackReq(BaseModel):
    """答案反馈请求体（A1 契约：{rating: up|down, question, answer, reason?, session_id?, teacher_id?}）。"""
    rating: str = "up"
    question: str = ""
    answer: str = ""
    reason: str = ""
    session_id: str = ""
    teacher_id: str = "T001"


@router.post("/feedback")
def submit_feedback(
    req: FeedbackReq = Body(default_factory=FeedbackReq),
    authorization: str = Header(default=""),
):
    """提交答案反馈（赞/踩）。匿名用户也可反馈（user_id=anonymous）。"""
    user = _deps._resolve_user(authorization)
    uid = user["user_id"] if user else "anonymous"
    try:
        return _deps.study_store.add_feedback(
            uid, req.teacher_id, req.session_id,
            req.question[:500], req.answer[:2000],
            req.rating, req.reason[:200],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/documents")
def documents(teacher_id: str = "T001"):
    """知识库文档列表（按来源聚合块数）。"""
    _deps._check_teacher(teacher_id)
    return {"teacher_id": teacher_id, "documents": store.list_documents(teacher_id)}


@router.delete("/documents/{doc_name}")
def delete_document(doc_name: str, teacher_id: str = "T001",
                    authorization: str = Header(default="")):
    """删除指定文档全部块（安全加固：删除属管理操作，仅管理员可调用）。"""
    _deps._admin_user(authorization)
    _deps._check_teacher(teacher_id)
    n = store.delete_document(teacher_id, doc_name)
    return {"deleted": n, "remaining": _deps._safe_count(teacher_id)}


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
    upload_dir = _deps.cfg.upload_dir(teacher_id)
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
        from ..docreg import get_doc_registry
        get_doc_registry().register(teacher_id, clean_name, category=category,
                                    size_bytes=len(data),
                                    chars=sum(len(c.content) for c in chunks))
    except Exception:  # noqa: BLE001 — 注册表故障不阻断入库主链路
        pass
    return {"chunks": len(chunks), "stored": n, "total": _deps._safe_count(teacher_id)}


@router.post("/upload")
async def upload(teacher_id: str = "T001", x_upload_secret: str = Header(default=""),
                 file: UploadFile = File(...)):
    """上传课件入库（md/txt/docx/pptx/pdf/xlsx）。
    若配置了 UPLOAD_SECRET，则必须携带 X-Upload-Secret 匹配，否则 403（防公网滥用）。
    C5 安全加固：扩展名白名单二次校验 + 应用层大小上限 + 文件名清洗（防路径穿越）。
    """
    if _deps.cfg.upload_secret and x_upload_secret != _deps.cfg.upload_secret:
        raise HTTPException(status_code=403, detail="无上传权限")
    tcfg = _deps._check_teacher(teacher_id)
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


def _under_roots(rp: _Path, roots: list[_Path]) -> bool:
    return any(rp == r or str(rp).startswith(str(r) + _os.sep) for r in roots)


@router.get("/admin/documents/browse")
def admin_browse_documents(path: str = "", authorization: str = Header(default="")):
    """浏览路径导入白名单根下的目录（供「选择上传内容的路径」弹窗用）。
    仅管理员；只列目录与受支持扩展名的文件；隐藏文件不展示。"""
    _deps._admin_user(authorization)
    roots = _deps._import_roots()
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


@router.post("/admin/documents/import-path")
def admin_import_document_by_path(
    req: DocImportPathReq = Body(default_factory=DocImportPathReq),
    authorization: str = Header(default=""),
):
    """从服务器路径导入文档入库（复用 /upload 同一套校验与入库链路）。
    仅管理员；路径必须在 DOC_IMPORT_DIRS 白名单根下；扩展名/大小/空文件同 /upload 约束。"""
    _deps._admin_user(authorization)
    tcfg = _deps._check_teacher(req.teacher_id)
    raw = (req.path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="路径不能为空")
    roots = _deps._import_roots()
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


@router.get("/v1/documents/health")
def openapi_documents_health():
    """开放 API 能力探测（公开）：第三方对接前先探此端点确认能力与限制。"""
    return {"enabled": bool(_deps.cfg.openapi_key), "formats": sorted(_UPLOAD_EXTS),
            "max_mb": 50, "rate_limit": "10/min per IP", "auth": "X-API-Key header"}


@router.post("/v1/documents/upload")
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
    if not _deps.cfg.openapi_key:
        return _v1_err(503, "开放 API 未启用：服务端未配置 OPENAPI_KEY")
    if not x_api_key or not hmac.compare_digest(x_api_key, _deps.cfg.openapi_key):
        return _v1_err(401, "无效 X-API-Key（请在请求头 X-API-Key 携带开放密钥）")
    allowed, retry = _openapi_rl.check(_client_ip(request))
    if not allowed:
        return _v1_err(429, f"请求过于频繁（限 10 次/分钟/IP），请 {retry}s 后重试",
                       {"Retry-After": str(retry)})
    tid = (teacher_id or "").strip()
    if not tid:
        return _v1_err(400, "缺少 teacher_id（multipart 表单字段，必填）")
    try:
        tcfg = _deps._check_teacher(tid)
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
            from ..docreg import get_doc_registry
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


@router.get("/v1/knowledge/health")
def openapi_knowledge_health():
    """知识上传 API 能力探测（公开）。"""
    return {"enabled": bool(_deps.cfg.openapi_key), "formats": [".md", ".txt"],
            "max_mb": 5, "rate_limit": "6/min per IP",
            "limits": "300 节点/次，层级≤6", "auth": "X-API-Key header"}


@router.get("/v1/knowledge/teachers")
def openapi_knowledge_teachers(request: Request,
                               x_api_key: str = Header(default="")):
    """(#35 R4) 可用老师列表（供外部上传方自查 teacher_id；鉴权与限流同 upload）。"""
    if not _deps.cfg.openapi_key:
        return _v1_err(503, "开放 API 未启用：服务端未配置 OPENAPI_KEY")
    if not x_api_key or not hmac.compare_digest(x_api_key, _deps.cfg.openapi_key):
        return _v1_err(401, "无效 X-API-Key（请在请求头 X-API-Key 携带开放密钥）")
    allowed, retry = _kn_rl.check(_client_ip(request))
    if not allowed:
        return _v1_err(429, f"请求过于频繁（限 6 次/分钟/IP），请 {retry}s 后重试",
                       {"Retry-After": str(retry)})
    items = _kn_enabled_teachers()
    return {"code": 0, "message": "ok",
            "data": {"teachers": items, "total": len(items)}}


@router.post("/v1/knowledge/upload")
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
    if not _deps.cfg.openapi_key:
        return _v1_err(503, "开放 API 未启用：服务端未配置 OPENAPI_KEY")
    if not x_api_key or not hmac.compare_digest(x_api_key, _deps.cfg.openapi_key):
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
        _deps._check_teacher(tid)
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

    from ..knowledge import get_knowledge_store
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