# -*- coding: utf-8 -*-
"""C5 · P3 视频转写预留接口（阻塞中）。

现状（docs/项目状态总览.md §六）：中转站无 whisper/audio 通道、服务器无
ffmpeg、2C/3.6G 跑不动本地 ASR → 死胡同。此处仅预留 OpenAI 兼容 audio 接口
契约，待上游（中转站）暴露 whisper 后只需填 base_url/模型名即可启用。
启用条件：环境变量 WHISPER_ENABLED=1 + WHISPER_BASE_URL/WHISPER_API_KEY/WHISPER_MODEL。
"""

import os as _os_whisper

from fastapi import APIRouter, HTTPException, Request

from .. import store

router = APIRouter()


@router.get("/audio/transcriptions/status")
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


@router.post("/audio/transcriptions")
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