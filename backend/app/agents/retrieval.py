"""Anchor-query pgvector retrieval for the Risk Radar node (REQ-004 Slice 2).

This module is the one piece of retrieval logic that the Risk Radar node uses
to surface risk-bearing chunks from a tender. It runs a small fixed set of
hand-curated "risk anchor" queries against the tender's chunks in pgvector,
rather than sending the entire tender to the LLM. This keeps token usage
proportional to risk density, not document length (REQ-004 Main Flow step 2).

The `chunks` argument is part of the function contract (see imp-slice-02) and
is also used as a defensive fallback if the database has no embedded rows for
this tender. In the normal case the in-memory chunks and the
`tender_chunks` table hold the same content; the DB is the source of truth
because it carries the pre-computed embeddings produced by the Ingestor
(REQ-001).
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.embeddings import EmbeddingUnavailable, get_embeddings_client
from app.db.models import TenderChunk
from app.db.session import with_session

logger = logging.getLogger(__name__)


# A small, hand-curated set of risk-bearing queries. Each one is intentionally
# distinct so the union of top-K results spans the major FIDIC risk surfaces
# (penalties, bonds/LGs, termination, damages) without redundancy.
RISK_ANCHOR_QUERIES: list[str] = [
    "penalty for delay in completion",
    "performance bond and letter of guarantee requirements",
    "termination for default or convenience",
    "liquidated damages and liability caps",
    "FIDIC sub-clause conditions",
]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity for the in-memory fallback path.

    Returns a value in [-1.0, 1.0]. We only need relative ordering for
    top-K selection, so no library is pulled in for the rare case where
    the DB is empty.
    """
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return -1.0
    return dot / (na * nb)


async def _retrieve_via_db(
    tender_id: str,
    company_id: str,
    query_vectors: list[list[float]],
    top_k_per_query: int,
) -> list[dict[str, Any]]:
    """Run per-query pgvector cosine-distance searches and union the results.

    Deduplicates by `chunk_index` — the same chunk can surface for several
    anchors. The first time we see a chunk_index wins; we keep the chunk's
    row id, content, language, and page number.
    """
    seen: dict[int, dict[str, Any]] = {}
    async with with_session() as session:
        session: AsyncSession
        for q_vec in query_vectors:
            stmt = (
                select(TenderChunk)
                .where(
                    TenderChunk.tender_id == tender_id,
                    TenderChunk.company_id == company_id,
                )
                .order_by(TenderChunk.embedding.cosine_distance(q_vec))
                .limit(top_k_per_query)
            )
            result = await session.execute(stmt)
            for chunk in result.scalars():
                if chunk.chunk_index in seen:
                    continue
                seen[chunk.chunk_index] = {
                    "chunk_id": chunk.id,
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "detected_language": chunk.detected_language,
                    "page_number": chunk.page_number,
                }
    return sorted(seen.values(), key=lambda c: c["chunk_index"])


async def _retrieve_via_memory(
    chunks: list[dict[str, Any]],
    query_vectors: list[list[float]],
    top_k_per_query: int,
) -> list[dict[str, Any]]:
    """In-memory cosine-similarity fallback for tests / DB-empty paths.

    Used only when the DB has no embedded rows for this tender. We assume
    the caller has not pre-computed embeddings for the chunks, so we embed
    them here on demand. Sorting matches the DB path: ascending by best
    distance across the union of per-query top-K.
    """
    if not chunks:
        return []
    embeddings = get_embeddings_client()
    chunk_texts = [c.get("content", "") for c in chunks]
    chunk_vecs = embeddings.embed_documents(chunk_texts)

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for q_vec in query_vectors:
        # Compute similarity per chunk and keep the top-K for this query.
        per_query = sorted(
            (
                (_cosine_similarity(q_vec, c_vec), c_idx, c)
                for c_idx, (c_vec, c) in enumerate(zip(chunk_vecs, chunks))
            ),
            key=lambda t: t[0],
            reverse=True,
        )[:top_k_per_query]
        scored.extend(per_query)

    # Union by chunk_index, keeping the best (highest) score per index.
    best_by_index: dict[int, tuple[float, dict[str, Any]]] = {}
    for score, _c_idx, chunk in scored:
        c_idx = chunk.get("chunk_index", _c_idx)
        existing = best_by_index.get(c_idx)
        if existing is None or score > existing[0]:
            best_by_index[c_idx] = (score, chunk)

    return [chunk for _, (score, chunk) in sorted(best_by_index.items())]


async def retrieve_risk_relevant_chunks(
    tender_id: str,
    chunks: list[dict],
    top_k_per_query: int = 5,
    *,
    company_id: str | None = None,
    embeddings: Any | None = None,
) -> list[dict]:
    """Return chunks most likely to contain risk-bearing clauses.

    Runs each of `RISK_ANCHOR_QUERIES` against this tender's embedded chunks
    and returns the union of the top `top_k_per_query` results per query,
    deduplicated by `chunk_index`.

    The DB path is used when `company_id` is provided AND the tender has
    embedded rows in `tender_chunks`. Otherwise we fall back to an in-memory
    similarity search over the in-memory `chunks` argument — useful for tests
    and for the no-embeddings edge case.

    Args:
        tender_id: UUID of the tender whose chunks to search.
        chunks: The in-memory chunks (used for the in-memory fallback path
            and to keep the slice spec signature stable). The DB chunks are
            the canonical source when they are available.
        top_k_per_query: Per-anchor-query top-K. Default 5 (slice spec).
        company_id: Tenant id, required for the secure DB path. When None
            we cannot scope a DB query safely (api-security-reviewer skill),
            so we fall back to the in-memory path.
        embeddings: Optional pre-built embeddings client (tests pass a stub).

    Returns:
        List of chunk dicts with keys: `chunk_id`, `chunk_index`, `content`,
        `detected_language`, `page_number`. Ordered by ascending chunk_index.
    """
    if not RISK_ANCHOR_QUERIES:
        return []

    embeddings = embeddings or get_embeddings_client()
    try:
        query_vectors = embeddings.embed_documents(RISK_ANCHOR_QUERIES)
    except EmbeddingUnavailable as exc:
        logger.warning(
            "retrieval_embedding_unavailable tender_id=%s reason=%s",
            tender_id,
            type(exc).__name__,
        )
        return []
    except Exception as exc:  # noqa: BLE001
        # The Ingestor already retries on rate-limit. Here we are a non-fatal
        # helper — surface nothing rather than crashing the whole graph run.
        logger.warning(
            "retrieval_embedding_failed tender_id=%s reason=%s",
            tender_id,
            type(exc).__name__,
        )
        return []

    if company_id is not None:
        try:
            db_results = await _retrieve_via_db(
                tender_id, company_id, query_vectors, top_k_per_query
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "retrieval_db_query_failed tender_id=%s reason=%s",
                tender_id,
                type(exc).__name__,
            )
            db_results = []
        if db_results:
            return db_results
        # DB had no rows for this tender — fall through to in-memory if we
        # were given any chunks to work with.
        if not chunks:
            return []

    return await _retrieve_via_memory(chunks, query_vectors, top_k_per_query)
