"""API tokens — scoped bearer credentials for programmatic access.

Feature 4a of the Hub Tier-2 design
(`docs/notes/2026-05-20-hub-tier2-design.md` §4a).

A first-class personal / service API token, so automation no longer has
to ride on a human user's session bearer token. The token plaintext is
shown exactly once at mint time; the hub only ever stores a SHA-256
`token_hash` — the same one-way-hash discipline `DeviceCredential`
already uses for device tokens.

`ApiToken` is `TenantScoped` — it carries the nullable `organization_id`
column the org-boundary mixin provides, so a token is owned by the org
that minted it and the `do_orm_execute` read filter scopes the
Settings list automatically.

New table: `Base.metadata.create_all()` adds it at startup; the matching
Alembic revision (`0006_api_tokens`) chains off the current head for
parity with a migrated deployment.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import JSON, Boolean, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column
from app.services.tenant_scope import TenantScoped


# The token-string prefix. Every minted token plaintext is
# `rbt_<urlsafe-random>` — `rbt_` makes a leaked token greppable in logs
# and recognisable to the bearer-auth resolver.
TOKEN_STRING_PREFIX = "rbt_"

# Scope vocabulary — v1 is deliberately coarse (design Q4): `read` covers
# every GET, `write` covers every mutation. Finer per-resource scopes can
# be added to this tuple later without a schema change (scopes is JSON).
SCOPE_READ = "read"
SCOPE_WRITE = "write"
KNOWN_SCOPES = (SCOPE_READ, SCOPE_WRITE)

# Sensible default expiry — 90 days. A token with no operator-chosen
# expiry gets this; design §4a "Risks" calls for a default expiry on a
# public SaaS rather than non-expiring bearer credentials.
DEFAULT_EXPIRY_DAYS = 90


class ApiToken(TenantScoped, Base):
    # TODO(org-phase3): flip `organization_id` to NOT NULL (RESTRICT FK)
    # once the backfill is confirmed everywhere — see the org-boundary
    # design §2 / §6.3, same as the other Tier-A tables.
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "apt")
    )

    # Operator-facing label — "CI deploy bot", "Grafana poller".
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # SHA-256 hex of the full token plaintext. Never the plaintext.
    # Unique so a verify can look the row up by hash directly.
    token_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    # First ~12 chars of the plaintext (`rbt_` + a few random chars),
    # shown in the list so an operator can tell tokens apart without
    # ever seeing the secret again.
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)

    # Granted scopes — a JSON list drawn from KNOWN_SCOPES.
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Site scope parity with the other operator-managed tables.
    site_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("sites.id", ondelete="SET NULL"), nullable=True
    )

    created_by_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # NULL = never expires (only reachable if the operator explicitly
    # clears the expiry); minting defaults to now + DEFAULT_EXPIRY_DAYS.
    expires_at: Mapped[datetime | None] = ts_column(
        default_now=False, nullable=True
    )
    last_used_at: Mapped[datetime | None] = ts_column(
        default_now=False, nullable=True
    )
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = ts_column()


Index("ix_api_tokens_token_hash", ApiToken.token_hash)
Index("ix_api_tokens_revoked_expires", ApiToken.revoked, ApiToken.expires_at)
