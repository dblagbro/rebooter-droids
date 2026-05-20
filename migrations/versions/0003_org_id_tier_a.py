"""org boundary phase 1 — nullable organization_id on Tier-A tables

Revision ID: 0003_org_id_tier_a
Revises: 0002_org_tables
Create Date: 2026-05-20

Phase 1 of the multi-tenant organization boundary (see
docs/notes/2026-05-20-organization-boundary-design.md sections 2, 6.2).

Adds a NULLABLE `organization_id` FK column to each of the 10 Tier-A
tables (design section 2, "Tier-A"). Nullable only — no NOT NULL, no
per-org unique constraints, no enforcement. The on-delete is SET NULL
in phase 1 so a stray org delete can never block; phase 2 swaps the
per-table on-delete behaviour (RESTRICT for sites/groups/rules, etc.).

`audit_events_archive` also gets a plain nullable `organization_id` (no
FK) so the audit-prune copy of `audit_events` rows stays complete.

The data backfill that populates these columns runs separately as the
idempotent `ensure_default_organization_backfill()` in
app/services/bootstrap.py (design section 6.1).

TODO(org-phase3): a later migration ALTERs these columns to SET NOT NULL
(after the backfill is confirmed on every DB), swaps the on-delete
behaviour, and adds the per-org unique constraints (design sections 6.2,
6.3). None of that is in phase 1 or phase 2 — phase 2 is the runtime
enforcement mechanism (app/services/tenant_scope.py), not constraint
hardening.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_org_id_tier_a"
down_revision: Union[str, None] = "0002_org_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The 10 Tier-A tables that mix in TenantScoped — each gets a nullable
# organization_id FK to organizations.id. Order is not significant (all
# are independent ALTERs); kept in design-doc order for readability.
_TIER_A_TABLES: tuple[str, ...] = (
    "sites",
    "groups",
    "watchdog_rules",
    "schedules",
    "scenes",
    "enrollment_tokens",
    "external_sensor_sources",
    "role_bindings",
    "invitations",
    "audit_events",
)


def _fk_name(table: str) -> str:
    return f"fk_{table}_organization_id"


def upgrade() -> None:
    for table in _TIER_A_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column(
                    "organization_id", sa.String(length=40), nullable=True
                )
            )
            batch.create_foreign_key(
                _fk_name(table),
                "organizations",
                ["organization_id"],
                ["id"],
                ondelete="SET NULL",
            )

    # audit_events_archive mirrors audit_events so the prune copy stays
    # complete. Plain nullable column, no FK — the archive is not a
    # phase-2 query-filter target (design section 3.6).
    with op.batch_alter_table("audit_events_archive") as batch:
        batch.add_column(
            sa.Column("organization_id", sa.String(length=40), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_events_archive") as batch:
        batch.drop_column("organization_id")

    for table in reversed(_TIER_A_TABLES):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(_fk_name(table), type_="foreignkey")
            batch.drop_column("organization_id")
