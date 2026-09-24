# -*- coding: utf-8 -*-
"""A5 内容审核预检 — 敏感词/违规预检（轻量版）。

设计（C5 P2 安全加固的延伸，贴合 2C/3.6G 资源约束）：
  - 输入预检：提问/注册用户名等入口做敏感词扫描，命中即拒绝并返回提示。
  - 输出抽检：对 LLM 回答做敏感词扫描（低成本字符串匹配，不额外调模型）。
  - 词库可配置：SENSITIVE_WORDS 环境变量（逗号分隔），空 = 关闭预检（默认）。
    内置极少兜底词（仅演示默认不生效），避免误伤教学场景。
  - 纯字符串匹配，零依赖、零外部调用，不拖慢问答主链。

用法：
  from .sensitive import check_sensitive, SENSITIVE_ENABLED
  if SENSITIVE_ENABLED:
      hit = check_sensitive(text)
      if hit: raise HTTPException(400, f"内容包含敏感词: {hit}")
"""
import os

# 环境变量开关：SENSITIVE_WORDS 逗号分隔；空 = 关闭（默认，不误伤）
_SENSITIVE_WORDS = [
    w.strip()
    for w in os.environ.get("SENSITIVE_WORDS", "").split(",")
    if w.strip()
]
SENSITIVE_ENABLED = bool(_SENSITIVE_WORDS)


def check_sensitive(text: str) -> str | None:
    """命中返回第一个敏感词，未命中/未启用返回 None。"""
    if not SENSITIVE_ENABLED or not text:
        return None
    for w in _SENSITIVE_WORDS:
        if w and w in text:
            return w
    return None
