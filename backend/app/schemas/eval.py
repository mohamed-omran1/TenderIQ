"""API request/response schemas for eval endpoints (REQ-012 Slice 2)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EvalRequest(BaseModel):
    tender_id: UUID
    run_risk_radar: bool = True
    run_scorer_consistency: bool = False

    model_config = ConfigDict(extra="forbid")


class EvalResultResponse(BaseModel):
    id: UUID
    tender_id: UUID
    overall_status: str
    total_cost_usd: float
    run_at: datetime
    result: dict

    model_config = ConfigDict(from_attributes=True)
