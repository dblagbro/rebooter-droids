from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import DeviceEvent, DevicePowerSample, GroupMembership

MAX_BATCH = 200
MAX_POWER_BATCH = 3600
# `heartbeat` joins the taxonomy for Tier-2: the firmware retires the
# dedicated /device/power-samples endpoint and folds a compact `power`
# summary object into the heartbeat. A `source="heartbeat"` row is a
# real (CSE7766-derived) measurement just like steady/burst — it is the
# interval's min/avg/max rather than a single instantaneous sample.
ALLOWED_POWER_SOURCES = {"steady", "burst", "synthetic", "heartbeat"}


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


def _as_bool(value) -> bool | None:
    """Lenient bool coercion — JSON `true`/`false`, 0/1, or a string.
    None passes through (field absent)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _first(raw: dict, *keys):
    """First present (non-None) value among `keys` — used where the
    firmware upload-row key for a field is still being pinned down."""
    for k in keys:
        v = raw.get(k)
        if v is not None:
            return v
    return None


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

            # v0.5.66 (P1.3): low-load current semantics (firmware
            # 0.1.27+). The firmware clamps measured current below
            # ~50 mA to zero, so a real standby load uploads `i_ma=0`
            # with an `i_ma_estimated` flag + an `i_ma_estimate`. We
            # accept the short `i_ma_*` upload keys and the firmware's
            # published `power_*` status-field names — see the firmware
            # note `docs/notes/2026-05-15-to-firmware-current-semantics.md`.
            i_ma_estimated = _as_bool(
                _first(raw, "i_ma_estimated", "power_current_estimated",
                       "current_estimated")
            )
            i_ma_estimate = _as_int(
                _first(raw, "i_ma_estimate", "power_estimated_current_ma",
                       "estimated_current_ma"),
                "i_ma_estimate",
            )

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
                i_ma_estimated=i_ma_estimated,
                i_ma_estimate=i_ma_estimate,
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


def ingest_power_summary(
    session,
    device_id: str,
    summary: dict,
    received_at: datetime,
) -> bool:
    """Tier-2: store the compact `power` summary object the firmware now
    folds into the `/device/heartbeat` payload.

    The dedicated `/device/power-samples` endpoint is being retired; the
    firmware sends one summary per heartbeat interval instead. The shape:

        "power": {
          "min_w": 1.2, "avg_w": 12.4, "max_w": 90.1,   # interval watts
          "v_v": 122.7, "i_a": 0.10, "pf": 0.62,         # latest readings
          "energy_wh": 14821,                            # cumulative Wh
          "valid_frame_count": 58,                       # CSE7766 frames
          "invalid_frame_count": 2,
          "sampled_uptime_seconds": 87421,               # provenance
          "i_ma_estimated": false, "i_ma_estimate": null # low-load semantics
        }

    Stored as a single `DevicePowerSample` row with `source="heartbeat"`,
    consistent with the existing power data model — `avg_w` lands in the
    canonical `p_w` column (so every existing power query/rollup keeps
    working unchanged), the extremes in `min_w`/`max_w`, and the CSE7766
    frame counts reuse `crc_fail_count` (invalid frames) plus a new
    nothing — invalid frames are the data-quality signal already charted.

    Org scope derives via device→site, exactly like every other power
    row — `DevicePowerSample` carries no own scope column.

    Runs inside the caller's `record_heartbeat` session_scope so there is
    no extra round-trip / transaction. Returns True when a row was
    written, False when the summary is absent/malformed (best-effort —
    a bad `power` object must never block heartbeat ingestion).
    """
    if not isinstance(summary, dict):
        return False

    # `avg_w` is the one required field — without an average there is no
    # meaningful power row to store. Accept the legacy `p_w` alias too.
    avg = _as_float(_first(summary, "avg_w", "p_w"), "power.avg_w")
    if avg is None:
        return False

    min_w = _as_float(_first(summary, "min_w"), "power.min_w")
    max_w = _as_float(_first(summary, "max_w"), "power.max_w")

    # Latest instantaneous V / A / PF. The firmware reports amps; the
    # column stores milliamps (matching the dedicated endpoint).
    v_v = _as_float(summary.get("v_v"), "power.v_v")
    i_a = _as_float(_first(summary, "i_a", "i_amps"), "power.i_a")
    i_ma = _as_int(summary.get("i_ma"), "power.i_ma")
    if i_ma is None and i_a is not None:
        i_ma = int(round(i_a * 1000))
    pf = _as_float(summary.get("pf"), "power.pf")
    hz = _as_float(summary.get("hz"), "power.hz")
    energy_wh = _as_int(summary.get("energy_wh"), "power.energy_wh")

    # CSE7766 frame counts — the invalid count is the existing
    # data-quality signal; map it onto `crc_fail_count` so the existing
    # power-health surfaces pick it up with no new column.
    invalid_frames = _as_int(
        _first(summary, "invalid_frame_count", "power_invalid_frame_count"),
        "power.invalid_frame_count",
    )

    # Low-load current semantics (firmware 0.1.27+) carry through.
    i_ma_estimated = _as_bool(
        _first(summary, "i_ma_estimated", "power_current_estimated")
    )
    i_ma_estimate = _as_int(
        _first(summary, "i_ma_estimate", "power_estimated_current_ma"),
        "power.i_ma_estimate",
    )

    sampled_uptime = _as_int(
        summary.get("sampled_uptime_seconds"), "power.sampled_uptime_seconds"
    )

    row = DevicePowerSample(
        device_id=device_id,
        channel_id=0,
        sampled_at=received_at,
        received_at=received_at,
        sampled_uptime_seconds=sampled_uptime,
        source="heartbeat",
        source_flags=0,
        v_v=v_v,
        i_ma=i_ma,
        i_ma_estimated=i_ma_estimated,
        i_ma_estimate=i_ma_estimate,
        p_w=avg,
        min_w=min_w,
        max_w=max_w,
        pf=pf,
        hz=hz,
        energy_wh=energy_wh,
        crc_fail_count=invalid_frames,
    )
    session.add(row)
    return True


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
