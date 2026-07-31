"""Shared app bootstrap for process-isolated entrypoints."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import get_settings
from app.core.db.database import get_db_manager, get_async_db_manager

load_dotenv()

def _configure_app_logging() -> None:
    """统一 entrypoint 日志配置，确保业务 INFO 日志可见。"""
    settings = get_settings()
    level_name = str(getattr(settings.log, "level", "INFO")).strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )
    root_logger.setLevel(level)

    # 关键业务日志始终跟随全局等级，避免被 uvicorn 默认配置吞掉。
    for name in ("rag.document_ingestor", "rag.document_parser", "rag.ocr_pool"):
        logging.getLogger(name).setLevel(level)


_configure_app_logging()
logger = logging.getLogger(__name__)

for lib in [
    "uvicorn.access",
    "httpx",
    "httpcore",
    "neo4j",
    "chromadb",
    "multipart",
    "watchfiles",
]:
    logging.getLogger(lib).setLevel(logging.WARNING)


@asynccontextmanager
async def shared_lifespan(app: FastAPI):
    """Shared startup/shutdown logic for backend entrypoints."""
    from app.core.db.init_db import check_database_connection, init_database
    from app.core.rag.pageindex.pageindex_worker import get_pageindex_worker  # [DEPRECATED] PageIndex 功能已禁用
    from app.core.rag.ocr_pool import initialize_ocr_pool, shutdown_ocr_pool
    from app.core.rag.summary_service import start_summary_service, stop_summary_service
    from app.core.utils.sandbox_cleanup import (
        start_sandbox_cleanup_service,
        stop_sandbox_cleanup_service,
    )

    _configure_app_logging()
    settings = get_settings()
    runtime_mode = getattr(app.state, "runtime_mode", "admin")
    enable_admin_background_jobs = runtime_mode != "experience"
    logger.info("DeluData startup, env=%s", settings.app.env)

    db_manager = get_db_manager()
    if not db_manager.test_connection():
        logger.warning("Sync DB ping failed; partial features may be unavailable")

    # [DEPRECATED] PageIndex 功能已禁用
    pageindex_worker = None
    try:
        db_status = await check_database_connection()
        if db_status.get("connected"):
            await init_database()
        else:
            logger.warning("Async DB ping failed: %s", db_status.get("error"))
    except Exception as exc:
        logger.error("Async DB initialization failed: %s", exc)

    if enable_admin_background_jobs:
        try:
            from app.services.semantic_auto_governance_service import (
                get_semantic_auto_governance_service,
            )

            recovered_runs = await get_semantic_auto_governance_service().mark_stale_runs_failed()
            if recovered_runs:
                logger.warning(
                    "Recovered %s interrupted semantic governance runs as failed",
                    recovered_runs,
                )
        except Exception as exc:
            logger.warning("Failed to recover interrupted semantic governance runs: %s", exc)

        try:
            await start_summary_service()
        except Exception as exc:
            logger.warning("Summary service startup failed: %s", exc)

        try:
            start_sandbox_cleanup_service()
        except Exception as exc:
            logger.warning("Sandbox cleanup startup failed: %s", exc)

        # [DEPRECATED] PageIndex 功能已禁用
        if settings.pageindex.enabled:
            try:
                pageindex_worker = get_pageindex_worker()
                await pageindex_worker.start()
                await pageindex_worker.recover_stale_tasks()
            except Exception as exc:
                logger.warning("PageIndex worker startup failed: %s", exc)

    else:
        logger.info(
            "Skip summary/sandbox/pageindex workers for runtime_mode=%s",
            runtime_mode,
        )
    if settings.rag.ingest_use_db_queue:
        logger.info(
            "DB queue enabled. Run dedicated worker process: python -m app.entrypoints.ingestion_worker"
        )

    # [稳态优化] 启动 OCR 进程池（支持 windowed_v2 模式）
    try:
        initialize_ocr_pool()
    except Exception as exc:
        logger.warning("OCR pool startup failed: %s", exc)

    try:
        yield
    finally:
        if enable_admin_background_jobs:
            try:
                await stop_summary_service()
            except Exception as exc:
                logger.warning("Summary service shutdown failed: %s", exc)

            try:
                stop_sandbox_cleanup_service()
            except Exception as exc:
                logger.warning("Sandbox cleanup shutdown failed: %s", exc)

        # [DEPRECATED] PageIndex 功能已禁用
        if pageindex_worker is not None:
            try:
                await pageindex_worker.stop()
            except Exception as exc:
                logger.warning("PageIndex worker shutdown failed: %s", exc)

        # [稳态优化] 关闭 OCR 进程池，避免子进程泄漏
        try:
            shutdown_ocr_pool()
        except Exception as exc:
            logger.warning("OCR pool shutdown failed: %s", exc)

        db_manager.dispose()
        try:
            await get_async_db_manager().dispose()
        except Exception:
            pass


def create_application(
    *,
    title: str,
    description: str,
    api_router: APIRouter,
    health_router: APIRouter,
    cors_origins: Optional[list[str]] = None,
    runtime_mode: str = "admin",
) -> FastAPI:
    """Create a FastAPI app with shared middleware and error handlers."""
    settings = get_settings()

    app = FastAPI(
        title=title,
        description=description,
        version="1.0.0",
        lifespan=shared_lifespan,
    )
    app.state.runtime_mode = runtime_mode

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"]
        if settings.app.debug
        else (
            cors_origins
            or [
                "http://localhost:5173",
                "http://localhost:3001",
                "http://localhost:3000",
            ]
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Session-Id", "X-Visitor-UUID"],
    )
    app.add_middleware(
        ProxyHeadersMiddleware,
        trusted_hosts=os.getenv("FORWARDED_ALLOW_IPS", "*"),
    )

    static_dir = Path(__file__).resolve().parents[2] / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/api/static", StaticFiles(directory=str(static_dir)), name="api_static")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled exception: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "internal_server_error",
                "detail": str(exc) if settings.app.debug else None,
            },
        )

    app.include_router(health_router, tags=["Health"])
    app.include_router(api_router, prefix="/api")

    @app.get("/")
    async def _root():
        return {"name": title, "version": "1.0.0", "status": "running"}

    return app
