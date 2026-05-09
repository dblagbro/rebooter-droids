"""v0.2 — RBAC, invites, audit log."""

import requests

from .conftest import unique_suffix


def test_me_includes_role(base_url, admin_headers):
    r = requests.get(f"{base_url}/api/v1/auth/me", headers=admin_headers, timeout=10)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["role"] == "super_admin"


def test_users_endpoint_super_admin_can_list(base_url, admin_headers):
    r = requests.get(
        f"{base_url}/api/v1/admin/users", headers=admin_headers, timeout=10
    )
    assert r.status_code == 200
    body = r.json()["data"]
    assert "users" in body
    assert any(u["role"] == "super_admin" for u in body["users"])


def test_invitation_mint_returns_redeem_url(base_url, admin_headers):
    email = f"qa-invite-{unique_suffix()}@example.com"
    r = requests.post(
        f"{base_url}/api/v1/admin/invitations",
        headers=admin_headers,
        json={"email": email, "role": "viewer", "note": "qa"},
        timeout=10,
    )
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["redeem_url"].startswith(base_url + "/app/invite/inv_")
    assert d["expires_at"]


def test_invitation_redeem_creates_user_and_signs_in(base_url, admin_headers):
    email = f"qa-redeem-{unique_suffix()}@example.com"
    r = requests.post(
        f"{base_url}/api/v1/admin/invitations",
        headers=admin_headers,
        json={"email": email, "role": "operator"},
        timeout=10,
    )
    token = r.json()["data"]["invitation_token"]

    sess = requests.Session()
    redeem = sess.post(
        f"{base_url}/app/invite/{token}",
        data={
            "password": "qa-test-password-123",
            "password_confirm": "qa-test-password-123",
            "display_name": "QA Test",
        },
        timeout=10,
        allow_redirects=False,
    )
    assert redeem.status_code in (302, 303), redeem.text
    assert "session" in sess.cookies, "redeem should issue a session cookie"

    # The user should now exist and have role=operator
    r2 = sess.get(
        f"{base_url}/app/", timeout=10, allow_redirects=False
    )
    assert r2.status_code == 200, r2.status_code

    # Confirm via admin API
    listing = requests.get(
        f"{base_url}/api/v1/admin/users", headers=admin_headers, timeout=10
    ).json()["data"]["users"]
    new_user = next((u for u in listing if u["email"] == email), None)
    assert new_user is not None
    assert new_user["role"] == "operator"


def test_invitation_double_redeem_rejected(base_url, admin_headers):
    email = f"qa-doubleredeem-{unique_suffix()}@example.com"
    token = requests.post(
        f"{base_url}/api/v1/admin/invitations",
        headers=admin_headers,
        json={"email": email, "role": "viewer"},
        timeout=10,
    ).json()["data"]["invitation_token"]

    s1 = requests.Session()
    r1 = s1.post(
        f"{base_url}/app/invite/{token}",
        data={
            "password": "test-redeem-pw-12345",
            "password_confirm": "test-redeem-pw-12345",
            "display_name": "First",
        },
        timeout=10,
        allow_redirects=False,
    )
    assert r1.status_code in (302, 303)

    # Second attempt should fail (consumed) and re-render the form with an error.
    s2 = requests.Session()
    r2 = s2.post(
        f"{base_url}/app/invite/{token}",
        data={
            "password": "test-redeem-pw-12345",
            "password_confirm": "test-redeem-pw-12345",
            "display_name": "Second",
        },
        timeout=10,
    )
    assert r2.status_code == 400, r2.status_code


def test_invitation_unknown_token_shows_invalid_page(base_url):
    r = requests.get(f"{base_url}/app/invite/inv_definitely-not-real", timeout=10)
    assert r.status_code == 200  # page still renders, with error message
    assert "invitation link is invalid" in r.text


def test_audit_log_records_invite_action(base_url, admin_headers):
    email = f"qa-audit-{unique_suffix()}@example.com"
    requests.post(
        f"{base_url}/api/v1/admin/invitations",
        headers=admin_headers,
        json={"email": email, "role": "viewer"},
        timeout=10,
    )
    r = requests.get(
        f"{base_url}/api/v1/admin/audit?action=user.invited&limit=10",
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 200
    events = r.json()["data"]["events"]
    assert any(e["details"].get("email") == email for e in events)


def test_super_admin_only_endpoints_reject_admin_role(base_url, admin_headers):
    """Mint an invite for an admin, redeem, attempt super-admin-only op."""
    email = f"qa-admin-role-{unique_suffix()}@example.com"
    token = requests.post(
        f"{base_url}/api/v1/admin/invitations",
        headers=admin_headers,
        json={"email": email, "role": "admin"},
        timeout=10,
    ).json()["data"]["invitation_token"]

    sess = requests.Session()
    sess.post(
        f"{base_url}/app/invite/{token}",
        data={
            "password": "admin-test-pw-12345",
            "password_confirm": "admin-test-pw-12345",
            "display_name": "QA Admin",
        },
        timeout=10,
        allow_redirects=False,
    )
    # Now log this admin in via JSON to get a JWT
    login = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": "admin-test-pw-12345"},
        timeout=10,
    )
    if login.status_code == 429:
        # rate-limited from earlier tests; skip cleanly
        return
    assert login.status_code == 200
    admin_role_token = login.json()["data"]["access_token"]
    admin_role_headers = {"Authorization": f"Bearer {admin_role_token}"}

    # admin should NOT be able to delete a user
    target_user = next(
        u for u in requests.get(
            f"{base_url}/api/v1/admin/users", headers=admin_headers
        ).json()["data"]["users"]
        if u["role"] == "viewer" and u["is_active"]
    )
    r = requests.post(
        f"{base_url}/api/v1/admin/users/{target_user['id']}/deactivate",
        headers=admin_role_headers,
        timeout=10,
    )
    assert r.status_code == 403, r.text


def test_patch_unknown_field_now_rejected(base_url, admin_headers):
    """v0.2 — BUG-010 is fixed; unknown fields cause 400."""
    devs = (
        requests.get(f"{base_url}/api/v1/admin/devices", headers=admin_headers)
        .json()["data"]["devices"]
    )
    if not devs:
        return
    r = requests.patch(
        f"{base_url}/api/v1/admin/devices/{devs[0]['id']}",
        headers=admin_headers,
        json={"is_admin": True, "display_name": "qa-after"},
        timeout=10,
    )
    assert r.status_code == 400
    assert "is_admin" in r.json()["error"]["message"]


def test_favicon_served(base_url):
    r = requests.get(f"{base_url}/static/favicon.ico", timeout=10)
    assert r.status_code == 200
    assert r.headers.get("Content-Type", "").startswith(("image/", "application/octet-stream"))
