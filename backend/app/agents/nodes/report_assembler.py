"""Report Assembler agent node (REQ-008 Slice 2).

Replaces the REQ-003 stub with a real LLM-driven Go/No-Go brief synthesis
node. This node runs AFTER the HITL gate — the graph is compiled with
``interrupt_before=["report_assembler"]``, so it only fires once the
analyst has approved or overridden the feasibility score (REQ-007).

Pipeline (per the slice spec):
  1. Determine the effective feasibility score — read
     ``hitl_override_score`` first, fall back to ``feasibility_score``
     only if the override is ``None``. The check uses ``is not None``,
     never a falsy check, because ``hitl_override_score = 0.0`` is a
     valid analyst decision (a hard decline) and must be used as-is.
  2. Compute Go/No-Go in Python via ``compute_go_no_go(effective_score)``.
     The LLM never decides Go/No-Go — it receives the determination as
     input and uses it verbatim.
  3. Build the report context dict from ``state["aggregated_results"]``,
     with risks sorted by severity and capped at the top 5.
  4. Call the LLM with the ``REPORT_SYNTHESIS_PROMPT`` system message,
     ``REPORT_FEW_SHOT_EXAMPLES`` as prior Human/AI message pairs, and
     the formatted report context as the final Human message. The LLM
     is forced to return a ``ReportOutput`` Pydantic object via
     ``with_structured_output(..., method="json_schema")``.
     ``CostTrackingHandler(node_name="report_assembler")`` is wired
     onto the call so an ``llm_cost_events`` row is written.
  5. On schema-validation failure: retry once. On second failure:
     build a fallback report dict and return it. Log a WARNING that
     contains only metadata (run_id, attempt, error_type).
  6. On LLM API failure (network/rate-limit): retry with exponential
     backoff, 3 attempts, via tenacity. On exhausted retries: build
     a fallback report dict and return it. Log an ERROR. **Never
     raise** — REQ-008 mandates that a report-assembly failure must
     never revert the run to "failed" because the analyst has already
     committed their HITL decision and cannot re-approve.
  7. On success: serialise the structured output to a JSON-compatible
     dict, log metadata only, and return it.

Security:
  - ``financial_summary`` values (contract_value, bond amounts,
    currency codes, LD rates, retention rates, advance payment
    amounts) NEVER appear in any application log line. Only metadata
    (run_id, go_no_go, score, override flag, counts, presence flags)
    is logged. Consistent with REQ-006 security rules.
  - The Python-computed ``effective_score`` and ``go_no_go`` are
    never recomputed by the LLM. The LLM is told the decision in
    advance and is forbidden to override it (per the system prompt
    in the skill package).
  - The fallback report always carries the Python-computed
    ``effective_score`` and ``go_no_go`` — never the placeholder
    values from the FALLBACK_REPORT constant.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackManager
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_google_genai import ChatGoogleGenerativeAI
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.agents.skills.report_synthesis import (
    FALLBACK_REPORT,
    REPORT_FEW_SHOT_EXAMPLES,
    REPORT_SYNTHESIS_PROMPT,
    ReportOutput,
    compute_go_no_go,
)
from app.db.session import with_session
from app.middleware.cost_tracker import CostTrackingHandler

logger = logging.getLogger(__name__)

# Same model as REQ-004 / REQ-005 / REQ-006 (risk_radar, feasibility_scorer,
# financial_analyst). Free-tier friendly, structured output via
# json_schema method. The cost tracker treats unknown model names as
# 0.0 USD — a Gemini model still produces a valid llm_cost_events row,
# just with cost_usd=0.
_LLM_MODEL = "gemini-2.5-flash"

_NODE_NAME = "report_assembler"

# Truncation length for any log entry that quotes a raw LLM response
# after a schema validation failure. Keeps logs from accidentally
# spilling structured-output JSON that contains risk-finding text.
_RAW_LOG_PREVIEW_CHARS = 200

# Severity ordering for sorting risk findings (critical first, low last).
# Used by ``_select_top_risks`` to pick the top 5 by severity.
_SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

# Cap on the number of risk findings passed into the report context.
# The slice spec + the ReportOutput schema (max_length=5) both cap at 5.
_TOP_RISKS_LIMIT = 5


# ---------------------------------------------------------------------------
# Step 1 — effective_score determination
# ---------------------------------------------------------------------------


def _resolve_effective_score(
    state: Any,
) -> tuple[float, float, bool]:
    """Determine ``effective_score``, ``ai_score``, and ``is_analyst_override``.

    CRITICAL: the override check uses ``is not None`` — never a falsy
    check. ``hitl_override_score = 0.0`` is a valid analyst decision
    (a hard decline) and MUST be used as the effective score. A
    falsy check (e.g. ``if state["hitl_override_score"]:``) would
    incorrectly treat 0.0 as None and fall back to the AI score.

    Returns a 3-tuple ``(effective_score, ai_score, is_analyst_override)``.
    """
    if state["hitl_override_score"] is not None:
        effective_score = float(state["hitl_override_score"])
        is_analyst_override = True
        # The AI's original feasibility score (always present in state
        # at this point — the aggregator has already run). The ``or 0.0``
        # is a defensive fallback; under normal flow feasibility_score
        # is set by the Feasibility Scorer node.
        ai_score = float(state["feasibility_score"] or 0.0)
    else:
        effective_score = float(state["feasibility_score"] or 0.0)
        is_analyst_override = False
        ai_score = effective_score
    return effective_score, ai_score, is_analyst_override


# ---------------------------------------------------------------------------
# Step 3 — top-risks selection
# ---------------------------------------------------------------------------


def _select_top_risks(risk_findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort risk findings by severity (critical first) and cap at top 5.

    Unknown severities sort to the end (treated as "low" — index 3).
    The slice spec's ReportOutput schema enforces ``max_length=5`` on
    ``risk_summary``; we pre-cap here so the LLM context matches.
    """
    return sorted(
        risk_findings,
        key=lambda r: _SEVERITY_ORDER.get(r.get("severity", "low"), 3),
    )[:_TOP_RISKS_LIMIT]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _format_few_shot_examples_as_messages() -> list[Any]:
    """Render ``REPORT_FEW_SHOT_EXAMPLES`` as prior Human/AI message pairs.

    Each example becomes one HumanMessage (rendered context as a labelled
    block) followed by one AIMessage (the expected ``ReportOutput`` as a
    JSON dump). This is the same pattern the financial_analyst uses for
    its FEW_SHOT_EXAMPLES.

    The report_assembler's few-shot shape is ``(context, expected_output,
    note)`` — different from risk_radar's ``(input_chunk, expected_output)``
    and feasibility_scorer's ``(company_profile_summary, tender_scope_summary,
    expected_output)``. The context dict is rendered field-by-field so
    every label is explicit and the LLM can see the input shape.
    """
    out: list[Any] = []
    for i, ex in enumerate(REPORT_FEW_SHOT_EXAMPLES, start=1):
        context = ex.get("context", {})
        note = ex.get("note", "")
        try:
            expected_json = json.dumps(
                ex.get("expected_output", {}), ensure_ascii=False, indent=2
            )
        except (TypeError, ValueError):
            expected_json = str(ex.get("expected_output", ""))

        user_lines: list[str] = [
            f"### Example {i}",
            f"Note: {note}",
            "",
            "Report context:",
        ]
        for key, value in context.items():
            user_lines.append(f"- {key}: {value}")
        user_lines.append("")
        user_lines.append("Return your answer as a ReportOutput JSON object.")
        out.append(HumanMessage(content="\n".join(user_lines)))
        out.append(AIMessage(content=expected_json))
    return out


def _format_report_context(report_context: dict[str, Any]) -> str:
    """Render the per-run report context as a clearly-labelled text block.

    Per the slice spec: "User content: formatted string of report_context
    dict, clearly labelled with section headers for each field." Every
    field the LLM needs to write the ``ReportOutput`` is rendered with
    a section header so the model can map context to schema fields.

    Risk-finding text and financial values are passed in full — the LLM
    needs the exact numbers to write ``financial_highlights`` and
    ``risk_summary``. The same values must NEVER appear in any log line
    (the report_assembler only logs metadata — see the node body).
    """
    effective_score = report_context.get("effective_score")
    go_no_go = report_context.get("go_no_go")
    is_analyst_override = report_context.get("is_analyst_override")
    ai_score = report_context.get("ai_score")
    source_languages = report_context.get("source_languages", [])
    top_risks = report_context.get("top_risks", [])
    feasibility_breakdown = report_context.get("feasibility_breakdown", {})
    financial_summary = report_context.get("financial_summary", {})

    lines: list[str] = [
        "# Report Assembly Context",
        "",
        "Synthesise the following structured inputs into a Go/No-Go brief. "
        "Return your answer using the ReportOutput JSON schema. The Go/No-Go "
        "recommendation below is already computed in Python — use it "
        "exactly as given; do not override it with your own judgment.",
        "",
        "## Effective Feasibility Score",
        f"{effective_score} (this is the score to use in the report)",
        "",
        "## Go/No-Go Recommendation (computed in Python — do not override)",
        f"{go_no_go}",
        "",
        "## Is Analyst Override",
        f"{is_analyst_override}",
    ]
    if is_analyst_override and ai_score is not None:
        # The LLM needs BOTH the AI's original score and the override
        # score to write the mandatory analyst_note. The override score
        # IS the effective score when is_analyst_override is True.
        lines.append(f"AI's original feasibility score (ai_score): {ai_score}")
        lines.append(
            f"Analyst override score (override_score = effective_score): "
            f"{effective_score}"
        )
    lines.append("")

    lines.append("## Source Languages Detected")
    lines.append(f"{source_languages}")
    lines.append("")

    lines.append(
        f"## Top {len(top_risks)} Risks "
        "(sorted by severity: critical first, then high, medium, low)"
    )
    for i, r in enumerate(top_risks, start=1):
        category = r.get("category", "?")
        severity = r.get("severity", "?")
        explanation = r.get("explanation") or r.get("clause_text", "")
        lines.append(f"{i}. [{severity}] {category}: {explanation}")
    lines.append("")

    lines.append("## Feasibility Breakdown (5 dimensions, each scored 0-20)")
    if isinstance(feasibility_breakdown, dict):
        for dim_name, dim_data in feasibility_breakdown.items():
            if isinstance(dim_data, dict) and "score" in dim_data:
                rationale = dim_data.get("rationale", "")
                lines.append(
                    f"- {dim_name}: {dim_data['score']}/20 — {rationale}"
                )
            else:
                lines.append(f"- {dim_name}: {dim_data}")
    lines.append("")

    lines.append("## Financial Summary")
    if isinstance(financial_summary, dict) and financial_summary:
        for key, value in financial_summary.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("(no financial summary available)")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM wiring
# ---------------------------------------------------------------------------


def _build_llm() -> Any:
    """Construct the chat model + structured-output wrapper.

    ``method="json_schema"`` is the recommended Gemini path for reliable
    structured output (langchain-google docs, 2026). The Google GenAI
    SDK automatically resolves ``$ref`` references and inlines ``$defs``
    definitions, so the nested Pydantic schema
    (``ReportOutput`` → ``list[RiskSummaryItem]``) is supported natively.
    """
    llm = ChatGoogleGenerativeAI(model=_LLM_MODEL, google_api_key=get_settings().google_api_key)
    return llm.with_structured_output(ReportOutput, method="json_schema")  # type: ignore[return-value]


def _build_callback_config(
    run_id: str,
    node_name: str,
    db: Any,
    incoming: RunnableConfig | None,
) -> dict[str, Any]:
    """Build the RunnableConfig that wires CostTrackingHandler onto the LLM.

    The graph runtime may pass its own RunnableConfig (with checkpointer
    metadata, configurable, etc.). We preserve those keys and only
    augment the ``callbacks`` list.
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
# Steps 5 + 6 — schema retry + API retry, both ending in fallback
# ---------------------------------------------------------------------------
# Path 1 (API failure): tenacity decorator — 3 attempts with exponential
# backoff. Re-raises on exhaustion.
#
# Path 2 (schema failure): explicit for-loop — 1 retry, then returns None
# so the caller builds the fallback.
#
# Both paths converge on None → fallback report. The difference from
# REQ-004/005/006 is that on API exhaustion we DO NOT re-raise — REQ-008
# mandates that report-assembly failure must not revert a run to
# "failed" because the analyst has already committed their HITL
# decision and cannot re-approve.


@retry(
    # 3 total attempts = 1 initial + 2 retries. Slice spec step (6).
    stop=stop_after_attempt(3),
    # Exponential backoff: 2s, 4s, 8s, capped at 30s.
    wait=wait_exponential(multiplier=2, min=2, max=30),
    # Retry on any exception EXCEPT OutputParserException — parser
    # errors are handled by the outer 1-retry schema loop (step 5).
    retry=retry_if_not_exception_type(OutputParserException),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _invoke_llm_with_api_retry(
    structured_llm: Any,
    messages: list[Any],
    config: dict[str, Any],
) -> ReportOutput:
    """Single-shot LLM call wrapped with 3x exponential-backoff retries.

    Re-raises on exhausted retries. The outer caller
    (``_invoke_with_fallback``) catches the re-raise and produces a
    fallback report — this is the key difference from
    REQ-004/005/006, which let the graph-level failure handling mark
    the run as failed. REQ-008 requires that report-assembly failures
    never revert a run to "failed" because the analyst has already
    committed their HITL decision and cannot re-approve.
    """
    return await structured_llm.ainvoke(messages, config=config)  # type: ignore[return-value]


async def _invoke_with_fallback(
    structured_llm: Any,
    messages: list[Any],
    config: dict[str, Any],
    run_id: str,
) -> ReportOutput | None:
    """Combined retry: 1 schema retry wrapped around 3 API attempts.

    Returns ``None`` on any failure (schema or API) — the caller turns
    ``None`` into a fallback report. NEVER raises — per REQ-008, a
    report-assembly failure must not revert the run to "failed".
    """
    last_error: Exception | None = None
    for schema_attempt in (1, 2):
        try:
            return await _invoke_llm_with_api_retry(
                structured_llm, messages, config
            )
        except OutputParserException as exc:
            # Schema validation failure (slice spec step 5). Retry once
            # with the same prompt. On second failure, fall through
            # and return None so the caller builds the fallback report.
            last_error = exc
            logger.warning(
                "report_assembler_schema_validation_failed run_id=%s "
                "node_name=report_assembler attempt=%d error_type=%s",
                run_id,
                schema_attempt,
                type(exc).__name__,
            )
        except Exception as exc:
            # API failure (network/rate-limit/etc.) — the inner
            # tenacity retry layer has already exhausted its 3 attempts
            # and re-raised here. Per REQ-008, we DO NOT re-raise: the
            # analyst has already committed their HITL decision, so
            # a report-assembly failure must not revert the run to
            # "failed". Return None so the caller builds the fallback.
            last_error = exc
            logger.error(
                "report_assembler_llm_api_failed run_id=%s "
                "node_name=report_assembler error_type=%s "
                "— using fallback report",
                run_id,
                type(exc).__name__,
            )
            return None
    # Schema validation exhausted its 1 retry. Log a short, non-financial
    # preview of the error and return None so the caller builds the
    # fallback report.
    preview = repr(last_error)[:_RAW_LOG_PREVIEW_CHARS] if last_error else ""
    logger.warning(
        "report_assembler_schema_validation_exhausted run_id=%s "
        "node_name=report_assembler raw_preview=%s",
        run_id,
        preview,
    )
    return None


# ---------------------------------------------------------------------------
# Fallback report construction (Steps 5 + 6 — shared between both paths)
# ---------------------------------------------------------------------------


def _build_fallback_report(
    effective_score: float,
    go_no_go_value: str,
) -> dict[str, Any]:
    """Build a fallback report dict with the Python-computed score / GoNoGo.

    The slice spec mandates: "The fallback report must always have
    effective_score and go_no_go set to the Python-computed values —
    never use the FALLBACK_REPORT constants directly without updating
    these two fields." The FALLBACK_REPORT constant from the skill
    package is a template (effective_score=0.0, go_no_go="REVIEW" are
    placeholders); we copy it and overwrite both fields with the
    actual computed values for this run.
    """
    fallback = dict(FALLBACK_REPORT)
    fallback["effective_score"] = effective_score
    fallback["go_no_go"] = go_no_go_value
    return fallback


# ---------------------------------------------------------------------------
# The node
# ---------------------------------------------------------------------------


async def report_assembler_node(
    state: Any, config: RunnableConfig
) -> dict[str, Any]:
    """Real Report Assembler — Go/No-Go + LLM synthesis + fallback safety net.

    Runs AFTER the HITL gate (``interrupt_before=["report_assembler"]``).
    Reads the effective feasibility score (override first, then AI score),
    computes Go/No-Go in Python (never by the LLM), and asks the LLM to
    synthesize a structured ``ReportOutput`` from the upstream analysis.

    This node MUST NEVER raise an exception under any circumstances —
    both schema failures and API failures produce a fallback report
    (REQ-008). This is a hard requirement because the analyst has
    already committed their HITL decision and cannot re-approve if
    report assembly fails.

    Returns ``{"final_report": <dict>}`` — the LangGraph runtime merges
    this into ``TenderState["final_report"]``.
    """
    run_id = state["run_id"]

    # --- Step 1: Determine effective score (the "is not None" check) ---
    effective_score, ai_score, is_analyst_override = _resolve_effective_score(
        state
    )

    # --- Step 2: Compute Go/No-Go in Python. Never by the LLM. -------
    go_no_go = compute_go_no_go(effective_score)

    # --- Step 3: Build the report context for the LLM. ---------------
    aggregated = state.get("aggregated_results") or {}
    risk_findings = aggregated.get("risk_findings", []) or []
    feasibility_breakdown = aggregated.get("feasibility_breakdown", {}) or {}
    financial_summary = aggregated.get("financial_summary", {}) or {}
    top_risks = _select_top_risks(risk_findings)

    report_context: dict[str, Any] = {
        "effective_score": effective_score,
        "go_no_go": go_no_go.value,
        "is_analyst_override": is_analyst_override,
        "ai_score": ai_score if is_analyst_override else None,
        "top_risks": top_risks,
        "feasibility_breakdown": feasibility_breakdown,
        "financial_summary": financial_summary,
        "source_languages": state.get("source_languages", []),
    }

    # --- Step 4: Build the LLM call. ---------------------------------
    structured_llm = _build_llm()

    # System prompt (skill package, slice 1) + few-shot examples as
    # prior Human/AI message pairs + the per-run report context as
    # the final user message.
    messages: list[Any] = [SystemMessage(content=REPORT_SYNTHESIS_PROMPT)]
    messages.extend(_format_few_shot_examples_as_messages())
    messages.append(HumanMessage(content=_format_report_context(report_context)))

    # Cost tracking — open a session for the handler to write into.
    # The session is held open until the LLM call returns (when
    # on_llm_end fires), then released. The handler is parameterised
    # by node_name="report_assembler" so the /analytics/cost endpoint
    # can break down spend per agent.
    async with with_session() as db:
        llm_config = _build_callback_config(
            run_id=run_id,
            node_name=_NODE_NAME,
            db=db,
            incoming=config,
        )

        # --- Steps 5 + 6: combined schema + API retry. --------------
        # Returns None on any failure (schema OR API). The block below
        # turns None into a fallback report. Never raises.
        output = await _invoke_with_fallback(
            structured_llm, messages, llm_config, run_id
        )

    if output is None:
        # Schema or API failure path. Build a fallback report carrying
        # the Python-computed effective_score and go_no_go. NEVER raise
        # — per REQ-008, the run must always transition to "complete".
        fallback = _build_fallback_report(
            effective_score=effective_score,
            go_no_go_value=go_no_go.value,
        )
        return {"final_report": fallback}

    # --- Step 7: Success path — return the structured report. ---------
    report_dict = output.model_dump()

    # Log metadata only. NEVER any financial_summary value, currency,
    # monetary amount, risk-finding text, or chunk content (REQ-006 /
    # REQ-008 Security NFRs).
    feasibility_dim_count = (
        len(feasibility_breakdown) if isinstance(feasibility_breakdown, dict) else 0
    )
    has_financial_summary = bool(financial_summary)
    logger.info(
        "report_assembler_complete run_id=%s node_name=report_assembler "
        "go_no_go=%s score=%s override=%s risks=%d feasibility_dims=%d "
        "has_financial_summary=%s",
        run_id,
        go_no_go.value,
        effective_score,
        is_analyst_override,
        len(top_risks),
        feasibility_dim_count,
        has_financial_summary,
    )

    return {"final_report": report_dict}


# Local import to avoid a top-level cycle: state.py is the source of
# truth for TenderState, and report_assembler.py must not import from
# anywhere that re-exports it. Matches the pattern in risk_radar.py,
# feasibility_scorer.py, and financial_analyst.py.
from app.agents.state import TenderState  # noqa: E402, F401
