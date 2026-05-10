"""Rate limiting for auth endpoints.

Module-level limiter so blueprint views can decorate at definition
time. App attached via init_rate_limit(app).

Storage is in-memory and per-worker. We keep Gunicorn at 1 worker
(see gunicorn.conf.py) so the bucket is shared across all incoming
traffic for this node. Going multi-worker or multi-node-active-active
later requires a shared backend (Redis recommended).

v0.4.4 — `REBOOTER_RATE_LIMIT_EXEMPT_IPS` env var (comma-separated)
bypasses the limiter for matching client IPs. Used for the QA test
host so a full suite run (~50 logins) doesn't burn through the
200/hour budget. NEVER set this for arbitrary client IPs in
production — it's a deliberate hole punched for a known-trusted
host.
"""

from __future__ import annotations

import os

from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from app.middleware.response import err


def _exempt_ips() -> set[str]:
    """v0.4.26: read from runtime_settings → env-var fallback so the
    operator can edit the exempt-IP list from /app/settings/network
    without recreating the container."""
    raw = ""
    try:
        from app.services import runtime_settings
        raw = runtime_settings.get(
            "network.rate_limit_exempt_ips",
            env_var="REBOOTER_RATE_LIMIT_EXEMPT_IPS",
            default="",
        ) or ""
    except Exception:
        raw = os.environ.get("REBOOTER_RATE_LIMIT_EXEMPT_IPS", "")
    if not raw:
        return set()
    return {ip.strip() for ip in raw.split(",") if ip.strip()}


def _key_func() -> str:
    # ProxyFix(x_for=1) sets request.remote_addr from X-Forwarded-For.
    return get_remote_address() or "unknown"


def _is_exempt() -> bool:
    """Per-request exemption check. Returns True iff the client IP
    is in REBOOTER_RATE_LIMIT_EXEMPT_IPS — Flask-Limiter then skips
    the entire decorator chain for this request."""
    return _key_func() in _exempt_ips()


limiter = Limiter(
    key_func=_key_func,
    storage_uri="memory://",
    default_limits=[],
    headers_enabled=True,
)


def init_rate_limit(app: Flask) -> Limiter:
    """Attach the limiter to the app and install the envelope 429 handler."""
    limiter.init_app(app)

    # Per-request exemption hook. Flask-Limiter calls this BEFORE
    # any per-decorator limit, so an exempt IP sees no 429.
    @limiter.request_filter
    def _global_exemption():
        return _is_exempt()

    @app.errorhandler(429)
    def _ratelimit_handler(e):
        return err(
            "rate_limited",
            f"Too many requests — try again later. {e.description}",
            status=429,
        )

    return limiter


def get_limiter() -> Limiter:
    return limiter
