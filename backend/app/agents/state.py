"""TenderState — the shared LangGraph state (PRD §5.3).

Only the fields consumed by the Ingestor node are populated for REQ-001.
The rest (risk_findings, feasibility_*, financial_summary, hitl_*, final_report)
are stubbed with None/empty defaults and will be owned by their respective
nodes in Week 2 (agent-designer skill: each field has exactly one writer).

Lists written from parallel branches MUST carry a reducer so the last branch
to finish doesn't clobber the others. For REQ-001 the Ingestor runs alone, so
reducer-annotated lists are forward-compat declarations, not active fan-out.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class ChunkRef(TypedDict):
    """A reference to a persisted chunk (id + provenance), NOT the raw text.

    TenderState carries chunk *references*, never raw chunk text. Storing prose
    in the checkpoint store leaks commercially sensitive tender content into an
    audit log and bloats the checkpoint (ai-security T5). Agents that need the
    text re-fetch it by id from tender_chunks, tenant-scoped.
    """

    chunk_id: str
    page_number: int | None
    detected_language: str


class TenderState(TypedDict):
    tender_id: str
    run_id: str | None
    # Ingestor-owned: lightweight references to persisted chunks.
    chunks: list[ChunkRef]
    # Languages detected in the PDF (subset of ["ar", "en"]).
    source_languages: Annotated[list[str], operator.add]

    # ---- Week 2 stubs (owned by their respective nodes) ----
    risk_findings: Annotated[list[dict], operator.add]  # risk_radar (parallel-safe)
    feasibility_score: float | None                     # scorer
    feasibility_breakdown: dict | None                  # scorer
    financial_summary: dict | None                      # financial
    hitl_approved: bool                                 # HITL gate
    hitl_override_score: float | None                   # override endpoint
    final_report: str | None                            # report_assembler
    token_usage: Annotated[list[dict], operator.add]    # cost callback


def initial_state(tender_id: str) -> TenderState:
    """Fresh state for a new ingestion run."""
    return TenderState(
        tender_id=tender_id,
        run_id=None,
        chunks=[],
        source_languages=[],
        risk_findings=[],
        feasibility_score=None,
        feasibility_breakdown=None,
        financial_summary=None,
        hitl_approved=False,
        hitl_override_score=None,
        final_report=None,
        token_usage=[],
    )
