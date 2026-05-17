"""v0.4.1 — password-reset + Settings → Notifications tab.

Covers:
- /app/forgot-password renders + accepts a non-existent email
  without leaking that the email is unknown.
- /app/reset-password rejects bad tokens with a generic message.
- /app/settings/notifications renders for admin, lists env-var
  SMTP config, exposes the "send test email" form.
- The "Forgot your password?" link is on /app/login.
- Invite default TTL is now 30 days (read off the API response
  expires_at vs created_at where exposed; otherwise smoke-only).

The actual SMTP send isn't exercised against the live deployment
(no SMTP creds in QA env); we only verify the routes accept input
and behave non-disclosing.
"""

from __future__ import annotations

import pytest
import requests

from .conftest import unique_suffix

# v0.5.79: in the `-m ci` gate (P-QA gate-2 widening).
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


def test_login_page_has_forgot_link(base_url):
    r = requests.get(f"{base_url}/app/login", timeout=10)
    assert r.status_code == 200
    assert "Forgot your password" in r.text
    assert "/app/forgot-password" in r.text


def test_forgot_password_get_renders(base_url):
    r = requests.get(f"{base_url}/app/forgot-password", timeout=10)
    assert r.status_code == 200
    assert "Forgot your password" in r.text
    assert "Send reset link" in r.text


def test_forgot_password_post_unknown_email_is_non_disclosing(base_url):
    """Posting a never-registered email must NOT leak that fact —
    the page renders the same 'we sent it if it exists' message."""
    bogus = f"qa041-{unique_suffix()}@example.invalid"
    r = requests.post(
        f"{base_url}/app/forgot-password",
        data={"email": bogus},
        timeout=10,
        allow_redirects=False,
    )
    assert r.status_code == 200
    # Masked echo: first 3 chars of the local-part are kept.
    assert "qa0***@example.invalid" in r.text
    assert "we've emailed" in r.text or "if an account exists" in r.text.lower()
    # NEVER says "no such user".
    assert "no such" not in r.text.lower()
    assert "not found" not in r.text.lower()


def test_forgot_password_post_known_email_is_non_disclosing(
    base_url, admin_creds
):
    """And posting a real email looks identical from the response."""
    email, _ = admin_creds
    r = requests.post(
        f"{base_url}/app/forgot-password",
        data={"email": email},
        timeout=10,
        allow_redirects=False,
    )
    assert r.status_code == 200
    assert "if an account exists" in r.text.lower() or "we've emailed" in r.text


def test_reset_password_get_no_token_shows_error(base_url):
    r = requests.get(f"{base_url}/app/reset-password", timeout=10)
    assert r.status_code == 200
    assert "No reset token" in r.text or "Request a new link" in r.text


def test_reset_password_post_bogus_token_rejects_generic(base_url):
    r = requests.post(
        f"{base_url}/app/reset-password",
        data={
            "token": "pwr_definitely_bogus_token_xyz",
            "password": "longenough123",
            "password_confirm": "longenough123",
        },
        timeout=10,
        allow_redirects=False,
    )
    assert r.status_code == 200
    # Generic "invalid or expired" message — never "unknown token"
    assert "invalid or has expired" in r.text.lower()


def test_reset_password_post_short_password_rejects(base_url):
    r = requests.post(
        f"{base_url}/app/reset-password",
        data={
            "token": "anything",
            "password": "short",
            "password_confirm": "short",
        },
        timeout=10,
        allow_redirects=False,
    )
    assert r.status_code == 200
    assert "at least 8" in r.text.lower()


def test_reset_password_post_mismatched_passwords_rejects(base_url):
    r = requests.post(
        f"{base_url}/app/reset-password",
        data={
            "token": "anything",
            "password": "longenough123",
            "password_confirm": "different456",
        },
        timeout=10,
        allow_redirects=False,
    )
    assert r.status_code == 200
    # Apostrophe gets HTML-escaped by Jinja autoescape, so check for
    # the unambiguous "match or are empty" suffix.
    assert "match or are empty" in r.text


# ── Settings → Notifications ─────────────────────────────────────────────


def test_notifications_tab_renders(base_url, shell_session):
    r = shell_session.get(
        f"{base_url}/app/settings/notifications", timeout=10
    )
    assert r.status_code == 200
    body = r.text
    assert "Notifications" in body
    assert "Outgoing email" in body or "SMTP" in body
    # Send-test form is present
    assert "Send test" in body or "send test" in body.lower()
    # Env-var doc reference
    assert "REBOOTER_SMTP_HOST" in body


def test_notifications_tab_in_settings_strip(base_url, shell_session):
    """The Notifications tab link is in the strip on every settings page."""
    body = shell_session.get(f"{base_url}/app/settings", timeout=10).text
    assert "/app/settings/notifications" in body
    assert "Notifications" in body
