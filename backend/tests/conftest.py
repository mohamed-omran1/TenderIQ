"""Shared pytest fixtures.

Design (per senior-qa skill):
- DB tests run against a real Postgres+pgvector (docker compose). Each test
  opens a connection inside a transaction and rolls back at teardown — no
  cross-test pollution, no ordering dependence.
- The embeddings client is stubbed by default (never hit real Gemini in CI).
- fakeredis stands in for Redis so rate-limit tests are deterministic.
- A two-company fixture pair makes tenant-isolation tests trivial to write.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# `fakeredis` provides an in-process async Redis for deterministic rate-limit tests.
import fakeredis.aioredis

from app.config import get_settings
from app.db import models  # noqa: F401  (register metadata)
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.middleware import rate_limit as rate_limit_module
from app.middleware.auth import _hash_key
from app.db.models import Company

settings = get_settings()

# A separate engine+sessionmaker bound to the test connection so we can wrap
# every test in a single rollback. Tests must not see each other's writes.
_test_engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    # NullPool: never reuse connections across event loops. pytest-asyncio
    # uses a fresh loop per test by default; a shared pool would hand a test
    # an asyncpg connection bound to a prior loop -> "Future attached to a
    # different loop". NullPool opens+closes a connection per checkout, which
    # is fine for tests (the per-test transaction still governs cleanup).
    poolclass=NullPool,
)
_TestSessionLocal = async_sessionmaker(
    bind=_test_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


@pytest.fixture(scope="session")
def event_loop():
    """Single event loop for the whole session — pytest-asyncio default is per-test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_schema() -> AsyncIterator[None]:
    """Create tables once per session. Drops at the end so reruns are clean.

    NOTE: pgvector HNSW index needs the extension; `CREATE EXTENSION vector`
    must have been run by the migration (`alembic upgrade head`) beforehand.
    """
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await _test_engine.dispose()


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """Per-test session whose writes roll back at teardown, even if the code
    under test calls `session.commit()`.

    Pattern: open a real connection-level transaction, then a SAVEPOINT. The
    session works inside the savepoint; when code calls `commit()`, SQLAlchemy
    releases that savepoint — we intercept via `after_transaction_end` and
    immediately open a fresh savepoint so subsequent commits stay nested. At
    teardown we roll back the outer transaction, discarding everything.
    """
    async with _test_engine.connect() as conn:
        outer = await conn.begin()
        # Nest everything in a savepoint so session.commit() doesn't escape.
        await conn.begin_nested()

        session = AsyncSession(bind=conn, expire_on_commit=False, autoflush=False)

        @event.listens_for(session.sync_session, "after_transaction_end")
        def _reopen_savepoint(sess, transaction):  # noqa: ANN001
            if transaction.nested and not transaction._parent.nested:  # type: ignore[attr-defined]
                # The savepoint was committed/rolled back by the app code —
                # reopen a new one so the next commit also stays nested.
                sess.begin_nested()

        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


@pytest_asyncio.fixture
async def app_client(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client wired so the app uses the per-test transactional session.

    Both the router's `get_session` dep and the background ingestion path are
    overridden to use the test session — but ingestion runs as a BackgroundTask
    after the response, so we *don't* await it (tests inspect pre-ingestion
    state for upload-flow assertions and call `run_ingestion` explicitly when
    they want the post-state).
    """
    app = create_app()

    async def _override_session() -> AsyncSession:
        yield db

    app.dependency_overrides[get_session] = _override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def ingestion_session(db: AsyncSession, monkeypatch):
    """Route ingestion's session through the test's transaction.

    `run_ingestion` and the ingestor node open their own session via
    `with_session()` and commit on it. If that's the production SessionLocal(),
    those writes commit *outside* the per-test rollback boundary and persist
    across tests (seen as duplicate-key violations when chunk inserts survive).

    We point `with_session` at the test's own `db` so every write the ingestor
    makes stays inside the per-test savepoint and rolls back at teardown.

    IMPORTANT: the ingestor modules did `from app.db.session import with_session`,
    so each holds its OWN module-level reference. Patching `app.db.session`
    alone does nothing for them — we must patch the name in every module that
    looks it up.
    """
    from app.agents import ingestion as ingestion_module
    from app.agents.nodes import ingestor as ingestor_module

    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, tb):
            # Never commit/rollback here — the per-test `db` fixture owns the
            # transaction. Ingestion commits become no-ops on the outer txn.
            return False

    _factory = lambda: _Ctx()  # noqa: E731
    monkeypatch.setattr(ingestion_module, "with_session", _factory)
    monkeypatch.setattr(ingestor_module, "with_session", _factory)
    return db


@pytest_asyncio.fixture
async def fake_redis() -> AsyncIterator[Any]:
    """fakeredis client + wired into the rate-limit module."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    original = rate_limit_module.get_redis

    async def _fake_get_redis():
        return client

    rate_limit_module.get_redis = _fake_get_redis  # type: ignore[assignment]
    try:
        yield client
    finally:
        rate_limit_module.get_redis = original  # type: ignore[assignment]
        await client.flushall()
        await client.aclose()


# ---- helpers / company fixtures ----------------------------------------------

async def create_company(
    db: AsyncSession,
    *,
    name: str = "Test Co",
    raw_api_key: str | None = None,
    monthly_doc_limit: int = 100,
) -> tuple[Company, str]:
    """Insert a company with a hashed key. Returns (company, raw_key_for_client)."""
    raw_api_key = raw_api_key or f"sk-test-{uuid.uuid4().hex}"
    company = Company(
        name=name,
        api_key_hash=_hash_key(raw_api_key),
        monthly_doc_limit=monthly_doc_limit,
    )
    db.add(company)
    await db.flush()
    return company, raw_api_key


@pytest_asyncio.fixture
async def company_a(db: AsyncSession) -> tuple[Company, str]:
    return await create_company(db, name="Tenant A", raw_api_key="sk-test-tenant-a")


@pytest_asyncio.fixture
async def company_b(db: AsyncSession) -> tuple[Company, str]:
    return await create_company(db, name="Tenant B", raw_api_key="sk-test-tenant-b")


@pytest.fixture
def auth_headers():
    """Helper: build Bearer headers from a raw key."""

    def _make(raw_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {raw_key}"}

    return _make


# ---- PDF fixtures ------------------------------------------------------------

def _make_pdf_bytes(text: str = "Tender content") -> bytes:
    """Generate a tiny valid PDF in-memory (no fixture file on disk needed).

    The PDF has one page containing `text`. Starts with `%PDF-` so it passes
    the magic-byte check.

    For Arabic text we must register a TTF font with Arabic glyph coverage —
    reportlab's default Helvetica has none, so non-ASCII text renders as
    garbage and PyMuPDF extracts garbage back. We try common system fonts
    (Tahoma/Arial on Windows, DejaVu on Linux). If none is found, the Arabic
    glyphs won't be extractable; callers should use `_arabic_extraction_works()`
    to decide whether to run Arabic-content assertions.
    """
    import os

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    from io import BytesIO

    font_name = "Helvetica"  # reportlab built-in; Latin only
    if any(ord(ch) > 0x2000 for ch in text):
        for candidate in (
            r"C:\Windows\Fonts\tahoma.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ):
            if os.path.exists(candidate):
                try:
                    pdfmetrics.registerFont(TTFont("TestArabic", candidate))
                    font_name = "TestArabic"
                    break
                except Exception:
                    pass

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont(font_name, 12)
    c.drawString(100, 750, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def _arabic_extraction_works() -> bool:
    """True if this machine can build an Arabic PDF whose text PyMuPDF can read.

    Arabic-content end-to-end tests skip when this is False (e.g. a CI image
    without Arabic fonts). The language detector itself is still covered by the
    unit tests, which don't need a PDF.
    """
    import fitz

    pdf = _make_pdf_bytes("ضمان حسن التنفيذ")
    try:
        doc = fitz.open(stream=pdf, filetype="pdf")
        text = doc.load_page(0).get_text("text")
        doc.close()
    except Exception:
        return False
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


@pytest.fixture
def valid_pdf_bytes() -> bytes:
    return _make_pdf_bytes("Sample tender content for testing.")


@pytest.fixture
def valid_pdf_arabic_bytes() -> bytes:
    return _make_pdf_bytes("هذا اختبار لمحتوى المناقصة باللغة العربية")


@pytest.fixture
def not_pdf_bytes() -> bytes:
    """A plain-text file masquerading as PDF content."""
    return b"This is not a PDF at all, just plain text."


@pytest.fixture
def corrupt_pdf_bytes() -> bytes:
    """Has the PDF magic bytes but is structurally broken (PyMuPDF will reject)."""
    return b"%PDF-1.4\n\nthis is not a valid pdf body\n%%EOF"


@pytest.fixture
def scanned_pdf_bytes() -> bytes:
    """An image-only PDF with no extractable text layer (simulates a scanned tender).

    Built with reportlab: a blank page with a drawn rectangle and NO text. The
    Ingestor should detect near-zero extractable text and fail the run with a
    'scanned' reason (REQ-001 Alt Flow 5; OCR out of MVP scope).
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from io import BytesIO

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    # Draw shapes but no string — get_text() returns "" on this page.
    c.rect(100, 700, 200, 50, fill=1, stroke=1)
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest_asyncio.fixture
async def stub_embeddings(monkeypatch):
    """Replace the Gemini embeddings client with a deterministic stub.

    Set this fixture on any test that exercises the ingestion pipeline so the
    suite never makes a real (billed, rate-limited) Gemini call (senior-qa
    skill: never hit real providers in CI).

    NOTE: the ingestor node did `from app.agents.embeddings import
    get_embeddings_client`, binding the name into ITS OWN namespace. Patching
    `app.agents.embeddings.get_embeddings_client` alone does nothing for it —
    we must patch the symbol where it's actually looked up.
    """
    from app.agents import embeddings as embeddings_module
    from app.agents.nodes import ingestor as ingestor_module
    from app.agents.embeddings import make_stub_embeddings

    stub = make_stub_embeddings(dim=get_settings().embedding_dimensions)
    # The factory module (for anything building a client fresh) ...
    monkeypatch.setattr(embeddings_module, "get_embeddings_client", lambda: stub)
    # ... AND the ingestor's own bound reference (the path actually taken by
    # `run_ingestion` -> `ingest_tender`).
    monkeypatch.setattr(ingestor_module, "get_embeddings_client", lambda: stub)
    return stub

