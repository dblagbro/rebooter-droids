"""v0.3.7 — stale cookie must not cause a redirect loop.

Operator-reported `ERR_TOO_MANY_REDIRECTS` when the browser had a
cookie carrying a user_id whose `iat` was older than the user's
`tokens_valid_after` cutoff (the freshness check). Pre-v0.3.7,
admin_required_ui rejected the cookie and redirected to /app/login,
but login_page saw user_id-still-in-cookie and redirected back —
infinite loop.

Tests:
- Forge a stale cookie scenario by:
  1. Login → capture the cookie
  2. Trigger /api/v1/auth/logout → server bumps tokens_valid_after
  3. Replay the OLD cookie against /app/login
  → server should: clear the cookie + render the login form (not
     redirect back to /app/).
- Ensure /app/ with a stale cookie also lands on /app/login
  cleanly (single redirect, not a loop) — verified by curl with
  --max-redirs 5.
"""

from __future__ import annotations

import pytest
import requests

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


def _make_stale_cookie_session(base_url: str, email: str, pw: str) -> requests.Session:
    """Forge a stale-cookie scenario: login (cookie iat=T1), wait
    >1.5s so int(iat) < int(tokens_valid_after) after the bump,
    then logout (bumps tokens_valid_after), then re-inject the
    pre-logout cookie.

    v0.4.4 (BUG-021): callers must pass DISPOSABLE creds, not the
    bootstrap admin's — calling /api/v1/auth/logout bumps that
    user's `tokens_valid_after` and would corrupt the shared
    session-scoped admin_token used by the rest of the suite.
    """
    import time

    s = requests.Session()
    s.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    )
    cookie_jar_before = dict(s.cookies)
    # Sleep so that the int-second truncated cookie iat is strictly
    # less than the int-second truncated tokens_valid_after that
    # the next logout will set.
    time.sleep(1.5)
    s.post(f"{base_url}/api/v1/auth/logout", timeout=10)
    # Re-inject the pre-logout cookie — simulates a browser whose
    # cookie cache still has the old value while the server has
    # advanced its cutoff.
    for name, value in cookie_jar_before.items():
        s.cookies.set(name, value, domain="voipguru.org")
    return s


def test_app_root_with_stale_cookie_terminates(base_url, disposable_admin_session):
    """A stale cookie hitting /app/ resolves in at most one redirect
    (to /app/login) and renders the login form. No loop."""
    email = disposable_admin_session["email"]
    pw = disposable_admin_session["password"]
    s = _make_stale_cookie_session(base_url, email, pw)

    # Hit /app/ with the stale cookie. allow_redirects=True follows
    # up to 30 (requests' default). If the loop bug is back, this
    # will hit the limit and raise.
    r = s.get(
        f"{base_url}/app/",
        timeout=15,
        allow_redirects=True,
    )
    assert r.status_code == 200
    assert "/app/login" in r.url, f"final URL was {r.url}"
    assert 'name="email"' in r.text and 'name="password"' in r.text


def test_login_page_clears_stale_cookie(base_url, disposable_admin_session):
    """GET /app/login with a stale cookie does NOT redirect to
    /app/. It clears the cookie and renders the form (single hop)."""
    email = disposable_admin_session["email"]
    pw = disposable_admin_session["password"]
    s = _make_stale_cookie_session(base_url, email, pw)

    r = s.get(
        f"{base_url}/app/login",
        timeout=10,
        allow_redirects=False,
    )
    assert r.status_code == 200, (
        f"login_page should render the form (200) on a stale "
        f"cookie, got {r.status_code} {r.headers.get('Location')}"
    )
    assert 'name="email"' in r.text
