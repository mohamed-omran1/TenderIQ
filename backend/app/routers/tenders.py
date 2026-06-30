"""Tenders router — POST /tenders/upload and GET /tenders/{id}.

Implements REQ-001 Main Flow steps 1–5 and 10:
  upload validates + stores + inserts row + schedules ingestion, returns 202.
  get_status is tenant-scoped polling for `ready` / `failed`.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Path,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.ingestion import run_ingestion
from app.agents.state import TenderState
from app.config import Settings, get_settings
from app.db.models import AnalysisRun, Company, Tender, TenderChunk
from app.db.session import get_session, with_session
from app.errors import NotFound, QuotaExceeded, RateLimited
from app.middleware.auth import get_current_company
from app.middleware.rate_limit import check_rate_limit
from app.schemas.analysis import AnalyseResponse, RunStatusResponse
from app.schemas.tender import TenderDetailResponse, TenderUploadResponse
from app.services.storage import save_upload
from app.services.validation import reject_oversize_declared, sanitize_filename, validate_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenders", tags=["tenders"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CompanyDep = Annotated[Company, Depends(get_current_company)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def run_graph(
    run_id: str,
    tender_id: str,
    company_id: str,
    chunks: list[dict],
) -> None:
    """Background worker: executes the LangGraph pipeline for one run.

    Uses its own AsyncSession so it does not depend on the request-scoped
    session that closes when the HTTP response is sent.
    """
    # Import graph lazily inside the background task to avoid any circular
    # import at router module load time (REQ-003 Slice 2 Rules).
    from app.agents.graph import graph
    from app.middleware.cost_tracker import CostTrackingHandler

    async with with_session() as db:
        try:
            await db.execute(
                update(AnalysisRun).where(AnalysisRun.id == run_id).values(state="running")
            )
            await db.commit()

            initial_state = TenderState(
                tender_id=str(tender_id),
                run_id=str(run_id),
                company_id=str(company_id),
                chunks=chunks,
                supervisor_ready=False,
                risk_findings=[],
                feasibility_score=None,
                feasibility_breakdown=None,
                financial_summary=None,
                aggregated_results=None,
                hitl_approved=False,
                hitl_override_score=None,
                final_report=None,
                token_usage=[],
                source_languages=[],
            )
            config = {
                "configurable": {"thread_id": str(run_id)},
                "callbacks": [
                    CostTrackingHandler(
                        run_id=str(run_id),
                        node_name="graph",
                        db=db,
                    )
                ],
            }

            saw_aggregator = False
            async for event in graph.astream(initial_state, config):
                node_name = list(event.keys())[0]
                # LangGraph internal events (e.g., __interrupt__ from the HITL gate)
                # contain non-serializable objects and are not node outputs.
                if node_name.startswith("__"):
                    continue
                if node_name == "aggregator":
                    saw_aggregator = True
                await db.execute(
                    update(AnalysisRun)
                    .where(AnalysisRun.id == run_id)
                    .values(
                        agent_trace=AnalysisRun.agent_trace.concat({node_name: event[node_name]})
                    )
                )
                await db.commit()

            if saw_aggregator:
                # Graph reached the interrupt_before=["report_assembler"] gate.
                await db.execute(
                    update(AnalysisRun)
                    .where(AnalysisRun.id == run_id)
                    .values(state="awaiting_hitl")
                )
            else:
                # The graph was interrupted before aggregation (e.g., supervisor
                # rejected missing profile or empty chunks).
                await db.execute(
                    update(AnalysisRun)
                    .where(AnalysisRun.id == run_id)
                    .values(
                        state="failed",
                        error_reason="Graph interrupted before aggregation completed.",
                    )
                )
            await db.commit()
        except Exception as e:
            logger.exception("analysis_run_failed run_id=%s", run_id)
            try:
                await db.execute(
                    update(AnalysisRun)
                    .where(AnalysisRun.id == run_id)
                    .values(state="failed", error_reason=str(e))
                )
                await db.commit()
            except Exception:
                logger.exception("analysis_run_failed_to_persist_error run_id=%s", run_id)


@router.post(
    "/upload",
    response_model=TenderUploadResponse,
    status_code=202,
    summary="Upload a tender PDF and start background ingestion.",
)
async def upload_tender(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile,
    company: CompanyDep,
    session: SessionDep,
    settings: SettingsDep,
) -> TenderUploadResponse:
    # --- Rate limit (Redis sliding window, per company) ---
    retry_after = await check_rate_limit(company.id)
    if retry_after:
        raise RateLimited(retry_after)

    # --- Reject oversize BEFORE buffering the whole body into memory ---
    reject_oversize_declared(file.size)

    # --- Read body once, then validate ---
    body = await file.read()
    validate_upload(file.content_type, body)

    # --- Monthly quota (companies.monthly_doc_limit) ---
    this_month_count = await session.scalar(
        select(func.count(Tender.id)).where(
            Tender.company_id == company.id,
            Tender.uploaded_at >= func.date_trunc("month", func.now()),
        )
    )
    if (this_month_count or 0) >= company.monthly_doc_limit:
        raise QuotaExceeded(
            f"Monthly document upload quota exceeded ({company.monthly_doc_limit})."
        )

    # --- Insert row, store file (UUID generated server-side as primary key) ---
    tender_id = str(uuid4())
    tender = Tender(
        id=tender_id,
        company_id=company.id,
        filename=sanitize_filename(file.filename),
        storage_path="",  # filled in after save so the path matches the stored id
        file_size_bytes=len(body),
        status="uploading",
    )
    session.add(tender)
    await session.flush()  # get the row in without committing yet

    storage_path = await save_upload(body, company.id, tender_id)
    tender.storage_path = str(storage_path)
    await session.commit()

    # Log only metadata — never chunk content (ai-security T5, REQ-001 NFR).
    logger.info(
        "tender_uploaded tender_id=%s company_id=%s filename=%s size_bytes=%d",
        tender_id,
        company.id,
        tender.filename,
        len(body),
    )

    # --- Schedule ingestion, return 202 immediately (steps 5–6) ---
    background_tasks.add_task(run_ingestion, tender_id)
    return TenderUploadResponse(tender_id=tender_id, status="uploading")


@router.get(
    "/{tender_id}",
    response_model=TenderDetailResponse,
    summary="Get a tender's status (tenant-scoped).",
)
async def get_tender(
    tender_id: Annotated[str, Path()],
    company: CompanyDep,
    session: SessionDep,
) -> TenderDetailResponse:
    """Tenant-scoped lookup: filter by id AND company_id in the same query.

    Loading by `id` alone and checking ownership afterwards leaks existence
    via timing and risks a forgotten check (api-security-reviewer API1/BOLA).
    A 404 (not 403) on mismatch avoids confirming the resource exists.
    """
    result = await session.execute(
        select(Tender).where(Tender.id == tender_id, Tender.company_id == company.id)
    )
    tender = result.scalar_one_or_none()
    if tender is None:
        raise NotFound()
    return TenderDetailResponse.model_validate(tender)


@router.post(
    "/{tender_id}/analyse",
    response_model=AnalyseResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an analysis run for a ready tender.",
)
async def analyse_tender(
    background_tasks: BackgroundTasks,
    tender_id: Annotated[str, Path()],
    company: CompanyDep,
    session: SessionDep,
) -> AnalyseResponse:
    """Validate the tender, create a pending analysis run, and launch the graph.

    Returns HTTP 202 immediately; the graph runs in a background task with its
    own database session.
    """
    # b) Fetch tender — 404 if it does not exist.
    result = await session.execute(select(Tender).where(Tender.id == tender_id))
    tender = result.scalar_one_or_none()
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found.")

    # c) Authorisation — 403 if it belongs to another tenant.
    if tender.company_id != company.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorised to analyse this tender.",
        )

    # d) Status check — 409 unless the tender is ready.
    if tender.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tender is not ready for analysis. Current status: {tender.status}.",
        )

    # e) Duplicate run check — 409 if one is already active.
    active_states = ("pending", "running", "awaiting_hitl")
    dup_result = await session.execute(
        select(AnalysisRun.id).where(
            AnalysisRun.tender_id == tender_id,
            AnalysisRun.state.in_(active_states),
        )
    )
    if dup_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An analysis run is already in progress for this tender.",
        )

    # h) Pre-fetch chunks as plain dicts before the request session closes.
    chunk_rows = await session.execute(
        select(
            TenderChunk.content,
            TenderChunk.detected_language,
            TenderChunk.chunk_index,
        )
        .where(TenderChunk.tender_id == tender_id)
        .order_by(TenderChunk.chunk_index)
    )
    chunks = [
        {
            "content": row.content,
            "detected_language": row.detected_language,
            "chunk_index": row.chunk_index,
        }
        for row in chunk_rows.all()
    ]

    # f) Create the pending run and commit so the background task can see it.
    run = AnalysisRun(
        tender_id=tender_id,
        company_id=company.id,
        state="pending",
    )
    session.add(run)
    await session.flush()
    run_id = run.id
    await session.commit()

    # g) Launch graph in a background task with its own session.
    background_tasks.add_task(run_graph, run_id, tender_id, company.id, chunks)
    return AnalyseResponse(run_id=run_id, status="pending")


@router.get(
    "/{tender_id}/status",
    response_model=RunStatusResponse,
    summary="Get the latest analysis-run status for a tender.",
)
async def get_analysis_status(
    tender_id: Annotated[str, Path()],
    company: CompanyDep,
    session: SessionDep,
) -> RunStatusResponse:
    """Return the most recent analysis run state for the caller's tender."""
    result = await session.execute(
        select(AnalysisRun)
        .where(AnalysisRun.tender_id == tender_id)
        .order_by(AnalysisRun.started_at.desc())
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No analysis run found for this tender.",
        )

    # c) Authorisation: the run carries its own company_id for tenant scoping.
    if run.company_id != company.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorised to view this analysis run.",
        )

    return RunStatusResponse(
        run_id=run.id,
        state=run.state,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_reason=run.error_reason,
        agent_trace=run.agent_trace or {},
    )
