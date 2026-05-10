"""Append-only audit log for admin mutations + selected device events."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import has_request_context, request
from sqlalchemy import select

from app.db import session_scope
from app.models import AuditEvent

log = logging.getLogger(__name__)


def _client_ip() -> str | None:
    if not has_request_context():
        return None
    # ProxyFix sets remote_addr from X-Forwarded-For.
    return request.remote_addr


def record(
    action: str,
    *,
    actor_user_id: str | None = None,
    actor_email_snapshot: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
    ip: str | None = None,
) -> None:
    """Best-effort: never raise from the audit path."""
    try:
        evt = AuditEvent(
            at=datetime.now(timezone.utc),
            actor_user_id=actor_user_id,
            actor_email_snapshot=actor_email_snapshot,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details or {},
            ip=ip if ip is not None else _client_ip(),
        )
        with session_scope() as session:
            session.add(evt)
    except Exception:
        log.exception("audit emit failed for action=%s target=%s/%s", action, target_type, target_id)


def record_per_device(
    action: str,
    *,
    actor_user_id: str | None,
    actor_email_snapshot: str | None,
    device_ids: list[str],
    details_for: callable | None = None,
    base_details: dict | None = None,
    ip: str | None = None,
) -> int:
    """v0.4.9 (B14) — fan out a bulk action to one audit row per
    device. Operator gets a per-device record alongside the
    aggregate `*.bulk_*` meta-row, which makes "what did this
    bulk-delete actually touch?" answerable from /app/audit.

    `action`: e.g. `device.bulk_deleted_per_device`.
    `details_for`: optional callable `(device_id) -> dict` to
       merge per-row context. `base_details` is merged on top of
       it (or used alone when details_for is None).
    Returns the number of rows attempted (best-effort; never
    raises).
    """
    base = dict(base_details or {})
    n = 0
    for did in device_ids or ():
        try:
            details = dict(details_for(did)) if details_for else {}
            details.update(base)
            record(
                action,
                actor_user_id=actor_user_id,
                actor_email_snapshot=actor_email_snapshot,
                target_type="device",
                target_id=did,
                details=details,
                ip=ip,
            )
            n += 1
        except Exception:
            log.exception("audit per-device fanout failed for %s", did)
    return n


def query(
    actor_user_id: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    limit = max(1, min(limit, 1000))
    with session_scope() as session:
        stmt = select(AuditEvent)
        if actor_user_id:
            stmt = stmt.where(AuditEvent.actor_user_id == actor_user_id)
        if action:
            stmt = stmt.where(AuditEvent.action == action)
        if target_type:
            stmt = stmt.where(AuditEvent.target_type == target_type)
        if target_id:
            stmt = stmt.where(AuditEvent.target_id == target_id)
        stmt = stmt.order_by(AuditEvent.at.desc()).limit(limit)
        rows = list(session.scalars(stmt))
        return [
            {
                "id": e.id,
                "at": e.at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "actor_user_id": e.actor_user_id,
                "actor_email_snapshot": e.actor_email_snapshot,
                "action": e.action,
                "target_type": e.target_type,
                "target_id": e.target_id,
                "details": e.details,
                "ip": e.ip,
            }
            for e in rows
        ]
