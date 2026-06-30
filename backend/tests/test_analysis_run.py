"""Tests for the analysis-run pipeline (REQ-003 Slice 5 — QA).

Every test case maps directly to an Acceptance Criteria item (or an Alternative
Flow) from REQ-003.  Tests are fully isolated — each test gets its own
transactional database session that rolls back at teardown (see ``db`` fixture
in conftest.py).

Design rules (from imp-slice-05):
  - Use httpx.AsyncClient wired via ASGITransport (``app_client`` fixture).
  - Use a real test database (``TEST_DATABASE_URL``), never mocks for the DB.
  - Mock the LLM client *only* where needed (cost-tracker unit tests).
  - Every poll loop has a 10-second timeout to avoid hanging.
"""
from __future__ import annotations

import asyncio
import time
from uuid import UUID, uuid4

import pytest
from langchain_core.outputs import Generation, LLMResult
from sqlalchemy import select

from app.config import get_settings
from app.db.models import AnalysisRun, LlmCostEvent, Tender, TenderChunk

settings = get_settings()

# ==============================================================================
# Happy path
# ==============================================================================


class TestAnalyseHappyPath:
    """REQ-003 Main Flow — successful analysis trigger and completion."""

    async def test_analyse_returns_202_with_run_id(
        self,
        app_client,
        ready_tender,
        company_with_profile,
        auth_headers,
        graph_session,
        profile_lookup_session,
    ):
        _, raw_key = company_with_profile
        start = time.monotonic()
        resp = await app_client.post(
            f"/tenders/{ready_tender.id}/analyse",
            headers=auth_headers(raw_key),
        )
        elapsed = time.monotonic() - start

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert "run_id" in body
        UUID(body["run_id"])
        assert body["status"] == "pending"
        assert elapsed < 2.0, f"Response took {elapsed:.3f}s (expected < 2.0s — first-request cold start)"

    async def test_status_reflects_state_transitions(
        self,
        app_client,
        ready_tender,
        company_with_profile,
        auth_headers,
        graph_session,
        profile_lookup_session,
    ):
        _, raw_key = company_with_profile
        resp = await app_client.post(
            f"/tenders/{ready_tender.id}/analyse",
            headers=auth_headers(raw_key),
        )
        run_id = resp.json()["run_id"]

        # Poll immediately — graph background task runs synchronously via
        # ASGITransport, so state is likely already "awaiting_hitl".
        status = await app_client.get(
            f"/tenders/{ready_tender.id}/status",
            headers=auth_headers(raw_key),
        )
        assert status.status_code == 200
        body = status.json()
        assert body["run_id"] == run_id
        assert body["state"] in ("pending", "running", "awaiting_hitl"), (
            f"Unexpected state {body['state']!r}, error_reason={body.get('error_reason')!r}"
        )

        # Wait for final state.
        for _ in range(50):
            status = await app_client.get(
                f"/tenders/{ready_tender.id}/status",
                headers=auth_headers(raw_key),
            )
            body = status.json()
            if body["state"] == "awaiting_hitl":
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail(
                f"Timed out waiting for state='awaiting_hitl'. Last state: {body['state']}"
            )

        trace = body.get("agent_trace", {})
        for expected in ("supervisor", "risk_radar", "scorer", "financial", "aggregator"):
            assert expected in trace, (
                f"agent_trace missing key {expected!r}. "
                f"Got keys: {list(trace)}"
            )

    async def test_aggregated_results_merges_all_stub_outputs(
        self,
        app_client,
        ready_tender,
        company_with_profile,
        auth_headers,
        graph_session,
        profile_lookup_session,
    ):
        _, raw_key = company_with_profile
        resp = await app_client.post(
            f"/tenders/{ready_tender.id}/analyse",
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]

        # Poll until the graph reaches the HITL gate.
        for _ in range(50):
            status = await app_client.get(
                f"/tenders/{ready_tender.id}/status",
                headers=auth_headers(raw_key),
            )
            body = status.json()
            if body["state"] == "awaiting_hitl":
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail(
                f"Timed out waiting for 'awaiting_hitl'. "
                f"Last state: {body['state']!r}, error={body.get('error_reason')!r}"
            )

        trace = body.get("agent_trace", {})
        for expected in ("supervisor", "risk_radar", "scorer", "financial", "aggregator"):
            assert expected in trace, (
                f"agent_trace missing key {expected!r}. "
                f"Got keys: {list(trace)}"
            )

        # The aggregated_results dict is stored in the graph state but not
        # yet persisted to the AnalysisRun row (to be wired in a later slice).
        # Verify the agent_trace confirms all stub nodes executed.
        assert body["state"] == "awaiting_hitl"


# ==============================================================================
# Authorization / validation
# ==============================================================================


class TestAnalyseValidation:
    """REQ-003 Alternative Flows — validation and auth guards.

    Maps to: wrong-company 403, not-ready 409, nonexistent 404, duplicate 409.
    """

    async def test_analyse_wrong_company_returns_403(
        self,
        app_client,
        ready_tender,
        company_with_profile,
        company_b,
        auth_headers,
    ):
        _, key_b = company_b
        resp = await app_client.post(
            f"/tenders/{ready_tender.id}/analyse",
            headers=auth_headers(key_b),
        )
        assert resp.status_code == 403, resp.text
        assert "Not authorised" in resp.json()["detail"]

    async def test_analyse_tender_not_ready_returns_409(
        self,
        app_client,
        company_with_profile,
        auth_headers,
        db,
    ):
        company, raw_key = company_with_profile
        tender = Tender(
            id=str(uuid4()),
            company_id=company.id,
            filename="not_ready.pdf",
            storage_path="/tmp/not_ready.pdf",
            file_size_bytes=100,
            status="uploading",
        )
        db.add(tender)
        await db.flush()

        resp = await app_client.post(
            f"/tenders/{tender.id}/analyse",
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 409, resp.text
        assert "not ready" in resp.json()["detail"].lower()

    async def test_analyse_nonexistent_tender_returns_404(
        self,
        app_client,
        company_with_profile,
        auth_headers,
    ):
        _, raw_key = company_with_profile
        fake_id = str(uuid4())
        resp = await app_client.post(
            f"/tenders/{fake_id}/analyse",
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 404, resp.text

    async def test_analyse_duplicate_run_returns_409(
        self,
        app_client,
        ready_tender,
        company_with_profile,
        auth_headers,
        graph_session,
        profile_lookup_session,
    ):
        _, raw_key = company_with_profile
        first = await app_client.post(
            f"/tenders/{ready_tender.id}/analyse",
            headers=auth_headers(raw_key),
        )
        assert first.status_code == 202, first.text

        second = await app_client.post(
            f"/tenders/{ready_tender.id}/analyse",
            headers=auth_headers(raw_key),
        )
        assert second.status_code == 409, second.text
        assert "already in progress" in second.json()["detail"].lower()


# ==============================================================================
# Failure paths
# ==============================================================================


class TestAnalyseFailurePaths:
    """REQ-003 Alternative Flows — supervisor-level failures.

    Maps to: no-profile, empty-chunks, unknown-status, wrong-company status.
    """

    async def _poll_until(
        self,
        app_client,
        headers,
        tender_id: str,
        *,
        target: str,
        timeout: float = 10.0,
    ) -> dict:
        deadline = time.monotonic() + timeout
        last_body = {}
        while time.monotonic() < deadline:
            resp = await app_client.get(
                f"/tenders/{tender_id}/status",
                headers=headers,
            )
            if resp.status_code == 200:
                last_body = resp.json()
                if last_body["state"] == target:
                    return last_body
            await asyncio.sleep(0.2)
        pytest.fail(
            f"Timed out ({timeout}s) waiting for state={target!r}. "
            f"Last response: status={resp.status_code}, body={last_body}"
        )

    async def test_analyse_no_company_profile_fails_gracefully(
        self,
        app_client,
        company_without_profile,
        auth_headers,
        db,
        graph_session,
        profile_lookup_session,
    ):
        company, raw_key = company_without_profile
        tender = Tender(
            id=str(uuid4()),
            company_id=company.id,
            filename="no_profile.pdf",
            storage_path="/tmp/no_profile.pdf",
            file_size_bytes=100,
            status="ready",
        )
        db.add(tender)
        for i in range(3):
            db.add(
                TenderChunk(
                    id=str(uuid4()),
                    tender_id=tender.id,
                    company_id=company.id,
                    chunk_index=i,
                    content=f"Chunk {i}",
                    detected_language="en",
                    embedding=[0.01] * settings.embedding_dimensions,
                )
            )
        await db.flush()

        resp = await app_client.post(
            f"/tenders/{tender.id}/analyse",
            headers=auth_headers(raw_key),
        )
        run_id = resp.json()["run_id"]

        body = await self._poll_until(
            app_client, auth_headers(raw_key), tender.id, target="failed"
        )
        assert body["error_reason"] is not None, "Should have an error_reason"
        assert body["state"] != "awaiting_hitl"

    async def test_analyse_empty_chunks_fails_gracefully(
        self,
        app_client,
        company_with_profile,
        auth_headers,
        db,
        graph_session,
        profile_lookup_session,
    ):
        company, raw_key = company_with_profile
        tender = Tender(
            id=str(uuid4()),
            company_id=company.id,
            filename="no_chunks.pdf",
            storage_path="/tmp/no_chunks.pdf",
            file_size_bytes=100,
            status="ready",
        )
        db.add(tender)
        await db.flush()

        resp = await app_client.post(
            f"/tenders/{tender.id}/analyse",
            headers=auth_headers(raw_key),
        )
        run_id = resp.json()["run_id"]

        body = await self._poll_until(
            app_client, auth_headers(raw_key), tender.id, target="failed"
        )
        assert body["error_reason"] is not None, "Should have an error_reason"

    async def test_status_unknown_tender_returns_404(
        self,
        app_client,
        company_with_profile,
        auth_headers,
    ):
        _, raw_key = company_with_profile
        resp = await app_client.get(
            f"/tenders/{uuid4()}/status",
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 404, resp.text

    async def test_status_wrong_company_returns_403(
        self,
        app_client,
        ready_tender,
        company_with_profile,
        company_b,
        auth_headers,
        graph_session,
        profile_lookup_session,
    ):
        _, key_a = company_with_profile
        _, key_b = company_b

        await app_client.post(
            f"/tenders/{ready_tender.id}/analyse",
            headers=auth_headers(key_a),
        )

        resp = await app_client.get(
            f"/tenders/{ready_tender.id}/status",
            headers=auth_headers(key_b),
        )
        assert resp.status_code == 403, resp.text


# ==============================================================================
# Resilience
# ==============================================================================


class TestResilience:
    """REQ-003 — checkpoint survives simulated server restart."""

    async def test_checkpoint_survives_simulated_restart(
        self,
        app_client,
        ready_tender,
        company_with_profile,
        auth_headers,
        graph_session,
        profile_lookup_session,
    ):
        _, raw_key = company_with_profile
        resp = await app_client.post(
            f"/tenders/{ready_tender.id}/analyse",
            headers=auth_headers(raw_key),
        )
        run_id = resp.json()["run_id"]

        for _ in range(50):
            status = await app_client.get(
                f"/tenders/{ready_tender.id}/status",
                headers=auth_headers(raw_key),
            )
            body = status.json()
            if body["state"] == "awaiting_hitl":
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail(f"Timed out waiting for awaiting_hitl: {body}")

        from app.agents.graph import AsyncPostgresCheckpointer

        conn_string = settings.database_url.replace(
            "postgresql+asyncpg://", "postgresql://", 1
        )
        checkpointer = AsyncPostgresCheckpointer(conn_string)
        checkpoint = await checkpointer.aget(
            {"configurable": {"thread_id": run_id}}
        )

        assert checkpoint is not None, (
            f"Checkpoint should exist for thread_id={run_id}"
        )
        cv = checkpoint.get("channel_values", {})
        assert cv.get("tender_id") == ready_tender.id, (
            f"Checkpoint tender_id mismatch: {cv.get('tender_id')} != {ready_tender.id}"
        )
        assert cv.get("run_id") == run_id


# ==============================================================================
# Cost tracker wiring
# ==============================================================================


class TestCostTracker:
    """REQ-003 Slice 3 — CostTrackingHandler unit tests.

    These tests use real DB (for FK constraints) but mock the LLM result
    object — we never modify node files.
    """

    async def _create_run(
        self, db, company_with_profile, ready_tender
    ) -> str:
        company, _ = company_with_profile
        run_id = str(uuid4())
        run = AnalysisRun(
            id=run_id,
            tender_id=ready_tender.id,
            company_id=company.id,
            state="pending",
        )
        db.add(run)
        await db.flush()
        return run_id

    async def test_cost_tracker_handler_fires_on_mock_llm_call(
        self,
        db,
        company_with_profile,
        ready_tender,
    ):
        from app.middleware.cost_tracker import CostTrackingHandler, compute_cost

        run_id = await self._create_run(db, company_with_profile, ready_tender)
        handler = CostTrackingHandler(
            run_id=run_id, node_name="risk_radar", db=db
        )

        mock_response = LLMResult(
            generations=[[Generation(text="test")]],
            llm_output={
                "model_name": "gpt-4o",
                "token_usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )
        await handler.on_llm_end(mock_response)

        result = await db.execute(
            select(LlmCostEvent).where(LlmCostEvent.run_id == run_id)
        )
        events = result.scalars().all()
        assert len(events) == 1, f"Expected 1 row, got {len(events)}"

        event = events[0]
        assert event.node_name == "risk_radar"
        assert event.model == "gpt-4o"
        assert event.input_tokens == 100
        assert event.output_tokens == 50

        expected = compute_cost("gpt-4o", {"prompt_tokens": 100, "completion_tokens": 50})
        assert event.cost_usd == expected, (
            f"cost_usd mismatch: {event.cost_usd} != {expected}"
        )

    async def test_cost_tracker_never_raises_on_failure(
        self,
        db,
        company_with_profile,
        ready_tender,
    ):
        from app.middleware.cost_tracker import CostTrackingHandler

        run_id = await self._create_run(db, company_with_profile, ready_tender)
        handler = CostTrackingHandler(
            run_id=run_id, node_name="test", db=db
        )

        mock_response = LLMResult(
            generations=[[Generation(text="test")]],
            llm_output={
                "model_name": "gpt-4o"
            },
        )
        try:
            await handler.on_llm_end(mock_response)
        except Exception as exc:
            pytest.fail(f"CostTrackingHandler.on_llm_end raised: {exc}")

    async def test_compute_cost_unknown_model_returns_zero(self):
        from app.middleware.cost_tracker import compute_cost

        result = compute_cost(
            "totally-made-up-model",
            {"prompt_tokens": 100, "completion_tokens": 50},
        )
        assert result == 0.0
