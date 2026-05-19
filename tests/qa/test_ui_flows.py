"""Browser-driven UI flow tests."""

import pytest

from .conftest import unique_suffix

# v0.5.98 (P-QA gate-3): the `page` fixture is built on
# `chromium_browser`, which skips cleanly when playwright / the
# chromium binary isn't available — so the CI gate skips these
# uniformly. Safe to mark.
pytestmark = pytest.mark.ci


def test_login_logout_round_trip(page, base_url, admin_creds):
    email, pw = admin_creds
    page.goto(f"{base_url}/app/login")
    assert "Sign In" in page.title()

    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', pw)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    assert "/app/" in page.url and "login" not in page.url.split("/app/")[-1]
    # v0.3.1 (R-DSH-1) replaced the v0.2.x "Dashboard" home with the
    # operator-needs-attention "Status" page. Title is now "Status -
    # Rebooter-Droids". The earlier assertion was stale.
    assert "Status" in page.title()

    # Sign out — v0.4.3 restored this link to the persistent header
    # (BUG-022). Pre-v0.4.3 it was hidden inside Profile.
    page.click('a:has-text("Sign out")')
    page.wait_for_load_state("networkidle")
    assert "/app/login" in page.url


def test_unauth_redirects_to_login(page, base_url):
    page.goto(f"{base_url}/app/")
    page.wait_for_load_state("networkidle")
    assert "/app/login" in page.url, (
        f"unauthenticated GET /app/ should redirect to /app/login; got {page.url}"
    )


def test_login_with_bad_password_shows_error(page, base_url):
    page.goto(f"{base_url}/app/login")
    page.fill('input[name="email"]', "dblagbro")
    page.fill('input[name="password"]', "wrong-password")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    assert "Invalid email or password" in page.content()


def test_create_group_does_not_log_user_out(logged_in_page, base_url):
    """User-reported in v0.1.2 conversation. We could not reproduce in clean state.

    This test asserts the post-create page is the groups list, not the login form.
    """
    page = logged_in_page
    page.goto(f"{base_url}/app/groups")
    page.fill('input[name="name"]', f"qa-no-logout-{unique_suffix()}")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    body = page.content()
    assert "Sign in" not in body, (
        "create-group response is showing the login form — user reported this"
    )
    assert "<h1>Groups</h1>" in body
    # v0.3.3 cookie-domain rework renamed `session` → `rebooter_session`.
    cookies = page.context.cookies()
    sess = [c for c in cookies if c["name"] == "rebooter_session"]
    assert sess and sess[0].get("expires", 0) > 0


def test_create_site_via_ui(logged_in_page, base_url):
    page = logged_in_page
    page.goto(f"{base_url}/app/sites")
    page.fill('input[name="name"]', f"qa-site-{unique_suffix()}")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    assert "Sign in" not in page.content()
    assert "<h1>Sites</h1>" in page.content()


def test_dashboard_shows_super_admin_badge(logged_in_page):
    body = logged_in_page.content()
    assert "super admin" in body.lower(), (
        "dashboard should show super admin badge for the architect account"
    )


def test_no_console_errors_on_dashboard(logged_in_page):
    """Refresh and assert no JS errors fire."""
    page = logged_in_page
    page.reload()
    page.wait_for_load_state("networkidle")
    serious = [
        (t, msg)
        for (t, msg) in page._console_errors  # type: ignore
        if "404" not in msg  # tolerate the favicon 404 for now (logged separately)
    ]
    assert not serious, f"unexpected console errors: {serious}"


def test_enrollment_token_minting_via_ui(logged_in_page, base_url):
    page = logged_in_page
    page.goto(f"{base_url}/app/enrollment-tokens")
    page.fill('input[name="display_name_hint"]', f"QA UI {unique_suffix()}")
    page.click('button:has-text("Mint token")')
    page.wait_for_load_state("networkidle")
    body = page.content()
    assert "et_" in body, "minted token (et_…) should appear once in the UI"


def test_devices_page_loads_for_super_admin(logged_in_page, base_url):
    page = logged_in_page
    page.goto(f"{base_url}/app/devices")
    page.wait_for_load_state("networkidle")
    assert "Devices" in page.title()
    # If devices exist, expect a table; otherwise a "No devices yet" hint
    body = page.content()
    assert ("<table>" in body) or ("No devices yet" in body)


def test_firmware_page_loads(logged_in_page, base_url):
    page = logged_in_page
    page.goto(f"{base_url}/app/firmware")
    page.wait_for_load_state("networkidle")
    assert "Firmware" in page.title()


def test_back_button_after_logout_does_not_resurrect_session(
    logged_in_page, base_url
):
    """Clicking back after logout must not let the user act on the dashboard."""
    page = logged_in_page
    page.click('a:has-text("Sign out")')
    page.wait_for_load_state("networkidle")
    page.go_back()
    page.wait_for_load_state("networkidle")
    # Even if HTML is cached, the cookie has been cleared, so any navigation
    # to a protected URL should redirect back to login.
    page.goto(f"{base_url}/app/")
    page.wait_for_load_state("networkidle")
    assert "/app/login" in page.url
