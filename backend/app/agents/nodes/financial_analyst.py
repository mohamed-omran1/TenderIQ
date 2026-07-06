"""Financial Analyst agent node (REQ-006 Slice 2).

Replaces the REQ-003 stub with real LLM-driven bond and commitment
extraction. Runs as the third parallel branch in the REQ-003 fan-out
alongside REQ-004 (Risk Radar) and REQ-005 (Feasibility Scorer) and
writes the structured financial summary to state["financial_summary"],
which the REQ-003 Aggregator and the REQ-008 Report Assembler consume.

Pipeline (per the slice spec):
  a) Call `retrieve_financial_chunks(...)` over the in-state chunks.
     The "no financial-relevant chunks" fallback to the first 15
     chunks by chunk_index is handled inside the retrieval function.
  b) Build the LLM call from the Slice 1 skill package
     (FINANCIAL_SYSTEM_PROMPT, FEW_SHOT_EXAMPLES, FinancialOutput
     schema). Few-shot examples are passed as prior Human/AI message
     pairs (slice spec step b). The LLM is forced to return a
     FinancialOutput Pydantic object via
     `with_structured_output(..., method="json_schema")`.
  c) Wire a `CostTrackingHandler(node_name="financial_analyst")` onto
     the call so the llm_cost_events row is written.
  d) On schema-validation failure: retry once with the same prompt.
     On second failure: return
     {"financial_summary": {"error": "...", "bonds": [], ...}}
     — the graph continues without crashing.
  e) On LLM API failure (network/rate-limit): retry with exponential
     backoff, 3 attempts, via tenacity. On exhausted retries: re-raise
     so the graph-level failure handling from REQ-003 marks the run
     as failed.
  f) On success: post-process via `postprocess_financial_output`
     (currency normalisation, percentage→absolute bond amounts).
  g) Log metadata only — never any monetary value, currency code,
     contract amount, or chunk text.

Security:
  - Financial values (amount_value, amount_currency, contract amounts,
    bond amounts, LD rate/cap, advance payment) NEVER appear in any
    application log line. Only metadata (run_id, node_name, tender_id,
    count of bonds, count of milestones, presence of LD) is logged.
  - Currency normalisation warnings reference the FIELD NAME only —
    never the actual value. The post-processor walks the structured
    output in-process; nothing reaches the log layer.
  - The pure-function `validate_and_normalise_currency` is independently
    unit-testable: no I/O, no DB, no logging.
"""

from __future__ import annotations

import json
import logging
from typing import Any

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

from app.agents.retrieval import retrieve_financial_chunks
from app.agents.skills.financial_extraction import (
    CURRENCY_NORMALISATION,
    FEW_SHOT_EXAMPLES,
    FINANCIAL_SYSTEM_PROMPT,
    FinancialOutput,
    MonetaryValue,
)
from app.db.session import with_session
from app.middleware.cost_tracker import CostTrackingHandler

logger = logging.getLogger(__name__)

# Same model as REQ-004 / REQ-005 (risk_radar, feasibility_scorer).
# Free-tier friendly, structured output via json_schema method. The
# cost tracker treats unknown model names as 0.0 USD — a Gemini model
# still produces a valid llm_cost_events row, just with cost_usd=0.
_LLM_MODEL = "gemini-2.5-flash"

_NODE_NAME = "financial_analyst"

# Truncation length for any log entry that quotes a raw LLM response
# after a schema validation failure. Keeps logs from accidentally
# spilling structured-output JSON that contains monetary values.
_RAW_LOG_PREVIEW_CHARS = 200

# ISO 4217 currency codes accepted without normalisation. The slice
# spec lists "at minimum" this set. Any code in this set is treated
# as a valid ISO 4217 code and returned unchanged. Codes outside this
# set AND outside CURRENCY_NORMALISATION fall through to the
# "UNKNOWN" / needs_review=True path.
_VALID_ISO_CODES: frozenset[str] = frozenset(
    {
        "SAR",
        "AED",
        "EGP",
        "USD",
        "QAR",
        "KWD",
        "BHD",
        "OMR",
        "EUR",
        "GBP",
    }
)


# ---------------------------------------------------------------------------
# Currency validation — pure function (slice spec step 2)
# ---------------------------------------------------------------------------


def validate_and_normalise_currency(currency_str: str) -> tuple[str, bool]:
    """Normalise a currency string to an ISO 4217 code.

    Pure function — no I/O, no DB, no logging. Independently unit-
    testable.

    Returns `(normalised_iso_code, needs_review)`:
      - If `currency_str` is already a valid ISO 4217 code (in the
        allow-list): return `(currency_str, False)`.
      - If `currency_str` is in CURRENCY_NORMALISATION: return
        `(mapped_code, False)`.
      - Otherwise: return `("UNKNOWN", True)`.

    Never raises — always returns a valid tuple. None / empty / non-
    string inputs degrade gracefully to the "UNKNOWN" fallback.
    """
    if not currency_str or not isinstance(currency_str, str):
        return ("UNKNOWN", True)

    candidate = currency_str.strip()
    if not candidate:
        return ("UNKNOWN", True)

    # Case-sensitive ISO code check (ISO 4217 codes are uppercase).
    if candidate in _VALID_ISO_CODES:
        return (candidate, False)

    # Look up in the normalisation map. The map is case-sensitive
    # because the Arabic keys would not match upper-cased English
    # forms and the English forms are already stored in their natural
    # case (e.g. "Riyals", "Dollars").
    if candidate in CURRENCY_NORMALISATION:
        return (CURRENCY_NORMALISATION[candidate], False)

    return ("UNKNOWN", True)


# ---------------------------------------------------------------------------
# Post-processing — currency normalisation + percentage bond resolution
# (slice spec step 3)
# ---------------------------------------------------------------------------


def _normalise_monetary(
    value: MonetaryValue | None,
    field_name: str,
    run_id: str,
) -> None:
    """Apply `validate_and_normalise_currency` to one MonetaryValue in place.

    Logs a single WARNING when `needs_review` becomes True — naming the
    FIELD, never the value. Mutates `value` directly.
    """
    if value is None:
        return
    normalised, needs_review = validate_and_normalise_currency(value.currency)
    if normalised != value.currency:
        value.currency = normalised
    if needs_review:
        value.needs_review = True
        logger.warning(
            "run_id=%s currency normalisation required for %s — set to UNKNOWN",
            run_id,
            field_name,
        )


def postprocess_financial_output(
    output: FinancialOutput,
    run_id: str,
) -> dict:
    """Validate and normalise the LLM output before writing to TenderState.

    Slice spec step 3:
      a) For every MonetaryValue (contract_value, all bond amounts, LD
         rate and cap, advance_payment): apply currency normalisation
         and update the value in place.
      b) For any MonetaryValue where needs_review becomes True: log
         ONE WARNING per field, naming the field only — never the
         value.
      c) For bonds expressed as percentage only (amount.value == 0.0
         and percentage is not None): if contract_value is known and
         not needs_review, compute amount.value = (percentage / 100) *
         contract_value.value, set amount.currency = contract_value.
         currency, set amount.needs_review = False. Otherwise leave
         amount.value = 0.0 and set amount.needs_review = True.
      d) Return `output.model_dump()` as the financial_summary dict.

    The function NEVER raises. Any unexpected exception during
    post-processing is swallowed and we fall back to the raw
    model_dump() of the output — the caller still gets a dict, and
    the rest of the graph continues.
    """
    try:
        # ---- (a) + (b): normalise currencies ---------------------
        _normalise_monetary(output.contract_value, "contract_value", run_id)
        _normalise_monetary(output.advance_payment, "advance_payment", run_id)

        if output.liquidated_damages is not None:
            ld = output.liquidated_damages
            _normalise_monetary(ld.rate, "liquidated_damages.rate", run_id)
            _normalise_monetary(ld.cap, "liquidated_damages.cap", run_id)

        for idx, bond in enumerate(output.bonds):
            _normalise_monetary(bond.amount, f"bonds[{idx}].amount", run_id)

        # ---- (c): resolve percentage-only bonds ------------------
        contract = output.contract_value
        contract_known = (
            contract is not None and not contract.needs_review and contract.currency != "UNKNOWN"
        )
        for bond in output.bonds:
            if bond.amount.value != 0.0:
                continue
            if bond.percentage is None:
                continue
            if contract_known and contract is not None:
                bond.amount.value = (bond.percentage / 100.0) * contract.value
                bond.amount.currency = contract.currency
                bond.amount.needs_review = False
            else:
                # Contract value is unknown or needs review — leave
                # amount.value at 0.0 and flag the bond for human
                # review.
                bond.amount.needs_review = True

        # ---- (d): serialise --------------------------------------
        return output.model_dump()
    except Exception:  # noqa: BLE001
        # The slice spec mandates that this function never raises.
        # Return the raw model_dump() so the caller still has a valid
        # financial_summary shape — the structured output survived
        # even if the post-processing of edge cases did not.
        logger.warning(
            "run_id=%s node_name=%s postprocess_unexpected_fallback",
            run_id,
            _NODE_NAME,
        )
        return output.model_dump()


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _format_few_shot_examples_as_messages() -> list[Any]:
    """Render FEW_SHOT_EXAMPLES as prior Human/AI message pairs.

    Per the slice spec step (b): "FEW_SHOT_EXAMPLES as prior messages".
    Each example becomes one HumanMessage (rendered input chunks) and
    one AIMessage (rendered expected output JSON). The actual user
    content (real retrieved chunks) is appended after the examples.
    """
    out: list[Any] = []
    for i, ex in enumerate(FEW_SHOT_EXAMPLES, start=1):
        input_chunks = ex.get("input_chunks", [])
        try:
            expected_json = json.dumps(ex.get("expected_output", {}), ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            expected_json = str(ex.get("expected_output", ""))

        user_lines: list[str] = [
            f"### Example {i}",
            "Input chunks:",
        ]
        for c_idx, chunk_text in enumerate(input_chunks):
            user_lines.append(f"--- chunk {c_idx} ---")
            user_lines.append(chunk_text)
            user_lines.append("")
        user_lines.append("Return your answer as a FinancialOutput JSON object.")
        out.append(HumanMessage(content="\n".join(user_lines)))
        out.append(AIMessage(content=expected_json))
    return out


def _format_user_prompt(retrieved_chunks: list[dict[str, Any]]) -> str:
    """Render the retrieved chunks as the user content for the LLM.

    Each chunk is labelled with its 0-based chunk_index and detected
    language — the model uses the index to set `source_chunk_index`
    on every commitment entry. The number of chunks passed in is
    bounded by the retrieval function's top_k_per_query and
    deduplication.
    """
    lines: list[str] = [
        "Below are the chunks of the tender that are most likely to "
        "contain financial commitments (contract value, bonds, advance "
        "payment, retention, liquidated damages, payment schedule). "
        "Extract every financial commitment present and return your "
        "answer using the FinancialOutput JSON schema. Use the 0-based "
        "`source_chunk_index` shown in the header of each chunk for "
        "the corresponding bond or liquidated-damages entry.\n",
    ]
    for c in retrieved_chunks:
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
    structured output (langchain-google docs, 2026). The Google GenAI
    SDK automatically resolves `$ref` references and inlines `$defs`
    definitions, so the nested Pydantic schema (FinancialOutput →
    MonetaryValue / BondRequirement / LiquidatedDamages) is supported
    natively.
    """
    llm = ChatGoogleGenerativeAI(model=_LLM_MODEL)
    return llm.with_structured_output(FinancialOutput, method="json_schema")  # type: ignore[return-value]


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
    else:
        merged = [existing, handler]
    base["callbacks"] = merged
    return base


# ---------------------------------------------------------------------------
# Two independent retry layers (slice spec steps d and e)
# ---------------------------------------------------------------------------
# Path 1 (API failure): tenacity decorator — 3 attempts, exponential
# backoff, re-raises on exhaustion so the graph-level failure handling
# from REQ-003 marks the run as failed.
#
# Path 2 (schema failure): explicit for-loop — 1 retry, then degrades
# to the structured error dict. The graph continues without crashing.
#
# The two paths are independent: a schema error is NEVER retried by
# the tenacity decorator (excluded via `retry_if_not_exception_type
# (OutputParserException)`), and an API error NEVER reaches the
# schema loop (the tenacity layer either succeeds or re-raises).


@retry(
    # 3 total attempts = 1 initial + 2 retries. Slice spec step (e).
    stop=stop_after_attempt(3),
    # Exponential backoff: 2s, 4s, 8s, capped at 30s.
    wait=wait_exponential(multiplier=2, min=2, max=30),
    # Retry on any exception EXCEPT OutputParserException — parser
    # errors are handled by the outer 1-retry schema loop (step d).
    retry=retry_if_not_exception_type(OutputParserException),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _invoke_llm_with_api_retry(
    structured_llm: Any,
    messages: list[Any],
    config: dict[str, Any],
) -> FinancialOutput:
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
) -> FinancialOutput | None:
    """Call the LLM and parse to FinancialOutput.

    On OutputParserException: retry once with the same prompt. On
    second parse failure: log a WARNING (NEVER any monetary value) and
    return None (the caller turns that into the structured error dict
    per slice spec step d).

    On any non-parser exception (API errors, timeouts, etc.): the
    inner tenacity retry layer has already exhausted its 3 attempts
    and re-raised; this function does not catch it, so the graph-level
    failure handling from REQ-003 marks the run as failed.
    """
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            return await _invoke_llm_with_api_retry(structured_llm, messages, config)
        except OutputParserException as exc:
            last_error = exc
            logger.warning(
                "financial_analyst_schema_validation_failed run_id=%s "
                "node_name=%s attempt=%d error_type=%s",
                run_id,
                _NODE_NAME,
                attempt,
                type(exc).__name__,
            )
    # Both attempts failed with a schema validation error. Log a
    # short, non-financial preview of the error and return None.
    preview = repr(last_error)[:_RAW_LOG_PREVIEW_CHARS] if last_error else ""
    logger.warning(
        "financial_analyst_schema_validation_exhausted run_id=%s node_name=%s raw_preview=%s",
        run_id,
        _NODE_NAME,
        preview,
    )
    return None


# ---------------------------------------------------------------------------
# The node
# ---------------------------------------------------------------------------


def _malformed_response_dict() -> dict[str, Any]:
    """Return the structured error dict for malformed-LLM-response fallback.

    The exact shape the slice spec demands: error key + empty lists +
    payment_schedule=None. The Aggregator (REQ-003) reads this dict
    and the Report Assembler (REQ-008) renders the error to the
    analyst.
    """
    return {
        "financial_summary": {
            "error": ("Financial extraction unavailable — malformed LLM response"),
            "bonds": [],
            "commitments": [],
            "payment_schedule": None,
        }
    }


async def financial_analyst_node(state: TenderState, config: RunnableConfig) -> dict[str, Any]:
    """Real Financial Analyst — anchor retrieval, structured LLM, normalise.

    Returns a dict suitable for direct merge into TenderState. The
    Aggregator (REQ-003) reads `state["financial_summary"]` from this
    dict; the contract is a non-None dict that always has the same
    top-level shape (REQ-006 Postcondition).
    """
    run_id = state["run_id"]
    tender_id = state["tender_id"]
    chunks = state.get("chunks", []) or []

    # (a) Anchor-query retrieval. The function handles the "no
    # financial-relevant chunks" fallback to the first 15 chunks
    # internally. Empty `chunks` propagates as an empty result and
    # degrades to the fallback at the next call-site only if chunks
    # were non-empty upstream — here we proceed with whatever it
    # returns, since the slice spec guarantees state["chunks"] is
    # non-empty.
    finance_chunks = await retrieve_financial_chunks(
        tender_id=tender_id,
        chunks=chunks,
        top_k_per_query=4,
    )

    if not finance_chunks:
        # Should be impossible given the retrieval fallback, but
        # guard anyway: log metadata only and return the error dict.
        logger.warning(
            "financial_analyst_no_chunks_available run_id=%s node_name=%s tender_id=%s",
            run_id,
            _NODE_NAME,
            tender_id,
        )
        return _malformed_response_dict()

    # (b) Build the LLM call: system + few-shot examples as prior
    # messages + the real retrieved chunks as the final user message.
    structured_llm = _build_llm()
    messages: list[Any] = [SystemMessage(content=FINANCIAL_SYSTEM_PROMPT)]
    messages.extend(_format_few_shot_examples_as_messages())
    messages.append(HumanMessage(content=_format_user_prompt(finance_chunks)))

    # (c) Cost tracking — we open a session for the handler to write
    # into. The session is held open until the LLM call returns
    # (which is when on_llm_end fires), then released.
    async with with_session() as db:
        llm_config = _build_callback_config(
            run_id=run_id,
            node_name=_NODE_NAME,
            db=db,
            incoming=config,
        )

        # (d) + (e) Schema retry (1) + API retry (3) by tenacity.
        # On schema failure: returns None (we degrade below).
        # On API failure: re-raises, which propagates to graph-level
        # failure handling from REQ-003 (analysis_runs.state = "failed").
        output = await _invoke_llm_with_schema_retry(structured_llm, messages, llm_config, run_id)

    if output is None:
        # Schema validation exhausted its 1 retry. Per slice spec
        # step (d), proceed with the structured error dict rather
        # than crashing the graph.
        return _malformed_response_dict()

    # (f) Post-process: currency normalisation + percentage→absolute
    # bond amounts. This function NEVER raises — see its contract.
    summary = postprocess_financial_output(output, run_id)

    # (g) Log metadata only. NEVER amount_value, currency, contract
    # amount, or chunk text.
    logger.info(
        "financial_analyst_complete run_id=%s node_name=%s tender_id=%s "
        "bonds=%d milestones=%d has_ld=%s",
        run_id,
        _NODE_NAME,
        tender_id,
        len(summary.get("bonds", [])),
        len(summary.get("payment_schedule", [])),
        summary.get("liquidated_damages") is not None,
    )

    return {"financial_summary": summary}


# Local import to avoid a top-level cycle: state.py is the source of
# truth for TenderState, and financial_analyst.py must not import from
# anywhere that re-exports it.
from app.agents.state import TenderState  # noqa: E402
