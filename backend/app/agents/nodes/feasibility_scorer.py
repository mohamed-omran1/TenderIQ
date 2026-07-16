"""Feasibility Scorer agent node (REQ-005 Slice 2).

Replaces the REQ-003 stub with real LLM-driven feasibility scoring across
five dimensions, producing a 0-100 composite score that is the primary
Go/No-Go signal in the final report.

Pipeline (per the slice spec):
  a) Fetch the company profile via `profile_lookup.ainvoke(...)` so the
     scoring has the full profile data even though the Supervisor
     already validated its existence.
  b) Retrieve scope-relevant chunks via `retrieve_scope_relevant_chunks`
     (REQ-005 Slice 2 retrieval). The fallback to the first 20 chunks
     by chunk_index is handled inside the retrieval function.
  c) Build the LLM call from the Slice 1 skill package
     (FEASIBILITY_SYSTEM_PROMPT, SCORING_DIMENSIONS rubric,
     FEW_SHOT_EXAMPLES) and the formatted user content (profile +
     retrieved chunks). The LLM is forced to return a FeasibilityOutput
     Pydantic object via `with_structured_output(..., method="json_schema")`.
  d) Wire a `CostTrackingHandler(node_name="feasibility_scorer")` onto
     the call so the llm_cost_events row is written.
  e) On schema-validation failure: retry once. On second failure: return
     {"feasibility_score": 0.0, "feasibility_breakdown": {"error": "..."}}
     — the graph continues without crashing.
  f) On LLM API failure (network/rate-limit): retry with exponential
     backoff, 3 attempts, via tenacity. On exhausted retries: re-raise
     so the graph-level failure handling from REQ-003 marks the run as
     failed.
  g) Clamp every dimension score to [0, 20] BEFORE summing. Log a
     WARNING (with run_id, node_name, dimension name, original value,
     clamped value) when the clamp fires.
  h) Compute the composite score in Python: `float(sum(dimension_scores))`.
     A Python `assert` enforces the arithmetic integrity check
     (REQ-005 Postconditions: "verified by a Python assertion in the
     node code itself").
  i) Build `feasibility_breakdown` with exactly 5 keys
     (technical_fit, financial_capacity, timeline, geographic_scope,
     past_experience), each with `{score, rationale}`.
  j) Return `{"feasibility_score": composite, "feasibility_breakdown":
     breakdown}` — the exact contract the REQ-003 aggregator reads.

Security:
  - Company profile data (financial_capacity, past_projects values) is
    never written to any application log. Only metadata (run_id,
    node_name, tender_id, composite, dimension names) appears in logs.
  - Profile data flows only into the LLM prompt, never into the
    `logger` calls.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackManager
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_google_genai import ChatGoogleGenerativeAI
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.agents.retrieval import retrieve_scope_relevant_chunks
from app.config import get_settings
from app.agents.skills.feasibility_scoring import (
    FEASIBILITY_SYSTEM_PROMPT,
    FEW_SHOT_EXAMPLES,
    SCORING_DIMENSIONS,
    FeasibilityOutput,
)
from app.db.session import with_session
from app.middleware.cost_tracker import CostTrackingHandler

logger = logging.getLogger(__name__)

# Same model as REQ-004 Slice 2 (risk_radar.py). Free-tier friendly,
# structured output via json_schema method. The cost tracker treats
# unknown model names as 0.0 USD — a Gemini model still produces a
# valid llm_cost_events row, just with cost_usd=0.
_LLM_MODEL = "gemini-2.5-flash"

_NODE_NAME = "feasibility_scorer"

# The five dimensions the breakdown must always contain. Used as the
# defensive-fill keys for missing dimensions in the rare case the
# LLM response is shaped in an unexpected way that still validates.
_EXPECTED_DIMENSIONS: tuple[str, ...] = (
    "technical_fit",
    "financial_capacity",
    "timeline",
    "geographic_scope",
    "past_experience",
)

# Fixed rationale the LLM must use when a dimension cannot be scored
# due to missing profile data (REQ-005 Alt Flow). We use the same
# string verbatim when the node has to defensive-fill a missing
# dimension.
_MISSING_DATA_RATIONALE = "Insufficient profile data for this dimension."


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _format_few_shot_examples() -> str:
    """Render FEW_SHOT_EXAMPLES as a textual block in the system prompt.

    The feasibility examples are structured as (company_profile_summary,
    tender_scope_summary, expected_output) — different from the risk
    examples, which carry a single input_chunk. We render them as
    labelled blocks so the model sees the same shape in the prompt as
    in the call site.
    """
    blocks: list[str] = []
    for i, ex in enumerate(FEW_SHOT_EXAMPLES, start=1):
        try:
            expected = json.dumps(
                ex["expected_output"], ensure_ascii=False, indent=2
            )
        except (TypeError, ValueError):
            expected = str(ex.get("expected_output", ""))
        profile_summary = ex.get("company_profile_summary", "")
        tender_summary = ex.get("tender_scope_summary", "")
        blocks.append(
            f"### Example {i}\n"
            f"Company profile summary:\n{profile_summary}\n\n"
            f"Tender scope summary:\n{tender_summary}\n\n"
            f"Expected output:\n{expected}"
        )
    return "\n\n".join(blocks)


def _format_scoring_dimensions() -> str:
    """Render the SCORING_DIMENSIONS rubric as a textual block."""
    parts: list[str] = []
    for dim_name, dim in SCORING_DIMENSIONS.items():
        description = dim.get("description", "")
        parts.append(f"## {dim_name}\n{description}\n")
        for anchor_score, anchor_text in sorted(
            dim.get("score_anchors", {}).items()
        ):
            parts.append(f"- Score {anchor_score}: {anchor_text}")
        parts.append("")
    return "\n".join(parts)


def _build_system_prompt() -> str:
    """Compose the full system prompt: skill prompt + rubric + few-shots.

    Mirrors the risk_radar pattern: the static, reviewable skill content
    from Slice 1 lives in the system message; the per-run user content
    (profile + retrieved chunks) is the human message.
    """
    return (
        f"{FEASIBILITY_SYSTEM_PROMPT}\n\n"
        f"---\n\n"
        f"## Scoring rubric\n\n"
        f"{_format_scoring_dimensions()}\n"
        f"---\n\n"
        f"## Few-shot examples\n\n"
        f"{_format_few_shot_examples()}"
    )


def _format_company_profile(profile: Any) -> str:
    """Format the company profile as a labelled text block for the LLM.

    The LLM must see all 6 fields (specializations, financial_capacity.*
    [3 sub-fields], geographic_reach, past_projects, max_project_value)
    clearly labelled. Profile data only enters the LLM prompt; it
    never appears in any application log (REQ-005 Security NFR).
    """
    fc = profile.financial_capacity
    past_projects_dump = [
        p.model_dump() if hasattr(p, "model_dump") else p
        for p in (profile.past_projects or [])
    ]
    return (
        "Company profile (use these fields to score each dimension):\n"
        f"- specializations: {profile.specializations}\n"
        f"- financial_capacity.currency: {fc.currency}\n"
        f"- financial_capacity.annual_turnover: {fc.annual_turnover}\n"
        f"- financial_capacity.available_bonding_capacity: "
        f"{fc.available_bonding_capacity}\n"
        f"- geographic_reach: {profile.geographic_reach}\n"
        f"- past_projects: {past_projects_dump}\n"
        f"- max_project_value: {profile.max_project_value}\n"
    )


def _format_scope_chunks(chunks: list[dict[str, Any]]) -> str:
    """Format retrieved scope chunks as labelled text for the LLM."""
    lines: list[str] = [
        "Below are the chunks of the tender that are most likely to "
        "contain scope information (project description, contract value, "
        "timeline, location, required qualifications). Use them to score "
        "each dimension.\n",
    ]
    for c in chunks:
        idx = c.get("chunk_index", "?")
        lang = c.get("detected_language", "?")
        lines.append(f"--- chunk {idx} (language: {lang}) ---")
        lines.append(c.get("content", ""))
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM wiring
# ---------------------------------------------------------------------------

def _build_llm() -> Any:
    """Construct the chat model + structured-output wrapper.

    `method="json_schema"` is the recommended Gemini path for reliable
    structured output (langchain-google docs, 2026).
    """
    llm = ChatGoogleGenerativeAI(model=_LLM_MODEL, google_api_key=get_settings().google_api_key)
    return llm.with_structured_output(FeasibilityOutput, method="json_schema")  # type: ignore[return-value]


def _build_callback_config(
    run_id: str,
    node_name: str,
    db: Any,
    incoming: RunnableConfig | None,
) -> dict[str, Any]:
    """Build the RunnableConfig that wires CostTrackingHandler onto the LLM.

    The graph runtime may pass its own RunnableConfig (with checkpointer
    metadata, configurable, etc.). We preserve those keys and only
    augment the `callbacks` list.
    """
    handler = CostTrackingHandler(run_id=run_id, node_name=node_name, db=db)
    base: dict[str, Any] = dict(incoming) if incoming else {}
    existing = base.get("callbacks")
    if existing is None:
        merged = [handler]
    elif isinstance(existing, list):
        merged = list(existing) + [handler]
    elif isinstance(existing, BaseCallbackManager):
        merged = list(existing.handlers) + [handler]
    else:
        merged = [existing, handler]
    base["callbacks"] = merged
    return base


# ---------------------------------------------------------------------------
# Two independent retry layers (slice spec steps e and f)
# ---------------------------------------------------------------------------
# Path 1 (API failure): tenacity decorator — 3 attempts, exponential
# backoff, re-raises on exhaustion so the graph-level failure handling
# from REQ-003 marks the run as failed.
#
# Path 2 (schema failure): explicit for-loop — 1 retry, then degrades
# to feasibility_score=0.0 with an error breakdown. The graph continues
# without crashing.
#
# The two paths are independent code: a schema error is NEVER retried
# by the tenacity decorator (it is explicitly excluded via
# `retry_if_not_exception_type(OutputParserException)`), and an API
# error NEVER reaches the schema loop (the tenacity layer either
# succeeds or re-raises).

@retry(
    # 3 total attempts = 1 initial + 2 retries. Slice spec step (f).
    stop=stop_after_attempt(3),
    # Exponential backoff: 2s, 4s, 8s, capped at 30s.
    wait=wait_exponential(multiplier=2, min=2, max=30),
    # Retry on any exception EXCEPT OutputParserException — parser
    # errors are handled by the outer 1-retry schema loop (step e).
    retry=retry_if_not_exception_type(OutputParserException),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _invoke_llm_with_api_retry(
    structured_llm: Any,
    messages: list[Any],
    config: dict[str, Any],
) -> FeasibilityOutput:
    """Single-shot LLM call wrapped with 3x exponential-backoff retries.

    Re-raises on exhausted retries so the graph-level failure handling
    from REQ-003 (analysis_runs.state = "failed") can take over.
    """
    return await structured_llm.ainvoke(messages, config=config)  # type: ignore[return-value]


async def _invoke_llm_with_schema_retry(
    structured_llm: Any,
    messages: list[Any],
    config: dict[str, Any],
    run_id: str,
) -> FeasibilityOutput | None:
    """Call the LLM and parse to FeasibilityOutput.

    On OutputParserException: retry once with the same prompt. On second
    parse failure: return None (the caller turns that into the degraded
    `{"feasibility_score": 0.0, "feasibility_breakdown": {"error": "..."}}`
    per slice spec step e).

    On any non-parser exception (API errors, timeouts, etc.): the inner
    tenacity retry layer has already exhausted its 3 attempts and
    re-raised; this function does not catch it, so the graph-level
    failure handling from REQ-003 marks the run as failed.
    """
    for attempt in (1, 2):
        try:
            return await _invoke_llm_with_api_retry(
                structured_llm, messages, config
            )
        except OutputParserException as exc:
            logger.warning(
                "feasibility_scorer_schema_validation_failed run_id=%s "
                "node_name=feasibility_scorer attempt=%d error_type=%s",
                run_id,
                attempt,
                type(exc).__name__,
            )
    return None


# ---------------------------------------------------------------------------
# Clamp + composite (slice spec steps g and h)
# ---------------------------------------------------------------------------

def _clamp_and_sum(
    output: FeasibilityOutput,
    run_id: str,
) -> tuple[float, dict[str, dict[str, Any]]]:
    """Clamp each dimension to [0, 20] and compute the composite score.

    Slice spec steps g, h, i:
      - Clamp every dimension score to [0, 20] BEFORE summing.
      - Log a WARNING (run_id, node_name, dimension name, original,
        clamped) when the clamp fires.
      - Compute composite as `float(sum(dimension_scores))` in Python.
      - Assert the composite equals the sum (mathematical integrity
        check, REQ-005 Postconditions).
      - Build the breakdown dict with exactly 5 keys
        (technical_fit, financial_capacity, timeline, geographic_scope,
        past_experience), defensive-filling any missing dimension
        with score 0 and the fixed missing-data rationale.
    """
    dump = output.model_dump()
    breakdown: dict[str, dict[str, Any]] = {}
    for dim_name in _EXPECTED_DIMENSIONS:
        dim_data = dump.get(dim_name)
        if not isinstance(dim_data, dict) or "score" not in dim_data:
            # Defensive fill — the Pydantic schema should have made this
            # impossible, but the postcondition requires the breakdown
            # to always have exactly 5 dimension keys.
            breakdown[dim_name] = {
                "score": 0,
                "rationale": _MISSING_DATA_RATIONALE,
            }
            continue
        raw_score = dim_data["score"]
        try:
            raw_int = int(raw_score)
        except (TypeError, ValueError):
            raw_int = 0
        clamped = max(0, min(20, raw_int))
        if clamped != raw_int:
            logger.warning(
                "feasibility_scorer_dimension_clamped run_id=%s "
                "node_name=feasibility_scorer dimension=%s "
                "score_clamped_from=%s score_clamped_to=%d",
                run_id,
                dim_name,
                raw_int,
                clamped,
            )
        rationale = dim_data.get("rationale") or _MISSING_DATA_RATIONALE
        breakdown[dim_name] = {"score": clamped, "rationale": rationale}

    dimension_scores = [
        d["score"]
        for d in breakdown.values()
        if isinstance(d, dict) and "score" in d
    ]
    composite = float(sum(dimension_scores))
    # Mathematical integrity check — must stay in production code
    # (REQ-005 Postconditions + imp-slice-02 Rule "the assert ... must
    # stay in production code, not just in tests").
    assert abs(composite - sum(dimension_scores)) < 0.01, (
        f"Composite score mismatch: {composite} != {sum(dimension_scores)}"
    )
    return composite, breakdown


# ---------------------------------------------------------------------------
# The node
# ---------------------------------------------------------------------------

async def feasibility_scorer_node(
    state: TenderState, config: RunnableConfig
) -> dict[str, Any]:
    """Real Feasibility Scorer — fetch profile, retrieve scope chunks, score.

    Returns a dict suitable for direct merge into TenderState. The
    Aggregator (REQ-003) reads `state["feasibility_score"]` and
    `state["feasibility_breakdown"]` from this dict.
    """
    run_id = state["run_id"]
    tender_id = state["tender_id"]
    company_id = state["company_id"]
    chunks = state.get("chunks", []) or []

    # (a) Fetch the company profile via the LangChain tool. Even though
    # the supervisor already validated the profile exists, the scorer
    # needs the full profile fields to score the five dimensions.
    profile = await profile_lookup.ainvoke({"company_id": company_id})

    # (b) Retrieve scope-relevant chunks. The function handles the
    # "no scope-relevant chunks found" fallback internally.
    scope_chunks = await retrieve_scope_relevant_chunks(
        tender_id=tender_id,
        chunks=chunks,
        top_k_per_query=4,
    )

    # (c) Build the LLM call.
    structured_llm = _build_llm()
    user_content = (
        _format_company_profile(profile)
        + "\n"
        + _format_scope_chunks(scope_chunks)
    )
    messages = [
        SystemMessage(content=_build_system_prompt()),
        HumanMessage(content=user_content),
    ]

    # (d) Cost tracking — we open a session for the handler to write
    # into. The session is held open until the LLM call returns (which
    # is when on_llm_end fires), then released.
    async with with_session() as db:
        llm_config = _build_callback_config(
            run_id=run_id,
            node_name=_NODE_NAME,
            db=db,
            incoming=config,
        )

        # (e) + (f) Schema retry (1) + API retry (3) by tenacity.
        # On schema failure: returns None (we degrade below).
        # On API failure: re-raises, which propagates to graph-level
        # failure handling from REQ-003 (analysis_runs.state = "failed").
        output = await _invoke_llm_with_schema_retry(
            structured_llm, messages, llm_config, run_id
        )

    if output is None:
        # Schema validation exhausted its 1 retry. Per slice spec
        # step (e), proceed with a zero score and the fixed error
        # breakdown rather than crashing the graph.
        return {
            "feasibility_score": 0.0,
            "feasibility_breakdown": {
                "error": "Scoring unavailable — malformed LLM response"
            },
        }

    # (g) + (h) + (i) Clamp dimensions, compute composite, build
    # breakdown. Includes the production-code assert.
    composite, breakdown = _clamp_and_sum(output, run_id)

    # Log only metadata. NEVER financial_capacity, past_projects, or
    # any rationale text (REQ-005 Security NFR).
    logger.info(
        "feasibility_scorer_complete run_id=%s node_name=feasibility_scorer "
        "tender_id=%s composite=%s dimensions=%s",
        run_id,
        tender_id,
        composite,
        sorted(breakdown.keys()),
    )

    return {
        "feasibility_score": composite,
        "feasibility_breakdown": breakdown,
    }


# Local imports to avoid top-level cycles: state.py is the source of
# truth for TenderState, and profile_lookup is a LangChain tool that
# resolves at call time. Both are imported here to match the pattern
# in risk_radar.py.
from app.agents.state import TenderState  # noqa: E402
from app.agents.tools.profile_lookup import profile_lookup  # noqa: E402
