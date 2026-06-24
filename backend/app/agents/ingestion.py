"""Background ingestion entrypoint.

`run_ingestion(tender_id)` is the function scheduled by the upload router's
BackgroundTasks. It dispatches to the real ingestor node (Slice 2) by default.
A Slice-1 stub (immediate 'ready' with zero chunks) is available opt-in via the
`TENDERIQ_USE_STUB_INGESTOR` env var — used by tests that only care about the
upload endpoint, not the ingestion pipeline.

This file is the seam: the router never imports the ingestor node directly,
only `run_ingestion`. That keeps the contract stable.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import select

from app.db.models import Tender
from app.db.session import with_session

logger = logging.getLogger(__name__)


def _stub_enabled() -> bool:
    """Slice-1 stub is opt-in via env (default: real ingestor)."""
    return os.getenv("TENDERIQ_USE_STUB_INGESTOR", "").lower() in {"1", "true", "yes"}


async def run_ingestion(tender_id: str) -> None:
    """Run the ingestion pipeline for one tender.

    Real path (default): processing -> extract -> chunk -> embed -> persist ->
    ready (or failed with cleanup). Stub path: immediately ready, 0 chunks.
    """
    if _stub_enabled():
        await _run_stub(tender_id)
        return

    from app.agents.nodes.ingestor import ingest_tender

    await ingest_tender(tender_id)


async def _run_stub(tender_id: str) -> None:
    """Slice-1 stub: mark tender ready with zero chunks. Tests only."""
    async with with_session() as session:
        result = await session.execute(select(Tender).where(Tender.id == tender_id))
        tender = result.scalar_one_or_none()
        if tender is None:
            logger.warning("ingestion_skipped_no_tender tender_id=%s", tender_id)
            return

        tender.status = "ready"
        await session.commit()
        logger.info(
            "ingestion_stub_complete tender_id=%s filename=%s chunks=0",
            tender_id,
            tender.filename,
        )
