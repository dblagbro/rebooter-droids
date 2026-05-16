"""Sync-emission hooks — B11 / RFC-004 Option C.

Mapper-level SQLAlchemy event listeners on the four syncable models
(Device, Site, Group, User). Every insert/update/delete of a syncable
entity writes a row into ``outbox_events`` on the *same* DB connection
and transaction as the mutation, so multi-hub sync converges without
each mutation call-site having to emit explicitly.

Why hooks, not audit-action parsing: emission must not depend on a
human-readable audit string being present and correctly verbed at every
call-site — it wasn't (see CHANGELOG v0.5.70: `sites.py` had zero audit
calls, device-create on `/register` was unaudited, `user` verbs didn't
match). A mapper hook fires on the actual row write, so it can neither
miss a mutation nor misfire.

Loop prevention: the applier (`sync.apply_outbox_event`) writes to these
same tables; it runs inside `sync.suppress_emission()`, and these hooks
no-op while that flag is set — otherwise an applied peer event would
re-emit and ping-pong between hubs forever.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import event, inspect

from app.models import Device, Group, Site, User
from app.models.sync import OutboxEvent
from app.services.sync import emission_suppressed, entity_to_dict

log = logging.getLogger(__name__)

# entity_type label per syncable model.
_MODEL_ENTITY_TYPE: dict[type, str] = {
    Device: "device",
    Site: "site",
    Group: "group",
    User: "user",
}

# Columns whose change *alone* must NOT trigger an `updated` event.
# `updated_at` bumps on every write. The Device telemetry columns are
# refreshed on each hub independently from that device's own heartbeats
# (RFC-004: operational state is per-hub, only config state syncs), so
# replicating them through the outbox is both redundant and high-churn —
# every heartbeat would otherwise emit a full device snapshot.
_NON_EMITTING_COLUMNS: dict[str, set[str]] = {
    "device": {
        "updated_at",
        "last_heartbeat_at",
        "firmware_version",
        "local_ip",
        "last_reported_config",
        "reported_recovery_mode",
        "reported_auto_recovery_triggered",
        "reported_last_known_good_restored",
        "reported_consecutive_unhealthy_boots",
        "reported_in_captive_portal",
        "reported_central_enabled",
        "reported_central_registered",
        "reported_central_state",
    },
    "site": {"updated_at"},
    "group": {"updated_at"},
    "user": {"updated_at"},
}


def _emit(connection, entity_type, entity_id, verb, payload, *, tombstone_for=None):
    """Insert one outbox event on the mutation's own connection."""
    connection.execute(
        OutboxEvent.__table__.insert().values(
            at=datetime.now(timezone.utc),
            event_type=f"{entity_type}.{verb}",
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            tombstone_for=tombstone_for,
            scope_claims=None,
        )
    )
    log.debug("Sync emit: %s.%s %s", entity_type, verb, entity_id)


def _after_insert(mapper, connection, target):
    if emission_suppressed():
        return
    entity_type = _MODEL_ENTITY_TYPE[type(target)]
    _emit(connection, entity_type, target.id, "created", entity_to_dict(target))


def _after_update(mapper, connection, target):
    if emission_suppressed():
        return
    entity_type = _MODEL_ENTITY_TYPE[type(target)]
    ignore = _NON_EMITTING_COLUMNS.get(entity_type, {"updated_at"})
    insp = inspect(target)
    # Emit only if a column *outside* the ignore set actually changed —
    # otherwise this is heartbeat/telemetry churn, not a config change.
    changed = any(
        col.name not in ignore
        and insp.attrs[col.name].history.has_changes()
        for col in target.__table__.columns
    )
    if not changed:
        return
    _emit(connection, entity_type, target.id, "updated", entity_to_dict(target))


def _after_delete(mapper, connection, target):
    if emission_suppressed():
        return
    entity_type = _MODEL_ENTITY_TYPE[type(target)]
    _emit(
        connection,
        entity_type,
        target.id,
        "deleted",
        {"deleted": True},
        tombstone_for=target.id,
    )


_registered = False


def register_sync_emission() -> None:
    """Attach the emission hooks to the syncable models.

    Idempotent — safe to call once per process from ``create_app()``.
    """
    global _registered
    if _registered:
        return
    for model in _MODEL_ENTITY_TYPE:
        event.listen(model, "after_insert", _after_insert)
        event.listen(model, "after_update", _after_update)
        event.listen(model, "after_delete", _after_delete)
    _registered = True
    log.info(
        "Sync emission hooks registered for %s",
        ", ".join(sorted(_MODEL_ENTITY_TYPE.values())),
    )
