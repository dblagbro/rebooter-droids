"""Rate limiting for auth endpoints.

Module-level limiter so blueprint views can decorate at definition
time. App attached via init_rate_limit(app).

Storage is in-memory and per-worker. We keep Gunicorn at 1 worker
(see gunicorn.conf.py) so the bucket is shared across all incoming
traffic for this node. Going multi-worker or multi-node-active-active
later requires a shared backend (Redis recommended).
"""

from __future__ import annotations

from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from app.middleware.response import err


def _key_func() -> str:
    # ProxyFix(x_for=1) sets request.remote_addr from X-Forwarded-For.
    return get_remote_address() or "unknown"


limiter = Limiter(
    key_func=_key_func,
    storage_uri="memory://",
    default_limits=[],
    headers_enabled=True,
)


def init_rate_limit(app: Flask) -> Limiter:
    """Attach the limiter to the app and install the envelope 429 handler."""
    limiter.init_app(app)

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
