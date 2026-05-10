"""Unified history feed (v0.4.30, C1 from redesign-continuation-plan-v2).

Combines three on-disk event sources into one normalised stream:

- ``audit``        — `audit_events` (admin mutations + per-record audit slices)
- ``watchdog_probe`` — `watchdog_probe_events` (probe outcomes per rule)
- ``device_event``  — `device_events` (device-emitted events posted via
                      `POST /api/v1/device/events`)

The unified shape lets the `/app/history` page chip-filter by source
without three separate queries. Schedule fires and notification sends
will join when their tables exist (currently they live in `audit_events`
already as `schedule.*` / `notification.*` actions, so the audit source
covers them transitively).

A request for `source=all` returns the time-merged union, ordered by
event timestamp descending and capped by ``limit``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterator

from sqlalchemy import cast, or_, select
from sqlalchemy.types import Text

from app.db import session_scope
from app.models import AuditEvent, DeviceEvent, WatchdogProbeEvent


SOURCES = ("audit", "watchdog_probe", "device_event")


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _row_audit(e: AuditEvent) -> dict:
    return {
        "source": "audit",
        "at": _iso(e.at),
        "at_dt": e.at,
        "actor": e.actor_email_snapshot or e.actor_user_id or "",
        "action": e.action,
        "target_type": e.target_type,
        "target_id": e.target_id,
        "details": e.details or {},
        "ip": e.ip,
    }


def _row_probe(e: WatchdogProbeEvent) -> dict:
    return {
        "source": "watchdog_probe",
        "at": _iso(e.at),
        "at_dt": e.at,
        "actor": "watchdog",
        "action": f"watchdog_probe.{e.outcome}",
        "target_type": "watchdog_rule",
        "target_id": e.rule_id,
        "details": e.details or {},
        "ip": None,
    }


def _row_device_evt(e: DeviceEvent) -> dict:
    return {
        "source": "device_event",
        "at": _iso(e.timestamp),
        "at_dt": e.timestamp,
        "actor": e.device_id,
        "action": f"device_event.{e.type}",
        "target_type": "device",
        "target_id": e.device_id,
        "details": {
            "mode": e.mode,
            "message": e.message,
            **(e.details or {}),
        },
        "ip": None,
    }


def _audit_iter(
    session,
    *,
    actor_user_id: str | None,
    action: str | None,
    action_prefix: str | None,
    target_type: str | None,
    target_id: str | None,
    q: str | None,
    limit: int,
) -> Iterator[dict]:
    stmt = select(AuditEvent)
    if actor_user_id:
        stmt = stmt.where(AuditEvent.actor_user_id == actor_user_id)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if action_prefix:
        stmt = stmt.where(AuditEvent.action.like(f"{action_prefix}.%"))
    if target_type:
        stmt = stmt.where(AuditEvent.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditEvent.target_id == target_id)
    if q:
        # v0.4.32 (C3): free-text search across the row + its details
        # JSON. Cast details::text on the SQL side so it's a single
        # LIKE pass; combine with the indexed scalar columns so an
        # operator can grep for an email, a device id, or a value
        # that lives inside the JSON blob in one box.
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                AuditEvent.action.ilike(like),
                AuditEvent.actor_email_snapshot.ilike(like),
                AuditEvent.actor_user_id.ilike(like),
                AuditEvent.target_type.ilike(like),
                AuditEvent.target_id.ilike(like),
                cast(AuditEvent.details, Text).ilike(like),
            )
        )
    stmt = stmt.order_by(AuditEvent.at.desc()).limit(limit)
    for e in session.scalars(stmt):
        yield _row_audit(e)


def _probe_iter(
    session,
    *,
    target_id: str | None,
    action_prefix: str | None,
    q: str | None,
    limit: int,
) -> Iterator[dict]:
    stmt = select(WatchdogProbeEvent)
    if target_id:
        stmt = stmt.where(WatchdogProbeEvent.rule_id == target_id)
    if action_prefix:
        # chip "watchdog_probe" applies to this source; anything else
        # filters everything out so it shows nothing — which is the
        # correct behaviour, matching the audit-side prefix semantics.
        if not "watchdog_probe".startswith(action_prefix) and not action_prefix.startswith("watchdog_probe"):
            return
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                WatchdogProbeEvent.rule_id.ilike(like),
                WatchdogProbeEvent.outcome.ilike(like),
                cast(WatchdogProbeEvent.details, Text).ilike(like),
            )
        )
    stmt = stmt.order_by(WatchdogProbeEvent.at.desc()).limit(limit)
    for e in session.scalars(stmt):
        yield _row_probe(e)


def _device_evt_iter(
    session,
    *,
    target_id: str | None,
    action_prefix: str | None,
    q: str | None,
    limit: int,
) -> Iterator[dict]:
    stmt = select(DeviceEvent)
    if target_id:
        stmt = stmt.where(DeviceEvent.device_id == target_id)
    if action_prefix:
        if not "device_event".startswith(action_prefix) and not action_prefix.startswith("device_event"):
            return
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                DeviceEvent.device_id.ilike(like),
                DeviceEvent.type.ilike(like),
                DeviceEvent.message.ilike(like),
                cast(DeviceEvent.details, Text).ilike(like),
            )
        )
    stmt = stmt.order_by(DeviceEvent.timestamp.desc()).limit(limit)
    for e in session.scalars(stmt):
        yield _row_device_evt(e)


def query_unified(
    *,
    source: str = "audit",
    actor_user_id: str | None = None,
    action: str | None = None,
    action_prefix: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    q: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Return up to ``limit`` rows from the requested source(s),
    newest first. ``source`` is one of:

    - ``"audit"`` (default — back-compat with pre-v0.4.30 callers)
    - ``"watchdog_probe"``
    - ``"device_event"``
    - ``"all"`` — merge of all three, time-ordered

    Free-text filters apply per-source where the field exists; on
    sources without that field, the filter is a no-op (so e.g.
    ``actor_user_id`` only narrows the audit slice).
    """
    limit = max(1, min(limit, 1000))
    src = (source or "audit").lower()
    if src not in (*SOURCES, "all"):
        src = "audit"

    q_norm = (q or "").strip() or None
    out: list[dict] = []
    with session_scope() as session:
        if src in ("audit", "all"):
            out.extend(
                _audit_iter(
                    session,
                    actor_user_id=actor_user_id,
                    action=action,
                    action_prefix=action_prefix,
                    target_type=target_type,
                    target_id=target_id,
                    q=q_norm,
                    limit=limit,
                )
            )
        if src in ("watchdog_probe", "all"):
            out.extend(
                _probe_iter(
                    session,
                    target_id=(target_id if target_type in (None, "watchdog_rule") else None),
                    action_prefix=action_prefix,
                    q=q_norm,
                    limit=limit,
                )
            )
        if src in ("device_event", "all"):
            out.extend(
                _device_evt_iter(
                    session,
                    target_id=(target_id if target_type in (None, "device") else None),
                    action_prefix=action_prefix,
                    q=q_norm,
                    limit=limit,
                )
            )

    if src == "all":
        # Three slices already came back DESC-by-time from their
        # respective queries; collapse and re-sort the combined
        # set so the global ordering is correct.
        out.sort(key=lambda r: r["at_dt"], reverse=True)

    # Drop the helper datetime; templates use the iso string.
    for r in out:
        r.pop("at_dt", None)
    return out[:limit]
