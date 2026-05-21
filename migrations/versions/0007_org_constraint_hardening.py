"""org boundary phase 3 — constraint hardening

Revision ID: 0007_org_constraint_hardening
Revises: 0006_api_tokens
Create Date: 2026-05-20

Phase 3 of the multi-tenant organization boundary (see
docs/notes/2026-05-20-organization-boundary-design.md §6.2, §6.3, §2).

Phases 1 & 2 added a NULLABLE `organization_id` FK to every Tier-A
table and shipped the runtime `do_orm_execute` / `before_flush`
enforcement. By the time this revision runs the data has already been
backfilled by `ensure_default_organization_backfill()` (bootstrap.py),
so the NOT-NULL flip is safe.

This revision does three things, per the design:

1. NOT-NULL flip on `organization_id` for the Tier-A tables whose
   column is permanently required (every Tier-A table EXCEPT
   `device_announcements` — an un-adopted announcement legitimately
   has no org, §2 / migration 0004 — and `audit_events` — platform/
   system audit rows have no org, §3.6).

2. Per-org unique constraints replacing the global ones (§6.3):
   `sites.name`, `groups.name`, `scenes.name` become
   `UNIQUE(organization_id, name)`. `users.email` deliberately stays
   global (§6.3) and is untouched. `role_bindings`'
   `uq_role_binding_scope` widens to include `organization_id` (§4.3).

3. FK on-delete swaps (§2 Tier-A table): phase-1's interim `SET NULL`
   is replaced by the per-table target behaviour — `RESTRICT` for
   sites/groups/watchdog_rules/schedules/scenes/enrollment_tokens/
   external_sensor_sources/role_bindings, `CASCADE` for invitations.
   `audit_events` keeps `SET NULL` (nullable, platform rows).
   `device_announcements` keeps `SET NULL` (migration 0004).

Postgres RLS (§3.1 / §8.1 step 9) is intentionally NOT in this
revision — see the module-level note below.

All ALTERs use `op.batch_alter_table` so the revision also runs on
SQLite (batch mode recreates the table). On Postgres batch mode falls
through to plain `ALTER TABLE`.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_org_constraint_hardening"
down_revision: Union[str, None] = "0006_api_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Tier-A tables and their phase-3 target on-delete behaviour ─────────
#
# Every entry gets its `organization_id` flipped to NOT NULL AND its FK
# on-delete swapped from phase-1's interim SET NULL to the target.
# `audit_events` and `device_announcements` are deliberately absent —
# both keep a NULLABLE column and SET NULL on-delete (design §3.6, §2).
_TIER_A_NOT_NULL: dict[str, str] = {
    "sites": "RESTRICT",
    "groups": "RESTRICT",
    "watchdog_rules": "RESTRICT",
    "schedules": "RESTRICT",
    "scenes": "RESTRICT",
    "enrollment_tokens": "RESTRICT",
    "external_sensor_sources": "RESTRICT",
    "role_bindings": "RESTRICT",
    "invitations": "CASCADE",
}

# Tables whose global UNIQUE(name) becomes UNIQUE(organization_id, name).
_PER_ORG_NAME_UNIQUE: tuple[str, ...] = ("sites", "groups", "scenes")

# The baseline schema created `UNIQUE(name)` on sites/groups/scenes and
# `uq_role_binding_scope` WITHOUT an explicit name on the unnamed ones —
# SQLite stores them anonymously, so `batch_alter_table` cannot drop
# them by name unless it reflects under a naming convention. Supplying
# the SQLAlchemy-default convention makes a reflected unnamed UNIQUE on
# `sites.name` resolve to `uq_sites_name`, which is what we drop below.
# (`uq_role_binding_scope` was already explicitly named in the baseline,
# so it needs no convention.) Postgres-named constraints are unaffected.
_NAMING_CONVENTION = {
    "uq": "uq_%(table_name)s_%(column_0_name)s",
}


def _fk_name(table: str) -> str:
    return f"fk_{table}_organization_id"


def upgrade() -> None:
    # ── 1 + 3: NOT-NULL flip + FK on-delete swap ───────────────────────
    # The naming convention is supplied so that, when batch mode
    # recreates a table on SQLite, any reflected unnamed constraint
    # resolves to a deterministic name — keeping sites/groups/scenes'
    # `uq_<table>_name` droppable in step 2 below.
    for table, ondelete in _TIER_A_NOT_NULL.items():
        with op.batch_alter_table(
            table, naming_convention=_NAMING_CONVENTION
        ) as batch:
            batch.alter_column(
                "organization_id",
                existing_type=sa.String(length=40),
                nullable=False,
            )
            # Drop the phase-1 SET NULL FK and recreate with the target
            # on-delete behaviour. On SQLite batch mode recreates the
            # table; on Postgres this is DROP CONSTRAINT + ADD CONSTRAINT.
            batch.drop_constraint(_fk_name(table), type_="foreignkey")
            batch.create_foreign_key(
                _fk_name(table),
                "organizations",
                ["organization_id"],
                ["id"],
                ondelete=ondelete,
            )

    # ── 2: per-org unique constraints replacing the global ones ────────
    # The baseline created these as anonymous UNIQUE(name) constraints.
    # The naming convention lets batch mode reflect them as
    # `uq_<table>_name` so they can be dropped by name; the new per-org
    # constraint is named explicitly so a downgrade can find it.
    for table in _PER_ORG_NAME_UNIQUE:
        with op.batch_alter_table(
            table, naming_convention=_NAMING_CONVENTION
        ) as batch:
            batch.drop_constraint(f"uq_{table}_name", type_="unique")
            batch.create_unique_constraint(
                f"uq_{table}_org_name", ["organization_id", "name"]
            )

    # role_bindings: widen uq_role_binding_scope to include the org so a
    # binding is unique *within* an org (design §4.3).
    with op.batch_alter_table(
        "role_bindings", naming_convention=_NAMING_CONVENTION
    ) as batch:
        batch.drop_constraint("uq_role_binding_scope", type_="unique")
        batch.create_unique_constraint(
            "uq_role_binding_scope",
            ["organization_id", "user_id", "scope_type", "scope_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table(
        "role_bindings", naming_convention=_NAMING_CONVENTION
    ) as batch:
        batch.drop_constraint("uq_role_binding_scope", type_="unique")
        batch.create_unique_constraint(
            "uq_role_binding_scope",
            ["user_id", "scope_type", "scope_id"],
        )

    for table in _PER_ORG_NAME_UNIQUE:
        with op.batch_alter_table(
            table, naming_convention=_NAMING_CONVENTION
        ) as batch:
            batch.drop_constraint(f"uq_{table}_org_name", type_="unique")
            batch.create_unique_constraint(f"uq_{table}_name", ["name"])

    for table, _ondelete in _TIER_A_NOT_NULL.items():
        with op.batch_alter_table(
            table, naming_convention=_NAMING_CONVENTION
        ) as batch:
            batch.drop_constraint(_fk_name(table), type_="foreignkey")
            batch.create_foreign_key(
                _fk_name(table),
                "organizations",
                ["organization_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.alter_column(
                "organization_id",
                existing_type=sa.String(length=40),
                nullable=True,
            )
