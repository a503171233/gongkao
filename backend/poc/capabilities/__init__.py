# -*- coding: utf-8 -*-
"""能力层（《运行时AI技能与MCP能力开发说明书》§五）。

每个能力 = 内置 provider（现有自研实现兜底）+ MCP provider（新），经 registry
用环境变量切换。P0 接入：search / fetch / docparse。
vision/asr/rerank 属 P1/P2，未注册即由调用方按现状降级。
"""
from . import registry  # noqa: F401  暴露注册表
# 导入即注册各能力的 builtin/mcp provider
from . import search_cap   # noqa: F401,E402
from . import fetch_cap     # noqa: F401,E402
from . import docparse_cap  # noqa: F401,E402
