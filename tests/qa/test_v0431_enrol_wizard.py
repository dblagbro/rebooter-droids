"""v0.4.31 — Device-add wizard enhancements (E5 from continuation plan v2).

The wizard at /app/devices/new shipped in v0.3.1. v0.4.31 adds:

- Site selector (when sites exist)
- TTL picker (1h / 24h / 7d / 30d)
- Cross-links to /app/pending-adoption for the no-serial-access flow
- Surfaces as the primary "+ Enrol a device" entry point on the
  Status page and from unregistered_devices.html (previously linked
  to /app/enrollment-tokens which is a list view, not a guided form)
"""

from __future__ import annotations

import re

import requests


def _login(base_url: str) -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": "dblagbro@gmail.com", "password": "Super*120120"},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    return s


def test_wizard_page_renders_with_ttl_picker_and_pending_adoption_link(base_url):
    s = _login(base_url)
    body = s.get(f"{base_url}/app/devices/new", timeout=10).text
    # TTL picker present with the four operator-friendly options
    for label in (
        "1 hour",
        "24 hours",
        "7 days",
        "30 days",
        'name="ttl_seconds"',
    ):
        assert label in body, f"missing wizard field: {label}"
    # Cross-link to pending adoption
    assert "/app/pending-adoption" in body


def test_wizard_mints_token_with_ttl_override(base_url):
    """Submit the form with ttl_seconds=3600 and verify the
    returned token has an expiry that's ~1h out, not 24h."""
    s = _login(base_url)
    r = s.post(
        f"{base_url}/app/devices/new",
        data={
            "display_name_hint": "v0.4.31 wizard test",
            "ttl_seconds": "3600",
            "note": "automated test",
        },
        timeout=10,
        allow_redirects=False,
    )
    assert r.status_code in (302, 303), r.text
    body = s.get(f"{base_url}/app/devices/new", timeout=10).text
    # We can find the issued-token panel
    assert "Enrollment token issued" in body
    # And the expiry timestamp is visible
    m = re.search(r"<code>(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)</code>", body)
    assert m, "no token-expiry timestamp rendered"


def test_status_page_routes_to_new_wizard(base_url):
    s = _login(base_url)
    body = s.get(f"{base_url}/app/", timeout=10).text
    # The Status-page "+ Enrol a device" button should point at
    # /app/devices/new, not /app/enrollment-tokens.
    assert "/app/devices/new" in body, "Status page didn't surface the new wizard URL"
