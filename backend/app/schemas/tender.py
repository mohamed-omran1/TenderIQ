"""Pydantic v2 request/response models for the tenders router.

Response models are explicit (never `return orm_object.dict()`) so we never
leak `api_key_hash`, internal storage paths, or another tenant's data
(api-security-reviewer skill, API3).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TenderUploadResponse(BaseModel):
    """Returned by POST /tenders/upload — HTTP 202 Accepted."""

    model_config = ConfigDict(from_attributes=True)

    tender_id: str = Field(..., description="Stable UUID; references all downstream endpoints.")
    status: Literal["uploading", "processing", "ready", "failed"]


class TenderDetailResponse(BaseModel):
    """Returned by GET /tenders/{id}. Tenant-scoped — only the owner's tenders."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    status: Literal["uploading", "processing", "ready", "failed"]
    primary_language: Literal["ar", "en", "bilingual"] | None = None
    page_count: int | None = None
    file_size_bytes: int
    error_reason: str | None = None
    uploaded_at: datetime
