# -*- coding: utf-8 -*-
"""FastAPI 应用工厂。"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .deps import (
    csrf_origin_check,
    http_metrics,
    unhandled_exception,
    auth_store,
)
from . import autocollect as autocollect_mod
from .routers.core import router as core_router
from .routers.auth import router as auth_router
from .routers.membership import router as membership_router
from .routers.study import router as study_router
from .routers.forum import router as forum_router
from .routers.tracking import router as tracking_router
from .routers.banner import router as banner_router
from .routers.messages import router as messages_router
from .routers.articles import router as articles_router
from .routers.mockexam import router as mockexam_router
from .routers.smartexam import router as smartexam_router
from .routers.documents import router as documents_router
from .routers.admin import router as admin_router
from .routers.questions import router as questions_router
from .routers.autocollect import router as autocollect_router
from .routers.aimodels import router as aimodels_router
from .routers.knowledge import router as knowledge_router
from .routers.operations import router as operations_router
from .routers.whisper import router as whisper_router


def create_app() -> FastAPI:
    app = FastAPI(title="多老师RAG内核", version="0.3.2")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(Exception, unhandled_exception)
    app.middleware("http")(csrf_origin_check)
    app.middleware("http")(http_metrics)
    app.include_router(core_router)
    app.include_router(auth_router)
    app.include_router(membership_router)
    app.include_router(study_router)
    app.include_router(forum_router)
    app.include_router(tracking_router)
    app.include_router(banner_router)
    app.include_router(messages_router)
    app.include_router(articles_router)
    app.include_router(mockexam_router)
    app.include_router(smartexam_router)
    app.include_router(documents_router)
    app.include_router(admin_router)
    app.include_router(questions_router)
    app.include_router(autocollect_router)
    app.include_router(aimodels_router)
    app.include_router(knowledge_router)
    app.include_router(operations_router)
    app.include_router(whisper_router)

    @app.on_event("startup")
    def on_startup():
        import os
        if os.environ.get("ADMIN_USERNAME"):
            uid = auth_store.ensure_admin()
            if uid:
                print(f"[C2] admin 账号已初始化: {os.environ.get('ADMIN_USERNAME')}")
            else:
                print("[C2] ADMIN_USERNAME 已配置但密码为空，跳过 admin 初始化")
        autocollect_mod.start()

    return app