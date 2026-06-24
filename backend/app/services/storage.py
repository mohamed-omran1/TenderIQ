"""Raw file storage on local volume (Architecture §5.1: local for MVP, R2 later).

Files live under `{storage_dir}/{company_id}/{tender_id}.pdf` — the company_id
segment enforces tenant separation on disk, mirroring the DB-level isolation.
"""
from __future__ import annotations

from pathlib import Path

from app.config import get_settings


def tender_storage_path(company_id: str, tender_id: str) -> Path:
    """Where a tender's raw PDF lives on disk."""
    base = get_settings().storage_path / company_id
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{tender_id}.pdf"


async def save_upload(data: bytes, company_id: str, tender_id: str) -> Path:
    """Persist the uploaded PDF bytes to disk and return the path."""
    path = tender_storage_path(company_id, tender_id)
    # Async-friendly: writes are small (<=50MB) and disk-bound; offloading to a
    # thread would add complexity without helping at MVP scale.
    path.write_bytes(data)
    return path
