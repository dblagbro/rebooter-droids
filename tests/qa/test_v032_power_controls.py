"""v0.3.2 P3 — Power controls + safety confirmations + lockout flag.

Asserts:
- is_protected serialised on every device row.
- PATCH /api/v1/admin/devices/<id> accepts is_protected and the
  toggle round-trips.
- Power command against a protected device returns 423 Locked.
- override_lockout=true unblocks the protected device.
- hold_off=1 sets is_held_off; subsequent relay_on clears it.
- cancel-pending API flips a pending command to cancelled.
- Audit rows on power commands carry details.reason='operator'.
- Device-detail UI renders the lockout banner when protected and
  the held-off banner when held off.
"""

from __future__ import annotations

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
    assert r.status_code == 200, r.text
    return s


def _enroll_device(shell_session, base_url, hint: str) -> dict:
    et = shell_session.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        json={"display_name_hint": hint, "note": "qa-power-controls"},
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
    assert reg.status_code == 201, reg.text
    return reg.json()["data"]


def _delete(shell_session, base_url, dev_id: str) -> None:
    shell_session.delete(
        f"{base_url}/api/v1/admin/devices/{dev_id}", timeout=10
    )


# ── is_protected serialisation + toggle ───────────────────────────────────

def test_devices_serialiser_includes_is_protected(base_url, shell_session):
    devs = shell_session.get(
        f"{base_url}/api/v1/admin/devices?show_qa_fixtures=1",
        timeout=10,
    ).json()["data"]["devices"]
    for d in devs:
        assert "is_protected" in d, f"missing is_protected on {d['id']}"
        assert isinstance(d["is_protected"], bool)
        assert "is_held_off" in d
        assert isinstance(d["is_held_off"], bool)


def test_patch_device_toggles_is_protected(base_url, shell_session):
    reg = _enroll_device(shell_session, base_url, f"QA protect {unique_suffix()}")
    try:
        # Set protected
        r = shell_session.patch(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            json={"is_protected": True},
            timeout=10,
        )
        assert r.status_code == 200
        assert r.json()["data"]["is_protected"] is True

        # Clear it
        r = shell_session.patch(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            json={"is_protected": False},
            timeout=10,
        )
        assert r.status_code == 200
        assert r.json()["data"]["is_protected"] is False
    finally:
        _delete(shell_session, base_url, reg["device_id"])


# ── lockout enforcement ───────────────────────────────────────────────────

def test_protected_device_blocks_power_command_with_423(base_url, shell_session):
    reg = _enroll_device(shell_session, base_url, f"QA blk {unique_suffix()}")
    try:
        shell_session.patch(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            json={"is_protected": True},
            timeout=10,
        )
        r = shell_session.post(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}/commands",
            json={"type": "relay_off"},
            timeout=10,
        )
        assert r.status_code == 423, r.text
        assert r.json()["error"]["code"] == "device_locked"
    finally:
        _delete(shell_session, base_url, reg["device_id"])


def test_override_lockout_unblocks_protected_device(base_url, shell_session):
    reg = _enroll_device(shell_session, base_url, f"QA ovr {unique_suffix()}")
    try:
        shell_session.patch(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            json={"is_protected": True},
            timeout=10,
        )
        r = shell_session.post(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}/commands",
            json={"type": "relay_off", "override_lockout": True},
            timeout=10,
        )
        assert r.status_code == 201, r.text
    finally:
        _delete(shell_session, base_url, reg["device_id"])


def test_non_power_command_ignores_lockout(base_url, shell_session):
    """Non-power commands (e.g., check_firmware, set_mode, apply_config)
    are NOT gated by is_protected — only physical-power-affecting
    commands are."""
    reg = _enroll_device(shell_session, base_url, f"QA nonpw {unique_suffix()}")
    try:
        shell_session.patch(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            json={"is_protected": True},
            timeout=10,
        )
        r = shell_session.post(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}/commands",
            json={"type": "check_firmware"},
            timeout=10,
        )
        assert r.status_code == 201, r.text
    finally:
        _delete(shell_session, base_url, reg["device_id"])


# ── hold-off ──────────────────────────────────────────────────────────────

def test_hold_off_sets_flag_and_relay_on_clears_it(base_url, shell_session):
    reg = _enroll_device(shell_session, base_url, f"QA hold {unique_suffix()}")
    try:
        # relay_off with hold_off=1
        r = shell_session.post(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}/commands",
            json={"type": "relay_off", "hold_off": True},
            timeout=10,
        )
        assert r.status_code == 201

        # is_held_off must now be True
        d = shell_session.get(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            timeout=10,
        ).json()["data"]
        assert d["is_held_off"] is True

        # Issue relay_on — should clear the flag.
        r = shell_session.post(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}/commands",
            json={"type": "relay_on"},
            timeout=10,
        )
        assert r.status_code == 201

        d = shell_session.get(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            timeout=10,
        ).json()["data"]
        assert d["is_held_off"] is False
    finally:
        _delete(shell_session, base_url, reg["device_id"])


# ── cancel-pending ────────────────────────────────────────────────────────

def test_cancel_pending_command_flips_status(base_url, shell_session):
    reg = _enroll_device(shell_session, base_url, f"QA cncl {unique_suffix()}")
    try:
        # Issue a command. Don't poll for it from the device side, so it
        # stays in `pending` rather than being marked `accepted`.
        cmd = shell_session.post(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}/commands",
            json={"type": "relay_off"},
            timeout=10,
        ).json()["data"]
        cmd_id = cmd["command_id"]

        r = shell_session.post(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}"
            f"/commands/{cmd_id}/cancel",
            timeout=10,
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["cancelled"] is True
    finally:
        _delete(shell_session, base_url, reg["device_id"])


def test_cancel_already_accepted_command_returns_409(base_url, shell_session):
    """If the device has polled and accepted the command, cancel must
    fail with 409 — we can't recall a delivered command."""
    reg = _enroll_device(shell_session, base_url, f"QA cncl409 {unique_suffix()}")
    try:
        cmd = shell_session.post(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}/commands",
            json={"type": "check_firmware"},
            timeout=10,
        ).json()["data"]
        cmd_id = cmd["command_id"]

        # Simulate device-poll: this marks pending → accepted.
        device_token = reg["device_token"]
        requests.get(
            f"{base_url}/api/v1/device/commands",
            headers={"Authorization": f"Bearer {device_token}"},
            timeout=10,
        )

        r = shell_session.post(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}"
            f"/commands/{cmd_id}/cancel",
            timeout=10,
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "not_cancellable"
    finally:
        _delete(shell_session, base_url, reg["device_id"])


# ── audit reason field ────────────────────────────────────────────────────

def test_power_command_audit_carries_reason_operator(base_url, shell_session):
    reg = _enroll_device(shell_session, base_url, f"QA reason {unique_suffix()}")
    try:
        shell_session.post(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}/commands",
            json={"type": "relay_on"},
            timeout=10,
        )
        rows = shell_session.get(
            f"{base_url}/api/v1/admin/audit"
            f"?target_type=device&target_id={reg['device_id']}"
            f"&action=device.command_issued",
            timeout=10,
        ).json()["data"]["events"]
        assert rows, "expected at least one device.command_issued audit row"
        for r in rows:
            assert r["details"].get("reason") == "operator", r
    finally:
        _delete(shell_session, base_url, reg["device_id"])


# ── UI rendering ──────────────────────────────────────────────────────────

def test_protected_device_renders_lockout_banner(base_url, shell_session):
    reg = _enroll_device(shell_session, base_url, f"QA UI-lock {unique_suffix()}")
    try:
        shell_session.patch(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            json={"is_protected": True},
            timeout=10,
        )
        body = shell_session.get(
            f"{base_url}/app/devices/{reg['device_id']}", timeout=10
        ).text
        assert "v3-lockout-banner" in body
        assert "🔒 protected" in body
    finally:
        _delete(shell_session, base_url, reg["device_id"])


def test_held_off_device_renders_holdoff_banner(base_url, shell_session):
    reg = _enroll_device(shell_session, base_url, f"QA UI-hold {unique_suffix()}")
    try:
        shell_session.post(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}/commands",
            json={"type": "relay_off", "hold_off": True},
            timeout=10,
        )
        body = shell_session.get(
            f"{base_url}/app/devices/{reg['device_id']}", timeout=10
        ).text
        assert "v3-holdoff-banner" in body
        assert "held off" in body.lower()
    finally:
        _delete(shell_session, base_url, reg["device_id"])
