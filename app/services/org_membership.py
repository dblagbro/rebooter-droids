"""Organization-membership service — multi-tenant boundary, Phase 2.

Per `docs/notes/2026-05-20-organization-boundary-design.md` §3.2, §4.1.

Resolves which org the active tenant scope should be bound to for an
authenticated principal:

  * `memberships_for_user()` — every org a user belongs to (M:N).
  * `resolve_active_org()` — the *one* org a request runs under, read
    from the Flask session's `active_org_id` and ALWAYS re-validated
    against the user's live memberships (design §3.2: "never trust the
    session value blind"). Defaults to the user's sole/primary org.
  * `is_member()` — defense-in-depth membership check.

These run inside `tenant_scope.system()` because resolving a user's
memberships is itself a query against org-owned tables — it must not be
filtered by a scope that has not been bound yet (chicken-and-egg).
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select

from app.db import session_scope
from app.models import Organization, OrganizationMembership
from app.models.organizations import ORG_ROLE_OWNER, ORG_STATUS_ACTIVE
from app.services import tenant_scope

log = logging.getLogger(__name__)


def memberships_for_user(user_id: str) -> list[dict]:
    """Every org membership for a user — id, org_id, org_role, plus the
    org's slug/name/status. Ordered owner-first then by org name so a
    sensible "primary" org falls out of the list head.
    """
    if not user_id:
        return []
    with tenant_scope.system():
        with session_scope() as s:
            rows = list(
                s.execute(
                    select(OrganizationMembership, Organization)
                    .join(
                        Organization,
                        Organization.id == OrganizationMembership.organization_id,
                    )
                    .where(OrganizationMembership.user_id == user_id)
                )
            )
            out = [
                {
                    "membership_id": m.id,
                    "organization_id": m.organization_id,
                    "org_role": m.org_role,
                    "slug": o.slug,
                    "name": o.name,
                    "status": o.status,
                }
                for (m, o) in rows
            ]
    # Owner memberships first, then by org name — gives a deterministic
    # "primary" org for users with a single membership (the common case)
    # and a stable default for MSP users with several.
    out.sort(key=lambda r: (r["org_role"] != ORG_ROLE_OWNER, r["name"] or ""))
    return out


def is_member(user_id: str, org_id: str) -> bool:
    """True iff the user holds a membership in the given org. Used as a
    defense-in-depth check before honouring a session-supplied org id."""
    if not (user_id and org_id):
        return False
    with tenant_scope.system():
        with session_scope() as s:
            return (
                s.scalar(
                    select(OrganizationMembership.id).where(
                        OrganizationMembership.user_id == user_id,
                        OrganizationMembership.organization_id == org_id,
                    )
                )
                is not None
            )


def resolve_active_org(user, *, session_org_id: Optional[str] = None) -> Optional[str]:
    """The org the current request should be scoped to.

    Algorithm (design §3.2):
      1. Gather the user's live memberships.
      2. If `session_org_id` is supplied (from `session["active_org_id"]`,
         set by the org-switcher) AND the user is still a member of it
         AND that org is not closed — honour it.
      3. Otherwise fall back to the head of `memberships_for_user()`
         (owner-first, then name) — the user's sole/primary org.
      4. If the user has NO memberships, return None. The request then
         runs unscoped; the `do_orm_execute` filter will flag any
         Tier-A access as a `tenant.unscoped_access` audit row. A
         membership-less user is a data anomaly (the backfill creates a
         membership per user) and is surfaced rather than silently
         granted ambient cross-org reach.

    Crucially the session value is NEVER trusted blind — it is
    re-validated against the live membership list on every request, so a
    stale or tampered `active_org_id` cannot widen access.
    """
    user_id = getattr(user, "id", None)
    if not user_id:
        return None

    memberships = memberships_for_user(user_id)
    if not memberships:
        log.warning(
            "resolve_active_org: user %s has no organization membership — "
            "request will run unscoped (data anomaly; backfill should have "
            "created a membership).",
            user_id,
        )
        return None

    member_org_ids = {m["organization_id"] for m in memberships}

    if session_org_id and session_org_id in member_org_ids:
        # Confirm the org is still usable (not closed). A suspended org
        # is still scoped to (so the suspension lockout can be applied
        # by a separate layer); a closed org is treated as gone.
        status = next(
            (m["status"] for m in memberships if m["organization_id"] == session_org_id),
            None,
        )
        if status != "closed":
            return session_org_id
        log.info(
            "resolve_active_org: session active_org_id %s for user %s is "
            "closed — falling back to primary org",
            session_org_id,
            user_id,
        )

    # Default: the primary (owner-first, name-sorted) membership.
    return memberships[0]["organization_id"]
