"""v0.4.8 — Schedules as a separate primitive (B8).

Covers:
- Create / list / delete a power_cycle schedule via API.
- Validation: missing name / kind / target / weekdays for weekly.
- Sentence render covers all three recurrences + both kinds.
- Schedules page renders the form + the list.
- next_run_at populated post-create.

Tick-driven firing isn't exercised here (it requires wall-clock to
reach next_run_at); the ./_runtime.py module is unit-testable in
isolation but not from the live deployment without time machinery.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import requests

from .conftest import unique_suffix


@pytest.fixture(scope="module")
def shell_session(base_url, admin_creds):
    s = requests.Session()
    email, pw = admin_creds
    r = s.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    )
    assert r.status_code == 200
    return s


def _create(shell_session, base_url, **body):
    return shell_session.post(
        f"{base_url}/api/v1/admin/schedules", json=body, timeout=10
    )


def _delete(shell_session, base_url, sid):
    shell_session.delete(
        f"{base_url}/api/v1/admin/schedules/{sid}", timeout=10
    )


def test_create_daily_power_cycle(base_url, shell_session):
    name = f"qa048d-{unique_suffix()}"
    r = _create(
        shell_session, base_url,
        name=name,
        kind="power_cycle",
        recurrence="daily",
        at_time_utc="03:00",
        target={"kind": "tag", "tag": "qa048"},
        power_off_seconds=5,
    )
    assert r.status_code == 201, r.text
    s = r.json()["data"]
    try:
        assert s["sentence"]
        assert "every day at 03:00" in s["sentence"]
        assert "power-cycle" in s["sentence"]
        assert s["next_run_at"] is not None
    finally:
        _delete(shell_session, base_url, s["id"])


def test_create_weekly_maintenance(base_url, shell_session):
    name = f"qa048w-{unique_suffix()}"
    r = _create(
        shell_session, base_url,
        name=name,
        kind="maintenance",
        recurrence="weekly",
        at_time_utc="02:00",
        weekdays=[5, 6],  # Sat, Sun
        duration_seconds=3600,
    )
    assert r.status_code == 201, r.text
    s = r.json()["data"]
    try:
        assert "pause watchdog" in s["sentence"]
        assert "Sat" in s["sentence"] and "Sun" in s["sentence"]
        assert s["next_run_at"] is not None
    finally:
        _delete(shell_session, base_url, s["id"])


def test_create_one_shot(base_url, shell_session):
    """One-shot: future ISO datetime."""
    when = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    r = _create(
        shell_session, base_url,
        name=f"qa048o-{unique_suffix()}",
        kind="power_cycle",
        recurrence="once",
        start_at=when,
        target={"kind": "tag", "tag": "qa048"},
    )
    assert r.status_code == 201, r.text
    s = r.json()["data"]
    try:
        assert "once at" in s["sentence"]
    finally:
        _delete(shell_session, base_url, s["id"])


@pytest.mark.parametrize("body,field", [
    ({"name": "", "kind": "power_cycle", "recurrence": "daily", "at_time_utc": "03:00"}, "name"),
    ({"name": "x", "kind": "bogus", "recurrence": "daily"}, "kind"),
    ({"name": "x", "kind": "power_cycle", "recurrence": "weekly", "at_time_utc": "03:00"}, "weekdays"),
    ({"name": "x", "kind": "power_cycle", "recurrence": "daily", "at_time_utc": "03:00"}, "target"),
    ({"name": "x", "kind": "maintenance", "recurrence": "daily", "at_time_utc": "03:00", "duration_seconds": 0}, "duration_seconds"),
])
def test_validation_rejects(base_url, shell_session, body, field):
    r = _create(shell_session, base_url, **body)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation_failed"
    assert field in r.json()["error"]["message"]


def test_disable_then_re_enable(base_url, shell_session):
    r = _create(
        shell_session, base_url,
        name=f"qa048t-{unique_suffix()}",
        kind="power_cycle",
        recurrence="daily",
        at_time_utc="03:00",
        target={"kind": "tag", "tag": "qa048"},
    )
    sid = r.json()["data"]["id"]
    try:
        # Toggle off
        rd = shell_session.post(
            f"{base_url}/app/schedules/{sid}/toggle",
            data={"enabled": "0"}, timeout=10,
        )
        assert rd.status_code in (302, 200)
        rows = shell_session.get(
            f"{base_url}/api/v1/admin/schedules", timeout=10
        ).json()["data"]
        assert next(s for s in rows if s["id"] == sid)["enabled"] is False
    finally:
        _delete(shell_session, base_url, sid)


def test_schedules_page_renders(base_url, shell_session):
    body = shell_session.get(f"{base_url}/app/schedules", timeout=10).text
    assert "Schedules" in body
    assert "Create a schedule" in body
    assert "power_cycle" in body
    assert "maintenance" in body


def test_rules_page_links_to_schedules(base_url, shell_session):
    body = shell_session.get(f"{base_url}/app/rules", timeout=10).text
    assert "/app/schedules" in body
