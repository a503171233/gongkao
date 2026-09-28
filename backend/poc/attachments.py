# -*- coding: utf-8 -*-
"""批次28 · 附件存储与解析（学习页上传文件/图片提问）。

职责：
- 保存学员上传的图片（png/jpg/jpeg/webp/gif）与文档（txt/md/pdf/docx）
- 文档即传即解析为纯文本（图片存原图，提问时走视觉模型）
- index.json 轻量索引（重启不丢）；附件归属校验；总量上限防滥用

设计约束：
- 文件名一律 uuid 化落盘（防路径注入），原始文件名只存索引
- 单文件 ≤8MB；每请求 ≤3 附件（api 层校验）；全局条目 ≤400（超出拒绝）
- 游客可用（user_id='anonymous'），归属校验：附件仅归属上传时身份可用
"""
import base64
import json
import mimetypes
import uuid
from pathlib import Path

_CFG_DIR = Path(__file__).resolve().parents[2] / "data"
ATTACH_DIR = _CFG_DIR / "uploads"
INDEX_PATH = ATTACH_DIR / "index.json"

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_DOC_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 20000      # 单附件解析文本截断
MAX_TOTAL_ITEMS = 400       # 全局附件条目上限

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_DOC_EXTS = {".txt", ".md", ".pdf", ".docx"}


def _load_index() -> dict:
    if INDEX_PATH.exists():
        try:
            return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def _save_index(idx: dict) -> None:
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    tmp.replace(INDEX_PATH)


def _detect_kind(filename: str) -> tuple[str, str]:
    """(kind, ext)。kind: image / file；不支持抛 ValueError。"""
    ext = Path(filename or "").suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image", ext
    if ext in _DOC_EXTS:
        return "file", ext
    raise ValueError(
        "暂不支持该类型（图片：png/jpg/webp/gif；文档：txt/md/pdf/docx）")


def _parse_doc_text(path: Path, ext: str) -> str:
    """文档 → 纯文本（轻量复刻 ingest.py 解析能力，不抽图片不入库）。"""
    if ext in (".txt", ".md"):
        for enc in ("utf-8", "gb18030"):
            try:
                return path.read_text(encoding=enc)
            except (UnicodeDecodeError, ValueError):
                continue
        return path.read_text(encoding="utf-8", errors="ignore")
    if ext == ".pdf":
        import fitz  # pymupdf（ingest 同款依赖）
        doc = fitz.open(str(path))
        try:
            return "\n".join(page.get_text() for page in doc)
        finally:
            doc.close()
    if ext == ".docx":
        import docx  # python-docx（ingest 同款依赖）
        d = docx.Document(str(path))
        return "\n".join(p.text for p in d.paragraphs if p.text.strip())
    raise ValueError(f"暂不支持该格式: {ext}")


def save_upload(user_id: str, filename: str, data: bytes) -> dict:
    """保存附件并返回元信息。图片存原图；文档解析文本存旁路 .txt。"""
    if not data:
        raise ValueError("文件内容为空")
    kind, ext = _detect_kind(filename)
    limit = MAX_IMAGE_BYTES if kind == "image" else MAX_DOC_BYTES
    if len(data) > limit:
        raise ValueError(f"文件太大（上限 {limit // (1024 * 1024)}MB）")

    idx = _load_index()
    if len(idx) >= MAX_TOTAL_ITEMS:
        raise ValueError("附件库已满，请联系管理员清理后再试")

    att_id = uuid.uuid4().hex[:20]
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = ATTACH_DIR / f"{att_id}{ext}"
    raw_path.write_bytes(data)

    text_chars = 0
    if kind == "file":
        try:
            text = _parse_doc_text(raw_path, ext)
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001  解析失败=文件损坏/加密等
            raw_path.unlink(missing_ok=True)
            raise ValueError(f"文档解析失败: {e}") from e
        text = text.strip()[:MAX_TEXT_CHARS]
        if not text:
            raw_path.unlink(missing_ok=True)
            raise ValueError("未能从文档中解析出文字（扫描版 PDF 请改用截图上传）")
        (ATTACH_DIR / f"{att_id}.txt").write_text(text, encoding="utf-8")
        text_chars = len(text)

    safe_name = Path(filename or "attachment").name[:80]
    idx[att_id] = {
        "user_id": user_id or "anonymous",
        "name": safe_name,
        "kind": kind,
        "ext": ext,
        "size": len(data),
        "text_chars": text_chars,
        "created": raw_path.stat().st_mtime,
    }
    _save_index(idx)
    return {"attachment_id": att_id, "kind": kind, "name": safe_name,
            "text_chars": text_chars, "size": len(data)}


def resolve_attachments(att_ids: list[str] | None, user_id: str) -> list[dict]:
    """按 id 列表解析附件（校验归属）；返回 [{id,kind,name,text,image_data_url}]。

    归属规则：附件仅归属上传时身份（登录用户 / anonymous）可用——
    防止 UUID 碰撞之外的身份串用。unknown id 静默跳过（不阻断提问）。
    """
    if not att_ids:
        return []
    idx = _load_index()
    out = []
    for aid in att_ids[:3]:
        meta = idx.get(str(aid))
        if not meta:
            continue
        if (meta.get("user_id") or "anonymous") != (user_id or "anonymous"):
            continue
        item = {"id": str(aid), "kind": meta["kind"], "name": meta["name"], "text": ""}
        raw_path = ATTACH_DIR / f"{aid}{meta.get('ext', '')}"
        if meta["kind"] == "file":
            txt_path = ATTACH_DIR / f"{aid}.txt"
            if txt_path.exists():
                item["text"] = txt_path.read_text(encoding="utf-8")
        elif meta["kind"] == "image" and raw_path.exists():
            mime = mimetypes.guess_type(raw_path.name)[0] or "image/png"
            b64 = base64.b64encode(raw_path.read_bytes()).decode("ascii")
            item["image_data_url"] = f"data:{mime};base64,{b64}"
        out.append(item)
    return out
