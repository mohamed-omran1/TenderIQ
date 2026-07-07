"""Tests for REQ-007 — HITL Override Gate (Slice 4 — QA).

Every test case maps to one or more Acceptance Criteria from REQ-007.
Tests are fully isolated — each test gets its own transactional database
session that rolls back at teardown (see ``db`` fixture in conftest.py).

Design rules (from imp-slice-04):
- Use httpx.AsyncClient wired via ASGITransport (``app_client`` fixture).
- Use a real test database (``TEST_DATABASE_URL``), never mocks for the DB.
- Mock the report_assembler node so the graph never makes real LLM calls.
- Every poll loop has a 15-second timeout to avoid hanging.
- Every test is fully isolated — unique run_id, unique tender, clean DB.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from app.agents.graph import graph
from app.config import get_settings
from app.db.models import AnalysisRun, HITLOverride, Tender

settings = get_settings()


# ==============================================================================
# Helpers
# ==============================================================================


async def _poll_until_complete(
    app_client,
    headers: dict,
    tender_id: str,
    *,
    timeout: float = 15.0,
) -> dict:
    """Poll GET /status until state='complete'. Fail with message on timeout."""
    deadline = time.monotonic() + timeout
    last_body = {}
    while time.monotonic() < deadline:
        resp = await app_client.get(
            f"/tenders/{tender_id}/status",
            headers=headers,
        )
        if resp.status_code == 200:
            last_body = resp.json()
            if last_body["state"] == "complete":
                return last_body
        await asyncio.sleep(0.2)
    pytest.fail(
        f"Timed out ({timeout}s) waiting for state='complete'. "
        f"Last response: status={resp.status_code}, body={last_body}"
    )


async def _read_run(db, run_id: str) -> AnalysisRun:
    result = await db.execute(
        select(AnalysisRun).where(AnalysisRun.id == run_id)
    )
    run = result.scalar_one_or_none()
    assert run is not None, f"AnalysisRun {run_id} not found"
    return run


def _auth(run_data: dict) -> dict:
    """Build auth headers from awaiting_hitl_run fixture data."""
    return {"Authorization": f"Bearer {run_data['raw_key']}"}


# ==============================================================================
# Flow A — Approve as-is
# ==============================================================================


class TestApproveFlow:
    """REQ-007 Main Flow A — approve the AI score without modification.

    Maps to AC1, AC7, AC8, AC9.
    """

    async def test_approve_returns_202_with_hitl_response(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        run_id = awaiting_hitl_run["run_id"]

        run = await _read_run(db, run_id)
        original_score = run.feasibility_score
        assert original_score is not None

        resp = await app_client.post(
            f"/tenders/{tid}/approve",
            headers=headers,
            json={"justification": "Approved as-is."},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["run_id"] == run_id
        assert body["action"] == "approved"
        assert body["original_score"] == original_score
        assert body["overridden_score"] is None
        assert "message" in body

    async def test_approve_creates_immutable_hitl_overrides_row(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        run_id = awaiting_hitl_run["run_id"]

        run = await _read_run(db, run_id)
        original_score = run.feasibility_score

        resp = await app_client.post(
            f"/tenders/{tid}/approve",
            headers=headers,
            json={"justification": "Approved as-is."},
        )
        assert resp.status_code == 202, resp.text

        result = await db.execute(
            select(HITLOverride).where(HITLOverride.run_id == run_id)
        )
        rows = result.scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.action == "approved"
        assert row.original_score == original_score
        assert row.overridden_score is None
        assert row.created_at is not None

    async def test_approve_transitions_run_to_complete(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
        graph_session,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        run_id = awaiting_hitl_run["run_id"]

        resp = await app_client.post(
            f"/tenders/{tid}/approve",
            headers=headers,
            json={"justification": "Approved."},
        )
        assert resp.status_code == 202, resp.text

        body = await _poll_until_complete(app_client, headers, tid)
        assert body["state"] == "complete"

        run = await _read_run(db, run_id)
        assert run.completed_at is not None

    async def test_approve_report_assembler_ran(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
        graph_session,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        run_id = awaiting_hitl_run["run_id"]

        resp = await app_client.post(
            f"/tenders/{tid}/approve",
            headers=headers,
            json={},
        )
        assert resp.status_code == 202, resp.text

        await _poll_until_complete(app_client, headers, tid)

        run = await _read_run(db, run_id)
        trace = run.agent_trace or {}
        assert "report_assembler" in trace, (
            f"agent_trace missing 'report_assembler'. "
            f"Got keys: {list(trace)}"
        )

    async def test_hitl_approved_injected_into_checkpoint(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        run_id = awaiting_hitl_run["run_id"]

        resp = await app_client.post(
            f"/tenders/{tid}/approve",
            headers=headers,
            json={},
        )
        assert resp.status_code == 202, resp.text

        config = {"configurable": {"thread_id": str(run_id)}}
        state = await graph.aget_state(config)
        assert state.values.get("hitl_approved") is True, (
            f"Expected hitl_approved=True, got {state.values.get('hitl_approved')}"
        )
        assert state.values.get("hitl_override_score") is None


# ==============================================================================
# Flow B — Override score
# ==============================================================================


class TestOverrideFlow:
    """REQ-007 Main Flow B — override the AI score with an adjusted value.

    Maps to AC2, AC3, AC8.
    """

    async def test_override_returns_202_with_correct_scores(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        run_id = awaiting_hitl_run["run_id"]

        run = await _read_run(db, run_id)
        original_score = run.feasibility_score
        assert original_score is not None

        resp = await app_client.post(
            f"/tenders/{tid}/override",
            headers=headers,
            json={
                "overridden_score": 85.0,
                "justification": "Adjusted based on analyst review of company capacity.",
            },
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["action"] == "overridden"
        assert body["original_score"] == original_score
        assert body["overridden_score"] == 85.0

    async def test_override_creates_hitl_overrides_row_with_scores(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        run_id = awaiting_hitl_run["run_id"]

        run = await _read_run(db, run_id)
        original_score = run.feasibility_score
        assert original_score is not None

        resp = await app_client.post(
            f"/tenders/{tid}/override",
            headers=headers,
            json={
                "overridden_score": 30.0,
                "justification": "Score overridden due to capacity concerns.",
            },
        )
        assert resp.status_code == 202, resp.text

        result = await db.execute(
            select(HITLOverride).where(HITLOverride.run_id == run_id)
        )
        rows = result.scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.action == "overridden"
        assert row.overridden_score == 30.0
        assert row.original_score != 30.0, (
            "original_score should differ from overridden_score"
        )
        assert row.justification is not None

    async def test_override_injects_score_into_checkpoint(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        run_id = awaiting_hitl_run["run_id"]

        resp = await app_client.post(
            f"/tenders/{tid}/override",
            headers=headers,
            json={
                "overridden_score": 72.5,
                "justification": "Override with verified financial data.",
            },
        )
        assert resp.status_code == 202, resp.text

        config = {"configurable": {"thread_id": str(run_id)}}
        state = await graph.aget_state(config)
        assert state.values.get("hitl_override_score") == 72.5, (
            f"Expected hitl_override_score=72.5, "
            f"got {state.values.get('hitl_override_score')}"
        )
        assert state.values.get("hitl_approved") is True, (
            "Expected hitl_approved=True"
        )

    async def test_override_score_used_in_final_report(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
        graph_session,
    ):
        """Documents the CONTRACT: hitl_override_score must be in the checkpoint
        when report_assembler runs. Full validation of report content is REQ-008."""
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        run_id = awaiting_hitl_run["run_id"]

        resp = await app_client.post(
            f"/tenders/{tid}/override",
            headers=headers,
            json={
                "overridden_score": 90.0,
                "justification": "Adjusted score per in-depth financial review.",
            },
        )
        assert resp.status_code == 202, resp.text

        await _poll_until_complete(app_client, headers, tid)

        run = await _read_run(db, run_id)
        trace = run.agent_trace or {}
        assert "report_assembler" in trace, (
            "report_assembler did not run — the checkpoint may not have been "
            "consumed correctly"
        )

        config = {"configurable": {"thread_id": str(run_id)}}
        state = await graph.aget_state(config)
        assert state.values.get("hitl_override_score") == 90.0, (
            "hitl_override_score=90.0 must be present in checkpoint when "
            "report_assembler runs (REQ-008 contract)"
        )


# ==============================================================================
# Boundary values
# ==============================================================================


class TestOverrideBoundaries:
    """REQ-007 Alternative Flow — overridden_score validation.

    Maps to AC5 (score range), AC6 (justification required).
    """

    async def test_override_minimum_score_zero(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        run_id = awaiting_hitl_run["run_id"]

        resp = await app_client.post(
            f"/tenders/{tid}/override",
            headers=headers,
            json={
                "overridden_score": 0.0,
                "justification": "Minimum valid score test.",
            },
        )
        assert resp.status_code == 202, resp.text

        result = await db.execute(
            select(HITLOverride.overridden_score).where(
                HITLOverride.run_id == run_id
            )
        )
        val = result.scalar_one_or_none()
        assert val == 0.0

    async def test_override_maximum_score_hundred(
        self,
        app_client,
        awaiting_hitl_run: dict,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)

        resp = await app_client.post(
            f"/tenders/{tid}/override",
            headers=headers,
            json={
                "overridden_score": 100.0,
                "justification": "Maximum valid score test.",
            },
        )
        assert resp.status_code == 202, resp.text

    async def test_override_score_below_zero_returns_422(
        self,
        app_client,
        awaiting_hitl_run: dict,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)

        resp = await app_client.post(
            f"/tenders/{tid}/override",
            headers=headers,
            json={
                "overridden_score": -1.0,
                "justification": "Below minimum score test.",
            },
        )
        assert resp.status_code == 422, resp.text

    async def test_override_score_above_hundred_returns_422(
        self,
        app_client,
        awaiting_hitl_run: dict,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)

        resp = await app_client.post(
            f"/tenders/{tid}/override",
            headers=headers,
            json={
                "overridden_score": 100.1,
                "justification": "Above maximum score test.",
            },
        )
        assert resp.status_code == 422, resp.text

    async def test_override_justification_too_short_returns_422(
        self,
        app_client,
        awaiting_hitl_run: dict,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)

        resp = await app_client.post(
            f"/tenders/{tid}/override",
            headers=headers,
            json={
                "overridden_score": 50.0,
                "justification": "short",
            },
        )
        assert resp.status_code == 422, resp.text

    async def test_override_missing_justification_returns_422(
        self,
        app_client,
        awaiting_hitl_run: dict,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)

        resp = await app_client.post(
            f"/tenders/{tid}/override",
            headers=headers,
            json={
                "overridden_score": 50.0,
            },
        )
        assert resp.status_code == 422, resp.text


# ==============================================================================
# Authorisation
# ==============================================================================


class TestAuthorization:
    """REQ-007 Alternative Flow — tenant isolation and resource existence.

    Maps to AC7 (cross-company 403), AC4 (not-found 404).
    """

    async def test_approve_wrong_company_returns_403(
        self,
        app_client,
        awaiting_hitl_run: dict,
        second_company: str,
    ):
        tid = awaiting_hitl_run["tender_id"]

        resp = await app_client.post(
            f"/tenders/{tid}/approve",
            headers={"Authorization": f"Bearer {second_company}"},
            json={"justification": "Wrong company attempt."},
        )
        assert resp.status_code == 403, resp.text

    async def test_override_wrong_company_returns_403(
        self,
        app_client,
        awaiting_hitl_run: dict,
        second_company: str,
    ):
        tid = awaiting_hitl_run["tender_id"]

        resp = await app_client.post(
            f"/tenders/{tid}/override",
            headers={"Authorization": f"Bearer {second_company}"},
            json={
                "overridden_score": 50.0,
                "justification": "Wrong company override attempt.",
            },
        )
        assert resp.status_code == 403, resp.text

    async def test_approve_nonexistent_tender_returns_404(
        self,
        app_client,
        awaiting_hitl_run: dict,
    ):
        headers = _auth(awaiting_hitl_run)
        fake_id = str(uuid4())

        resp = await app_client.post(
            f"/tenders/{fake_id}/approve",
            headers=headers,
            json={},
        )
        assert resp.status_code == 404, resp.text


# ==============================================================================
# State validation
# ==============================================================================


class TestStateValidation:
    """REQ-007 Alternative Flow — state-based error responses.

    Maps to AC4 (non-awaiting_hitl → 409), AC9 (duplicate approve → 409).
    """

    async def test_approve_run_not_in_awaiting_hitl_returns_409(
        self,
        app_client,
        db,
        company_with_profile,
        auth_headers,
        graph_session,
        profile_lookup_session,
    ):
        """Create a run in 'running' state and attempt to approve it."""
        company, raw_key = company_with_profile
        tender = Tender(
            id=str(uuid4()),
            company_id=company.id,
            filename="running_test.pdf",
            storage_path="/tmp/running_test.pdf",
            file_size_bytes=100,
            status="ready",
        )
        db.add(tender)
        run = AnalysisRun(
            id=str(uuid4()),
            tender_id=tender.id,
            company_id=company.id,
            state="running",
            feasibility_score=65.0,
        )
        db.add(run)
        await db.flush()

        resp = await app_client.post(
            f"/tenders/{tender.id}/approve",
            headers=auth_headers(raw_key),
            json={},
        )
        assert resp.status_code == 409, resp.text
        assert "awaiting review" in resp.json()["detail"].lower()

    async def test_approve_already_complete_run_returns_409(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
        graph_session,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)

        resp = await app_client.post(
            f"/tenders/{tid}/approve",
            headers=headers,
            json={},
        )
        assert resp.status_code == 202, resp.text
        await _poll_until_complete(app_client, headers, tid)

        resp = await app_client.post(
            f"/tenders/{tid}/approve",
            headers=headers,
            json={},
        )
        assert resp.status_code == 409, resp.text
        assert "not awaiting review" in resp.json()["detail"].lower()

    async def test_double_approve_race_condition(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
    ):
        """Fire two POST /approve requests concurrently.

        The "resuming" intermediate state prevents double-resume:
        exactly one should get 202 and the other 409.
        """
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        run_id = awaiting_hitl_run["run_id"]

        async def _approve() -> tuple[int, str]:
            resp = await app_client.post(
                f"/tenders/{tid}/approve",
                headers=headers,
                json={},
            )
            return resp.status_code, resp.text

        results = await asyncio.gather(_approve(), _approve(), return_exceptions=True)

        statuses: list[int] = []
        for r in results:
            if isinstance(r, BaseException):
                statuses.append(500)
            elif isinstance(r, tuple):
                statuses.append(r[0])
            else:
                statuses.append(500)

        assert statuses.count(202) == 1, (
            f"Expected exactly one 202, got {statuses}"
        )
        other = [s for s in statuses if s != 202]
        assert any(s in (409, 500) for s in other), (
            f"Expected 409 (or 500) for the second request, got {other}"
        )

        overrides = await db.execute(
            select(HITLOverride).where(HITLOverride.run_id == run_id)
        )
        rows = overrides.scalars().all()
        assert len(rows) == 1, (
            f"Expected exactly one hitl_overrides row, got {len(rows)}"
        )


# ==============================================================================
# Audit log immutability
# ==============================================================================


class TestAuditLog:
    """REQ-007 Audit Integrity NFR — immutable audit trail.

    Maps to AC9 (no update path), AC10 (preserved on resume failure).
    """

    async def test_hitl_overrides_row_cannot_be_updated(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
    ):
        """Verify the application layer has no code path that UPDATEs
        hitl_overrides. This is a documentation constraint test — the DB
        does not enforce an UPDATE trigger."""
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        run_id = awaiting_hitl_run["run_id"]

        await app_client.post(
            f"/tenders/{tid}/approve",
            headers=headers,
            json={},
        )

        from pathlib import Path
        router_path = Path(__file__).parent.parent / "app" / "routers" / "tenders.py"
        source = router_path.read_text()
        import re
        matches = list(re.finditer(r"update\(.*HITLOverride", source))
        assert len(matches) == 0, (
            f"Found {len(matches)} update() call(s) on HITLOverride "
            f"in routers/tenders.py: line(s) "
            f"{[source[:m.start()].count(chr(10)) + 1 for m in matches]}"
        )

    async def test_hitl_overrides_row_preserved_on_resume_failure(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        monkeypatch,
        graph_session,
    ):
        """Verify the hitl_overrides audit log is preserved even if the graph
        resume fails after the HITL decision was committed."""
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        run_id = awaiting_hitl_run["run_id"]

        from app.routers import tenders as tenders_router

        async def _failing_resume(run_id_arg: str, override_score: float | None) -> None:
            async with tenders_router.with_session() as session:
                await session.execute(
                    update(AnalysisRun)
                    .where(AnalysisRun.id == run_id_arg)
                    .values(
                        state="failed",
                        error_reason="Resume failed: Simulated resume failure for testing",
                    )
                )
                await session.commit()

        monkeypatch.setattr(
            "app.routers.tenders._resume_graph",
            _failing_resume,
        )

        resp = await app_client.post(
            f"/tenders/{tid}/approve",
            headers=headers,
            json={"justification": "Approve before resume failure."},
        )
        assert resp.status_code == 202, resp.text

        for _ in range(50):
            status = await app_client.get(
                f"/tenders/{tid}/status",
                headers=headers,
            )
            body = status.json()
            if body["state"] == "failed":
                break
            await asyncio.sleep(0.2)
        else:
            pytest.fail(
                f"Timed out waiting for state='failed'. "
                f"Last body: {body}"
            )

        overrides = await db.execute(
            select(HITLOverride).where(HITLOverride.run_id == run_id)
        )
        rows = overrides.scalars().all()
        assert len(rows) == 1, (
            "Hitl_overrides row should be preserved on resume failure"
        )
        assert rows[0].action == "approved"


# ==============================================================================
# GET /hitl-override endpoint
# ==============================================================================


class TestGetHITLOverride:
    """REQ-007 — GET /tenders/{id}/hitl-override endpoint.

    Maps to AC1 (returns record after approve), security NFR (no justification).
    """

    async def test_get_hitl_override_returns_record_after_approve(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        run_id = awaiting_hitl_run["run_id"]

        await app_client.post(
            f"/tenders/{tid}/approve",
            headers=headers,
            json={},
        )

        resp = await app_client.get(
            f"/tenders/{tid}/hitl-override",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["run_id"] == run_id
        assert body["action"] == "approved"
        assert isinstance(body["original_score"], float)
        assert body["overridden_score"] is None
        assert "created_at" in body
        assert body.get("justification") is None or "justification" not in body, (
            "justification must never be in the API response"
        )

    async def test_get_hitl_override_returns_404_before_hitl(
        self,
        app_client,
        awaiting_hitl_run: dict,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)

        resp = await app_client.get(
            f"/tenders/{tid}/hitl-override",
            headers=headers,
        )
        assert resp.status_code == 404, resp.text

    async def test_get_hitl_override_wrong_company_returns_403(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
        second_company: str,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)

        await app_client.post(
            f"/tenders/{tid}/approve",
            headers=headers,
            json={},
        )

        resp = await app_client.get(
            f"/tenders/{tid}/hitl-override",
            headers={"Authorization": f"Bearer {second_company}"},
        )
        assert resp.status_code == 403, resp.text


# ==============================================================================
# Security
# ==============================================================================


class TestSecurity:
    """REQ-007 Security NFR — justification must never leak via API or logs."""

    async def test_justification_never_in_api_response(
        self,
        app_client,
        db,
        awaiting_hitl_run: dict,
        mock_report_assembler,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        secret = "This is secret internal justification text"

        resp = await app_client.post(
            f"/tenders/{tid}/override",
            headers=headers,
            json={
                "overridden_score": 75.0,
                "justification": secret,
            },
        )
        assert resp.status_code == 202, resp.text

        override_resp = await app_client.get(
            f"/tenders/{tid}/hitl-override",
            headers=headers,
        )
        assert override_resp.status_code == 200, override_resp.text
        body_str = override_resp.text
        assert secret not in body_str, (
            "Justification text leaked into GET /hitl-override response"
        )
        body = override_resp.json()
        assert body.get("justification") is None or "justification" not in body, (
            "justification field must be absent or null"
        )

    async def test_justification_not_in_logs(
        self,
        app_client,
        awaiting_hitl_run: dict,
        mock_report_assembler,
        caplog,
    ):
        tid = awaiting_hitl_run["tender_id"]
        headers = _auth(awaiting_hitl_run)
        secret = "This is secret internal justification text"

        caplog.set_level(logging.INFO)
        caplog.clear()

        resp = await app_client.post(
            f"/tenders/{tid}/override",
            headers=headers,
            json={
                "overridden_score": 75.0,
                "justification": secret,
            },
        )
        assert resp.status_code == 202, resp.text

        for record in caplog.records:
            if secret in record.getMessage():
                pytest.fail(
                    f"Justification text leaked into log: {record.getMessage()}"
                )
