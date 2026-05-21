"""v0.4.20 — pending-adoption announce/adopt flow."""

from __future__ import annotations

import pytest
import requests

from app.services.announcements import _normalize_mac

from .conftest import unique_suffix

# v0.5.83: in the `-m ci` gate (P-QA gate-3). The earlier "two tests
# expect awaiting_register" concern is resolved — that was stale
# pre-v0.5.68 behaviour. The P-REG fix keeps the adoption token
# re-deliverable until the device registers, so a repeat /announce is
# `adopted` again. The state machine is now pinned directly by
# tests/unit/test_announce_state_machine.py.
pytestmark = pytest.mark.ci


def _mac() -> str:
    """Synthesize a unique-ish hex MAC for each test."""
    import secrets
    h = secrets.token_hex(6).upper()
    return ":".join(h[i:i+2] for i in range(0, 12, 2))


def _find_announcement(listing: list, mac: str) -> dict | None:
    """Match an announcement row by MAC.

    S1-6 (`_normalize_mac`) made announce rows store the MAC in
    canonical form (separators stripped), so a row announced as
    ``AA:BB:CC:11:22:33`` is stored — and serialized back — as
    ``AABBCC112233``. The lookup must therefore compare *normalized*
    MACs, not the raw colon-form the test synthesized. Mirrors the
    helper in tests/unit/test_announce_state_machine.py.
    """
    want = _normalize_mac(mac)
    return next(
        (a for a in listing if _normalize_mac(a["mac_address"]) == want), None
    )


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
    match = _find_announcement(listing, mac)
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

        # 5. Subsequent poll WITHOUT registering → still `adopted`, the
        # same token re-delivered. v0.5.68 (P-REG fix) keeps the token
        # on the row until the device registers, so a device that loses
        # one announce response self-heals instead of being stranded.
        r = requests.post(
            f"{base_url}/api/v1/device/announce",
            json={"mac_address": mac, "hardware_model": "sonoff_s31"},
            timeout=10,
        )
        body = r.json()["data"]
        assert body["status"] == "adopted"
        assert body["enrollment_token"] == token  # same token re-delivered

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
    match = _find_announcement(listing, mac)
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
    aid = _find_announcement(listing, mac)["id"]
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
        match = _find_announcement(listing, mac)
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

        repoll = requests.post(
            f"{base_url}/api/v1/device/announce",
            json={
                "mac_address": mac,
                "hardware_model": "sonoff_s31",
                "firmware_version": "0.1.17-test",
                "local_ip": local_ip,
            },
            timeout=10,
        )
        assert repoll.status_code == 200, repoll.text
        # v0.5.68: the re-minted token stays re-deliverable until the
        # device registers — a repeat announce is `adopted`, not
        # `awaiting_register`.
        assert repoll.json()["data"]["status"] == "adopted"

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
