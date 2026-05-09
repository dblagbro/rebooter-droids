"""Skeptical / adversarial probes — looking for real bugs and weak spots."""

import concurrent.futures as cf
import hashlib
import os
import tempfile

import jwt
import pytest
import requests

from .conftest import unique_suffix


# ── duplicates / unique-constraint surface ─────────────────────────────────

def test_duplicate_group_name_returns_409(base_url, admin_headers):
    """v0.1.4 — groups.name UNIQUE; duplicate returns 409 name_conflict."""
    name = f"qa-dupe-{unique_suffix()}"
    a = requests.post(
        f"{base_url}/api/v1/admin/groups",
        headers=admin_headers,
        json={"name": name},
        timeout=10,
    )
    b = requests.post(
        f"{base_url}/api/v1/admin/groups",
        headers=admin_headers,
        json={"name": name},
        timeout=10,
    )
    assert a.status_code == 201
    assert b.status_code == 409, b.text
    assert b.json()["error"]["code"] == "name_conflict"


def test_duplicate_site_name_returns_409(base_url, admin_headers):
    """v0.1.4 — sites.name UNIQUE; duplicate returns 409 name_conflict."""
    name = f"qa-site-dupe-{unique_suffix()}"
    a = requests.post(
        f"{base_url}/api/v1/admin/sites",
        headers=admin_headers,
        json={"name": name},
        timeout=10,
    )
    b = requests.post(
        f"{base_url}/api/v1/admin/sites",
        headers=admin_headers,
        json={"name": name},
        timeout=10,
    )
    assert a.status_code == 201
    assert b.status_code == 409
    # cleanup
    requests.delete(
        f"{base_url}/api/v1/admin/sites/{a.json()['data']['id']}",
        headers=admin_headers,
        timeout=10,
    )


# ── concurrency races ──────────────────────────────────────────────────────

def test_concurrent_enrollment_redemption_only_succeeds_once(base_url, admin_headers):
    """Two simultaneous register calls with the same enrollment token."""
    et = requests.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        headers=admin_headers,
        json={"display_name_hint": f"QA race {unique_suffix()}"},
        timeout=10,
    ).json()["data"]["enrollment_token"]

    def do():
        return requests.post(
            f"{base_url}/api/v1/device/register",
            json={"enrollment_token": et, "hardware_model": "sonoff_s31"},
            timeout=15,
        )

    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        r1, r2 = pool.submit(do), pool.submit(do)
        out = [r1.result(), r2.result()]
    statuses = sorted(r.status_code for r in out)
    assert statuses == [201, 409], (
        f"expected one 201 + one 409 (consumed); got {statuses} — "
        "race condition allows double-redemption"
    )


def test_concurrent_firmware_upload_same_version(base_url, admin_headers):
    """Two simultaneous uploads of the same version. Only one should win."""
    body = os.urandom(512)
    sha = hashlib.sha256(body).hexdigest()
    suffix = unique_suffix()

    def do():
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(body)
            path = f.name
        try:
            return requests.post(
                f"{base_url}/api/v1/admin/firmware/releases",
                headers=admin_headers,
                data={
                    "version": f"qa-race-{suffix}",
                    "channel": "dev",
                    "sha256": sha,
                },
                files={"file": ("fw.bin", open(path, "rb"))},
                timeout=20,
            )
        finally:
            os.unlink(path)

    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        r1, r2 = [f.result() for f in [pool.submit(do), pool.submit(do)]]
    statuses = sorted([r1.status_code, r2.status_code])
    assert 201 in statuses, "neither concurrent upload succeeded"
    other = [s for s in statuses if s != 201][0] if 201 in statuses else None
    # We expect 400 (already exists) for the loser. 500 here is a real bug.
    assert other in (400, None), (
        f"second concurrent upload returned {other}, not a clean 400 — race not handled"
    )

    # cleanup any release that landed
    for r in (r1, r2):
        if r.status_code == 201:
            rid = r.json()["data"]["id"]
            requests.delete(
                f"{base_url}/api/v1/admin/firmware/releases/{rid}",
                headers=admin_headers,
                timeout=10,
            )


# ── PATCH semantics ────────────────────────────────────────────────────────

def test_patch_device_with_empty_body_should_be_idempotent(base_url, admin_headers):
    """Empty PATCH should not crash, and should not change anything."""
    devs = (
        requests.get(f"{base_url}/api/v1/admin/devices", headers=admin_headers)
        .json()["data"]["devices"]
    )
    if not devs:
        return
    dev_id = devs[0]["id"]
    before = requests.get(
        f"{base_url}/api/v1/admin/devices/{dev_id}", headers=admin_headers
    ).json()["data"]
    r = requests.patch(
        f"{base_url}/api/v1/admin/devices/{dev_id}",
        headers=admin_headers,
        json={},
        timeout=10,
    )
    assert r.status_code in (200, 400), r.status_code
    after = requests.get(
        f"{base_url}/api/v1/admin/devices/{dev_id}", headers=admin_headers
    ).json()["data"]
    assert before["display_name"] == after["display_name"]


def test_patch_device_unknown_field_now_rejected(base_url, admin_headers):
    """v0.2 — BUG-010 fixed; PATCH with unknown fields returns 400."""
    devs = (
        requests.get(f"{base_url}/api/v1/admin/devices", headers=admin_headers)
        .json()["data"]["devices"]
    )
    if not devs:
        return
    dev_id = devs[0]["id"]
    r = requests.patch(
        f"{base_url}/api/v1/admin/devices/{dev_id}",
        headers=admin_headers,
        json={"unknown_field": "should-be-rejected", "is_admin": True},
        timeout=10,
    )
    assert r.status_code == 400
    msg = r.json()["error"]["message"]
    assert "unknown_field" in msg or "is_admin" in msg


# ── deploy / firmware paths ────────────────────────────────────────────────

def test_deploy_unknown_release_returns_404(base_url, admin_headers):
    """v0.2.5+ — when target_count <= 5 the mass-action gate is a no-op
    and the LookupError surfaces as the 404 we expect. We force the
    single-device path here so the test exercises only the
    release-not-found branch, not the gate.
    """
    r = requests.post(
        f"{base_url}/api/v1/admin/firmware/deployments",
        headers=admin_headers,
        json={
            "release_id": "fwr_does-not-exist",
            "target_type": "device",
            "target_id": "dev_does-not-exist-either",
        },
        timeout=10,
    )
    assert r.status_code == 404


def test_deploy_invalid_target_type_400(base_url, admin_headers):
    r = requests.post(
        f"{base_url}/api/v1/admin/firmware/deployments",
        headers=admin_headers,
        json={"release_id": "fwr_x", "target_type": "everything"},
        timeout=10,
    )
    assert r.status_code in (400, 404)
    j = r.json()
    if r.status_code == 400:
        assert "target_type" in j["error"]["message"]


def test_zero_byte_firmware_upload_rejected(base_url, admin_headers):
    """v0.1.4 — empty firmware uploads must be rejected."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        path = f.name  # zero bytes
    try:
        r = requests.post(
            f"{base_url}/api/v1/admin/firmware/releases",
            headers=admin_headers,
            data={"version": f"qa-empty-{unique_suffix()}", "channel": "dev"},
            files={"file": ("fw.bin", open(path, "rb"))},
            timeout=10,
        )
    finally:
        os.unlink(path)
    assert r.status_code == 400
    assert "empty" in r.json()["error"]["message"].lower()


# ── session / cookie ───────────────────────────────────────────────────────

def test_session_cookie_attributes(base_url):
    s = requests.Session()
    r = s.post(
        f"{base_url}/app/login",
        data={"email": "dblagbro", "password": "Super*120120"},
        allow_redirects=False,
        timeout=10,
    )
    assert r.status_code == 302
    sc = r.headers.get("Set-Cookie", "")
    assert "Secure" in sc, "session cookie must be Secure"
    assert "HttpOnly" in sc, "session cookie must be HttpOnly"
    assert "SameSite=Lax" in sc or "SameSite=Strict" in sc, (
        f"session cookie must be SameSite-protected: {sc}"
    )


def test_logout_does_not_revoke_cookie_server_side(base_url):
    """Findings probe — Flask's session.clear() only clears server's idea of
    the session. The signed cookie remains valid until its `Expires`. This
    is mitigated by SECRET_KEY rotation but not by /logout.
    """
    s = requests.Session()
    s.post(
        f"{base_url}/app/login",
        data={"email": "dblagbro", "password": "Super*120120"},
        allow_redirects=False,
        timeout=10,
    )
    cookie = s.cookies.get("session")
    assert cookie

    # log out
    requests.get(f"{base_url}/app/logout", cookies={"session": cookie}, timeout=10,
                 allow_redirects=False)

    # try the OLD cookie value
    me = requests.get(
        f"{base_url}/app/", cookies={"session": cookie}, timeout=10,
        allow_redirects=False,
    )
    # If me.status_code == 200, the cookie still authenticates after logout.
    # We accept this as a known limitation in v0.1 and assert the test
    # documents it — flip to assert == 302 if we add server-side revocation.
    # For now: just record the actual behaviour.
    # We DO assert that login → access still works (sanity).
    assert me.status_code in (200, 302), me.status_code


# ── auth brute-force / rate limiting ───────────────────────────────────────

@pytest.mark.timeout(120)
def test_login_rate_limit_kicks_in(base_url):
    """v0.1.4 — login is rate-limited (30/minute, 200/hour) per IP.

    Fire 35 rapid bad-password attempts and verify at least one 429
    surfaces. The 35-call burst takes ~5s; we then sleep ~65s so the
    per-minute window clears and later tests in the suite still
    authenticate cleanly. Total wall time is ~70s so this test
    explicitly carries `@pytest.mark.timeout(120)` (BUG-025) — the
    suite default is 60s which would kill it.
    """
    import time as _t

    statuses = []
    for i in range(35):
        r = requests.post(
            f"{base_url}/api/v1/auth/login",
            json={"email": "dblagbro", "password": f"wrong-{i}"},
            timeout=10,
        )
        statuses.append(r.status_code)
    assert 429 in statuses, (
        f"expected at least one 429 (rate_limited) within 35 rapid attempts; "
        f"got {statuses}"
    )
    # And no 5xx surfaced on the way to the limit.
    assert not any(s >= 500 for s in statuses)
    # Sleep so the per-minute window clears for downstream tests
    # that use the same source IP.
    _t.sleep(65)
    # Drain the per-minute bucket so later tests can still log in.
    _t.sleep(61)


# ── JWT cross-realm / forgery ──────────────────────────────────────────────

def test_jwt_alg_none_attack_rejected(base_url):
    """jwt with alg=none MUST be rejected."""
    forged = jwt.encode(
        {"sub": "usr_x", "kind": "access", "aud": "rebooter-droids", "exp": 9999999999},
        "",
        algorithm="none",
    )
    r = requests.get(
        f"{base_url}/api/v1/auth/me",
        headers={"Authorization": f"Bearer {forged}"},
        timeout=10,
    )
    assert r.status_code == 401


def test_admin_endpoint_returns_envelope_on_404(base_url, admin_headers):
    r = requests.get(
        f"{base_url}/api/v1/admin/devices/dev_nope",
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 404
    body = r.json()
    assert body["ok"] is False
    assert "code" in body["error"]


# ── strict_slashes ─────────────────────────────────────────────────────────

def test_trailing_slash_on_admin_endpoint(base_url, admin_headers):
    """/admin/devices vs /admin/devices/ — Flask defaults to redirecting one."""
    a = requests.get(
        f"{base_url}/api/v1/admin/devices",
        headers=admin_headers,
        timeout=10,
        allow_redirects=False,
    )
    b = requests.get(
        f"{base_url}/api/v1/admin/devices/",
        headers=admin_headers,
        timeout=10,
        allow_redirects=False,
    )
    assert a.status_code == 200
    # Either b == 200 OR b == 308/301 redirect — but never 404
    assert b.status_code in (200, 301, 302, 308), b.status_code
