"""org boundary phase 2 — nullable organization_id on device_announcements

Revision ID: 0004_org_id_announcements
Revises: 0003_org_id_tier_a
Create Date: 2026-05-20

Phase 2 of the multi-tenant organization boundary (see
docs/notes/2026-05-20-organization-boundary-design.md §2).

Phase 1's Tier-A table list (migration 0003) omitted
`device_announcements`, but the design doc §2 lists it as a Tier-A
entity ("device_announcements | SET NULL | Pre-adoption; may have no org
yet. Nullable until adopted."). Phase 2 brings it under the
`do_orm_execute` tenant filter by adding the same NULLABLE
`organization_id` FK column the other 10 Tier-A tables carry.

Unlike the other Tier-A columns, this one stays NULLABLE permanently —
an un-adopted announcement legitimately has no org. So this is the one
Tier-A entity NOT subject to the phase-3 NOT-NULL flip. The on-delete is
SET NULL, matching both phase-1's conservative default and the design's
explicit SET NULL choice for this table.

The data backfill that stamps existing rows runs in
`ensure_default_organization_backfill()` (app/services/bootstrap.py) —
`device_announcements` was added to that function's `_ORG_TIER_A_TABLES`
list in phase 2.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_org_id_announcements"
down_revision: Union[str, None] = "0003_org_id_tier_a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FK_NAME = "fk_device_announcements_organization_id"


def upgrade() -> None:
    with op.batch_alter_table("device_announcements") as batch:
        batch.add_column(
            sa.Column("organization_id", sa.String(length=40), nullable=True)
        )
        batch.create_foreign_key(
            _FK_NAME,
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("device_announcements") as batch:
        batch.drop_constraint(_FK_NAME, type_="foreignkey")
        batch.drop_column("organization_id")
