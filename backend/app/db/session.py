"""Async DB engine, session factory, and FastAPI dependency.

`get_session` is the request-scoped session dependency used in routers:
`session: Annotated[AsyncSession, Depends(get_session)]`.

For background ingestion (which runs outside a request), call
`with_session()` directly — see `app/agents/ingestion.py`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_settings = get_settings()

# `pool_pre_ping` guards against stale connections after a DB restart.
engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # we read objects back after commit (e.g. tender.id)
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a session and rolls back on any exception."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def with_session() -> AsyncSession:
    """Factory for use outside a request (BackgroundTasks). Caller closes it."""
    return SessionLocal()
