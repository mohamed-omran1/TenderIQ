"""Risk Radar agent node (REQ-004 Slice 2).

Replaces the REQ-003 stub with real LLM-driven risk clause extraction.

Pipeline (per the slice spec):
  a) Call retrieve_risk_relevant_chunks(...) over the in-state chunks
  b) If the retrieval returns nothing: return {"risk_findings": []} — a
     valid outcome per REQ-004 Alt Flow "No risk-relevant chunks found".
  c) Build an LLM call using the Slice 1 skill package
     (RISK_RADAR_SYSTEM_PROMPT, FEW_SHOT_EXAMPLES, RiskRadarOutput schema).
  d) Attach a CostTrackingHandler(node_name="risk_radar") to the call.
  e) On schema-validation failure: retry once with the same prompt; on
     second failure, log a WARNING that NEVER contains clause_text or
     explanation, and return {"risk_findings": []}.
  f) On LLM API failure (network/rate-limit): retry with exponential
     backoff, 3 attempts. On exhausted retries, raise so the graph-level
     failure handling from REQ-003 marks the run as failed.
  g) Deduplicate findings: if two findings have semantically similar
     clause_text (cosine similarity >= 0.92) AND the same category, keep
     one — prefer the English version of clause_text.
  h) Return {"risk_findings": [f.model_dump() for f in deduplicated]}.

The return shape exactly matches what the REQ-003 aggregator node reads:
a dict with key "risk_findings" whose value is a list of dicts (one per
finding). See app/agents/nodes/aggregator.py:13-19.
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

from app.agents.embeddings import get_embeddings_client
from app.config import get_settings
from app.agents.retrieval import retrieve_risk_relevant_chunks
from app.agents.skills.risk_clause_extraction import (
    FEW_SHOT_EXAMPLES,
    RISK_RADAR_SYSTEM_PROMPT,
    RiskFinding,
    RiskRadarOutput,
)
from app.db.session import with_session
from app.middleware.cost_tracker import CostTrackingHandler

logger = logging.getLogger(__name__)

# The structured-output chat model. Free-tier friendly, structured output
# supported (json_schema mode). The cost tracker treats any model not in
# its pricing table as 0.0 USD — so a Gemini model name still produces a
# valid llm_cost_events row, just with cost_usd=0.
_LLM_MODEL = "gemini-2.5-flash"

# Cosine-similarity threshold above which two findings with the same
# category are treated as duplicates of the same underlying clause.
# Matches the architecture's semantic-dedup pattern (Section "Cost
# Tracking Middleware" notes the system uses embedding-based dedup).
_DEDUP_SIMILARITY_THRESHOLD = 0.92

# Arabic Unicode block — used only to detect Arabic clause_text for the
# dedup "prefer English" tie-breaker. No clause content is logged.
_ARABIC_BLOCK_START = 0x0600
_ARABIC_BLOCK_END = 0x06FF

# Truncation length for any log entry that quotes a raw LLM response
# after a schema validation failure. Keeps logs from accidentally
# spilling the full malformed JSON (which could include clause-sized
# fragments) into observability tooling.
_RAW_LOG_PREVIEW_CHARS = 200


# ---------------------------------------------------------------------------
# Build the LLM call
# ---------------------------------------------------------------------------

def _format_few_shot_examples() -> str:
    """Render FEW_SHOT_EXAMPLES as a textual block to append to the system prompt.

    Gemini's `with_structured_output(json_schema)` works best when the entire
    instruction set — including few-shots — lives in a single system message.
    We embed the few-shots there rather than as prior messages so the model
    sees them as part of the schema contract, not as separate turns.
    """
    # TODO: verify Arabic legal phrasing accuracy in the Arabic few-shots.
    # The Arabic clauses in FEW_SHOT_EXAMPLES were written by an LLM and have
    # not been reviewed by a contracts professional. They are used as-is for
    # the MVP. A qualified Arabic-speaking contracts reviewer must sign off
    # on the legal wording before this is shipped to a paid pilot.
    blocks: list[str] = []
    for i, ex in enumerate(FEW_SHOT_EXAMPLES, start=1):
        try:
            expected = json.dumps(ex["expected_output"], ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            expected = str(ex.get("expected_output", ""))
        blocks.append(
            f"### Example {i}\n"
            f"Input chunk:\n{ex['input_chunk']}\n\n"
            f"Expected output:\n{expected}"
        )
    return "\n\n".join(blocks)


def _build_system_prompt() -> str:
    """Compose the full system prompt: skill prompt + few-shot examples.

    No clause_text / explanation from any finding is included here — only
    the static, reviewable skill content from Slice 1.
    """
    return (
        f"{RISK_RADAR_SYSTEM_PROMPT}\n\n"
        f"---\n\n"
        f"## Few-shot examples\n\n"
        f"{_format_few_shot_examples()}"
    )


def _format_user_prompt(retrieved_chunks: list[dict[str, Any]]) -> str:
    """Render the retrieved chunks as the user content for the LLM.

    Each chunk is labelled with its 0-based index, detected language, and
    page number — the model uses the index to set `source_chunk_index` on
    every finding (slice spec step c). Chunk content is the actual
    retrieval unit; no findings are injected into the prompt.
    """
    lines: list[str] = [
        "Below are the chunks of the tender that are most likely to contain "
        "risk-bearing clauses. Identify every risk clause present and return "
        "your answer using the structured output schema. Use the 0-based "
        "`source_chunk_index` shown in the header of each chunk for the "
        "corresponding finding.\n",
    ]
    for c in retrieved_chunks:
        idx = c.get("chunk_index", "?")
        lang = c.get("detected_language", "?")
        page = c.get("page_number")
        page_str = f", page: {page}" if page is not None else ""
        lines.append(f"--- chunk {idx} (language: {lang}{page_str}) ---")
        lines.append(c.get("content", ""))
        lines.append("")
    return "\n".join(lines)


def _build_llm() -> ChatGoogleGenerativeAI:
    """Construct the chat model + structured-output wrapper.

    `method="json_schema"` is the recommended Gemini path for reliable
    structured output (langchain-google docs, 2025). The model's own
    `GOOGLE_API_KEY` env var is picked up automatically.
    """
    llm = ChatGoogleGenerativeAI(model=_LLM_MODEL, google_api_key=get_settings().google_api_key)
    return llm.with_structured_output(RiskRadarOutput, method="json_schema")  # type: ignore[return-value]


def _build_callback_config(
    run_id: str,
    node_name: str,
    db: Any,
    incoming: RunnableConfig | None,
) -> dict[str, Any]:
    """Build the RunnableConfig that wires CostTrackingHandler onto the LLM call.

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
# Retry layers (REVIEW: see module docstring steps e and f)
# ---------------------------------------------------------------------------

@retry(
    # 3 total attempts = 1 initial + 2 retries. Slice spec step (f).
    stop=stop_after_attempt(3),
    # Exponential backoff: 2s, 4s, 8s, capped at 30s.
    wait=wait_exponential(multiplier=2, min=2, max=30),
    # Retry on any exception EXCEPT OutputParserException — parser errors
    # are handled by the outer 1-retry schema loop (step e).
    retry=retry_if_not_exception_type(OutputParserException),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _invoke_llm_with_api_retry(
    structured_llm: Any,
    messages: list[Any],
    config: dict[str, Any],
) -> RiskRadarOutput:
    """Single-shot LLM call wrapped with 3x exponential-backoff retries.

    Re-raises on exhausted retries so the graph-level failure handling
    from REQ-003 (analysis_runs.state = "failed") can take over.
    """
    return await structured_llm.ainvoke(messages, config=config)  # type: ignore[return-value]


async def _extract_findings(
    structured_llm: Any,
    messages: list[Any],
    config: dict[str, Any],
    run_id: str,
) -> RiskRadarOutput | None:
    """Call the LLM and parse to RiskRadarOutput.

    On OutputParserException: retry once with the same prompt. On second
    parse failure: log a WARNING that NEVER includes clause_text or
    explanation content, and return None (the caller turns this into
    `{"risk_findings": []}` per the slice spec).

    On any non-parser exception (API errors, timeouts, etc.): the inner
    tenacity retry layer has already exhausted its 3 attempts and
    re-raised; this function does not catch it, so the graph-level
    failure handling from REQ-003 marks the run as failed.
    """
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            return await _invoke_llm_with_api_retry(
                structured_llm, messages, config
            )
        except OutputParserException as exc:
            last_error = exc
            # Only generic metadata may be logged — no clause_text, no
            # explanation, no raw content beyond a short preview. The
            # preview is a structural error message, not clause content.
            logger.warning(
                "risk_radar_schema_validation_failed run_id=%s node_name=risk_radar "
                "attempt=%d error_type=%s",
                run_id,
                attempt,
                type(exc).__name__,
            )
            # Fall through to next attempt (1 retry total per slice spec).
    # Both attempts failed with a schema validation error. The clause
    # "log the raw malformed response" is interpreted as: log enough
    # metadata to debug without leaking clause text. We log the
    # exception's repr (truncated) and NOT the underlying LLM text.
    preview = repr(last_error)[:_RAW_LOG_PREVIEW_CHARS] if last_error else ""
    logger.warning(
        "risk_radar_schema_validation_exhausted run_id=%s node_name=risk_radar "
        "raw_preview=%s",
        run_id,
        preview,
    )
    return None


# ---------------------------------------------------------------------------
# Deduplication (slice spec step g)
# ---------------------------------------------------------------------------

def _is_arabic(text: str) -> bool:
    """True if `text` contains at least one character from the Arabic block.

    Used only to break dedup ties: when two findings collapse into one,
    prefer the version whose clause_text is NOT Arabic. No logging.
    """
    if not text:
        return False
    return any(_ARABIC_BLOCK_START <= ord(ch) <= _ARABIC_BLOCK_END for ch in text)


def _pick_winner(a: RiskFinding, b: RiskFinding) -> RiskFinding:
    """Tie-breaker when two findings are semantically the same clause.

    Prefer the English clause_text if one of (a, b) is Arabic and the
    other is not. Otherwise keep `a` for determinism.
    """
    a_ar = _is_arabic(a.clause_text)
    b_ar = _is_arabic(b.clause_text)
    if a_ar and not b_ar:
        return b
    if b_ar and not a_ar:
        return a
    return a


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity for the dedup pass.

    Returns a value in [-1.0, 1.0]. We only need relative ordering and
    threshold checks, so no external library is pulled in.
    """
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return -1.0
    return dot / (na * nb)


async def _deduplicate_findings(
    findings: list[RiskFinding],
    embeddings: Any,
) -> list[RiskFinding]:
    """Merge findings that refer to the same underlying clause.

    Two findings are duplicates iff they share a category AND the
    cosine similarity of their clause_text embeddings is >= 0.92. On
    a merge we keep the English clause_text if one of the two is in
    Arabic and the other is not (per REQ-004 Alt Flow "Bilingual").
    """
    if len(findings) <= 1:
        return list(findings)

    clause_texts = [f.clause_text for f in findings]
    vectors = embeddings.embed_documents(clause_texts)

    # Each kept entry is (finding, vector). We compare every new finding
    # against the survivors; on a match we keep the English-preferred one.
    kept: list[tuple[RiskFinding, list[float]]] = []
    for finding, vector in zip(findings, vectors):
        merged = False
        for idx, (existing, existing_vec) in enumerate(kept):
            if existing.category != finding.category:
                continue
            sim = _cosine_similarity(vector, existing_vec)
            if sim >= _DEDUP_SIMILARITY_THRESHOLD:
                kept[idx] = (_pick_winner(existing, finding), vector)
                merged = True
                break
        if not merged:
            kept.append((finding, vector))
    return [f for f, _ in kept]


# ---------------------------------------------------------------------------
# The node
# ---------------------------------------------------------------------------

async def risk_radar_node(
    state: TenderState, config: RunnableConfig
) -> dict[str, Any]:
    """Real Risk Radar — anchor retrieval -> structured LLM -> dedup.

    Returns a dict suitable for direct merge into TenderState. The
    Aggregator (REQ-003) reads `state["risk_findings"]` from this dict;
    the contract is: a list of finding dicts, possibly empty, never None.
    """
    run_id = state["run_id"]
    tender_id = state["tender_id"]
    company_id = state["company_id"]
    chunks = state.get("chunks", []) or []

    # (a) Anchor-query retrieval. Returns [] if the DB has no rows AND
    # the in-memory chunks list is empty — both are safe "no findings"
    # outcomes, not errors.
    retrieved = await retrieve_risk_relevant_chunks(
        tender_id=tender_id,
        chunks=chunks,
        top_k_per_query=5,
        company_id=company_id,
    )

    # (b) No relevant chunks: return empty per REQ-004 Alt Flow. Logging
    # only metadata — no chunk content, no findings, no PII.
    if not retrieved:
        logger.info(
            "risk_radar_no_relevant_chunks run_id=%s node_name=risk_radar "
            "tender_id=%s",
            run_id,
            tender_id,
        )
        return {"risk_findings": []}

    # (c) Build the LLM call.
    structured_llm = _build_llm()
    messages = [
        SystemMessage(content=_build_system_prompt()),
        HumanMessage(content=_format_user_prompt(retrieved)),
    ]

    # (d) Cost tracking — we open a session for the handler to write into.
    # The session is held open until the LLM call returns (which is when
    # on_llm_end fires), then released.
    async with with_session() as db:
        llm_config = _build_callback_config(
            run_id=run_id,
            node_name="risk_radar",
            db=db,
            incoming=config,
        )

        # (e) + (f) Call with structured-output retry, API-retry by tenacity.
        # On schema failure: returns None (we turn that into empty below).
        # On API failure: re-raises, which propagates to graph-level failure
        # handling from REQ-003 (analysis_runs.state = "failed").
        output = await _extract_findings(
            structured_llm, messages, llm_config, run_id
        )

    if output is None:
        # Schema validation exhausted its 1 retry. Per slice spec step (e),
        # proceed with empty findings rather than crashing the graph.
        return {"risk_findings": []}

    findings: list[RiskFinding] = list(output.findings)

    # (g) Deduplicate semantically-equivalent findings.
    embeddings = get_embeddings_client()
    deduped = await _deduplicate_findings(findings, embeddings)

    # (h) Log only counts + categories. NEVER clause_text or explanation.
    category_counts: dict[str, int] = {}
    for f in deduped:
        category_counts[f.category] = category_counts.get(f.category, 0) + 1
    logger.info(
        "risk_radar_complete run_id=%s node_name=risk_radar tender_id=%s "
        "retrieved_chunks=%d raw_findings=%d deduped_findings=%d categories=%s",
        run_id,
        tender_id,
        len(retrieved),
        len(findings),
        len(deduped),
        category_counts,
    )

    return {"risk_findings": [f.model_dump() for f in deduped]}


# Local import to avoid a top-level cycle: state.py is the source of
# truth for TenderState, and risk_radar.py must not import from anywhere
# that re-exports it.
from app.agents.state import TenderState  # noqa: E402
