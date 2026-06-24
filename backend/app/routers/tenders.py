"""Tenders router — POST /tenders/upload and GET /tenders/{id}.

Implements REQ-001 Main Flow steps 1–5 and 10:
  upload validates + stores + inserts row + schedules ingestion, returns 202.
  get_status is tenant-scoped polling for `ready` / `failed`.
"""
from __future__ import annotations

import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.ingestion import run_ingestion
from app.config import Settings, get_settings
from app.db.models import Company, Tender
from app.db.session import get_session
from app.errors import NotFound, QuotaExceeded, RateLimited
from app.middleware.auth import get_current_company
from app.middleware.rate_limit import check_rate_limit
from app.schemas.tender import TenderDetailResponse, TenderUploadResponse
from app.services.storage import save_upload
from app.services.validation import reject_oversize_declared, sanitize_filename, validate_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenders", tags=["tenders"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CompanyDep = Annotated[Company, Depends(get_current_company)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


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
