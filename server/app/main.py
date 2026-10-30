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


def _ensure_runtime(app: FastAPI) -> None:
    """确保 worker / scheduler 已启动（幂等）。

    正常启动由 lifespan 完成；本函数作为自愈路径，在第一个 HTTP 请求到达时
    补齐未启动的后台组件，避免出现"录音上传成功但永远没有卡片"的静默故障。
    """
    state = app.state
    provider_check = get_providers()
    if settings.scheduler_enabled and getattr(state, "scheduler", None) is None:
        scheduler = SchedulerService(settings, provider_check)
        set_scheduler(scheduler)
        scheduler.start()
        state.scheduler = scheduler
        logger.warning("scheduler was missing; started lazily")
    if not settings.worker_enabled:
        return
    worker = getattr(state, "worker", None)
    if worker is None:
        from .services.scheduler import get_scheduler as _get_scheduler

        worker = BackgroundWorker(settings, provider_check, _get_scheduler())
        worker.start()
        worker.start_watchdog()
        state.worker = worker
        logger.warning("background worker was missing; started lazily")
    elif not worker.alive:
        logger.error("background worker thread is dead; restarting")
        worker.start()


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
            worker.start_watchdog()
        else:
            logger.warning(
                "OA_WORKER_ENABLED=false: 上传的音频不会自动处理（不会生成卡片）。"
                "个人部署请保持 true。"
            )
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

    @app.middleware("http")
    async def ensure_runtime_middleware(request: Request, call_next):
        # 自愈：某些启动方式（--lifespan off、热重载器差异等）可能没执行 lifespan，
        # 导致 worker/scheduler 没启动；第一个请求到达时补齐，避免"有录音但永远不处理"。
        try:
            _ensure_runtime(request.app)
        except Exception:  # pragma: no cover - 自愈失败不应影响 API
            logger.exception("failed to lazily start background runtime")
        return await call_next(request)

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
