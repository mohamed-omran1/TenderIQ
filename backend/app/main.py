"""FastAPI application entrypoint.

Lifespan: future home of graph-compile-at-startup (Week 2). For REQ-001 we
only need the app to be importable for tests and runnable via uvicorn.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.errors import ApiError, RateLimited
from app.routers import tenders

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Week 2 will compile the LangGraph graph here and stash it on app.state.
    yield


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

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
