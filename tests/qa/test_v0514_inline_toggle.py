"""v0.5.14 (B18): inline on/off toggle on the devices list.

Two contract assertions:
1. /api/v1/admin/devices payloads now carry `latest_relay_on` +
   `latest_mode` (None when the device has never heartbeated; bool /
   string when it has).
2. POST /app/devices/<id>/commands with `next=list` redirects to
   /app/devices on success (instead of the device detail page).

The actual fan-out of relay commands is covered by the existing
admin-API command tests; this file just guards the new surface so a
future refactor can't accidentally drop the inline-toggle wire-up.
"""

from __future__ import annotations

import pytest
import requests

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


def test_admin_devices_payload_has_latest_relay_on(base_url, shell_session):
    r = shell_session.get(
        f"{base_url}/api/v1/admin/devices", timeout=10
    )
    assert r.status_code == 200, r.text
    devices = r.json()["data"]["devices"]
    if not devices:
        pytest.skip("no devices in fleet")
    # Contract: every device row has the new keys.
    for d in devices:
        assert "latest_relay_on" in d, (
            f"missing latest_relay_on on device row {d.get('id')!r}; "
            f"keys={sorted(d.keys())}"
        )
        assert "latest_mode" in d
        # Type contract:
        assert d["latest_relay_on"] is None or isinstance(
            d["latest_relay_on"], bool
        )
        assert d["latest_mode"] is None or isinstance(d["latest_mode"], str)


def test_inline_toggle_redirects_to_list(base_url, shell_session):
    """POST to /app/devices/<id>/commands with next=list should
    redirect (303/302) to /app/devices, not the device detail page.

    Uses an arbitrary toggleable device; if the fleet has none online
    we skip — the live device's actual relay state is incidental to
    the redirect contract."""
    r = shell_session.get(
        f"{base_url}/api/v1/admin/devices", timeout=10
    )
    devs = r.json()["data"]["devices"]
    online_devs = [
        d for d in devs
        if d.get("online") and d.get("latest_relay_on") is not None
        and not d.get("is_held_off")
        and not d.get("is_protected")
        and not d.get("is_qa_fixture") is False  # prefer non-QA but accept either
    ]
    # Fall back to any online device with a known relay state if no
    # non-QA candidate is available.
    if not online_devs:
        online_devs = [
            d for d in devs
            if d.get("online") and d.get("latest_relay_on") is not None
            and not d.get("is_held_off") and not d.get("is_protected")
        ]
    if not online_devs:
        pytest.skip("no online togglable device in fleet")
    target = online_devs[0]
    # Round-trip the device back to its current state — fire whichever
    # command matches what it's already in so we don't actually flip
    # power on real hardware.
    cmd_type = "relay_on" if target["latest_relay_on"] else "relay_off"
    r = shell_session.post(
        f"{base_url}/app/devices/{target['id']}/commands",
        data={"type": cmd_type, "next": "list"},
        allow_redirects=False,
        timeout=10,
    )
    assert r.status_code in (302, 303), r.status_code
    location = r.headers.get("Location", "")
    assert location.endswith("/app/devices") or "/app/devices?" in location, (
        f"expected redirect to /app/devices, got {location!r}"
    )
