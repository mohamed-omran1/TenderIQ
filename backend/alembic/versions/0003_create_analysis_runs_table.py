"""Create analysis_runs table for REQ-003 Slice 2.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-28

Stores one row per LangGraph analysis execution. State is tracked through
pending -> running -> awaiting_hitl -> complete, with a terminal failed state.
agent_trace is append-only JSONB used for node-level replay and status polling.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column(
            "id",
            sa.String(36),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
        sa.Column(
            "tender_id",
            sa.String(36),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.String(36),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("feasibility_score", sa.Float, nullable=True),
        sa.Column(
            "agent_trace",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("aggregated_results", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("error_reason", sa.Text, nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'awaiting_hitl', 'complete', 'failed')",
            name="analysis_runs_state_check",
        ),
    )
    op.create_index(
        "ix_analysis_runs_tender_started",
        "analysis_runs",
        ["tender_id", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_runs_tender_started", table_name="analysis_runs")
    op.drop_table("analysis_runs")
