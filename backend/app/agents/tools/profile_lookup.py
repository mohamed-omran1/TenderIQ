"""LangChain tool that retrieves a company's benchmarking profile.

This tool is consumed by the Feasibility Scorer node (REQ-002). It is
independently callable outside the full LangGraph graph so that scorer logic
can be unit-tested without starting the HTTP app.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.tools import tool

from app.db.models import CompanyProfile
from app.db.session import SessionLocal
from app.schemas.company import CompanyProfileSchema


@tool
async def profile_lookup(company_id: str) -> CompanyProfileSchema:
    """Retrieves the company benchmarking profile used by the Feasibility Scorer to evaluate tender fit.

    Args:
        company_id: The UUID of the company whose profile should be fetched.

    Returns:
        A validated CompanyProfileSchema populated with the stored profile data.

    Raises:
        ValueError: If no profile exists for the given company_id.
    """
    async with SessionLocal() as session:
        session: AsyncSession
        result = await session.execute(
            select(CompanyProfile).where(CompanyProfile.company_id == company_id)
        )
        profile = result.scalar_one_or_none()

    if profile is None:
        raise ValueError(
            f"No company profile found for company_id={company_id}. "
            "The profile must be created before running an analysis."
        )

    return CompanyProfileSchema.model_validate(profile)
