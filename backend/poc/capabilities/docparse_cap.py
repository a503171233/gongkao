# -*- coding: utf-8 -*-
"""docparse 能力（§五表 / §5.3：parse(file_path) -> (markdown, warning)）。

builtin = ingest.extract_text（现有 pymupdf/docx/pptx 解析，行为原样）；
mcp     = MCP 文档解析工具（MCP_TOOL_DOCPARSE，含 OCR，解决扫描版课件）。
默认 provider=builtin（P2 才在线验证 mcp；§5.1 表）。
"""
from __future__ import annotations

from pathlib import Path

from . import registry
from .. import mcp_client


def _builtin(file_path: str) -> tuple[str, str]:
    from ..ingest import extract_text
    try:
        return extract_text(file_path), ""
    except ValueError as e:          # extract_text 失败语义：入库失败: ...
        return "", f"本地解析失败: {str(e)[:160]}"
    except Exception as e:  # noqa: BLE001
        return "", f"本地解析异常（{type(e).__name__}: {str(e)[:120]}）"


def _mcp(file_path: str) -> tuple[str, str]:
    tool = mcp_client.tool_bound("MCP_TOOL_DOCPARSE")
    if not tool:
        return "", "MCP 文档解析工具未绑定（MCP_TOOL_DOCPARSE 为空）"
    p = Path(file_path)
    if not p.is_file():
        return "", f"待解析文件不存在: {p.name}"
    # 契约：args 传文件名+base64 内容（网关侧落盘解析）；返回 {"markdown": "..."}
    import base64
    try:
        raw = p.read_bytes()
    except OSError as e:
        return "", f"文件读取失败: {e}"
    if len(raw) > 50 * 1024 * 1024:
        return "", f"文件超过 50MB 上限（当前 {len(raw) // 1024 // 1024}MB）"
    res, warn = mcp_client.call_tool(tool, {
        "filename": p.name,
        "content_b64": base64.b64encode(raw).decode(),
    })
    if warn:
        return "", warn
    if isinstance(res, dict):
        md = str(res.get("markdown") or res.get("text") or res.get("content") or "")
    else:
        md = str(res or "")
    if not md.strip():
        return "", "MCP docparse 返回空内容"
    return md, ""


registry.register("docparse", "builtin", lambda file_path: _builtin(file_path))
registry.register("docparse", "mcp", lambda file_path: _mcp(file_path))
