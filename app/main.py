"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.app_state import AppState
from app.auth import is_authorized
from app.config import get_config
from app.converter import is_v4l2_h264_available
from app.database import close_db, get_session_factory, init_db
from app.routes import files, jobs, stats, settings as settings_router
from app.routes import events as events_router
from app.scheduler import (
    start_scheduler,
    stop_scheduler,
    update_convert_schedule,
    update_scan_schedule,
)
from app.services import enqueue_auto_convert, run_scan
from app.worker import ConversionWorker

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = get_config()
    app.state.config = config

    logger.info("V4L2 H.264 available: %s", is_v4l2_h264_available())

    # Ensure the data directory exists.
    config.db_path.parent.mkdir(parents=True, exist_ok=True)

    await init_db(str(config.db_path))
    sf = get_session_factory()

    app_state = AppState()
    app.state.app_state = app_state

    worker = ConversionWorker(sf, config.media_root, config, app_state)
    app.state.worker = worker
    worker.start()

    # Register scheduler jobs from compose-driven config.
    start_scheduler()

    async def _scan_job() -> None:
        async with sf() as s:
            await run_scan(s, config.media_root, app_state)

    async def _convert_job() -> None:
        async with sf() as s:
            await enqueue_auto_convert(s, worker, config)

    if config.auto_scan_enabled:
        update_scan_schedule(config.scan_schedule, _scan_job)
    if config.auto_convert_enabled:
        update_convert_schedule(config.convert_schedule, _convert_job)

    logger.info("Media Encoding Tracker ready — media root: %s", config.media_root)
    yield

    await worker.stop()
    stop_scheduler()
    await close_db()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Media Encoding Tracker",
        description="Track and batch-convert media files on an SMB share.",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    @app.middleware("http")
    async def enforce_api_auth(request: Request, call_next):
        if request.url.path.startswith("/api"):
            config = request.app.state.config
            if not is_authorized(request.headers.get("Authorization"), config):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid credentials"},
                    headers={"WWW-Authenticate": "Basic"},
                )
        return await call_next(request)

    app.include_router(files.router)
    app.include_router(jobs.router)
    app.include_router(stats.router)
    app.include_router(settings_router.router)
    app.include_router(events_router.router)

    # Serve the SPA at root; static assets under /static.
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_ui() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    return app


app = create_app()
