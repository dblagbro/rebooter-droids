"""Role-binding service — Tier-A foundation (v0.5.0).

Wraps `role_bindings` table CRUD and the **effective-scope resolver**
used by the shadow-mode middleware (A2 in the redesign plan v2).

Per B10 Q1, a user's effective access is the **union** of:
1. ``global`` bindings → can act on anything of that role tier
2. ``site`` bindings → can act on rows in that site
3. ``group`` bindings → can act on devices that are members of that group
4. ``device`` bindings → can act on that specific device

`effective_device_ids(user_id, role_needed)` returns either:
- the literal string ``"ALL"`` (sentinel for global-scoped access), or
- a finite set of device_ids the user can act on, computed by unioning
  the four sources above.

The same shape works for sites: ``effective_site_ids`` returns
``"ALL"`` or a set. Groups likewise.

**This module is logging-only during shadow mode.** The middleware
calls these resolvers, compares against what the legacy auth would
have allowed, and writes a `rbac.shadow_deny` audit row for any
divergence — but the legacy auth still authoritatively decides
whether to let the request through. The enforce flip (A8) is a
separate ship gated on ≥7 days of clean shadow logs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select

from app.db import session_scope
from app.models import Device, GroupMembership, RoleBinding, Site
from app.models.role_bindings import (
    ALL_SCOPE_TYPES,
    SCOPE_DEVICE,
    SCOPE_GLOBAL,
    SCOPE_GROUP,
    SCOPE_SITE,
)
from app.models.users import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_SUPER_ADMIN,
    ROLE_VIEWER,
)


ALL_SENTINEL = "ALL"


# ── ROLE HIERARCHY ───────────────────────────────────────────────────────
# Higher in the list = more privileged. "satisfies" means: if a user has
# role X, do they satisfy a check for role Y? Yes iff X is at-or-above Y
# in this ordering.
_ROLE_RANK = {
    ROLE_SUPER_ADMIN: 4,
    ROLE_ADMIN: 3,
    ROLE_OPERATOR: 2,
    ROLE_VIEWER: 1,
}


def _role_satisfies(have: str, need: str) -> bool:
    return _ROLE_RANK.get(have, 0) >= _ROLE_RANK.get(need, 0)


# ── CRUD ─────────────────────────────────────────────────────────────────


def grant(
    *,
    user_id: str,
    scope_type: str,
    scope_id: str | None,
    role: str,
    granted_by_user_id: str | None = None,
) -> RoleBinding:
    """Insert a binding. Idempotent — re-granting the same
    (user, scope_type, scope_id) updates the role + updated_at."""
    if scope_type not in ALL_SCOPE_TYPES:
        raise ValueError(f"unknown scope_type: {scope_type}")
    if scope_type == SCOPE_GLOBAL and scope_id is not None:
        raise ValueError("scope_id must be NULL for scope_type='global'")
    if scope_type != SCOPE_GLOBAL and not scope_id:
        raise ValueError(f"scope_id required for scope_type={scope_type}")
    if role not in _ROLE_RANK:
        raise ValueError(f"unknown role: {role}")

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        existing = session.scalar(
            select(RoleBinding).where(
                RoleBinding.user_id == user_id,
                RoleBinding.scope_type == scope_type,
                RoleBinding.scope_id == scope_id,
            )
        )
        if existing is not None:
            existing.role = role
            existing.updated_at = now
            session.flush()
            return existing
        rb = RoleBinding(
            user_id=user_id,
            scope_type=scope_type,
            scope_id=scope_id,
            role=role,
            created_at=now,
            updated_at=now,
            created_by_user_id=granted_by_user_id,
        )
        session.add(rb)
        session.flush()
        return rb


def revoke(
    *,
    user_id: str,
    scope_type: str,
    scope_id: str | None,
) -> bool:
    """Drop one binding. Returns True if a row was deleted."""
    with session_scope() as session:
        existing = session.scalar(
            select(RoleBinding).where(
                RoleBinding.user_id == user_id,
                RoleBinding.scope_type == scope_type,
                RoleBinding.scope_id == scope_id,
            )
        )
        if existing is None:
            return False
        session.delete(existing)
        return True


def list_for_user(user_id: str) -> list[dict]:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(RoleBinding).where(RoleBinding.user_id == user_id)
            )
        )
        return [
            {
                "id": r.id,
                "scope_type": r.scope_type,
                "scope_id": r.scope_id,
                "role": r.role,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rows
        ]


# ── EFFECTIVE-SCOPE RESOLVERS ────────────────────────────────────────────


def has_global_role(user_id: str, role_needed: str) -> bool:
    """True if the user holds a global binding at or above role_needed."""
    with session_scope() as session:
        rows = session.scalars(
            select(RoleBinding).where(
                RoleBinding.user_id == user_id,
                RoleBinding.scope_type == SCOPE_GLOBAL,
            )
        )
        return any(_role_satisfies(r.role, role_needed) for r in rows)


def effective_site_ids(user_id: str, role_needed: str) -> str | set[str]:
    """All site_ids the user can act on at the requested role tier.
    Returns ``"ALL"`` sentinel for global binding, else a set."""
    with session_scope() as session:
        bindings = list(
            session.scalars(
                select(RoleBinding).where(RoleBinding.user_id == user_id)
            )
        )
        for b in bindings:
            if b.scope_type == SCOPE_GLOBAL and _role_satisfies(b.role, role_needed):
                return ALL_SENTINEL
        return {
            b.scope_id
            for b in bindings
            if b.scope_type == SCOPE_SITE
            and _role_satisfies(b.role, role_needed)
            and b.scope_id is not None
        }


def effective_device_ids(user_id: str, role_needed: str) -> str | set[str]:
    """All device_ids the user can act on at the requested role tier.
    Computed by unioning four sources:
      1. global binding → ALL
      2. site bindings → all devices whose site_id is in those sites
      3. group bindings → all devices in those groups (via GroupMembership)
      4. device bindings → those device_ids directly
    """
    with session_scope() as session:
        bindings = list(
            session.scalars(
                select(RoleBinding).where(RoleBinding.user_id == user_id)
            )
        )
        # Short-circuit: global binding at-or-above role → ALL
        for b in bindings:
            if b.scope_type == SCOPE_GLOBAL and _role_satisfies(b.role, role_needed):
                return ALL_SENTINEL

        site_ids = {
            b.scope_id for b in bindings
            if b.scope_type == SCOPE_SITE
            and _role_satisfies(b.role, role_needed)
            and b.scope_id is not None
        }
        group_ids = {
            b.scope_id for b in bindings
            if b.scope_type == SCOPE_GROUP
            and _role_satisfies(b.role, role_needed)
            and b.scope_id is not None
        }
        direct_device_ids = {
            b.scope_id for b in bindings
            if b.scope_type == SCOPE_DEVICE
            and _role_satisfies(b.role, role_needed)
            and b.scope_id is not None
        }

        device_ids: set[str] = set(direct_device_ids)
        if site_ids:
            device_ids.update(
                session.scalars(
                    select(Device.id).where(Device.site_id.in_(site_ids))
                )
            )
        if group_ids:
            device_ids.update(
                session.scalars(
                    select(GroupMembership.device_id).where(
                        GroupMembership.group_id.in_(group_ids)
                    )
                )
            )
        return device_ids


def can_act_on_device(user_id: str, device_id: str, role_needed: str) -> bool:
    eff = effective_device_ids(user_id, role_needed)
    return eff == ALL_SENTINEL or device_id in eff


def can_act_on_site(user_id: str, site_id: str, role_needed: str) -> bool:
    eff = effective_site_ids(user_id, role_needed)
    return eff == ALL_SENTINEL or site_id in eff
