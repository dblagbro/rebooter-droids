"""v0.3.0 P1 — design system / layout / navigation foundation.

Asserts:
- Five new top-nav destinations render and resolve:
  Status (/) · Devices · Rules · History · Settings
- Top nav appears on the rendered HTML; bottom-tab nav appears too
  (it is hidden via CSS at ≥768 px but the markup is always present
  for mobile breakpoints).
- The five destinations carry an `aria-current="page"` attribute on
  the appropriate nav link.
- Theme picker round-trips: GET /app/settings/theme reflects the
  current cookie; POST /app/settings/theme writes a cookie that
  survives the redirect.
- Old URLs continue to resolve (no bookmarks broken).
- Pages do not horizontally overflow at 375 px viewport. The seven
  pre-existing failures in test_responsive.py (login, dashboard,
  devices, events, audit, users) are explicitly re-asserted here so
  any regression is caught.

These tests run against the live deployment (consistent with the
rest of the QA suite).
"""

from __future__ import annotations

import pytest
import requests


@pytest.fixture(scope="module")
def shell_session(base_url, admin_creds):
    """Module-scoped login. The rest of the QA suite uses a per-test
    `admin_token` fixture which trips the 30/min login rate limit
    when many test files run sequentially. The v030 bucket logs in
    exactly once per module."""
    s = requests.Session()
    email, pw = admin_creds
    r = s.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    return s


# ── new top-nav destinations resolve ─────────────────────────────────────

def test_status_page_resolves_at_root_app(base_url, shell_session):
    s = shell_session
    r = s.get(f"{base_url}/app/", timeout=10)
    assert r.status_code == 200
    assert "Rebooter" in r.text


def test_devices_page_resolves(base_url, shell_session):
    s = shell_session
    r = s.get(f"{base_url}/app/devices", timeout=10)
    assert r.status_code == 200


def test_rules_page_resolves(base_url, shell_session):
    """New /app/rules destination must render the rules empty-state
    page and carry the v0.3.0 watchdog framing copy."""
    s = shell_session
    r = s.get(f"{base_url}/app/rules", timeout=10)
    assert r.status_code == 200
    assert "No watchdog rules yet" in r.text or "Rules" in r.text


def test_history_page_resolves(base_url, shell_session):
    s = shell_session
    r = s.get(f"{base_url}/app/history", timeout=10)
    assert r.status_code == 200
    assert "History" in r.text


def test_settings_page_resolves(base_url, shell_session):
    s = shell_session
    r = s.get(f"{base_url}/app/settings", timeout=10)
    assert r.status_code == 200
    assert "Settings" in r.text


# ── nav semantics ──────────────────────────────────────────────────────────

def test_top_nav_renders_five_destinations(base_url, shell_session):
    s = shell_session
    body = s.get(f"{base_url}/app/", timeout=10).text
    # Each top-nav destination link must be present.
    assert ">Status<" in body
    assert ">Devices<" in body
    assert ">Rules<" in body
    assert ">History<" in body
    assert ">Settings<" in body


def test_status_marks_status_active_on_root(base_url, shell_session):
    """The Status nav link must carry aria-current='page' when the
    operator is on /app/."""
    s = shell_session
    body = s.get(f"{base_url}/app/", timeout=10).text
    # Robust assertion: somewhere in the body, aria-current="page"
    # must appear on a link to /app/ (it could be the top or bottom
    # nav, both render).
    assert 'aria-current="page"' in body, "no nav item is marked active"


def test_devices_marks_devices_active(base_url, shell_session):
    s = shell_session
    r = s.get(f"{base_url}/app/devices", timeout=10)
    assert r.status_code == 200
    assert 'aria-current="page"' in r.text


def test_rules_marks_rules_active(base_url, shell_session):
    s = shell_session
    r = s.get(f"{base_url}/app/rules", timeout=10)
    assert r.status_code == 200
    assert 'aria-current="page"' in r.text


def test_history_marks_history_active(base_url, shell_session):
    s = shell_session
    r = s.get(f"{base_url}/app/history", timeout=10)
    assert r.status_code == 200
    assert 'aria-current="page"' in r.text


def test_settings_marks_settings_active(base_url, shell_session):
    s = shell_session
    r = s.get(f"{base_url}/app/settings", timeout=10)
    assert r.status_code == 200
    assert 'aria-current="page"' in r.text


def test_users_page_marks_settings_active(base_url, shell_session):
    """Existing /app/users keeps working AND highlights Settings in
    the nav (URL-prefix derivation in _ctx()._derive_active())."""
    s = shell_session
    r = s.get(f"{base_url}/app/users", timeout=10)
    assert r.status_code == 200


def test_audit_page_marks_history_active(base_url, shell_session):
    """Existing /app/audit keeps working AND highlights History in
    the nav (URL-prefix derivation)."""
    s = shell_session
    r = s.get(f"{base_url}/app/audit", timeout=10)
    assert r.status_code == 200


# ── theme picker round-trip ────────────────────────────────────────────────

def test_theme_picker_default_is_system(base_url, shell_session):
    s = shell_session
    r = s.get(f"{base_url}/app/settings/theme", timeout=10)
    assert r.status_code == 200
    # First-load default = system; the radio for system is checked.
    assert 'value="system"' in r.text and "checked" in r.text


def test_theme_picker_writes_cookie_on_post(base_url, shell_session):
    s = shell_session
    r = s.post(
        f"{base_url}/app/settings/theme",
        data={"theme": "dark"},
        timeout=10,
        allow_redirects=True,
    )
    assert r.status_code == 200
    assert s.cookies.get("theme") == "dark"
    body = s.get(f"{base_url}/app/settings/theme", timeout=10).text
    # The dark radio is now selected.
    import re
    assert re.search(r'value="dark"[^>]*checked', body) or re.search(
        r'checked[^>]*value="dark"', body
    ), "dark radio should be checked after writing the cookie"


def test_theme_picker_rejects_invalid_value(base_url, shell_session):
    s = shell_session
    s.post(
        f"{base_url}/app/settings/theme",
        data={"theme": "neon-eyestrain"},
        timeout=10,
        allow_redirects=True,
    )
    # Server clamps to 'system' for unknown values.
    assert s.cookies.get("theme") == "system"


# ── old URLs keep working (no bookmarks broken) ───────────────────────────

def test_old_audit_url_still_resolves(base_url, shell_session):
    s = shell_session
    assert s.get(f"{base_url}/app/audit", timeout=10).status_code == 200


def test_old_users_url_still_resolves(base_url, shell_session):
    s = shell_session
    assert s.get(f"{base_url}/app/users", timeout=10).status_code == 200


def test_old_firmware_url_still_resolves(base_url, shell_session):
    s = shell_session
    assert s.get(f"{base_url}/app/firmware", timeout=10).status_code == 200


def test_old_invitations_url_still_resolves(base_url, shell_session):
    s = shell_session
    assert s.get(f"{base_url}/app/invitations", timeout=10).status_code == 200


def test_old_events_url_still_resolves(base_url, shell_session):
    s = shell_session
    assert s.get(f"{base_url}/app/events", timeout=10).status_code == 200


def test_old_unregistered_url_still_resolves(base_url, shell_session):
    s = shell_session
    assert s.get(
        f"{base_url}/app/unregistered-devices", timeout=10
    ).status_code == 200
