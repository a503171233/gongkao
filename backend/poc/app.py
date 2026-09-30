# -*- coding: utf-8 -*-
"""FastAPI 应用工厂。"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .deps import (
    csrf_origin_check,
    http_metrics,
    unhandled_exception,
)
from .routers.core import router as core_router
from .routers.auth import router as auth_router
from .routers.membership import router as membership_router
from .routers.study import router as study_router
from .routers.forum import router as forum_router
from .routers.tracking import router as tracking_router
from .routers.banner import router as banner_router
from .routers.messages import router as messages_router


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
    return app