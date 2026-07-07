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

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from langchain_core.exceptions import OutputParserException
from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# `fakeredis` provides an in-process async Redis for deterministic rate-limit tests.
import fakeredis.aioredis

from app.agents.skills.feasibility_scoring import (
    DimensionScore,
    FeasibilityOutput,
    SCOPE_ANCHOR_QUERIES,
)
from app.agents.skills.risk_clause_extraction import RiskFinding, RiskRadarOutput
from app.config import get_settings
from app.db import models  # noqa: F401  (register metadata)
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.middleware import rate_limit as rate_limit_module
from app.middleware.auth import _hash_key
from app.db.models import Company, CompanyProfile, Tender, TenderChunk

settings = get_settings()

# Allow CI/dev to point tests at a dedicated test database without touching
# the running dev database (REQ-002 QA slice).
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", settings.database_url)

# A separate engine+sessionmaker bound to the test connection so we can wrap
# every test in a single rollback. Tests must not see each other's writes.
_test_engine = create_async_engine(
    TEST_DATABASE_URL,
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


@pytest_asyncio.fixture(autouse=True)
async def _reset_checkpointer() -> None:
    """Clear the graph checkpointer's pool + saver before each test.

    ``_ensure_saver`` (:file:`app/agents/graph.py`) lazily creates a psycopg
    ``AsyncConnectionPool`` bound to the current event loop.  pytest-asyncio
    uses a fresh event loop per test function by default; if the checkpointer
    still holds a pool from a prior loop, ``_ensure_saver`` will try to close
    it via ``await self._pool.close()`` — but that pool's connections belong
    to the **old** (closed) loop, producing ``CancelledError``.

    We set both ``_pool`` and ``_saver`` to ``None`` before each test so that
    ``_ensure_saver`` skips the close step and creates a fresh pool in the
    current test's loop.  The old pool is garbage-collected along with its
    dead event loop — no need for explicit teardown.
    """
    from app.agents.graph import graph

    graph.checkpointer._pool = None
    graph.checkpointer._saver = None


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


# ---- REQ-002 company-profile fixtures ---------------------------------------

@pytest_asyncio.fixture
async def async_client(app_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """Alias for app_client matching the REQ-002 QA slice naming."""
    yield app_client


@pytest_asyncio.fixture
async def company_api_key(company_a: tuple[Company, str]) -> str:
    """Raw API key for the primary test tenant."""
    return company_a[1]


@pytest_asyncio.fixture
async def second_company_api_key(company_b: tuple[Company, str]) -> str:
    """Raw API key for a second test tenant (cross-tenant isolation)."""
    return company_b[1]


@pytest_asyncio.fixture
async def clean_profile(
    db: AsyncSession, company_a: tuple[Company, str]
) -> AsyncIterator[None]:
    """Delete any profile for the primary test tenant before and after a test."""
    from sqlalchemy import delete

    company_id = company_a[0].id

    async def _delete() -> None:
        await db.execute(
            delete(CompanyProfile).where(CompanyProfile.company_id == company_id)
        )
        await db.commit()

    await _delete()
    yield
    await _delete()


@pytest_asyncio.fixture
async def profile_lookup_session(
    db: AsyncSession, monkeypatch: Any
) -> AsyncIterator[None]:
    """Route profile_lookup's SessionLocal through the test transaction."""
    import importlib
    from contextlib import asynccontextmanager

    profile_lookup_module = importlib.import_module("app.agents.tools.profile_lookup")

    @asynccontextmanager
    async def _test_session() -> AsyncIterator[AsyncSession]:
        yield db

    monkeypatch.setattr(profile_lookup_module, "SessionLocal", _test_session)
    yield


# ---- REQ-003 analysis-run fixtures ------------------------------------------

EMBEDDING_STUB = [0.01] * get_settings().embedding_dimensions


@pytest_asyncio.fixture
async def ready_tender(db: AsyncSession, company_a: tuple[Company, str]) -> Tender:
    """Tender with status='ready' and 3 tender_chunks rows."""
    company, _ = company_a
    tender = Tender(
        id=str(uuid.uuid4()),
        company_id=company.id,
        filename="analysis_test.pdf",
        storage_path="/tmp/analysis_test.pdf",
        file_size_bytes=100,
        status="ready",
    )
    db.add(tender)
    await db.flush()

    for i in range(3):
        chunk = TenderChunk(
            id=str(uuid.uuid4()),
            tender_id=tender.id,
            company_id=company.id,
            chunk_index=i,
            content=f"Chunk {i} content for analysis testing.",
            detected_language="en",
            embedding=EMBEDDING_STUB,
        )
        db.add(chunk)
    await db.flush()
    return tender


@pytest_asyncio.fixture
async def company_with_profile(
    db: AsyncSession, company_a: tuple[Company, str]
) -> tuple[Company, str]:
    """company_a augmented with a valid CompanyProfile row."""
    company, raw_key = company_a
    profile = CompanyProfile(
        company_id=company.id,
        specializations=["civil"],
        financial_capacity={
            "currency": "SAR",
            "annual_turnover": 1_000_000,
            "available_bonding_capacity": 500_000,
        },
        geographic_reach=["SA"],
        past_projects=[],
        max_project_value=500_000,
    )
    db.add(profile)
    await db.flush()
    return company_a


@pytest_asyncio.fixture
async def company_without_profile(db: AsyncSession) -> tuple[Company, str]:
    """Company with NO CompanyProfile (failure-path test)."""
    return await create_company(db, name="No Profile Co")


@pytest_asyncio.fixture
async def graph_session(db: AsyncSession, monkeypatch: Any) -> None:
    """Route run_graph's with_session() through the per-test transaction.

    The POST /analyse endpoint schedules run_graph as a BackgroundTask, which
    opens its own database session via ``with_session()`` from
    ``app.db.session``. Without this patch, that session would use the
    production engine — writes would leak outside the test transaction and
    persist across tests.

    We patch the module-level reference that tenders.py imported at load time
    so that ``run_graph`` uses the same per-test ``db`` session, keeping all
    writes inside the savepoint-based rollback boundary.
    """
    from app.routers import tenders as tenders_router

    class _GraphSessionCtx:
        async def __aenter__(self) -> AsyncSession:
            return db

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(tenders_router, "with_session", lambda: _GraphSessionCtx())
    yield


# ---- REQ-004 Risk Radar fixtures --------------------------------------------

class _MockStructuredLLM:
    """Mock structured-output LLM that returns a canned RiskRadarOutput or raises.

    Tracks call_count so tests can verify retry behaviour without inspecting
    log output or timing.
    """

    def __init__(
        self,
        return_value: RiskRadarOutput | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._return_value = return_value
        self._raise_exc = raise_exc
        self.call_count = 0

    async def ainvoke(self, messages: list, config: dict | None = None, **kwargs: Any) -> Any:
        self.call_count += 1
        if self._raise_exc:
            raise self._raise_exc
        return self._return_value


@pytest.fixture
def sample_chunks() -> list[dict]:
    """5 realistic chunk dicts matching the Ingestor output shape (REQ-004).

    Includes one Arabic chunk so bilingual-dedup tests have a realistic source.
    """
    return [
        {
            "content": (
                "The Contractor shall pay liquidated damages for delay in completion "
                "at the rate of 0.1% of the Contract Price per day, up to a maximum "
                "of 10% of the Contract Price."
            ),
            "detected_language": "en",
            "chunk_index": 0,
        },
        {
            "content": (
                "The Performance Security shall be in the amount of 10% of the "
                "Contract Price and shall be issued by a bank acceptable to the "
                "Employer. The security shall remain valid until the issue of the "
                "Taking-Over Certificate."
            ),
            "detected_language": "en",
            "chunk_index": 1,
        },
        {
            "content": (
                "The Employer may terminate the Contract if the Contractor "
                "subcontracts the whole of the Works without prior approval, "
                "or becomes bankrupt or insolvent. Termination shall take effect "
                "upon receipt of the notice."
            ),
            "detected_language": "en",
            "chunk_index": 2,
        },
        {
            "content": (
                "يجب على المقاول تقديم خطاب ضمان حسن التنفيذ بنسبة 5% من قيمة العقد "
                "ويكون ساري المفعول حتى تاريخ إصدار شهادة الاستلام الابتدائي"
            ),
            "detected_language": "ar",
            "chunk_index": 3,
        },
        {
            "content": (
                "Any dispute arising out of or in connection with the Contract "
                "shall be referred to arbitration in accordance with the Rules of "
                "Arbitration of the International Chamber of Commerce."
            ),
            "detected_language": "en",
            "chunk_index": 4,
        },
    ]


@pytest_asyncio.fixture
async def mock_llm(monkeypatch: Any) -> _MockStructuredLLM:
    """Fixture: patches risk_radar._build_llm to return a canned valid output.

    The mock returns two findings with all required fields populated.
    """
    findings = RiskRadarOutput(findings=[
        RiskFinding(
            category="penalty",
            severity="high",
            clause_text="0.1% of the Contract Price per day, up to a maximum of 10%",
            explanation="Standard delay penalty within typical range",
            source_chunk_index=0,
            confidence=0.92,
        ),
        RiskFinding(
            category="fidic",
            severity="critical",
            clause_text="The Employer may terminate the Contract if the Contractor subcontracts the whole of the Works without prior approval",
            explanation="Uncapped termination right with no cure period",
            source_chunk_index=2,
            confidence=0.88,
        ),
    ])
    mock = _MockStructuredLLM(return_value=findings)
    monkeypatch.setattr("app.agents.nodes.risk_radar._build_llm", lambda: mock)
    return mock


@pytest_asyncio.fixture
async def mock_llm_malformed(monkeypatch: Any) -> _MockStructuredLLM:
    """Fixture: patches risk_radar._build_llm to always fail schema validation."""
    mock = _MockStructuredLLM(
        raise_exc=OutputParserException("Failed to parse LLM output as RiskRadarOutput")
    )
    monkeypatch.setattr("app.agents.nodes.risk_radar._build_llm", lambda: mock)
    return mock


@pytest_asyncio.fixture
async def mock_llm_api_error(monkeypatch: Any) -> _MockStructuredLLM:
    """Fixture: patches risk_radar._build_llm to raise API errors on every call."""
    mock = _MockStructuredLLM(raise_exc=Exception("Simulated API connection error"))
    monkeypatch.setattr("app.agents.nodes.risk_radar._build_llm", lambda: mock)
    return mock


# ---- REQ-005 Feasibility Scorer fixtures -----------------------------------

class _MockFeasibilityLLM:
    """Mock structured-output LLM that returns a canned FeasibilityOutput or raises.

    Tracks call_count so tests can verify retry behaviour without inspecting
    log output or timing. Mirrors the _MockStructuredLLM pattern used for
    risk_radar (REQ-004).
    """

    def __init__(
        self,
        return_value: FeasibilityOutput | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._return_value = return_value
        self._raise_exc = raise_exc
        self.call_count = 0

    async def ainvoke(
        self, messages: list, config: dict | None = None, **kwargs: Any
    ) -> Any:
        self.call_count += 1
        if self._raise_exc:
            raise self._raise_exc
        return self._return_value


@pytest_asyncio.fixture
async def mock_feasibility_llm(monkeypatch: Any) -> _MockFeasibilityLLM:
    """Fixture: patches feasibility_scorer._build_llm to return a FeasibilityOutput.

    Uses varied scores including one above 20 (timeline=25) and one below 0
    (geographic_scope=-3) to test clamping.  Uses model_construct to bypass
    Pydantic field-level validation for the out-of-range values.
    """
    output = FeasibilityOutput.model_construct(**{
        "technical_fit": DimensionScore.model_construct(
            score=18,
            rationale="Company specialisations of civil and roads cover the tender scope of highway construction.",
        ),
        "financial_capacity": DimensionScore.model_construct(
            score=14,
            rationale="Tender value is within company max_project_value and bonding capacity is adequate.",
        ),
        "timeline": DimensionScore.model_construct(
            score=25,
            rationale="Tender duration of 24 months is well within the company's demonstrated capability.",
        ),
        "geographic_scope": DimensionScore.model_construct(
            score=-3,
            rationale="Tender location in SA matches company geographic_reach of SA.",
        ),
        "past_experience": DimensionScore.model_construct(
            score=12,
            rationale="Company has 2 past_projects in roads and civil sectors covering similar scope.",
        ),
    })
    mock = _MockFeasibilityLLM(return_value=output)
    monkeypatch.setattr(
        "app.agents.nodes.feasibility_scorer._build_llm", lambda: mock
    )
    return mock


@pytest_asyncio.fixture
async def mock_feasibility_llm_malformed(
    monkeypatch: Any,
) -> _MockFeasibilityLLM:
    """Fixture: patches feasibility_scorer._build_llm to fail schema validation."""
    mock = _MockFeasibilityLLM(
        raise_exc=OutputParserException(
            "Failed to parse LLM output as FeasibilityOutput"
        )
    )
    monkeypatch.setattr(
        "app.agents.nodes.feasibility_scorer._build_llm", lambda: mock
    )
    return mock


@pytest_asyncio.fixture
async def mock_feasibility_llm_api_error(
    monkeypatch: Any,
) -> _MockFeasibilityLLM:
    """Fixture: patches feasibility_scorer._build_llm to raise API errors."""
    mock = _MockFeasibilityLLM(
        raise_exc=Exception("Simulated API connection error")
    )
    monkeypatch.setattr(
        "app.agents.nodes.feasibility_scorer._build_llm", lambda: mock
    )
    return mock


@pytest_asyncio.fixture
async def company_profile_fixture(
    db: AsyncSession, company_a: tuple[Company, str]
) -> tuple[Company, str]:
    """Company with a fully populated profile (all 6 fields of CompanyProfileSchema).

    Includes non-empty past_projects and all financial_capacity sub-fields
    so the feasibility scorer can score all 5 dimensions (REQ-005 Slice 5).
    """
    company, raw_key = company_a
    profile = CompanyProfile(
        company_id=company.id,
        specializations=["civil", "roads"],
        financial_capacity={
            "currency": "SAR",
            "annual_turnover": 1_000_000,
            "available_bonding_capacity": 500_000,
        },
        geographic_reach=["SA"],
        past_projects=[
            {
                "name": "Road Project Alpha",
                "value": 300_000,
                "year": 2024,
                "sector": "roads",
            },
            {
                "name": "Civil Works Beta",
                "value": 200_000,
                "year": 2023,
                "sector": "civil",
            },
        ],
        max_project_value=500_000,
    )
    db.add(profile)
    await db.flush()
    return company, raw_key


@pytest.fixture
def sample_scope_chunks() -> list[dict]:
    """5 chunk dicts covering project scope, value, timeline, location, qualifications.

    Matches the SCOPE_ANCHOR_QUERIES structure from feasibility_scoring.py
    (project description, contract value, timeline, location, qualifications).
    """
    return [
        {
            "content": (
                "The project involves the construction of a 15km highway connecting "
                "the industrial zone to the main port. Scope includes earthworks, "
                "paving, drainage systems, and lighting."
            ),
            "detected_language": "en",
            "chunk_index": 0,
        },
        {
            "content": (
                "The estimated contract value is SAR 45,000,000. The Employer will "
                "require a performance bond of 10% of the contract value upon award."
            ),
            "detected_language": "en",
            "chunk_index": 1,
        },
        {
            "content": (
                "The project duration is 24 months from the date of commencement. "
                "Expected completion date is December 2027. An early completion "
                "bonus of SAR 500,000 is available."
            ),
            "detected_language": "en",
            "chunk_index": 2,
        },
        {
            "content": (
                "The project is located in the Eastern Province of Saudi Arabia, "
                "approximately 50km from Dammam. Site access will be provided "
                "by the Employer."
            ),
            "detected_language": "en",
            "chunk_index": 3,
        },
        {
            "content": (
                "Contractors must have at least 10 years of experience in highway "
                "construction, a valid SAGMA classification in roadworks Grade A, "
                "and must have completed at least two projects of similar value "
                "in the GCC region."
            ),
            "detected_language": "en",
            "chunk_index": 4,
        },
    ]


# ---- REQ-006 Financial Analyst fixtures -------------------------------------


class _MockFinancialLLM:
    """Mock structured-output LLM that returns a canned FinancialOutput or raises.

    Tracks call_count so tests can verify retry behaviour without inspecting
    log output or timing. Mirrors the _MockStructuredLLM pattern used for
    risk_radar (REQ-004) and _MockFeasibilityLLM (REQ-005).
    """

    def __init__(
        self,
        return_value: Any | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._return_value = return_value
        self._raise_exc = raise_exc
        self.call_count = 0

    async def ainvoke(
        self, messages: list, config: dict | None = None, **kwargs: Any
    ) -> Any:
        self.call_count += 1
        if self._raise_exc:
            raise self._raise_exc
        return self._return_value


@pytest_asyncio.fixture
async def mock_financial_llm(monkeypatch: Any) -> _MockFinancialLLM:
    """Fixture: patches financial_analyst._build_llm to return a valid FinancialOutput.
    Contains all fields populated with the spec's canonical values.
    """
    from app.agents.skills.financial_extraction import (
        BondRequirement,
        FinancialOutput,
        LiquidatedDamages,
        MonetaryValue,
        PaymentMilestone,
    )

    output = FinancialOutput(
        contract_value=MonetaryValue(
            value=35_000_000.0, currency="SAR", needs_review=False,
        ),
        bonds=[
            BondRequirement(
                bond_type="performance",
                amount=MonetaryValue(
                    value=3_500_000.0, currency="SAR", needs_review=False,
                ),
                percentage=10.0,
                conditions=(
                    "Unconditional bank guarantee valid until issuance "
                    "of the Performance Certificate."
                ),
                source_chunk_index=0,
            ),
            BondRequirement(
                bond_type="advance_payment",
                amount=MonetaryValue(
                    value=5_250_000.0, currency="SAR", needs_review=False,
                ),
                percentage=15.0,
                conditions="Advance Payment Guarantee, 15% of contract value.",
                source_chunk_index=1,
            ),
        ],
        liquidated_damages=LiquidatedDamages(
            rate=MonetaryValue(
                value=5_000.0, currency="SAR", needs_review=False,
            ),
            period="per day",
            cap=MonetaryValue(
                value=3_500_000.0, currency="SAR", needs_review=False,
            ),
            cap_percentage=10.0,
            source_chunk_index=2,
        ),
        payment_schedule=[
            PaymentMilestone(
                description="Advance mobilisation payment",
                percentage=20.0,
                amount=None,
                trigger="on signing of Contract Agreement",
            ),
            PaymentMilestone(
                description="Completion of works",
                percentage=50.0,
                amount=None,
                trigger="on completion of all works",
            ),
            PaymentMilestone(
                description="Final payment on taking-over certificate",
                percentage=30.0,
                amount=None,
                trigger="on issuance of Taking-Over Certificate",
            ),
        ],
        retention_rate=5.0,
        advance_payment=MonetaryValue(
            value=5_250_000.0, currency="SAR", needs_review=False,
        ),
    )
    mock = _MockFinancialLLM(return_value=output)
    monkeypatch.setattr(
        "app.agents.nodes.financial_analyst._build_llm", lambda: mock
    )
    return mock


@pytest_asyncio.fixture
async def mock_financial_llm_invalid_currency(
    monkeypatch: Any,
) -> _MockFinancialLLM:
    """Fixture: returns a FinancialOutput with invalid currency codes.
    contract_value.currency = "Riyals" (mapped via CURRENCY_NORMALISATION)
    bonds[1].amount.currency = "INVALID_CURR" (-> UNKNOWN / needs_review=True)
    """
    from app.agents.skills.financial_extraction import (
        BondRequirement,
        FinancialOutput,
        MonetaryValue,
    )

    output = FinancialOutput(
        contract_value=MonetaryValue(
            value=35_000_000.0, currency="Riyals", needs_review=False,
        ),
        bonds=[
            BondRequirement(
                bond_type="performance",
                amount=MonetaryValue(
                    value=3_500_000.0, currency="SAR", needs_review=False,
                ),
                percentage=10.0,
                conditions="Performance bond.",
                source_chunk_index=0,
            ),
            BondRequirement(
                bond_type="advance_payment",
                amount=MonetaryValue(
                    value=5_250_000.0, currency="INVALID_CURR", needs_review=False,
                ),
                percentage=15.0,
                conditions="Advance payment guarantee.",
                source_chunk_index=1,
            ),
        ],
        liquidated_damages=None,
        payment_schedule=[],
        retention_rate=None,
        advance_payment=None,
    )
    mock = _MockFinancialLLM(return_value=output)
    monkeypatch.setattr(
        "app.agents.nodes.financial_analyst._build_llm", lambda: mock
    )
    return mock


@pytest_asyncio.fixture
async def mock_financial_llm_malformed(
    monkeypatch: Any,
) -> _MockFinancialLLM:
    """Fixture: patches financial_analyst._build_llm to fail schema validation."""
    mock = _MockFinancialLLM(
        raise_exc=OutputParserException(
            "Failed to parse LLM output as FinancialOutput"
        )
    )
    monkeypatch.setattr(
        "app.agents.nodes.financial_analyst._build_llm", lambda: mock
    )
    return mock


@pytest_asyncio.fixture
async def mock_financial_llm_api_error(
    monkeypatch: Any,
) -> _MockFinancialLLM:
    """Fixture: patches financial_analyst._build_llm to raise API errors."""
    mock = _MockFinancialLLM(
        raise_exc=Exception("Simulated API connection error")
    )
    monkeypatch.setattr(
        "app.agents.nodes.financial_analyst._build_llm", lambda: mock
    )
    return mock


@pytest_asyncio.fixture
async def mock_financial_llm_bilingual_duplicate(
    monkeypatch: Any,
) -> _MockFinancialLLM:
    """Fixture: returns a FinancialOutput with TWO performance bond entries.
    Simulates pre-dedup state from Arabic + English chunks.
    """
    from app.agents.skills.financial_extraction import (
        BondRequirement,
        FinancialOutput,
        MonetaryValue,
    )

    output = FinancialOutput(
        contract_value=MonetaryValue(
            value=35_000_000.0, currency="ريال سعودي", needs_review=False,
        ),
        bonds=[
            BondRequirement(
                bond_type="performance",
                amount=MonetaryValue(
                    value=3_500_000.0, currency="ريال سعودي", needs_review=False,
                ),
                percentage=10.0,
                conditions="خطاب ضمان حسن التنفيذ بنسبة 10% من قيمة العقد",
                source_chunk_index=0,
            ),
            BondRequirement(
                bond_type="performance",
                amount=MonetaryValue(
                    value=3_500_000.0, currency="SAR", needs_review=False,
                ),
                percentage=10.0,
                conditions="Performance bond of 10% of contract value.",
                source_chunk_index=1,
            ),
        ],
        liquidated_damages=None,
        payment_schedule=[],
        retention_rate=None,
        advance_payment=None,
    )
    mock = _MockFinancialLLM(return_value=output)
    monkeypatch.setattr(
        "app.agents.nodes.financial_analyst._build_llm", lambda: mock
    )
    return mock


# ---- REQ-007 HITL Override Gate fixtures -------------------------------


@pytest_asyncio.fixture
async def awaiting_hitl_run(
    app_client,
    db,
    company_with_profile,
    auth_headers,
    mock_llm,
    mock_feasibility_llm,
    mock_financial_llm,
    profile_lookup_session,
    monkeypatch,
) -> dict:
    """Create a tender, run full analysis, and return when state='awaiting_hitl'.

    The returned dict provides tender_id, run_id, company, and raw_key so each
    test can POST /approve or /override without repeating the setup.

    Depends on mock_llm fixtures so the graph runs without real API calls
    (REQ-007 QA: never hit real LLM providers in CI).

    The graph is run via its checkpointer directly (not through
    ``run_graph``) so the test session is never contested.  The DB state
    is updated manually after the graph reaches the HITL interrupt.
    """
    import asyncio
    from uuid import uuid4

    from app.db.models import AnalysisRun, Tender, TenderChunk
    from sqlalchemy import func, update

    company, raw_key = company_with_profile
    EMBEDDING_STUB = [0.01] * get_settings().embedding_dimensions

    # ── Mock embeddings ─────────────────────────────────────────────────────
    # Every module that calls get_embeddings_client must be patched separately
    # because Python's ``from ... import`` creates a local name binding that is
    # invisible to a module-level monkeypatch of the source module.
    class _MockEmbeddings:
        def embed_documents(self, texts):
            return [[0.01] * get_settings().embedding_dimensions for _ in texts]
        def embed_query(self, text):
            return [0.01] * get_settings().embedding_dimensions
    monkeypatch.setattr("app.agents.retrieval.get_embeddings_client", lambda: _MockEmbeddings())
    monkeypatch.setattr("app.agents.nodes.risk_radar.get_embeddings_client", lambda: _MockEmbeddings())

    tender = Tender(
        id=str(uuid4()),
        company_id=company.id,
        filename="hitl_test.pdf",
        storage_path="/tmp/hitl_test.pdf",
        file_size_bytes=100,
        status="ready",
    )
    db.add(tender)
    await db.flush()

    for i in range(3):
        chunk = TenderChunk(
            id=str(uuid4()),
            tender_id=tender.id,
            company_id=company.id,
            chunk_index=i,
            content=f"HITL test chunk {i} content for analysis testing.",
            detected_language="en",
            embedding=EMBEDDING_STUB,
        )
        db.add(chunk)
    await db.flush()

    run = AnalysisRun(
        id=str(uuid4()),
        tender_id=tender.id,
        company_id=company.id,
        state="pending",
    )
    db.add(run)
    await db.flush()
    run_id = run.id
    await db.commit()

    chunks = [
        {
            "content": f"HITL test chunk {i} content for analysis testing.",
            "detected_language": "en",
            "chunk_index": i,
        }
        for i in range(3)
    ]

    from app.agents.graph import graph
    from app.agents.state import TenderState

    initial_state = TenderState(
        tender_id=str(tender.id),
        run_id=str(run_id),
        company_id=str(company.id),
        chunks=chunks,
        supervisor_ready=False,
        risk_findings=[],
        feasibility_score=None,
        feasibility_breakdown=None,
        financial_summary=None,
        aggregated_results=None,
        hitl_approved=False,
        hitl_override_score=None,
        final_report=None,
        token_usage=[],
        source_languages=[],
    )
    config = {"configurable": {"thread_id": str(run_id)}}

    saw_aggregator = False
    async for event in graph.astream(initial_state, config):
        node_name = list(event.keys())[0]
        if node_name.startswith("__"):
            continue
        if node_name == "aggregator":
            saw_aggregator = True

    if saw_aggregator:
        final_checkpoint = await graph.aget_state(config)
        feasibility_score = final_checkpoint.values.get("feasibility_score") if final_checkpoint else None
        findings = (final_checkpoint.values.get("risk_findings", []) or []) if final_checkpoint else []
        financial_summary = (final_checkpoint.values.get("financial_summary", {}) or {}) if final_checkpoint else {}

        if findings:
            from sqlalchemy import insert
            from app.db.models import RiskFinding
            await db.execute(
                insert(RiskFinding).values([
                    {
                        "run_id": run_id,
                        "category": f["category"],
                        "severity": f["severity"],
                        "clause_text": f["clause_text"],
                        "explanation": f["explanation"],
                        "source_chunk_index": f["source_chunk_index"],
                        "confidence": f["confidence"],
                    }
                    for f in findings
                ])
            )

        if "error" not in financial_summary:
            from app.routers.tenders import _flatten_financial_summary
            commitment_rows = _flatten_financial_summary(financial_summary, run_id)
            if commitment_rows:
                from sqlalchemy import insert
                from app.db.models import FinancialCommitment
                await db.execute(
                    insert(FinancialCommitment).values(commitment_rows)
                )

        await db.execute(
            update(AnalysisRun)
            .where(AnalysisRun.id == run_id)
            .values(
                state="awaiting_hitl",
                feasibility_score=feasibility_score,
                started_at=func.now(),
            )
        )
        await db.commit()
    else:
        pytest.fail("Graph did not reach the aggregator — HITL gate not hit")

    return {
        "tender_id": tender.id,
        "run_id": run_id,
        "company": company,
        "raw_key": raw_key,
    }


@pytest_asyncio.fixture
async def mock_report_assembler(monkeypatch) -> None:
    """Patch report_assembler_node to return immediately with a mock report.

    Prevents real LLM calls during HITL tests even after REQ-008 wires in
    the real Report Assembler (REQ-007 QA: never make real LLM calls in tests).

    Patching the module-level function is sufficient because the compiled graph
    was constructed with ``add_node("report_assembler", report_assembler_node)``
    which stores a reference from the module-level name.  After REQ-008
    replaces the stub, this fixture will need to also update the compiled
    graph's internal node reference.
    """
    async def _mock(state, config):
        state["final_report"] = "MOCK REPORT"
        return state

    monkeypatch.setattr(
        "app.agents.nodes.report_assembler.report_assembler_node",
        _mock,
    )


@pytest_asyncio.fixture
async def second_company(company_b: tuple) -> str:
    """Raw API key for a second tenant (cross-tenant authorisation tests).

    Alias for ``second_company_api_key`` to match the naming in the REQ-007
    QA slice spec.
    """
    return company_b[1]


@pytest.fixture
def sample_financial_chunks() -> list[dict]:
    """6 chunk dicts covering bond requirements, payment terms, LD clauses,
    and contract value (REQ-006 financial anchor topics).
    """
    return [
        {
            "content": (
                "Section 4.2 - Performance Security. The Contractor shall provide "
                "a Performance Security in the form of an unconditional bank "
                "guarantee in the amount of 10% of the Accepted Contract Amount."
            ),
            "detected_language": "en",
            "chunk_index": 0,
        },
        {
            "content": (
                "Section 14.2 - Advance Payment. The Employer shall make an "
                "advance payment of 15% of the Accepted Contract Amount upon "
                "submission of the Performance Security."
            ),
            "detected_language": "en",
            "chunk_index": 1,
        },
        {
            "content": (
                "Section 8.7 - Delay Damages. The Contractor shall pay Delay "
                "Damages at the rate of SAR 5,000 per day. The total amount "
                "shall not exceed 10% of the Accepted Contract Amount."
            ),
            "detected_language": "en",
            "chunk_index": 2,
        },
        {
            "content": (
                "Section 14.3 - Interim Payments. The Contractor shall submit "
                "IPCs monthly. The Employer shall retain 5% of each IPC."
            ),
            "detected_language": "en",
            "chunk_index": 3,
        },
        {
            "content": (
                "The Accepted Contract Amount is SAR 35,000,000. Payment shall "
                "be made as follows: 20%% on signing, 50%% on completion, "
                "30%% on taking-over certificate."
            ),
            "detected_language": "en",
            "chunk_index": 4,
        },
        {
            "content": (
                "Section 14.2 - Advance Payment. The advance payment of "
                "SAR 5,250,000 shall be repaid by deductions from each IPC "
                "at a rate of 25%% of the amount of each IPC."
            ),
            "detected_language": "en",
            "chunk_index": 5,
        },
    ]

