"""v0.5.68 (P-REG) — enrolment token must survive a lost /announce response.

The bug
-------
`upsert_announcement` used to clear `adoption_token_secret` on the
*first* /announce poll that delivered it. A device that lost that
single HTTP response — a dropped packet, or an ESP8266 crash under
TLS/heap pressure (exactly what the firmware team documents) — could
never obtain the token again: every later poll returned
`awaiting_register` ("token already delivered"). The device was
permanently bricked at adoption time. This is the "failing
registrations" symptom.

The fix
-------
The secret stays on the announcement row, re-deliverable on every
poll, until the device actually completes /register (`mark_consumed`
clears it then).

This test drives the real adoption flow against the deployment:
announce → adopt → announce (1st delivery) → announce again (the lost
packet) → register → announce (registered). The second announce is
the regression guard — pre-fix it returned `awaiting_register`.
"""

from __future__ import annotations

import secrets

import pytest
import requests

# Verified green against a fresh ephemeral instance — part of the
# GitHub Actions CI gate. See docs/test-plan.md.
pytestmark = pytest.mark.ci


def _announce(base_url: str, mac: str) -> dict:
    r = requests.post(
        f"{base_url}/api/v1/device/announce",
        json={
            "mac_address": mac,
            "hardware_model": "S31",
            "firmware_version": "0.1.29-dev-central-safe",
            "local_ip": "192.168.1.250",
            "display_name_hint": "qa-redeliv",
        },
        timeout=10,
    )
    assert r.status_code == 200, f"announce failed: {r.status_code} {r.text}"
    return r.json()["data"]


def test_enrollment_token_redelivered_until_register(base_url, admin_headers):
    mac = "AA:BB:CC:" + ":".join(
        secrets.token_hex(1).upper() for _ in range(3)
    )
    device_id = None
    try:
        # 1. Device announces itself — no token yet.
        first = _announce(base_url, mac)
        assert first["status"] == "pending", first

        # 2. Operator adopts it.
        listing = requests.get(
            f"{base_url}/api/v1/admin/pending-adoption",
            headers=admin_headers,
            timeout=10,
        )
        listing.raise_for_status()
        match = next(
            (a for a in listing.json()["data"] if a["mac_address"] == mac), None
        )
        assert match, f"announcement for {mac} not in pending list"
        announcement_id = match["id"]  # noqa: F841 — used in the adopt URL below

        adopt = requests.post(
            f"{base_url}/api/v1/admin/pending-adoption/{announcement_id}/adopt",
            headers=admin_headers,
            json={"display_name": "qa-redeliv-fixture"},
            timeout=10,
        )
        assert adopt.status_code == 200, f"adopt failed: {adopt.text}"

        # 3. First announce after adoption — token delivered.
        delivered = _announce(base_url, mac)
        assert delivered["status"] == "adopted", delivered
        token_1 = delivered.get("enrollment_token")
        assert token_1, f"no enrollment_token in first delivery: {delivered}"

        # 4. THE REGRESSION GUARD. The device "lost" the response above.
        #    A second announce MUST still hand back the token — pre-fix
        #    this returned status='awaiting_register' with no token and
        #    the device was permanently stranded.
        redelivered = _announce(base_url, mac)
        assert redelivered["status"] == "adopted", (
            f"token NOT re-delivered after a lost response — device would "
            f"be stranded. Got: {redelivered}"
        )
        token_2 = redelivered.get("enrollment_token")
        assert token_2 == token_1, (
            f"re-delivered token differs from the original: "
            f"{token_2!r} != {token_1!r}"
        )

        # 5. Device finally registers with the (re-delivered) token.
        reg = requests.post(
            f"{base_url}/api/v1/device/register",
            json={
                "enrollment_token": token_2,
                "mac_address": mac,
                "hardware_model": "S31",
                "firmware_version": "0.1.29-dev-central-safe",
                "qa_fixture": True,
            },
            timeout=10,
        )
        assert reg.status_code == 201, f"register failed: {reg.status_code} {reg.text}"
        reg_data = reg.json()["data"]
        device_id = reg_data["device_id"]
        assert reg_data.get("device_token"), reg_data

        # 6. Post-register announce — the row is now `registered` and the
        #    plaintext secret has been cleared.
        after = _announce(base_url, mac)
        assert after["status"] == "registered", after
    finally:
        # Best-effort cleanup. The device is registered as a QA fixture
        # (hidden in the admin UI); delete it outright if the endpoint
        # exists. The announcement ends in `registered` state, which the
        # default pending-adoption list already filters out, so it needs
        # no cleanup.
        if device_id:
            try:
                requests.delete(
                    f"{base_url}/api/v1/admin/devices/{device_id}",
                    headers=admin_headers,
                    timeout=10,
                )
            except requests.RequestException:
                pass
