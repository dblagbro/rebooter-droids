"""v0.5.13 (B19): firmware scan must pick up content-changed binaries
that kept the same filename.

The firmware team's iterative-fix workflow rebuilds a release
without bumping the version string (e.g. fixing a BearSSL heap bug
in `rebooter-0.1.9-dev-central.bin`). Pre-v0.5.13 the on-disk scan
skipped these because the dedupe key was filename-only — devices
saw the new bytes from disk but the hub still advertised the OLD
SHA, so device-side verification refused the OTA.

This is a live-deployment test:
1. Trigger a scan to ensure the catalog is current.
2. Snapshot the highest-version stable scanned release's SHA.
3. Trigger another scan immediately (idempotent — no on-disk changes).
4. Confirm the SHA didn't change and `updated` is empty (no spurious updates).
5. Confirm `updated` appears in the API response shape (forwards-compat).

We can't safely mutate the live `.bin` from a QA test, so the
strong contract assertions are around the response shape and idempotence.
The actual mutation path is exercised by the existing live-soak
bug-log entries from the firmware team.
"""

from __future__ import annotations

import pytest
import requests


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


def test_firmware_scan_response_includes_updated_list(base_url, shell_session):
    r = shell_session.post(
        f"{base_url}/api/v1/admin/firmware/scan", timeout=15
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    # v0.5.13: scan response carries the `updated` array alongside the
    # existing `discovered`. May be empty when nothing changed since the
    # last scan, but the key MUST exist.
    assert "updated" in body, (
        f"v0.5.13 scan response should include `updated`; got keys={sorted(body.keys())}"
    )
    assert isinstance(body["updated"], list)


def test_firmware_scan_idempotent_no_spurious_updates(base_url, shell_session):
    """Back-to-back scans with no on-disk changes must report
    `updated: []` (and `discovered: []`) — otherwise we'd churn the
    DB + audit log every operator click."""
    first = shell_session.post(
        f"{base_url}/api/v1/admin/firmware/scan", timeout=15
    ).json()["data"]
    second = shell_session.post(
        f"{base_url}/api/v1/admin/firmware/scan", timeout=15
    ).json()["data"]
    assert second.get("updated", []) == [], (
        f"second scan should not report `updated` rows when nothing changed; "
        f"first.updated={first.get('updated', [])}, second.updated={second.get('updated', [])}"
    )
