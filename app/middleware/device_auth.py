from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from functools import wraps

from flask import g, request
from sqlalchemy import select

from app.db import session_scope
from app.middleware.response import err
from app.models import Device, DeviceCredential


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _resolve_device():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token_hash = _hash(auth.split(" ", 1)[1])
    with session_scope() as session:
        cred = session.scalar(
            select(DeviceCredential).where(
                DeviceCredential.token_hash == token_hash,
                DeviceCredential.revoked.is_(False),
            )
        )
        if cred is None:
            return None
        device = session.get(Device, cred.device_id)
        if device is None or device.registration_state != "active":
            return None
        cred.last_used_at = datetime.now(timezone.utc)
        session.add(cred)
        session.flush()
        session.expunge(device)
        return device


def _claimed_device_id_from_request() -> str | None:
    """Best-effort extraction of the device_id the caller claimed.

    The device-API endpoints accept device_id either in JSON body or as a
    query string. Both are untrusted input — we cap length in the recorder.
    """
    qs_id = request.args.get("device_id") if request.args else None
    if qs_id:
        return qs_id
    body = request.get_json(silent=True) or {}
    if isinstance(body, dict):
        return body.get("device_id")
    return None


def device_auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        device = _resolve_device()
        if device is None:
            # v0.2.5: best-effort log to surface unregistered-firmware in admin UI.
            try:
                from app.services import unregistered

                unregistered.record(
                    claimed_device_id=_claimed_device_id_from_request(),
                    source_ip=request.remote_addr,
                    endpoint=request.path,
                    user_agent=request.headers.get("User-Agent"),
                    auth_present=request.headers.get(
                        "Authorization", ""
                    ).startswith("Bearer "),
                )
            except Exception:
                pass
            return err("auth_invalid", "Device authentication required.", status=401)
        g.current_device = device
        return fn(*args, **kwargs)

    return wrapper
