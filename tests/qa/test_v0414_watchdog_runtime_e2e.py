"""v0.4.14 — End-to-end watchdog runtime against the live cron.

Probe-now (v0.4.2) is synchronous and bypasses the state machine.
This file actually waits for the APScheduler tick to do its job:

  1. Create a rule with failure_threshold=1, window_seconds=10,
     cooldown_seconds=30, probe = TCP to 127.0.0.1:1 (always
     refused on the hub host).
  2. Wait ~25 s for two-three ticks.
  3. Assert at least one `failure` event.
  4. Assert at least one `action_fired` event (since
     failure_threshold=1).
  5. Assert at least one `cooldown_skip` event (subsequent
     failures within cooldown_seconds=30 should record but not
     re-fire).
  6. Assert serialized rule exposes runtime state: failure_streak,
     last_outcome, last_action_at populated.

The test is wall-clock (~30s); marked timeout(120). Skip with
SKIP_E2E=1 in CI environments without enough budget.
"""

from __future__ import annotations

import os
import time

import pytest
import requests

from .conftest import unique_suffix


SKIP_E2E = os.environ.get("SKIP_E2E", "").strip().lower() in ("1", "true", "yes")
WAIT_SECONDS = 25  # 2-3 ticks at 10s cadence + buffer


@pytest.mark.timeout(120)
@pytest.mark.skipif(SKIP_E2E, reason="SKIP_E2E=1 set; wall-clock test skipped")
def test_failing_probe_fires_action_then_cooldown_skips(
    base_url, admin_headers,
):
    """Full state-machine round-trip against the real cron tick."""
    name = f"qa-e2e-{unique_suffix()}"
    create = requests.post(
        f"{base_url}/api/v1/admin/rules",
        headers=admin_headers,
        json={
            "name": name,
            "probe": {"kind": "tcp", "host": "127.0.0.1", "port": 1},
            "target": {"kind": "tag", "tag": "qa-e2e"},
            "action": {"kind": "notify_only"},
            "failure_threshold": 1,
            "recovery_threshold": 1,
            "window_seconds": 10,
            "cooldown_seconds": 30,
        },
        timeout=10,
    )
    assert create.status_code == 201, create.text
    rid = create.json()["data"]["id"]
    try:
        time.sleep(WAIT_SECONDS)

        # ── events ──────────────────────────────────────────────
        events = requests.get(
            f"{base_url}/api/v1/admin/rules/{rid}/events",
            headers=admin_headers, timeout=10,
        ).json()["data"]
        outcomes = [e["outcome"] for e in events]

        assert "failure" in outcomes, (
            f"expected at least one failure event after {WAIT_SECONDS}s; "
            f"got outcomes={outcomes}"
        )
        assert "action_fired" in outcomes, (
            f"expected action_fired after failure_threshold=1 was crossed; "
            f"got outcomes={outcomes}"
        )
        assert "cooldown_skip" in outcomes, (
            f"expected cooldown_skip on subsequent failures within "
            f"cooldown_seconds=30; got outcomes={outcomes}"
        )

        # ── runtime state on serialized rule ────────────────────
        rules = requests.get(
            f"{base_url}/api/v1/admin/rules",
            headers=admin_headers, timeout=10,
        ).json()["data"]
        rule = next(r for r in rules if r["id"] == rid)

        # BUG-042: these keys must exist in the response.
        assert "failure_streak" in rule
        assert "last_outcome" in rule
        assert "last_action_at" in rule
        assert "last_probed_at" in rule

        # And they should reflect what we just observed.
        assert rule["last_outcome"] is not None
        assert rule["last_probed_at"] is not None
        assert rule["last_action_at"] is not None
        assert rule["failure_streak"] >= 1
        assert rule["status"] == "firing"
    finally:
        requests.delete(
            f"{base_url}/api/v1/admin/rules/{rid}",
            headers=admin_headers, timeout=10,
        )


@pytest.mark.timeout(120)
@pytest.mark.skipif(SKIP_E2E, reason="SKIP_E2E=1 set; wall-clock test skipped")
def test_succeeding_probe_keeps_rule_armed(base_url, admin_headers):
    """Symmetric test — a probe that succeeds should never fire
    the action and should leave the rule in 'armed' state."""
    name = f"qa-e2e-ok-{unique_suffix()}"
    create = requests.post(
        f"{base_url}/api/v1/admin/rules",
        headers=admin_headers,
        json={
            "name": name,
            # HTTP probe to ourselves — guaranteed 200.
            "probe": {"kind": "http", "url": f"{base_url}/api/v1/version"},
            "target": {"kind": "tag", "tag": "qa-e2e"},
            "action": {"kind": "cycle", "power_off_seconds": 5},
            "failure_threshold": 1,
            "recovery_threshold": 1,
            "window_seconds": 10,
            "cooldown_seconds": 30,
        },
        timeout=10,
    )
    rid = create.json()["data"]["id"]
    try:
        time.sleep(WAIT_SECONDS)
        events = requests.get(
            f"{base_url}/api/v1/admin/rules/{rid}/events",
            headers=admin_headers, timeout=10,
        ).json()["data"]
        outcomes = [e["outcome"] for e in events]

        assert "success" in outcomes
        assert "action_fired" not in outcomes
        assert "failure" not in outcomes

        rules = requests.get(
            f"{base_url}/api/v1/admin/rules",
            headers=admin_headers, timeout=10,
        ).json()["data"]
        rule = next(r for r in rules if r["id"] == rid)
        assert rule["status"] == "armed"
        assert rule["failure_streak"] == 0
    finally:
        requests.delete(
            f"{base_url}/api/v1/admin/rules/{rid}",
            headers=admin_headers, timeout=10,
        )


@pytest.mark.timeout(120)
@pytest.mark.skipif(SKIP_E2E, reason="SKIP_E2E=1 set; wall-clock test skipped")
def test_maintenance_window_suppresses_runtime_action(
    base_url, admin_headers,
):
    """A failing probe DURING a maintenance window must record
    `maintenance_skip` events instead of `failure` / `action_fired`.
    (v0.4.7 + this e2e test.)"""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    # Window covers the full 25s wait: from 5s ago to 60s from now.
    start = (now - timedelta(seconds=5)).isoformat()
    end = (now + timedelta(seconds=60)).isoformat()
    name = f"qa-e2e-maint-{unique_suffix()}"
    create = requests.post(
        f"{base_url}/api/v1/admin/rules",
        headers=admin_headers,
        json={
            "name": name,
            "probe": {"kind": "tcp", "host": "127.0.0.1", "port": 1},
            "target": {"kind": "tag", "tag": "qa-e2e"},
            "action": {"kind": "notify_only"},
            "failure_threshold": 1,
            "recovery_threshold": 1,
            "window_seconds": 10,
            "cooldown_seconds": 30,
            "maintenance_windows": [{"start": start, "end": end}],
        },
        timeout=10,
    )
    rid = create.json()["data"]["id"]
    try:
        time.sleep(WAIT_SECONDS)
        events = requests.get(
            f"{base_url}/api/v1/admin/rules/{rid}/events",
            headers=admin_headers, timeout=10,
        ).json()["data"]
        outcomes = [e["outcome"] for e in events]
        assert "maintenance_skip" in outcomes, (
            f"expected at least one maintenance_skip; got {outcomes}"
        )
        assert "action_fired" not in outcomes, (
            f"action MUST NOT fire during maintenance; got {outcomes}"
        )
    finally:
        requests.delete(
            f"{base_url}/api/v1/admin/rules/{rid}",
            headers=admin_headers, timeout=10,
        )
