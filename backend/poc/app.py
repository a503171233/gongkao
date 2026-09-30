# -*- coding: utf-8 -*-
"""FastAPI 应用工厂。"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .deps import (
    csrf_origin_check,
    http_metrics,
    unhandled_exception,
)


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
    return app