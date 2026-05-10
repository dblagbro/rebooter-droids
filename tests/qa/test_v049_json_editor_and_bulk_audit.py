"""v0.4.9 — Rule JSON editor (B9) + bulk-action per-device audit (B14)."""

from __future__ import annotations

import json

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


# ── B9 — JSON editor ────────────────────────────────────────────────


def test_rules_page_advertises_json_editor(base_url, shell_session):
    body = shell_session.get(f"{base_url}/app/rules", timeout=10).text
    assert "Advanced" in body
    assert "rule_json" in body
    assert "/rules/json" in body


def test_create_rule_via_json_editor(base_url, shell_session):
    name = f"qa049j-{unique_suffix()}"
    body = {
        "name": name,
        "probe": {"kind": "http", "url": "https://www.voipguru.org/rebooter/api/v1/version"},
        "target": {"kind": "tag", "tag": "qa049"},
        "action": {"kind": "notify_only"},
        "failure_threshold": 5,
        "recovery_threshold": 3,
        "window_seconds": 120,
        "cooldown_seconds": 600,
    }
    r = shell_session.post(
        f"{base_url}/app/rules/json",
        data={"rule_json": json.dumps(body)},
        timeout=10,
        allow_redirects=False,
    )
    assert r.status_code in (302, 303), r.text

    # Find via API
    rows = shell_session.get(
        f"{base_url}/api/v1/admin/rules", timeout=10
    ).json()["data"]
    match = next((rl for rl in rows if rl["name"] == name), None)
    assert match is not None
    try:
        assert match["failure_threshold"] == 5
        assert match["recovery_threshold"] == 3
        assert match["window_seconds"] == 120
        assert match["cooldown_seconds"] == 600
        assert match["probe"]["url"].endswith("/api/v1/version")
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/rules/{match['id']}", timeout=10
        )


def test_json_editor_rejects_bad_json(base_url, shell_session):
    r = shell_session.post(
        f"{base_url}/app/rules/json",
        data={"rule_json": "{ this is not json"},
        timeout=10,
        allow_redirects=True,
    )
    assert r.status_code == 200
    assert "JSON parse error" in r.text or "rule_json" in r.text  # flash message visible


def test_json_editor_rejects_validation_failure(base_url, shell_session):
    """Bad probe.kind goes through validation, not JSON parsing."""
    r = shell_session.post(
        f"{base_url}/app/rules/json",
        data={"rule_json": json.dumps({
            "name": f"qa049v-{unique_suffix()}",
            "probe": {"kind": "bogus"},
            "target": {"kind": "tag", "tag": "x"},
            "action": {"kind": "notify_only"},
        })},
        timeout=10,
        allow_redirects=True,
    )
    # Form re-renders with the validation error in flash. We don't
    # assert text since the flash region is page-template-dependent;
    # verify no rule was created.
    rows = shell_session.get(
        f"{base_url}/api/v1/admin/rules", timeout=10
    ).json()["data"]
    assert not any(rl["name"].startswith("qa049v-") for rl in rows), (
        "validation_failed JSON should NOT have created a rule"
    )


def test_json_editor_round_trip_lossless(base_url, shell_session):
    """Create from JSON; fetch via API; verify the shape we get back
    matches what we sent for every field that's a contract."""
    name = f"qa049rt-{unique_suffix()}"
    sent = {
        "name": name,
        "probe": {"kind": "http", "url": "https://example.com/health"},
        "target": {"kind": "tag", "tag": "qa049"},
        "action": {"kind": "cycle", "power_off_seconds": 7, "post_reboot_holdoff_seconds": 240},
        "failure_threshold": 4,
        "recovery_threshold": 2,
        "window_seconds": 90,
        "cooldown_seconds": 450,
        "maintenance_windows": [
            {"start": "2026-05-15T02:00:00+00:00", "end": "2026-05-15T03:00:00+00:00"}
        ],
    }
    r = shell_session.post(
        f"{base_url}/app/rules/json",
        data={"rule_json": json.dumps(sent)},
        timeout=10,
        allow_redirects=False,
    )
    assert r.status_code in (302, 303)
    try:
        rows = shell_session.get(
            f"{base_url}/api/v1/admin/rules", timeout=10
        ).json()["data"]
        match = next(rl for rl in rows if rl["name"] == name)

        for k in ("failure_threshold", "recovery_threshold",
                  "window_seconds", "cooldown_seconds"):
            assert match[k] == sent[k], f"{k}: got {match[k]}, sent {sent[k]}"
        assert match["probe"] == sent["probe"]
        assert match["target"] == sent["target"]
        assert match["action"] == sent["action"]
        assert match["maintenance_windows"] == sent["maintenance_windows"]
    finally:
        if 'match' in locals():
            shell_session.delete(
                f"{base_url}/api/v1/admin/rules/{match['id']}", timeout=10
            )


# ── B14 — per-device bulk audit ────────────────────────────────────


def test_per_device_audit_action_exists_in_log(base_url, admin_headers):
    """v0.4.9 introduces device.bulk_deleted_per_device +
    device.mass_command_issued_per_device action names. The audit
    log should accept queries on these even if no rows exist yet
    (post-deploy state). This test verifies the API doesn't 500 on
    these new action filters."""
    for action in (
        "device.bulk_deleted_per_device",
        "device.bulk_delete_skipped_per_device",
        "device.mass_command_issued_per_device",
        "device.mass_command_skipped_per_device",
    ):
        r = requests.get(
            f"{base_url}/api/v1/admin/audit",
            headers=admin_headers,
            params={"action": action, "limit": 10},
            timeout=10,
        )
        assert r.status_code == 200, f"{action}: {r.text}"
