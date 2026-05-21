from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import session_scope
from app.models import Site


class DuplicateNameError(ValueError):
    pass


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def serialize_site(s: Site) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "created_at": _iso(s.created_at),
    }


def list_sites() -> list[dict]:
    with session_scope() as session:
        stmt = select(Site).order_by(Site.name)

        # v0.5.37 (B1 RBAC Phase 3): Apply scope-based filtering.
        # In shadow mode, logs what WOULD be hidden; in enforce mode, actually filters.
        from app.services.rbac_filter import filter_sites_with_shadow_logging
        sites = filter_sites_with_shadow_logging(stmt, session)

        return [serialize_site(s) for s in sites]


DEFAULT_SITE_NAME = "Default"


def resolve_default_site_id(session) -> str:
    """Return a `site_id` to assign a device that arrives without one.

    `devices.site_id` has been NOT NULL since v0.5.36 (RBAC P2), but
    enrolment tokens minted by the announce/adopt flow carry no site —
    so a fresh `/register` would hit a NotNullViolation and 500. This
    resolves a fallback, in priority order:

      1. the site named "Default", if it exists,
      2. else the only site, if exactly one exists,
      3. else a freshly created "Default" site.

    Operates within the caller's session/transaction so the resolved
    (or created) site commits atomically with the device row.

    load-degradation fix (2026-05-21): the device-register path
    (`enrollment.consume_enrollment_token` → here) runs with NO bound
    org scope, and the `before_flush` org-stamping in `tenant_scope`
    only stamps `organization_id` when an org IS bound (it is also a
    no-op under `tenant_scope.system()`). The org-boundary work made
    `sites.organization_id` NOT NULL — so creating the `Default` site
    with no org raised a `NotNullViolation` and `/register` 500'd. The
    new `Site` therefore stamps `organization_id` EXPLICITLY here, from
    the same default organization the bootstrap backfill creates/uses.
    """
    site = session.scalar(select(Site).where(Site.name == DEFAULT_SITE_NAME))
    if site is not None:
        return site.id
    sites = list(session.scalars(select(Site)))
    if len(sites) == 1:
        return sites[0].id

    # `before_flush` will not stamp organization_id here (no org scope is
    # bound on the device-register path, and it is a no-op under
    # system()), and the column is NOT NULL — so resolve and set it
    # explicitly. `resolve_default_org_id` returns the same default org
    # the startup backfill uses (slug "default", or the sole org).
    from app.services.bootstrap import resolve_default_org_id

    org_id = resolve_default_org_id(session)
    if org_id is None:
        raise RuntimeError(
            "resolve_default_site_id: cannot create the Default site — no "
            "organization exists to own it. The default-organization "
            "backfill should have run at startup."
        )
    site = Site(name=DEFAULT_SITE_NAME, organization_id=org_id)
    session.add(site)
    session.flush()
    return site.id


def create_site(name: str, description: str | None) -> dict:
    s = Site(name=name, description=description)
    try:
        with session_scope() as session:
            session.add(s)
            session.flush()
            return serialize_site(s)
    except IntegrityError:
        raise DuplicateNameError(f"a site named '{name}' already exists")


def delete_site(site_id: str) -> bool:
    with session_scope() as session:
        s = session.get(Site, site_id)
        if s is None:
            return False
        session.delete(s)
        session.flush()
        return True
