"""v0.4.25 — runtime-editable SMTP via DB-backed runtime_settings."""

from __future__ import annotations

import pytest
import requests

from .conftest import ADMIN_EMAIL, ADMIN_PASS

# v0.5.79: in the `-m ci` gate (P-QA gate-3 brittle-file fixes).
pytestmark = pytest.mark.ci


def test_notifications_page_renders_editable_form(base_url, admin_headers):
    s = requests.Session()
    s.headers.update(admin_headers)
    # Use cookie-session for the UI form
    cs = requests.Session()
    r = cs.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
        timeout=10,
    )
    assert r.status_code == 200
    body = cs.get(f"{base_url}/app/settings/notifications", timeout=10).text
    assert "Edit SMTP settings" in body
    # Form fields exist
    assert 'name="host"' in body
    assert 'name="port"' in body
    assert 'name="from"' in body
    # Save + clear actions wired
    assert "/settings/notifications/save" in body
    assert "/settings/notifications/clear" in body
    # Override-vs-fallback indicator labels rendered
    assert "DB override" in body or "env-var fallback" in body


def test_save_and_clear_round_trip(base_url, admin_headers):
    """Save a custom HELO, verify it sticks, clear, verify it
    falls back. Doesn't touch host/user/password to avoid
    breaking real SMTP delivery."""
    cs = requests.Session()
    cs.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
        timeout=10,
    )

    # Probe what's currently set so we don't clobber real state
    body = cs.get(f"{base_url}/app/settings/notifications", timeout=10).text
    # Save a HELO override only — read-back from form value
    new_helo = "qa-test-helo.example"
    save = cs.post(
        f"{base_url}/app/settings/notifications/save",
        data={
            # To preserve existing host/from/etc, send them back
            # exactly as they were rendered. Cheap way: re-extract
            # from the current page. Skip if any field is missing.
            "host": _extract_value(body, 'name="host"'),
            "port": _extract_value(body, 'name="port"'),
            "user": _extract_value(body, 'name="user"'),
            "password": "********",  # signal "unchanged"
            "from": _extract_value(body, 'name="from"'),
            "helo": new_helo,
        },
        timeout=10,
        allow_redirects=False,
    )
    assert save.status_code in (302, 303), save.text

    # Reload, verify HELO is now our value with DB override marker
    body2 = cs.get(f"{base_url}/app/settings/notifications", timeout=10).text
    assert new_helo in body2
    # The HELO field should show "(DB override)"
    helo_section = body2.split('name="helo"', 1)[1][:400]
    assert "DB override" in helo_section

    # Clear all overrides
    clear = cs.post(
        f"{base_url}/app/settings/notifications/clear",
        timeout=10, allow_redirects=False,
    )
    assert clear.status_code in (302, 303)

    # Reload — HELO override gone, env-var fallback (whatever it
    # is) shown
    body3 = cs.get(f"{base_url}/app/settings/notifications", timeout=10).text
    helo_section3 = body3.split('name="helo"', 1)[1][:400]
    assert "env-var fallback" in helo_section3


def _extract_value(html: str, marker: str) -> str:
    """Extract the value="..." attribute that follows `marker`
    in the HTML. Cheap parser; good enough for these tests."""
    import re
    idx = html.find(marker)
    if idx < 0:
        return ""
    # Search backwards for the start of this <input ...> tag.
    tag_start = html.rfind("<input", 0, idx)
    tag_end = html.find(">", idx)
    if tag_start < 0 or tag_end < 0:
        return ""
    tag = html[tag_start:tag_end]
    m = re.search(r'value="([^"]*)"', tag)
    return m.group(1) if m else ""
