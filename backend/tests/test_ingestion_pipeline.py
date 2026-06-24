"""Ingestion pipeline tests — REQ-001 Alt Flows 4–6 + postconditions.

These exercise `run_ingestion` directly (the BackgroundTask body). They use the
stub-embeddings fixture so no real Gemini call is made. The DB session is
shared with the test's transaction via `ingestion_session` so writes roll back.

Each test names the REQ-001 scenario it covers. The orphan-cleanup test is the
single highest-signal assertion in this file (REQ-001 Postcondition: no
partial/orphaned tender_chunks rows after a failed run).
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.agents.ingestion import run_ingestion
from app.config import get_settings
from app.db.models import Tender, TenderChunk

# asyncio_mode="auto" (pyproject.toml) runs async tests as coroutines
# automatically; no manual pytest.mark.asyncio needed.

settings = get_settings()


# The upload endpoint schedules `run_ingestion` via FastAPI BackgroundTasks, and
# httpx's ASGI transport runs those inside the request lifecycle. `_upload_and_run`
# then ALSO calls `run_ingestion` explicitly — so without neutralising the
# scheduled task, ingestion runs TWICE per tender: the first pass inserts the
# chunks, the second trips the unique (tender_id, chunk_index) constraint. We
# stub the router's reference to a no-op and drive ingestion solely via the
# explicit call (with stubbed embeddings), which is what these tests assert on.
@pytest.fixture(autouse=True)
async def _no_background_ingestion(monkeypatch):
    from app.routers import tenders as tenders_router

    async def _noop(_tender_id: str) -> None:
        return None

    monkeypatch.setattr(tenders_router, "run_ingestion", _noop)


async def _upload_and_run(
    app_client,
    auth_headers,
    db,
    raw_key: str,
    pdf_bytes: bytes,
    *,
    filename: str = "tender.pdf",
) -> str:
    """Upload a PDF, return its tender_id, then run ingestion for it.

    Upload commits the tenders row (via the test's shared session). We then
    call run_ingestion directly rather than relying on the BackgroundTask fire.
    """
    resp = await app_client.post(
        "/tenders/upload",
        files={"file": (filename, pdf_bytes, "application/pdf")},
        headers=auth_headers(raw_key),
    )
    assert resp.status_code == 202, resp.text
    tender_id = resp.json()["tender_id"]
    # Make sure the uploaded file actually landed on disk (ingestion reads it).
    tender = (await db.execute(select(Tender).where(Tender.id == tender_id))).scalar_one()
    from pathlib import Path

    assert Path(tender.storage_path).exists(), "uploaded PDF not on disk"
    await run_ingestion(tender_id)
    return tender_id


# ---------------------------------------------------------------------------
# Happy path (postconditions) — REQ-001 step 9, Acceptance Criteria
# ---------------------------------------------------------------------------

class TestIngestionSuccess:
    async def test_valid_pdf_ingests_to_ready_with_embeddings(
        self,
        app_client,
        company_a,
        auth_headers,
        db,
        ingestion_session,
        stub_embeddings,
        valid_pdf_bytes,
    ):
        _, raw_key = company_a
        tender_id = await _upload_and_run(
            app_client, auth_headers, db, raw_key, valid_pdf_bytes
        )

        tender = (
            await db.execute(select(Tender).where(Tender.id == tender_id))
        ).scalar_one()
        assert tender.status == "ready"
        assert tender.primary_language == "en"
        assert tender.page_count == 1
        assert tender.error_reason is None

        chunks = (
            await db.execute(
                select(TenderChunk)
                .where(TenderChunk.tender_id == tender_id)
                .order_by(TenderChunk.chunk_index)
            )
        ).scalars().all()
        assert len(chunks) >= 1
        # Postcondition: every chunk has a non-null embedding vector.
        for c in chunks:
            assert c.embedding is not None
            assert len(c.embedding) == settings.embedding_dimensions
            assert c.detected_language in {"ar", "en", "mixed"}
            assert c.page_number is not None

    async def test_arabic_pdf_detected_as_ar(
        self,
        app_client,
        company_a,
        auth_headers,
        db,
        ingestion_session,
        stub_embeddings,
        valid_pdf_arabic_bytes,
    ):
        # Arabic extraction needs an Arabic-capable TTF on the machine. The
        # language detector itself is covered by the unit tests below.
        from tests.conftest import _arabic_extraction_works

        if not _arabic_extraction_works():
            pytest.skip("No Arabic-capable font available for the PDF fixture.")

        _, raw_key = company_a
        tender_id = await _upload_and_run(
            app_client, auth_headers, db, raw_key, valid_pdf_arabic_bytes
        )
        tender = (
            await db.execute(select(Tender).where(Tender.id == tender_id))
        ).scalar_one()
        assert tender.status == "ready"
        assert tender.primary_language == "ar"


# ---------------------------------------------------------------------------
# Alt Flow 4 — corrupt / password-protected PDF -> status='failed'
# ---------------------------------------------------------------------------

class TestAlternativeFlowCorrupt:
    async def test_corrupt_pdf_fails_with_reason(
        self,
        app_client,
        company_a,
        auth_headers,
        db,
        ingestion_session,
        stub_embeddings,
        corrupt_pdf_bytes,
    ):
        _, raw_key = company_a
        tender_id = await _upload_and_run(
            app_client, auth_headers, db, raw_key, corrupt_pdf_bytes
        )

        tender = (
            await db.execute(select(Tender).where(Tender.id == tender_id))
        ).scalar_one()
        assert tender.status == "failed"
        assert tender.error_reason is not None
        # Reason must be specific (REQ-001 Usability NFR), not a generic 500.
        assert len(tender.error_reason) > 0


# ---------------------------------------------------------------------------
# Alt Flow 5 — scanned PDF (no extractable text) -> status='failed', scanned reason
# ---------------------------------------------------------------------------

class TestAlternativeFlowScanned:
    async def test_scanned_pdf_fails_with_scanned_reason(
        self,
        app_client,
        company_a,
        auth_headers,
        db,
        ingestion_session,
        stub_embeddings,
        scanned_pdf_bytes,
    ):
        _, raw_key = company_a
        tender_id = await _upload_and_run(
            app_client, auth_headers, db, raw_key, scanned_pdf_bytes
        )

        tender = (
            await db.execute(select(Tender).where(Tender.id == tender_id))
        ).scalar_one()
        assert tender.status == "failed"
        # Reason must mention scanning/OCR so the analyst can act on it.
        reason = (tender.error_reason or "").lower()
        assert "scan" in reason or "ocr" in reason


# ---------------------------------------------------------------------------
# Alt Flow 6 — embedding API failure -> retry -> status='failed'
# ---------------------------------------------------------------------------

class _AlwaysFailsEmbeddings:
    """An embeddings client whose every call raises a non-rate-limit error.

    Simulates the embedding service being down (not a 429 — those would retry
    via tenacity). The ingestor should fail the run after retries are exhausted.
    """

    def embed_documents(self, texts):
        raise RuntimeError("embedding service permanently unavailable")

    def embed_query(self, text):
        raise RuntimeError("embedding service permanently unavailable")


class TestAlternativeFlowEmbeddingFailure:
    async def test_embedding_failure_marks_failed(
        self,
        app_client,
        company_a,
        auth_headers,
        db,
        ingestion_session,
        monkeypatch,
        valid_pdf_bytes,
    ):
        _, raw_key = company_a
        # Override the factory to return our failing client. Must patch BOTH the
        # factory module and the ingestor's own bound reference — the ingestor did
        # `from ...embeddings import get_embeddings_client`, so it holds its own
        # name and a factory-only patch is invisible to it.
        from app.agents import embeddings as embeddings_module
        from app.agents.nodes import ingestor as ingestor_module

        failing = _AlwaysFailsEmbeddings()
        _factory = lambda: failing  # noqa: E731
        monkeypatch.setattr(embeddings_module, "get_embeddings_client", _factory)
        monkeypatch.setattr(ingestor_module, "get_embeddings_client", _factory)

        tender_id = await _upload_and_run(
            app_client, auth_headers, db, raw_key, valid_pdf_bytes
        )
        tender = (
            await db.execute(select(Tender).where(Tender.id == tender_id))
        ).scalar_one()
        assert tender.status == "failed"
        assert tender.error_reason is not None


# ---------------------------------------------------------------------------
# Postcondition — no orphan tender_chunks after a failed run
# ---------------------------------------------------------------------------

class TestNoOrphanChunks:
    async def test_failed_run_leaves_no_chunks(
        self,
        app_client,
        company_a,
        auth_headers,
        db,
        ingestion_session,
        monkeypatch,
        valid_pdf_bytes,
    ):
        """REQ-001 Postcondition: cleanup on failure is atomic.

        A corrupt PDF path doesn't reach chunk insertion, so we instead force a
        failure *after* chunks would be built by killing embeddings. The
        contract is the same: status='failed' AND zero tender_chunks rows.
        """
        _, raw_key = company_a
        failing = _AlwaysFailsEmbeddings()
        # Patch the symbol where the ingestor actually reads it (see note above).
        from app.agents.nodes import ingestor as ingestor_module

        _factory = lambda: failing  # noqa: E731
        monkeypatch.setattr(ingestor_module, "get_embeddings_client", _factory)

        tender_id = await _upload_and_run(
            app_client, auth_headers, db, raw_key, valid_pdf_bytes
        )

        tender = (
            await db.execute(select(Tender).where(Tender.id == tender_id))
        ).scalar_one()
        assert tender.status == "failed"

        rows = (
            await db.execute(
                select(TenderChunk).where(TenderChunk.tender_id == tender_id)
            )
        ).scalars().all()
        assert rows == [], "failed run left orphaned tender_chunks rows"


# ---------------------------------------------------------------------------
# Unit-level: language + chunking sanity (no DB)
# ---------------------------------------------------------------------------

class TestLanguageDetectionUnit:
    async def test_english(self):
        from app.services.language import detect_language

        assert detect_language("Performance bond and liquidated damages clause.") == "en"

    async def test_arabic(self):
        from app.services.language import detect_language

        assert detect_language("ضمان حسن التنفيذ وبند الغرامات التأخيرية") == "ar"

    async def test_mixed(self):
        from app.services.language import detect_language

        assert detect_language("Performance bond ضمان حسن التنفيذ") == "mixed"

    async def test_primary_aggregation(self):
        from app.services.language import primary_language

        assert primary_language({"ar": 30, "en": 2}) == "ar"
        assert primary_language({"en": 40, "ar": 3}) == "en"
        assert primary_language({"ar": 10, "en": 10}) == "bilingual"
