"""Eval router — POST /eval/run and GET /eval/results (REQ-012 Slice 2).

Admin-only endpoints protected by X-Admin-Key header. Company API keys
are NOT accepted — they must return HTTP 403 even if valid.
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import EvalResult, LlmCostEvent, Tender
from app.db.session import get_session
from app.schemas.eval import EvalRequest, EvalResultResponse
from eval.run_eval import run_risk_radar_eval, run_scorer_consistency_eval
from eval.schemas import EvalRunResult

router = APIRouter()


async def require_admin_key(request: Request) -> None:
    """FastAPI dependency: validate X-Admin-Key header against ADMIN_API_KEY env var.

    Company API keys must NOT pass this check — even valid company keys are
    not admin keys. This is a separate auth path from the Bearer-based company
    auth in app/middleware/auth.py.
    """
    admin_key = request.headers.get("X-Admin-Key")
    settings = get_settings()
    if not settings.admin_api_key or admin_key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )


@router.post(
    "/run",
    dependencies=[Depends(require_admin_key)],
    response_model=EvalResultResponse,
)
async def run_eval(
    request: EvalRequest,
    db: AsyncSession = Depends(get_session),
) -> EvalResultResponse:
    tender = await db.get(Tender, str(request.tender_id))
    if tender is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tender not found.",
        )
    if tender.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tender must be ingested before running eval.",
        )

    if not request.run_risk_radar and not request.run_scorer_consistency:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one eval type must be enabled.",
        )

    json_path = pathlib.Path("eval/labelled_sample_tender.json")
    ground_truth = json.loads(json_path.read_text(encoding="utf-8"))
    labelled_findings: list[dict] = ground_truth.get("labelled_findings", [])

    eval_run_id = f"eval-{uuid4()}"
    db_url = get_settings().database_url

    risk_result = None
    scorer_result = None
    notes: str | None = None

    if request.run_risk_radar:
        if not labelled_findings:
            notes = "No labelled ground truth available."
        else:
            risk_result = await run_risk_radar_eval(
                str(request.tender_id),
                labelled_findings,
                eval_run_id,
                db_url,
            )

    if request.run_scorer_consistency:
        scorer_result = await run_scorer_consistency_eval(
            str(request.tender_id),
            str(tender.company_id),
            eval_run_id,
            db_url,
        )

    cost_result = await db.execute(
        select(func.coalesce(func.sum(LlmCostEvent.cost_usd), 0.0)).where(
            LlmCostEvent.run_id.like("eval-%"),
            LlmCostEvent.run_id.like(f"{eval_run_id}%"),
        )
    )
    total_cost_usd = float(cost_result.scalar_one())

    statuses: list[str] = []
    if risk_result is not None:
        statuses.append(risk_result.pass_fail)
    if scorer_result is not None:
        statuses.append(scorer_result.pass_fail)

    if not statuses:
        overall_status = "NO_DATA"
    elif all(s == "PASS" for s in statuses):
        overall_status = "PASS"
    elif any(s == "FAIL" for s in statuses):
        overall_status = "FAIL"
    else:
        overall_status = "PARTIAL"

    eval_run = EvalRunResult(
        eval_id=eval_run_id,
        tender_id=str(request.tender_id),
        tender_name=ground_truth.get("tender_name") or tender.filename,
        run_at=datetime.now(timezone.utc).isoformat(),
        risk_radar=risk_result,
        scorer=scorer_result,
        total_cost_usd=total_cost_usd,
        overall_status=overall_status,
        notes=notes,
    )

    db_row = EvalResult(
        company_id=tender.company_id,
        tender_id=str(request.tender_id),
        result=eval_run.model_dump(),
        overall_status=overall_status,
        total_cost_usd=total_cost_usd,
    )
    db.add(db_row)
    await db.commit()
    await db.refresh(db_row)

    return EvalResultResponse.model_validate(db_row)


@router.get(
    "/results",
    dependencies=[Depends(require_admin_key)],
    response_model=list[EvalResultResponse],
)
async def get_eval_results(
    limit: int = 10,
    db: AsyncSession = Depends(get_session),
) -> list[EvalResultResponse]:
    result = await db.execute(
        select(EvalResult)
        .order_by(EvalResult.run_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [EvalResultResponse.model_validate(row) for row in rows]
