"""Pydantic v2 request/response models for the analysis-run endpoints (REQ-003, REQ-007)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AnalyseResponse(BaseModel):
    """Returned by POST /tenders/{id}/analyse — HTTP 202 Accepted."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    status: str


class RunStatusResponse(BaseModel):
    """Returned by GET /tenders/{id}/status — current run state.

    The ORM column is `id` (REQ-007/cost-tracker uses the same name), but the
    public API contract exposes it as `run_id` (REQ-003 spec). We populate
    `run_id` explicitly in the router to keep the mapping visible and avoid
    a field-level alias that would also apply to the other column reads.

    `report_available` is True only when the run is complete AND the
    report_assembler node has populated ``agent_trace`` (REQ-008 Slice 3).
    The frontend uses this flag to navigate to the report page without
    having to call GET /report just to check availability.
    """

    run_id: UUID
    state: str
    started_at: datetime
    completed_at: datetime | None = None
    error_reason: str | None = None
    feasibility_score: float | None = None
    agent_trace: dict = Field(default_factory=dict)
    report_available: bool = False


class RiskFindingResponse(BaseModel):
    """Returned by GET /tenders/{id}/findings — one row per Risk Radar finding.

    Mirrors the Pydantic `RiskFinding` schema in
    `app/agents/skills/risk_clause_extraction.py` (the wire format used during
    the graph run) and the `risk_findings` ORM table (the persisted form).
    The field set is the union of the two minus the parent `run_id` (the URL
    identifies the tender; the run is derived server-side from the latest
    analysis_run for that tender).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category: str
    severity: str
    clause_text: str
    explanation: str
    source_chunk_index: int
    confidence: float


class FinancialCommitmentResponse(BaseModel):
    """Returned by GET /tenders/{id}/financial — one row per financial commitment.

    Mirrors the `financial_commitments` ORM table (REQ-006 Slice 3). The
    field set deliberately omits `run_id` (the URL identifies the tender;
    the run is derived server-side from the latest analysis_run for that
    tender).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    commitment_type: str
    amount_value: float | None = None
    amount_currency: str | None = None
    percentage: float | None = None
    description: str
    needs_review: bool
    source_chunk_index: int | None = None


# ── REQ-007 HITL Override Gate schemas ────────────────────────────


class ApproveRequest(BaseModel):
    """Request body for POST /tenders/{id}/approve — approve AI score as-is.

    justification is optional for approvals but encouraged for the audit log.
    """

    justification: str | None = None


class OverrideRequest(BaseModel):
    """Request body for POST /tenders/{id}/override — adjust the feasibility score.

    overridden_score must be between 0.0 and 100.0 (inclusive).
    justification is required — a minimum of 10 characters (REQ-007).
    """

    overridden_score: float = Field(
        ge=0.0, le=100.0,
        description="Analyst-adjusted feasibility score (0-100)",
    )
    justification: str = Field(
        min_length=10,
        description="Required when overriding the AI score",
    )


class HITLResponse(BaseModel):
    """Returned by POST /tenders/{id}/approve and /override — HTTP 202."""

    run_id: UUID
    action: str
    original_score: float
    overridden_score: float | None = None
    message: str


class HITLOverrideResponse(BaseModel):
    """Returned by GET /tenders/{id}/hitl-override — the HITL decision for a run.

    justification is NEVER included — it is an internal audit field only
    (REQ-007 Security NFR). The field is stored in the DB for audit trails
    but must never be surfaced to the UI or API responses.
    """

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    action: str
    original_score: float
    overridden_score: float | None = None
    created_at: datetime


# ── REQ-008 Report Assembler (Slice 3) response schemas ─────────────


class RiskSummaryItemResponse(BaseModel):
    """One item in the report's top-5 risk summary (REQ-008 Slice 3).

    Mirrors the ``RiskSummaryItem`` Pydantic schema in
    `app/agents/skills/report_synthesis.py` (the LLM structured output
    contract) and the items stored under
    ``agent_trace["report_assembler"]["final_report"]["risk_summary"]``.
    """

    category: str
    severity: str
    description: str


class ReportResponse(BaseModel):
    """Returned by GET /tenders/{id}/report — the full Go/No-Go brief.

    The wire contract for the report_assembler output. Field set matches
    the ``ReportOutput`` schema in
    `app/agents/skills/report_synthesis.py` plus the run's
    ``completed_at`` timestamp so the frontend can show "Generated at …".

    `analyst_note` is the override-acknowledgement string set by the
    Report Assembler when the analyst adjusted the AI score (REQ-007
    override flow). May be None when no override happened.

    The endpoint is HTTP 200 only when ``run.state == "complete"``; before
    that the router returns 404 (the report page polls this endpoint, so
    404 is the expected "not ready" signal — consistent with
    GET /tenders/{id}/financial in REQ-006).
    """

    run_id: UUID
    tender_id: UUID
    go_no_go: str
    effective_score: float
    is_analyst_override: bool
    executive_summary: str
    recommendation: str
    risk_summary: list[RiskSummaryItemResponse]
    feasibility_highlights: list[str]
    financial_highlights: list[str]
    analyst_note: str | None = None
    completed_at: datetime | None = None
