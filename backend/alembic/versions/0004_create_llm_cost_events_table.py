"""Create llm_cost_events table for REQ-003 Slice 3 (Cost Tracker).

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-29

One row per LLM call per node, written by CostTrackingHandler.on_llm_end.
Token counts and USD cost are commercially sensitive — never logged at
application level, only persisted here (REQ-003 NFR).

The index on run_id supports the per-run cost breakdown used by the
/analytics/cost endpoint (Architecture §7 — LLM cost observability).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_cost_events",
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
        sa.Column("node_name", sa.String(255), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("cost_usd", sa.Float, nullable=False),
        sa.Column(
            "logged_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_llm_cost_events_run_id",
        "llm_cost_events",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_cost_events_run_id", table_name="llm_cost_events")
    op.drop_table("llm_cost_events")
