from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import DeviceEvent, DevicePowerSample, GroupMembership

MAX_BATCH = 200
MAX_POWER_BATCH = 3600
ALLOWED_POWER_SOURCES = {"steady", "burst", "synthetic"}


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


def _as_int(value, field: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer")


def _as_float(value, field: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be numeric")


def ingest_power_samples(device_id: str, samples: list[dict]) -> int:
    if not samples:
        return 0
    if len(samples) > MAX_POWER_BATCH:
        raise ValueError(f"too many power samples in one batch (max {MAX_POWER_BATCH})")

    now = datetime.now(timezone.utc)
    inserted = 0
    with session_scope() as session:
        for raw in samples:
            if not isinstance(raw, dict):
                raise ValueError("each power sample must be an object")
            source = str(raw.get("source") or "steady")[:20]
            if source not in ALLOWED_POWER_SOURCES:
                raise ValueError(
                    f"source must be one of {sorted(ALLOWED_POWER_SOURCES)}"
                )

            sampled_at = _parse_ts(raw.get("sampled_at")) or now
            channel_id = _as_int(raw.get("channel_id", 0), "channel_id") or 0
            if channel_id < 0 or channel_id > 255:
                raise ValueError("channel_id must be in 0..255")

            source_flags = _as_int(raw.get("source_flags", 0), "source_flags") or 0
            if source_flags < 0:
                raise ValueError("source_flags must be >= 0")

            row = DevicePowerSample(
                device_id=device_id,
                channel_id=channel_id,
                sampled_at=sampled_at,
                received_at=now,
                sampled_uptime_seconds=_as_int(
                    raw.get("sampled_uptime_seconds"), "sampled_uptime_seconds"
                ),
                source=source,
                source_flags=source_flags,
                v_v=_as_float(raw.get("v_v"), "v_v"),
                i_ma=_as_int(raw.get("i_ma"), "i_ma"),
                p_w=_as_float(raw.get("p_w"), "p_w"),
                s_va=_as_float(raw.get("s_va"), "s_va"),
                pf=_as_float(raw.get("pf"), "pf"),
                hz=_as_float(raw.get("hz"), "hz"),
                energy_wh=_as_int(raw.get("energy_wh"), "energy_wh"),
                rssi_dbm=_as_int(raw.get("rssi_dbm"), "rssi_dbm"),
                tx_retry_count=_as_int(raw.get("tx_retry_count"), "tx_retry_count"),
                beacon_miss_count=_as_int(
                    raw.get("beacon_miss_count"), "beacon_miss_count"
                ),
                crc_fail_count=_as_int(raw.get("crc_fail_count"), "crc_fail_count"),
                chip_type=(str(raw.get("chip_type"))[:32] if raw.get("chip_type") else None),
            )
            session.add(row)
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
