"""v0.3.8 — failsafe-event surface (RFC-005 P1 backend).

Devices report B → C fallbacks via POST /api/v1/device/failsafe.
The endpoint records to `device_failsafe_events`; the Status
inbox surfaces them as critical attention items; the per-device
detail page shows a Failsafe history section.

Tests:
- POST /api/v1/device/failsafe with a valid device-token writes
  a row and returns 201.
- The failsafe shows up on the device-detail API + UI.
- The Status inbox surfaces the failsafe as a critical attention
  item with the right kind + the device link.
- Auth required: requests without a device token are rejected.
"""

from __future__ import annotations

import pytest
import requests

from .conftest import unique_suffix

# v0.5.79: in the `-m ci` gate (P-QA gate-2 widening).
pytestmark = pytest.mark.ci



@pytest.fixture(scope="module")
def shell_session(base_url, admin_creds):
    s = requests.Session()
    email, pw = admin_creds
    r = s.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    return s


def _enroll_device(shell_session, base_url, hint: str) -> dict:
    et = shell_session.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        json={"display_name_hint": hint, "note": "qa-failsafe"},
        timeout=10,
    ).json()["data"]["enrollment_token"]
    reg = requests.post(
        f"{base_url}/api/v1/device/register",
        json={
            "enrollment_token": et,
            "hardware_model": "sonoff_s31",
            "firmware_version": "0.1.0-qa",
            "display_name": hint,
        },
        timeout=10,
    )
    assert reg.status_code == 201
    return reg.json()["data"]


# ── endpoint contract ───────────────────────────────────────────────────

def test_failsafe_endpoint_records_event(base_url, shell_session):
    reg = _enroll_device(shell_session, base_url, f"QA failsafe {unique_suffix()}")
    try:
        r = requests.post(
            f"{base_url}/api/v1/device/failsafe",
            headers={"Authorization": f"Bearer {reg['device_token']}"},
            json={
                "device_id": reg["device_id"],
                "failed_version": "0.4.0-bad",
                "fallback_to_version": "0.3.9",
                "reason": "boot_failure",
                "details": {"watchdog_reset_count": 3, "uptime_s": 4},
            },
            timeout=10,
        )
        assert r.status_code == 201, r.text
        assert r.json()["data"]["reason"] == "boot_failure"
        assert r.json()["data"]["id"] is not None
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            timeout=10,
        )


def test_failsafe_requires_device_auth(base_url):
    """No bearer token → 401, no row written."""
    r = requests.post(
        f"{base_url}/api/v1/device/failsafe",
        json={
            "failed_version": "0.4.0",
            "fallback_to_version": "0.3.9",
            "reason": "boot_failure",
        },
        timeout=10,
    )
    assert r.status_code in (401, 403), r.status_code


def test_failsafe_unknown_reason_accepted_verbatim(base_url, shell_session):
    """Firmware can extend the reason vocabulary; we record what
    was sent rather than rejecting unknown values."""
    reg = _enroll_device(shell_session, base_url, f"QA failsafe-unk {unique_suffix()}")
    try:
        r = requests.post(
            f"{base_url}/api/v1/device/failsafe",
            headers={"Authorization": f"Bearer {reg['device_token']}"},
            json={
                "failed_version": "0.4.0",
                "fallback_to_version": "0.3.9",
                "reason": "future-vocab-not-yet-known",
                "details": {},
            },
            timeout=10,
        )
        assert r.status_code == 201
        # Verify the row exists with that reason.
        d = shell_session.get(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            timeout=10,
        ).json()["data"]
        assert any(
            f["reason"] == "future-vocab-not-yet-known"
            for f in d.get("failsafe_events", [])
        )
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            timeout=10,
        )


# ── per-device surface ───────────────────────────────────────────────────

def test_device_detail_returns_failsafe_events(base_url, shell_session):
    reg = _enroll_device(shell_session, base_url, f"QA fs-detail {unique_suffix()}")
    try:
        # Record two events.
        for v_failed, reason in (("0.4.0", "boot_failure"), ("0.4.1", "sha256_mismatch")):
            requests.post(
                f"{base_url}/api/v1/device/failsafe",
                headers={"Authorization": f"Bearer {reg['device_token']}"},
                json={
                    "failed_version": v_failed,
                    "fallback_to_version": "0.3.9",
                    "reason": reason,
                },
                timeout=10,
            )
        d = shell_session.get(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            timeout=10,
        ).json()["data"]
        events = d.get("failsafe_events") or []
        assert len(events) >= 2
        reasons = {e["reason"] for e in events}
        assert "boot_failure" in reasons
        assert "sha256_mismatch" in reasons
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            timeout=10,
        )


def test_device_detail_renders_failsafe_section(base_url, shell_session):
    reg = _enroll_device(shell_session, base_url, f"QA fs-ui {unique_suffix()}")
    try:
        requests.post(
            f"{base_url}/api/v1/device/failsafe",
            headers={"Authorization": f"Bearer {reg['device_token']}"},
            json={
                "failed_version": "0.4.0-bad",
                "fallback_to_version": "0.3.9",
                "reason": "boot_failure",
                "details": {"watchdog_reset_count": 3},
            },
            timeout=10,
        )
        body = shell_session.get(
            f"{base_url}/app/devices/{reg['device_id']}",
            timeout=10,
        ).text
        assert 'id="failsafe"' in body
        assert "Firmware failsafe events" in body
        assert "0.4.0-bad" in body
        assert "boot_failure" in body
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            timeout=10,
        )


# ── Status inbox surface ──────────────────────────────────────────────────

def test_failsafe_surfaces_on_status_inbox_as_critical(base_url, shell_session):
    reg = _enroll_device(shell_session, base_url, f"QA fs-inbox {unique_suffix()}")
    try:
        requests.post(
            f"{base_url}/api/v1/device/failsafe",
            headers={"Authorization": f"Bearer {reg['device_token']}"},
            json={
                "failed_version": "0.4.0-bad",
                "fallback_to_version": "0.3.9",
                "reason": "boot_failure",
            },
            timeout=10,
        )
        body = shell_session.get(f"{base_url}/app/", timeout=10).text
        # Item rendered with critical severity.
        assert "v3-sev-critical" in body
        # Title format from inbox.py.
        assert "Firmware failsafe" in body
        # Device link present.
        assert reg["device_id"] in body
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            timeout=10,
        )
