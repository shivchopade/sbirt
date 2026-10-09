"""FastAPI app factory. SBERT + spaCy are loaded once in the lifespan and shared by every request/task."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.health import router as health_router
from app.api.v1 import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import request_id_var, setup_logging

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, load_models: bool | None = None) -> FastAPI:
    settings = settings or get_settings()
    should_load = settings.load_models_on_startup if load_models is None else load_models

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(settings.log_level, settings.log_json)
        app.state.resources = None
        if should_load:
            from app.services.resources import load_resources

            app.state.resources = load_resources(settings)
            from app.services.task_queue import recover_interrupted

            recover_interrupted(app)
        log.info("startup complete", extra={"models_loaded": app.state.resources is not None})
        yield

    app = FastAPI(
        title="Resume Screening Engine API",
        version=__version__,
        description=(
            "Explainable, gaming-resistant resume screening: style-aware extraction, a visibility engine, "
            "SBERT scoring on visible text only, and evidence-backed integrity flags. No LLM calls."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router)
    return app


app = create_app()
