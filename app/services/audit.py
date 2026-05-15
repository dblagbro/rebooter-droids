"""Append-only audit log for admin mutations + selected device events."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import has_request_context, request
from sqlalchemy import select

from app.db import session_scope
from app.models import AuditEvent

log = logging.getLogger(__name__)


def _should_sync_action(action: str, target_type: str | None) -> bool:
    """Determine if an audit action should emit an outbox event for sync.

    Returns True for create/update/delete actions on syncable entity types
    (device, site, group, user).
    """
    if not target_type:
        return False

    # Syncable entity types
    syncable_types = {"device", "site", "group", "user"}
    if target_type not in syncable_types:
        return False

    # Syncable action patterns
    # Include: created, updated, deleted, renamed, adopted, restored
    # Exclude: command_issued, command_cancelled, etc. (operational events)
    syncable_verbs = {
        "created", "updated", "deleted", "renamed",
        "adopted", "restored", "decommissioned",
    }

    # Extract verb from action (e.g., "device.created" → "created")
    if "." not in action:
        return False
    verb = action.split(".", 1)[1]

    return verb in syncable_verbs


def _emit_outbox_for_scoped_action(
    action: str,
    target_type: str | None,
    target_id: str | None,
    scope_claim: dict | None,
    entity_snapshot: dict | None,
) -> None:
    """Emit an outbox event for multi-hub sync (B11 / RFC-004 Option C).

    Best-effort: never raises. Skips emission if target_type or target_id
    is missing (not all audit actions map to syncable entities).
    """
    if not (target_type and target_id):
        return  # Not a resource-level mutation; nothing to sync

    try:
        from app.services import sync as sync_svc

        # Determine event_type from action
        # Format: entity.verb (e.g., "device.created", "site.deleted")
        # Audit actions use this format already
        event_type = action

        # Determine if this is a delete action
        is_delete = action.endswith((".deleted", ".bulk_deleted"))

        with session_scope() as session:
            if is_delete:
                # For deletes, emit tombstone
                sync_svc.emit_outbox_event(
                    session,
                    event_type=event_type,
                    entity_type=target_type,
                    entity_id=target_id,
                    payload={"deleted": True},
                    tombstone_for=target_id,
                    scope_claims=scope_claim,
                )
            elif entity_snapshot:
                # For creates/updates, emit full entity payload
                sync_svc.emit_outbox_event(
                    session,
                    event_type=event_type,
                    entity_type=target_type,
                    entity_id=target_id,
                    payload=entity_snapshot,
                    scope_claims=scope_claim,
                )
            # else: no entity_snapshot provided for a non-delete; skip
    except Exception:
        log.exception(
            "outbox emit failed for action=%s target=%s/%s",
            action,
            target_type,
            target_id,
        )


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
    """Best-effort: never raise from the audit path.

    v0.5.48 (B11 Phase 4): Also emits outbox events for syncable entity
    mutations (device/site/group/user create/update/delete actions).
    """
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

    # v0.5.48 (B11 Phase 4): Emit outbox events for syncable mutations
    if _should_sync_action(action, target_type):
        _emit_outbox_for_scoped_action(
            action=action,
            target_type=target_type,
            target_id=target_id,
            scope_claim=None,  # Infer from target_type/target_id
            entity_snapshot=None,  # TODO: pass from callers incrementally
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
    entity_snapshot: dict | None = None,  # v0.5.45 (B11) — full entity for outbox payload
) -> None:
    """v0.5.35 (B1 RBAC Phase 1) — audit a per-resource mutation with its
    RBAC scope claim attached.

    v0.5.45 (B11 multi-hub sync) — this is now the outbox-emission
    choke-point per RFC-004 Option C. Every scoped mutation emits both
    an audit_events row AND an outbox_events row. The outbox event
    carries the full entity snapshot (if provided) or a tombstone
    marker (for deletes).

    ``scope_claim`` shape: ``{"scope_type": "device", "scope_id": "..."}``.
    ``entity_snapshot``: full entity dict for creates/updates; omit for deletes.
    Best-effort: never raises (see ``record``)."""
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

    # v0.5.45 (B11): Emit outbox event for multi-hub sync
    _emit_outbox_for_scoped_action(
        action=action,
        target_type=target_type,
        target_id=target_id,
        scope_claim=scope_claim,
        entity_snapshot=entity_snapshot,
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
