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

    # Provision via the invitation flow (no direct POST /api/v1/admin/users
    # endpoint — the only admin-side path to a new user is invite + redeem).
    auth = {"Authorization": f"Bearer {admin_token}"}
    invite = requests.post(
        f"{base_url}/api/v1/admin/invitations",
        headers=auth,
        json={"email": email, "role": "admin", "note": "QA disposable"},
        timeout=10,
    )
    if invite.status_code not in (200, 201):
        pytest.skip(f"could not mint invitation: {invite.status_code} {invite.text}")
    invite_data = invite.json()["data"]
    redeem_url = invite_data.get("redeem_url") or invite_data.get("invitation", {}).get("redeem_url")
    if not redeem_url:
        pytest.skip(f"invitation response shape unrecognised: {invite_data}")
    # redeem_url is /app/invite/<raw>; extract <raw>.
    raw = redeem_url.rsplit("/", 1)[-1]

    # Redeem via the form-style POST /app/invite/<token>.
    redeem = requests.post(
        f"{base_url}/app/invite/{raw}",
        data={
            "password": password,
            "password_confirm": password,
            "display_name": "QA Isolated",
        },
        timeout=10,
        allow_redirects=False,
    )
    if redeem.status_code not in (302, 303, 200):
        pytest.skip(f"could not redeem invitation: {redeem.status_code} {redeem.text[:200]}")

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
        "user_id": login.json()["data"].get("user", {}).get("id"),
    }

    # Best-effort cleanup. Deactivate the user (admin API supports
    # /users/<id>/deactivate); we don't have a hard-delete endpoint.
    try:
        users = requests.get(
            f"{base_url}/api/v1/admin/users", headers=auth, timeout=10
        ).json()["data"]["users"]
        match = next((u for u in users if u["email"] == email), None)
        if match:
            requests.post(
                f"{base_url}/api/v1/admin/users/{match['id']}/deactivate",
                headers=auth,
                timeout=10,
            )
    except Exception:
        pass


@pytest.fixture
def chromium_browser():
    """Headless Chromium tied to the playwright-installed binary.

    When playwright isn't installed (e.g. the CI gate, which does not
    pull a browser), browser-backed tests *skip* cleanly rather than
    erroring — so a mixed file can carry both API and browser tests.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        import pytest as _pytest

        _pytest.skip("playwright not installed — browser test skipped")

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
