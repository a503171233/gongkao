# -*- coding: utf-8 -*-
"""网站二（RAG 内核服务）FastAPI 入口。
多老师支持：所有接口带 teacher_id；SSE 流式输出。
对应说明书 V3.0：网站二职责 = 知识库管理/检索/调度/推理/输出校验。

契约：store/answer 均为 teacher_id 签名（见各模块 docstring）。
"""
import sys as _sys

from . import deps as _deps_mod
from .app import create_app

app = create_app()

# re-export tracking state for test compatibility (test accesses api._track_limiter / api._TRACK_MAX)
from .routers.tracking import _track_limiter, _TRACK_MAX  # noqa: F401

# re-export shared dicts and helpers that tests directly access on api
from .deps import _article_cooldown, _forum_write_cooldown, _import_roots  # noqa: F401

# ---- 模块属性同步：当测试等代码对 api.X 赋值时，同步到 deps.X ----
# 路由模块通过 "from ..deps import X" 引用共享状态（import 时绑定）；若仅修改
# api 的本地绑定，路由仍用旧值。此处利用 Python 模块 __class__ 可写特性
# （PEP 3130）为可替换属性添加 property 描述符，使 api.X = Y 等价于 deps.X = Y。

_PATCHABLE = {
    'cfg', 'chat_store', 'auth_store', 'gitee_oauth', 'payment_store',
    'study_store', 'incentive_store', 'mockexam_store', 'smartexam_store',
    'forum_store', 'tracking_store', 'banner_store', 'message_store',
    '_resolve_user',
    '_admin_user',
    '_forum_write_ok',
    '_grade_llm_call',
    '_login_rate_limit', '_login_fail_record', '_client_ip',
    '_security_rate_limit',
}


class _ApiProxy(type(_sys)):
    pass


for _attr in _PATCHABLE:
    def _mk_get(name):
        def fget(self):
            return getattr(_deps_mod, name)
        return fget
    def _mk_set(name):
        def fset(self, value):
            setattr(_deps_mod, name, value)
            # 使用 sys.modules 修改自身存储的 __dict__
            import sys
            sys.modules[__name__].__dict__[name] = value
        return fset
    def _mk_del(name):
        def fdel(self):
            delattr(_deps_mod, name)
            import sys
            del sys.modules[__name__].__dict__[name]
        return fdel
    setattr(_ApiProxy, _attr, property(_mk_get(_attr), _mk_set(_attr), _mk_del(_attr)))

_sys.modules[__name__].__class__ = _ApiProxy


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("poc.api:app", host="0.0.0.0", port=9000, reload=False)
