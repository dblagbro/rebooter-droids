"""Role bindings — Tier-A foundation for the Site+Group+Device RBAC redesign.

Replaces the flat `users.role` + `users.is_admin` + `users.is_super_admin`
model with a per-binding table that lets one user hold different roles
across different scopes. Locked by B10 redlines 2026-05-10 PM
(RFC-003 §9.0):

- **scope_type** is one of: ``global`` (whole-portal), ``site`` (one site_id),
  ``group`` (one group_id), ``device`` (one device_id).
- **role** is one of the existing constants in `app/models/users.py`:
  ``super_admin`` / ``admin`` / ``operator`` / ``viewer``.
- For ``scope_type='global'``, ``scope_id`` is ``NULL``. For every other
  scope_type, ``scope_id`` is the target row's ULID.

The migration adds this table only — it does not enforce anything. The
shadow-mode middleware (A2) will *log* would-have-denied requests for
≥7 days before the enforce flip (A8). Until then the legacy
`users.role` / `users.is_admin` / `users.is_super_admin` columns stay
authoritative.

Auto-backfill (run once per database by bootstrap.py) populates rows
per the B10 Q2 decision:
- existing ``users.is_super_admin=True`` → one ``('global', NULL, 'super_admin')``
- existing ``users.is_admin=True`` (and not super_admin) → one row per
  current site_id, ``('site', <site_id>, 'admin')``. If no sites exist
  yet, one ``('global', NULL, 'admin')`` row as a safety net so the
  admin isn't locked out on day one.
- existing ``users.role='operator'`` → **no rows**. Operator must be
  explicitly scoped by an admin before the enforce flip.

The backfill is idempotent and tracked via a row in `runtime_settings`
under the key ``rbac.role_bindings_backfilled_at`` so it runs once and
only once.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column
from app.services.tenant_scope import TenantScoped


SCOPE_GLOBAL = "global"
SCOPE_SITE = "site"
SCOPE_GROUP = "group"
SCOPE_DEVICE = "device"
ALL_SCOPE_TYPES = (SCOPE_GLOBAL, SCOPE_SITE, SCOPE_GROUP, SCOPE_DEVICE)


class RoleBinding(TenantScoped, Base):
    # org-boundary phase 2: `RoleBinding` is TenantScoped, so once the
    # tenant-scope ContextVar is bound every `select(RoleBinding)` is
    # auto-filtered to the active org by the do_orm_execute filter —
    # i.e. scope_type='global' is now "global *within this org*"
    # (design §4.2). The resolvers in app/services/role_bindings.py thus
    # became org-aware with no code change. A user's global binding in
    # org A has zero reach into org B.
    #
    # TODO(org-phase3): flip `organization_id` to NOT NULL (RESTRICT FK)
    # and widen `uq_role_binding_scope` to include `organization_id`
    # (design §4.3). Deferred to phase 3 with the other constraint
    # hardening.
    __tablename__ = "role_bindings"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "rb")
    )
    user_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # NULL for scope_type='global', else the scoped row's ULID. We don't
    # use polymorphic FKs because each scope_type maps to a different
    # parent table; integrity is enforced application-side.
    scope_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)

    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(40),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        # A user can have at most one binding per (scope_type, scope_id) pair.
        # NULL scope_id is treated by Postgres as distinct from any other
        # value including another NULL — so multiple global-scoped rows are
        # technically allowed at the schema level; the service layer enforces
        # at-most-one-global-per-user before insert.
        UniqueConstraint("user_id", "scope_type", "scope_id", name="uq_role_binding_scope"),
        Index("ix_role_bindings_user", "user_id"),
        Index("ix_role_bindings_scope", "scope_type", "scope_id"),
    )
