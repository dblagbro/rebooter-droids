"""v0.5.70 (B11) — apply_outbox_event() upsert + last-writer-wins.

In-process unit test of the multi-hub sync applier. Builds a throwaway
in-memory SQLite database and exercises `sync.apply_outbox_event`
directly — create, LWW-update, LWW-skip (stale write), idempotency,
tombstone-blocks-recreate, delete, and unknown-entity skip. No HTTP,
no Docker, no Postgres.

This is the first `tests/unit/`-style in-process test (P-QA charter,
docs/test-plan.md) — it runs in the `-m ci` gate.

org-boundary phase 3: `sites.organization_id` is now NOT NULL, and the
applier verifies/scopes the event's org (design §3.7). The fixture
seeds one `Organization` (`ORG_ID`) and every site payload carries it
both in the row body and in `scope_claims` so the applier can verify
and scope the write.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401 — registers every model on Base.metadata
from app.models import Base, Organization, Site
from app.models.sync import OutboxEvent, Tombstone
from app.services import sync as sync_svc

pytestmark = pytest.mark.ci

T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
ORG_ID = "org_testapplier0001"


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        # org-boundary phase 3: the applier verifies the event's org
        # exists locally — seed it once for the whole test.
        s.add(Organization(
            id=ORG_ID, name="Applier Test Org", slug="applier-test",
            status="active", plan="legacy", is_self_hosted_default=False,
        ))
        s.flush()
        yield s


def _site_payload(site_id: str, name: str, updated_at: datetime) -> dict:
    return {
        "id": site_id,
        "name": name,
        "organization_id": ORG_ID,
        "description": None,
        "created_at": updated_at.isoformat(),
        "updated_at": updated_at.isoformat(),
    }


def _event(entity_id, payload, at, *, tombstone_for=None, seq=1, entity_type="site"):
    # `at` is passed as an ISO string — mirrors the replicator path,
    # where the transient OutboxEvent is built from peer JSON.
    # org-boundary phase 3: a site event carries its org in
    # `scope_claims` so the applier can verify + scope it.
    scope_claims = (
        {"organization_id": ORG_ID} if entity_type == "site" else None
    )
    return OutboxEvent(
        seq=seq,
        at=at.isoformat() if isinstance(at, datetime) else at,
        event_type=f"{entity_type}.updated",
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        tombstone_for=tombstone_for,
        scope_claims=scope_claims,
    )


def test_create_inserts_a_new_row(session):
    ev = _event("site_aaa", _site_payload("site_aaa", "Lab", T0), T0)
    assert sync_svc.apply_outbox_event(session, ev) is True
    row = session.get(Site, "site_aaa")
    assert row is not None and row.name == "Lab"


def test_lww_newer_write_wins(session):
    session.add(Site(id="site_b", name="Old", organization_id=ORG_ID, created_at=T0, updated_at=T0))
    session.flush()
    newer = T0 + timedelta(minutes=5)
    ev = _event("site_b", _site_payload("site_b", "New", newer), newer)
    assert sync_svc.apply_outbox_event(session, ev) is True
    assert session.get(Site, "site_b").name == "New"


def test_lww_stale_write_is_skipped(session):
    newer = T0 + timedelta(minutes=5)
    session.add(Site(id="site_c", name="Current", organization_id=ORG_ID, created_at=T0, updated_at=newer))
    session.flush()
    # Event carries an OLDER updated_at — must lose.
    ev = _event("site_c", _site_payload("site_c", "Stale", T0), T0)
    assert sync_svc.apply_outbox_event(session, ev) is False
    assert session.get(Site, "site_c").name == "Current"


def test_apply_is_idempotent(session):
    payload = _site_payload("site_d", "Once", T0)
    ev = _event("site_d", payload, T0)
    assert sync_svc.apply_outbox_event(session, ev) is True
    session.flush()
    # Re-applying the identical event is a no-op (LWW: equal updated_at).
    assert sync_svc.apply_outbox_event(session, ev) is False
    assert session.get(Site, "site_d").name == "Once"


def test_delete_event_removes_row_and_writes_tombstone(session):
    session.add(Site(id="site_e", name="Doomed", organization_id=ORG_ID, created_at=T0, updated_at=T0))
    session.flush()
    ev = _event("site_e", {"deleted": True}, T0, tombstone_for="site_e", seq=9)
    assert sync_svc.apply_outbox_event(session, ev) is True
    session.flush()
    assert session.get(Site, "site_e") is None
    assert session.get(Tombstone, "site_e") is not None


def test_tombstoned_entity_cannot_be_recreated(session):
    sync_svc.add_tombstone(session, "site_f", "site", from_outbox_seq=1)
    session.flush()
    ev = _event("site_f", _site_payload("site_f", "Zombie", T0), T0)
    assert sync_svc.apply_outbox_event(session, ev) is False
    assert session.get(Site, "site_f") is None


def test_unknown_entity_type_is_skipped(session):
    ev = _event("x_1", {"id": "x_1"}, T0, entity_type="banana")
    assert sync_svc.apply_outbox_event(session, ev) is False
