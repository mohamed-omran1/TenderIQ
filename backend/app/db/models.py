"""ORM models — TenderIQ canonical schema (PRD §7).

Multi-tenancy rule: every business table carries `company_id` and every query
filters by it. `tender_chunks.company_id` is denormalized on purpose (a
deliberate deviation from PRD §7) so vector retrievals can filter
`WHERE tender_id = $1 AND company_id = $2` without a join — both a perf and a
cross-tenant-leak defence (rag-architect + api-security-reviewer skills).

Closed value sets use TEXT + CHECK (not native ENUM): adding a value is a
plain migration instead of ALTER TYPE (database-designer skill).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db.base import Base, Vector

# Embedding dimension is pinned by the model choice (gemini-embedding-001 @ 768).
# Changing it = full re-embed of every tender_chunks row, not a normal migration.
EMBEDDING_DIM = get_settings().embedding_dimensions


class Company(Base):
    """One row per tenant. Stores the bcrypt-hashed API key and quota tier."""

    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, server_default=text("gen_random_uuid()::text")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # bcrypt hash of the raw API key; never log or return this.
    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # PRD §6.1 / Architecture §6.2 — free tier 100 analyses/day; per-company override.
    monthly_doc_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tenders: Mapped[list[Tender]] = relationship(back_populates="company")


class CompanyProfile(Base):
    """1:1 with companies. JSONB benchmarking profile for the Feasibility Scorer."""

    __tablename__ = "company_profiles"

    company_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    specialisations: Mapped[Any] = mapped_column(JSON, nullable=True)
    financial_capacity: Mapped[Any] = mapped_column(JSON, nullable=True)
    past_projects: Mapped[Any] = mapped_column(JSON, nullable=True)
    max_project_value: Mapped[float | None] = mapped_column(nullable=True)


class Tender(Base):
    """An uploaded tender PDF. Status drives the upload lifecycle."""

    __tablename__ = "tenders"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, server_default=text("gen_random_uuid()::text")
    )
    company_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # REQ-001 Data Requirements: status enum uploading|processing|ready|failed
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="uploading",
    )
    # ar | en | bilingual — populated on successful ingestion
    primary_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Human-readable reason on the 'failed' path (REQ-001 Usability NFR).
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    company: Mapped[Company] = relationship(back_populates="tenders")
    chunks: Mapped[list[TenderChunk]] = relationship(
        back_populates="tender",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('uploading', 'processing', 'ready', 'failed')",
            name="tenders_status_check",
        ),
        CheckConstraint(
            "primary_language IS NULL OR primary_language IN ('ar', 'en', 'bilingual')",
            name="tenders_primary_language_check",
        ),
        # Dashboard list view filters by tenant + recency.
        Index("ix_tenders_company_created", "company_id", "uploaded_at"),
    )


class TenderChunk(Base):
    """One retrieval-sized chunk of a tender, with its embedding.

    `company_id` is denormalized here (deviation from PRD §7) so retrievals can
    filter `WHERE tender_id = $1 AND company_id = $2` without joining — both a
    performance optimisation and a defence against cross-tenant vector leakage.
    """

    __tablename__ = "tender_chunks"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, server_default=text("gen_random_uuid()::text")
    )
    tender_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenders.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalized from tenders (see class docstring) — see migration comment.
    company_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    detected_language: Mapped[str] = mapped_column(String(16), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)

    tender: Mapped[Tender] = relationship(back_populates="chunks")

    __table_args__ = (
        CheckConstraint(
            "detected_language IN ('ar', 'en', 'mixed')",
            name="tender_chunks_language_check",
        ),
        # Vector retrieval always filters tender + company together.
        Index("ix_tender_chunks_tender_company", "tender_id", "company_id"),
        Index("ix_tender_chunks_company", "company_id"),
        # HNSW ANN index — cosine distance matches Gemini embeddings' training.
        # Ops class MUST match the distance operator used in queries (.cosine_distance),
        # or the planner cannot use the index (database-designer skill).
        Index(
            "ix_tender_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# Add the (tender_id, chunk_index) uniqueness as a separate Index to keep
# __table_args__ clean and consistent above.
Index(
    "ux_tender_chunks_tender_index",
    TenderChunk.tender_id,
    TenderChunk.chunk_index,
    unique=True,
)
