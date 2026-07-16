"""FastAPI application entrypoint.

Lifespan: future home of graph-compile-at-startup (Week 2). For REQ-001 we
only need the app to be importable for tests and runnable via uvicorn.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

# Psycopg's async pool requires SelectorEventLoop on Windows. Uvicorn defaults
# to ProactorEventLoop on Windows, so set the policy before uvicorn creates it.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agents.graph import graph
from app.config import get_settings
from app.errors import ApiError, RateLimited
from app.routers import analytics, company, eval, stream, tenders
import app.services.event_bus as event_bus_module

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # REQ-003: ensure LangGraph Postgres checkpoint tables exist on startup.
    await graph.checkpointer.setup()

    # REQ-009: initialise Redis pub/sub event bus.
    settings = get_settings()
    event_bus_module.event_bus = event_bus_module.EventBus(
        redis_url=settings.redis_url
    )
    await event_bus_module.event_bus.connect()

    yield

    if event_bus_module.event_bus:
        await event_bus_module.event_bus.disconnect()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="TenderIQ API",
        version="0.1.0",
        description="Tender-document analysis pipeline.",
        lifespan=lifespan,
    )

    # CORS: explicit origin allowlist (never "*" with credentials).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Map typed ApiError subclasses to their status code + detail.
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        headers = {}
        if isinstance(exc, RateLimited):
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=headers,
        )

    app.include_router(tenders.router)
    app.include_router(company.router)
    app.include_router(analytics.router)
    app.include_router(stream.router)
    app.include_router(eval.router, prefix="/eval", tags=["eval"])

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
