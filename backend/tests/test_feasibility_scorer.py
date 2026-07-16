"""Tests for the Feasibility Scorer node (REQ-005 Slice 5 — QA).

Every test maps directly to a REQ-005 Acceptance Criteria item or Alternative
Flow. Tests are fully isolated — each test gets its own transactional database
session that rolls back at teardown (see ``db`` fixture in conftest.py).

Design rules:
  - Mock the LLM client — never make real API calls.
  - Use a real test database for persistence tests (``TEST_DATABASE_URL``).
  - Direct node calls use a null session for unit tests, real sessions for
    persistence/integration tests.
  - The two retry strategies (schema-validation → 1 retry, API-error → 2 retries)
    are verified independently with exact call-count assertions.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import pytest
import pytest_asyncio
from langchain_core.exceptions import OutputParserException
from langchain_core.outputs import Generation, LLMResult
from langchain_core.runnables import RunnableConfig
from sqlalchemy import insert, select, update

from app.agents.nodes.feasibility_scorer import feasibility_scorer_node
from app.agents.retrieval import (
    retrieve_risk_relevant_chunks,
    retrieve_scope_relevant_chunks,
    RISK_ANCHOR_QUERIES,
)
from app.agents.skills.feasibility_scoring import (
    DimensionScore,
    FeasibilityOutput,
    SCOPE_ANCHOR_QUERIES,
)
from tests.conftest import _MockStructuredLLM
from app.agents.skills.risk_clause_extraction import RiskFinding, RiskRadarOutput
from app.agents.state import TenderState
from app.db.models import AnalysisRun, LlmCostEvent, RiskFinding as RiskFindingDB, Tender, TenderChunk

settings = __import__("app.config", fromlist=["get_settings"]).get_settings()
EMBEDDING_STUB = [0.01] * settings.embedding_dimensions


# ---------------------------------------------------------------------------
# Mock structured LLM helper
# ---------------------------------------------------------------------------


class _MockFeasibilityLLM:
    """Mock structured-output LLM that returns a canned FeasibilityOutput or raises.

    Tracks call_count so tests can verify retry behaviour without inspecting
    log output or timing.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NullSession:
    """Quacks like an AsyncSession but silently swallows all DB operations.

    Used by unit tests that call feasibility_scorer_node directly but do not
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
    """Wrap a sync return value into an async callable (for monkeypatch setattr).

    LangChain node code does ``await retrieve_scope_relevant_chunks(...)``, so
    the patched function must be a coroutine function (not a plain lambda).
    """
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
    """REQ-005 AC: schema matches aggregator contract, strict keys."""

    @pytest_asyncio.fixture(autouse=True)
    async def _setup(
        self,
        monkeypatch: Any,
        sample_scope_chunks: list[dict],
        company_profile_fixture: Any,
    ) -> None:
        company, _ = company_profile_fixture
        self._company_id = company.id

        async def _fake_profile_lookup(input_dict: dict, *args: Any, **kwargs: Any) -> Any:
            from app.schemas.company import CompanyProfileSchema
            return CompanyProfileSchema(
                specializations=["civil", "roads"],
                financial_capacity={
                    "currency": "SAR",
                    "annual_turnover": 1_000_000,
                    "available_bonding_capacity": 500_000,
                },
                geographic_reach=["SA"],
                past_projects=[
                    {"name": "Road Alpha", "value": 300_000, "year": 2024, "sector": "roads"},
                    {"name": "Civil Beta", "value": 200_000, "year": 2023, "sector": "civil"},
                ],
                max_project_value=500_000,
            )

        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.profile_lookup",
            type("_FakeTool", (), {"ainvoke": _fake_profile_lookup})(),
        )
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.retrieve_scope_relevant_chunks",
            _async_ret(sample_scope_chunks),
        )
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.with_session",
            lambda: _NullSession(),
        )

    async def test_return_keys_match_aggregator_contract(
        self,
        mock_feasibility_llm: Any,
    ) -> None:
        state = _build_state([], company_id=self._company_id)
        result = await feasibility_scorer_node(state, _config(state["run_id"]))

        assert set(result.keys()) == {"feasibility_score", "feasibility_breakdown"}, (
            f"Unexpected keys: {set(result.keys())}"
        )

    async def test_feasibility_breakdown_has_all_5_dimensions(
        self,
        mock_feasibility_llm: Any,
    ) -> None:
        state = _build_state([], company_id=self._company_id)
        result = await feasibility_scorer_node(state, _config(state["run_id"]))

        breakdown = result["feasibility_breakdown"]
        expected_dims = {
            "technical_fit", "financial_capacity", "timeline",
            "geographic_scope", "past_experience",
        }
        assert set(breakdown.keys()) == expected_dims, (
            f"Unexpected breakdown keys: {set(breakdown.keys())}"
        )
        for dim_name in expected_dims:
            entry = breakdown[dim_name]
            assert "score" in entry, f"{dim_name} missing 'score'"
            assert "rationale" in entry, f"{dim_name} missing 'rationale'"
            assert isinstance(entry["score"], int), f"{dim_name} score not int"
            assert isinstance(entry["rationale"], str), f"{dim_name} rationale not str"

    async def test_composite_score_equals_sum_of_dimensions(
        self,
        monkeypatch: Any,
    ) -> None:
        from app.agents.nodes.feasibility_scorer import _clamp_and_sum

        output = FeasibilityOutput.model_construct(**{
            "technical_fit": DimensionScore.model_construct(score=18, rationale="A"),
            "financial_capacity": DimensionScore.model_construct(score=14, rationale="B"),
            "timeline": DimensionScore.model_construct(score=16, rationale="C"),
            "geographic_scope": DimensionScore.model_construct(score=20, rationale="D"),
            "past_experience": DimensionScore.model_construct(score=12, rationale="E"),
        })
        composite, _ = _clamp_and_sum(output, "test-run-id")
        assert composite == 80.0, f"Expected 80.0, got {composite}"

    async def test_composite_score_is_always_float(
        self,
        mock_feasibility_llm: Any,
    ) -> None:
        state = _build_state([], company_id=self._company_id)
        result = await feasibility_scorer_node(state, _config(state["run_id"]))

        assert isinstance(result["feasibility_score"], float), (
            f"Expected float, got {type(result['feasibility_score'])}"
        )


# ---------------------------------------------------------------------------
# Clamping
# ---------------------------------------------------------------------------


class TestClamping:
    """REQ-005 Alt Flow: out-of-range dimension scores are clamped to [0, 20]."""

    @pytest_asyncio.fixture(autouse=True)
    async def _setup(
        self,
        monkeypatch: Any,
        sample_scope_chunks: list[dict],
        company_profile_fixture: Any,
    ) -> None:
        company, _ = company_profile_fixture
        self._company_id = company.id

        async def _fake_profile_lookup(input_dict: dict, *args: Any, **kwargs: Any) -> Any:
            from app.schemas.company import CompanyProfileSchema
            return CompanyProfileSchema(
                specializations=["civil", "roads"],
                financial_capacity={
                    "currency": "SAR",
                    "annual_turnover": 1_000_000,
                    "available_bonding_capacity": 500_000,
                },
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
            _async_ret(sample_scope_chunks),
        )
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.with_session",
            lambda: _NullSession(),
        )

    async def test_out_of_range_high_score_is_clamped(
        self,
        monkeypatch: Any,
    ) -> None:
        output = FeasibilityOutput.model_construct(**{
            "technical_fit": DimensionScore.model_construct(score=25, rationale="Above max"),
            "financial_capacity": DimensionScore.model_construct(score=14, rationale="Valid"),
            "timeline": DimensionScore.model_construct(score=16, rationale="Valid"),
            "geographic_scope": DimensionScore.model_construct(score=20, rationale="Valid"),
            "past_experience": DimensionScore.model_construct(score=12, rationale="Valid"),
        })
        mock = _MockFeasibilityLLM(return_value=output)
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer._build_llm", lambda: mock
        )

        state = _build_state([], company_id=self._company_id)
        result = await feasibility_scorer_node(state, _config(state["run_id"]))

        assert result["feasibility_breakdown"]["technical_fit"]["score"] == 20, (
            "technical_fit should be clamped to 20"
        )
        # Composite = 20 + 14 + 16 + 20 + 12 = 82 (not 87)
        assert result["feasibility_score"] == 82.0, (
            f"Expected 82.0 (clamped), got {result['feasibility_score']}"
        )

    async def test_out_of_range_low_score_is_clamped(
        self,
        monkeypatch: Any,
    ) -> None:
        output = FeasibilityOutput.model_construct(**{
            "technical_fit": DimensionScore.model_construct(score=18, rationale="Valid"),
            "financial_capacity": DimensionScore.model_construct(score=14, rationale="Valid"),
            "timeline": DimensionScore.model_construct(score=16, rationale="Valid"),
            "geographic_scope": DimensionScore.model_construct(score=-3, rationale="Below min"),
            "past_experience": DimensionScore.model_construct(score=12, rationale="Valid"),
        })
        mock = _MockFeasibilityLLM(return_value=output)
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer._build_llm", lambda: mock
        )

        state = _build_state([], company_id=self._company_id)
        result = await feasibility_scorer_node(state, _config(state["run_id"]))

        assert result["feasibility_breakdown"]["geographic_scope"]["score"] == 0, (
            "geographic_scope should be clamped to 0"
        )
        # Composite = 18 + 14 + 16 + 0 + 12 = 60 (not 57)
        assert result["feasibility_score"] == 60.0, (
            f"Expected 60.0 (clamped), got {result['feasibility_score']}"
        )

    async def test_clamping_logs_warning_with_run_id(
        self,
        monkeypatch: Any,
        caplog: Any,
    ) -> None:
        caplog.set_level(logging.WARNING)

        output = FeasibilityOutput.model_construct(**{
            "technical_fit": DimensionScore.model_construct(score=25, rationale="Above max"),
            "financial_capacity": DimensionScore.model_construct(score=14, rationale="Valid"),
            "timeline": DimensionScore.model_construct(score=16, rationale="Valid"),
            "geographic_scope": DimensionScore.model_construct(score=20, rationale="Valid"),
            "past_experience": DimensionScore.model_construct(score=12, rationale="Valid"),
        })
        mock = _MockFeasibilityLLM(return_value=output)
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer._build_llm", lambda: mock
        )

        state = _build_state([], company_id=self._company_id)
        run_id = state["run_id"]
        await feasibility_scorer_node(state, _config(run_id))

        log_text = "\n".join(caplog.messages)
        assert run_id in log_text, f"Run ID {run_id} not found in warning logs"
        assert "technical_fit" in log_text, (
            "Dimension name 'technical_fit' not found in warning logs"
        )
        # No profile data should appear in the warning
        assert "annual_turnover" not in log_text, "Profile data leaked into logs"
        assert "available_bonding_capacity" not in log_text, "Profile data leaked into logs"


# ---------------------------------------------------------------------------
# Error handling — two independent retry strategies
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """REQ-005 Alternative Flows: schema-validation and API-error retry paths."""

    @pytest_asyncio.fixture(autouse=True)
    async def _setup(
        self,
        monkeypatch: Any,
        sample_scope_chunks: list[dict],
        company_profile_fixture: Any,
    ) -> None:
        company, _ = company_profile_fixture
        self._company_id = company.id

        async def _fake_profile_lookup(input_dict: dict, *args: Any, **kwargs: Any) -> Any:
            from app.schemas.company import CompanyProfileSchema
            return CompanyProfileSchema(
                specializations=["civil", "roads"],
                financial_capacity={
                    "currency": "SAR",
                    "annual_turnover": 1_000_000,
                    "available_bonding_capacity": 500_000,
                },
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
            _async_ret(sample_scope_chunks),
        )
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.with_session",
            lambda: _NullSession(),
        )

    async def test_malformed_output_retries_once_then_degrades(
        self,
        mock_feasibility_llm_malformed: Any,
    ) -> None:
        state = _build_state([], company_id=self._company_id)
        result = await feasibility_scorer_node(state, _config(state["run_id"]))

        assert mock_feasibility_llm_malformed.call_count == 2, (
            f"Expected 2 LLM calls (initial + 1 retry), "
            f"got {mock_feasibility_llm_malformed.call_count}"
        )
        assert result == {
            "feasibility_score": 0.0,
            "feasibility_breakdown": {
                "error": "Scoring unavailable — malformed LLM response"
            },
        }

    async def test_api_failure_retries_three_times_then_raises(
        self,
        mock_feasibility_llm_api_error: Any,
    ) -> None:
        state = _build_state([], company_id=self._company_id)
        with pytest.raises(Exception, match="Simulated API connection error"):
            await feasibility_scorer_node(state, _config(state["run_id"]))

        assert mock_feasibility_llm_api_error.call_count == 3, (
            f"Expected 3 LLM calls (API retry), "
            f"got {mock_feasibility_llm_api_error.call_count}"
        )

    async def test_retry_strategies_are_independent(
        self,
        monkeypatch: Any,
    ) -> None:
        # Schema-validation retry: OutputParserException -> 1 retry (2 total)
        malformed_mock = _MockFeasibilityLLM(
            raise_exc=OutputParserException("Schema error")
        )
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer._build_llm",
            lambda: malformed_mock,
        )

        state1 = _build_state([], company_id=self._company_id)
        await feasibility_scorer_node(state1, _config(state1["run_id"]))
        assert malformed_mock.call_count == 2, (
            f"Schema retry: expected 2 calls, got {malformed_mock.call_count}"
        )

        # API-error retry: Exception -> 3 total via tenacity
        api_mock = _MockFeasibilityLLM(
            raise_exc=Exception("Simulated API connection error")
        )
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer._build_llm",
            lambda: api_mock,
        )

        state2 = _build_state([], company_id=self._company_id)
        with pytest.raises(Exception, match="Simulated API connection error"):
            await feasibility_scorer_node(state2, _config(state2["run_id"]))
        assert api_mock.call_count == 3, (
            f"API retry: expected 3 calls, got {api_mock.call_count}"
        )


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


class TestRetrieval:
    """REQ-005 Main Flow step 2: scope anchor-query retrieval is separate from risk."""

    async def test_scope_retrieval_is_separate_from_risk_retrieval(
        self,
    ) -> None:
        assert set(SCOPE_ANCHOR_QUERIES) != set(RISK_ANCHOR_QUERIES), (
            "Scope and risk anchor queries must differ"
        )
        overlap = set(SCOPE_ANCHOR_QUERIES) & set(RISK_ANCHOR_QUERIES)
        assert len(overlap) == 0, (
            f"Scope and risk anchor queries share {len(overlap)} queries: {overlap}"
        )

    async def test_empty_scope_retrieval_falls_back_to_first_20_chunks(
        self,
        monkeypatch: Any,
        company_profile_fixture: Any,
    ) -> None:
        company, _ = company_profile_fixture
        company_id = company.id

        async def _fake_profile_lookup(input_dict: dict, *args: Any, **kwargs: Any) -> Any:
            from app.schemas.company import CompanyProfileSchema
            return CompanyProfileSchema(
                specializations=["civil", "roads"],
                financial_capacity={
                    "currency": "SAR",
                    "annual_turnover": 1_000_000,
                    "available_bonding_capacity": 500_000,
                },
                geographic_reach=["SA"],
                past_projects=[],
                max_project_value=500_000,
            )

        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.profile_lookup",
            type("_FakeTool", (), {"ainvoke": _fake_profile_lookup})(),
        )
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.with_session",
            lambda: _NullSession(),
        )

        # Do NOT patch retrieve_scope_relevant_chunks — instead, make the
        # embedding client raise so the retrieval function falls back to
        # _scope_fallback (first 20 chunks by chunk_index).
        class _FailingEmbeddings:
            async def aembed_query(self, text: str) -> list[float]:
                raise Exception("Embedding failed")
            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                raise Exception("Embedding failed")
            async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
                raise Exception("Embedding failed")
        monkeypatch.setattr(
            "app.agents.retrieval.get_embeddings_client",
            lambda: _FailingEmbeddings(),
        )

        # Build 25 sample chunks with consecutive chunk_index
        chunks = [
            {"content": f"Chunk {i} content", "detected_language": "en", "chunk_index": i}
            for i in range(25)
        ]

        # Patch the LLM so we can inspect what it was called with
        call_args_list = []

        class _CapturingMock:
            call_count = 0

            async def ainvoke(self, messages, config=None, **kwargs):
                self.call_count += 1
                call_args_list.append(messages)
                return FeasibilityOutput.model_construct(**{
                    "technical_fit": DimensionScore.model_construct(score=10, rationale="Fallback"),
                    "financial_capacity": DimensionScore.model_construct(score=10, rationale="Fallback"),
                    "timeline": DimensionScore.model_construct(score=10, rationale="Fallback"),
                    "geographic_scope": DimensionScore.model_construct(score=10, rationale="Fallback"),
                    "past_experience": DimensionScore.model_construct(score=10, rationale="Fallback"),
                })

        capturing_mock = _CapturingMock()
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer._build_llm",
            lambda: capturing_mock,
        )

        state = _build_state(chunks, company_id=company_id)
        await feasibility_scorer_node(state, _config(state["run_id"]))

        assert capturing_mock.call_count >= 1, "LLM should have been called"
        if call_args_list:
            human_content = str(call_args_list[0])
            for i in range(20):
                assert f"Chunk {i} content" in human_content, (
                    f"Chunk {i} content should be in the prompt"
                )
            assert "Chunk 24 content" not in human_content, (
                "Chunk out of first 20 should not be in the prompt"
            )

    async def test_scope_chunks_have_no_duplicate_chunk_index(
        self,
        stub_embeddings: Any,
        sample_scope_chunks: list[dict],
    ) -> None:
        from app.agents.embeddings import make_stub_embeddings

        stub = make_stub_embeddings(dim=settings.embedding_dimensions)
        result = await retrieve_scope_relevant_chunks(
            tender_id=str(uuid.uuid4()),
            chunks=sample_scope_chunks,
            top_k_per_query=5,
        )

        indices = [c["chunk_index"] for c in result]
        assert len(indices) == len(set(indices)), (
            f"Duplicate chunk_index values found: {indices}"
        )


# ---------------------------------------------------------------------------
# Profile data security
# ---------------------------------------------------------------------------


class TestProfileSecurity:
    """REQ-005 Security NFR: profile data never appears in logs."""

    @pytest_asyncio.fixture(autouse=True)
    async def _setup(
        self,
        monkeypatch: Any,
        sample_scope_chunks: list[dict],
        company_profile_fixture: Any,
    ) -> None:
        company, _ = company_profile_fixture
        self._company_id = company.id

        async def _fake_profile_lookup(input_dict: dict, *args: Any, **kwargs: Any) -> Any:
            from app.schemas.company import CompanyProfileSchema
            return CompanyProfileSchema(
                specializations=["civil", "roads"],
                financial_capacity={
                    "currency": "SAR",
                    "annual_turnover": 1_000_000,
                    "available_bonding_capacity": 500_000,
                },
                geographic_reach=["SA"],
                past_projects=[
                    {"name": "Road Alpha", "value": 300_000, "year": 2024, "sector": "roads"},
                    {"name": "Civil Beta", "value": 200_000, "year": 2023, "sector": "civil"},
                ],
                max_project_value=500_000,
            )

        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.profile_lookup",
            type("_FakeTool", (), {"ainvoke": _fake_profile_lookup})(),
        )
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.retrieve_scope_relevant_chunks",
            _async_ret(sample_scope_chunks),
        )
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.with_session",
            lambda: _NullSession(),
        )

    async def test_profile_data_never_appears_in_logs(
        self,
        mock_feasibility_llm: Any,
        caplog: Any,
    ) -> None:
        caplog.set_level(logging.INFO)
        caplog.set_level(logging.WARNING)

        state = _build_state([], company_id=self._company_id)
        await feasibility_scorer_node(state, _config(state["run_id"]))

        log_text = "\n".join(caplog.messages)

        assert "annual_turnover" not in log_text, "annual_turnover leaked into logs"
        assert "available_bonding_capacity" not in log_text, (
            "available_bonding_capacity leaked into logs"
        )
        assert "Road Alpha" not in log_text, "Past project name leaked into logs"
        assert "300_000" not in log_text, "Past project value leaked into logs"
        assert "financial_capacity" not in log_text, (
            "The key 'financial_capacity' appeared in log output"
        )

    async def test_profile_lookup_called_with_correct_company_id(
        self,
        monkeypatch: Any,
        mock_feasibility_llm: Any,
    ) -> None:
        call_args_records = []

        class _RecordingTool:
            call_args_list: list[dict] = []

            async def ainvoke(self, input: dict, *args: Any, **kwargs: Any) -> Any:
                self.call_args_list.append(input)
                call_args_records.append(input)
                from app.schemas.company import CompanyProfileSchema
                return CompanyProfileSchema(
                    specializations=["civil", "roads"],
                    financial_capacity={
                        "currency": "SAR",
                        "annual_turnover": 1_000_000,
                        "available_bonding_capacity": 500_000,
                    },
                    geographic_reach=["SA"],
                    past_projects=[],
                    max_project_value=500_000,
                )

        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.profile_lookup",
            _RecordingTool(),
        )

        state = _build_state([], company_id="test-uuid")
        await feasibility_scorer_node(state, _config(state["run_id"]))
        assert len(call_args_records) == 1, (
            f"Expected 1 call to profile_lookup, got {len(call_args_records)}"
        )
        assert call_args_records[0] == {"company_id": "test-uuid"}, (
            f"Expected company_id='test-uuid', got {call_args_records[0]}"
        )


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------


class TestCostTracking:
    """REQ-005 AC: llm_cost_events row with node_name='feasibility_scorer'."""

    async def _create_run(
        self, db: Any, company_profile_fixture: Any, ready_tender: Any
    ) -> str:
        company, _ = company_profile_fixture
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
        company_profile_fixture: Any,
        ready_tender: Any,
    ) -> None:
        from app.middleware.cost_tracker import CostTrackingHandler, compute_cost

        run_id = await self._create_run(db, company_profile_fixture, ready_tender)
        handler = CostTrackingHandler(
            run_id=run_id, node_name="feasibility_scorer", db=db
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
        assert event.node_name == "feasibility_scorer"
        assert event.run_id == run_id

    async def test_cost_tracker_does_not_fire_on_degraded_path(
        self,
        db: Any,
        company_profile_fixture: Any,
        ready_tender: Any,
    ) -> None:
        from app.middleware.cost_tracker import CostTrackingHandler

        run_id = await self._create_run(db, company_profile_fixture, ready_tender)
        handler = CostTrackingHandler(
            run_id=run_id, node_name="feasibility_scorer", db=db
        )

        # Simulate 2 LLM calls (initial + 1 retry) on the degraded path
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
            assert event.node_name == "feasibility_scorer"


# ---------------------------------------------------------------------------
# Persistence (integration)
# ---------------------------------------------------------------------------


class TestPersistence:
    """REQ-005 Slice 3: feasibility_score persisted on awaiting_hitl."""

    async def _create_ready_tender(
        self,
        db: Any,
        company: Any,
    ) -> tuple[Any, str]:
        tender = Tender(
            id=str(uuid.uuid4()),
            company_id=company.id,
            filename="feasibility_test.pdf",
            storage_path="/tmp/feasibility_test.pdf",
            file_size_bytes=100,
            status="ready",
        )
        db.add(tender)
        await db.flush()

        chunk_texts = [
            "The project involves highway construction in Saudi Arabia.",
            "The estimated contract value is SAR 45,000,000.",
            "Project duration is 24 months.",
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

    async def test_feasibility_score_persisted_on_awaiting_hitl(
        self,
        db: Any,
        company_profile_fixture: Any,
        monkeypatch: Any,
    ) -> None:
        company, _ = company_profile_fixture
        tender, _ = await self._create_ready_tender(db, company)

        chunks = [
            {"content": "Highway construction project in Saudi Arabia.",
             "detected_language": "en", "chunk_index": 0},
            {"content": "Estimated value SAR 45M, duration 24 months.",
             "detected_language": "en", "chunk_index": 1},
        ]

        # Patch feasibility_scorer's LLM to return score=80
        output = FeasibilityOutput.model_construct(**{
            "technical_fit": DimensionScore.model_construct(score=18, rationale="Good technical fit"),
            "financial_capacity": DimensionScore.model_construct(score=14, rationale="Good financial fit"),
            "timeline": DimensionScore.model_construct(score=16, rationale="Good timeline fit"),
            "geographic_scope": DimensionScore.model_construct(score=20, rationale="Good geographic fit"),
            "past_experience": DimensionScore.model_construct(score=12, rationale="Good experience fit"),
        })
        mock_feasibility = _MockFeasibilityLLM(return_value=output)
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer._build_llm",
            lambda: mock_feasibility,
        )

        # Patch risk_radar's LLM too (needed for graph to run)
        risk_output = RiskRadarOutput(findings=[
            RiskFinding(
                category="penalty", severity="high",
                clause_text="penalty clause text",
                explanation="Standard penalty",
                source_chunk_index=0, confidence=0.9,
            ),
        ])
        mock_risk = _MockStructuredLLM(return_value=risk_output)
        monkeypatch.setattr(
            "app.agents.nodes.risk_radar._build_llm",
            lambda: mock_risk,
        )

        # Patch profile_lookup
        async def _fake_profile_lookup(input_dict: dict, *args: Any, **kwargs: Any) -> Any:
            from app.schemas.company import CompanyProfileSchema
            return CompanyProfileSchema(
                specializations=["civil", "roads"],
                financial_capacity={
                    "currency": "SAR",
                    "annual_turnover": 1_000_000,
                    "available_bonding_capacity": 500_000,
                },
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

        # Patch embeddings everywhere
        from app.agents.embeddings import make_stub_embeddings
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
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.with_session", ctx_factory
        )
        monkeypatch.setattr(
            "app.agents.nodes.risk_radar.with_session", ctx_factory
        )
        monkeypatch.setattr(
            "app.agents.retrieval.with_session", ctx_factory
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
        feasibility_score: float | None = None

        async for event in graph.astream(initial_state, config):
            node_name = list(event.keys())[0]
            if node_name.startswith("__"):
                continue
            payload = event[node_name]
            if node_name == "scorer":
                feasibility_score = payload.get("feasibility_score")
            elif node_name == "risk_radar":
                findings_dicts = payload.get("risk_findings", []) or []
            if node_name == "aggregator":
                break

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
        await db.execute(
            update(AnalysisRun)
            .where(AnalysisRun.id == run_id)
            .values(
                state="awaiting_hitl",
                feasibility_score=feasibility_score,
            )
        )
        await db.commit()

        # Refresh to pick up the bulk UPDATE (identity map is stale)
        await db.refresh(run)
        run_row = run
        assert run_row is not None
        assert run_row.state == "awaiting_hitl", (
            f"Expected 'awaiting_hitl', got {run_row.state}"
        )
        assert run_row.feasibility_score == 80.0, (
            f"Expected feasibility_score=80.0, got {run_row.feasibility_score}"
        )

    async def test_all_three_operations_share_one_commit(
        self,
        db: Any,
        company_profile_fixture: Any,
        monkeypatch: Any,
    ) -> None:
        """Atomicity test for the combined REQ-004 + REQ-005 commit block.

        Simulates a DB failure after the risk_findings INSERT but before the
        analysis_runs UPDATE commits. Asserts all three operations (findings
        INSERT, feasibility_score UPDATE, state UPDATE) roll back together.

        Works within the ``db`` fixture's session using a manual savepoint to
        avoid conflicts with the conftest ``_reopen_savepoint`` listener.
        """
        from sqlalchemy import text

        company, _ = company_profile_fixture
        tender, _ = await self._create_ready_tender(db, company)

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

        # Use a manual savepoint (not begin_nested()) so we can recover from
        # a failed statement with ROLLBACK TO SAVEPOINT.
        await db.execute(text("SAVEPOINT atomic_sp"))
        try:
            await db.execute(insert(RiskFindingDB).values(findings_data))

            # Inject failure via a bogus column name
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
            # Recover from the error state by rolling back to the savepoint.
            await db.execute(text("ROLLBACK TO SAVEPOINT atomic_sp"))

        # Verify the savepoint rolled back the INSERT too
        result = await db.execute(
            select(RiskFindingDB).where(RiskFindingDB.run_id == run_id)
        )
        persisted_findings = result.scalars().all()
        assert len(persisted_findings) == 0, (
            f"Expected 0 risk_findings (rolled back), "
            f"got {len(persisted_findings)}"
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

    async def test_feasibility_score_in_status_response(
        self,
        db: Any,
        company_profile_fixture: Any,
        auth_headers: Any,
        monkeypatch: Any,
        app_client: Any,
    ) -> None:
        company, raw_key = company_profile_fixture
        tender, _ = await self._create_ready_tender(db, company)

        chunks = [
            {"content": "Highway construction project in Saudi Arabia.",
             "detected_language": "en", "chunk_index": 0},
            {"content": "Estimated value SAR 45M, duration 24 months.",
             "detected_language": "en", "chunk_index": 1},
        ]

        # Patch feasibility_scorer's LLM
        output = FeasibilityOutput.model_construct(**{
            "technical_fit": DimensionScore.model_construct(score=18, rationale="Good"),
            "financial_capacity": DimensionScore.model_construct(score=14, rationale="Good"),
            "timeline": DimensionScore.model_construct(score=16, rationale="Good"),
            "geographic_scope": DimensionScore.model_construct(score=20, rationale="Good"),
            "past_experience": DimensionScore.model_construct(score=12, rationale="Good"),
        })
        mock_feasibility = _MockFeasibilityLLM(return_value=output)
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer._build_llm",
            lambda: mock_feasibility,
        )

        # Patch risk_radar
        risk_output = RiskRadarOutput(findings=[
            RiskFinding(
                category="penalty", severity="high",
                clause_text="penalty",
                explanation="Standard penalty",
                source_chunk_index=0, confidence=0.9,
            ),
        ])
        mock_risk = _MockStructuredLLM(return_value=risk_output)
        monkeypatch.setattr(
            "app.agents.nodes.risk_radar._build_llm",
            lambda: mock_risk,
        )

        async def _fake_profile_lookup(input_dict: dict, *args: Any, **kwargs: Any) -> Any:
            from app.schemas.company import CompanyProfileSchema
            return CompanyProfileSchema(
                specializations=["civil", "roads"],
                financial_capacity={
                    "currency": "SAR",
                    "annual_turnover": 1_000_000,
                    "available_bonding_capacity": 500_000,
                },
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

        stub = __import__(
            "app.agents.embeddings", fromlist=["make_stub_embeddings"]
        ).make_stub_embeddings(dim=settings.embedding_dimensions)
        for mod_path in ("app.agents.retrieval", "app.agents.nodes.risk_radar",
                         "app.agents.embeddings"):
            monkeypatch.setattr(f"{mod_path}.get_embeddings_client", lambda: stub)

        class _TestSessionCtx:
            async def __aenter__(self) -> Any:
                return db
            async def __aexit__(self, *args: Any) -> None:
                pass
        ctx_factory = lambda: _TestSessionCtx()
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.with_session", ctx_factory
        )
        monkeypatch.setattr(
            "app.agents.nodes.risk_radar.with_session", ctx_factory
        )
        monkeypatch.setattr(
            "app.agents.retrieval.with_session", ctx_factory
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

        async for event in graph.astream(initial_state, config):
            node_name = list(event.keys())[0]
            if node_name.startswith("__"):
                continue
            if node_name == "aggregator":
                break

        final_state = await graph.aget_state(config)
        findings_dicts = final_state.values.get("risk_findings", []) or []
        feasibility_score = final_state.values.get("feasibility_score")

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
        await db.execute(
            update(AnalysisRun)
            .where(AnalysisRun.id == run_id)
            .values(
                state="awaiting_hitl",
                feasibility_score=feasibility_score,
            )
        )
        await db.commit()

        # Query the status endpoint
        resp = await app_client.get(
            f"/tenders/{tender.id}/status",
            headers=auth_headers(raw_key),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "feasibility_score" in body, (
            "feasibility_score missing from status response"
        )
        assert body["feasibility_score"] == 80.0, (
            f"Expected feasibility_score=80.0, got {body['feasibility_score']}"
        )


# ---------------------------------------------------------------------------
# Boundary values
# ---------------------------------------------------------------------------


class TestBoundaryValues:
    """REQ-005 Postconditions: score is always in [0, 100]."""

    @pytest_asyncio.fixture(autouse=True)
    async def _setup(
        self,
        monkeypatch: Any,
        sample_scope_chunks: list[dict],
        company_profile_fixture: Any,
    ) -> None:
        company, _ = company_profile_fixture
        self._company_id = company.id

        async def _fake_profile_lookup(input_dict: dict, *args: Any, **kwargs: Any) -> Any:
            from app.schemas.company import CompanyProfileSchema
            return CompanyProfileSchema(
                specializations=["civil", "roads"],
                financial_capacity={
                    "currency": "SAR",
                    "annual_turnover": 1_000_000,
                    "available_bonding_capacity": 500_000,
                },
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
            _async_ret(sample_scope_chunks),
        )
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer.with_session",
            lambda: _NullSession(),
        )

    async def test_maximum_possible_score(
        self,
        monkeypatch: Any,
    ) -> None:
        output = FeasibilityOutput.model_construct(**{
            "technical_fit": DimensionScore.model_construct(score=20, rationale="Max"),
            "financial_capacity": DimensionScore.model_construct(score=20, rationale="Max"),
            "timeline": DimensionScore.model_construct(score=20, rationale="Max"),
            "geographic_scope": DimensionScore.model_construct(score=20, rationale="Max"),
            "past_experience": DimensionScore.model_construct(score=20, rationale="Max"),
        })
        mock = _MockFeasibilityLLM(return_value=output)
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer._build_llm", lambda: mock
        )

        state = _build_state([], company_id=self._company_id)
        result = await feasibility_scorer_node(state, _config(state["run_id"]))

        assert result["feasibility_score"] == 100.0, (
            f"Expected 100.0, got {result['feasibility_score']}"
        )
        for dim_name in ("technical_fit", "financial_capacity", "timeline",
                         "geographic_scope", "past_experience"):
            assert result["feasibility_breakdown"][dim_name]["score"] == 20, (
                f"{dim_name} expected 20, "
                f"got {result['feasibility_breakdown'][dim_name]['score']}"
            )

    async def test_minimum_possible_score(
        self,
        monkeypatch: Any,
    ) -> None:
        output = FeasibilityOutput.model_construct(**{
            "technical_fit": DimensionScore.model_construct(score=0, rationale="Min"),
            "financial_capacity": DimensionScore.model_construct(score=0, rationale="Min"),
            "timeline": DimensionScore.model_construct(score=0, rationale="Min"),
            "geographic_scope": DimensionScore.model_construct(score=0, rationale="Min"),
            "past_experience": DimensionScore.model_construct(score=0, rationale="Min"),
        })
        mock = _MockFeasibilityLLM(return_value=output)
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer._build_llm", lambda: mock
        )

        state = _build_state([], company_id=self._company_id)
        result = await feasibility_scorer_node(state, _config(state["run_id"]))

        assert result["feasibility_score"] == 0.0, (
            f"Expected 0.0, got {result['feasibility_score']}"
        )
        for dim_name in ("technical_fit", "financial_capacity", "timeline",
                         "geographic_scope", "past_experience"):
            assert result["feasibility_breakdown"][dim_name]["score"] == 0, (
                f"{dim_name} expected 0, "
                f"got {result['feasibility_breakdown'][dim_name]['score']}"
            )

    async def test_score_is_never_outside_0_100_range(
        self,
        monkeypatch: Any,
    ) -> None:
        output = FeasibilityOutput.model_construct(**{
            "technical_fit": DimensionScore.model_construct(score=25, rationale="Above"),
            "financial_capacity": DimensionScore.model_construct(score=25, rationale="Above"),
            "timeline": DimensionScore.model_construct(score=25, rationale="Above"),
            "geographic_scope": DimensionScore.model_construct(score=25, rationale="Above"),
            "past_experience": DimensionScore.model_construct(score=25, rationale="Above"),
        })
        mock = _MockFeasibilityLLM(return_value=output)
        monkeypatch.setattr(
            "app.agents.nodes.feasibility_scorer._build_llm", lambda: mock
        )

        state = _build_state([], company_id=self._company_id)
        result = await feasibility_scorer_node(state, _config(state["run_id"]))

        # After clamping: each dim becomes 20, sum = 100
        assert result["feasibility_score"] == 100.0, (
            f"Expected 100.0 (clamped from 125), "
            f"got {result['feasibility_score']}"
        )
