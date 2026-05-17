"""v0.4.17 — schedule runtime + enrollment-token DELETE API.

The schedule-runtime test was a wall-clock e2e (~100 s of sleeps
racing the 30 s APScheduler tick); rewritten v0.5.82 (P-QA gate-3) to
drive `schedule_runtime.tick()` in-process with an injected `now`
against an isolated SQLite DB — deterministic, no sleeps.

The two enrollment-token DELETE tests stay HTTP — they're a plain API
surface, not timing-dependent.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import flask
import pytest
import requests

from app.config import load_settings
from app.db import get_engine, init_engine, session_scope
from app.models import Base, Schedule

from .conftest import unique_suffix

# v0.5.82: in the `-m ci` gate (P-QA gate-3 — timing e2e, now in-process).
pytestmark = pytest.mark.ci

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def hub_db(tmp_path):
    """Isolated SQLite hub DB + a bare Flask app context. Mirrors
    test_v0514 / test_v0414."""
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


def test_one_shot_maintenance_schedule_full_lifecycle(hub_db):
    """A one-shot maintenance schedule: fires once → portal maintenance
    flips ON (reason=schedule) for the window → flips OFF afterward
    (reason=schedule_window_ended). One-shot consumes itself."""
    from app.services import runtime_flags, schedule_runtime
    from app.services.schedules import create as create_schedule

    sched = create_schedule(
        name=f"qa-e2e-maint-{unique_suffix()}",
        kind="maintenance",
        recurrence="once",
        start_at=T0 + timedelta(seconds=15),
        duration_seconds=30,
    )
    sid = sched["id"]

    # Tick before start_at — schedule arms its next_run_at, fires nothing.
    schedule_runtime.tick(now=T0)
    assert runtime_flags.is_maintenance_mode_active() is False

    # Tick after start_at — schedule fires; the window covers `now`.
    schedule_runtime.tick(now=T0 + timedelta(seconds=20))
    assert runtime_flags.is_maintenance_mode_active() is True
    assert runtime_flags.maintenance_mode_details().get("reason") == "schedule"

    # Tick past the window end (fired ~T0+20, duration 30 → ends ~T0+50).
    schedule_runtime.tick(now=T0 + timedelta(seconds=120))
    assert runtime_flags.is_maintenance_mode_active() is False
    assert (
        runtime_flags.maintenance_mode_details().get("reason")
        == "schedule_window_ended"
    )

    # The one-shot has consumed itself.
    with session_scope() as session:
        row = session.get(Schedule, sid)
        assert row.last_run_at is not None
        assert row.last_outcome == "maintenance_window_open"
        assert row.next_run_at is None


# ── BUG-044 — DELETE API for enrollment tokens (HTTP, not timing) ────


def test_enrollment_token_delete_api_works(base_url, admin_headers):
    """v0.4.17 added DELETE /api/v1/admin/enrollment-tokens/<id>.
    Pre-fix only the UI POST /app/.../revoke existed."""
    create = requests.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        headers=admin_headers,
        json={
            "display_name_hint": f"qa-bug044-{unique_suffix()}",
            "note": "test for BUG-044",
        },
        timeout=10,
    )
    assert create.status_code == 201
    tid = create.json()["data"]["id"]

    delete = requests.delete(
        f"{base_url}/api/v1/admin/enrollment-tokens/{tid}",
        headers=admin_headers, timeout=10,
    )
    assert delete.status_code == 200, delete.text
    assert delete.json()["data"]["deleted"] is True

    # Already-deleted → 404
    delete2 = requests.delete(
        f"{base_url}/api/v1/admin/enrollment-tokens/{tid}",
        headers=admin_headers, timeout=10,
    )
    assert delete2.status_code == 404


def test_enrollment_token_delete_unknown_returns_404(base_url, admin_headers):
    delete = requests.delete(
        f"{base_url}/api/v1/admin/enrollment-tokens/et_does_not_exist",
        headers=admin_headers, timeout=10,
    )
    assert delete.status_code == 404
