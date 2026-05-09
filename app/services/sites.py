from __future__ import annotations

from sqlalchemy import select

from app.db import session_scope
from app.models import Site


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
        return [
            serialize_site(s)
            for s in session.scalars(select(Site).order_by(Site.name))
        ]


def create_site(name: str, description: str | None) -> dict:
    s = Site(name=name, description=description)
    with session_scope() as session:
        session.add(s)
        session.flush()
        return serialize_site(s)


def delete_site(site_id: str) -> bool:
    with session_scope() as session:
        s = session.get(Site, site_id)
        if s is None:
            return False
        session.delete(s)
        session.flush()
        return True
