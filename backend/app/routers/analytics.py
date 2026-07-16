"""Analytics router — GET /analytics/cost (REQ-003 Slice 3).

Returns per-run and monthly cost summaries for the authenticated company.
Strictly tenant-scoped: every query filters by company_id derived from the
API key — never from query parameters (api-security-reviewer, API2/BOLA).

Token counts and USD costs are commercially sensitive — surfaced only through
this authenticated endpoint, never in application logs.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import extract, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnalysisRun, Company, LlmCostEvent
from app.db.session import get_session
from app.middleware.auth import get_current_company
from app.schemas.analytics import (
    AnalyticsCostResponse,
    CostEventSchema,
    MonthlyCostSummary,
    RunCostSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CompanyDep = Annotated[Company, Depends(get_current_company)]


@router.get(
    "/cost",
    response_model=AnalyticsCostResponse,
    summary="Get per-run and monthly LLM cost summaries for the authenticated company.",
)
async def get_cost_analytics(
    company: CompanyDep,
    session: SessionDep,
    limit: int = Query(default=10, ge=1, le=100),
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
) -> AnalyticsCostResponse:
    run_query = select(AnalysisRun).where(AnalysisRun.company_id == company.id)

    if month is not None:
        year_str, month_str = month.split("-")
        run_query = run_query.where(
            extract("year", AnalysisRun.started_at) == int(year_str),
            extract("month", AnalysisRun.started_at) == int(month_str),
        )

    run_query = run_query.order_by(AnalysisRun.started_at.desc()).limit(limit)
    result = await session.execute(run_query)
    runs = list(result.scalars().all())

    per_run: list[RunCostSummary] = []
    monthly_totals: dict[str, dict[str, float | set[str]]] = defaultdict(
        lambda: {"total_cost_usd": 0.0, "run_ids": set()}
    )

    for run in runs:
        cost_result = await session.execute(
            select(LlmCostEvent)
            .where(LlmCostEvent.run_id == run.id)
            .order_by(LlmCostEvent.logged_at)
        )
        events = list(cost_result.scalars().all())

        event_schemas = [CostEventSchema.model_validate(e) for e in events]
        total_input = sum(e.input_tokens for e in events)
        total_output = sum(e.output_tokens for e in events)
        total_cost = sum(e.cost_usd for e in events)

        per_run.append(
            RunCostSummary(
                run_id=UUID(run.id) if isinstance(run.id, str) else run.id,
                total_cost_usd=total_cost,
                total_input_tokens=total_input,
                total_output_tokens=total_output,
                breakdown=event_schemas,
            )
        )

        run_month = run.started_at.strftime("%Y-%m")
        monthly_data = monthly_totals[run_month]
        monthly_data["total_cost_usd"] += total_cost
        monthly_data["run_ids"].add(run.id)

    monthly: list[MonthlyCostSummary] = []
    for month_key in sorted(monthly_totals.keys(), reverse=True):
        data = monthly_totals[month_key]
        run_ids = data["run_ids"]
        assert isinstance(run_ids, set)
        total_runs = len(run_ids)
        total_cost = float(data["total_cost_usd"])
        monthly.append(
            MonthlyCostSummary(
                month=month_key,
                total_cost_usd=total_cost,
                total_runs=total_runs,
                avg_cost_per_run=total_cost / total_runs if total_runs > 0 else 0.0,
            )
        )

    return AnalyticsCostResponse(per_run=per_run, monthly=monthly)
