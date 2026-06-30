"""Pydantic v2 response schemas for the analytics cost endpoint (REQ-003 Slice 3).

All schemas are response-only — no request bodies in this module. Token counts
and USD costs are commercially sensitive and must never appear in application
logs; they are surfaced only through this authenticated, tenant-scoped endpoint.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CostEventSchema(BaseModel):
    """One LLM cost event — maps directly to a llm_cost_events row."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    node_name: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    logged_at: datetime


class RunCostSummary(BaseModel):
    """Aggregated cost for a single analysis run."""

    run_id: UUID
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    breakdown: list[CostEventSchema]


class MonthlyCostSummary(BaseModel):
    """Aggregated cost grouped by calendar month."""

    month: str
    total_cost_usd: float
    total_runs: int
    avg_cost_per_run: float


class AnalyticsCostResponse(BaseModel):
    """Top-level response for GET /analytics/cost."""

    per_run: list[RunCostSummary]
    monthly: list[MonthlyCostSummary]
