from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import DeviceEvent, GroupMembership

MAX_BATCH = 200


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def ingest_events(device_id: str, events: list[dict]) -> int:
    if not events:
        return 0
    if len(events) > MAX_BATCH:
        raise ValueError(f"too many events in one batch (max {MAX_BATCH})")
    now = datetime.now(timezone.utc)
    inserted = 0
    with session_scope() as session:
        for raw in events:
            evt = DeviceEvent(
                device_id=device_id,
                type=str(raw.get("type") or "unknown")[:80],
                timestamp=_parse_ts(raw.get("timestamp")) or now,
                received_at=now,
                mode=raw.get("mode"),
                message=raw.get("message"),
                details=raw.get("details") or {},
            )
            session.add(evt)
            inserted += 1
        session.flush()
    return inserted


def query_events(
    device_id: str | None = None,
    group_id: str | None = None,
    type_: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    limit: int = 200,
) -> list[dict]:
    limit = max(1, min(limit, 1000))
    f = _parse_ts(from_ts)
    t = _parse_ts(to_ts)

    with session_scope() as session:
        stmt = select(DeviceEvent)
        if device_id:
            stmt = stmt.where(DeviceEvent.device_id == device_id)
        if group_id:
            stmt = stmt.join(
                GroupMembership, GroupMembership.device_id == DeviceEvent.device_id
            ).where(GroupMembership.group_id == group_id)
        if type_:
            stmt = stmt.where(DeviceEvent.type == type_)
        if f:
            stmt = stmt.where(DeviceEvent.timestamp >= f)
        if t:
            stmt = stmt.where(DeviceEvent.timestamp <= t)
        stmt = stmt.order_by(DeviceEvent.timestamp.desc()).limit(limit)
        rows = list(session.scalars(stmt))
        return [
            {
                "id": e.id,
                "device_id": e.device_id,
                "type": e.type,
                "timestamp": _iso(e.timestamp),
                "received_at": _iso(e.received_at),
                "mode": e.mode,
                "message": e.message,
                "details": e.details,
            }
            for e in rows
        ]
