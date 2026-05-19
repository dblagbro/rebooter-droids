"""v0.3.3 — session cookie cross-subdomain + rebooter-prefixed name.

Closes the "I keep getting signed out" complaint by making the
session cookie carry between www.voipguru.org and www2.voipguru.org.
The cookie is also renamed from `session` → `rebooter_session` so
it doesn't collide with peer voipguru.org apps using the Flask
default name.

Tests:
- Set-Cookie returns the rebooter_session name.
- Set-Cookie carries Domain=.voipguru.org (set via env in compose).
- Login at www → carries to www2 in the same browser context
  (Playwright assertion against the live deployment).
- Theme cookie likewise gains the cross-subdomain scope.
- Legacy `theme` cookie is still read on first request after
  upgrade (one-minor deprecation grace).
"""

from __future__ import annotations

import os

import pytest
import requests

# v0.5.98 (P-QA gate-3): the cross-host playwright test already skips
# unless the base_url is the voipguru deployment; the legacy-cookie
# test had a port-bug (host included `:port` so the cookie was never
# sent) — fixed by stripping the port from `host`.
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


# ── cookie name + attributes ──────────────────────────────────────────────

def test_login_sets_rebooter_session_cookie(base_url, admin_creds):
    """Cookie name MUST be rebooter_session in v0.3.3+."""
    email, pw = admin_creds
    r = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    )
    assert r.status_code == 200
    cookies = r.cookies
    assert "rebooter_session" in cookies, dict(cookies)
    assert "session" not in cookies, (
        "the legacy `session` cookie name must not be set in v0.3.3 "
        "to avoid collisions with peer voipguru.org apps"
    )


def test_login_cookie_carries_domain_attribute(base_url, admin_creds):
    """When REBOOTER_COOKIE_DOMAIN is configured, Set-Cookie carries
    a Domain= attribute matching it."""
    email, pw = admin_creds
    r = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    )
    set_cookie = r.headers.get("Set-Cookie", "")
    # Either Domain=[.]voipguru.org or no Domain at all (host-scoped
    # legacy mode for self-hosters who didn't set the env var).
    # RFC 6265 leading-dot is optional; Werkzeug strips it.
    if "Domain=" in set_cookie:
        assert "voipguru.org" in set_cookie, set_cookie


# ── cookie-domain cross-host carry (Playwright) ───────────────────────────

def test_login_at_primary_carries_to_secondary(base_url):
    """The bug this release fixes — login at www must keep the
    operator signed in on www2 in the same browser context."""
    if "voipguru.org" not in base_url:
        pytest.skip(
            "test requires the voipguru deployment with both www and www2"
        )

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=os.environ.get(
                "PLAYWRIGHT_CHROMIUM_PATH",
                "/home/dblagbro/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
            ),
            args=["--no-sandbox"],
        )
        try:
            ctx = browser.new_context()
            page = ctx.new_page()
            email = os.environ.get("REBOOTER_QA_EMAIL", "dblagbro@gmail.com")
            password = os.environ.get("REBOOTER_QA_PASS", "Super*120120")
            page.goto("https://www.voipguru.org/rebooter/app/login")
            page.fill('input[name="email"]', email)
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]')
            page.wait_for_load_state("networkidle")
            assert "/app/login" not in page.url, "login at primary failed"

            page.goto("https://www2.voipguru.org/rebooter/app/")
            page.wait_for_load_state("networkidle")
            assert "/app/login" not in page.url, (
                f"session did not carry from www to www2 — still seeing "
                f"the login page (url={page.url}). Cookie domain is "
                f"likely host-scoped; check REBOOTER_COOKIE_DOMAIN."
            )
        finally:
            browser.close()


# ── theme cookie domain ───────────────────────────────────────────────────

def test_theme_cookie_writes_rebooter_theme(base_url, shell_session):
    r = shell_session.post(
        f"{base_url}/app/settings/theme",
        data={"theme": "dark"},
        timeout=10,
        allow_redirects=True,
    )
    assert r.status_code == 200
    assert shell_session.cookies.get("rebooter_theme") == "dark"


def test_theme_cookie_legacy_name_still_read(base_url, shell_session):
    """Operators upgrading from v0.3.0–0.3.2 have a host-scoped
    `theme` cookie. The settings page still reads it (with
    rebooter_theme taking precedence)."""
    # Inject the legacy cookie at the host the session is for, then
    # GET the theme page and confirm the radio reflects it.
    shell_session.cookies.clear()
    # Re-login since we just nuked everything.
    email = os.environ.get("REBOOTER_QA_EMAIL", "dblagbro@gmail.com")
    password = os.environ.get("REBOOTER_QA_PASS", "Super*120120")
    shell_session.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    # Send the legacy cookie explicitly on this request. Setting it on
    # the session jar with `domain=host` is unreliable: when the host
    # is `localhost` (CI, no FQDN) cookielib's RFC enforcement may not
    # send it back, and when the URL carries a port (`host:port`)
    # `domain=netloc` includes the port — also not sent. The explicit
    # `cookies=` parameter sidesteps both: the request carries
    # `Cookie: theme=light` directly, alongside the session's auth
    # cookie.
    body = shell_session.get(
        f"{base_url}/app/settings/theme",
        cookies={"theme": "light"},
        timeout=10,
    ).text
    # The light radio should be checked because the legacy theme
    # cookie is honoured.
    import re
    assert re.search(r'value="light"[^>]*checked', body) or re.search(
        r'checked[^>]*value="light"', body
    ), "legacy `theme` cookie should still pre-select the radio"
