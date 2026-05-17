"""v0.3.9 — firmware mirror chain P1.

Per RFC-002 P1: a firmware upload now publishes to per-channel
sub-paths, maintains a `<channel>/latest.bin` pointer, and writes
mirror records into the new firmware_release_mirrors table.

Tests:
- Upload a release. Verify three URLs respond with the same SHA:
  - canonical flat:        /firmware/rebooter-<v>.bin
  - per-channel:           /firmware/<channel>/rebooter-<v>.bin
  - channel pointer:       /firmware/<channel>/latest.bin
- Verify the FirmwareRelease response carries 3 mirror records,
  all status=live, all verified_sha256 matching the upload.
- Verify uploading a NEWER release overwrites the channel pointer.
- Verify delete-release cleans up canonical + per-channel +
  channel pointer (when pointer still matches).

Tests run against the live deployment.
"""

from __future__ import annotations

import hashlib
import io

import pytest
import requests

from .conftest import unique_suffix

# v0.5.84: in the `-m ci` gate. The firmware mirror URLs are served by
# nginx from the shared firmware volume — the gate now fronts the app
# with nginx (ci/nginx.conf), so these run for real.
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


def _make_fake_firmware(suffix: str) -> bytes:
    """Synthetic ESP-shaped binary content. Just bytes; backend
    SHA-checks but doesn't parse."""
    return (
        b"REBOOTER-FAKE-FW-" + suffix.encode("ascii")
        + b"\0\1\2\3" * 200
    )


def _upload(shell_session, base_url: str, version: str, channel: str) -> dict:
    body = _make_fake_firmware(version + "-" + channel)
    sha = hashlib.sha256(body).hexdigest()
    files = {"file": ("rebooter.bin", io.BytesIO(body), "application/octet-stream")}
    data = {"version": version, "channel": channel, "sha256": sha}
    r = shell_session.post(
        f"{base_url}/api/v1/admin/firmware/releases",
        files=files,
        data=data,
        timeout=20,
    )
    assert r.status_code == 201, r.text
    payload = r.json()["data"]
    payload["_test_sha"] = sha
    payload["_test_body_len"] = len(body)
    return payload


def _get_bytes(url: str) -> tuple[int, bytes]:
    r = requests.get(url, timeout=20)
    return r.status_code, r.content


def _pointer_url_for(rel: dict, channel: str) -> str:
    """Build the redirect-endpoint URL from a release's canonical URL."""
    canonical_url = rel["download_url"]
    firmware_root = canonical_url.rsplit("/", 1)[0]
    api_root = firmware_root[: -len("/firmware")] + "/api/v1/firmware"
    return f"{api_root}/{channel}/latest"


def test_upload_publishes_to_three_locations(base_url, shell_session):
    """One upload = canonical (flat) + per-channel static file +
    channel-pointer redirect endpoint, all resolve to the same SHA.

    The channel pointer is a Flask 302 endpoint, NOT a static file
    (avoids nginx open_file_cache collisions on overwrite). Tests
    follow the redirect with allow_redirects=True."""
    version = f"qa039-{unique_suffix()}"
    rel = _upload(shell_session, base_url, version=version, channel="dev")
    try:
        canonical_url = rel["download_url"]
        per_channel_url = canonical_url.rsplit("/", 1)[0] + f"/dev/{rel['filename']}"
        pointer_url = _pointer_url_for(rel, "dev")

        for label, url in (
            ("canonical", canonical_url),
            ("per-channel", per_channel_url),
        ):
            sc, body = _get_bytes(url)
            assert sc == 200, f"{label} URL {url} returned {sc}"
            actual = hashlib.sha256(body).hexdigest()
            assert actual == rel["_test_sha"], (
                f"{label} URL {url} served sha={actual} but release sha is "
                f"{rel['_test_sha']}"
            )

        # Channel pointer follows the 302 to the actual binary.
        r = requests.get(pointer_url, timeout=20, allow_redirects=True)
        assert r.status_code == 200, (
            f"pointer URL {pointer_url} did not resolve to 200 "
            f"(history: {[h.status_code for h in r.history]}, final url: {r.url})"
        )
        assert hashlib.sha256(r.content).hexdigest() == rel["_test_sha"]

        # And single-hop the redirect itself: must be 302 with Location
        # pointing at the per-channel URL.
        r302 = requests.get(pointer_url, timeout=10, allow_redirects=False)
        assert r302.status_code == 302
        assert r302.headers.get("Location", "").endswith(f"/dev/{rel['filename']}")
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/firmware/releases/{rel['id']}",
            timeout=20,
        )


def test_release_carries_three_mirror_rows(base_url, shell_session):
    """Every upload writes 3 firmware_release_mirrors rows
    (canonical local, per-channel local, channel-pointer local)
    all status=live with verified_sha256 set."""
    version = f"qa039m-{unique_suffix()}"
    rel = _upload(shell_session, base_url, version=version, channel="beta")
    try:
        # Re-fetch the release list and find ours.
        rows = shell_session.get(
            f"{base_url}/api/v1/admin/firmware/releases",
            timeout=10,
        ).json()["data"]["releases"]
        match = next((r for r in rows if r["id"] == rel["id"]), None)
        assert match is not None, "release missing from list"
        mirrors = match.get("mirrors") or []
        assert len(mirrors) == 3, f"expected 3 mirror rows, got {len(mirrors)}: {mirrors}"
        for m in mirrors:
            assert m["status"] == "live", m
            assert m["verified_sha256"] == rel["_test_sha"], m
            assert m["url"], m
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/firmware/releases/{rel['id']}",
            timeout=20,
        )


def test_channel_pointer_redirects_to_latest_release(base_url, shell_session):
    """Uploading a SECOND release in the same channel makes the
    channel-pointer endpoint redirect to the NEWER binary. The
    redirect endpoint queries the DB on every request, so there's
    no caching to invalidate."""
    v1 = f"qa039a-{unique_suffix()}"
    rel1 = _upload(shell_session, base_url, version=v1, channel="dev")
    try:
        pointer_url = _pointer_url_for(rel1, "dev")
        # Step 1: pointer follows to rel1.
        r = requests.get(pointer_url, timeout=20, allow_redirects=True)
        assert r.status_code == 200
        assert hashlib.sha256(r.content).hexdigest() == rel1["_test_sha"]

        # Step 2: upload a newer release.
        v2 = f"qa039b-{unique_suffix()}"
        rel2 = _upload(shell_session, base_url, version=v2, channel="dev")
        try:
            r = requests.get(pointer_url, timeout=20, allow_redirects=True)
            assert r.status_code == 200
            actual = hashlib.sha256(r.content).hexdigest()
            assert actual == rel2["_test_sha"], (
                "pointer should now redirect to the newer release; "
                f"got sha {actual} (rel1={rel1['_test_sha']}, rel2={rel2['_test_sha']})"
            )
        finally:
            shell_session.delete(
                f"{base_url}/api/v1/admin/firmware/releases/{rel2['id']}",
                timeout=20,
            )

        # Step 3: with rel2 deleted, pointer reverts to rel1.
        r = requests.get(pointer_url, timeout=20, allow_redirects=True)
        assert r.status_code == 200
        assert hashlib.sha256(r.content).hexdigest() == rel1["_test_sha"]
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/firmware/releases/{rel1['id']}",
            timeout=20,
        )


def test_pointer_404_on_empty_channel(base_url, shell_session):
    """A channel with no releases yields 404 from the pointer
    endpoint — the freshly-flashed bootstrap firmware can detect
    'central isn't ready' and retry."""
    # Pre-clean: delete any existing 'beta' releases. (Unused in our
    # tests so this is usually a no-op, but ensures a clean state.)
    rows = shell_session.get(
        f"{base_url}/api/v1/admin/firmware/releases", timeout=10
    ).json()["data"]["releases"]
    for r in rows:
        if r["channel"] == "beta":
            shell_session.delete(
                f"{base_url}/api/v1/admin/firmware/releases/{r['id']}",
                timeout=20,
            )

    pointer_url = (
        f"{base_url}/api/v1/firmware/beta/latest"
    )
    r = requests.get(pointer_url, timeout=10, allow_redirects=False)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "no_release"


def test_pointer_unknown_channel_400(base_url):
    pointer_url = f"{base_url}/api/v1/firmware/zzz_bogus/latest"
    r = requests.get(pointer_url, timeout=10, allow_redirects=False)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unknown_channel"


def test_admin_firmware_list_renders_mirror_table(base_url, shell_session):
    """The /app/firmware page shows a `mirror(s)` expander per
    release with the per-mirror status + URL list."""
    version = f"qa039ui-{unique_suffix()}"
    rel = _upload(shell_session, base_url, version=version, channel="stable")
    try:
        body = shell_session.get(f"{base_url}/app/firmware", timeout=10).text
        # The release shows up.
        assert version in body
        # The mirror expander is present; the per-channel kind is rendered.
        assert "local_per_channel" in body
        assert "local_channel_pointer" in body
        # Pointer URL is the redirect endpoint, not a static file.
        assert "/api/v1/firmware/stable/latest" in body
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/firmware/releases/{rel['id']}",
            timeout=20,
        )
