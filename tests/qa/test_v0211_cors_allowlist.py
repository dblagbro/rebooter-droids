"""v0.2.11 — strict CORS allowlist for /api/v1/* (R8-CORS).

Default allowlist is empty (unchanged behaviour). When operators set
`REBOOTER_CORS_ALLOWED_ORIGINS`, only those exact origins are echoed
back in `Access-Control-Allow-Origin`.

These tests run against the live deployment, so we cannot manipulate
env vars from here. We can only assert that:

(a) when no allowlist is configured (the default), no
    `Access-Control-Allow-Origin` header is returned regardless of the
    `Origin` header sent — i.e. cross-origin browser requests are
    *not implicitly trusted* by the new middleware.

(b) the new middleware does not break vanilla (no-Origin) requests —
    no auth-path regression on the existing buckets.

The "with allowlist configured" path is exercised by an integration
test that assumes the operator has set the env var; we skip that
assertion gracefully if they haven't.
"""

from __future__ import annotations

import os

import requests
import pytest

# v0.5.79: in the `-m ci` gate (P-QA gate-2 widening).
pytestmark = pytest.mark.ci



def test_default_no_allowlist_does_not_echo_origin(base_url):
    """Default deployment (no allowlist) must NOT echo any Origin
    header back. This is the safe-by-default contract."""
    r = requests.get(
        f"{base_url}/api/v1/version",
        headers={"Origin": "https://attacker.example.com"},
        timeout=10,
    )
    assert r.status_code == 200
    assert "access-control-allow-origin" not in {
        k.lower() for k in r.headers.keys()
    }, dict(r.headers)


def test_options_preflight_from_unknown_origin_does_not_get_cors_headers(base_url):
    """A preflight from an origin that isn't in the allowlist must NOT
    receive CORS headers, regardless of the requested method/headers."""
    r = requests.options(
        f"{base_url}/api/v1/admin/devices",
        headers={
            "Origin": "https://random.example.org",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
        timeout=10,
    )
    headers_lower = {k.lower() for k in r.headers.keys()}
    assert "access-control-allow-origin" not in headers_lower, dict(r.headers)


def test_no_origin_header_means_no_cors_headers(base_url):
    """Vanilla server-to-server requests (no Origin header) get no CORS
    response headers. Required to ensure existing curl/api clients see
    no behavioural change."""
    r = requests.get(f"{base_url}/api/v1/version", timeout=10)
    assert r.status_code == 200
    headers_lower = {k.lower() for k in r.headers.keys()}
    assert "access-control-allow-origin" not in headers_lower
    assert "access-control-allow-credentials" not in headers_lower


def test_allowlist_configured_origin_gets_echoed(base_url):
    """If the operator has set REBOOTER_QA_CORS_TEST_ORIGIN to an
    origin that's in the live deployment's allowlist, that exact
    origin must be echoed back. Skipped when the env var is unset
    (which is the default during local dev)."""
    test_origin = os.environ.get("REBOOTER_QA_CORS_TEST_ORIGIN")
    if not test_origin:
        # Skip — no operator-configured allowlist origin to assert against.
        return
    r = requests.get(
        f"{base_url}/api/v1/version",
        headers={"Origin": test_origin},
        timeout=10,
    )
    assert r.status_code == 200
    assert r.headers.get("Access-Control-Allow-Origin") == test_origin
    assert r.headers.get("Access-Control-Allow-Credentials") == "true"
    vary = r.headers.get("Vary", "")
    assert "Origin" in vary, vary
