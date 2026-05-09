"""Smoke tests — fast top-level confidence."""

import requests


def test_version_endpoint_no_auth(base_url):
    r = requests.get(f"{base_url}/api/v1/version", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["service"] == "rebooter-droids"
    assert body["data"]["version"]
    assert body["data"]["server_time"].endswith("Z")


def test_login_with_full_email(base_url):
    r = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": "dblagbro@gmail.com", "password": "Super*120120"},
        timeout=10,
    )
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    for k in ("access_token", "refresh_token", "token_type", "user"):
        assert k in j["data"], f"missing {k}"


def test_login_with_bare_username(base_url):
    """v0.1.2 — both 'dblagbro' and 'dblagbro@gmail.com' must work."""
    r = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": "dblagbro", "password": "Super*120120"},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_login_rejects_bad_password(base_url):
    r = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": "dblagbro@gmail.com", "password": "definitely-wrong"},
        timeout=10,
    )
    assert r.status_code == 401
    j = r.json()
    assert j["ok"] is False
    assert j["error"]["code"] == "auth_invalid"


def test_login_rejects_empty_body(base_url):
    r = requests.post(f"{base_url}/api/v1/auth/login", json={}, timeout=10)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_failed"


def test_admin_unauth(base_url):
    r = requests.get(f"{base_url}/api/v1/admin/devices", timeout=10)
    assert r.status_code == 401


def test_admin_with_token(base_url, admin_headers):
    r = requests.get(
        f"{base_url}/api/v1/admin/devices", headers=admin_headers, timeout=10
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "devices" in body["data"]
    assert "total" in body["data"]


def test_admin_me_includes_super_admin(base_url, admin_headers):
    r = requests.get(
        f"{base_url}/api/v1/auth/me", headers=admin_headers, timeout=10
    )
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["is_admin"] is True
    assert d["is_super_admin"] is True
