"""Tests for POST /tenders/upload and GET /tenders/{id}.

Maps directly to REQ-001's Alternative Flows and Postconditions. Each test
names the REQ-001 scenario it covers so a reviewer can trace it back to the
spec. The tenant-isolation class is the highest-signal suite in the project
(api-security-reviewer API1/BOLA).

Ingestion here is the Slice-1 stub (sets status=ready, 0 chunks). Slice 2
swaps in the real ingestor and adds Alt Flows 4–6 + postcondition assertions
in test_ingestion_pipeline.py.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Tender

# asyncio_mode="auto" (pyproject.toml) runs async tests as coroutines
# automatically; no manual pytest.mark.asyncio needed.

settings = get_settings()


# Upload-flow tests assert on the upload response / pre-ingestion DB state only
# (REQ-001 Main Flow steps 1–5, 10). They never run the ingestor. But the upload
# endpoint schedules ingestion via FastAPI BackgroundTasks, and httpx's ASGI
# transport executes those tasks *inside* the request lifecycle. With the real
# ingestor that would call the Gemini client (no key in CI) and raise; with the
# stub it would flip status to 'ready', clobbering tests that assert the initial
# 'uploading' row state. Either way it pollutes the assertions in this file.
#
# The clean seam is the function the router actually schedules — `run_ingestion`
# — replaced with a no-op so no ingestion runs for these tests at all. The
# ingestion pipeline itself is covered by test_ingestion_pipeline.py, which calls
# run_ingestion explicitly with stubbed embeddings. This fixture stays scoped to
# THIS module.
@pytest.fixture(autouse=True)
async def _no_ingestion_for_upload_tests(monkeypatch):
    from app.routers import tenders as tenders_router

    async def _noop(_tender_id: str) -> None:
        return None

    monkeypatch.setattr(tenders_router, "run_ingestion", _noop)


# ---------------------------------------------------------------------------
# Main Flow — happy path
# ---------------------------------------------------------------------------

class TestUploadHappyPath:
    """REQ-001 Main Flow steps 1–5."""

    async def test_valid_pdf_returns_202_with_tender_id(
        self,
        app_client,
        company_a,
        auth_headers,
        valid_pdf_bytes,
    ):
        _, raw_key = company_a
        resp = await app_client.post(
            "/tenders/upload",
            files={"file": ("tender.pdf", valid_pdf_bytes, "application/pdf")},
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "uploading"
        assert "tender_id" in body
        # Stable UUID the client can reference on all downstream endpoints.
        assert len(body["tender_id"]) == 36

    async def test_upload_creates_tender_row_with_uploading_status(
        self,
        app_client,
        company_a,
        auth_headers,
        valid_pdf_bytes,
        db,
    ):
        _, raw_key = company_a
        resp = await app_client.post(
            "/tenders/upload",
            files={"file": ("tender.pdf", valid_pdf_bytes, "application/pdf")},
            headers=auth_headers(raw_key),
        )
        tender_id = resp.json()["tender_id"]

        tender = (await db.execute(select(Tender).where(Tender.id == tender_id))).scalar_one()
        assert tender.status == "uploading"
        assert tender.company_id is not None
        assert tender.file_size_bytes == len(valid_pdf_bytes)
        assert tender.filename == "tender.pdf"


# ---------------------------------------------------------------------------
# Alt Flow 1 — file is not a PDF -> 422
# ---------------------------------------------------------------------------

class TestAlternativeFlowNotPdf:
    """REQ-001 Alt Flow 1."""

    async def test_non_pdf_mime_returns_422(
        self,
        app_client,
        company_a,
        auth_headers,
        not_pdf_bytes,
    ):
        _, raw_key = company_a
        resp = await app_client.post(
            "/tenders/upload",
            files={"file": ("tender.txt", not_pdf_bytes, "text/plain")},
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 422
        assert "Only PDF files are supported" in resp.json()["detail"]

    async def test_pdf_mime_but_no_magic_bytes_returns_422(
        self,
        app_client,
        company_a,
        auth_headers,
        not_pdf_bytes,
    ):
        """Polyglot defence: a mislabelled file claiming application/pdf."""
        _, raw_key = company_a
        resp = await app_client.post(
            "/tenders/upload",
            files={"file": ("tender.pdf", not_pdf_bytes, "application/pdf")},
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 422
        assert "Only PDF files are supported" in resp.json()["detail"]

    async def test_no_db_row_created_for_non_pdf(
        self,
        app_client,
        company_a,
        auth_headers,
        not_pdf_bytes,
        db,
    ):
        _, raw_key = company_a
        await app_client.post(
            "/tenders/upload",
            files={"file": ("tender.txt", not_pdf_bytes, "text/plain")},
            headers=auth_headers(raw_key),
        )
        tenders = (await db.execute(select(Tender))).scalars().all()
        assert tenders == []


# ---------------------------------------------------------------------------
# Alt Flow 2 — file exceeds 50 MB -> 413
# ---------------------------------------------------------------------------

class TestAlternativeFlowOversize:
    """REQ-001 Alt Flow 2."""

    async def test_oversized_file_returns_413(
        self,
        app_client,
        company_a,
        auth_headers,
    ):
        _, raw_key = company_a
        # Synthesize a body just over the limit, with PDF magic bytes so we
        # reach the size check by exercising the same code path.
        oversized = b"%PDF-" + b"\x00" * (settings.max_upload_bytes + 1)
        resp = await app_client.post(
            "/tenders/upload",
            files={"file": ("big.pdf", oversized, "application/pdf")},
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 413
        assert "50MB" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Alt Flow 3 — rate limit exceeded -> 429 + Retry-After
# ---------------------------------------------------------------------------

class TestAlternativeFlowRateLimit:
    """REQ-001 Alt Flow 3 (Architecture §6.2 Redis sliding window)."""

    async def test_rate_limit_returns_429_with_retry_after(
        self,
        app_client,
        company_a,
        auth_headers,
        valid_pdf_bytes,
        fake_redis,
    ):
        _, raw_key = company_a
        limit = settings.rate_limit_rpm

        # Send `limit` allowed requests, then assert the next is blocked.
        for _ in range(limit):
            resp = await app_client.post(
                "/tenders/upload",
                files={"file": ("t.pdf", valid_pdf_bytes, "application/pdf")},
                headers=auth_headers(raw_key),
            )
            assert resp.status_code == 202, resp.text

        blocked = await app_client.post(
            "/tenders/upload",
            files={"file": ("t.pdf", valid_pdf_bytes, "application/pdf")},
            headers=auth_headers(raw_key),
        )
        assert blocked.status_code == 429
        assert "Retry-After" in blocked.headers
        assert int(blocked.headers["Retry-After"]) >= 1

    async def test_rate_limit_is_per_tenant(
        self,
        app_client,
        company_a,
        company_b,
        auth_headers,
        valid_pdf_bytes,
        fake_redis,
    ):
        """Exhausting tenant A's budget must NOT block tenant B."""
        _, key_a = company_a
        _, key_b = company_b

        for _ in range(settings.rate_limit_rpm):
            await app_client.post(
                "/tenders/upload",
                files={"file": ("t.pdf", valid_pdf_bytes, "application/pdf")},
                headers=auth_headers(key_a),
            )

        resp_b = await app_client.post(
            "/tenders/upload",
            files={"file": ("t.pdf", valid_pdf_bytes, "application/pdf")},
            headers=auth_headers(key_b),
        )
        assert resp_b.status_code == 202


# ---------------------------------------------------------------------------
# Tenant isolation (BOLA/IDOR) — the most important suite in the project.
# ---------------------------------------------------------------------------

class TestTenantIsolation:
    """api-security-reviewer API1: every {id} route must filter by company_id."""

    async def test_tenant_cannot_read_other_tenants_tender(
        self,
        app_client,
        company_a,
        company_b,
        auth_headers,
        valid_pdf_bytes,
    ):
        _, key_a = company_a
        _, key_b = company_b

        upload = await app_client.post(
            "/tenders/upload",
            files={"file": ("tender.pdf", valid_pdf_bytes, "application/pdf")},
            headers=auth_headers(key_a),
        )
        tender_id_a = upload.json()["tender_id"]

        # Tenant B tries to read tenant A's tender -> 404 (not 403).
        leak = await app_client.get(
            f"/tenders/{tender_id_a}",
            headers=auth_headers(key_b),
        )
        assert leak.status_code == 404

    async def test_owner_can_read_own_tender(
        self,
        app_client,
        company_a,
        auth_headers,
        valid_pdf_bytes,
    ):
        _, key_a = company_a
        upload = await app_client.post(
            "/tenders/upload",
            files={"file": ("tender.pdf", valid_pdf_bytes, "application/pdf")},
            headers=auth_headers(key_a),
        )
        tender_id = upload.json()["tender_id"]

        resp = await app_client.get(f"/tenders/{tender_id}", headers=auth_headers(key_a))
        assert resp.status_code == 200
        assert resp.json()["id"] == tender_id


# ---------------------------------------------------------------------------
# Auth — every protected route requires a valid Bearer key.
# ---------------------------------------------------------------------------

class TestAuth:
    async def test_missing_bearer_returns_401(self, app_client, valid_pdf_bytes):
        resp = await app_client.post(
            "/tenders/upload",
            files={"file": ("t.pdf", valid_pdf_bytes, "application/pdf")},
        )
        assert resp.status_code == 401

    async def test_invalid_bearer_returns_401(
        self, app_client, auth_headers, valid_pdf_bytes
    ):
        resp = await app_client.post(
            "/tenders/upload",
            files={"file": ("t.pdf", valid_pdf_bytes, "application/pdf")},
            headers=auth_headers("sk-not-a-real-key"),
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Monthly quota (REQ-001 Preconditions)
# ---------------------------------------------------------------------------

class TestMonthlyQuota:
    async def test_quota_exceeded_blocks_upload(
        self,
        app_client,
        db,
        auth_headers,
        valid_pdf_bytes,
        fake_redis,
    ):
        from tests.conftest import create_company

        company, raw_key = await create_company(db, name="Capped", monthly_doc_limit=1)

        # First upload succeeds (uses the single allowed slot).
        first = await app_client.post(
            "/tenders/upload",
            files={"file": ("t.pdf", valid_pdf_bytes, "application/pdf")},
            headers=auth_headers(raw_key),
        )
        assert first.status_code == 202

        # Second in the same month is rejected.
        second = await app_client.post(
            "/tenders/upload",
            files={"file": ("t.pdf", valid_pdf_bytes, "application/pdf")},
            headers=auth_headers(raw_key),
        )
        assert second.status_code == 429
        assert "quota" in second.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Input sanitization (api-security-reviewer API3 / log-injection defence)
# ---------------------------------------------------------------------------

class TestFilenameSanitization:
    def test_strips_path_components(self):
        from app.services.validation import sanitize_filename

        assert sanitize_filename("C:\\evil\\x.pdf") == "x.pdf"
        assert sanitize_filename("../../etc/passwd") == "passwd"
        assert sanitize_filename(None) == "tender.pdf"
        assert sanitize_filename("") == "tender.pdf"

    def test_strips_control_chars(self):
        from app.services.validation import sanitize_filename

        # Newlines would break log parsing if stored verbatim.
        cleaned = sanitize_filename("tender\r\nINJECTED.pdf")
        assert "\n" not in cleaned
        assert "\r" not in cleaned

    def test_reject_oversize_declared(self):
        from app.services.validation import reject_oversize_declared
        from app.errors import FileTooLarge

        reject_oversize_declared(None)  # chunked upload — fall back to post-read check
        reject_oversize_declared(1024)  # small, fine
        with pytest.raises(FileTooLarge):
            reject_oversize_declared(get_settings().max_upload_bytes + 1)
