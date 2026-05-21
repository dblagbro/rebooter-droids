"""Organization (tenant) model — multi-tenant boundary.

Per `docs/notes/2026-05-20-organization-boundary-design.md` §1 and §4.1.

This file introduces the first-class top-level tenant entity.
`Organization` is the tenant; `OrganizationMembership` is the M:N join
between users and organizations (a user — e.g. an MSP/contractor — can
belong to more than one org, so `users` itself does NOT carry an
`organization_id` column).

Phase 1 shipped the additive foundation (these tables + nullable
`organization_id` on the Tier-A tables). Phase 2 shipped the runtime
enforcement on top: the `do_orm_execute` tenant read filter, the
`before_flush` write-stamping, the org ContextVar plumbing and RBAC
re-scoping — all in `app/services/tenant_scope.py` and wired from the
auth middleware. Phase 3 shipped the constraint hardening — the
NOT-NULL flip on `organization_id`, the per-org unique constraints and
the FK on-delete swaps (Alembic revision 0005), plus the sync-applier
org-scoping fix (`app/services/sync.py`, design §3.7).

Postgres RLS (design §3.1 / §8.1 step 9) remains a clean follow-up —
see `tenant_scope.tenant_rls_todo()` for the deferral rationale.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column


# Organization lifecycle / billing enums — kept deliberately small for v1.
ORG_STATUS_ACTIVE = "active"
ORG_STATUS_SUSPENDED = "suspended"
ORG_STATUS_CLOSED = "closed"
ORG_STATUSES = (ORG_STATUS_ACTIVE, ORG_STATUS_SUSPENDED, ORG_STATUS_CLOSED)

ORG_PLAN_FREE = "free"
ORG_PLAN_PRO = "pro"
ORG_PLAN_ENTERPRISE = "enterprise"
# `legacy` is the grandfathered plan assigned to the one default org the
# backfill creates from a pre-multi-tenant database (design §6.1 step 2).
ORG_PLAN_LEGACY = "legacy"
ORG_PLANS = (ORG_PLAN_FREE, ORG_PLAN_PRO, ORG_PLAN_ENTERPRISE, ORG_PLAN_LEGACY)

# Org-tier authority on the membership join row (distinct from the
# resource-tier `role_bindings` roles — see design §4.1).
ORG_ROLE_OWNER = "owner"
ORG_ROLE_ADMIN = "admin"
ORG_ROLE_MEMBER = "member"
ORG_ROLES = (ORG_ROLE_OWNER, ORG_ROLE_ADMIN, ORG_ROLE_MEMBER)


class Organization(Base):
    """A tenant — a paying customer (SaaS) or the lone org on a
    self-hosted install. Per design §1."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "org")
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    # `slug` is the stable, URL-safe, globally-unique handle. This is the
    # one table that legitimately keeps a global unique constraint.
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)

    # Lifecycle / billing — minimum viable surface for v1.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ORG_STATUS_ACTIVE
    )
    plan: Mapped[str] = mapped_column(
        String(40), nullable=False, default=ORG_PLAN_FREE
    )
    # True for the lone org on a self-host (single-tenant) install.
    is_self_hosted_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Soft caps for the SaaS plan tiers (NULL = unlimited). Advisory in
    # v1 — checked at create-time in the service layer.
    max_devices: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_users: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Owner — the human who created/owns the org. SET NULL so deleting a
    # user never orphans the org; ownership is transferable by an admin.
    owner_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()


class OrganizationMembership(Base):
    """M:N join between `users` and `organizations` (design §4.1).

    A user can belong to more than one org (an MSP managing several
    customers; a contractor). `org_role` is org-tier authority — can you
    administer the org itself (invite users, create sites, see billing) —
    distinct from the resource-tier `role_bindings`.
    """

    __tablename__ = "organization_memberships"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "om")
    )
    organization_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_role: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ORG_ROLE_MEMBER
    )
    created_at: Mapped[datetime] = ts_column()

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", name="uq_org_membership"
        ),
    )
