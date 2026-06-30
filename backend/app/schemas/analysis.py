"""Pydantic v2 request/response models for the analysis-run endpoints (REQ-003)."""
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
    """

    run_id: UUID
    state: str
    started_at: datetime
    completed_at: datetime | None = None
    error_reason: str | None = None
    agent_trace: dict = Field(default_factory=dict)
