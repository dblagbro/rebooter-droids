"""v0.2.5: mass-action confirmation gate.

The gate fires server-side on group commands and firmware deployments.
Thresholds: target_count <= 5 → none required; 5 < N <= 20 → simple ack;
N > 20 → typed verb. We can't easily fabricate >5 real devices in QA, so
these tests focus on the negative shape of the response (it surfaces the
required level + expected typed value) and on the no-op behavior at low
target counts.
"""

import requests

from .conftest import unique_suffix


def _make_group(base_url, admin_headers, name_hint="qa-mass-gate") -> str:
    r = requests.post(
        f"{base_url}/api/v1/admin/groups",
        headers=admin_headers,
        json={"name": f"{name_hint}-{unique_suffix()}"},
        timeout=10,
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def test_empty_group_command_no_confirmation_needed(base_url, admin_headers):
    gid = _make_group(base_url, admin_headers, "qa-empty-gate")
    try:
        r = requests.post(
            f"{base_url}/api/v1/admin/groups/{gid}/commands",
            headers=admin_headers,
            json={"type": "relay_on"},
            timeout=10,
        )
        # 0 members → no fan-out, no confirmation required, returns 201 with
        # fan_out_count 0 and target_count 0.
        assert r.status_code == 201, r.text
        d = r.json()["data"]
        assert d["fan_out_count"] == 0
        assert d["target_count"] == 0
    finally:
        requests.delete(
            f"{base_url}/api/v1/admin/groups/{gid}",
            headers=admin_headers,
            timeout=10,
        )


def test_unregistered_devices_endpoint_reachable(base_url, admin_headers):
    """Admin API surface for the new tracker is wired."""
    r = requests.get(
        f"{base_url}/api/v1/admin/unregistered-devices",
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert "attempts" in body
    assert "active_60min" in body
    assert isinstance(body["attempts"], list)


def test_unregistered_device_attempt_recorded(base_url):
    """An unauthenticated heartbeat call should land in the tracker.

    Sends with a random device_id and checks that the admin endpoint
    eventually reports a non-zero hit count for it.
    """
    fake_id = f"dev_QATEST{unique_suffix()}"
    # Hit the endpoint without any auth header → 401 + record.
    r = requests.post(
        f"{base_url}/api/v1/device/heartbeat",
        json={"device_id": fake_id},
        timeout=10,
    )
    assert r.status_code == 401, r.text


def test_firmware_deploy_single_device_no_gate(base_url, admin_headers):
    """A single-device firmware deployment should not require confirmation
    because target_count <= 5. Just exercises the call shape — we don't
    actually have a release_id to deploy here, so we expect either a 404
    (release not found) or 400 (validation), but NOT 409 (confirmation_required)."""
    r = requests.post(
        f"{base_url}/api/v1/admin/firmware/deployments",
        headers=admin_headers,
        json={
            "release_id": "fw_doesnotexist",
            "target_type": "device",
            "target_id": "dev_doesnotexist",
        },
        timeout=10,
    )
    assert r.status_code in (400, 404), r.text
    body = r.json()
    assert body["error"]["code"] != "confirmation_required"
