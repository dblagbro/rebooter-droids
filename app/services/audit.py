"""Append-only audit log for admin mutations + selected device events."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import has_request_context, request
from sqlalchemy import select

from app.db import session_scope
from app.models import AuditEvent

log = logging.getLogger(__name__)

# v0.5.71 (B11): outbox emission for multi-hub sync is NO LONGER driven
# from the audit path. It is now done by mapper-level ORM hooks in
# `app.services.sync_emission`, which fire on the actual row write — so
# emission can't depend on an audit action being present and correctly
# verbed at every mutation call-site (it wasn't). The audit log and the
# sync outbox are now fully independent concerns.


def _client_ip() -> str | None:
    if not has_request_context():
        return None
    # ProxyFix sets remote_addr from X-Forwarded-For.
    return request.remote_addr


def _active_org_id() -> str | None:
    """The org scope currently bound (or None for a system/unscoped
    context). `audit_events` is exempt from the `before_flush`
    write-stamping (it is an append-only platform table whose org column
    is permanently nullable — design §3.6), so an audit row stamps its
    own org here: an audit row written under an org-scoped request keeps
    that org, a system/unscoped audit row keeps NULL. Best-effort."""
    try:
        from app.services import tenant_scope

        return tenant_scope.current_org()
    except Exception:
        return None


def _build_event(
    action: str,
    *,
    actor_user_id: str | None = None,
    actor_email_snapshot: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
    ip: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        at=datetime.now(timezone.utc),
        organization_id=_active_org_id(),
        actor_user_id=actor_user_id,
        actor_email_snapshot=actor_email_snapshot,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details or {},
        ip=ip if ip is not None else _client_ip(),
    )


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
    """Best-effort: never raise from the audit path.

    Outbox emission for multi-hub sync is independent of this path —
    see `app.services.sync_emission`.
    """
    try:
        evt = _build_event(
            action,
            actor_user_id=actor_user_id,
            actor_email_snapshot=actor_email_snapshot,
            target_type=target_type,
            target_id=target_id,
            details=details,
            ip=ip,
        )
        with session_scope() as session:
            session.add(evt)
    except Exception:
        log.exception("audit emit failed for action=%s target=%s/%s", action, target_type, target_id)


def record_on_session(
    session,
    action: str,
    *,
    actor_user_id: str | None = None,
    actor_email_snapshot: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
    ip: str | None = None,
) -> None:
    """Append an audit row to an ALREADY-OPEN session, instead of opening
    a new `session_scope()`.

    This is the path the tenant-scope `before_flush` / `do_orm_execute`
    hooks must use: they run *inside* an outer write transaction, so
    opening a second `session_scope()` here would deadlock a SQLite
    database (a second connection blocking on the first's write lock).
    Adding to the current session keeps the audit row in the same
    transaction — it commits (or rolls back) atomically with the write
    that triggered it.

    Best-effort — never raises (mirrors `record`)."""
    try:
        evt = _build_event(
            action,
            actor_user_id=actor_user_id,
            actor_email_snapshot=actor_email_snapshot,
            target_type=target_type,
            target_id=target_id,
            details=details,
            ip=ip,
        )
        session.add(evt)
    except Exception:
        log.exception(
            "audit emit (on-session) failed for action=%s target=%s/%s",
            action, target_type, target_id,
        )


def record_scoped(
    action: str,
    *,
    actor_user_id: str | None = None,
    actor_email_snapshot: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    scope_claim: dict | None = None,
    details: dict | None = None,
    ip: str | None = None,
) -> None:
    """v0.5.35 (B1 RBAC Phase 1) — audit a per-resource mutation with its
    RBAC scope claim attached.

    ``scope_claim`` shape: ``{"scope_type": "device", "scope_id": "..."}``.
    Outbox emission for multi-hub sync is independent of this path
    (`app.services.sync_emission`). Best-effort: never raises (see
    ``record``)."""
    merged = dict(details or {})
    if scope_claim is not None:
        merged["scope_claim"] = scope_claim
    record(
        action,
        actor_user_id=actor_user_id,
        actor_email_snapshot=actor_email_snapshot,
        target_type=target_type,
        target_id=target_id,
        details=merged,
        ip=ip,
    )


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
    action_prefix: str | None = None,
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
        if action_prefix:
            # v0.4.27: chip-style filter for the history page. Matches
            # exact `<prefix>` plus `<prefix>.*` so chips like
            # "watchdog_rule" cover watchdog_rule.created/deleted/etc.
            stmt = stmt.where(AuditEvent.action.like(f"{action_prefix}.%"))
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
