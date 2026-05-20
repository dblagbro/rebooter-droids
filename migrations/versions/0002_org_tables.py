"""org boundary phase 1 — organizations + organization_memberships

Revision ID: 0002_org_tables
Revises: 0001_baseline
Create Date: 2026-05-20

Phase 1 of the multi-tenant organization boundary (see
docs/notes/2026-05-20-organization-boundary-design.md sections 1, 4.1).

This revision is purely additive — it creates the two new tenant tables.
The nullable `organization_id` columns on the Tier-A tables land in the
next revision (0003), after these tables exist so the FK targets resolve.

TODO(org-phase2): a later migration flips the Tier-A `organization_id`
columns to NOT NULL, swaps per-table on-delete behaviour, and adds the
per-org unique constraints. None of that is in phase 1.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_org_tables"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("plan", sa.String(length=40), nullable=False),
        sa.Column("is_self_hosted_default", sa.Boolean(), nullable=False),
        sa.Column("max_devices", sa.Integer(), nullable=True),
        sa.Column("max_users", sa.Integer(), nullable=True),
        sa.Column("owner_user_id", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("organization_id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("org_role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_org_membership"
        ),
    )


def downgrade() -> None:
    op.drop_table("organization_memberships")
    op.drop_table("organizations")
