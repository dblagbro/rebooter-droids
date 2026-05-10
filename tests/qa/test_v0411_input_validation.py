"""v0.4.11 — input validation hardening (BUG-035, 036, 037).

Probes every unbounded text/number field surfaced during the
v0.4.10 iteration sweep. Each test sends an extreme value and
asserts the service returns 400, NOT 500 (column-overflow / runtime
state-machine breakage).
"""

from __future__ import annotations

import pytest
import requests

from .conftest import unique_suffix


# ── BUG-035 — rule numeric bounds ───────────────────────────────────


@pytest.mark.parametrize("field,value", [
    ("failure_threshold", -1),
    ("failure_threshold", 0),
    ("failure_threshold", 9999),
    ("recovery_threshold", -1),
    ("recovery_threshold", 0),
    ("recovery_threshold", 9999),
    ("window_seconds", 0),
    ("window_seconds", -100),
    ("window_seconds", 99_999_999),
    ("cooldown_seconds", -1),
    ("cooldown_seconds", 999_999_999),
])
def test_rule_numeric_bounds_rejected(base_url, admin_headers, field, value):
    body = {
        "name": f"qa-bound-{unique_suffix()}",
        "probe": {"kind": "internet"},
        "target": {"kind": "tag", "tag": "qa"},
        "action": {"kind": "notify_only"},
        field: value,
    }
    r = requests.post(
        f"{base_url}/api/v1/admin/rules",
        headers=admin_headers, json=body, timeout=10,
    )
    assert r.status_code == 400, f"{field}={value} → {r.status_code}: {r.text}"
    assert r.json()["error"]["code"] == "validation_failed"
    assert field in r.json()["error"]["message"]


# ── BUG-036 — name length bounds ─────────────────────────────────────


def test_rule_name_too_long_returns_400(base_url, admin_headers):
    long_name = "x" * 121
    r = requests.post(
        f"{base_url}/api/v1/admin/rules",
        headers=admin_headers,
        json={
            "name": long_name,
            "probe": {"kind": "internet"},
            "target": {"kind": "tag", "tag": "qa"},
            "action": {"kind": "notify_only"},
        },
        timeout=10,
    )
    assert r.status_code == 400, r.text
    assert "120 characters" in r.json()["error"]["message"]


def test_schedule_name_too_long_returns_400(base_url, admin_headers):
    long_name = "y" * 121
    r = requests.post(
        f"{base_url}/api/v1/admin/schedules",
        headers=admin_headers,
        json={
            "name": long_name,
            "kind": "power_cycle",
            "recurrence": "daily",
            "at_time_utc": "03:00",
            "target": {"kind": "tag", "tag": "qa"},
        },
        timeout=10,
    )
    assert r.status_code == 400, r.text
    assert "120 characters" in r.json()["error"]["message"]


# ── BUG-037 — maintenance reason cap ────────────────────────────────


def test_maintenance_reason_capped_at_200(base_url, admin_headers):
    """Long reason is silently truncated to 200 chars (197 + '...').
    Operator-visible behavior: the banner stays readable."""
    long_reason = "z" * 5000
    try:
        r = requests.post(
            f"{base_url}/api/v1/admin/maintenance",
            headers=admin_headers,
            json={"on": True, "reason": long_reason},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        stored = r.json()["data"]["reason"]
        assert stored is not None
        assert len(stored) <= 200
        # Truncation artifact present
        assert stored.endswith("...")
    finally:
        # Always clear the flag back to off
        requests.post(
            f"{base_url}/api/v1/admin/maintenance",
            headers=admin_headers,
            json={"on": False},
            timeout=10,
        )
