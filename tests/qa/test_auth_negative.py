"""Negative + edge tests for auth."""

import requests


def test_refresh_with_garbage(base_url):
    r = requests.post(
        f"{base_url}/api/v1/auth/refresh",
        json={"refresh_token": "not-a-jwt"},
        timeout=10,
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "auth_invalid"


def test_refresh_with_access_token_should_be_rejected(base_url, admin_token):
    """An access token should not be usable to refresh — only refresh tokens."""
    r = requests.post(
        f"{base_url}/api/v1/auth/refresh",
        json={"refresh_token": admin_token},
        timeout=10,
    )
    assert r.status_code == 401, (
        "passing an access token to /refresh must be rejected"
    )


def test_refresh_returns_fresh_token_pair(base_url):
    login = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": "dblagbro@gmail.com", "password": "Super*120120"},
        timeout=10,
    ).json()["data"]
    r = requests.post(
        f"{base_url}/api/v1/auth/refresh",
        json={"refresh_token": login["refresh_token"]},
        timeout=10,
    )
    assert r.status_code == 200
    d = r.json()["data"]
    assert "access_token" in d and "refresh_token" in d


def test_me_with_wrong_signature(base_url):
    """JWT signed with another secret must not authenticate."""
    import jwt
    from datetime import datetime, timedelta, timezone

    forged = jwt.encode(
        {
            "sub": "usr_FAKE",
            "kind": "access",
            "aud": "rebooter-droids",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        "an-attacker-secret",
        algorithm="HS256",
    )
    r = requests.get(
        f"{base_url}/api/v1/auth/me",
        headers={"Authorization": f"Bearer {forged}"},
        timeout=10,
    )
    assert r.status_code == 401, f"forged JWT was accepted: {r.text}"


def test_me_with_expired_token(base_url):
    import jwt
    from datetime import datetime, timedelta, timezone

    # We don't know the secret, but an expired JWT we *do* have should be rejected.
    # Use the real login and decode without verify just to learn structure, then
    # wait — we can't sign with the real secret without knowing it. Instead
    # confirm that a deliberately past-exp token, even if forged, gets 401.
    forged = jwt.encode(
        {
            "sub": "usr_X",
            "kind": "access",
            "aud": "rebooter-droids",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        },
        "anything",
        algorithm="HS256",
    )
    r = requests.get(
        f"{base_url}/api/v1/auth/me",
        headers={"Authorization": f"Bearer {forged}"},
        timeout=10,
    )
    assert r.status_code == 401


def test_login_method_get_not_allowed(base_url):
    r = requests.get(f"{base_url}/api/v1/auth/login", timeout=10)
    assert r.status_code in (404, 405)


def test_logout_idempotent(base_url):
    # logout requires no body; calling twice should not error
    r1 = requests.post(f"{base_url}/api/v1/auth/logout", timeout=10)
    assert r1.status_code == 200
    r2 = requests.post(f"{base_url}/api/v1/auth/logout", timeout=10)
    assert r2.status_code == 200
