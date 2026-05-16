"""Multi-hub sync service (RFC-004 Option C).

This module provides:
- Outbox event emission for all mutations
- Sync applier for incoming events from peer hubs
- Conflict resolution (last-writer-wins)
- Tombstone tracking for deletes
"""
from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, select
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    Device,
    Site,
    Group,
    User,
)
from app.models.sync import OutboxEvent, SyncCursor, Tombstone
from app.models._helpers import utcnow

log = logging.getLogger(__name__)


# ── Emission suppression ─────────────────────────────────────────────
# The applier writes to the syncable model tables via the ORM. Without
# this guard those writes would trigger the emission hooks
# (`sync_emission`) and re-emit outbox events — an infinite hub-to-hub
# loop. The applier runs inside `suppress_emission()`; the hooks no-op
# while the flag is set.
_emission_suppressed: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "rebooter_emission_suppressed", default=False
)


@contextmanager
def suppress_emission():
    """Suppress the sync-emission hooks for the duration of the block —
    wraps the applier so applied peer events do not re-emit."""
    token = _emission_suppressed.set(True)
    try:
        yield
    finally:
        _emission_suppressed.reset(token)


def emission_suppressed() -> bool:
    """True while inside `suppress_emission()`. Checked by the hooks."""
    return _emission_suppressed.get()


def emit_outbox_event(
    session: Session,
    event_type: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any],
    *,
    tombstone_for: str | None = None,
    scope_claims: dict[str, Any] | None = None,
) -> OutboxEvent:
    """Emit an outbox event for multi-hub sync.

    Args:
        session: Active database session
        event_type: Event type (e.g., "device.created", "site.deleted")
        entity_type: Entity type (e.g., "device", "site", "group")
        entity_id: Entity ULID
        payload: Full entity payload as dict
        tombstone_for: For deletes, the entity_id being deleted
        scope_claims: Optional RBAC scope claims for peer enforcement

    Returns:
        The created OutboxEvent
    """
    event = OutboxEvent(
        at=utcnow(),
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        tombstone_for=tombstone_for,
        scope_claims=scope_claims,
    )
    session.add(event)
    session.flush()  # Ensure seq is assigned
    log.debug(
        "Emitted outbox event seq=%d type=%s entity=%s/%s",
        event.seq,
        event_type,
        entity_type,
        entity_id,
    )
    return event


def get_sync_cursor(session: Session, peer_hub_id: str) -> int:
    """Get the last-applied sequence number for a peer hub.

    Returns 0 if no cursor exists (first sync).
    """
    cursor = session.scalar(
        select(SyncCursor).where(SyncCursor.peer_hub_id == peer_hub_id)
    )
    return cursor.last_seq if cursor else 0


def update_sync_cursor(
    session: Session,
    peer_hub_id: str,
    last_seq: int,
    error: str | None = None,
) -> None:
    """Update the sync cursor for a peer hub."""
    cursor = session.scalar(
        select(SyncCursor).where(SyncCursor.peer_hub_id == peer_hub_id)
    )
    now = utcnow()
    if cursor:
        cursor.last_seq = last_seq
        cursor.updated_at = now
        if error:
            cursor.last_error = error
            cursor.last_error_at = now
    else:
        cursor = SyncCursor(
            peer_hub_id=peer_hub_id,
            last_seq=last_seq,
            updated_at=now,
            last_error=error,
            last_error_at=now if error else None,
        )
        session.add(cursor)


def is_tombstoned(session: Session, entity_id: str) -> bool:
    """Check if an entity is tombstoned (deleted)."""
    return session.scalar(
        select(Tombstone).where(Tombstone.entity_id == entity_id)
    ) is not None


def add_tombstone(
    session: Session,
    entity_id: str,
    entity_type: str,
    from_outbox_seq: int,
) -> None:
    """Add a tombstone for a deleted entity."""
    if is_tombstoned(session, entity_id):
        log.debug("Tombstone already exists for %s/%s", entity_type, entity_id)
        return
    tombstone = Tombstone(
        entity_id=entity_id,
        entity_type=entity_type,
        created_at=utcnow(),
        from_outbox_seq=from_outbox_seq,
    )
    session.add(tombstone)
    log.info("Added tombstone for %s/%s", entity_type, entity_id)


# ── Syncable entities ────────────────────────────────────────────────
# Entity types whose create/update/delete mutations replicate between
# hubs. Mirrors `audit._should_sync_action`'s `syncable_types`.
_SYNCABLE_MODELS: dict[str, type] = {
    "device": Device,
    "site": Site,
    "group": Group,
    "user": User,
}


def _coerce_datetime(value: Any) -> Any:
    """Parse an ISO-8601 string into a datetime; pass datetimes/None through.

    Outbox payloads and a peer's `event.at` arrive as ISO strings over
    JSON (see `entity_to_dict`); the applier needs real datetimes both
    to assign to datetime columns and to compare for last-writer-wins.
    """
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value


def _as_utc(dt: datetime | None) -> datetime | None:
    """Normalise a datetime to tz-aware UTC so a last-writer-wins compare
    never hits the naive-vs-aware TypeError."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def snapshot_entity(
    session: Session, entity_type: str, entity_id: str
) -> dict[str, Any] | None:
    """Return a full column snapshot of a syncable entity for an outbox
    payload, or None if the type isn't syncable or the row is gone.

    Lets the emission path build every create/update payload itself, so
    individual mutation call-sites don't each have to assemble one.
    """
    model = _SYNCABLE_MODELS.get(entity_type)
    if model is None:
        return None
    entity = session.get(model, entity_id)
    return entity_to_dict(entity) if entity is not None else None


def apply_outbox_event(session: Session, event: OutboxEvent) -> bool:
    """Apply a single outbox event from a peer hub.

    Returns True if applied, False if skipped (tombstoned entity, an
    unsyncable entity type, an empty payload, or a stale write per
    last-writer-wins).

    - Delete events write a tombstone and remove the row.
    - Create/update events upsert the row, last-writer-wins on the
      entity's ``updated_at``.

    Idempotent: re-applying the same event is a no-op — a create finds
    the row already present and LWW-skips; an update finds ``updated_at``
    already equal and LWW-skips.

    Runs inside ``suppress_emission()`` and flushes within it, so the
    applier's own writes never trigger the emission hooks (no hub-to-hub
    re-emit loop).
    """
    with suppress_emission():
        applied = _apply_outbox_event(session, event)
        session.flush()
        return applied


def _apply_outbox_event(session: Session, event: OutboxEvent) -> bool:
    """Core applier logic — see ``apply_outbox_event``. Always called
    inside ``suppress_emission()``."""
    # A tombstoned entity must never be recreated or updated.
    if is_tombstoned(session, event.entity_id):
        log.warning(
            "Sync: skip seq=%s — entity %s/%s is tombstoned",
            event.seq, event.entity_type, event.entity_id,
        )
        return False

    model = _SYNCABLE_MODELS.get(event.entity_type)
    if model is None:
        log.warning(
            "Sync: skip seq=%s — unsyncable entity_type %r",
            event.seq, event.entity_type,
        )
        return False

    # ── Delete ───────────────────────────────────────────────────────
    if event.tombstone_for:
        add_tombstone(session, event.tombstone_for, event.entity_type, event.seq or 0)
        row = session.get(model, event.tombstone_for)
        if row is not None:
            session.delete(row)
        log.info(
            "Sync: deleted %s/%s (seq=%s)",
            event.entity_type, event.tombstone_for, event.seq,
        )
        return True

    # ── Create / update — last-writer-wins on updated_at ─────────────
    payload = event.payload or {}
    incoming: dict[str, Any] = {}
    for col in model.__table__.columns:
        if col.name not in payload:
            continue
        value = payload[col.name]
        if isinstance(col.type, DateTime):
            value = _coerce_datetime(value)
        incoming[col.name] = value

    if not incoming:
        log.warning(
            "Sync: skip seq=%s — empty payload for %s/%s",
            event.seq, event.entity_type, event.entity_id,
        )
        return False

    incoming_updated = _as_utc(
        _coerce_datetime(payload.get("updated_at")) or _coerce_datetime(event.at)
    )
    existing = session.get(model, event.entity_id)

    if existing is None:
        incoming.setdefault("id", event.entity_id)
        session.add(model(**incoming))
        log.info(
            "Sync: created %s/%s (seq=%s)",
            event.entity_type, event.entity_id, event.seq,
        )
        return True

    # Last-writer-wins: apply only if the incoming write is strictly newer.
    existing_updated = _as_utc(getattr(existing, "updated_at", None))
    if (
        incoming_updated is not None
        and existing_updated is not None
        and incoming_updated <= existing_updated
    ):
        log.debug(
            "Sync: LWW skip %s/%s — incoming %s <= local %s",
            event.entity_type, event.entity_id, incoming_updated, existing_updated,
        )
        return False

    for name, value in incoming.items():
        if name == "id":
            continue  # never reassign the primary key
        setattr(existing, name, value)
    log.info(
        "Sync: updated %s/%s (seq=%s)",
        event.entity_type, event.entity_id, event.seq,
    )
    return True


def fetch_outbox_events_since(
    session: Session,
    since_seq: int,
    limit: int = 100,
) -> list[OutboxEvent]:
    """Fetch outbox events since a given sequence number.

    Used by the /api/v1/sync/since endpoint.
    """
    return list(
        session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.seq > since_seq)
            .order_by(OutboxEvent.seq)
            .limit(limit)
        )
    )


def entity_to_dict(entity: Any) -> dict[str, Any]:
    """Convert a SQLAlchemy entity to a JSON-serializable dict.

    Helper for creating outbox event payloads.
    """
    result = {}
    for column in entity.__table__.columns:
        value = getattr(entity, column.name)
        # Handle datetime serialization
        if isinstance(value, datetime):
            result[column.name] = value.isoformat()
        else:
            result[column.name] = value
    return result
