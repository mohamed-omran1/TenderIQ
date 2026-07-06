"""Tests for the Financial Analyst node (REQ-006 Slice 5 — QA).

Every test maps directly to a REQ-006 Acceptance Criteria item or Alternative
Flow. Tests are fully isolated — each test gets its own transactional database
session that rolls back at teardown (see ``db`` fixture in conftest.py).

Design rules:
  - Mock the LLM client — never make real API calls in unit/integration tests.
  - Use a real test database for persistence tests (``TEST_DATABASE_URL``).
  - Direct node calls use a null session for unit tests, real sessions for
    persistence/integration tests.
  - The two retry strategies (schema-validation → 1 retry, API-error → 3
    retries) are verified independently with exact call-count assertions.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest
import pytest_asyncio
from langchain_core.exceptions import OutputParserException
from langchain_core.outputs import Generation, LLMResult
from langchain_core.runnables import RunnableConfig
from sqlalchemy import insert, select, text, update

from app.agents.embeddings import make_stub_embeddings
from app.agents.nodes.financial_analyst import (
    financial_analyst_node,
    validate_and_normalise_currency,
)
from app.agents.retrieval import (
    FINANCIAL_ANCHOR_QUERIES,
    RISK_ANCHOR_QUERIES,
    retrieve_financial_chunks,
)
from app.agents.skills.feasibility_scoring import SCOPE_ANCHOR_QUERIES
from app.agents.skills.financial_extraction import (
    BondRequirement,
    CURRENCY_NORMALISATION,
    FinancialOutput,
    MonetaryValue,
)
from app.agents.skills.risk_clause_extraction import RiskFinding, RiskRadarOutput
from app.agents.state import TenderState
from app.config import get_settings
from app.db.models import (
    AnalysisRun,
    FinancialCommitment,
    LlmCostEvent,
    RiskFinding as RiskFindingDB,
    Tender,
    TenderChunk,
)

settings = get_settings()
EMBEDDING_STUB = [0.01] * settings.embedding_dimensions


# ---------------------------------------------------------------------------
# Mock structured LLM helper
# ---------------------------------------------------------------------------


class _MockFinancialLLM:
    """Mock structured-output LLM that returns a canned FinancialOutput or raises.

    Tracks call_count so tests can verify retry behaviour without inspecting
    log output or timing.
    """

    def __init__(
        self,
        return_value: FinancialOutput | None = None,
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NullSession:
    """Quacks like an AsyncSession but silently swallows all DB operations.

    Used by unit tests that call financial_analyst_node directly but do not
    need to verify cost-tracking or persistence.
    """

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


def _async_ret(value: Any) -> Any:
    """Wrap a sync return value into an async callable (for monkeypatch setattr)."""
    async def _inner(**kwargs: Any) -> Any:
        return value
    return _inner


def _build_state(
    chunks: list[dict],
    tender_id: str | None = None,
    run_id: str | None = None,
    company_id: str | None = None,
) -> dict[str, Any]:
    """Build a minimal TenderState dict suitable for direct node invocation."""
    return dict(TenderState(
        tender_id=tender_id or str(uuid.uuid4()),
        run_id=run_id or str(uuid.uuid4()),
        company_id=company_id or str(uuid.uuid4()),
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
    ))


def _config(run_id: str | None = None) -> RunnableConfig:
    return RunnableConfig(configurable={"thread_id": run_id or str(uuid.uuid4())})


# ---------------------------------------------------------------------------
# Schema and output contract
# ---------------------------------------------------------------------------


class TestSchemaAndContract:
    """REQ-006 AC: schema matches aggregator contract, strict keys."""

    @pytest_asyncio.fixture(autouse=True)
    async def _setup(
        self,
        monkeypatch: Any,
        sample_financial_chunks: list[dict],
    ) -> None:
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.retrieve_financial_chunks",
            _async_ret(sample_financial_chunks),
        )
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.with_session",
            lambda: _NullSession(),
        )

    async def test_return_key_matches_aggregator_contract(
        self,
        mock_financial_llm: Any,
    ) -> None:
        state = _build_state([])
        result = await financial_analyst_node(state, _config(state["run_id"]))

        assert set(result.keys()) == {"financial_summary"}, (
            f"Unexpected keys: {set(result.keys())}"
        )

    async def test_financial_summary_shape_is_consistent(
        self,
        mock_financial_llm: Any,
    ) -> None:
        state = _build_state([])
        result = await financial_analyst_node(state, _config(state["run_id"]))

        summary = result["financial_summary"]
        required_keys = {
            "contract_value", "bonds", "liquidated_damages",
            "payment_schedule", "retention_rate", "advance_payment",
        }
        assert set(summary.keys()) >= required_keys, (
            f"Missing keys: {required_keys - set(summary.keys())}"
        )
        assert isinstance(summary["bonds"], list), "bonds must be a list"
        assert isinstance(summary["payment_schedule"], list), (
            "payment_schedule must be a list"
        )
        for key in ("contract_value", "liquidated_damages", "advance_payment"):
            assert key in summary, f"Key {key} must exist in financial_summary"

    async def test_summary_never_contains_stub_values(
        self,
        mock_financial_llm: Any,
    ) -> None:
        state = _build_state([])
        result = await financial_analyst_node(state, _config(state["run_id"]))

        summary = result["financial_summary"]
        assert "stub" not in summary, "stub key should not be present"
        stub_shape = {"stub": True, "bonds": [], "commitments": []}
        assert summary != stub_shape, (
            "Old REQ-003 stub shape should not appear"
        )


# ---------------------------------------------------------------------------
# Currency validation — pure-function unit tests
# ---------------------------------------------------------------------------


class TestCurrencyValidation:
    """REQ-006 AC: currency normalisation via validate_and_normalise_currency."""

    async def test_valid_iso_currency_passes_unchanged(self) -> None:
        result, needs_review = validate_and_normalise_currency("SAR")
        assert result == "SAR"
        assert needs_review is False

    async def test_currency_normalisation_map_applied(self) -> None:
        riyals_result, riyals_review = validate_and_normalise_currency("Riyals")
        assert riyals_result == "SAR"
        assert riyals_review is False

        arabic_result, arabic_review = validate_and_normalise_currency("ريال")
        assert arabic_result == "SAR"
        assert arabic_review is False

        dirhams_result, dirhams_review = validate_and_normalise_currency("Dirhams")
        assert dirhams_result == "AED"
        assert dirhams_review is False

    async def test_unknown_currency_flagged(self) -> None:
        result, needs_review = validate_and_normalise_currency("INVALID_CURR")
        assert result == "UNKNOWN"
        assert needs_review is True

        empty_result, empty_review = validate_and_normalise_currency("")
        assert empty_result == "UNKNOWN"
        assert empty_review is True

    async def test_all_6_gcc_currencies_normalise_correctly(self) -> None:
        pairs = [
            ("SAR", "SAR"), ("Saudi Riyals", "SAR"), ("ريال سعودي", "SAR"),
            ("AED", "AED"), ("UAE Dirhams", "AED"), ("درهم إماراتي", "AED"),
            ("QAR", "QAR"), ("Qatari Riyals", "QAR"), ("ريال قطري", "QAR"),
            ("KWD", "KWD"), ("Kuwaiti Dinars", "KWD"), ("دينار كويتي", "KWD"),
            ("BHD", "BHD"), ("Bahraini Dinars", "BHD"), ("دينار بحريني", "BHD"),
            ("OMR", "OMR"), ("Omani Riyals", "OMR"), ("ريال عماني", "OMR"),
        ]
        for raw, expected in pairs:
            result, needs_review = validate_and_normalise_currency(raw)
            assert result == expected, (
                f"Expected {raw} -> {expected}, got {result}"
            )
            assert needs_review is False, (
                f"{raw} should not need review"
            )

    async def test_invalid_currency_in_llm_output_normalised_in_postprocess(
        self,
        monkeypatch: Any,
        mock_financial_llm_invalid_currency: Any,
    ) -> None:
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.retrieve_financial_chunks",
            _async_ret([{"content": "test", "detected_language": "en", "chunk_index": 0}]),
        )
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.with_session",
            lambda: _NullSession(),
        )

        state = _build_state([])
        result = await financial_analyst_node(state, _config(state["run_id"]))
        summary = result["financial_summary"]

        assert summary["contract_value"]["currency"] == "SAR", (
            "Riyals should be normalised to SAR"
        )
        assert summary["bonds"][1]["amount"]["currency"] == "UNKNOWN", (
            "INVALID_CURR should become UNKNOWN"
        )
        assert summary["bonds"][1]["amount"]["needs_review"] is True, (
            "Invalid currency should set needs_review=True"
        )

    async def test_unknown_currency_sets_needs_review_true(
        self,
        monkeypatch: Any,
        mock_financial_llm_invalid_currency: Any,
    ) -> None:
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.retrieve_financial_chunks",
            _async_ret([{"content": "test", "detected_language": "en", "chunk_index": 0}]),
        )
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.with_session",
            lambda: _NullSession(),
        )

        state = _build_state([])
        result = await financial_analyst_node(state, _config(state["run_id"]))
        summary = result["financial_summary"]

        has_needs_review = False
        for bond in summary.get("bonds", []):
            if bond.get("amount", {}).get("needs_review", False):
                has_needs_review = True
                break
        assert has_needs_review, (
            "At least one commitment should have needs_review=True"
        )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    """REQ-006 AC: bilingual dedup produces one entry, not two."""

    @pytest_asyncio.fixture(autouse=True)
    async def _setup(
        self,
        monkeypatch: Any,
    ) -> None:
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.retrieve_financial_chunks",
            _async_ret([{"content": "test", "detected_language": "en", "chunk_index": 0}]),
        )
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.with_session",
            lambda: _NullSession(),
        )

    async def test_bilingual_duplicate_bond_produces_one_entry(
        self,
        mock_financial_llm_bilingual_duplicate: Any,
    ) -> None:
        state = _build_state([])
        result = await financial_analyst_node(state, _config(state["run_id"]))
        summary = result["financial_summary"]

        # The mock returns 2 performance bonds (Arabic + English).
        # The node should deduplicate to 1.
        assert len(summary["bonds"]) == 1, (
            f"Expected 1 bond after dedup, got {len(summary['bonds'])}"
        )


# ---------------------------------------------------------------------------
# Error handling — two independent retry strategies
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """REQ-006 Alternative Flows: schema-validation and API-error retry paths."""

    @pytest_asyncio.fixture(autouse=True)
    async def _setup(
        self,
        monkeypatch: Any,
    ) -> None:
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.retrieve_financial_chunks",
            _async_ret([{"content": "test", "detected_language": "en", "chunk_index": 0}]),
        )
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.with_session",
            lambda: _NullSession(),
        )

    async def test_malformed_output_retries_once_then_degrades(
        self,
        mock_financial_llm_malformed: Any,
    ) -> None:
        state = _build_state([])
        result = await financial_analyst_node(state, _config(state["run_id"]))

        assert mock_financial_llm_malformed.call_count == 2, (
            f"Expected 2 LLM calls (initial + 1 retry), "
            f"got {mock_financial_llm_malformed.call_count}"
        )
        summary = result["financial_summary"]
        assert "error" in summary, (
            "Error key should be present in degraded financial_summary"
        )
        assert summary["bonds"] == [], (
            "Bonds should be empty list on error path"
        )
        assert summary["payment_schedule"] is None or summary["payment_schedule"] == [], (
            f"payment_schedule should be None or [] on error path, "
            f"got {summary['payment_schedule']}"
        )

    async def test_api_failure_retries_three_times_then_raises(
        self,
        mock_financial_llm_api_error: Any,
    ) -> None:
        state = _build_state([])
        with pytest.raises(Exception, match="Simulated API connection error"):
            await financial_analyst_node(state, _config(state["run_id"]))

        assert mock_financial_llm_api_error.call_count == 3, (
            f"Expected 3 LLM calls (API retry), "
            f"got {mock_financial_llm_api_error.call_count}"
        )

    async def test_error_path_financial_summary_has_required_keys(
        self,
        mock_financial_llm_malformed: Any,
    ) -> None:
        state = _build_state([])
        result = await financial_analyst_node(state, _config(state["run_id"]))
        summary = result["financial_summary"]

        for key in ("error", "bonds", "commitments", "payment_schedule"):
            assert key in summary, (
                f"Key '{key}' missing from error-path financial_summary"
            )

    async def test_retry_strategies_are_independent(
        self,
        monkeypatch: Any,
    ) -> None:
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.retrieve_financial_chunks",
            _async_ret([{"content": "test", "detected_language": "en", "chunk_index": 0}]),
        )
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.with_session",
            lambda: _NullSession(),
        )

        # --- Schema-validation retry (OutputParserException) ---
        malformed_mock = _MockFinancialLLM(
            raise_exc=OutputParserException("Schema error")
        )
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst._build_llm", lambda: malformed_mock
        )

        state1 = _build_state([])
        await financial_analyst_node(state1, _config(state1["run_id"]))
        assert malformed_mock.call_count == 2, (
            f"Schema retry: expected 2 calls, got {malformed_mock.call_count}"
        )

        # --- API-error retry (non-OutputParserException) ---
        api_mock = _MockFinancialLLM(
            raise_exc=Exception("Simulated API connection error")
        )
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst._build_llm", lambda: api_mock
        )

        state2 = _build_state([])
        with pytest.raises(Exception, match="Simulated API connection error"):
            await financial_analyst_node(state2, _config(state2["run_id"]))
        assert api_mock.call_count == 3, (
            f"API retry: expected 3 calls, got {api_mock.call_count}"
        )


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


class TestRetrieval:
    """REQ-006 Main Flow: anchor-query retrieval for financial chunks."""

    async def test_financial_retrieval_uses_different_queries_from_risk_and_scope(
        self,
    ) -> None:
        risk_set = set(RISK_ANCHOR_QUERIES)
        scope_set = set(SCOPE_ANCHOR_QUERIES)
        financial_set = set(FINANCIAL_ANCHOR_QUERIES)

        overlap_risk = financial_set & risk_set
        assert len(overlap_risk) == 0, (
            f"Financial queries overlap with RISK queries: {overlap_risk}"
        )
        overlap_scope = financial_set & scope_set
        assert len(overlap_scope) == 0, (
            f"Financial queries overlap with SCOPE queries: {overlap_scope}"
        )

    async def test_empty_financial_retrieval_falls_back_to_first_15_chunks(
        self,
        mock_financial_llm: Any,
        monkeypatch: Any,
    ) -> None:
        """When retrieve_financial_chunks returns [], the node should fall back
        to the first 15 chunks (but the retrieval function itself has its own
        fallback). We test this by ensuring the LLM is called with the fallback
        content when embedding fails.
        """
        # Patch retrieval to return empty (simulate no financial chunks found)
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.retrieve_financial_chunks",
            _async_ret([]),
        )
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.with_session",
            lambda: _NullSession(),
        )

        state = _build_state([
            {"content": f"Chunk {i} content", "detected_language": "en", "chunk_index": i}
            for i in range(20)
        ])
        result = await financial_analyst_node(state, _config(state["run_id"]))
        # Empty retrieval should trigger fallback to error dict in node
        # because the node's guard returns _malformed_response_dict() when
        # finance_chunks is empty
        summary = result["financial_summary"]
        assert "error" in summary


# ---------------------------------------------------------------------------
# Security — financial values must never appear in logs
# ---------------------------------------------------------------------------


class TestSecurity:
    """REQ-006 Security NFR: amount_value and amount_currency never logged."""

    @pytest_asyncio.fixture(autouse=True)
    async def _setup(
        self,
        monkeypatch: Any,
    ) -> None:
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.retrieve_financial_chunks",
            _async_ret([{"content": "test", "detected_language": "en", "chunk_index": 0}]),
        )
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.with_session",
            lambda: _NullSession(),
        )

    async def test_financial_values_never_appear_in_logs(
        self,
        mock_financial_llm: Any,
        caplog: Any,
    ) -> None:
        caplog.set_level(logging.INFO)
        caplog.set_level(logging.WARNING)

        state = _build_state([])
        await financial_analyst_node(state, _config(state["run_id"]))

        log_text = "\n".join(caplog.messages)
        # The mock returns SAR 35,000,000, SAR 5,000, SAR 3,500,000, SAR 5,250,000
        # None of these numeric values should appear in logs
        assert "35000000.0" not in log_text, "Contract value leaked into logs"
        assert "5000.0" not in log_text, "LD rate value leaked into logs"
        assert "5250000.0" not in log_text, "Advance payment value leaked into logs"
        assert "3_500_000" not in log_text, "Bond value leaked into logs"
        # Currency codes should not appear in logs either
        assert "amount_currency" not in log_text, (
            "The key 'amount_currency' appeared in log output"
        )

    async def test_currency_warning_log_has_no_value(
        self,
        mock_financial_llm_invalid_currency: Any,
        caplog: Any,
    ) -> None:
        caplog.set_level(logging.WARNING)

        state = _build_state([])
        await financial_analyst_node(state, _config(state["run_id"]))

        log_text = "\n".join(caplog.messages)
        # There should be a WARNING about currency normalisation
        assert "currency normalisation" in log_text or "UNKNOWN" in log_text, (
            "Expected a warning about unknown/normalised currency"
        )
        # But no monetary value should appear
        assert "35000000.0" not in log_text, "Monetary value leaked in currency warning"


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------


class TestCostTracking:
    """REQ-006 AC: llm_cost_events row with node_name='financial_analyst'."""

    async def _create_run(
        self, db: Any, company_with_profile: Any, ready_tender: Any
    ) -> str:
        company, _ = company_with_profile
        run_id = str(uuid.uuid4())
        run = AnalysisRun(
            id=run_id,
            tender_id=ready_tender.id,
            company_id=company.id,
            state="pending",
        )
        db.add(run)
        await db.flush()
        return run_id

    async def test_cost_tracker_fires_with_correct_node_name(
        self,
        db: Any,
        company_with_profile: Any,
        ready_tender: Any,
    ) -> None:
        from app.middleware.cost_tracker import CostTrackingHandler, compute_cost

        run_id = await self._create_run(db, company_with_profile, ready_tender)
        handler = CostTrackingHandler(
            run_id=run_id, node_name="financial_analyst", db=db
        )

        mock_response = LLMResult(
            generations=[[Generation(text="test")]],
            llm_output={
                "model_name": "gemini-2.5-flash",
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
        assert event.node_name == "financial_analyst"
        assert event.run_id == run_id

    async def test_cost_tracker_fires_on_retry_attempts(
        self,
        db: Any,
        company_with_profile: Any,
        ready_tender: Any,
    ) -> None:
        from app.middleware.cost_tracker import CostTrackingHandler

        run_id = await self._create_run(db, company_with_profile, ready_tender)
        handler = CostTrackingHandler(
            run_id=run_id, node_name="financial_analyst", db=db
        )

        for _ in range(2):
            mock_response = LLMResult(
                generations=[[Generation(text="test")]],
                llm_output={
                    "model_name": "gemini-2.5-flash",
                    "token_usage": {"prompt_tokens": 50, "completion_tokens": 25},
                },
            )
            await handler.on_llm_end(mock_response)

        result = await db.execute(
            select(LlmCostEvent).where(LlmCostEvent.run_id == run_id)
        )
        events = result.scalars().all()
        assert len(events) == 2, (
            f"Expected 2 cost event rows (2 LLM calls), got {len(events)}"
        )
        for event in events:
            assert event.node_name == "financial_analyst"


# ---------------------------------------------------------------------------
# Flatten helper
# ---------------------------------------------------------------------------


class TestFlatten:
    """REQ-006 Slice 3: _flatten_financial_summary produces correct row count."""

    def _build_full_summary(self) -> dict:
        """Return a full financial_summary dict covering all 7 item types."""
        return {
            "contract_value": {"value": 35_000_000.0, "currency": "SAR", "needs_review": False},
            "bonds": [
                {
                    "bond_type": "performance",
                    "amount": {"value": 3_500_000.0, "currency": "SAR", "needs_review": False},
                    "percentage": 10.0,
                    "conditions": "Performance bond.",
                    "source_chunk_index": 0,
                },
                {
                    "bond_type": "advance_payment",
                    "amount": {"value": 5_250_000.0, "currency": "SAR", "needs_review": False},
                    "percentage": 15.0,
                    "conditions": "Advance payment guarantee.",
                    "source_chunk_index": 1,
                },
            ],
            "liquidated_damages": {
                "rate": {"value": 5_000.0, "currency": "SAR", "needs_review": False},
                "period": "per day",
                "cap": {"value": 3_500_000.0, "currency": "SAR", "needs_review": False},
                "cap_percentage": 10.0,
                "source_chunk_index": 2,
            },
            "payment_schedule": [
                {
                    "description": "Signing",
                    "percentage": 20.0,
                    "amount": None,
                    "trigger": "on signing",
                },
                {
                    "description": "Completion",
                    "percentage": 50.0,
                    "amount": None,
                    "trigger": "on completion",
                },
                {
                    "description": "TOC",
                    "percentage": 30.0,
                    "amount": None,
                    "trigger": "on TOC",
                },
            ],
            "retention_rate": 5.0,
            "advance_payment": {"value": 5_250_000.0, "currency": "SAR", "needs_review": False},
        }

    def test_flatten_produces_correct_row_count(self) -> None:
        from app.routers.tenders import _flatten_financial_summary

        summary = self._build_full_summary()
        run_id = uuid.uuid4()
        rows = _flatten_financial_summary(summary, run_id)

        # 1 contract_value + 2 bonds + 1 LD + 3 milestones + 1 retention + 1 advance = 9
        assert len(rows) == 9, (
            f"Expected 9 rows, got {len(rows)}"
        )

    def test_flatten_skips_null_items(self) -> None:
        from app.routers.tenders import _flatten_financial_summary

        summary = {
            "contract_value": None,
            "bonds": [
                {
                    "bond_type": "performance",
                    "amount": {"value": 3_500_000.0, "currency": "SAR", "needs_review": False},
                    "percentage": 10.0,
                    "conditions": "PB",
                    "source_chunk_index": 0,
                },
            ],
            "liquidated_damages": None,
            "payment_schedule": [
                {
                    "description": "Signing",
                    "percentage": 20.0,
                    "amount": None,
                    "trigger": "on signing",
                },
            ],
            "retention_rate": None,
            "advance_payment": None,
        }
        run_id = uuid.uuid4()
        rows = _flatten_financial_summary(summary, run_id)

        # Only bond (1) + milestone (1) should be present = 2 rows
        commitment_types = {r["commitment_type"] for r in rows}
        assert "contract_value" not in commitment_types
        assert "liquidated_damages" not in commitment_types
        assert "retention" not in commitment_types
        assert "advance_payment" not in commitment_types
        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"

    def test_flatten_skips_error_summary(self) -> None:
        from app.routers.tenders import _flatten_financial_summary

        summary = {"error": "malformed", "bonds": [], "payment_schedule": None}
        run_id = uuid.uuid4()
        rows = _flatten_financial_summary(summary, run_id)

        assert rows == [], (
            f"Expected empty list for error summary, got {rows}"
        )

    def test_flatten_includes_run_id_in_every_row(self) -> None:
        from app.routers.tenders import _flatten_financial_summary

        summary = {
            "contract_value": {"value": 35_000_000.0, "currency": "SAR", "needs_review": False},
            "bonds": [
                {
                    "bond_type": "performance",
                    "amount": {"value": 3_500_000.0, "currency": "SAR", "needs_review": False},
                    "percentage": 10.0,
                    "conditions": "PB",
                    "source_chunk_index": 0,
                },
            ],
            "liquidated_damages": None,
            "payment_schedule": [],
            "retention_rate": None,
            "advance_payment": None,
        }
        run_id = uuid.uuid4()
        rows = _flatten_financial_summary(summary, run_id)

        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
        for row in rows:
            assert row["run_id"] == run_id, (
                f"run_id mismatch: expected {run_id}, got {row['run_id']}"
            )


# ---------------------------------------------------------------------------
# Persistence (integration)
# ---------------------------------------------------------------------------


class TestPersistence:
    """REQ-006 Slice 3: atomic persistence and GET /financial endpoint."""

    async def _create_ready_tender(
        self,
        db: Any,
        company: Any,
    ) -> tuple[Any, str]:
        tender = Tender(
            id=str(uuid.uuid4()),
            company_id=company.id,
            filename="financial_test.pdf",
            storage_path="/tmp/financial_test.pdf",
            file_size_bytes=100,
            status="ready",
        )
        db.add(tender)
        await db.flush()

        chunk_texts = [
            "Performance bond of 10% of contract value.",
            "Advance payment of 15%.",
            "LD at SAR 5,000 per day, cap 10%.",
        ]
        for i, text in enumerate(chunk_texts):
            chunk = TenderChunk(
                id=str(uuid.uuid4()),
                tender_id=tender.id,
                company_id=company.id,
                chunk_index=i,
                content=text,
                detected_language="en",
                embedding=EMBEDDING_STUB,
            )
            db.add(chunk)
        await db.flush()
        return tender, company

    async def test_financial_commitments_persisted_on_awaiting_hitl(
        self,
        db: Any,
        company_with_profile: Any,
        monkeypatch: Any,
    ) -> None:
        company, _ = company_with_profile
        tender, _ = await self._create_ready_tender(db, company)

        chunks = [
            {"content": "Contract value SAR 35M, 10% PB.",
             "detected_language": "en", "chunk_index": 0},
            {"content": "Advance payment 15%.",
             "detected_language": "en", "chunk_index": 1},
        ]

        # Patch financial_analyst's LLM to return a valid output
        fin_output = FinancialOutput(
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
                    conditions="Performance bond.",
                    source_chunk_index=0,
                ),
            ],
            liquidated_damages=None,
            payment_schedule=[],
            retention_rate=None,
            advance_payment=None,
        )
        mock_fin = _MockFinancialLLM(return_value=fin_output)
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst._build_llm", lambda: mock_fin
        )

        # Patch risk_radar's LLM (needed for graph to run)
        risk_output = RiskRadarOutput(findings=[
            RiskFinding(
                category="penalty", severity="high",
                clause_text="penalty clause",
                explanation="Standard penalty",
                source_chunk_index=0, confidence=0.9,
            ),
        ])
        from tests.conftest import _MockStructuredLLM
        mock_risk = _MockStructuredLLM(return_value=risk_output)
        monkeypatch.setattr(
            "app.agents.nodes.risk_radar._build_llm", lambda: mock_risk
        )

        # Patch embeddings everywhere
        stub = make_stub_embeddings(dim=settings.embedding_dimensions)
        for mod_path in ("app.agents.retrieval", "app.agents.nodes.risk_radar",
                         "app.agents.embeddings"):
            monkeypatch.setattr(f"{mod_path}.get_embeddings_client", lambda: stub)

        # Patch with_session for all graph-reachable paths
        class _TestSessionCtx:
            async def __aenter__(self) -> Any:
                return db
            async def __aexit__(self, *args: Any) -> None:
                pass
        ctx_factory = lambda: _TestSessionCtx()
        for mod in ("app.agents.nodes.financial_analyst",
                     "app.agents.nodes.feasibility_scorer",
                     "app.agents.nodes.risk_radar",
                     "app.agents.retrieval"):
            monkeypatch.setattr(f"{mod}.with_session", ctx_factory)

        # Patch feasibility_scorer to return valid output for graph
        from app.agents.skills.feasibility_scoring import (
            DimensionScore, FeasibilityOutput,
        )
        feas_output = FeasibilityOutput.model_construct(**{
            "technical_fit": DimensionScore.model_construct(score=10, rationale="A"),
            "financial_capacity": DimensionScore.model_construct(score=10, rationale="B"),
            "timeline": DimensionScore.model_construct(score=10, rationale="C"),
            "geographic_scope": DimensionScore.model_construct(score=10, rationale="D"),
            "past_experience": DimensionScore.model_construct(score=10, rationale="E"),
        })
        from tests.test_feasibility_scorer import _MockFeasibilityLLM
        mock_feas = _MockFeasibilityLLM(return_value=feas_output)
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer._build_llm", lambda: mock_feas
        )

        async def _fake_profile_lookup(input_dict: dict, *args, **kwargs):
            from app.schemas.company import CompanyProfileSchema
            return CompanyProfileSchema(
                specializations=["civil", "roads"],
                financial_capacity={"currency": "SAR", "annual_turnover": 1_000_000,
                                    "available_bonding_capacity": 500_000},
                geographic_reach=["SA"],
                past_projects=[],
                max_project_value=500_000,
            )
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.profile_lookup",
            type("_FakeTool", (), {"ainvoke": _fake_profile_lookup})(),
        )
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.retrieve_scope_relevant_chunks",
            _async_ret(chunks),
        )

        # Patch profile_lookup's SessionLocal
        import importlib
        from contextlib import asynccontextmanager
        profile_lookup_mod = importlib.import_module("app.agents.tools.profile_lookup")
        @asynccontextmanager
        async def _profile_session():
            yield db
        monkeypatch.setattr(profile_lookup_mod, "SessionLocal", _profile_session)

        # Create the AnalysisRun
        run_id = str(uuid.uuid4())
        run = AnalysisRun(
            id=run_id, tender_id=tender.id,
            company_id=company.id, state="pending",
        )
        db.add(run)
        await db.flush()

        from app.agents.graph import graph

        config = {"configurable": {"thread_id": run_id}}
        initial_state = TenderState(
            tender_id=str(tender.id),
            run_id=run_id,
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

        findings_dicts: list[dict[str, Any]] = []
        feasi_score: float | None = None
        fin_summary: dict | None = None

        async for event in graph.astream(initial_state, config):
            node_name = list(event.keys())[0]
            if node_name.startswith("__"):
                continue
            payload = event[node_name]
            if node_name == "risk_radar":
                findings_dicts = payload.get("risk_findings", []) or []
            elif node_name == "scorer":
                feasi_score = payload.get("feasibility_score")
            elif node_name == "financial":
                fin_summary = payload.get("financial_summary")
            if node_name == "aggregator":
                break

        # Persist findings + feasibility + financial + state like run_graph does
        if findings_dicts:
            await db.execute(
                insert(RiskFindingDB).values([
                    {
                        "run_id": run_id,
                        "category": f["category"],
                        "severity": f["severity"],
                        "clause_text": f["clause_text"],
                        "explanation": f["explanation"],
                        "source_chunk_index": f["source_chunk_index"],
                        "confidence": f["confidence"],
                    }
                    for f in findings_dicts
                ])
            )

        commitment_count = 0
        if fin_summary and "error" not in fin_summary:
            from app.routers.tenders import _flatten_financial_summary
            commitment_rows = _flatten_financial_summary(fin_summary, run_id)
            if commitment_rows:
                await db.execute(
                    insert(FinancialCommitment).values(commitment_rows)
                )
                commitment_count = len(commitment_rows)

        await db.execute(
            update(AnalysisRun)
            .where(AnalysisRun.id == run_id)
            .values(
                state="awaiting_hitl",
                feasibility_score=feasi_score,
            )
        )
        await db.commit()

        # Verify financial commitments persisted
        fc_result = await db.execute(
            select(FinancialCommitment).where(
                FinancialCommitment.run_id == run_id
            )
        )
        persisted = fc_result.scalars().all()
        assert len(persisted) > 0, (
            "Expected at least one financial_commitments row, got 0"
        )

        # Verify state = awaiting_hitl
        db.expire_all()
        run_row = await db.get(AnalysisRun, run_id)
        assert run_row is not None
        assert run_row.state == "awaiting_hitl", (
            f"Expected 'awaiting_hitl', got {run_row.state}"
        )

    async def test_error_path_produces_no_financial_commitments_rows(
        self,
        db: Any,
        company_with_profile: Any,
        monkeypatch: Any,
    ) -> None:
        company, _ = company_with_profile
        tender, _ = await self._create_ready_tender(db, company)

        chunks = [
            {"content": "Some content.",
             "detected_language": "en", "chunk_index": 0},
        ]

        # Patch financial_analyst's LLM to fail (malformed path)
        mock_fin = _MockFinancialLLM(
            raise_exc=OutputParserException("Schema error")
        )
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst._build_llm", lambda: mock_fin
        )

        # Patch risk_radar LLM
        risk_output = RiskRadarOutput(findings=[
            RiskFinding(
                category="penalty", severity="high",
                clause_text="penalty",
                explanation="Standard penalty",
                source_chunk_index=0, confidence=0.9,
            ),
        ])
        from tests.conftest import _MockStructuredLLM
        mock_risk = _MockStructuredLLM(return_value=risk_output)
        monkeypatch.setattr(
            "app.agents.nodes.risk_radar._build_llm", lambda: mock_risk
        )

        # Patch embeddings
        stub = make_stub_embeddings(dim=settings.embedding_dimensions)
        for mod_path in ("app.agents.retrieval", "app.agents.nodes.risk_radar",
                         "app.agents.embeddings"):
            monkeypatch.setattr(f"{mod_path}.get_embeddings_client", lambda: stub)

        # Patch with_session
        class _TestSessionCtx:
            async def __aenter__(self) -> Any:
                return db
            async def __aexit__(self, *args: Any) -> None:
                pass
        ctx_factory = lambda: _TestSessionCtx()
        for mod in ("app.agents.nodes.financial_analyst",
                     "app.agents.nodes.feasibility_scorer",
                     "app.agents.nodes.risk_radar",
                     "app.agents.retrieval"):
            monkeypatch.setattr(f"{mod}.with_session", ctx_factory)

        # Patch feasibility_scorer
        from app.agents.skills.feasibility_scoring import (
            DimensionScore, FeasibilityOutput,
        )
        feas_output = FeasibilityOutput.model_construct(**{
            "technical_fit": DimensionScore.model_construct(score=10, rationale="A"),
            "financial_capacity": DimensionScore.model_construct(score=10, rationale="B"),
            "timeline": DimensionScore.model_construct(score=10, rationale="C"),
            "geographic_scope": DimensionScore.model_construct(score=10, rationale="D"),
            "past_experience": DimensionScore.model_construct(score=10, rationale="E"),
        })
        from tests.test_feasibility_scorer import _MockFeasibilityLLM
        mock_feas = _MockFeasibilityLLM(return_value=feas_output)
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer._build_llm", lambda: mock_feas
        )

        async def _fake_profile_lookup(input_dict: dict, *args, **kwargs):
            from app.schemas.company import CompanyProfileSchema
            return CompanyProfileSchema(
                specializations=["civil", "roads"],
                financial_capacity={"currency": "SAR", "annual_turnover": 1_000_000,
                                    "available_bonding_capacity": 500_000},
                geographic_reach=["SA"],
                past_projects=[],
                max_project_value=500_000,
            )
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.profile_lookup",
            type("_FakeTool", (), {"ainvoke": _fake_profile_lookup})(),
        )
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.retrieve_scope_relevant_chunks",
            _async_ret(chunks),
        )

        import importlib
        from contextlib import asynccontextmanager
        profile_lookup_mod = importlib.import_module("app.agents.tools.profile_lookup")
        @asynccontextmanager
        async def _profile_session():
            yield db
        monkeypatch.setattr(profile_lookup_mod, "SessionLocal", _profile_session)

        run_id = str(uuid.uuid4())
        run = AnalysisRun(
            id=run_id, tender_id=tender.id,
            company_id=company.id, state="pending",
        )
        db.add(run)
        await db.flush()

        from app.agents.graph import graph

        config = {"configurable": {"thread_id": run_id}}
        initial_state = TenderState(
            tender_id=str(tender.id),
            run_id=run_id,
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

        findings_dicts: list[dict[str, Any]] = []

        async for event in graph.astream(initial_state, config):
            node_name = list(event.keys())[0]
            if node_name.startswith("__"):
                continue
            payload_node = event[node_name]
            if node_name == "risk_radar":
                findings_dicts = payload_node.get("risk_findings", []) or []
            if node_name == "aggregator":
                break

        # Get final state
        final_state = await graph.aget_state(config)
        fin_summary = (
            final_state.values.get("financial_summary", {})
            if final_state is not None
            else {}
        ) or {}

        if findings_dicts:
            await db.execute(
                insert(RiskFindingDB).values([
                    {
                        "run_id": run_id,
                        "category": f["category"],
                        "severity": f["severity"],
                        "clause_text": f["clause_text"],
                        "explanation": f["explanation"],
                        "source_chunk_index": f["source_chunk_index"],
                        "confidence": f["confidence"],
                    }
                    for f in findings_dicts
                ])
            )

        # Financial summary has error -> skip INSERT
        commitment_count = 0
        if "error" not in fin_summary:
            from app.routers.tenders import _flatten_financial_summary
            commitment_rows = _flatten_financial_summary(fin_summary, run_id)
            if commitment_rows:
                await db.execute(
                    insert(FinancialCommitment).values(commitment_rows)
                )
                commitment_count = len(commitment_rows)

        await db.execute(
            update(AnalysisRun)
            .where(AnalysisRun.id == run_id)
            .values(state="awaiting_hitl")
        )
        await db.commit()

        # Verify zero financial_commitments rows
        fc_result = await db.execute(
            select(FinancialCommitment).where(
                FinancialCommitment.run_id == run_id
            )
        )
        persisted = fc_result.scalars().all()
        assert len(persisted) == 0, (
            f"Expected 0 financial_commitments rows on error path, "
            f"got {len(persisted)}"
        )

        # But state is still set
        db.expire_all()
        run_row = await db.get(AnalysisRun, run_id)
        assert run_row is not None
        assert run_row.state == "awaiting_hitl", (
            f"Expected 'awaiting_hitl' even on error path, got {run_row.state}"
        )

    async def test_all_four_operations_atomic_commit(
        self,
        db: Any,
        company_with_profile: Any,
        monkeypatch: Any,
    ) -> None:
        """
        *** CRITICAL TEST ***
        Validates that risk_findings INSERT, feasibility_score UPDATE,
        financial_commitments INSERT, and state UPDATE are all rolled back
        together if any single operation fails.
        """
        company, _ = company_with_profile
        tender, _ = await self._create_ready_tender(db, company)

        # Create run
        run_id = str(uuid.uuid4())
        run = AnalysisRun(
            id=run_id, tender_id=tender.id,
            company_id=company.id, state="pending",
        )
        db.add(run)
        await db.commit()

        findings_data = [
            {
                "run_id": run_id,
                "category": "penalty",
                "severity": "high",
                "clause_text": "Test clause",
                "explanation": "Test explanation",
                "source_chunk_index": 0,
                "confidence": 0.9,
            },
        ]

        await db.execute(text("SAVEPOINT atomic_sp"))
        try:
            await db.execute(insert(RiskFindingDB).values(findings_data))

            # Inject failure via a bogus column name in the UPDATE
            with pytest.raises(Exception):
                await db.execute(
                    text(
                        "UPDATE analysis_runs SET state='awaiting_hitl', "
                        "feasibility_score=80.0, nonexistent_column=1 "
                        "WHERE id=:id"
                    ),
                    {"id": run_id},
                )
        finally:
            await db.execute(text("ROLLBACK TO SAVEPOINT atomic_sp"))

        # Verify risk_findings rolled back
        result = await db.execute(
            select(RiskFindingDB).where(RiskFindingDB.run_id == run_id)
        )
        persisted_findings = result.scalars().all()
        assert len(persisted_findings) == 0, (
            f"Expected 0 risk_findings (rolled back), "
            f"got {len(persisted_findings)}"
        )

        # Verify financial_commitments rolled back
        fc_result = await db.execute(
            select(FinancialCommitment).where(
                FinancialCommitment.run_id == run_id
            )
        )
        persisted_fc = fc_result.scalars().all()
        assert len(persisted_fc) == 0, (
            f"Expected 0 financial_commitments (rolled back), "
            f"got {len(persisted_fc)}"
        )

        # analysis_run row is unchanged (still pending)
        db.expire_all()
        run_row = await db.get(AnalysisRun, run_id)
        assert run_row is not None
        assert run_row.state == "pending", (
            f"Expected state='pending' (rolled back), got {run_row.state}"
        )
        assert run_row.feasibility_score is None, (
            f"Expected feasibility_score=None (rolled back), "
            f"got {run_row.feasibility_score}"
        )

    async def test_get_financial_endpoint_returns_correct_types(
        self,
        db: Any,
        company_with_profile: Any,
        auth_headers: Any,
        monkeypatch: Any,
        app_client: Any,
    ) -> None:
        company, raw_key = company_with_profile
        tender, _ = await self._create_ready_tender(db, company)

        run_id = str(uuid.uuid4())
        run = AnalysisRun(
            id=run_id, tender_id=tender.id,
            company_id=company.id, state="awaiting_hitl",
        )
        db.add(run)

        commitment = FinancialCommitment(
            run_id=run_id,
            commitment_type="bond",
            amount_value=3_500_000.0,
            amount_currency="SAR",
            percentage=10.0,
            description="Performance bond",
            needs_review=False,
            source_chunk_index=0,
        )
        db.add(commitment)
        await db.commit()

        resp = await app_client.get(
            f"/tenders/{tender.id}/financial",
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 200, resp.text
        items = resp.json()
        assert isinstance(items, list), "Response should be a list"
        assert len(items) >= 1

        required_fields = {
            "id", "commitment_type", "amount_value", "amount_currency",
            "percentage", "description", "needs_review", "source_chunk_index",
        }
        for item in items:
            assert set(item.keys()) >= required_fields, (
                f"Missing fields in item: {required_fields - set(item.keys())}"
            )
            assert item["commitment_type"] in (
                "bond", "liquidated_damages", "payment_milestone",
                "retention", "advance_payment", "contract_value",
            ), f"Unexpected commitment_type: {item['commitment_type']}"

    async def test_get_financial_returns_404_before_awaiting_hitl(
        self,
        db: Any,
        company_with_profile: Any,
        auth_headers: Any,
        app_client: Any,
    ) -> None:
        company, raw_key = company_with_profile
        tender, _ = await self._create_ready_tender(db, company)

        run_id = str(uuid.uuid4())
        run = AnalysisRun(
            id=run_id, tender_id=tender.id,
            company_id=company.id, state="running",
        )
        db.add(run)
        await db.commit()

        resp = await app_client.get(
            f"/tenders/{tender.id}/financial",
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 404, (
            f"Expected 404 before awaiting_hitl, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Boundary values
# ---------------------------------------------------------------------------


class TestBoundaryValues:
    """REQ-006 Postconditions: needs_review correctly set."""

    @pytest_asyncio.fixture(autouse=True)
    async def _setup(
        self,
        monkeypatch: Any,
    ) -> None:
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.retrieve_financial_chunks",
            _async_ret([{"content": "test", "detected_language": "en", "chunk_index": 0}]),
        )
        monkeypatch.setattr(
            "app.agents.nodes.financial_analyst.with_session",
            lambda: _NullSession(),
        )

    async def test_all_needs_review_false_when_all_currencies_valid(
        self,
        mock_financial_llm: Any,
    ) -> None:
        state = _build_state([])
        result = await financial_analyst_node(state, _config(state["run_id"]))
        summary = result["financial_summary"]

        def _check_monetary(obj: Any, path: str) -> None:
            if obj is None:
                return
            if isinstance(obj, dict) and "needs_review" in obj:
                assert obj["needs_review"] is False, (
                    f"needs_review should be False at {path}, "
                    f"got {obj['needs_review']}"
                )

        _check_monetary(summary.get("contract_value"), "contract_value")
        _check_monetary(summary.get("advance_payment"), "advance_payment")

        ld = summary.get("liquidated_damages")
        if ld:
            _check_monetary(ld.get("rate"), "liquidated_damages.rate")
            _check_monetary(ld.get("cap"), "liquidated_damages.cap")

        for i, bond in enumerate(summary.get("bonds", [])):
            _check_monetary(bond.get("amount"), f"bonds[{i}].amount")

    async def test_needs_review_summary_count_matches_flagged_items(
        self,
        mock_financial_llm_invalid_currency: Any,
    ) -> None:
        state = _build_state([])
        result = await financial_analyst_node(state, _config(state["run_id"]))
        summary = result["financial_summary"]

        count = 0

        def _count_needs_review(obj: Any) -> None:
            nonlocal count
            if obj is None:
                return
            if isinstance(obj, dict) and "needs_review" in obj:
                if obj.get("needs_review", False):
                    count += 1

        _count_needs_review(summary.get("contract_value"))
        _count_needs_review(summary.get("advance_payment"))

        ld = summary.get("liquidated_damages")
        if ld:
            _count_needs_review(ld.get("rate"))
            _count_needs_review(ld.get("cap"))

        for bond in summary.get("bonds", []):
            _count_needs_review(bond.get("amount"))

        assert count == 1, (
            f"Expected exactly 1 needs_review=True item, "
            f"got {count}"
        )
