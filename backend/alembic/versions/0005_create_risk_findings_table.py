"""Create risk_findings table for REQ-004 Slice 3 (Persistence).

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-01

One row per Risk Radar finding, written in a single batch when the parent
analysis_run transitions to "awaiting_hitl" — never incrementally during the
LLM call — so a retry never produces duplicate rows (REQ-004 Data Requirements
"risk_findings persistence" row).

`clause_text` and `explanation` are commercially sensitive tender content
(REQ-004 Security NFR) and must never appear in application logs; they are
only persisted in this table.

Indexes:
  - ix_risk_findings_run_id        : GET /tenders/{id}/findings filters by run_id
  - ix_risk_findings_run_severity  : severity-filtered queries within a run
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_findings",
        sa.Column(
            "id",
            sa.String(36),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("clause_text", sa.Text, nullable=False),
        sa.Column("explanation", sa.Text, nullable=False),
        sa.Column("source_chunk_index", sa.Integer, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.CheckConstraint(
            "category IN ('fidic', 'penalty', 'lg_bond', 'termination', 'other')",
            name="risk_findings_category_check",
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low')",
            name="risk_findings_severity_check",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="risk_findings_confidence_range",
        ),
    )
    op.create_index(
        "ix_risk_findings_run_id",
        "risk_findings",
        ["run_id"],
    )
    op.create_index(
        "ix_risk_findings_run_severity",
        "risk_findings",
        ["run_id", "severity"],
    )


def downgrade() -> None:
    op.drop_index("ix_risk_findings_run_severity", table_name="risk_findings")
    op.drop_index("ix_risk_findings_run_id", table_name="risk_findings")
    op.drop_table("risk_findings")
