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
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    monthly_doc_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tenders: Mapped[list[Tender]] = relationship(back_populates="company")
    profile: Mapped[CompanyProfile | None] = relationship(
        back_populates="company",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CompanyProfile(Base):
    """1:1 benchmarking profile consumed by the Feasibility Scorer (REQ-002).

    `company_id` is both the foreign key AND the primary key — the idiomatic
    pattern for a true 1:1 (PRD: "Multiple profiles per company are deferred to
    v2"), and it makes the `ON CONFLICT (company_id)` upsert atomic and trivial.

    `financial_capacity` holds turnover / bonding figures — commercially
    sensitive, so it must never appear in logs (REQ-002 Security NFR). The
    `__repr__` below redacts it so even `repr(obj)` / default exception logging
    can't leak it (ai-security + senior-fullstack skills).
    """

    __tablename__ = "company_profiles"

    company_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    specializations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    financial_capacity: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    geographic_reach: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    past_projects: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    max_project_value: Mapped[float] = mapped_column(Float, nullable=False)
    # Server-managed on every write via a BEFORE UPDATE trigger (migration 0002)
    # AND explicitly SET in the upsert. Never accepted from the client.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    company: Mapped[Company] = relationship(back_populates="profile")

    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(specializations) = 'array' AND jsonb_array_length(specializations) >= 1",
            name="company_profiles_specializations_nonempty",
        ),
        CheckConstraint(
            "jsonb_typeof(geographic_reach) = 'array' "
            "AND jsonb_array_length(geographic_reach) >= 1",
            name="company_profiles_geographic_reach_nonempty",
        ),
        CheckConstraint(
            "max_project_value > 0",
            name="company_profiles_max_project_value_positive",
        ),
    )

    def __repr__(self) -> str:
        # financial_capacity intentionally omitted — it is sensitive and must
        # never surface in logs, tracebacks, or debugger sessions.
        return (
            f"CompanyProfile(company_id={self.company_id!r}, "
            f"specializations={self.specializations!r}, "
            f"geographic_reach={self.geographic_reach!r}, "
            f"past_projects_count={len(self.past_projects) if self.past_projects else 0}, "
            f"max_project_value={self.max_project_value!r}, "
            f"updated_at={self.updated_at!r})"
        )


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
    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(
        "AnalysisRun",
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


class AnalysisRun(Base):
    """One execution of the LangGraph analysis pipeline for a tender.

    State transitions: pending -> running -> awaiting_hitl -> complete
    (or failed). `agent_trace` is an append-only audit log of every node that
    ran; updates are atomic JSONB concatenations, never read-modify-write.
    """

    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, server_default=text("gen_random_uuid()::text")
    )
    tender_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenders.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="pending",
    )
    feasibility_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    agent_trace: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    aggregated_results: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tender: Mapped[Tender] = relationship("Tender", back_populates="analysis_runs")
    cost_events: Mapped[list["LlmCostEvent"]] = relationship(
        "LlmCostEvent",
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    risk_findings: Mapped[list["RiskFinding"]] = relationship(
        "RiskFinding",
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'running', 'awaiting_hitl', 'complete', 'failed')",
            name="analysis_runs_state_check",
        ),
        # Status polling always looks up the latest run for a tender.
        Index("ix_analysis_runs_tender_started", "tender_id", "started_at"),
    )


class RiskFinding(Base):
    """One risk-bearing clause identified by the Risk Radar (REQ-004 Slice 3).

    Written in a single batch when the parent analysis_run transitions to
    "awaiting_hitl" (never incrementally during the LLM call) so a retry never
    produces duplicate rows. clause_text and explanation are commercially
    sensitive tender content — they must never appear in application logs
    (REQ-004 Security NFR), only here in the persisted table.
    """

    __tablename__ = "risk_findings"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, server_default=text("gen_random_uuid()::text")
    )
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    clause_text: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    source_chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    run: Mapped[AnalysisRun] = relationship("AnalysisRun", back_populates="risk_findings")

    __table_args__ = (
        CheckConstraint(
            "category IN ('fidic', 'penalty', 'lg_bond', 'termination', 'other')",
            name="risk_findings_category_check",
        ),
        CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low')",
            name="risk_findings_severity_check",
        ),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="risk_findings_confidence_range",
        ),
        # GET /tenders/{id}/findings always filters by run_id.
        Index("ix_risk_findings_run_id", "run_id"),
        # Severity-filtered queries (e.g. "all critical findings for this run").
        Index("ix_risk_findings_run_severity", "run_id", "severity"),
    )


class LlmCostEvent(Base):
    """One row per LLM call per node — cost-tracking audit log (REQ-003 Slice 3).

    Written exclusively by CostTrackingHandler.on_llm_end; never exposed in
    application logs at DEBUG or INFO level (token counts and USD cost are
    commercially sensitive).
    """

    __tablename__ = "llm_cost_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, server_default=text("gen_random_uuid()::text")
    )
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped[AnalysisRun] = relationship("AnalysisRun", back_populates="cost_events")

    __table_args__ = (Index("ix_llm_cost_events_run_id", "run_id"),)


# Add the (tender_id, chunk_index) uniqueness as a separate Index to keep
# __table_args__ clean and consistent above.
Index(
    "ux_tender_chunks_tender_index",
    TenderChunk.tender_id,
    TenderChunk.chunk_index,
    unique=True,
)
