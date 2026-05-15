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
