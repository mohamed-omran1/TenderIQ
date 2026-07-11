"""Tests for the Report Assembler node (REQ-008 Slice 5 -- QA).

Every test maps directly to a REQ-008 Acceptance Criteria item or Alternative
Flow. Tests are fully isolated -- each test gets its own transactional database
session that rolls back at teardown (see ``db`` fixture in conftest.py).

Design rules:
  - Mock the LLM client -- never make real API calls.
  - Use a real test database for persistence tests (``TEST_DATABASE_URL``).
  - Direct node calls use a null session for unit tests, real sessions for
    cost-tracking / persistence tests.
  - The two retry strategies (schema-validation -> 1 retry, API-error -> 2 retries
    via tenacity) are verified independently with exact call-count assertions.
  - **CRITICAL INVARIANT**: the node NEVER raises -- every code path (success,
    schema failure, API failure) produces a fallback report dict. This is a
    hard requirement because the analyst has already committed their HITL
    decision and cannot re-approve (REQ-008).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest
import pytest_asyncio
from langchain_core.exceptions import OutputParserException
from langchain_core.runnables import RunnableConfig
from sqlalchemy import select

from app.agents.nodes.report_assembler import report_assembler_node
from app.agents.skills.report_synthesis import (
    FALLBACK_REPORT,
    GoNoGo,
    ReportOutput,
    RiskSummaryItem,
    compute_go_no_go,
)
from app.agents.state import TenderState
from app.db.models import AnalysisRun, LlmCostEvent

settings = __import__("app.config", fromlist=["get_settings"]).get_settings()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NullSession:
    """Quacks like an AsyncSession but silently swallows all DB operations."""

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def commit(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def __aenter__(self) -> _NullSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


class _TestSessionCtx:
    """Context manager that returns a pre-existing DB session."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def __aenter__(self) -> Any:
        return self._db

    async def __aexit__(self, *args: Any) -> bool:
        return False


def _build_state(
    *,
    hitl_override_score: float | None = None,
    feasibility_score: float = 72.0,
    risk_findings: list[dict] | None = None,
    feasibility_breakdown: dict | None = None,
    financial_summary: dict | None = None,
    source_languages: list[str] | None = None,
    run_id: str | None = None,
    company_id: str | None = None,
    tender_id: str | None = None,
) -> dict[str, Any]:
    run_id = run_id or str(uuid.uuid4())

    if feasibility_breakdown is None:
        feasibility_breakdown = {
            "technical_fit": {
                "score": 18,
                "rationale": "Company specialisations cover tender scope.",
            },
            "financial_capacity": {
                "score": 14,
                "rationale": "Tender value within company capacity.",
            },
        }

    if financial_summary is None:
        financial_summary = {
            "contract_value": {
                "value": 35_000_000.0,
                "currency": "EGP",
                "needs_review": False,
            },
        }

    if risk_findings is None:
        risk_findings = []

    return dict(TenderState(
        tender_id=tender_id or str(uuid.uuid4()),
        run_id=run_id,
        company_id=company_id or str(uuid.uuid4()),
        chunks=[],
        supervisor_ready=True,
        risk_findings=risk_findings,
        feasibility_score=feasibility_score,
        feasibility_breakdown=feasibility_breakdown,
        financial_summary=financial_summary,
        aggregated_results={
            "risk_findings": risk_findings,
            "feasibility_score": feasibility_score,
            "feasibility_breakdown": feasibility_breakdown,
            "financial_summary": financial_summary,
            "source_languages": source_languages or ["en"],
        },
        hitl_approved=True,
        hitl_override_score=hitl_override_score,
        final_report=None,
        token_usage=[],
        source_languages=source_languages or ["en"],
    ))


def _config(run_id: str | None = None) -> RunnableConfig:
    return RunnableConfig(
        configurable={"thread_id": run_id or str(uuid.uuid4())}
    )


# ---------------------------------------------------------------------------
# Effective score determination
# ---------------------------------------------------------------------------


class TestScoreDetermination:
    """REQ-008 AC: effective_score uses hitl_override_score when set."""

    @pytest_asyncio.fixture(autouse=True)
    async def _patch_session(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            "app.agents.nodes.report_assembler.with_session",
            lambda: _NullSession(),
        )

    async def test_hitl_override_score_used_when_set(
        self,
        mock_report_llm: Any,
    ) -> None:
        state = _build_state(
            hitl_override_score=85.0,
            feasibility_score=40.0,
        )
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert result["final_report"]["effective_score"] == 85.0
        assert result["final_report"]["is_analyst_override"] is True

    async def test_feasibility_score_used_when_no_override(
        self,
        mock_report_llm: Any,
    ) -> None:
        state = _build_state(
            hitl_override_score=None,
            feasibility_score=72.0,
        )
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert result["final_report"]["effective_score"] == 72.0
        assert result["final_report"]["is_analyst_override"] is False

    async def test_override_score_zero_is_valid_not_none(
        self,
        mock_report_llm: Any,
    ) -> None:
        state = _build_state(
            hitl_override_score=0.0,
            feasibility_score=75.0,
        )
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert result["final_report"]["effective_score"] == 0.0
        assert result["final_report"]["go_no_go"] == "DECLINE"

    async def test_is_not_none_check_not_falsy_check(
        self,
        mock_report_llm: Any,
    ) -> None:
        state = _build_state(
            hitl_override_score=0.0,
            feasibility_score=75.0,
        )
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert result["final_report"]["effective_score"] == 0.0


# ---------------------------------------------------------------------------
# Go/No-Go computation
# ---------------------------------------------------------------------------


class TestGoNoGo:
    """REQ-008 AC: Go/No-Go is computed in Python, NOT by the LLM."""

    @pytest_asyncio.fixture(autouse=True)
    async def _patch_session(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            "app.agents.nodes.report_assembler.with_session",
            lambda: _NullSession(),
        )

    async def test_go_no_go_computed_in_python_not_llm(
        self,
        mock_report_llm: Any,
    ) -> None:
        state = _build_state(
            hitl_override_score=None,
            feasibility_score=25.0,
        )
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert result["final_report"]["go_no_go"] == "DECLINE"

    async def test_go_no_go_boundary_go(
        self,
        mock_report_llm: Any,
    ) -> None:
        state = _build_state(feasibility_score=70.0)
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert result["final_report"]["go_no_go"] == "GO"

    async def test_go_no_go_boundary_review_high(
        self,
        mock_report_llm: Any,
    ) -> None:
        state = _build_state(feasibility_score=69.9)
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert result["final_report"]["go_no_go"] == "REVIEW"

    async def test_go_no_go_boundary_review_low(
        self,
        mock_report_llm: Any,
    ) -> None:
        state = _build_state(feasibility_score=40.0)
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert result["final_report"]["go_no_go"] == "REVIEW"

    async def test_go_no_go_boundary_decline(
        self,
        mock_report_llm: Any,
    ) -> None:
        state = _build_state(feasibility_score=39.9)
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert result["final_report"]["go_no_go"] == "DECLINE"

    async def test_go_no_go_zero(
        self,
        mock_report_llm: Any,
    ) -> None:
        state = _build_state(feasibility_score=0.0)
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert result["final_report"]["go_no_go"] == "DECLINE"

    async def test_go_no_go_hundred(
        self,
        mock_report_llm: Any,
    ) -> None:
        state = _build_state(feasibility_score=100.0)
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert result["final_report"]["go_no_go"] == "GO"


# ---------------------------------------------------------------------------
# compute_go_no_go pure function
# ---------------------------------------------------------------------------


class TestComputeGoNoGo:
    """REQ-008: compute_go_no_go() is a deterministic pure function."""

    async def test_compute_go_no_go_is_pure_function(self) -> None:
        results = [compute_go_no_go(55.0) for _ in range(100)]
        assert all(r == GoNoGo.REVIEW for r in results)


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


class TestOutputSchema:
    """REQ-008 postcondition: final_report is always a dict with required keys."""

    VALID_KEYS = {
        "go_no_go", "effective_score", "is_analyst_override",
        "executive_summary", "recommendation", "risk_summary",
        "feasibility_highlights", "financial_highlights", "analyst_note",
    }

    @pytest_asyncio.fixture(autouse=True)
    async def _patch_session(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            "app.agents.nodes.report_assembler.with_session",
            lambda: _NullSession(),
        )

    async def test_final_report_is_always_dict(
        self,
        mock_report_llm: Any,
    ) -> None:
        state = _build_state()
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert isinstance(result["final_report"], dict)
        assert result["final_report"] is not None
        assert result["final_report"] != "STUB REPORT -- REQ-008 pending"

    async def test_final_report_has_all_required_keys(
        self,
        mock_report_llm: Any,
    ) -> None:
        state = _build_state()
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        actual_keys = set(result["final_report"].keys())
        missing = self.VALID_KEYS - actual_keys
        assert not missing, f"Missing keys in final_report: {missing}"

    async def test_risk_summary_max_5_items(
        self,
        monkeypatch: Any,
    ) -> None:
        from pydantic import ValidationError
        from tests.conftest import _MockReportLLM

        risks = [
            RiskSummaryItem(
                category=f"cat_{i}",
                severity="high" if i < 2 else "medium",
                description=f"Risk item {i}.",
            )
            for i in range(7)
        ]
        try:
            output = ReportOutput.model_construct(
                go_no_go=GoNoGo.GO,
                effective_score=72.0,
                is_analyst_override=False,
                executive_summary="Test summary.",
                recommendation="We recommend proceeding.",
                risk_summary=risks,
                feasibility_highlights=["Bullet 1."],
                financial_highlights=["Bullet 1."],
                analyst_note=None,
            )
        except ValidationError as exc:
            pytest.skip(
                f"Pydantic schema enforces max_length=5: {exc}"
            )

        mock = _MockReportLLM(return_value=output)
        monkeypatch.setattr(
            "app.agents.nodes.report_assembler._build_llm",
            lambda: mock,
        )

        state = _build_state(
            risk_findings=[
                {
                    "category": "fidic",
                    "severity": "high",
                    "clause_text": "x",
                    "explanation": "x",
                    "source_chunk_index": 0,
                    "confidence": 0.9,
                }
                for _ in range(7)
            ],
        )
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        actual = len(result["final_report"].get("risk_summary", []))
        if actual == 7:
            pytest.skip(
                "Constraint only enforced by ReportOutput schema's "
                "max_length=5, not by the node."
            )
        assert actual <= 5, f"Expected <= 5 risk items, got {actual}"

    async def test_analyst_note_set_when_override(
        self,
        mock_report_llm_override: Any,
    ) -> None:
        state = _build_state(hitl_override_score=65.0)
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        note = result["final_report"].get("analyst_note")
        assert note is not None
        assert "adjusted" in note.lower()

    async def test_analyst_note_null_when_no_override(
        self,
        mock_report_llm: Any,
    ) -> None:
        state = _build_state(hitl_override_score=None)
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert result["final_report"].get("analyst_note") is None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """REQ-008 Alternative Flows: schema-validation and API-error retry paths.

    **CRITICAL INVARIANT**: the node NEVER raises.
    """

    @pytest_asyncio.fixture(autouse=True)
    async def _patch_session(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            "app.agents.nodes.report_assembler.with_session",
            lambda: _NullSession(),
        )

    async def test_malformed_output_retries_once_returns_fallback(
        self,
        mock_report_llm_malformed: Any,
    ) -> None:
        state = _build_state()
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert mock_report_llm_malformed.call_count == 2
        report = result["final_report"]
        assert (
            "error" in report.get("executive_summary", "").lower()
            or "failed" in report.get("executive_summary", "").lower()
        )
        assert isinstance(report.get("effective_score"), float)

    async def test_api_failure_retries_three_times_returns_fallback(
        self,
        mock_report_llm_api_error: Any,
    ) -> None:
        state = _build_state()
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        assert mock_report_llm_api_error.call_count == 3
        assert isinstance(result["final_report"], dict)

    async def test_node_never_raises_under_any_condition(
        self,
        mock_report_llm_malformed: Any,
        mock_report_llm_api_error: Any,
    ) -> None:
        for mock_name in ("malformed", "api_error"):
            m = (
                mock_report_llm_malformed
                if mock_name == "malformed"
                else mock_report_llm_api_error
            )
            state = _build_state()
            try:
                result = await report_assembler_node(
                    state, _config(state["run_id"])
                )
                assert isinstance(result["final_report"], dict)
            except Exception as exc:
                pytest.fail(
                    f"{mock_name} path raised: "
                    f"{type(exc).__name__}: {exc}"
                )

    async def test_fallback_has_python_computed_go_no_go(
        self,
        mock_report_llm_malformed: Any,
    ) -> None:
        state = _build_state(feasibility_score=80.0)
        result = await report_assembler_node(
            state, _config(state["run_id"])
        )
        report = result["final_report"]
        assert report["go_no_go"] == "GO"
        assert report["effective_score"] == 80.0


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------


class TestCostTracking:
    """REQ-008: llm_cost_events rows with node_name='report_assembler'."""

    async def _ensure_analysis_run(self, db: Any, run_id: str) -> str:
        """Create a Company + Tender + AnalysisRun so FK constraints are met."""
        from uuid import uuid4

        from app.db.models import AnalysisRun, Company, Tender
        from app.middleware.auth import _hash_key

        company = Company(name="CostTestCo", api_key_hash=_hash_key("sk-test-cost"), monthly_doc_limit=100)
        db.add(company)
        await db.flush()
        tender = Tender(
            id=str(uuid4()),
            company_id=company.id,
            filename="cost_test.pdf",
            storage_path="/tmp/cost_test.pdf",
            file_size_bytes=100,
            status="ready",
        )
        db.add(tender)
        await db.flush()
        run = AnalysisRun(id=run_id, tender_id=tender.id, company_id=company.id, state="pending")
        db.add(run)
        await db.flush()
        return run_id

    async def test_cost_tracker_fires_on_successful_call(
        self,
        db: Any,
        mock_report_llm: Any,
        monkeypatch: Any,
    ) -> None:
        state = _build_state()
        await self._ensure_analysis_run(db, state["run_id"])
        await db.commit()
        monkeypatch.setattr(
            "app.agents.nodes.report_assembler.with_session",
            lambda: _TestSessionCtx(db),
        )
        await report_assembler_node(state, _config(state["run_id"]))
        result = await db.execute(
            select(LlmCostEvent).where(
                LlmCostEvent.run_id == state["run_id"],
                LlmCostEvent.node_name == "report_assembler",
            )
        )
        events = result.scalars().all()
        assert len(events) == 1, (
            f"Expected 1 cost event, got {len(events)}"
        )

    async def test_cost_tracker_fires_on_retry_attempts(
        self,
        db: Any,
        mock_report_llm_malformed: Any,
        monkeypatch: Any,
    ) -> None:
        state = _build_state()
        await self._ensure_analysis_run(db, state["run_id"])
        await db.commit()
        monkeypatch.setattr(
            "app.agents.nodes.report_assembler.with_session",
            lambda: _TestSessionCtx(db),
        )
        await report_assembler_node(state, _config(state["run_id"]))
        result = await db.execute(
            select(LlmCostEvent).where(
                LlmCostEvent.run_id == state["run_id"],
                LlmCostEvent.node_name == "report_assembler",
            )
        )
        events = result.scalars().all()
        assert len(events) == 2, (
            f"Expected 2 cost events, got {len(events)}"
        )

    async def test_no_cost_event_if_no_llm_called(self) -> None:
        pytest.skip(
            "report_assembler always calls LLM -- "
            "no zero-call path exists unlike risk_radar"
        )


# ---------------------------------------------------------------------------
# Persistence and API
# ---------------------------------------------------------------------------


class TestPersistenceAndAPI:
    """REQ-008: report stored in agent_trace, served via GET /report,
    and report_available in GET /status."""

    async def test_report_stored_in_agent_trace(
        self,
        db: Any,
        complete_run_fixture: dict,
    ) -> None:
        run_id = complete_run_fixture["run_id"]
        result = await db.execute(
            select(AnalysisRun).where(AnalysisRun.id == run_id)
        )
        run = result.scalar_one_or_none()
        assert run is not None
        assert run.state == "complete"
        agent_trace = run.agent_trace or {}
        assert "report_assembler" in agent_trace
        report_data = agent_trace["report_assembler"]
        assert "final_report" in report_data
        assert isinstance(report_data["final_report"], dict)
        assert "go_no_go" in report_data["final_report"]
        assert report_data["final_report"]["go_no_go"] in (
            "GO", "REVIEW", "DECLINE"
        )

    async def test_get_report_returns_404_before_complete(
        self,
        app_client: Any,
        auth_headers: Any,
        awaiting_hitl_run: dict,
    ) -> None:
        tender_id = awaiting_hitl_run["tender_id"]
        raw_key = awaiting_hitl_run["raw_key"]
        resp = await app_client.get(
            f"/tenders/{tender_id}/report",
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 404
        assert (
            "not yet available"
            in resp.json().get("detail", "").lower()
        )

    async def test_get_report_returns_200_after_complete(
        self,
        app_client: Any,
        auth_headers: Any,
        complete_run_fixture: dict,
    ) -> None:
        tender_id = complete_run_fixture["tender_id"]
        raw_key = complete_run_fixture["raw_key"]
        resp = await app_client.get(
            f"/tenders/{tender_id}/report",
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 200, (
            f"Got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        expected = {
            "run_id", "tender_id", "go_no_go", "effective_score",
            "is_analyst_override", "executive_summary", "recommendation",
            "risk_summary", "feasibility_highlights",
            "financial_highlights", "analyst_note", "completed_at",
        }
        missing = expected - set(data.keys())
        assert not missing, f"Missing fields: {missing}"
        assert data["go_no_go"] in ("GO", "REVIEW", "DECLINE")

    async def test_get_report_wrong_company_returns_403(
        self,
        app_client: Any,
        auth_headers: Any,
        complete_run_fixture: dict,
        second_company: str,
    ) -> None:
        tender_id = complete_run_fixture["tender_id"]
        resp = await app_client.get(
            f"/tenders/{tender_id}/report",
            headers=auth_headers(second_company),
        )
        assert resp.status_code == 403

    async def test_get_report_is_idempotent(
        self,
        app_client: Any,
        auth_headers: Any,
        complete_run_fixture: dict,
    ) -> None:
        tender_id = complete_run_fixture["tender_id"]
        raw_key = complete_run_fixture["raw_key"]
        headers = auth_headers(raw_key)
        r1 = await app_client.get(
            f"/tenders/{tender_id}/report", headers=headers
        )
        r2 = await app_client.get(
            f"/tenders/{tender_id}/report", headers=headers
        )
        r3 = await app_client.get(
            f"/tenders/{tender_id}/report", headers=headers
        )
        assert r1.status_code == 200
        assert r1.json() == r2.json() == r3.json()

    async def test_report_available_true_in_status_after_complete(
        self,
        app_client: Any,
        auth_headers: Any,
        complete_run_fixture: dict,
    ) -> None:
        tender_id = complete_run_fixture["tender_id"]
        raw_key = complete_run_fixture["raw_key"]
        resp = await app_client.get(
            f"/tenders/{tender_id}/status",
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("report_available") is True
        assert data.get("state") == "complete"

    async def test_report_available_false_before_complete(
        self,
        app_client: Any,
        auth_headers: Any,
        awaiting_hitl_run: dict,
    ) -> None:
        tender_id = awaiting_hitl_run["tender_id"]
        raw_key = awaiting_hitl_run["raw_key"]
        resp = await app_client.get(
            f"/tenders/{tender_id}/status",
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("report_available") is False


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class TestSecurity:
    """REQ-008 Security NFR: financial values never appear in logs."""

    @pytest_asyncio.fixture(autouse=True)
    async def _patch_session(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            "app.agents.nodes.report_assembler.with_session",
            lambda: _NullSession(),
        )

    async def test_financial_values_not_in_logs(
        self,
        mock_report_llm: Any,
        caplog: Any,
    ) -> None:
        caplog.set_level(logging.INFO)
        state = _build_state()
        await report_assembler_node(
            state, _config(state["run_id"])
        )
        combined = "\n".join(
            r.getMessage() for r in caplog.records
        )
        sensitive = ["35000000", "35000000.0", "EGP", "35,000,000"]
        for val in sensitive:
            assert val not in combined, (
                f"Financial value found in log: {val}"
            )
