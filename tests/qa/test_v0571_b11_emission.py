"""v0.5.71 (B11) — sync-emission ORM hooks.

In-process unit test of `sync_emission`: every create/update/delete of a
syncable model must land an `outbox_events` row, an `updated` event must
fire only on a real config change (not heartbeat/telemetry churn), and
the applier's own writes must not emit (loop prevention). Self-contained
SQLite — runs in the `-m ci` gate, no infra.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401 — registers every model on Base.metadata
from app.models import Base, Device, Organization, Site
from app.models.sync import OutboxEvent
from app.services.sync import suppress_emission
from app.services.sync_emission import register_sync_emission
from app.services.tenant_scope import org_context

pytestmark = pytest.mark.ci

T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def session():
    register_sync_emission()  # idempotent — attaches the hooks once
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        # org-boundary phase 3 made `organization_id` NOT NULL on the
        # Tier-A tables (Site, Device, ...). Seed the default org and
        # run the test body inside `org_context` — exactly how a real
        # request runs, where the admin-auth middleware binds the scope
        # so the `before_flush` hook stamps `organization_id` onto every
        # new Tier-A row. Without it the direct `Site(...)` inserts below
        # would hit the NOT NULL constraint.
        org = Organization(
            id="org_test", name="Test Org", slug="default", plan="legacy"
        )
        s.add(org)
        s.flush()
        with org_context(org.id):
            yield s


def _events(session, entity_type=None):
    stmt = select(OutboxEvent).order_by(OutboxEvent.seq)
    if entity_type:
        stmt = stmt.where(OutboxEvent.entity_type == entity_type)
    return list(session.scalars(stmt))


def test_create_emits_a_created_event(session):
    session.add(Site(id="site_a", name="Lab", created_at=T0, updated_at=T0))
    session.flush()
    evs = _events(session, "site")
    assert len(evs) == 1
    assert evs[0].event_type == "site.created"
    assert evs[0].entity_id == "site_a"
    assert evs[0].payload["name"] == "Lab"  # full snapshot, not a marker


def test_config_update_emits_an_updated_event(session):
    session.add(Site(id="site_b", name="Old", created_at=T0, updated_at=T0))
    session.flush()
    site = session.get(Site, "site_b")
    site.name = "Renamed"
    site.updated_at = T0 + timedelta(minutes=1)
    session.flush()
    updates = [e for e in _events(session, "site") if e.event_type == "site.updated"]
    assert len(updates) == 1
    assert updates[0].payload["name"] == "Renamed"


def test_updated_at_only_bump_does_not_emit(session):
    session.add(Site(id="site_c", name="Steady", created_at=T0, updated_at=T0))
    session.flush()
    site = session.get(Site, "site_c")
    site.updated_at = T0 + timedelta(minutes=1)  # only the ignored column
    session.flush()
    assert [e for e in _events(session, "site") if e.event_type == "site.updated"] == []


def test_delete_emits_a_tombstone_event(session):
    session.add(Site(id="site_d", name="Doomed", created_at=T0, updated_at=T0))
    session.flush()
    session.delete(session.get(Site, "site_d"))
    session.flush()
    dels = [e for e in _events(session, "site") if e.event_type == "site.deleted"]
    assert len(dels) == 1
    assert dels[0].tombstone_for == "site_d"


def test_suppressed_writes_do_not_emit(session):
    # The applier wraps its writes in suppress_emission() — exercise that.
    with suppress_emission():
        session.add(Site(id="site_e", name="Quiet", created_at=T0, updated_at=T0))
        session.flush()
    assert _events(session) == []


def test_device_heartbeat_columns_do_not_emit_but_config_does(session):
    session.add(Site(id="site_dev", name="S", created_at=T0, updated_at=T0))
    session.add(Device(
        id="dev_x", display_name="Plug", site_id="site_dev",
        registration_state="active", capabilities={},
        created_at=T0, updated_at=T0,
    ))
    session.flush()
    assert [e.event_type for e in _events(session, "device")] == ["device.created"]

    dev = session.get(Device, "dev_x")
    # A heartbeat touches only telemetry columns — must NOT emit.
    dev.last_heartbeat_at = T0 + timedelta(minutes=1)
    dev.reported_central_state = "central_ok"
    dev.updated_at = T0 + timedelta(minutes=1)
    session.flush()
    assert [e.event_type for e in _events(session, "device")] == ["device.created"]

    # A config change (rename) must emit.
    dev.display_name = "Renamed Plug"
    dev.updated_at = T0 + timedelta(minutes=2)
    session.flush()
    assert [e.event_type for e in _events(session, "device")] == [
        "device.created", "device.updated",
    ]
