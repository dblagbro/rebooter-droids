"""v0.4.20 — pending-adoption announce/adopt flow."""

from __future__ import annotations

import pytest
import requests

from .conftest import unique_suffix

# NB: not in the `-m ci` gate yet. Two tests assert the announce
# lifecycle returns status='awaiting_register' on a repeat /announce of
# an adopted-not-yet-registered device, but a from-scratch instance
# returns 'adopted' both times. That adopted->awaiting_register
# transition needs confirming with the announce-lifecycle owner before
# this file can gate — tracked in docs/test-plan.md gate-3.


def _mac() -> str:
    """Synthesize a unique-ish hex MAC for each test."""
    import secrets
    h = secrets.token_hex(6).upper()
    return ":".join(h[i:i+2] for i in range(0, 12, 2))


def test_announce_creates_pending_then_adopts(base_url, admin_headers):
    mac = _mac()
    # 1. Device announces — should get pending
    r = requests.post(
        f"{base_url}/api/v1/device/announce",
        json={
            "mac_address": mac,
            "hardware_model": "sonoff_s31",
            "firmware_version": "0.1.5-test",
            "local_ip": "192.168.1.99",
        },
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["status"] == "pending"
    # retry_after_seconds is REBOOTER_ANNOUNCE_PENDING_RETRY_AFTER_SECONDS
    # (default 5). The old `== 30` hardcoded a value no deployment sets —
    # assert a sane positive hint instead of a specific config value.
    assert isinstance(body["retry_after_seconds"], int)
    assert body["retry_after_seconds"] >= 1
    assert "enrollment_token" not in body  # not yet adopted

    # 2. Operator sees in pending list
    listing = requests.get(
        f"{base_url}/api/v1/admin/pending-adoption",
        headers=admin_headers, timeout=10,
    ).json()["data"]
    match = next((a for a in listing if a["mac_address"] == mac), None)
    assert match is not None
    assert match["state"] == "pending"
    aid = match["id"]

    try:
        # 3. Operator adopts
        adopt = requests.post(
            f"{base_url}/api/v1/admin/pending-adoption/{aid}/adopt",
            headers=admin_headers,
            json={"display_name": f"qa-adopt-{unique_suffix()}"},
            timeout=10,
        )
        assert adopt.status_code == 200, adopt.text
        # adopt response NEVER includes the secret
        assert "adoption_token_secret" not in adopt.json()["data"]
        assert "enrollment_token" not in adopt.json()["data"]
        assert adopt.json()["data"]["state"] == "awaiting_pickup"
        assert adopt.json()["data"]["enrollment_token_id"] is not None

        # 4. Device polls again — gets the token
        r = requests.post(
            f"{base_url}/api/v1/device/announce",
            json={
                "mac_address": mac,
                "hardware_model": "sonoff_s31",
                "firmware_version": "0.1.5-test",
            },
            timeout=10,
        )
        body = r.json()["data"]
        assert body["status"] == "adopted"
        assert "enrollment_token" in body
        assert body["enrollment_token"].startswith("et_")
        assert "central_register_url" in body
        token = body["enrollment_token"]

        # 5. Subsequent poll WITHOUT consuming → awaiting_register
        r = requests.post(
            f"{base_url}/api/v1/device/announce",
            json={"mac_address": mac, "hardware_model": "sonoff_s31"},
            timeout=10,
        )
        body = r.json()["data"]
        assert body["status"] == "awaiting_register"
        assert "enrollment_token" not in body  # secret was cleared after delivery

        # 6. Use the token to register — should succeed and stamp consumed_at
        reg = requests.post(
            f"{base_url}/api/v1/device/register",
            json={
                "enrollment_token": token,
                "mac_address": mac,
                "hardware_model": "sonoff_s31",
                "firmware_version": "0.1.5-test",
            },
            timeout=10,
        )
        assert reg.status_code == 201, reg.text
        device_id = reg.json()["data"]["device_id"]

        # 7. Announcement now reads "registered"
        listing = requests.get(
            f"{base_url}/api/v1/admin/pending-adoption?show_all=1",
            headers=admin_headers, timeout=10,
        ).json()["data"]
        post = next((a for a in listing if a["id"] == aid), None)
        assert post is not None
        assert post["state"] == "registered"
        assert post["consumed_at"] is not None

        # Cleanup the device row
        requests.delete(
            f"{base_url}/api/v1/admin/devices/{device_id}",
            headers=admin_headers, timeout=10,
        )
    finally:
        # Best-effort cleanup of the announcement row
        try:
            requests.post(
                f"{base_url}/app/pending-adoption/{aid}/delete",
                headers=admin_headers, timeout=10,
            )
        except Exception:
            pass


def test_announce_validation_rejects_garbage_mac(base_url):
    r = requests.post(
        f"{base_url}/api/v1/device/announce",
        json={"mac_address": "<script>alert(1)</script>"},
        timeout=10,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_failed"


def test_announce_validation_rejects_overlong_field(base_url):
    r = requests.post(
        f"{base_url}/api/v1/device/announce",
        json={
            "mac_address": _mac(),
            "firmware_version": "x" * 41,  # cap is 40
        },
        timeout=10,
    )
    assert r.status_code == 400
    assert "firmware_version" in r.json()["error"]["message"]


def test_announce_missing_mac_rejects(base_url):
    r = requests.post(
        f"{base_url}/api/v1/device/announce",
        json={"hardware_model": "sonoff_s31"},
        timeout=10,
    )
    assert r.status_code == 400
    assert "mac_address is required" in r.json()["error"]["message"]


def test_repeat_announce_increments_count(base_url, admin_headers):
    mac = _mac()
    for _ in range(3):
        r = requests.post(
            f"{base_url}/api/v1/device/announce",
            json={"mac_address": mac, "hardware_model": "sonoff_s31"},
            timeout=10,
        )
        assert r.status_code == 200

    listing = requests.get(
        f"{base_url}/api/v1/admin/pending-adoption",
        headers=admin_headers, timeout=10,
    ).json()["data"]
    match = next((a for a in listing if a["mac_address"] == mac), None)
    assert match is not None
    assert match["announce_count"] == 3
    # Cleanup
    requests.post(
        f"{base_url}/api/v1/admin/pending-adoption/{match['id']}/reject",
        headers=admin_headers, timeout=10,
    )


def test_reject_returns_back_off(base_url, admin_headers):
    mac = _mac()
    requests.post(
        f"{base_url}/api/v1/device/announce",
        json={"mac_address": mac}, timeout=10,
    )
    listing = requests.get(
        f"{base_url}/api/v1/admin/pending-adoption",
        headers=admin_headers, timeout=10,
    ).json()["data"]
    aid = next(a["id"] for a in listing if a["mac_address"] == mac)
    requests.post(
        f"{base_url}/api/v1/admin/pending-adoption/{aid}/reject",
        headers=admin_headers, timeout=10,
    )
    # Device polls again → rejected with 1h back-off
    r = requests.post(
        f"{base_url}/api/v1/device/announce",
        json={"mac_address": mac}, timeout=10,
    )
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["status"] == "rejected"
    assert body["retry_after_seconds"] == 3600


def test_pending_adoption_page_renders(base_url, shell_session_admin):
    body = shell_session_admin.get(
        f"{base_url}/app/pending-adoption", timeout=10,
    ).text
    assert "Pending Adoption" in body
    assert "How adoption works" in body


def test_known_device_missing_token_auto_rebinds(base_url, admin_headers):
    mac = _mac()
    local_ip = "192.168.1.148"
    aid = None
    device_id = None

    try:
        first = requests.post(
            f"{base_url}/api/v1/device/announce",
            json={
                "mac_address": mac,
                "hardware_model": "sonoff_s31",
                "firmware_version": "0.1.17-test",
                "local_ip": local_ip,
            },
            timeout=10,
        )
        assert first.status_code == 200, first.text

        listing = requests.get(
            f"{base_url}/api/v1/admin/pending-adoption",
            headers=admin_headers, timeout=10,
        ).json()["data"]
        match = next((a for a in listing if a["mac_address"] == mac), None)
        assert match is not None
        aid = match["id"]

        adopt = requests.post(
            f"{base_url}/api/v1/admin/pending-adoption/{aid}/adopt",
            headers=admin_headers,
            json={"display_name": f"qa-rebind-{unique_suffix()}"},
            timeout=10,
        )
        assert adopt.status_code == 200, adopt.text

        adopted = requests.post(
            f"{base_url}/api/v1/device/announce",
            json={
                "mac_address": mac,
                "hardware_model": "sonoff_s31",
                "firmware_version": "0.1.17-test",
                "local_ip": local_ip,
            },
            timeout=10,
        )
        assert adopted.status_code == 200, adopted.text
        token = adopted.json()["data"]["enrollment_token"]

        reg = requests.post(
            f"{base_url}/api/v1/device/register",
            json={
                "enrollment_token": token,
                "mac_address": mac,
                "hardware_model": "sonoff_s31",
                "firmware_version": "0.1.17-test",
                "local_ip": local_ip,
            },
            timeout=10,
        )
        assert reg.status_code == 201, reg.text
        device_id = reg.json()["data"]["device_id"]

        auto = requests.post(
            f"{base_url}/api/v1/device/announce",
            json={
                "mac_address": mac,
                "hardware_model": "sonoff_s31",
                "firmware_version": "0.1.17-test",
                "local_ip": local_ip,
            },
            timeout=10,
        )
        assert auto.status_code == 200, auto.text
        auto_body = auto.json()["data"]
        assert auto_body["status"] == "adopted"
        assert auto_body["enrollment_token"].startswith("et_")
        replacement_token = auto_body["enrollment_token"]

        awaiting = requests.post(
            f"{base_url}/api/v1/device/announce",
            json={
                "mac_address": mac,
                "hardware_model": "sonoff_s31",
                "firmware_version": "0.1.17-test",
                "local_ip": local_ip,
            },
            timeout=10,
        )
        assert awaiting.status_code == 200, awaiting.text
        assert awaiting.json()["data"]["status"] == "awaiting_register"

        rebind = requests.post(
            f"{base_url}/api/v1/device/register",
            json={
                "enrollment_token": replacement_token,
                "mac_address": mac,
                "hardware_model": "sonoff_s31",
                "firmware_version": "0.1.17-test",
                "local_ip": local_ip,
            },
            timeout=10,
        )
        assert rebind.status_code == 201, rebind.text
        assert rebind.json()["data"]["device_id"] == device_id
    finally:
        if device_id:
            requests.delete(
                f"{base_url}/api/v1/admin/devices/{device_id}",
                headers=admin_headers, timeout=10,
            )
        if aid:
            try:
                requests.post(
                    f"{base_url}/app/pending-adoption/{aid}/delete",
                    headers=admin_headers, timeout=10,
                )
            except Exception:
                pass


@pytest.fixture(scope="module")
def shell_session_admin(base_url, admin_creds):
    s = requests.Session()
    email, pw = admin_creds
    s.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw}, timeout=10,
    )
    return s
