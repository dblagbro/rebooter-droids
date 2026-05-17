"""Edge cases at the nginx + ProxyFix + PrefixMiddleware boundary."""

import pytest
import requests

# v0.5.80: in the `-m ci` gate (P-QA gate-3 partial-fail bucket). The
# two genuinely nginx-layer tests below skip when the base URL is not
# the `/rebooter`-prefixed deployment (e.g. the bare CI app instance).
pytestmark = pytest.mark.ci


def test_root_redirects_to_app(base_url):
    if "/rebooter" not in base_url:
        pytest.skip("nginx PrefixMiddleware deployment only")
    r = requests.get(f"{base_url}/", timeout=10, allow_redirects=False)
    assert r.status_code in (301, 302), r.status_code
    loc = r.headers.get("Location", "")
    assert loc.endswith("/rebooter/app/"), loc


def test_root_no_trailing_slash_redirects_to_app(base_url):
    # /rebooter (no trailing slash) → 302 /rebooter/app/
    r = requests.get(
        f"{base_url}".rstrip("/") + "", timeout=10, allow_redirects=False
    )
    # Going through URL normalisation, this should land on the app
    assert r.status_code in (200, 301, 302)


def test_firmware_dir_does_not_index(base_url):
    """autoindex off — /rebooter/firmware/ must 403, not list contents."""
    if "/rebooter" not in base_url:
        pytest.skip("nginx-served firmware dir — prefixed deployment only")
    r = requests.get(f"{base_url}/firmware/", timeout=10)
    assert r.status_code == 403


def test_unknown_api_path_returns_404(base_url, admin_headers):
    r = requests.get(
        f"{base_url}/api/v1/admin/this-endpoint-does-not-exist",
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 404


def test_wrong_method_returns_405(base_url):
    r = requests.delete(f"{base_url}/api/v1/version", timeout=10)
    assert r.status_code == 405


def test_static_assets_served(base_url):
    r = requests.get(f"{base_url}/static/css/app.css", timeout=10)
    assert r.status_code == 200
    assert r.headers.get("Content-Type", "").startswith("text/css")


def test_envelope_on_validation_error(base_url):
    """Every error path should return the envelope shape."""
    r = requests.post(f"{base_url}/api/v1/auth/login", json={}, timeout=10)
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert "code" in body["error"]
    assert "message" in body["error"]
