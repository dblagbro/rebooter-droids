"""RBAC list filtering — v0.5.37 (B1 RBAC Phase 3).

Applies scope-based filtering to list queries in shadow or enforce mode.
In shadow mode, double-queries and logs diffs; in enforce mode, applies
the filter directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flask import g

from app.services import audit as audit_service
from app.services import role_bindings as rb

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from sqlalchemy.sql import Select

log = logging.getLogger(__name__)


def apply_device_scope_filter(
    stmt: Select,
    session: Session,
    *,
    user_id: str | None = None,
    role_needed: str = "viewer",
) -> tuple[Select, dict]:
    """Apply RBAC device scope filter to a SELECT statement.

    Returns:
        (filtered_stmt, metadata) where metadata contains:
        - mode: "shadow" | "enforce"
        - filtered: bool (whether filtering was applied)
        - scope: "ALL" | set of device_ids
    """
    from app.models.users import ROLE_VIEWER

    # Use current user if not specified
    if user_id is None:
        user_id = getattr(g.get("current_user"), "id", None)

    if not user_id:
        # No user context — return unfiltered (system/background job)
        return stmt, {"mode": "none", "filtered": False, "scope": "ALL"}

    # Super_admin escape hatch
    if rb._is_super_admin(user_id):
        return stmt, {"mode": "bypass", "filtered": False, "scope": "ALL"}

    # Get enforce mode
    mode = rb.enforce_mode()

    # Resolve effective scope
    scope = rb.effective_device_ids(user_id, role_needed or ROLE_VIEWER)

    # If scope is "ALL", no filtering needed
    if scope == "ALL":
        return stmt, {"mode": mode, "filtered": False, "scope": "ALL"}

    # Apply filter
    from app.models import Device
    filtered_stmt = stmt.where(Device.id.in_(scope))

    return filtered_stmt, {
        "mode": mode,
        "filtered": True,
        "scope": scope,
        "scope_size": len(scope) if isinstance(scope, set) else 0,
    }


def filter_devices_with_shadow_logging(
    base_stmt: Select,
    session: Session,
    *,
    user_id: str | None = None,
    role_needed: str = "viewer",
) -> list:
    """Execute device query with RBAC filtering and shadow-mode diff logging.

    In shadow mode: runs both unfiltered and filtered queries, logs diff,
    returns unfiltered results.

    In enforce mode: runs filtered query, returns filtered results.
    """
    from app.models import Device

    # Get filtered statement and metadata
    filtered_stmt, meta = apply_device_scope_filter(
        base_stmt, session, user_id=user_id, role_needed=role_needed
    )

    # If no filtering applied (super_admin or global scope), return unfiltered
    if not meta["filtered"]:
        return list(session.scalars(base_stmt))

    mode = meta["mode"]

    if mode == "enforce":
        # Enforce mode: return filtered results only
        return list(session.scalars(filtered_stmt))

    # Shadow mode: double-query and log diff
    unfiltered_rows = list(session.scalars(base_stmt))
    filtered_rows = list(session.scalars(filtered_stmt))

    unfiltered_ids = {d.id for d in unfiltered_rows}
    filtered_ids = {d.id for d in filtered_rows}
    hidden_ids = unfiltered_ids - filtered_ids

    if hidden_ids:
        # Log shadow diff
        user = g.get("current_user")
        audit_service.record(
            "rbac.shadow_diff",
            actor_user_id=user_id,
            actor_email_snapshot=user.email if user else None,
            target_type="device",
            target_id=None,
            details={
                "resource_type": "device",
                "role_needed": role_needed,
                "total_count": len(unfiltered_ids),
                "scoped_count": len(filtered_ids),
                "hidden_count": len(hidden_ids),
                "hidden_sample": sorted(hidden_ids)[:10],  # First 10 for inspection
                "scope_size": meta.get("scope_size", 0),
            },
        )
        log.info(
            "RBAC shadow diff: device list for user %s would hide %d/%d devices",
            user_id,
            len(hidden_ids),
            len(unfiltered_ids),
        )

    # Shadow mode returns unfiltered results (legacy behavior preserved)
    return unfiltered_rows


def filter_groups_with_shadow_logging(
    base_stmt: Select,
    session: Session,
    *,
    user_id: str | None = None,
    role_needed: str = "viewer",
) -> list:
    """Execute group query with RBAC filtering and shadow-mode diff logging.

    Similar to filter_devices_with_shadow_logging but for groups.
    """
    from app.models import Group
    from app.models.users import ROLE_VIEWER

    if user_id is None:
        user_id = getattr(g.get("current_user"), "id", None)

    if not user_id:
        return list(session.scalars(base_stmt))

    if rb._is_super_admin(user_id):
        return list(session.scalars(base_stmt))

    mode = rb.enforce_mode()
    scope = rb.effective_group_ids(user_id, role_needed or ROLE_VIEWER)

    if scope == "ALL":
        return list(session.scalars(base_stmt))

    filtered_stmt = base_stmt.where(Group.id.in_(scope))

    if mode == "enforce":
        return list(session.scalars(filtered_stmt))

    # Shadow mode: double-query and log diff
    unfiltered_rows = list(session.scalars(base_stmt))
    filtered_rows = list(session.scalars(filtered_stmt))

    unfiltered_ids = {g.id for g in unfiltered_rows}
    filtered_ids = {g.id for g in filtered_rows}
    hidden_ids = unfiltered_ids - filtered_ids

    if hidden_ids:
        user = g.get("current_user")
        audit_service.record(
            "rbac.shadow_diff",
            actor_user_id=user_id,
            actor_email_snapshot=user.email if user else None,
            target_type="group",
            target_id=None,
            details={
                "resource_type": "group",
                "role_needed": role_needed,
                "total_count": len(unfiltered_ids),
                "scoped_count": len(filtered_ids),
                "hidden_count": len(hidden_ids),
                "hidden_sample": sorted(hidden_ids)[:10],
                "scope_size": len(scope) if isinstance(scope, set) else 0,
            },
        )
        log.info(
            "RBAC shadow diff: group list for user %s would hide %d/%d groups",
            user_id,
            len(hidden_ids),
            len(unfiltered_ids),
        )

    return unfiltered_rows


def filter_sites_with_shadow_logging(
    base_stmt: Select,
    session: Session,
    *,
    user_id: str | None = None,
    role_needed: str = "viewer",
) -> list:
    """Execute site query with RBAC filtering and shadow-mode diff logging.

    Similar to filter_devices_with_shadow_logging but for sites.
    """
    from app.models import Site
    from app.models.users import ROLE_VIEWER

    if user_id is None:
        user_id = getattr(g.get("current_user"), "id", None)

    if not user_id:
        return list(session.scalars(base_stmt))

    if rb._is_super_admin(user_id):
        return list(session.scalars(base_stmt))

    mode = rb.enforce_mode()
    scope = rb.effective_site_ids(user_id, role_needed or ROLE_VIEWER)

    if scope == "ALL":
        return list(session.scalars(base_stmt))

    filtered_stmt = base_stmt.where(Site.id.in_(scope))

    if mode == "enforce":
        return list(session.scalars(filtered_stmt))

    # Shadow mode: double-query and log diff
    unfiltered_rows = list(session.scalars(base_stmt))
    filtered_rows = list(session.scalars(filtered_stmt))

    unfiltered_ids = {s.id for s in unfiltered_rows}
    filtered_ids = {s.id for s in filtered_rows}
    hidden_ids = unfiltered_ids - filtered_ids

    if hidden_ids:
        user = g.get("current_user")
        audit_service.record(
            "rbac.shadow_diff",
            actor_user_id=user_id,
            actor_email_snapshot=user.email if user else None,
            target_type="site",
            target_id=None,
            details={
                "resource_type": "site",
                "role_needed": role_needed,
                "total_count": len(unfiltered_ids),
                "scoped_count": len(filtered_ids),
                "hidden_count": len(hidden_ids),
                "hidden_sample": sorted(hidden_ids)[:10],
                "scope_size": len(scope) if isinstance(scope, set) else 0,
            },
        )
        log.info(
            "RBAC shadow diff: site list for user %s would hide %d/%d sites",
            user_id,
            len(hidden_ids),
            len(unfiltered_ids),
        )

    return unfiltered_rows
