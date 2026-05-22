from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from functools import wraps

from flask import g, request
from sqlalchemy import select

from app.db import session_scope
from app.middleware.response import err
from app.models import Device, DeviceCredential, Site

log = logging.getLogger(__name__)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _resolve_device():
    """Resolve the authenticated device AND its owning org.

    Returns `(device, organization_id)` or `None`. The device's org is
    *derived* — `device → site → organization` (design §1: "derive,
    don't denormalize"). The whole resolution runs under
    `tenant_scope.system()` because it must read `sites` (a Tier-A
    table) BEFORE any org scope can be bound — the chicken-and-egg case
    of design §3.4. The caller then binds the resolved org for the
    duration of the request so a heartbeat/event write lands in the
    device's org.
    """
    from app.services import tenant_scope

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token_hash = _hash(auth.split(" ", 1)[1])
    with tenant_scope.system():
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
            # Derive the org via the device's site. site_id may be NULL
            # on a not-yet-sited device — then org stays None and the
            # request runs unscoped (the device API resolves its own
            # targets by device_id, which is Tier-B).
            org_id = None
            if device.site_id:
                site = session.get(Site, device.site_id)
                org_id = getattr(site, "organization_id", None) if site else None
            now = datetime.now(timezone.utc)
            cred.last_used_at = now
            session.add(cred)
            # v0.6.3 (devices-page correctness): stamp the device's real
            # last-contact timestamp on EVERY authenticated device
            # request — heartbeat, the /commands long-poll, command-result,
            # events, firmware-check, failsafe. Pre-0.6.3 only a full
            # /heartbeat moved a contact timestamp the devices list could
            # see, so a device that was actively long-polling /commands
            # but not yet due for a heartbeat rendered 'offline' while
            # plainly reachable. `last_seen_at` lets the list reflect the
            # device's REAL last contact by any device path.
            device.last_seen_at = now
            session.add(device)
            session.flush()
            session.expunge(device)
            return device, org_id


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
        resolved = _resolve_device()
        if resolved is None:
            # v0.2.5: best-effort log to surface unregistered-firmware in admin UI.
            try:
                from app.services import unregistered

                with __import__(
                    "app.services.tenant_scope", fromlist=["system"]
                ).system():
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
        device, org_id = resolved
        g.current_device = device
        # org-boundary phase 2: bind the device's derived org for the
        # request so heartbeat/event/command writes land in the right
        # tenant (design §3.4 — "this is mandatory, not optional").
        # Cleared in the same teardown_request hook as the user path.
        try:
            from app.services import tenant_scope

            tenant_scope.set_org(org_id)
        except Exception:
            log.exception(
                "device-auth tenant-scope binding failed for device %s",
                getattr(device, "id", None),
            )
        return fn(*args, **kwargs)

    return wrapper
