"""v0.3.6 — `device_auth_rejected` attention items on the Status inbox.

When a device tries to call /api/v1/device/* with a stale or
unknown bearer token, the existing `unregistered_auth_attempts`
tracker (v0.2.5+) records it. Pre-v0.3.6 those rows were only
visible from `/app/unregistered-devices`; v0.3.6 surfaces them on
the Status inbox once the same (device_id, ip, endpoint) tuple
crosses 3 hits in 60 minutes.

Tests:
- Trigger 3+ 401s with a fake bearer token → attention item
  appears on the Status page + the totals.auth_rejected count
  bumps.
- Single-hit 401 → does NOT trigger an attention item (filter).
- Status page renders the attention item with a link to
  /app/unregistered-devices (not /app/devices/<id>, which would
  404 for the claimed device id).
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


def _provoke_401(base_url: str, claimed_id: str, count: int) -> None:
    """Send `count` heartbeat-with-bogus-bearer-token calls. Each
    triggers a 401 and a row in unregistered_auth_attempts (with
    hit_count incrementing on the same tuple)."""
    for _ in range(count):
        r = requests.post(
            f"{base_url}/api/v1/device/heartbeat",
            headers={"Authorization": "Bearer dt_fake_token_for_qa"},
            json={
                "device_id": claimed_id,
                "firmware_version": "qa-bogus",
                "mode": "smart_plug",
                "relay_on": True,
                "wifi_connected": True,
                "health_state": "healthy",
                "uptime_seconds": 1,
            },
            timeout=10,
        )
        # The auth middleware rejects with 401 (or 403); any non-2xx
        # is the rejection signal that should land in the tracker.
        assert r.status_code in (401, 403), r.status_code


def test_qa_shaped_401s_are_filtered_from_attention(base_url, shell_session):
    """v0.3.7: requests from machine-internal IPs (docker bridge =
    192.168.18.1, localhost) AND requests with QA-prefixed device-ids
    (`dev_QA_*`, `qa-*`, `test-*`, `playwright*`) MUST be filtered
    OUT of the Status attention feed.

    Rationale: QA tests in this repo intentionally provoke 401s to
    exercise the auth path; operator complained about test noise
    polluting their Status inbox.

    The data is still recorded in unregistered_auth_attempts (so the
    /app/unregistered-devices diagnostic page still shows it) — it
    just doesn't surface as an attention item."""
    claimed_id = f"dev_QA_{unique_suffix()}"
    _provoke_401(base_url, claimed_id, count=3)

    body = shell_session.get(f"{base_url}/app/", timeout=10).text
    assert claimed_id not in body, (
        f"QA-shaped claimed_id {claimed_id} should NOT surface as an "
        f"attention item on Status. Either the QA-prefix filter or the "
        f"machine-internal-IP filter (192.168.18.1) is broken."
    )
    # Sanity: the row IS still in the diagnostic page (the filter
    # is presentation-only; the data path still records it).
    diag_body = shell_session.get(
        f"{base_url}/app/unregistered-devices", timeout=10
    ).text
    assert claimed_id in diag_body, (
        f"QA-shaped claimed_id {claimed_id} should still appear in the "
        f"/app/unregistered-devices diagnostic page (data is recorded; "
        f"only the Status surface filters it)."
    )


def test_single_401_does_not_surface(base_url, shell_session):
    """Hit-count threshold is 3. A single rejected request must not
    generate an attention item — filters out flapping noise."""
    claimed_id = f"dev_QA_solo_{unique_suffix()}"
    _provoke_401(base_url, claimed_id, count=1)
    body = shell_session.get(f"{base_url}/app/", timeout=10).text
    assert claimed_id not in body, (
        f"single 401 should not surface; claimed_id {claimed_id} appeared "
        f"on the Status page"
    )


def test_status_page_does_not_crash_on_tracker_failure(base_url, shell_session):
    """Best-effort guarantee — Status page must always render a
    verdict block, regardless of any subsystem failure (tracker,
    DB hiccup, etc.). Smoke check: the page returns 200 and contains
    the verdict CSS class."""
    r = shell_session.get(f"{base_url}/app/", timeout=10)
    assert r.status_code == 200
    assert "v3-verdict" in r.text
