"""
PACR Pipeline — FastAPI Application Entry Point
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.common.logging import configure_logging, get_logger
from app.common.models import ApiResponse, ServiceStatusEnum
from app.pipeline.scheduler import get_scheduler_status, start_scheduler, stop_scheduler
from app.pipeline.router import router as pipeline_router

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("PACR Pipeline starting up")
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("PACR Pipeline shut down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="PACR Research Ingestion Pipeline",
        description="Automated research paper ingestion, scoring, and publication API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Restrict to your frontend domain in production
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        """Attach a unique X-Request-Id to every request and response."""
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    # ── Routers ────────────────────────────────────────────────────────────────
    app.include_router(pipeline_router)

    return app


app = create_app()
