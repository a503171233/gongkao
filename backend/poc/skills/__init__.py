# -*- coding: utf-8 -*-
"""运行时 AI 技能层（《运行时AI技能与MCP能力开发说明书》§四）。

技能 = prompts/*.md 模板（版本化）+ schema 校验 + JSON 修复重试，
调用方经 skills.run(skill_id, inputs, ...) 统一入口使用，不感知 prompt 细节。
"""
from .base import SkillResult, run, registry, get_skill  # noqa: F401
