"""OpenAssistant Server - FastAPI application factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api import api_router
from .config import get_settings
from .db import SessionLocal, init_db
from .providers import build_providers, get_providers, set_providers
from .security import ensure_bootstrap_user
from .services.jobs import BackgroundWorker
from .services.scheduler import SchedulerService, set_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("openassistant")

settings = get_settings()


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        with SessionLocal() as db:
            user = ensure_bootstrap_user(db)
            if user is not None:
                logger.info("bootstrap user ready: %s", user.display_name)
        providers = build_providers()
        set_providers(providers)
        logger.info("providers: %s", providers.describe())

        scheduler: SchedulerService | None = None
        worker: BackgroundWorker | None = None
        if settings.scheduler_enabled:
            scheduler = SchedulerService(settings, providers)
            set_scheduler(scheduler)
            scheduler.start()
        if settings.worker_enabled:
            from .services.scheduler import get_scheduler as _get_scheduler

            worker = BackgroundWorker(settings, providers, _get_scheduler())
            worker.start()
        app.state.worker = worker
        app.state.scheduler = scheduler
        try:
            yield
        finally:
            if worker is not None:
                worker.stop()
            if scheduler is not None:
                scheduler.shutdown()

    app = FastAPI(
        title="OpenAssistant Server",
        version=__version__,
        description=(
            "个人语音记录与智能助手系统 Server 端：ASR、说话人分离与声纹识别、"
            "AI 总结卡片、日程/待办、会议纪要、指令执行、定时任务、钉钉推送。"
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {
            "name": settings.app_name,
            "version": __version__,
            "api": settings.api_prefix,
            "docs": "/docs",
        }

    @app.get("/health", include_in_schema=False)
    def root_health() -> dict:
        from .utils import now_utc

        providers = get_providers()
        return {
            "status": "ok",
            "version": __version__,
            "mode": providers.modes,
            "time": now_utc().isoformat() + "Z",
        }

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc), "message": str(exc)})

    return app


app = create_app()
