"""Tenant scope — the multi-tenant `organization` boundary.

Per `docs/notes/2026-05-20-organization-boundary-design.md` §3.

Phase 1 (this file, current state) ships ONLY the `TenantScoped` mixin —
the marker that the phase-2 enforcement will target. Tier-A models that
carry a (currently nullable) `organization_id` column mix this in, so
"is this entity tenant-scoped?" is a single MRO fact.

Phase 1 deliberately does NOT implement any isolation enforcement. There
is no `do_orm_execute` filter, no `before_flush` write-stamping, no
ContextVar plumbing. The `organization_id` columns are nullable and
nothing reads them for access control yet.

TODO(org-phase2): everything below the mixin hooks in here —

  * `_current_org: ContextVar[str | None]` and `_bypass: ContextVar[bool]`
    — the active tenant for the current request/job (design §3.1).
  * `@event.listens_for(orm.Session, "do_orm_execute")` — the global
    FROM-clause filter that adds `WHERE organization_id = :current_org`
    to every SELECT against a `TenantScoped` entity, via
    `with_loader_criteria(TenantScoped, ...)` (design §3.1).
  * a `before_flush` (or `before_insert` mapper) event that stamps
    `organization_id` from the ContextVar onto inserted `TenantScoped`
    rows and raises on a foreign `organization_id` (design §3.3).
  * `set_org()` / `reset()` / `system()` context helpers, wired from
    `role_required_*` and the device-auth middleware (design §3.2, §3.4).
  * a shadow / enforce runtime toggle modeled on `rbac.enforce_mode`
    (design §3.5.3, §8.1).
  * NOT-NULL + per-org unique constraints land with phase-2 migrations
    (design §6.3).
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


class TenantScoped:
    """Mixin marker for a tenant-owned (Tier-A) model.

    Any model that mixes this in carries the `organization_id` column and
    is, in phase 2, auto-filtered by the `do_orm_execute` event. In
    phase 1 the mixin only declares the (nullable) column and serves as
    the marker — there is no filtering or write-stamping yet.

    The column is declared here (rather than per-model) via
    `declared_attr` so every Tier-A table gets an identical definition
    and the phase-2 `with_loader_criteria(TenantScoped, ...)` target is
    unambiguous. The column is NULLABLE in phase 1 — see the module
    docstring.

    TODO(org-phase2): make `organization_id` NOT NULL via a migration
    once `ensure_default_organization_backfill()` has run on every DB.
    """

    @declared_attr
    def organization_id(cls) -> Mapped[Optional[str]]:
        return mapped_column(
            String(40),
            # SET NULL on-delete in phase 1 so a stray org delete can
            # never block. Phase 2 swaps the per-table on-delete
            # behaviour (RESTRICT for sites/groups/rules, etc.)
            # alongside the NOT-NULL flip — see design §2 Tier-A table.
            ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        )
