"""v0.5.89 — end-to-end device adoption regression test.

The charter (P-REG) named this the highest-value missing test: the
~60 KB announce → pending-adoption → adopt → token-mint → /register →
first-heartbeat → "online" path spans `announcements.py`,
`pending_adoption.py`, `enrollment.py` and `device_api.py` but had **no
single test driving it as one flow**. The v0.5.36→v0.5.68 regression
(siteless adoption tokens 500'd `/register` for 32 versions) is exactly
the class of bug this guards against — every prior test exercised one
hop, so a break in the *seam between* hops merged silently.

HTTP integration test against a running instance; gated into the CI
`-m ci` bucket so it runs on every push.
"""

from __future__ import annotations

import secrets

import pytest
import requests

from app.services.announcements import _normalize_mac

pytestmark = pytest.mark.ci


def _mac() -> str:
    """A unique hex MAC per run — keeps each run independent of any
    announcement row a prior run left behind."""
    h = secrets.token_hex(6).upper()
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def test_full_adoption_flow_announce_to_online(base_url, admin_headers):
    """Drive the complete bring-up as one flow: a device announces, the
    operator adopts it, the device polls for and registers with the
    enrollment token, sends its first heartbeat — and the hub then
    reports it active and online."""
    mac = _mac()
    device_id = None
    aid = None
    try:
        # ── 1. Device announces (unauthenticated) → lands as pending ──
        r = requests.post(
            f"{base_url}/api/v1/device/announce",
            json={
                "mac_address": mac,
                "hardware_model": "sonoff_s31",
                "hardware_revision": "rev2",
                "firmware_version": "0.1.29",
                "local_ip": "192.168.1.123",
                "display_name_hint": "QA E2E Plug",
            },
            timeout=10,
        )
        assert r.status_code == 200, r.text
        announced = r.json()["data"]
        assert announced["status"] == "pending"
        assert "enrollment_token" not in announced  # not adopted yet

        # ── 2. Operator sees it in the pending-adoption list ──
        listing = requests.get(
            f"{base_url}/api/v1/admin/pending-adoption",
            headers=admin_headers, timeout=10,
        )
        assert listing.status_code == 200, listing.text
        # S1-6 (`_normalize_mac`) stores — and serializes back —
        # announcement MACs in canonical form (separators stripped),
        # so compare normalized MACs, not the raw colon-form.
        match = next(
            (
                a for a in listing.json()["data"]
                if _normalize_mac(a["mac_address"]) == _normalize_mac(mac)
            ),
            None,
        )
        assert match is not None, "announced device missing from pending-adoption"
        assert match["state"] == "pending"
        aid = match["id"]

        # ── 3. Operator adopts → an enrollment token is minted ──
        adopt = requests.post(
            f"{base_url}/api/v1/admin/pending-adoption/{aid}/adopt",
            headers=admin_headers,
            json={"display_name": "QA E2E Plug"},
            timeout=10,
        )
        assert adopt.status_code == 200, adopt.text
        adopt_data = adopt.json()["data"]
        assert adopt_data["state"] == "awaiting_pickup"
        # the raw secret is never handed back to the operator
        assert "enrollment_token" not in adopt_data
        assert "adoption_token_secret" not in adopt_data

        # ── 4. Device's next announce poll picks up the token ──
        r = requests.post(
            f"{base_url}/api/v1/device/announce",
            json={"mac_address": mac, "hardware_model": "sonoff_s31"},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        adopted = r.json()["data"]
        assert adopted["status"] == "adopted"
        token = adopted["enrollment_token"]
        assert token.startswith("et_")

        # ── 5. Device registers with the token → gets a device token ──
        reg = requests.post(
            f"{base_url}/api/v1/device/register",
            json={
                "enrollment_token": token,
                "mac_address": mac,
                "hardware_model": "sonoff_s31",
                "firmware_version": "0.1.29",
            },
            timeout=10,
        )
        assert reg.status_code == 201, reg.text
        reg_data = reg.json()["data"]
        device_id = reg_data["device_id"]
        device_token = reg_data["device_token"]
        assert device_token.startswith("dt_")

        # ── 6. Device sends its first heartbeat (Bearer device token) ──
        hb = requests.post(
            f"{base_url}/api/v1/device/heartbeat",
            headers={"Authorization": f"Bearer {device_token}"},
            json={
                "device_id": device_id,
                "firmware_version": "0.1.29",
                "relay_on": True,
                "wifi_connected": True,
                "health_state": "healthy",
                "uptime_seconds": 120,
            },
            timeout=10,
        )
        assert hb.status_code == 200, hb.text

        # ── 7. The hub now reports the device active + online ──
        detail = requests.get(
            f"{base_url}/api/v1/admin/devices/{device_id}",
            headers=admin_headers, timeout=10,
        )
        assert detail.status_code == 200, detail.text
        dev = detail.json()["data"]
        assert dev["registration_state"] == "active"
        assert dev["last_heartbeat_at"] is not None
        assert dev["online"] is True
        assert dev["heartbeat_state"] == "online"
        assert dev["mac_address"] == mac
        assert dev["firmware_version"] == "0.1.29"

        # ── 8. The announcement closed out as `registered` ──
        all_anns = requests.get(
            f"{base_url}/api/v1/admin/pending-adoption?show_all=1",
            headers=admin_headers, timeout=10,
        ).json()["data"]
        ann = next((a for a in all_anns if a["id"] == aid), None)
        assert ann is not None
        assert ann["state"] == "registered"
        assert ann["consumed_at"] is not None

        # ── 9. The device token keeps authenticating subsequent calls ──
        hb2 = requests.post(
            f"{base_url}/api/v1/device/heartbeat",
            headers={"Authorization": f"Bearer {device_token}"},
            json={"device_id": device_id, "health_state": "healthy"},
            timeout=10,
        )
        assert hb2.status_code == 200, hb2.text

        # ── 10. The spent enrollment token cannot be reused ──
        replay = requests.post(
            f"{base_url}/api/v1/device/register",
            json={"enrollment_token": token, "mac_address": mac},
            timeout=10,
        )
        assert replay.status_code == 409, replay.text
        assert replay.json()["error"]["code"] == "enrollment_consumed"
    finally:
        if device_id:
            requests.delete(
                f"{base_url}/api/v1/admin/devices/{device_id}",
                headers=admin_headers, timeout=10,
            )
        if aid:
            # Best-effort — the announcement row delete is a UI route.
            try:
                requests.post(
                    f"{base_url}/app/pending-adoption/{aid}/delete",
                    headers=admin_headers, timeout=10,
                )
            except Exception:
                pass
