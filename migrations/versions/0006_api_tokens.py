"""hub tier-2 feature 4a — api_tokens table

Revision ID: 0006_api_tokens
Revises: 0005_notifications_webhooks
Create Date: 2026-05-20

Adds the `api_tokens` table — scoped bearer credentials for programmatic
hub access (see docs/notes/2026-05-20-hub-tier2-design.md §4a). The model
is `app/models/api_tokens.py::ApiToken`; on a fresh deployment
`Base.metadata.create_all()` creates it, this revision keeps a migrated
deployment in parity.

`api_tokens` is a Tier-A (org-scoped) table — it carries the same
nullable `organization_id` FK the org-boundary `TenantScoped` mixin adds
to every Tier-A table. Like the other phase-1/2 Tier-A columns it stays
NULLABLE with an ON DELETE SET NULL FK; the phase-3 NOT-NULL flip is
deferred. `token_hash` carries a SHA-256 hex digest; the plaintext is
never stored.

Sibling Tier-2 branches each added a revision off 0004; the
consolidation merge linearized them into a single chain
(0004 -> 0005_notifications_webhooks -> 0006_api_tokens ->
0007_org_constraint_hardening).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_api_tokens"
down_revision: Union[str, None] = "0005_notifications_webhooks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=40), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # Tier-A org-scope column — same shape as the other Tier-A tables.
        sa.Column("organization_id", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_api_tokens_token_hash", "api_tokens", ["token_hash"]
    )
    op.create_index(
        "ix_api_tokens_revoked_expires",
        "api_tokens",
        ["revoked", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_api_tokens_revoked_expires", table_name="api_tokens")
    op.drop_index("ix_api_tokens_token_hash", table_name="api_tokens")
    op.drop_table("api_tokens")
