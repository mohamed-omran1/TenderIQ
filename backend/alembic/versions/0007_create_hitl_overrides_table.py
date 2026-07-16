"""Create hitl_overrides table for REQ-007 Slice 1 (Backend — HITL Override Gate).

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-06

Immutable audit log — one row per HITL decision. Rows are write-once;
never updated or deleted (REQ-007 audit integrity NFR).

UNIQUE constraint on run_id enforces "one override per run". The separate
index on run_id provides fast lookup per run. Also expands
analysis_runs_state_check to include the "resuming" intermediate state
used to prevent double-approval race conditions.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hitl_overrides",
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
        sa.Column(
            "analyst_company_id",
            sa.String(36),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("original_score", sa.Float, nullable=False),
        sa.Column("overridden_score", sa.Float, nullable=True),
        sa.Column("justification", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('approved', 'overridden')",
            name="hitl_overrides_action_check",
        ),
        sa.UniqueConstraint("run_id", name="uq_hitl_overrides_run_id"),
    )
    op.create_index(
        "ix_hitl_overrides_run_id",
        "hitl_overrides",
        ["run_id"],
    )

    # Expand analysis_runs.state CHECK to include "resuming" intermediate
    # state (REQ-007 — prevents double-approval race conditions).
    op.drop_constraint(
        "analysis_runs_state_check", "analysis_runs", type_="check"
    )
    op.create_check_constraint(
        "analysis_runs_state_check",
        "analysis_runs",
        "state IN ("
        "'pending', 'running', 'awaiting_hitl', 'resuming', "
        "'complete', 'failed')",
    )


def downgrade() -> None:
    # Restore the original analysis_runs state check (without "resuming").
    op.drop_constraint(
        "analysis_runs_state_check", "analysis_runs", type_="check"
    )
    op.create_check_constraint(
        "analysis_runs_state_check",
        "analysis_runs",
        "state IN ("
        "'pending', 'running', 'awaiting_hitl', "
        "'complete', 'failed')",
    )

    op.drop_index("ix_hitl_overrides_run_id", table_name="hitl_overrides")
    op.drop_table("hitl_overrides")
