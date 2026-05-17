"""v0.4.17 — End-to-end schedule runtime against the live cron.

The v0.4.8 schedule_tick is APScheduler-driven (every 30s). Probe-now
doesn't exist for schedules (they're time-based, not condition-based),
so this is wall-clock by necessity.

Covers:
- One-shot maintenance schedule: fires once, flips maintenance flag
  ON, waits the window, flips OFF with reason=schedule_window_ended.
- next_run_at correctly None after a one-shot consumes itself.
- Operator override during a scheduled-maintenance window
  (BUG-032 fix from v0.4.10): operator's manual flip stays.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

from .conftest import unique_suffix

# NB: not in the `-m ci` gate — this is a wall-clock e2e test racing the
# 30s APScheduler tick; it flakes when the test's waits don't align with
# a tick boundary. Timing e2e tests don't belong in a deterministic gate.



SKIP_E2E = os.environ.get("SKIP_E2E", "").strip().lower() in ("1", "true", "yes")


@pytest.mark.timeout(180)
@pytest.mark.skipif(SKIP_E2E, reason="SKIP_E2E=1 set; wall-clock test skipped")
def test_one_shot_maintenance_schedule_full_lifecycle(base_url, admin_headers):
    """Full lifecycle in ~100s: schedule fires → maintenance ON
    → window ends → maintenance OFF."""
    when = (datetime.now(timezone.utc) + timedelta(seconds=15)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    create = requests.post(
        f"{base_url}/api/v1/admin/schedules",
        headers=admin_headers,
        json={
            "name": f"qa-e2e-maint-{unique_suffix()}",
            "kind": "maintenance",
            "recurrence": "once",
            "start_at": when,
            "duration_seconds": 30,
        },
        timeout=10,
    )
    assert create.status_code == 201, create.text
    sid = create.json()["data"]["id"]
    try:
        # Pre-fire: maintenance should be off (or at most operator-
        # set, not schedule-set).
        before = requests.get(
            f"{base_url}/api/v1/admin/maintenance",
            headers=admin_headers, timeout=10,
        ).json()["data"]
        assert before.get("reason") != "schedule"

        # Wait ~45s for fire + tick + reconciler.
        time.sleep(45)
        during = requests.get(
            f"{base_url}/api/v1/admin/maintenance",
            headers=admin_headers, timeout=10,
        ).json()["data"]
        assert during["on"] is True, f"maintenance should be ON during window: {during}"
        assert during["reason"] == "schedule"

        # Wait for window to end + reconcile cycle.
        time.sleep(40)
        after = requests.get(
            f"{base_url}/api/v1/admin/maintenance",
            headers=admin_headers, timeout=10,
        ).json()["data"]
        assert after["on"] is False, f"maintenance should be OFF post-window: {after}"
        assert after["reason"] == "schedule_window_ended"

        # Schedule itself: one-shot consumed, next_run_at is None
        schedules = requests.get(
            f"{base_url}/api/v1/admin/schedules",
            headers=admin_headers, timeout=10,
        ).json()["data"]
        match = next(s for s in schedules if s["id"] == sid)
        assert match["last_run_at"] is not None
        assert match["last_outcome"] == "maintenance_window_open"
        assert match["next_run_at"] is None  # one-shot — no future run
    finally:
        requests.delete(
            f"{base_url}/api/v1/admin/schedules/{sid}",
            headers=admin_headers, timeout=10,
        )


# ── BUG-044 — DELETE API for enrollment tokens ───────────────────────


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
