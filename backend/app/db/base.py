"""SQLAlchemy declarative base + pgvector Vector type re-export.

All ORM models inherit from `Base`. We extend `AsyncAttrs` so lazy-loaded
attributes can be awaited on async sessions without tripping MissingGreenlet.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase

# Re-export so models import the dimension-aware Vector type from one place.
from pgvector.sqlalchemy import Vector  # noqa: F401


class Base(AsyncAttrs, DeclarativeBase):
    """Project-wide declarative base."""
