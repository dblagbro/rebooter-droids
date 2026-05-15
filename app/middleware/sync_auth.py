"""HMAC bearer authentication for peer-to-peer sync endpoints (B11 Phase 6).

Peer hubs authenticate to /api/v1/sync/* endpoints using HMAC-signed
bearer tokens. This avoids sharing admin credentials between hubs while
providing mutual authentication.

HMAC key stored in runtime_settings as "sync.hmac_key" (hex-encoded).
Each hub must have the same key to participate in the sync network.
"""
from __future__ import annotations

import hmac
import hashlib
import logging
from functools import wraps

from flask import request, jsonify

from app.services import runtime_settings as rs

log = logging.getLogger(__name__)


def _get_sync_hmac_key() -> bytes | None:
    """Get the sync HMAC key from runtime settings.

    Returns None if not configured.
    """
    key_hex = rs.get("sync.hmac_key", default=None)
    if not key_hex:
        return None
    try:
        return bytes.fromhex(key_hex)
    except (ValueError, TypeError):
        log.error("Invalid sync.hmac_key format (expected hex)")
        return None


def _verify_hmac_bearer(token: str) -> bool:
    """Verify an HMAC bearer token.

    Token format: "hmac-sha256.<payload>.<signature>"
    - payload: arbitrary peer identifier (e.g., "www2")
    - signature: hex(HMAC-SHA256(key, payload))

    Returns True if signature is valid.
    """
    key = _get_sync_hmac_key()
    if not key:
        log.warning("sync.hmac_key not configured; rejecting HMAC auth")
        return False

    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "hmac-sha256":
        return False

    _, payload, provided_sig = parts

    expected_sig = hmac.new(
        key,
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_sig, provided_sig)


def sync_peer_required(fn):
    """Decorator: require HMAC bearer authentication for sync endpoints.

    Checks Authorization: Bearer hmac-sha256.<payload>.<signature>

    Falls back to admin_required_api if HMAC auth fails (for initial
    testing / manual curl debugging).
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({
                "ok": False,
                "error": {
                    "code": "auth_required",
                    "message": "Bearer token required for sync endpoints",
                },
            }), 401

        token = auth_header[7:]  # Strip "Bearer "

        # Check HMAC bearer
        if token.startswith("hmac-sha256."):
            if _verify_hmac_bearer(token):
                # Authenticated as peer
                return fn(*args, **kwargs)
            else:
                return jsonify({
                    "ok": False,
                    "error": {
                        "code": "invalid_token",
                        "message": "HMAC signature verification failed",
                    },
                }), 403

        # Fall back to admin auth (for manual testing)
        # Import here to avoid circular dependency
        from app.middleware.admin_auth import admin_required_api
        decorated = admin_required_api(fn)
        return decorated(*args, **kwargs)

    return wrapper
