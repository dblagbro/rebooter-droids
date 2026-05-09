"""Shared QA fixtures.

Suite targets the live deployment at https://www.voipguru.org/rebooter
unless REBOOTER_QA_BASE is overridden.
"""

import os
import re
import time

import pytest
import requests

BASE = os.environ.get("REBOOTER_QA_BASE", "https://www.voipguru.org/rebooter")
ADMIN_EMAIL = os.environ.get("REBOOTER_QA_EMAIL", "dblagbro@gmail.com")
ADMIN_PASS = os.environ.get(
    "REBOOTER_QA_PASS", "Super*120120"
)


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE


@pytest.fixture(scope="session")
def admin_creds():
    return ADMIN_EMAIL, ADMIN_PASS


@pytest.fixture(scope="session")
def admin_token(base_url, admin_creds) -> str:
    """Bearer JWT for the bootstrap admin. Session-scoped — one
    login per suite run.

    Tests that mutate auth state on the bootstrap admin
    (`/api/v1/auth/logout`, password-reset consume, revoke-all)
    MUST use the `disposable_admin_session` fixture instead — that
    one gives back a freshly-provisioned admin user whose token
    bumps don't affect this shared session token (BUG-021).
    """
    email, pw = admin_creds
    r = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["data"]["access_token"]


@pytest.fixture
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def disposable_admin_session(base_url, admin_token):
    """v0.4.4 — fresh admin user + logged-in requests.Session.

    Provisions a brand-new admin via the admin API, returns a
    requests.Session that's already authenticated as that user via
    cookie + JWT. After the test, attempts to delete the user
    (cleanup is best-effort).

    Use this in tests that call /api/v1/auth/logout, redeem a
    password-reset, or otherwise bump the user's
    `tokens_valid_after` — those mutations would corrupt the
    shared bootstrap-admin token used by the rest of the suite.
    """
    import secrets

    email = f"qa-isolated-{secrets.token_hex(6)}@voipguru.org"
    password = "qa-test-Pa55*" + secrets.token_hex(4)

    # Create the user via the bootstrap admin's bearer token.
    create = requests.post(
        f"{base_url}/api/v1/admin/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": email,
            "password": password,
            "display_name": "QA Isolated",
            "role": "admin",
        },
        timeout=10,
    )
    if create.status_code not in (200, 201):
        pytest.skip(f"could not provision disposable admin: {create.status_code} {create.text}")

    user_id = create.json()["data"]["id"]

    # Log them in via the same JSON path the suite uses.
    sess = requests.Session()
    login = sess.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    if login.status_code != 200:
        pytest.skip(f"could not log in disposable admin: {login.status_code} {login.text}")
    token = login.json()["data"]["access_token"]
    sess.headers.update({"Authorization": f"Bearer {token}"})

    yield {
        "session": sess,
        "email": email,
        "password": password,
        "token": token,
        "user_id": user_id,
    }

    # Best-effort cleanup. The user might already be gone if the
    # test deleted them; ignore failures.
    try:
        requests.delete(
            f"{base_url}/api/v1/admin/users/{user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
    except Exception:
        pass


@pytest.fixture
def chromium_browser():
    """Headless Chromium tied to the playwright-installed binary."""
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
            yield browser
        finally:
            browser.close()


# ── Viewport profiles for the responsive pass ───────────────────────────────
#
# Mobile + tablet viewport widths picked from the most-common real-device
# bands. Values match what Chromium's DevTools "device toolbar" presets
# use — so manual reproduction of a failure is one-click.
#
#   mobile : 375 × 667 (iPhone SE / iPhone 12-mini class — narrowest tier
#                       still in active-use traffic)
#   tablet : 768 × 1024 (iPad portrait class — widest tier where the
#                        phone-only @media (max-width: 640px) does NOT
#                        kick in, so it exercises the tablet @media block)
#
# Devicemetrics-only profile: we don't simulate touch events or change
# user-agent. The CSS-first responsive pass is the surface under test;
# input-mode behaviours land in a later slice.
# ─────────────────────────────────────────────────────────────────────────────

MOBILE_VIEWPORT = {"width": 375, "height": 667}
TABLET_VIEWPORT = {"width": 768, "height": 1024}


def _make_page_with_viewport(browser, viewport: dict | None = None):
    """Construct a fresh context+page, optionally with a forced viewport,
    wired up with the same console/5xx watchers as the default `page`
    fixture. Returns (context, page) — caller closes the context."""
    ctx = browser.new_context(viewport=viewport) if viewport else browser.new_context()
    p = ctx.new_page()
    p._console_errors = []  # type: ignore
    p._failed_responses = []  # type: ignore
    p.on(
        "console",
        lambda m: p._console_errors.append((m.type, m.text))  # type: ignore
        if m.type in ("error",)
        else None,
    )
    p.on(
        "response",
        lambda r: p._failed_responses.append((r.status, r.request.method, r.url))  # type: ignore
        if r.status >= 500
        else None,
    )
    return ctx, p


@pytest.fixture
def page(chromium_browser):
    ctx, p = _make_page_with_viewport(chromium_browser)
    yield p
    ctx.close()


@pytest.fixture
def mobile_page(chromium_browser):
    """Page object pinned to a phone-class viewport (375×667).
    Exercises the @media (max-width: 640px) branch."""
    ctx, p = _make_page_with_viewport(chromium_browser, MOBILE_VIEWPORT)
    yield p
    ctx.close()


@pytest.fixture
def tablet_page(chromium_browser):
    """Page object pinned to a tablet-class viewport (768×1024).
    Exercises the @media (max-width: 1024px) branch but NOT the
    640px phone branch."""
    ctx, p = _make_page_with_viewport(chromium_browser, TABLET_VIEWPORT)
    yield p
    ctx.close()


def _login(page, base_url: str, email: str, pw: str) -> None:
    """Shared login helper — used by `logged_in_page` and the responsive
    `mobile_logged_in_page` / `tablet_logged_in_page` fixtures."""
    page.goto(f"{base_url}/app/login")
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', pw)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    assert "/app/" in page.url and "/login" not in page.url, (
        f"login failed; landed at {page.url}"
    )


@pytest.fixture
def logged_in_page(page, base_url, admin_creds):
    """Pre-authenticated page object."""
    email, pw = admin_creds
    _login(page, base_url, email, pw)
    return page


@pytest.fixture
def mobile_logged_in_page(mobile_page, base_url, admin_creds):
    """Pre-authenticated mobile-viewport page."""
    email, pw = admin_creds
    _login(mobile_page, base_url, email, pw)
    return mobile_page


@pytest.fixture
def tablet_logged_in_page(tablet_page, base_url, admin_creds):
    """Pre-authenticated tablet-viewport page."""
    email, pw = admin_creds
    _login(tablet_page, base_url, email, pw)
    return tablet_page


def unique_suffix() -> str:
    return f"{int(time.time() * 1000) % 100000:05d}"
