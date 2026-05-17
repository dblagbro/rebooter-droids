"""B1 RBAC P2 regression — Device.site_id NOT NULL + audit archive (v0.5.36).

In-process: builds an isolated SQLite hub DB and exercises the audit
archive schema + the nightly-prune service directly — no HTTP, no
Docker. Runs in the `-m ci` gate.

Rewritten v0.5.80 (P-QA gate-3): the original used `create_app()` +
`app_context()` against a reachable database and a `base_url` version
gate, so it could only run on a hub host. The isolated-SQLite pattern
mirrors test_v0514.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import flask
import pytest

from app.config import load_settings
from app.db import get_engine, init_engine, session_scope
from app.models import Base

# v0.5.80: in the `-m ci` gate (P-QA gate-3 — in-process tests).
pytestmark = pytest.mark.ci


@pytest.fixture
def hub_db(tmp_path):
    """Isolated SQLite hub DB + a bare Flask app context (some services
    reach for Flask `g`). Mirrors test_v0514's in-process pattern."""
    settings = replace(
        load_settings(),
        database_url=f"sqlite:///{tmp_path / 'rebooter-qa.sqlite'}",
    )
    init_engine(settings)
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with flask.Flask(__name__).app_context():
        yield settings


def test_all_devices_have_site_id(hub_db):
    """(a) No devices row has a null site_id (the NOT NULL column holds)."""
    from sqlalchemy import func, select

    from app.models import Device

    with session_scope() as session:
        null_count = session.scalar(
            select(func.count()).select_from(Device).where(Device.site_id.is_(None))
        ) or 0
    assert null_count == 0


def test_audit_events_archive_table_exists(hub_db):
    """(b) audit_events_archive table exists with the expected shape."""
    from sqlalchemy import inspect

    inspector = inspect(get_engine())
    assert inspector.has_table("audit_events_archive"), (
        "audit_events_archive table not found"
    )
    columns = {c["name"]: c for c in inspector.get_columns("audit_events_archive")}
    expected = {
        "id", "at", "actor_user_id", "actor_email_snapshot",
        "action", "target_type", "target_id", "details", "ip",
        "archived_at",  # P2 addition
    }
    assert expected <= set(columns), (
        f"missing columns in audit_events_archive: {expected - set(columns)}"
    )
    assert not columns["archived_at"]["nullable"], "archived_at must be NOT NULL"


def test_nightly_prune_job(hub_db):
    """(c) The prune job archives + removes audit events past retention."""
    from app.models import AuditEvent, AuditEventArchive
    from app.services import runtime_settings as rs
    from app.services.audit_prune import prune_old_audit_events

    now = datetime.now(timezone.utc)
    old_timestamp = now - timedelta(days=2)
    with session_scope() as session:
        event = AuditEvent(
            at=old_timestamp,
            actor_user_id=None,
            actor_email_snapshot="test-prune@example.com",
            action="test.prune_target",
            target_type="test",
            target_id="prune_test_001",
            details={"test": "prune"},
            ip="127.0.0.1",
        )
        session.add(event)
        session.flush()
        old_event_id = event.id

    # Retention of 1 day puts our 2-day-old event past the threshold.
    rs.set_("system.audit_retention_days", 1)
    rs.delete("system.audit_prune_last_run_date")

    stats = prune_old_audit_events()
    assert stats["archived"] >= 1, f"expected >=1 archived, got {stats}"
    assert stats["pruned"] >= 1, f"expected >=1 pruned, got {stats}"
    assert not stats.get("errors"), f"prune had errors: {stats.get('errors')}"

    with session_scope() as session:
        assert session.get(AuditEvent, old_event_id) is None, (
            "old event still in audit_events after prune"
        )
        archived = session.get(AuditEventArchive, old_event_id)
        assert archived is not None, "old event not found in archive"
        assert archived.action == "test.prune_target"
        assert archived.archived_at is not None


def test_prune_date_rollover_guard(hub_db):
    """The prune job runs at most once per UTC day (date-rollover guard)."""
    from app.services import runtime_settings as rs
    from app.services.audit_prune import prune_old_audit_events

    rs.set_("system.audit_retention_days", 1)
    # Mark the job as already run today — the next call must skip.
    today = datetime.now(timezone.utc).date().isoformat()
    rs.set_("system.audit_prune_last_run_date", today)

    stats = prune_old_audit_events()
    assert stats.get("skipped") is True, f"expected same-day re-run to skip: {stats}"
    assert stats["archived"] == 0
    assert stats["pruned"] == 0
