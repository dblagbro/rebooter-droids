"""B1 RBAC P2 regression — Device.site_id NOT NULL + audit archive (v0.5.36).

Asserts:
  (a) Every existing devices row has non-null site_id after backfill
  (b) audit_events_archive table exists with correct shape
  (c) Nightly prune job moves old audit_events to archive correctly

Note: This test requires direct database access and must run against
the live deployment with the app context initialized.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Add project root to path so we can import app modules
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture(scope="module", autouse=True)
def init_app():
    """Initialize Flask app and database engine for database tests."""
    from app import create_app
    from app.db import init_engine

    app = create_app()
    # Use existing database connection
    with app.app_context():
        yield app


@pytest.fixture(scope="module", autouse=True)
def version_gate(base_url):
    """Only run if backend is v0.5.36+"""
    import requests
    resp = requests.get(f"{base_url}/api/v1/version", timeout=10)
    ver = resp.json()["version"]
    major, minor, patch = map(int, ver.split("."))
    if (major, minor, patch) < (0, 5, 36):
        pytest.skip(f"Backend {ver} < 0.5.36")


def test_all_devices_have_site_id(init_app):
    """(a) Every existing devices row has non-null site_id after backfill."""
    from sqlalchemy import func, select
    from app.db import session_scope
    from app.models import Device

    with init_app.app_context():
        with session_scope() as session:
            null_count = session.scalar(
                select(func.count()).select_from(Device).where(Device.site_id.is_(None))
            ) or 0

            assert null_count == 0, (
                f"Found {null_count} device(s) with null site_id — backfill did not run"
            )


def test_audit_events_archive_table_exists(init_app):
    """(b) audit_events_archive table exists with correct shape."""
    from sqlalchemy import inspect
    from app.db import get_engine

    with init_app.app_context():
        engine = get_engine()
        inspector = inspect(engine)

        # Table exists
        assert inspector.has_table("audit_events_archive"), (
            "audit_events_archive table not found"
        )

        # Check columns match AuditEventArchive model
        columns = {c["name"]: c for c in inspector.get_columns("audit_events_archive")}
        expected_cols = {
            "id", "at", "actor_user_id", "actor_email_snapshot",
            "action", "target_type", "target_id", "details", "ip",
            "archived_at"  # P2 addition
        }
        assert expected_cols <= set(columns.keys()), (
            f"Missing columns in audit_events_archive. Expected: {expected_cols}, "
            f"Got: {set(columns.keys())}"
        )

        # archived_at should be NOT NULL
        archived_at_col = columns["archived_at"]
        assert not archived_at_col["nullable"], "archived_at should be NOT NULL"


def test_nightly_prune_job(init_app):
    """(c) Nightly prune job moves old audit_events to archive and removes source."""
    from sqlalchemy import text
    from app.db import session_scope
    from app.models import AuditEvent, AuditEventArchive
    from app.services import runtime_settings as rs
    from app.services.audit_prune import prune_old_audit_events

    with init_app.app_context():
        now = datetime.now(timezone.utc)
        old_timestamp = now - timedelta(days=2)  # 2 days old

        # Seed an old audit event
        old_event_id = None
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

        # Set retention to 1 day so our 2-day-old event is past the threshold
        original_retention = rs.get("system.audit_retention_days")
        rs.set_("system.audit_retention_days", 1)

        # Clear last-run guard so prune runs immediately
        rs.delete("system.audit_prune_last_run_date")

        try:
            # Run the prune job
            stats = prune_old_audit_events()

            # Should have archived at least our seeded event
            assert stats["archived"] >= 1, f"Expected ≥1 archived, got {stats['archived']}"
            assert stats["pruned"] >= 1, f"Expected ≥1 pruned, got {stats['pruned']}"
            assert not stats.get("errors"), f"Prune job had errors: {stats.get('errors')}"

            # Verify the event is gone from source
            with session_scope() as session:
                source_event = session.get(AuditEvent, old_event_id)
                assert source_event is None, "Old event still in audit_events after prune"

                # Verify it's in the archive
                archived_event = session.get(AuditEventArchive, old_event_id)
                assert archived_event is not None, "Old event not found in archive"
                assert archived_event.action == "test.prune_target"
                assert archived_event.archived_at is not None
                assert archived_event.archived_at > old_timestamp

        finally:
            # Restore original retention setting
            if original_retention is not None:
                rs.set_("system.audit_retention_days", original_retention)
            else:
                rs.delete("system.audit_retention_days")

            # Clean up archive row
            with session_scope() as session:
                if old_event_id:
                    session.execute(
                        text("DELETE FROM audit_events_archive WHERE id = :id"),
                        {"id": old_event_id}
                    )


def test_prune_date_rollover_guard(init_app):
    """Verify prune job only runs once per day via date-rollover guard."""
    from app.services import runtime_settings as rs
    from app.services.audit_prune import prune_old_audit_events

    with init_app.app_context():
        # Set retention low
        original_retention = rs.get("system.audit_retention_days")
        rs.set_("system.audit_retention_days", 1)

        # Set last-run to today
        today = datetime.now(timezone.utc).date().isoformat()
        rs.set_("system.audit_prune_last_run_date", today)

        try:
            # First call should skip
            stats = prune_old_audit_events()
            assert stats.get("skipped") is True, "Expected prune to skip on same-day re-run"
            assert stats["archived"] == 0
            assert stats["pruned"] == 0

        finally:
            # Restore
            if original_retention is not None:
                rs.set_("system.audit_retention_days", original_retention)
            else:
                rs.delete("system.audit_retention_days")
            rs.delete("system.audit_prune_last_run_date")
