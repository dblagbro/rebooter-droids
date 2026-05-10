"""v0.2.10 — server-side session shadow mode (R7-shadow).

Every UI cookie login and every JWT access/refresh issuance now writes
a `user_sessions` row server-side. v0.2.10 does NOT yet enforce
authorisation against this table — that flip lands in a future minor
behind `REBOOTER_SESSIONS_ENFORCE`. These tests assert the *write
side* of the contract:

- JWT payloads contain a `jti` claim.
- Login still works (no regression on the auth path).
- The auth-API contract (`/api/v1/auth/login` → `/me` → `/logout`) is
  unchanged at the user-visible level.

Direct assertions about the new table are intentionally light-touch:
the QA suite hits the live deployment over HTTPS and we don't expose
a "list my sessions" endpoint yet (queued for the v0.2.11 surface).
The presence of the `jti` claim on the JWT is the load-bearing public
contract change for this minor.
"""

from __future__ import annotations

import base64
import json

import requests


def _decode_jwt_payload(jwt_str: str) -> dict:
    # Quick non-verifying decode for inspecting claims.
    parts = jwt_str.split(".")
    assert len(parts) == 3, jwt_str
    pad = "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(parts[1] + pad))


def test_login_still_returns_access_and_refresh_tokens(base_url, admin_creds):
    email, pw = admin_creds
    r = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "Bearer"


def test_access_token_payload_contains_jti(base_url, admin_creds):
    email, pw = admin_creds
    data = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    ).json()["data"]
    payload = _decode_jwt_payload(data["access_token"])
    assert "jti" in payload, payload
    assert isinstance(payload["jti"], str) and payload["jti"]
    # jti must be unique per issuance — two consecutive logins must
    # never collide.
    second = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    ).json()["data"]
    assert _decode_jwt_payload(second["access_token"])["jti"] != payload["jti"]


def test_refresh_token_payload_contains_jti(base_url, admin_creds):
    email, pw = admin_creds
    data = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    ).json()["data"]
    payload = _decode_jwt_payload(data["refresh_token"])
    assert "jti" in payload, payload


def test_refreshed_token_gets_a_new_jti(base_url, admin_creds):
    email, pw = admin_creds
    initial = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    ).json()["data"]
    refresh_resp = requests.post(
        f"{base_url}/api/v1/auth/refresh",
        json={"refresh_token": initial["refresh_token"]},
        timeout=10,
    )
    assert refresh_resp.status_code == 200, refresh_resp.text
    new_access = refresh_resp.json()["data"]["access_token"]
    new_jti = _decode_jwt_payload(new_access)["jti"]
    old_jti = _decode_jwt_payload(initial["access_token"])["jti"]
    assert new_jti != old_jti, "refresh must mint a new jti for the access token"


def test_login_does_not_break_on_session_write_failure(base_url, admin_creds):
    """Login is best-effort with respect to session recording — even
    under heavy concurrent login load, every legitimate password match
    must yield 200 + tokens. We can't simulate a DB failure from
    outside, but we can assert that 5 quick consecutive logins all
    succeed; if the session-record path were blocking, a unique-constraint
    or transactional failure would surface here."""
    email, pw = admin_creds
    for _ in range(5):
        r = requests.post(
            f"{base_url}/api/v1/auth/login",
            json={"email": email, "password": pw},
            timeout=10,
        )
        assert r.status_code == 200, r.text


def test_logout_does_not_break_subsequent_login(base_url, disposable_admin_session):
    """Logout marks the cookie session revoked but does not prevent the
    next login from succeeding. (Shadow mode: no read-path enforcement.)

    v0.4.4 (BUG-021): use disposable_admin_session — calling
    /api/v1/auth/logout for the bootstrap admin would bump that
    user's tokens_valid_after and cascade-invalidate the session-
    scoped admin_token used by the rest of the suite.
    """
    email = disposable_admin_session["email"]
    pw = disposable_admin_session["password"]
    s = requests.Session()
    r1 = s.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    )
    assert r1.status_code == 200
    r2 = s.post(f"{base_url}/api/v1/auth/logout", timeout=10)
    assert r2.status_code == 200
    r3 = s.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    )
    assert r3.status_code == 200
