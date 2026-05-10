"""v0.4.33 — Firmware UI moves under /app/settings/firmware (D3).

The page that's been at /app/firmware since v0.1 is now a canonical
Settings tab. Old URL keeps working via 302 redirect so existing
bookmarks + external docs + the per-device upgrade button keep
functioning.
"""

from __future__ import annotations

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


def test_settings_firmware_url_renders_with_tab_strip(base_url):
    s = _login(base_url)
    body = s.get(f"{base_url}/app/settings/firmware", timeout=10).text
    # Page renders normally
    assert "Firmware" in body
    # Settings tab strip present, firmware tab marked active
    assert 'aria-current="page"' in body
    # The page body has the upload form (firmware-specific content)
    assert 'name="version"' in body
    assert 'name="file"' in body


def test_legacy_firmware_url_redirects(base_url):
    s = _login(base_url)
    r = s.get(
        f"{base_url}/app/firmware",
        timeout=10,
        allow_redirects=False,
    )
    assert r.status_code in (301, 302, 303), r.text
    loc = r.headers.get("Location", "")
    assert "/app/settings/firmware" in loc, f"redirect target wrong: {loc}"
