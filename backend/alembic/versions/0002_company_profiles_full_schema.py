"""Expand company_profiles to the full REQ-002 schema.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-25

The company_profiles table was stubbed in 0001 with nullable columns and no
time/geo fields. REQ-002 requires the full benchmarking profile: non-null
specializations / financial_capacity / geographic_reach / max_project_value, a
default-empty past_projects, and a server-managed updated_at refreshed on every
upsert. This migration evolves the stub in place rather than recreating it —
there are no rows yet (no endpoint has ever written one).

Two documented deviations from the imp-slice wording (PRD "Document Control":
surface deviations, don't silently handle them):

1. Column rename `specialisations` -> `specializations`. 0001 used the British
   spelling; the PRD and API contract use `specializations`. Aligning DB, ORM
   and API on one spelling removes a permanent source of mapping bugs. Safe
   because the table is empty. (senior-fullstack + database-designer skills.)

2. No surrogate `id` PK. The slice listed both an `id` PK and a unique
   `company_id`, but the PRD pins a true 1:1 ("Multiple profiles per company
   are deferred to v2") and 0001 already made `company_id` the primary key.
   FK-as-PK is the idiomatic 1:1 pattern and makes the
   `ON CONFLICT (company_id)` upsert atomic and trivial — so we keep it.

Structural CHECKs live at the DB layer (non-empty arrays, positive value); the
controlled-list *policy* (which specialisation strings are allowed) stays in
Pydantic because the PRD explicitly opens it to free-text in v2.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Rename to the PRD/API spelling (table is empty, so safe) ---
    op.alter_column(
        "company_profiles",
        "specialisations",
        new_column_name="specializations",
        existing_type=sa.dialects.postgresql.JSONB,
    )

    # --- Tighten the 0001 stub columns to NOT NULL (no rows exist yet) ---
    op.alter_column(
        "company_profiles",
        "specializations",
        existing_type=sa.dialects.postgresql.JSONB,
        nullable=False,
    )
    op.alter_column(
        "company_profiles",
        "financial_capacity",
        existing_type=sa.dialects.postgresql.JSONB,
        nullable=False,
    )
    op.alter_column(
        "company_profiles",
        "max_project_value",
        existing_type=sa.Float,
        nullable=False,
    )

    # --- Add geographic_reach (NOT NULL; temp default guards against any pre-existing rows) ---
    op.add_column(
        "company_profiles",
        sa.Column(
            "geographic_reach",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # Required field with no real default — drop the placeholder now it's NOT NULL.
    op.alter_column("company_profiles", "geographic_reach", server_default=None)

    # --- past_projects: NOT NULL with a permanent '[]' default (slice spec: "default []") ---
    op.alter_column(
        "company_profiles",
        "past_projects",
        existing_type=sa.dialects.postgresql.JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )

    # --- updated_at: server-managed, refreshed on every write (trigger below) ---
    op.add_column(
        "company_profiles",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # --- Structural CHECKs (DB invariants; value-allowlist policy is in Pydantic) ---
    op.create_check_constraint(
        "company_profiles_specializations_nonempty",
        "company_profiles",
        "jsonb_typeof(specializations) = 'array' "
        "AND jsonb_array_length(specializations) >= 1",
    )
    op.create_check_constraint(
        "company_profiles_geographic_reach_nonempty",
        "company_profiles",
        "jsonb_typeof(geographic_reach) = 'array' "
        "AND jsonb_array_length(geographic_reach) >= 1",
    )
    op.create_check_constraint(
        "company_profiles_max_project_value_positive",
        "company_profiles",
        "max_project_value > 0",
    )

    # --- updated_at refresh trigger (server-side guarantee on every UPDATE/upsert) ---
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_company_profiles_updated_at()
        RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_company_profiles_updated_at
            BEFORE UPDATE ON company_profiles
            FOR EACH ROW
            EXECUTE FUNCTION set_company_profiles_updated_at()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_company_profiles_updated_at ON company_profiles")
    op.execute("DROP FUNCTION IF EXISTS set_company_profiles_updated_at()")

    op.drop_constraint(
        "company_profiles_max_project_value_positive", "company_profiles", type_="check"
    )
    op.drop_constraint(
        "company_profiles_geographic_reach_nonempty", "company_profiles", type_="check"
    )
    op.drop_constraint(
        "company_profiles_specializations_nonempty", "company_profiles", type_="check"
    )

    op.drop_column("company_profiles", "updated_at")

    op.alter_column(
        "company_profiles",
        "past_projects",
        existing_type=sa.dialects.postgresql.JSONB,
        server_default=None,
        nullable=True,
    )
    op.drop_column("company_profiles", "geographic_reach")

    op.alter_column(
        "company_profiles", "max_project_value", existing_type=sa.Float, nullable=True
    )
    op.alter_column(
        "company_profiles",
        "financial_capacity",
        existing_type=sa.dialects.postgresql.JSONB,
        nullable=True,
    )
    op.alter_column(
        "company_profiles",
        "specializations",
        existing_type=sa.dialects.postgresql.JSONB,
        nullable=True,
        new_column_name="specialisations",
    )
