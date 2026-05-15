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


def effective_group_ids(user_id: str, role_needed: str) -> str | set[str]:
    """All group_ids the user can act on at the requested role tier.
    Returns ``"ALL"`` for a global binding, else the set of the user's
    direct group bindings. A `site` binding does NOT imply group access —
    in the binding model a group is not nested under a site (the two are
    parallel device-set sources, see `effective_device_ids`)."""
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
            if b.scope_type == SCOPE_GROUP
            and _role_satisfies(b.role, role_needed)
            and b.scope_id is not None
        }


def can_act_on_group(user_id: str, group_id: str, role_needed: str) -> bool:
    eff = effective_group_ids(user_id, role_needed)
    return eff == ALL_SENTINEL or group_id in eff


# ── SHADOW-MODE ENFORCEMENT (A2 — v0.5.35, B1 RBAC Phase 1) ──────────────
# The resolvers above answer "can this user act on X?". The require_*
# helpers below turn that answer into a *gate*: in shadow mode a miss is
# logged as an `rbac.shadow_deny` audit row and the request still proceeds
# (legacy `role_required_*` stays authoritative); in enforce mode a miss
# raises RbacScopeDenied. The ONLY difference between the two modes is the
# `rbac.enforce_mode` runtime setting — there is no code branch to flip,
# so the A8 cut-over is a single setting toggle with no redeploy.

ENFORCE_MODE_SHADOW = "shadow"
ENFORCE_MODE_ENFORCE = "enforce"

RBAC_ENFORCE_MODE_KEY = "rbac.enforce_mode"
RBAC_ENFORCE_MODE_ENV = "REBOOTER_RBAC_ENFORCE_MODE"


class RbacScopeDenied(Exception):
    """Raised by ``require_can_act_on_*`` when ``rbac.enforce_mode`` is
    ``enforce`` and the caller's role bindings do not cover the target
    resource. In shadow mode this is never raised — the miss is logged
    as an audit row instead and the request proceeds."""

    def __init__(
        self,
        scope_type: str,
        scope_id: str | None,
        role_needed: str,
        reason: str = "out_of_scope",
    ):
        self.scope_type = scope_type
        self.scope_id = scope_id
        self.role_needed = role_needed
        self.reason = reason
        super().__init__(
            f"caller may not act on {scope_type}:{scope_id} at role '{role_needed}'"
        )


def enforce_mode() -> str:
    """Current RBAC enforcement mode — ``shadow`` (default) or
    ``enforce``. Runtime-toggleable via the ``rbac.enforce_mode``
    setting; takes effect immediately with no container restart."""
    from app.services import runtime_settings

    raw = runtime_settings.get(
        RBAC_ENFORCE_MODE_KEY,
        env_var=RBAC_ENFORCE_MODE_ENV,
        default=ENFORCE_MODE_SHADOW,
    )
    return (
        ENFORCE_MODE_ENFORCE
        if str(raw).strip().lower() == ENFORCE_MODE_ENFORCE
        else ENFORCE_MODE_SHADOW
    )


def _current_user():
    """Best-effort fetch of the request's authenticated user. The role
    decorators set ``g.current_user``; outside a request context this is
    simply ``None`` and the gate becomes a no-op."""
    try:
        from flask import g

        return getattr(g, "current_user", None)
    except Exception:
        return None


def _request_route() -> tuple[str | None, str | None]:
    try:
        from flask import request

        return request.path, request.method
    except Exception:
        return None, None


def _is_super_admin(user) -> bool:
    """super_admin escape hatch (RFC-003 §9.0 / B10 Q1): a super_admin is
    never scope-denied. Accepts EITHER the legacy ``users.role`` column or
    a global super_admin binding, so a super_admin stays exempt even if
    the one-shot backfill row is somehow missing."""
    if user is None:
        return False
    if getattr(user, "role", None) == ROLE_SUPER_ADMIN:
        return True
    uid = getattr(user, "id", None)
    return bool(uid) and has_global_role(uid, ROLE_SUPER_ADMIN)


def _scope_gate(
    user,
    *,
    allowed: bool,
    scope_type: str,
    scope_id: str | None,
    role_needed: str,
) -> None:
    """Shared shadow/enforce decision. When ``allowed`` → no-op. When
    denied: emit an audit row (``rbac.shadow_deny`` or
    ``rbac.enforce_deny``) and, in enforce mode only, raise
    ``RbacScopeDenied``."""
    if allowed:
        return
    mode = enforce_mode()
    route, method = _request_route()
    action = (
        "rbac.enforce_deny" if mode == ENFORCE_MODE_ENFORCE else "rbac.shadow_deny"
    )
    # The audit path is best-effort and never raises (see audit.record).
    from app.services import audit

    audit.record(
        action,
        actor_user_id=getattr(user, "id", None),
        actor_email_snapshot=getattr(user, "email", None),
        target_type=scope_type,
        target_id=scope_id,
        details={
            "route": route,
            "method": method,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "role_needed": role_needed,
            "reason": "out_of_scope",
            "enforce_mode": mode,
        },
    )
    if mode == ENFORCE_MODE_ENFORCE:
        raise RbacScopeDenied(scope_type, scope_id, role_needed)


def require_can_act_on_device(
    device_id: str, role_needed: str, *, user=None
) -> None:
    """Gate the caller against a device-scoped resource. See the
    module-level SHADOW-MODE note. ``user`` defaults to
    ``g.current_user`` (set by the role decorators)."""
    user = user if user is not None else _current_user()
    if user is None:
        return  # legacy auth already 401'd; nothing to gate
    if _is_super_admin(user):
        return
    allowed = can_act_on_device(user.id, device_id, role_needed)
    _scope_gate(
        user,
        allowed=allowed,
        scope_type=SCOPE_DEVICE,
        scope_id=device_id,
        role_needed=role_needed,
    )


def require_can_act_on_site(site_id: str, role_needed: str, *, user=None) -> None:
    """Gate the caller against a site-scoped resource."""
    user = user if user is not None else _current_user()
    if user is None:
        return
    if _is_super_admin(user):
        return
    allowed = can_act_on_site(user.id, site_id, role_needed)
    _scope_gate(
        user,
        allowed=allowed,
        scope_type=SCOPE_SITE,
        scope_id=site_id,
        role_needed=role_needed,
    )


def require_can_act_on_group(group_id: str, role_needed: str, *, user=None) -> None:
    """Gate the caller against a group-scoped resource."""
    user = user if user is not None else _current_user()
    if user is None:
        return
    if _is_super_admin(user):
        return
    allowed = can_act_on_group(user.id, group_id, role_needed)
    _scope_gate(
        user,
        allowed=allowed,
        scope_type=SCOPE_GROUP,
        scope_id=group_id,
        role_needed=role_needed,
    )
