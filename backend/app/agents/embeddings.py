"""Embeddings client — Gemini `gemini-embedding-001` at 768 dims.

LangChain's `GoogleGenerativeAIEmbeddings` does NOT retry on its own, and the
Gemini free tier is restrictive (15 RPM / 1500 RPD). We wrap `embed_documents`
in a tenacity retry with exponential backoff on 429 RESOURCE_EXHAUSTED.

The client is built via a factory so tests stub it (senior-qa skill: never hit
real providers in CI). Tests monkeypatch `get_embeddings_client`.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

from langchain_core.embeddings import Embeddings
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings

logger = logging.getLogger(__name__)

# LangChain caps each batch at 100 strings internally; keep ours smaller to be
# gentle on the free-tier rate limit (the API's inline endpoint takes ~5/call).
EMBED_BATCH_SIZE = 8


class EmbeddingUnavailable(Exception):
    """Raised when the embedding provider returns a non-retriable error after
    retries are exhausted. The ingestor catches this to fail the tender run."""


def _is_rate_limited(exc: BaseException) -> bool:
    """Tenacity predicate: is this a 429 we should retry?"""
    msg = str(exc)
    # google-genai raises ClientError with "429" / "RESOURCE_EXHAUSTED" inside.
    return "429" in msg or "RESOURCE_EXHAUSTED" in msg or "rate" in msg.lower()


class RetryingEmbeddings:
    """Wrap an Embeddings instance with batched, retrying embed_documents.

    We retry only on rate-limit signals; other errors propagate so the ingestor
    fails fast with a useful reason instead of burning all retries on a 400.
    """

    def __init__(self, base: Embeddings, batch_size: int = EMBED_BATCH_SIZE) -> None:
        self._base = base
        self._batch_size = batch_size

        self._embed_one_batch = retry(
            stop=stop_after_attempt(4),  # 1 initial + 3 retries (REQ-001 Alt Flow 6)
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type(Exception),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )(self._embed_batch)

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Re-raise non-rate-limit errors immediately as EmbeddingUnavailable so
        # tenacity's `retry=retry_if_exception_type(Exception)` won't loop on them.
        try:
            return self._base.embed_documents(texts)
        except Exception as exc:
            if _is_rate_limited(exc):
                raise  # tenacity retries this
            raise EmbeddingUnavailable(str(exc)) from exc

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts, batching to stay under the rate limit."""
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            out.extend(self._embed_one_batch(batch))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._base.embed_query(text)


def get_embeddings_client() -> RetryingEmbeddings:
    """Factory: build the Gemini embeddings client from settings.

    Tests override this (monkeypatch `app.agents.embeddings.get_embeddings_client`)
    to return a deterministic stub. Production reads GOOGLE_API_KEY from env.
    """
    settings = get_settings()
    # Lazy import keeps test collection fast and avoids requiring the SDK at
    # import time when no key is configured.
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    if not settings.google_api_key:
        raise EmbeddingUnavailable(
            "GOOGLE_API_KEY is not configured. Set it in .env to enable embeddings."
        )

    base = GoogleGenerativeAIEmbeddings(
        model=settings.embedding_model,           # "gemini-embedding-001"
        google_api_key=settings.google_api_key,
        output_dimensionality=settings.embedding_dimensions,  # 768
        task_type="RETRIEVAL_DOCUMENT",
    )
    return RetryingEmbeddings(base)


def make_stub_embeddings(dim: int, value: float = 0.01) -> RetryingEmbeddings:
    """Build a deterministic stub embeddings client for tests.

    Produces constant vectors of the right dimension so pgvector columns accept
    them. Never used in production.
    """

    class _Stub(Embeddings):
        def embed_documents(self, texts: Iterable[str]) -> list[list[float]]:  # type: ignore[override]
            return [[value] * dim for _ in texts]

        def embed_query(self, text: str) -> list[float]:  # type: ignore[override]
            return [value] * dim

    return RetryingEmbeddings(_Stub(), batch_size=64)
