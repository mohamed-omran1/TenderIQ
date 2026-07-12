"""Tenders router — POST /tenders/upload, GET /tenders/{id}, and analysis-run endpoints.

Implements REQ-001 Main Flow steps 1–5 and 10:
  upload validates + stores + inserts row + schedules ingestion, returns 202.
  get_status is tenant-scoped polling for `ready` / `failed`.

Also implements REQ-003 (analyse + status), REQ-004 Slice 3 (findings
persistence + GET /tenders/{id}/findings), REQ-005 Slice 3 (feasibility
score persistence in the same atomic commit block), REQ-006 Slice 3
(financial commitments persistence + GET /tenders/{id}/financial), and
REQ-007 Slice 1 (POST /approve + POST /override + _resume_graph).
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID, uuid4

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
from sqlalchemy import case, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.ingestion import run_ingestion
from app.agents.state import TenderState
from app.config import Settings, get_settings
from app.db.models import (
    AnalysisRun,
    Company,
    FinancialCommitment,
    HITLOverride,
    RiskFinding,
    Tender,
    TenderChunk,
)
from app.db.session import get_session, with_session
from app.errors import NotFound, QuotaExceeded, RateLimited
from app.middleware.auth import get_current_company
from app.middleware.rate_limit import check_rate_limit
from app.schemas.analysis import (
    AnalyseResponse,
    ApproveRequest,
    FinancialCommitmentResponse,
    HITLOverrideResponse,
    HITLResponse,
    OverrideRequest,
    ReportResponse,
    RiskFindingResponse,
    RiskSummaryItemResponse,
    RunStatusResponse,
)
from app.schemas.stream import make_event
from app.schemas.tender import TenderDetailResponse, TenderUploadResponse
from app.services.event_bus import get_event_bus
from app.services.storage import save_upload
from app.services.validation import reject_oversize_declared, sanitize_filename, validate_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenders", tags=["tenders"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CompanyDep = Annotated[Company, Depends(get_current_company)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _flatten_financial_summary(
    summary: dict, run_id: UUID
) -> list[dict]:
    """Convert the nested financial_summary dict into a flat row-list.

    One row per commitment item across all categories — ready for bulk
    INSERT into financial_commitments. Pure synchronous transformation:
    no async, no DB calls, no I/O.

    Rules (REQ-006 Slice 3):
      - Always include run_id in every row dict.
      - Skip any item where the source field is None/null
        (e.g. if contract_value is None, skip it).
      - Never raise — wrap in try/except and return [] on any
        unexpected error (log the error with run_id, no values).
    """
    try:
        rows: list[dict] = []

        contract_value = summary.get("contract_value")
        if contract_value:
            rows.append(
                {
                    "run_id": run_id,
                    "commitment_type": "contract_value",
                    "amount_value": contract_value.get("value"),
                    "amount_currency": contract_value.get("currency"),
                    "percentage": None,
                    "description": "Contract value",
                    "needs_review": bool(contract_value.get("needs_review", False)),
                    "source_chunk_index": None,
                }
            )

        for bond in summary.get("bonds", []) or []:
            amount = bond.get("amount") or {}
            rows.append(
                {
                    "run_id": run_id,
                    "commitment_type": "bond",
                    "amount_value": amount.get("value"),
                    "amount_currency": amount.get("currency"),
                    "percentage": bond.get("percentage"),
                    "description": bond.get("conditions", ""),
                    "needs_review": bool(amount.get("needs_review", False)),
                    "source_chunk_index": bond.get("source_chunk_index"),
                }
            )

        ld = summary.get("liquidated_damages")
        if ld:
            rate = ld.get("rate") or {}
            cap = ld.get("cap") or {}
            cap_value = cap.get("value") if cap else None
            rows.append(
                {
                    "run_id": run_id,
                    "commitment_type": "liquidated_damages",
                    "amount_value": rate.get("value"),
                    "amount_currency": rate.get("currency"),
                    "percentage": ld.get("cap_percentage"),
                    "description": (
                        f"LD rate: {ld.get('period', '')}. "
                        f"Cap: {cap_value if cap_value is not None else 'None'}"
                    ),
                    "needs_review": bool(rate.get("needs_review", False)),
                    "source_chunk_index": ld.get("source_chunk_index"),
                }
            )

        for milestone in summary.get("payment_schedule", []) or []:
            amount = milestone.get("amount") or {}
            has_amount = bool(amount)
            rows.append(
                {
                    "run_id": run_id,
                    "commitment_type": "payment_milestone",
                    "amount_value": amount.get("value") if has_amount else None,
                    "amount_currency": amount.get("currency") if has_amount else None,
                    "percentage": milestone.get("percentage"),
                    "description": f"{milestone.get('description', '')} — {milestone.get('trigger', '')}",
                    "needs_review": bool(amount.get("needs_review", False)) if has_amount else False,
                    "source_chunk_index": None,
                }
            )

        retention_rate = summary.get("retention_rate")
        if retention_rate is not None:
            rows.append(
                {
                    "run_id": run_id,
                    "commitment_type": "retention",
                    "amount_value": None,
                    "amount_currency": None,
                    "percentage": retention_rate,
                    "description": f"Retention: {retention_rate}% of contract value",
                    "needs_review": False,
                    "source_chunk_index": None,
                }
            )

        advance_payment = summary.get("advance_payment")
        if advance_payment:
            rows.append(
                {
                    "run_id": run_id,
                    "commitment_type": "advance_payment",
                    "amount_value": advance_payment.get("value"),
                    "amount_currency": advance_payment.get("currency"),
                    "percentage": None,
                    "description": "Advance payment / mobilisation",
                    "needs_review": bool(advance_payment.get("needs_review", False)),
                    "source_chunk_index": None,
                }
            )

        return rows
    except Exception as e:
        # Never raise — log run_id + error type, no values (REQ-006 NFR).
        logger.exception(
            "flatten_financial_summary_failed run_id=%s err_type=%s",
            run_id,
            type(e).__name__,
        )
        return []


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
            event_bus = get_event_bus()
            run_id_str = str(run_id)
            async for event in graph.astream(initial_state, config):
                node_name = list(event.keys())[0]
                # LangGraph internal events (e.g., __interrupt__ from the HITL gate)
                # contain non-serializable objects and are not node outputs.
                if node_name.startswith("__"):
                    continue
                if node_name == "aggregator":
                    saw_aggregator = True

                await event_bus.publish_event(run_id_str, make_event(
                    run_id_str, "node_started",
                    node_name=node_name,
                ))

                await db.execute(
                    update(AnalysisRun)
                    .where(AnalysisRun.id == run_id)
                    .values(
                        agent_trace=AnalysisRun.agent_trace.concat({node_name: event[node_name]})
                    )
                )
                await db.commit()

                await event_bus.publish_event(run_id_str, make_event(
                    run_id_str, "node_completed",
                    node_name=node_name,
                ))

            if saw_aggregator:
                # Graph reached the interrupt_before=["report_assembler"] gate.
                # Persist the risk findings produced by the Risk Radar in the
                # SAME transaction as the state transition to "awaiting_hitl"
                # — if the INSERT fails, the state must NOT move forward
                # (REQ-004 Slice 3 atomicity rule).
                #
                # We read the final state from the checkpoint rather than
                # relying on the in-loop event payloads, because the
                # aggregator's output is the source of truth for the
                # consolidated `risk_findings` list and is captured at the
                # last reducer write.
                final_checkpoint = await graph.aget_state(config)
                findings_dicts = (
                    final_checkpoint.values.get("risk_findings", [])
                    if final_checkpoint is not None
                    else []
                ) or []

                if findings_dicts:
                    await db.execute(
                        insert(RiskFinding).values([
                            {
                                "run_id": run_id,
                                "category": f["category"],
                                "severity": f["severity"],
                                "clause_text": f["clause_text"],
                                "explanation": f["explanation"],
                                "source_chunk_index": f["source_chunk_index"],
                                "confidence": f["confidence"],
                            }
                            for f in findings_dicts
                        ])
                    )

                # REQ-006: financial commitments.
                # Do NOT persist if the degraded-path "error" key is present
                # — the Analyst sees the error on the report page; no partial
                # DB rows should be produced.
                financial_summary = (
                    final_checkpoint.values.get("financial_summary", {})
                    if final_checkpoint is not None
                    else {}
                ) or {}
                commitment_count = 0
                if "error" not in financial_summary:
                    commitment_rows = _flatten_financial_summary(
                        financial_summary, run_id
                    )
                    if commitment_rows:
                        await db.execute(
                            insert(FinancialCommitment).values(commitment_rows)
                        )
                        commitment_count = len(commitment_rows)

                await db.execute(
                    update(AnalysisRun)
                    .where(AnalysisRun.id == run_id)
                    .values(
                        state="awaiting_hitl",
                        feasibility_score=final_checkpoint.values.get(
                            "feasibility_score"
                        ),
                    )
                )
                # Single commit — INSERTs (risk_findings, financial_commitments)
                # + UPDATE land atomically across all four operations.
                await db.commit()

                await event_bus.publish_event(run_id_str, make_event(
                    run_id_str, "awaiting_hitl",
                    data={
                        "feasibility_score": final_checkpoint.values.get(
                            "feasibility_score"
                        ),
                        "risk_count": len(
                            final_checkpoint.values.get("risk_findings", [])
                        ),
                    }
                ))

                # Log metadata only — NEVER clause_text, explanation, amount_value
                # or amount_currency (REQ-004 / REQ-006 Security NFR,
                # ai-security T5).
                logger.info(
                    "analysis_run_awaiting_hitl run_id=%s finding_count=%d "
                    "commitment_count=%d",
                    run_id,
                    len(findings_dicts),
                    commitment_count,
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


async def _resume_graph(
    run_id: str,
    override_score: float | None,
) -> None:
    """Background worker: resumes the LangGraph pipeline from the HITL gate.

    Uses its own AsyncSession — not the request session — consistent with
    the run_graph() pattern (REQ-003 Slice 2 Rules).

    Injects hitl_approved=True (and optionally hitl_override_score) into the
    checkpoint state, then resumes graph.astream(None, config) from the
    existing checkpoint. The report_assembler node runs and the run
    transitions to "complete" (or "failed" on error).

    The hitl_overrides row is NEVER deleted on failure — the audit log is
    preserved even if the resume fails (REQ-007 Reliability NFR).
    """
    from app.agents.graph import graph

    event_bus = get_event_bus()
    run_id_str = str(run_id)

    async with with_session() as db:
        try:
            await event_bus.publish_event(run_id_str, make_event(
                run_id_str, "resuming",
                data={
                    "action": "overridden" if override_score is not None else "approved",
                    "effective_score": override_score,
                }
            ))

            config = {"configurable": {"thread_id": run_id_str}}

            update_values: dict = {"hitl_approved": True}
            if override_score is not None:
                update_values["hitl_override_score"] = override_score

            await graph.aupdate_state(config, update_values)

            async for event in graph.astream(None, config):
                node_name = list(event.keys())[0]
                if node_name.startswith("__"):
                    continue
                await db.execute(
                    update(AnalysisRun)
                    .where(AnalysisRun.id == run_id)
                    .values(
                        agent_trace=AnalysisRun.agent_trace.concat(
                            {node_name: event[node_name]}
                        )
                    )
                )
                await db.commit()

            await db.execute(
                update(AnalysisRun)
                .where(AnalysisRun.id == run_id)
                .values(state="complete", completed_at=func.now())
            )
            await db.commit()

            final_state = await graph.aget_state(config)
            report = (final_state.values.get("final_report", {}) if final_state is not None else {}) or {}
            await event_bus.publish_event(run_id_str, make_event(
                run_id_str, "complete",
                data={
                    "go_no_go": report.get("go_no_go", "REVIEW"),
                    "effective_score": report.get("effective_score", 0.0),
                }
            ))

        except Exception as e:
            await event_bus.publish_event(run_id_str, make_event(
                run_id_str, "failed",
                data={"error_reason": str(e)}
            ))
            await db.execute(
                update(AnalysisRun)
                .where(AnalysisRun.id == run_id)
                .values(state="failed", error_reason=f"Resume failed: {str(e)}")
            )
            await db.commit()


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
        feasibility_score=run.feasibility_score,
        agent_trace=run.agent_trace or {},
        # REQ-008 Slice 3: True only when the run is complete AND the
        # report_assembler node has populated agent_trace. The frontend uses
        # this to navigate to the report page without an extra GET /report
        # call. `agent_trace` may be an empty dict on a failed run — that
        # must not be reported as "report_available".
        report_available=(
            run.state == "complete"
            and bool((run.agent_trace or {}).get("report_assembler"))
        ),
    )


@router.get(
    "/{tender_id}/findings",
    response_model=list[RiskFindingResponse],
    summary="Get the Risk Radar findings for a tender's latest analysis run.",
)
async def get_findings(
    tender_id: Annotated[str, Path()],
    company: CompanyDep,
    session: SessionDep,
) -> list[RiskFindingResponse]:
    """Return risk findings for the latest analysis run, tenant-scoped.

    Ordering: critical -> high -> medium -> low, then by `confidence` DESC
    within each severity group (a CASE expression preserves the enum's
    business ordering that an alphabetical sort would scramble).
    """
    # a) + b) Latest analysis run for this tender.
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

    # c) Authorisation — the run carries its own company_id for tenant scoping.
    # 403 (not 404) here is intentional: the run exists, the caller just
    # belongs to a different tenant. Compare with get_analysis_status above
    # which returns 404 only when the run itself is missing.
    if run.company_id != company.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorised to view findings for this tender.",
        )

    # d) Findings for this run, ordered by severity then confidence DESC.
    # The CASE expression keeps the fixed business ordering of the severity
    # enum (critical first); an ORDER BY severity ASC would put 'critical'
    # last because 'c' < 'h' < 'l' < 'm' alphabetically.
    severity_order = case(
        (RiskFinding.severity == "critical", 1),
        (RiskFinding.severity == "high", 2),
        (RiskFinding.severity == "medium", 3),
        (RiskFinding.severity == "low", 4),
        else_=5,
    )
    findings_result = await session.execute(
        select(RiskFinding)
        .where(RiskFinding.run_id == run.id)
        .order_by(severity_order.asc(), RiskFinding.confidence.desc())
    )
    return [RiskFindingResponse.model_validate(f) for f in findings_result.scalars().all()]


@router.get(
    "/{tender_id}/financial",
    response_model=list[FinancialCommitmentResponse],
    summary="Get the financial commitments for a tender's latest analysis run.",
)
async def get_financial(
    tender_id: Annotated[str, Path()],
    company: CompanyDep,
    session: SessionDep,
) -> list[FinancialCommitmentResponse]:
    """Return financial commitments for the latest analysis run, tenant-scoped.

    Ordering: commitment_type ASC, id ASC — groups all rows of the same
    type together in a stable order.
    """
    # a) + b) Latest analysis run for this tender.
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

    # c) Authorisation — the run carries its own company_id for tenant scoping.
    if run.company_id != company.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorised to view financial commitments for this tender.",
        )

    # d) State gate — commitments only exist after the awaiting_hitl transition.
    if run.state not in ("awaiting_hitl", "complete"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Financial summary not yet available.",
        )

    # e) Commitments for this run, ordered by type then id (stable within a type).
    commitments_result = await session.execute(
        select(FinancialCommitment)
        .where(FinancialCommitment.run_id == run.id)
        .order_by(
            FinancialCommitment.commitment_type.asc(),
            FinancialCommitment.id.asc(),
        )
    )
    return [
        FinancialCommitmentResponse.model_validate(c)
        for c in commitments_result.scalars().all()
    ]


@router.get(
    "/{tender_id}/hitl-override",
    response_model=HITLOverrideResponse,
    summary="Get the HITL override decision for the latest analysis run.",
)
async def get_hitl_override(
    tender_id: Annotated[str, Path()],
    company: CompanyDep,
    session: SessionDep,
) -> HITLOverrideResponse:
    """Return the HITL override decision for the latest analysis run, tenant-scoped.

    404 if no override exists yet — the run may not have reached the HITL
    gate or the analyst has not yet acted. justification is NEVER included
    in the response (REQ-007 Security NFR).
    """
    # a) + b) Latest analysis run for this tender.
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

    # c) Authorisation — the run belongs to the caller's company.
    if run.company_id != company.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorised to view this HITL override.",
        )

    # d) Query hitl_overrides for this run.
    override_result = await session.execute(
        select(HITLOverride).where(HITLOverride.run_id == run.id)
    )
    override = override_result.scalar_one_or_none()
    if override is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No HITL override has been recorded for this run.",
        )

    return HITLOverrideResponse.model_validate(override)


@router.get(
    "/{tender_id}/report",
    response_model=ReportResponse,
    summary="Get the Report Assembler's Go/No-Go brief for a tender's latest run.",
)
async def get_report(
    tender_id: Annotated[str, Path()],
    company: CompanyDep,
    session: SessionDep,
) -> ReportResponse:
    """Return the Go/No-Go brief produced by the report_assembler node.

    The report is read from
    ``analysis_runs.agent_trace["report_assembler"]["final_report"]``
    which the node writes after HITL approval (REQ-008 Slice 2). The
    endpoint is HTTP 200 only when ``run.state == "complete"``; before
    that the router returns 404 — the report page polls this endpoint
    and 404 is the expected "not ready" signal, consistent with
    GET /tenders/{id}/financial in REQ-006.

    Security:
    - Tenant-scoped: returns 403 if the run belongs to another company.
    - Never logs raw risk-clause text or financial commitment values
      (REQ-006 / REQ-008 Security NFR). Only metadata (run_id, state,
      report_data presence) is logged.
    - The report_assembler may produce a fallback report on LLM failure;
      this endpoint surfaces that fallback as-is.
    """
    # a) + b) Fetch latest analysis_run for this tender.
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

    # c) Authorisation — the run carries its own company_id for tenant
    # scoping. 403 (not 404) here is intentional: the run exists, the
    # caller just belongs to a different tenant.
    if run.company_id != company.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorised to view report for this tender.",
        )

    # d) State gate — report only exists after the run is "complete".
    # 404 (not 409) is the polling signal (see slice spec). The frontend
    # expects 404 here for "not ready" and treats it as a normal
    # poll-result, not an error.
    if run.state != "complete":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Report not yet available. Current state: {run.state}."
            ),
        )

    # e) Extract report data with defensive .get() at every level.
    # agent_trace may be an empty dict on a failed run, and the
    # "report_assembler" key may be missing or its value may not be a
    # dict (legacy or partial writes). Never assume structure.
    report_data = (
        (run.agent_trace or {})
        .get("report_assembler", {})
        .get("final_report", {})
    ) or {}

    # f) If the trace exists but has no final_report, the run is
    # malformed (shouldn't happen in practice — `_resume_graph` sets
    # state="complete" after the report_assembler node finishes). Treat
    # it the same as "not ready" from the caller's perspective.
    if not report_data:
        logger.warning(
            "get_report_missing_final_report run_id=%s state=%s",
            run.id,
            run.state,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report data not found in run trace.",
        )

    # g) Build and return the typed ReportResponse. Every field uses
    # .get() with safe defaults so a partial trace (e.g. fallback report
    # written by the assembler after an LLM failure) still serialises.
    # We log metadata only — never the actual report body, which may
    # contain risk-clause paraphrases and financial commitment values.
    logger.info(
        "get_report_served run_id=%s go_no_go=%s override=%s "
        "risk_count=%d highlight_counts=fea:%d/fin:%d",
        run.id,
        report_data.get("go_no_go", "REVIEW"),
        report_data.get("is_analyst_override", False),
        len(report_data.get("risk_summary", []) or []),
        len(report_data.get("feasibility_highlights", []) or []),
        len(report_data.get("financial_highlights", []) or []),
    )

    return ReportResponse(
        run_id=run.id,
        tender_id=tender_id,
        go_no_go=report_data.get("go_no_go", "REVIEW"),
        effective_score=float(
            report_data.get("effective_score", 0.0)
        ),
        is_analyst_override=bool(
            report_data.get("is_analyst_override", False)
        ),
        executive_summary=report_data.get("executive_summary", "") or "",
        recommendation=report_data.get("recommendation", "") or "",
        risk_summary=[
            RiskSummaryItemResponse(**r)
            for r in (report_data.get("risk_summary", []) or [])
            if isinstance(r, dict)
        ],
        feasibility_highlights=list(
            report_data.get("feasibility_highlights", []) or []
        ),
        financial_highlights=list(
            report_data.get("financial_highlights", []) or []
        ),
        analyst_note=report_data.get("analyst_note"),
        completed_at=run.completed_at,
    )


@router.post(
    "/{tender_id}/approve",
    response_model=HITLResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Approve the AI feasibility score as-is and resume the analysis.",
)
async def approve_analysis(
    background_tasks: BackgroundTasks,
    tender_id: Annotated[str, Path()],
    request: ApproveRequest,
    company: CompanyDep,
    session: SessionDep,
) -> HITLResponse:
    """Approve the AI score without modification. Graph resumes from checkpoint.

    HTTP 202 returned immediately; the Report Assembler runs as a background
    task. The hitl_overrides row is an immutable audit record (REQ-007).
    """
    # b) Fetch latest analysis_run for this tender_id.
    run_result = await session.execute(
        select(AnalysisRun)
        .where(AnalysisRun.tender_id == tender_id)
        .order_by(AnalysisRun.started_at.desc())
        .limit(1)
    )
    run = run_result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No analysis run found for this tender.",
        )

    # c) Authorisation — the run belongs to the caller's company.
    if run.company_id != company.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorised to act on this tender.",
        )

    # d) State check — must be awaiting_hitl.
    if run.state != "awaiting_hitl":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run is not awaiting review. Current state: {run.state}.",
        )

    # e) Check no existing hitl_overrides row for this run_id.
    existing = await session.execute(
        select(HITLOverride.id).where(HITLOverride.run_id == run.id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This run has already been reviewed.",
        )

    if run.feasibility_score is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Feasibility score is not available for this run.",
        )

    original_score = run.feasibility_score

    # f) Write hitl_overrides row — action="approved".
    override = HITLOverride(
        run_id=run.id,
        analyst_company_id=company.id,
        action="approved",
        original_score=original_score,
        overridden_score=None,
        justification=request.justification,
    )
    session.add(override)

    # g) Update analysis_runs.state = "resuming" (prevents double-approval).
    run.state = "resuming"

    # h) Commit BEFORE launching background task.
    await session.commit()

    # i) Launch background task with its own session.
    background_tasks.add_task(_resume_graph, run.id, None)

    # j) Return HTTP 202 immediately.
    return HITLResponse(
        run_id=run.id,
        action="approved",
        original_score=original_score,
        overridden_score=None,
        message="Report assembly started.",
    )


@router.post(
    "/{tender_id}/override",
    response_model=HITLResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Override the AI feasibility score and resume the analysis.",
)
async def override_analysis(
    background_tasks: BackgroundTasks,
    tender_id: Annotated[str, Path()],
    request: OverrideRequest,
    company: CompanyDep,
    session: SessionDep,
) -> HITLResponse:
    """Override the AI score with an analyst-adjusted value. Graph resumes.

    HTTP 202 returned immediately; the Report Assembler runs with the
    analyst's overridden_score instead of the AI feasibility_score.
    The hitl_overrides row is an immutable audit record (REQ-007).
    """
    # b) Fetch latest analysis_run for this tender_id.
    run_result = await session.execute(
        select(AnalysisRun)
        .where(AnalysisRun.tender_id == tender_id)
        .order_by(AnalysisRun.started_at.desc())
        .limit(1)
    )
    run = run_result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No analysis run found for this tender.",
        )

    # c) Authorisation — the run belongs to the caller's company.
    if run.company_id != company.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorised to act on this tender.",
        )

    # d) State check — must be awaiting_hitl.
    if run.state != "awaiting_hitl":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run is not awaiting review. Current state: {run.state}.",
        )

    # e) Check no existing hitl_overrides row for this run_id.
    existing = await session.execute(
        select(HITLOverride.id).where(HITLOverride.run_id == run.id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This run has already been reviewed.",
        )

    if run.feasibility_score is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Feasibility score is not available for this run.",
        )

    original_score = run.feasibility_score

    # g) Write hitl_overrides row — action="overridden".
    override = HITLOverride(
        run_id=run.id,
        analyst_company_id=company.id,
        action="overridden",
        original_score=original_score,
        overridden_score=request.overridden_score,
        justification=request.justification,
    )
    session.add(override)

    # h) Update analysis_runs.state = "resuming" (prevents double-approval).
    run.state = "resuming"

    # i) Commit BEFORE launching background task.
    await session.commit()

    # j) Launch background task with its own session.
    background_tasks.add_task(
        _resume_graph, run.id, request.overridden_score
    )

    # k) Return HTTP 202 immediately.
    return HITLResponse(
        run_id=run.id,
        action="overridden",
        original_score=original_score,
        overridden_score=request.overridden_score,
        message="Report assembly started.",
    )
