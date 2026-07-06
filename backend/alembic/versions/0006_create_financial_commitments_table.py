"""Create financial_commitments table for REQ-006 Slice 3 (Persistence).

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-05

One row per financial commitment item across all categories (contract_value,
bond, liquidated_damages, payment_milestone, retention, advance_payment).
Written in a single batch when the parent analysis_run transitions to
"awaiting_hitl" — never incrementally during the LLM call — so a retry
never produces duplicate rows (REQ-006 Slice 3 atomicity rule).

`amount_value` and `amount_currency` are commercially sensitive tender
content (REQ-006 Security NFR) and must never appear in application logs;
they are only persisted in this table.

Indexes:
  - ix_financial_commitments_run_id
        Filters by run_id (GET /tenders/{id}/financial).
  - ix_financial_commitments_run_type
        Per-type rollups inside a run (e.g. all bonds for this run).
  - ix_financial_commitments_run_review
        Fast query for items flagged for analyst review (needs_review=true).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "financial_commitments",
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
        sa.Column("commitment_type", sa.String(32), nullable=False),
        sa.Column("amount_value", sa.Float, nullable=True),
        sa.Column("amount_currency", sa.String(10), nullable=True),
        sa.Column("percentage", sa.Float, nullable=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column(
            "needs_review",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("source_chunk_index", sa.Integer, nullable=True),
        sa.CheckConstraint(
            "commitment_type IN ("
            "'bond', 'liquidated_damages', 'payment_milestone', "
            "'retention', 'advance_payment', 'contract_value')",
            name="financial_commitments_commitment_type_check",
        ),
    )
    op.create_index(
        "ix_financial_commitments_run_id",
        "financial_commitments",
        ["run_id"],
    )
    op.create_index(
        "ix_financial_commitments_run_type",
        "financial_commitments",
        ["run_id", "commitment_type"],
    )
    op.create_index(
        "ix_financial_commitments_run_review",
        "financial_commitments",
        ["run_id", "needs_review"],
    )


def downgrade() -> None:
    op.drop_index("ix_financial_commitments_run_review", table_name="financial_commitments")
    op.drop_index("ix_financial_commitments_run_type", table_name="financial_commitments")
    op.drop_index("ix_financial_commitments_run_id", table_name="financial_commitments")
    op.drop_table("financial_commitments")
