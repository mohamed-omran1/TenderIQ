"""Shared LangGraph state schema for the TenderIQ analysis pipeline (REQ-003)."""
from __future__ import annotations

from typing import TypedDict


class TenderState(TypedDict):
    """All nodes communicate exclusively through this TypedDict.

    Fields are final for REQ-003 Slice 1 — do not add, remove, or rename.
    """

    # Identity
    tender_id: str
    run_id: str
    company_id: str

    # Ingestor output (populated before the graph starts)
    chunks: list[dict]  # {content, detected_language, chunk_index}

    # Supervisor
    supervisor_ready: bool

    # Specialist node outputs (stubbed in Slice 1, replaced by real agents later)
    risk_findings: list[dict]
    feasibility_score: float | None
    feasibility_breakdown: dict | None
    financial_summary: dict | None

    # Aggregator
    aggregated_results: dict | None

    # HITL gate (REQ-007)
    hitl_approved: bool
    hitl_override_score: float | None

    # Report (REQ-008)
    final_report: str | None

    # Cost tracking
    token_usage: list[dict]  # accumulates per node

    # Languages detected across tender chunks
    source_languages: list[str]
