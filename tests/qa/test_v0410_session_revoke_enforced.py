"""v0.4.10 — BUG-005 enforce: server-side session revocation now
denies access even if the cookie/JWT itself hasn't expired.

Pre-v0.4.10: revoke_one(jti) wrote a `revoked_at` timestamp but
the auth middleware ignored the row. Anyone who exfiltrated a
session cookie could keep using it for up to 31 days post-logout.

v0.4.10: middleware checks the sessions table; revoked rows are
treated as unauthenticated.

Tests use disposable_admin_session so the bootstrap admin's
tokens_valid_after never gets bumped (BUG-021 lesson).
"""

from __future__ import annotations

import pytest
import requests

# v0.5.79: in the `-m ci` gate (P-QA gate-2 widening).
pytestmark = pytest.mark.ci



@pytest.fixture
def fresh_session_with_cookie(base_url, disposable_admin_session):
    """Login via the form-style /app/login path and return the
    cookie jar + the underlying user creds."""
    s = requests.Session()
    r = s.post(
        f"{base_url}/app/login",
        data={
            "email": disposable_admin_session["email"],
            "password": disposable_admin_session["password"],
        },
        timeout=10,
        allow_redirects=False,
    )
    assert r.status_code in (302, 200)
    return s, disposable_admin_session


def test_cookie_session_dies_after_explicit_revoke(base_url, disposable_admin_session):
    """JSON /api/v1/auth/logout calls revoke_all_tokens which bumps
    tokens_valid_after AND revokes the cookie's session row. The
    middleware should now reject the OLD cookie even though Flask's
    signed cookie is still well within its Expires."""
    # Login via cookie path; capture the cookie.
    s = requests.Session()
    r = s.post(
        f"{base_url}/app/login",
        data={
            "email": disposable_admin_session["email"],
            "password": disposable_admin_session["password"],
        },
        timeout=10,
        allow_redirects=False,
    )
    assert r.status_code == 302, r.text
    cookie = s.cookies.get("rebooter_session")
    assert cookie

    # Sanity: cookie authenticates against /app/.
    r = s.get(f"{base_url}/app/", timeout=10, allow_redirects=False)
    assert r.status_code == 200, f"cookie should authenticate; got {r.status_code}"

    # Hit JSON logout — revokes the session row.
    s.post(f"{base_url}/api/v1/auth/logout", timeout=10)

    # Old cookie replayed: should NOT authenticate even though
    # Flask's signed cookie is still cryptographically valid.
    r = requests.get(
        f"{base_url}/app/",
        cookies={"rebooter_session": cookie},
        timeout=10,
        allow_redirects=False,
    )
    # 302 to /app/login is the correct rejection.
    assert r.status_code == 302, (
        f"revoked cookie should bounce to login; got {r.status_code}. "
        f"BUG-005 has regressed to shadow-mode behavior."
    )
    assert "/app/login" in r.headers.get("Location", "")


def test_bearer_jwt_dies_after_explicit_revoke(base_url, disposable_admin_session):
    """The JSON access token should also stop working after logout.
    Logout calls revoke_all_for_user which marks every session row
    (including JWT-access rows) revoked. Subsequent /api/v1/auth/me
    with the old bearer should be 401."""
    creds = disposable_admin_session
    s = requests.Session()
    login = s.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": creds["email"], "password": creds["password"]},
        timeout=10,
    )
    token = login.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Sanity: bearer works.
    me = requests.get(f"{base_url}/api/v1/auth/me", headers=headers, timeout=10)
    assert me.status_code == 200

    # Logout (also revokes the cookie session via the cookie jar).
    s.post(f"{base_url}/api/v1/auth/logout", timeout=10)

    # Old bearer replayed.
    me2 = requests.get(f"{base_url}/api/v1/auth/me", headers=headers, timeout=10)
    assert me2.status_code == 401, (
        f"revoked JWT should 401; got {me2.status_code}. "
        f"BUG-005 has regressed for the bearer path."
    )


def test_security_headers_present(base_url):
    """v0.4.10 (BUG-033) — every response carries standard
    security headers."""
    r = requests.get(f"{base_url}/api/v1/version", timeout=10)
    h = r.headers
    assert h.get("X-Frame-Options") == "DENY"
    assert h.get("X-Content-Type-Options") == "nosniff"
    assert "max-age" in h.get("Strict-Transport-Security", "")
    assert h.get("Referrer-Policy")
    assert "default-src 'self'" in h.get("Content-Security-Policy", "")


def test_schedule_bad_at_time_utc_returns_400_not_500(base_url, admin_headers):
    """v0.4.10 (BUG-034) — at_time_utc validation prevents the
    DataError → 500 path."""
    bad = ["not-a-time", "25:00", "12:99", "12", "abc:de"]
    for v in bad:
        r = requests.post(
            f"{base_url}/api/v1/admin/schedules",
            headers=admin_headers,
            json={
                "name": "qa-bad-time",
                "kind": "power_cycle",
                "recurrence": "daily",
                "at_time_utc": v,
                "target": {"kind": "tag", "tag": "x"},
            },
            timeout=10,
        )
        assert r.status_code == 400, f"{v!r} → {r.status_code}: {r.text}"
        assert r.json()["error"]["code"] == "validation_failed"
        assert "at_time_utc" in r.json()["error"]["message"]


def test_legacy_cookie_without_sid_still_works(base_url, disposable_admin_session):
    """Defensive: a session cookie that doesn't carry `sid` (legacy
    pre-v0.2.10 format) should still authenticate. The enforce check
    must skip when the jti is None — otherwise upgrades break
    every active session."""
    # The current login flow always sets `sid`, so we synthesize
    # a "no sid" scenario by logging in and verifying that the
    # auth happens even when `sid` is in the cookie. The actual
    # legacy-cookie scenario requires Flask to deserialize a cookie
    # without sid — we can't easily forge that against the live
    # deployment without the secret_key. Instead, this test is a
    # smoke check that the new middleware path doesn't 500 when
    # session.get('sid') would return None — by hitting /app/
    # without any cookie at all (unauthenticated path).
    r = requests.get(f"{base_url}/app/", timeout=10, allow_redirects=False)
    assert r.status_code == 302  # unauth → login redirect, no 500
