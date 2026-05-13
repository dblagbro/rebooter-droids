"""v0.5.11 — B22 hot-fix: scanned releases must hand devices the
per-channel URL, not the canonical root URL.

Pre-v0.5.11 the scan path wrote `download_url={base}/{filename}` —
but the scan only ever finds files at `{base}/{channel}/{filename}`,
so the device received a 404 for every assigned scanned release.
This test exercises the live deployment: it scans, finds the
highest-version stable release, GETs the `download_url` advertised
on that row, and asserts a 200 with a non-empty body. It also
asserts the mirror-row layout: `local_per_channel` + `channel_pointer`
present, no `local` row claiming a live root URL for scanned entries.
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


def test_scanned_stable_release_download_url_is_per_channel_and_serves(base_url, shell_session):
    # Trigger a scan to ensure the catalog is fresh.
    scan = shell_session.post(
        f"{base_url}/api/v1/admin/firmware/scan", timeout=15
    )
    assert scan.status_code == 200, scan.text

    body = shell_session.get(
        f"{base_url}/api/v1/admin/firmware/releases", timeout=10
    ).json()["data"]
    rows = body["releases"] if isinstance(body, dict) else body
    stable = [r for r in rows if r.get("channel") == "stable"]
    assert stable, "no stable releases found"

    # Pick any scanned release (release_notes carries the "discovered" tag).
    scanned = [r for r in stable if (r.get("release_notes") or "").startswith("discovered")]
    assert scanned, "expected at least one scanned release in the stable channel"
    rel = scanned[0]
    url = rel["download_url"]

    # v0.5.11 contract: the URL the device will be handed must live
    # under the per-channel subdirectory, not the root.
    assert "/firmware/stable/" in url, (
        f"expected per-channel URL for scanned release; got {url!r}"
    )

    # And the URL must actually serve. HEAD is fine — we just need
    # not-404.
    head = requests.head(url, timeout=10, allow_redirects=True)
    assert head.status_code == 200, (
        f"scanned release download_url {url!r} returned {head.status_code}; "
        f"bug B22 regression"
    )
    # Body should not be empty
    assert int(head.headers.get("Content-Length") or 0) > 0


def test_scanned_release_mirror_row_layout(base_url, shell_session):
    """v0.5.11: scanned releases should NOT emit a `local` mirror row
    pointing at the root URL (the file isn't there). Only
    `local_per_channel` + `local_channel_pointer` are valid for scanned
    artifacts."""
    body = shell_session.get(
        f"{base_url}/api/v1/admin/firmware/releases", timeout=10
    ).json()["data"]
    rows = body["releases"] if isinstance(body, dict) else body
    scanned = [
        r for r in rows
        if (r.get("release_notes") or "").startswith("discovered")
        and r.get("channel") == "stable"
    ]
    if not scanned:
        pytest.skip("no scanned releases to verify mirror layout against")

    # The list response carries mirrors inline on this hub version.
    rel = scanned[0]
    mirrors = rel.get("mirrors") or []
    kinds = {m.get("kind") for m in mirrors}
    assert "local_per_channel" in kinds
    assert "local_channel_pointer" in kinds
    # Root `local` mirror must NOT be present for a scanned entry
    # (would be a B22 regression).
    assert "local" not in kinds, (
        f"scanned release should not have a root-path `local` mirror; got {kinds}"
    )
