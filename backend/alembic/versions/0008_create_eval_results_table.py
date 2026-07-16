"""Create eval_results table for REQ-012 Slice 2 (API Endpoint).

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-12

Append-only history log for evaluation runs. Rows are write-once; never
updated or deleted. An index on (company_id, run_at DESC) supports fast
"last 10 evals for this company" queries.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_results",
        sa.Column(
            "id",
            sa.String(36),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
        sa.Column(
            "company_id",
            sa.String(36),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tender_id",
            sa.String(36),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("overall_status", sa.String(32), nullable=False),
        sa.Column(
            "total_cost_usd",
            sa.Float,
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        sa.Column(
            "run_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_eval_results_company_runat",
        "eval_results",
        ["company_id", sa.text("run_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_eval_results_company_runat", table_name="eval_results")
    op.drop_table("eval_results")
