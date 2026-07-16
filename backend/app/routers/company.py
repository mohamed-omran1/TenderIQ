"""Company-profile router — GET + PUT /company-profile (REQ-002 Slice 1).

Implements the REQ-002 Main Flow:
  * GET  resolves company_id from the API key, returns the stored profile — or,
    if none exists yet, an empty-profile shape (all nulls/empties) so the
    frontend can render a blank form without null-checks. Never 404.
  * PUT  validates the body, then upserts with a SINGLE atomic
    INSERT ... ON CONFLICT (company_id) DO UPDATE statement — never a
    check-then-insert. `company_id` comes from the API key, `updated_at` is set
    server-side; neither is ever read from the request body.

Security (REQ-002 + api-security-reviewer):
  * company_id is always derived from get_current_company — never from the body.
  * financial_capacity is sensitive; we log only metadata (company_id + whether
    it was a create vs. update), never the payload. The ORM __repr__ redacts it
    too, so even an unhandled traceback can't leak it.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Company, CompanyProfile
from app.db.session import get_session
from app.middleware.auth import get_current_company
from app.schemas.company import CompanyProfileSchema, EmptyProfileResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/company-profile", tags=["company-profile"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CompanyDep = Annotated[Company, Depends(get_current_company)]


@router.get(
    "",
    response_model=CompanyProfileSchema | EmptyProfileResponse,
    summary="Get the caller's company profile (empty shape if none exists).",
)
async def get_company_profile(
    company: CompanyDep,
    session: SessionDep,
) -> CompanyProfileSchema | EmptyProfileResponse:
    """Return the authenticated company's profile.

    Tenant-scoped by construction: `company_id` is the API-key-derived tenant id
    and the sole primary key, so another tenant's row is unreachable here. If no
    row exists we return HTTP 200 with an empty-profile body (REQ-002 Main Flow
    step 5 / Usability NFR) — explicitly NOT a 404.
    """
    result = await session.execute(
        select(CompanyProfile).where(CompanyProfile.company_id == company.id)
    )
    profile = result.scalar_one_or_none()

    if profile is None:
        # No profile yet: stable, non-null root shape for the frontend form.
        return EmptyProfileResponse(company_id=company.id)

    return CompanyProfileSchema.model_validate(profile)


@router.put(
    "",
    response_model=CompanyProfileSchema,
    summary="Create or replace the caller's company profile (atomic upsert).",
)
async def upsert_company_profile(
    body: CompanyProfileSchema,
    company: CompanyDep,
    session: SessionDep,
) -> CompanyProfileSchema:
    """Upsert the authenticated company's profile in one atomic statement.

    Uses PostgreSQL `INSERT ... ON CONFLICT (company_id) DO UPDATE` — a single
    statement, so there is no check-then-insert race window and no partial write
    (REQ-002 Reliability NFR). Concurrent PUTs are last-writer-wins at MVP.

    `updated_at` is set server-side in the UPDATE clause AND by a DB trigger; it
    is never read from the request body (`extra="forbid"` rejects it anyway).
    """
    # Build the row from the validated body + server-derived tenant id only.
    values = {
        "company_id": company.id,
        "specializations": body.specializations,
        "financial_capacity": body.financial_capacity.model_dump(),
        "geographic_reach": body.geographic_reach,
        "past_projects": [p.model_dump() for p in body.past_projects],
        "max_project_value": body.max_project_value,
    }

    # Bind the INSERT first so we can reference `.excluded` (the EXCLUDED.<col>
    # pseudo-table) in the conflict clause below. One statement, one round-trip.
    stmt = pg_insert(CompanyProfile).values(**values)
    stmt = (
        stmt.on_conflict_do_update(
            index_elements=[CompanyProfile.company_id],
            # EXCLUDED.<col> = the value we tried to INSERT; write the new
            # payload on the conflict branch. Server-side updated_at below.
            set_={
                "specializations": stmt.excluded.specializations,
                "financial_capacity": stmt.excluded.financial_capacity,
                "geographic_reach": stmt.excluded.geographic_reach,
                "past_projects": stmt.excluded.past_projects,
                "max_project_value": stmt.excluded.max_project_value,
                # Server-side; also enforced by the BEFORE UPDATE trigger.
                "updated_at": func.now(),
            },
        )
        # Return the freshly written row so the response reflects exactly what
        # was stored (including the server-set updated_at), without a second
        # round-trip. Supported via the dialect's RETURNING.
        .returning(CompanyProfile)
    )

    result = await session.execute(stmt)
    profile = result.scalar_one()
    await session.commit()

    # Metadata only — never the body or financial_capacity (REQ-002 Security NFR).
    logger.info(
        "company_profile_upserted company_id=%s updated_at=%s",
        company.id,
        profile.updated_at.isoformat() if profile.updated_at else None,
    )

    return CompanyProfileSchema.model_validate(profile)
