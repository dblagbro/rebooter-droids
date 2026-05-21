"""tier-2 feature 6 — notification channels + subscriptions + delivery queue

Revision ID: 0005_notifications_webhooks
Revises: 0004_org_id_announcements
Create Date: 2026-05-20

Tier-2 Feature 6 (see docs/notes/2026-05-20-hub-tier2-design.md
"Feature 6 — Hub-side notifications / outbound webhooks").

Three new tables, all org-scoped (the org boundary shipped in phases
1-2): every table carries the same NULLABLE `organization_id` FK that
the `TenantScoped` mixin declares, so the runtime `do_orm_execute` read
filter / `before_flush` write-stamping apply uniformly.

  * webhook_channels         — a configured outbound destination.
  * notification_subscriptions — event-type → channel binding.
  * webhook_deliveries       — the delivery queue, drained by the
    `webhook_delivery` APScheduler job.

TODO(org-phase3): these `organization_id` columns flip to NOT NULL
alongside the other Tier-A tables in the deferred phase-3 hardening
migration — see the org-boundary design doc §6.3.

NOTE: sibling Tier-2 branches (Backup, API tokens) also add revisions
off 0004; the merge reconciles the chain. This revision only depends on
0004 being present.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_notifications_webhooks"
down_revision: Union[str, None] = "0004_org_id_announcements"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhook_channels",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("signing_secret", sa.String(length=80), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=40), nullable=True),
        sa.Column("organization_id", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "notification_subscriptions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("channel_id", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("organization_id", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["webhook_channels.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notification_subscriptions_event",
        "notification_subscriptions",
        ["event_type"],
    )
    op.create_index(
        "ix_notification_subscriptions_channel",
        "notification_subscriptions",
        ["channel_id"],
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("channel_id", sa.String(length=40), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("response_snippet", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("organization_id", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["webhook_channels.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_webhook_deliveries_status_next",
        "webhook_deliveries",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_webhook_deliveries_status_next", table_name="webhook_deliveries"
    )
    op.drop_table("webhook_deliveries")
    op.drop_index(
        "ix_notification_subscriptions_channel",
        table_name="notification_subscriptions",
    )
    op.drop_index(
        "ix_notification_subscriptions_event",
        table_name="notification_subscriptions",
    )
    op.drop_table("notification_subscriptions")
    op.drop_table("webhook_channels")
