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

# v0.5.80: in the `-m ci` gate (P-QA gate-3 partial-fail bucket).
pytestmark = pytest.mark.ci



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


# ── BUG-050 / BUG-051 — device register payload validation ─────────


def _mint_token(base_url, admin_headers):
    r = requests.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        headers=admin_headers,
        json={"display_name_hint": f"qa-reg-test-{unique_suffix()}", "ttl_seconds": 600},
        timeout=10,
    )
    return r.json()["data"]["enrollment_token"]


@pytest.mark.parametrize("field,value,expect_msg", [
    ("display_name", "x" * 121, "120"),
    ("hardware_model", "y" * 81, "80"),
    ("firmware_version", "z" * 41, "40"),
    ("mac_address", "a" * 41, "40"),
    ("local_ip", "1" * 65, "64"),
])
def test_device_register_rejects_overlong_field(base_url, admin_headers, field, value, expect_msg):
    """v0.4.18 (BUG-050) — caller-supplied fields longer than the
    column width return 400, not the pre-fix 500."""
    tok = _mint_token(base_url, admin_headers)
    payload = {"enrollment_token": tok, "mac_address": "AA:BB:CC:DD:EE:FF"}
    payload[field] = value
    r = requests.post(
        f"{base_url}/api/v1/device/register",
        json=payload, timeout=10,
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation_failed"
    assert expect_msg in r.json()["error"]["message"]


@pytest.mark.parametrize("mac", [
    "<script>alert(1)</script>",
    "totally not a mac",
    "AA:BB::ZZ::QQ::WW",  # contains Z which isn't hex
])
def test_device_register_rejects_garbage_mac(base_url, admin_headers, mac):
    """v0.4.18 (BUG-051) — MAC must be hex-shaped. Pre-fix the
    register accepted any string, persisted it verbatim, and
    operators saw nonsense in the MAC column."""
    tok = _mint_token(base_url, admin_headers)
    r = requests.post(
        f"{base_url}/api/v1/device/register",
        json={"enrollment_token": tok, "mac_address": mac},
        timeout=10,
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation_failed"
    assert "mac_address" in r.json()["error"]["message"]


# ── BUG-038 — rule target requires concrete id ───────────────────────


@pytest.mark.parametrize("target,expect_field", [
    ({"kind": "device"}, "target.id"),
    ({"kind": "device", "id": ""}, "target.id"),
    ({"kind": "group"}, "target.id"),
    ({"kind": "tag"}, "target.tag"),
    ({"kind": "tag", "tag": ""}, "target.tag"),
])
def test_rule_target_requires_concrete_id(base_url, admin_headers, target, expect_field):
    r = requests.post(
        f"{base_url}/api/v1/admin/rules",
        headers=admin_headers,
        json={
            "name": f"qa-target-{unique_suffix()}",
            "probe": {"kind": "internet"},
            "target": target,
            "action": {"kind": "notify_only"},
        },
        timeout=10,
    )
    assert r.status_code == 400, r.text
    assert expect_field in r.json()["error"]["message"]


# ── BUG-040 / BUG-041 — schedule weekday hygiene ───────────────────


def test_schedule_weekly_dedups_duplicate_weekdays(base_url, admin_headers):
    """Duplicates → silently deduped + sorted. Avoids 'Sat, Sat, Sat'."""
    r = requests.post(
        f"{base_url}/api/v1/admin/schedules",
        headers=admin_headers,
        json={
            "name": f"qa-dups-{unique_suffix()}",
            "kind": "power_cycle",
            "recurrence": "weekly",
            "at_time_utc": "03:00",
            "weekdays": [5, 5, 5, 1, 1],
            "target": {"kind": "tag", "tag": "qa"},
        },
        timeout=10,
    )
    assert r.status_code == 201, r.text
    sid = r.json()["data"]["id"]
    try:
        weekdays = r.json()["data"]["weekdays"]
        assert weekdays == [1, 5], weekdays
        # Sentence should not contain double-Sat/Tue
        sent = r.json()["data"]["sentence"]
        assert sent.count("Tue") == 1, sent
        assert sent.count("Sat") == 1, sent
    finally:
        requests.delete(
            f"{base_url}/api/v1/admin/schedules/{sid}",
            headers=admin_headers, timeout=10,
        )


@pytest.mark.parametrize("weekdays", [[7], [99], [-1], [3, 99]])
def test_schedule_weekly_rejects_out_of_range_weekdays(base_url, admin_headers, weekdays):
    r = requests.post(
        f"{base_url}/api/v1/admin/schedules",
        headers=admin_headers,
        json={
            "name": f"qa-bad-wd-{unique_suffix()}",
            "kind": "power_cycle",
            "recurrence": "weekly",
            "at_time_utc": "03:00",
            "weekdays": weekdays,
            "target": {"kind": "tag", "tag": "qa"},
        },
        timeout=10,
    )
    assert r.status_code == 400, r.text
    assert "weekdays" in r.json()["error"]["message"]


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
