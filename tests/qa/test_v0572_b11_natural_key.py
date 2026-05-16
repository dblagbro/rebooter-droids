"""v0.5.72 (B11) — applier natural-key reconciliation + site_id remap.

The two hubs bootstrap their own admin user and "Default" site
independently, so the *same* logical entity exists on each under a
*different* id. Pre-fix, syncing one hub's bootstrap entity to the
other hit a UNIQUE-constraint collision (`users_email_key`,
`sites_name_key`) — a permanent cursor error.

This verifies the fix: a create whose id is unknown locally but whose
unique natural key matches an existing row reconciles into an update
(no collision); and a device/group whose `site_id` points at a site
this hub lacks is remapped to the local Default site (no FK error).

In-process SQLite — runs in the `-m ci` gate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401 — registers every model on Base.metadata
from app.models import Base, Device, Site, User
from app.models.sync import OutboxEvent
from app.services import sync as sync_svc

pytestmark = pytest.mark.ci

T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _event(entity_type, entity_id, payload, at):
    return OutboxEvent(
        seq=1, at=at.isoformat(), event_type=f"{entity_type}.updated",
        entity_type=entity_type, entity_id=entity_id, payload=payload,
        tombstone_for=None,
    )


def test_site_create_reconciles_on_name_not_collides(session):
    # Local "Default" under one id; peer sends "Default" under another.
    session.add(Site(id="site_local", name="Default", description="local",
                      created_at=T0, updated_at=T0))
    session.flush()
    newer = T0 + timedelta(minutes=5)
    ev = _event("site", "site_peer", {
        "id": "site_peer", "name": "Default", "description": "from peer",
        "created_at": newer.isoformat(), "updated_at": newer.isoformat(),
    }, newer)
    assert sync_svc.apply_outbox_event(session, ev) is True
    # No colliding row created; the local row converged instead.
    assert session.get(Site, "site_peer") is None
    assert session.get(Site, "site_local").description == "from peer"
    assert len(list(session.scalars(select(Site)))) == 1


def test_user_create_reconciles_on_email_not_collides(session):
    session.add(User(id="usr_local", email="admin@example.com",
                     password_hash="x", display_name="Local",
                     created_at=T0, updated_at=T0))
    session.flush()
    newer = T0 + timedelta(minutes=5)
    ev = _event("user", "usr_peer", {
        "id": "usr_peer", "email": "admin@example.com", "display_name": "Peer",
        "created_at": newer.isoformat(), "updated_at": newer.isoformat(),
    }, newer)
    assert sync_svc.apply_outbox_event(session, ev) is True
    assert session.get(User, "usr_peer") is None
    assert session.get(User, "usr_local").display_name == "Peer"
    assert len(list(session.scalars(select(User)))) == 1


def test_natural_key_reconcile_still_respects_lww(session):
    # Local row is NEWER — a stale peer event must not overwrite it.
    newer = T0 + timedelta(minutes=5)
    session.add(Site(id="site_local", name="Default", description="current",
                     created_at=T0, updated_at=newer))
    session.flush()
    ev = _event("site", "site_peer", {
        "id": "site_peer", "name": "Default", "description": "stale",
        "created_at": T0.isoformat(), "updated_at": T0.isoformat(),
    }, T0)
    assert sync_svc.apply_outbox_event(session, ev) is False
    assert session.get(Site, "site_local").description == "current"
    assert session.get(Site, "site_peer") is None


def test_device_with_unknown_site_id_is_remapped_to_default(session):
    session.add(Site(id="site_default", name="Default",
                     created_at=T0, updated_at=T0))
    session.flush()
    ev = _event("device", "dev_x", {
        "id": "dev_x", "display_name": "Plug", "site_id": "site_ghost",
        "registration_state": "active", "capabilities": {},
        "created_at": T0.isoformat(), "updated_at": T0.isoformat(),
    }, T0)
    assert sync_svc.apply_outbox_event(session, ev) is True
    # The unknown site_id was remapped to the local Default — no FK break.
    assert session.get(Device, "dev_x").site_id == "site_default"


def test_device_known_site_id_is_left_alone(session):
    session.add(Site(id="site_default", name="Default", created_at=T0, updated_at=T0))
    session.add(Site(id="site_garage", name="Garage", created_at=T0, updated_at=T0))
    session.flush()
    ev = _event("device", "dev_y", {
        "id": "dev_y", "display_name": "Plug", "site_id": "site_garage",
        "registration_state": "active", "capabilities": {},
        "created_at": T0.isoformat(), "updated_at": T0.isoformat(),
    }, T0)
    assert sync_svc.apply_outbox_event(session, ev) is True
    assert session.get(Device, "dev_y").site_id == "site_garage"
