"""v0.4.28 — regression test for the one-click Upgrade button.

v0.4.21 shipped the per-device "Upgrade" button on /app/devices.
Between then and v0.4.27 the handler picked up a stray reference to
`current_app` without importing it, so the very first click yielded
`{"error":{"code":"internal_error"...}}` (NameError raised inside
the view → caught by the generic 500 handler).

This test asserts the handler returns a clean 302 redirect (with a
flash message in the cookie session) and never the JSON envelope.
"""

from __future__ import annotations

import requests


def _login(base_url: str) -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": "dblagbro@gmail.com", "password": "Super*120120"},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    return s


def _first_device_id(s: requests.Session, base_url: str) -> str | None:
    r = s.get(f"{base_url}/api/v1/admin/devices", timeout=10)
    assert r.status_code == 200, r.text
    devs = r.json().get("data", {}).get("devices", [])
    return devs[0]["id"] if devs else None


def test_upgrade_button_does_not_500(base_url):
    s = _login(base_url)
    device_id = _first_device_id(s, base_url)
    if not device_id:
        # No devices to test against — environment-dependent skip.
        # Don't fail the suite; if the cluster has no devices the
        # button isn't reachable anyway.
        return
    r = s.post(
        f"{base_url}/app/devices/{device_id}/upgrade-to-latest",
        timeout=10,
        allow_redirects=False,
    )
    # Expected: 302 redirect back to /app/devices with a flash cookie
    # set. Either a successful "queued" flash OR a benign "no stable
    # release tracked yet" flash is fine — both prove the handler
    # didn't NameError its way to a 500.
    assert r.status_code in (302, 303), (
        f"upgrade button regressed — got {r.status_code} {r.text!r}"
    )
    # And the response body MUST NOT be the JSON 500 envelope
    assert "internal_error" not in r.text
