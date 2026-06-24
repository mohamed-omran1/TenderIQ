"""Ingestor node — PDF → chunks → embeddings → pgvector, with atomic cleanup.

This is the data-loading half of the TenderIQ pipeline (PRD §5.2). It runs as
a FastAPI BackgroundTask triggered by POST /tenders/upload. It is NOT yet wired
into the compiled LangGraph graph (that lands in Week 2 with the analysis
agents); for REQ-001 it's a standalone async function.

Failure contract (REQ-001 Postconditions): every path terminates in `ready` or
`failed`. On failure we DELETE any tender_chunks already inserted for this
tender (so no partial/orphaned rows survive) and persist a human-readable
reason on the tenders row. Embeddings happen in batches across many calls, so
we can't wrap the whole run in one DB transaction — cleanup-on-exception is the
correct atomicity model here.
"""
from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy import delete, select

from app.agents.embeddings import EmbeddingUnavailable, get_embeddings_client
from app.db.models import Tender, TenderChunk
from app.db.session import with_session
from app.services.chunking import Chunk, chunk_page
from app.services.language import detect_language, primary_language
from app.services.pdf import (
    ExtractedPage,
    PdfExtractionError,
    ScannedPdfError,
    extract_pages,
)

logger = logging.getLogger(__name__)


class IngestionFailed(Exception):
    """Wraps any failure with a human-readable reason for tenders.error_reason."""


async def _load_tender(session, tender_id: str) -> Tender:
    """Fetch the tender or raise IngestionFailed (can't ingest what doesn't exist)."""
    tender = (
        await session.execute(select(Tender).where(Tender.id == tender_id))
    ).scalar_one_or_none()
    if tender is None:
        raise IngestionFailed(f"Tender {tender_id} not found.")
    return tender


async def _set_status(
    session, tender_id: str, status: str, **fields
) -> None:
    """Update the tender's status (and optional fields) and commit."""
    values: dict = {"status": status}
    values.update(fields)
    await session.execute(
        Tender.__table__.update().where(Tender.id == tender_id).values(**values)
    )
    await session.commit()


async def _cleanup_chunks(session, tender_id: str) -> None:
    """Delete any chunks inserted for this tender — the 'no orphan rows' rule."""
    await session.execute(
        delete(TenderChunk).where(TenderChunk.tender_id == tender_id)
    )
    await session.commit()


async def _fail(session, tender_id: str, reason: str) -> None:
    """Terminal failure path: clean up chunks, mark failed with a reason."""
    logger.warning("ingestion_failed tender_id=%s reason=%s", tender_id, reason)
    await _cleanup_chunks(session, tender_id)
    await _set_status(session, tender_id, "failed", error_reason=reason)


def _build_chunks(pages: list[ExtractedPage]) -> list[Chunk]:
    """Chunk every page, then detect language per chunk (after overlap)."""
    chunks: list[Chunk] = []
    for page in pages:
        chunks.extend(chunk_page(page.text, page.page_number))
    # Detect language per chunk now that overlap is applied.
    return [
        Chunk(
            content=c.content,
            page_number=c.page_number,
            detected_language=detect_language(c.content),
        )
        for c in chunks
    ]


def _aggregate_languages(chunks: list[Chunk]) -> tuple[str, list[str]]:
    """Return (primary_language, source_languages[]) for the tenders row."""
    counts = Counter(c.detected_language for c in chunks)
    primary = primary_language(dict(counts))
    # source_languages is the distinct set of detected scripts.
    distinct = sorted({c.detected_language for c in chunks} - {"mixed"})
    if not distinct:
        # All chunks were 'mixed' or empty — record both possibilities.
        distinct = ["ar", "en"]
    return primary, distinct


async def ingest_tender(tender_id: str, embeddings=None) -> None:
    """Run the full ingestion pipeline for one tender.

    `embeddings` is injectable so tests pass a stub (senior-qa skill). When
    None, we build the real Gemini client via get_embeddings_client().
    """
    embeddings = embeddings or get_embeddings_client()

    async with with_session() as session:
        tender = await _load_tender(session, tender_id)

        # Mark processing up front so the row never sits in 'uploading' forever
        # (REQ-001 Reliability NFR).
        await _set_status(session, tender_id, "processing")

        # --- 1. Read the stored PDF bytes ---
        try:
            pdf_bytes = _read_stored_pdf(tender.storage_path)
        except FileNotFoundError:
            await _fail(session, tender_id, "Uploaded file not found on disk.")
            return

        # --- 2. Extract text page-by-page (raises on corrupt / scanned) ---
        try:
            pages = extract_pages(pdf_bytes)
        except ScannedPdfError as exc:
            await _fail(session, tender_id, str(exc))
            return
        except PdfExtractionError as exc:
            await _fail(session, tender_id, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — defensive: never leave uploading/processing
            await _fail(session, tender_id, f"Extraction error: {type(exc).__name__}")
            return

        # --- 3. Chunk + detect language ---
        chunks = _build_chunks(pages)
        if not chunks:
            await _fail(
                session,
                tender_id,
                "No extractable text found (PDF may be scanned). OCR is not supported in MVP.",
            )
            return

        # --- 4. Embed all chunk texts in batches (with retry/backoff) ---
        try:
            vectors = embeddings.embed_documents([c.content for c in chunks])
        except EmbeddingUnavailable as exc:
            await _fail(
                session,
                tender_id,
                f"Embedding service unavailable after retries: {exc}",
            )
            return
        except Exception as exc:  # noqa: BLE001
            await _fail(
                session,
                tender_id,
                f"Embedding failed after retries: {type(exc).__name__}",
            )
            return

        if len(vectors) != len(chunks):
            await _fail(
                session,
                tender_id,
                f"Embedding count mismatch: {len(vectors)} vectors for {len(chunks)} chunks.",
            )
            return

        # --- 5. Persist chunks (embeddings non-null by construction) ---
        primary_lang, source_langs = _aggregate_languages(chunks)
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            session.add(
                TenderChunk(
                    tender_id=tender_id,
                    company_id=tender.company_id,  # denormalized — see models.py docstring
                    chunk_index=i,
                    content=chunk.content,
                    detected_language=chunk.detected_language,
                    page_number=chunk.page_number,
                    embedding=vec,
                )
            )
        # page_count and primary_language land alongside the terminal status.
        await _set_status(
            session,
            tender_id,
            "ready",
            primary_language=primary_lang,
            page_count=len(pages),
        )
        # Log metadata only — never chunk content (ai-security T5, REQ-001 NFR).
        logger.info(
            "ingestion_complete tender_id=%s chunks=%d primary_language=%s source=%s",
            tender_id,
            len(chunks),
            primary_lang,
            source_langs,
        )


def _read_stored_pdf(path: str) -> bytes:
    """Read the stored PDF bytes. Raises FileNotFoundError if missing."""
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    return p.read_bytes()
