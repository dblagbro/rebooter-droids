"""Power-telemetry queries — v0.5.26 (B16 Phase 1A).

Read-only surface over `device_power_samples`. No ingestion here —
that's `services.events.ingest_power_samples()` driven by the
`POST /api/v1/device/power-samples` endpoint shipped in v0.5.12.

Designed for three callers:

- `get_device_detail()` — latest sample + a short raw-window for the
  Device-detail Power tab.
- The (future v0.5.27) fleet `/app/power` page — aggregate summary
  across all devices.
- The (future v0.5.26) devices-list "latest W" chip — single
  most-recent reading per row.

Cadence/staleness model: if a device hasn't sampled in
`MAX_FRESH_SAMPLE_AGE_SECONDS` (default 5 min) we surface the sample
as "stale" so the UI can chip it amber instead of pretending it's
current. The empty-state path is distinct from stale — operator
sees "never reported" vs "last reported 23 min ago" vs "ON 87 W".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import func, select, text

from app.db import session_scope
from app.models import Device, DevicePowerRollup, DevicePowerSample


MAX_FRESH_SAMPLE_AGE_SECONDS = 300  # 5 min — beyond this, surface as stale
RECENT_WINDOW_DEFAULT_SECONDS = 60 * 60  # 1 h — Device-detail recent series
RECENT_WINDOW_MAX_SECONDS = 24 * 60 * 60  # 24 h — Device-detail upper cap


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _serialize_sample(s: DevicePowerSample, *, now: datetime | None = None) -> dict:
    """Render a sample row to the dict shape templates + API consume.

    Numeric (Decimal) columns are coerced to float so the JSON envelope
    + Jinja templates don't have to deal with Decimal arithmetic.
    """
    now = now or datetime.now(timezone.utc)
    sampled_at = s.sampled_at
    age_seconds: int | None = None
    if sampled_at is not None:
        if sampled_at.tzinfo is None:
            sampled_at = sampled_at.replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((now - sampled_at).total_seconds()))

    def _f(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        "id": s.id,
        "channel_id": s.channel_id,
        "sampled_at": _iso(sampled_at),
        "received_at": _iso(s.received_at),
        "age_seconds": age_seconds,
        "is_stale": (
            age_seconds is not None and age_seconds > MAX_FRESH_SAMPLE_AGE_SECONDS
        ),
        "source": s.source,
        "source_flags": s.source_flags,
        "v_v": _f(s.v_v),
        "i_ma": s.i_ma,
        "p_w": _f(s.p_w),
        "s_va": _f(s.s_va),
        "pf": _f(s.pf),
        "hz": _f(s.hz),
        "energy_wh": s.energy_wh,
        "rssi_dbm": s.rssi_dbm,
        "chip_type": s.chip_type,
        "sampled_uptime_seconds": s.sampled_uptime_seconds,
    }


def latest_sample(device_id: str, *, channel_id: int = 0) -> dict | None:
    """Latest power sample for the device (or None if no samples ever).

    Cheap: one indexed lookup on
    `ix_device_power_samples_device_channel_sampled`.
    """
    if not device_id:
        return None
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.scalar(
            select(DevicePowerSample)
            .where(
                DevicePowerSample.device_id == device_id,
                DevicePowerSample.channel_id == channel_id,
            )
            .order_by(DevicePowerSample.sampled_at.desc())
            .limit(1)
        )
        if row is None:
            return None
        return _serialize_sample(row, now=now)


def latest_samples_by_device(
    device_ids: Iterable[str], *, channel_id: int = 0
) -> dict[str, dict]:
    """One-query batch lookup — used by the devices-list "latest W" chip.

    Returns a dict keyed by device_id; devices with no samples are absent.
    Single SELECT with `(device_id, sampled_at desc)` ordering; in-Python
    setdefault wins the newest row per device.
    """
    ids = [d for d in device_ids if d]
    if not ids:
        return {}
    now = datetime.now(timezone.utc)
    out: dict[str, dict] = {}
    with session_scope() as session:
        # Single ordered scan + first-wins per device. For larger fleets
        # this could move to a window-function or distinct-on, but with
        # the current fleet size (~7) it's a non-issue.
        rows = session.scalars(
            select(DevicePowerSample)
            .where(
                DevicePowerSample.device_id.in_(ids),
                DevicePowerSample.channel_id == channel_id,
            )
            .order_by(
                DevicePowerSample.device_id.asc(),
                DevicePowerSample.sampled_at.desc(),
            )
        )
        for row in rows:
            if row.device_id in out:
                continue
            out[row.device_id] = _serialize_sample(row, now=now)
    return out


def recent_samples(
    device_id: str,
    *,
    channel_id: int = 0,
    window_seconds: int = RECENT_WINDOW_DEFAULT_SECONDS,
    limit: int = 720,
) -> list[dict]:
    """Raw sample window for the Device-detail Power tab.

    `window_seconds` is clamped to [60, 86400]. `limit` is a defensive
    upper bound — at 1 Hz cadence a 24h window is 86400 rows, way more
    than we want to render. Default 720 ≈ 1 sample per minute over a
    12-hour window.
    """
    if not device_id:
        return []
    win = max(60, min(int(window_seconds or RECENT_WINDOW_DEFAULT_SECONDS), RECENT_WINDOW_MAX_SECONDS))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=win)
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(DevicePowerSample)
                .where(
                    DevicePowerSample.device_id == device_id,
                    DevicePowerSample.channel_id == channel_id,
                    DevicePowerSample.sampled_at >= cutoff,
                )
                .order_by(DevicePowerSample.sampled_at.desc())
                .limit(int(limit))
            )
        )
        return [_serialize_sample(r, now=now) for r in rows]


# ── Fleet summary (used by Phase 1B /app/power) ──────────────────────────


def fleet_summary(*, window_seconds: int = 24 * 60 * 60) -> dict:
    """Compact aggregate across all devices for the given window.

    Today returns per-device:
      - latest sample (if any)
      - sample_count in window
      - avg / min / max watts in window
      - cumulative energy in window (when device reports energy_wh)

    Aggregates are computed at the SQL layer so this stays cheap on a
    fleet of any reasonable size.
    """
    win = max(60, min(int(window_seconds or 24 * 60 * 60), 30 * 24 * 60 * 60))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=win)

    with session_scope() as session:
        # Per-device aggregates over the window.
        agg_rows = list(
            session.execute(
                select(
                    DevicePowerSample.device_id.label("device_id"),
                    func.count(DevicePowerSample.id).label("sample_count"),
                    func.avg(DevicePowerSample.p_w).label("avg_w"),
                    func.min(DevicePowerSample.p_w).label("min_w"),
                    func.max(DevicePowerSample.p_w).label("max_w"),
                    func.min(DevicePowerSample.sampled_at).label("first_at"),
                    func.max(DevicePowerSample.sampled_at).label("last_at"),
                )
                .where(
                    DevicePowerSample.channel_id == 0,
                    DevicePowerSample.sampled_at >= cutoff,
                )
                .group_by(DevicePowerSample.device_id)
            )
        )

        # Device-row info for friendly labels.
        device_ids = [r.device_id for r in agg_rows]
        names: dict[str, str] = {}
        if device_ids:
            for d in session.scalars(
                select(Device).where(Device.id.in_(device_ids))
            ):
                names[d.id] = d.display_name or d.id

        # Latest sample per device, for the "live W" column.
        latest = latest_samples_by_device(device_ids) if device_ids else {}

    per_device: list[dict] = []
    total_avg = 0.0
    total_max = 0.0
    for r in agg_rows:
        avg_w = float(r.avg_w) if r.avg_w is not None else None
        min_w = float(r.min_w) if r.min_w is not None else None
        max_w = float(r.max_w) if r.max_w is not None else None
        per_device.append({
            "device_id": r.device_id,
            "display_name": names.get(r.device_id, r.device_id),
            "sample_count": int(r.sample_count or 0),
            "avg_w": avg_w,
            "min_w": min_w,
            "max_w": max_w,
            "first_sample_at": _iso(r.first_at),
            "last_sample_at": _iso(r.last_at),
            "latest_sample": latest.get(r.device_id),
        })
        if avg_w is not None:
            total_avg += avg_w
        if max_w is not None and max_w > total_max:
            total_max = max_w

    # Sort biggest-hogs first for the future fleet view.
    per_device.sort(key=lambda d: (d["avg_w"] or 0), reverse=True)
    return {
        "window_seconds": win,
        "window_started_at": _iso(cutoff),
        "now": _iso(now),
        "device_count": len(per_device),
        "fleet_avg_w": total_avg or None,
        "fleet_peak_w": total_max or None,
        "per_device": per_device,
    }


# ── Daily rollups (Phase 1C — v0.5.29) ────────────────────────────────


def _serialize_rollup(r: DevicePowerRollup) -> dict:
    def _f(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return {
        "device_id": r.device_id,
        "day_bucket": _iso(r.day_bucket),
        "sample_count": r.sample_count,
        "avg_w": _f(r.avg_w),
        "min_w": _f(r.min_w),
        "max_w": _f(r.max_w),
        "kwh": _f(r.kwh),
        "computed_at": _iso(r.computed_at),
    }


def daily_rollups_for_device(
    device_id: str, *, days: int = 7
) -> list[dict]:
    """Most-recent N daily rollups for a device, newest-first.

    Used by the per-device sparkline on the Device-detail Power tab.
    Default 7 days; clamped to [1, 365].
    """
    if not device_id:
        return []
    n = max(1, min(int(days or 7), 365))
    cutoff = datetime.now(timezone.utc) - timedelta(days=n)
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(DevicePowerRollup)
                .where(
                    DevicePowerRollup.device_id == device_id,
                    DevicePowerRollup.day_bucket >= cutoff,
                )
                .order_by(DevicePowerRollup.day_bucket.desc())
            )
        )
        return [_serialize_rollup(r) for r in rows]


def fleet_daily_rollups(*, days: int = 30) -> dict:
    """Fleet timeseries — every device's daily rollups for the last N days.

    Used by the fleet `/app/power` chart (Phase 1C). Returns a shape
    convenient for stacked-bar rendering: a list of day buckets +
    per-device totals keyed by device_id.
    """
    n = max(1, min(int(days or 30), 365))
    cutoff = datetime.now(timezone.utc) - timedelta(days=n)
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(DevicePowerRollup)
                .where(DevicePowerRollup.day_bucket >= cutoff)
                .order_by(DevicePowerRollup.day_bucket.asc())
            )
        )
        device_ids = sorted({r.device_id for r in rows})
        names: dict[str, str] = {}
        if device_ids:
            for d in session.scalars(
                select(Device).where(Device.id.in_(device_ids))
            ):
                names[d.id] = d.display_name or d.id

    # Pivot into per-day-per-device dict.
    day_keys: list[str] = sorted({_iso(r.day_bucket) for r in rows})
    by_day: dict[str, dict[str, float | None]] = {k: {} for k in day_keys}
    for r in rows:
        day = _iso(r.day_bucket)
        if r.avg_w is None:
            continue
        by_day[day][r.device_id] = float(r.avg_w)
    return {
        "days": n,
        "day_keys": day_keys,
        "device_ids": device_ids,
        "device_names": names,
        "avg_w_by_day": [
            {
                "day": k,
                "values": [by_day[k].get(did) for did in device_ids],
            }
            for k in day_keys
        ],
    }


def compute_daily_rollups(*, day: datetime | None = None) -> dict:
    """Aggregate one day's worth of `device_power_samples` into the
    `device_power_rollups` table. Idempotent — re-runs for the same
    `day` upsert via `INSERT ... ON CONFLICT (device_id, day_bucket)
    DO UPDATE`.

    `day` is the start-of-day-UTC timestamp; defaults to "yesterday in
    UTC" which matches the typical nightly-cron use case. Returns a
    stats dict.

    SQL-only aggregation — no per-row Python loop — so this scales
    cheaply to large fleets.
    """
    now = datetime.now(timezone.utc)
    if day is None:
        # Yesterday's full UTC day.
        yest = now - timedelta(days=1)
        day = yest.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        day = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day + timedelta(days=1)

    stats = {
        "day": _iso(day),
        "day_end": _iso(day_end),
        "device_count": 0,
        "rollups_written": 0,
    }

    with session_scope() as session:
        # Per-device aggregation for the target day. kWh is the
        # cumulative `energy_wh` delta within the day (works when
        # firmware reports monotonically increasing energy_wh) divided
        # by 1000. Devices that don't report energy_wh land NULL.
        agg = list(
            session.execute(
                select(
                    DevicePowerSample.device_id.label("device_id"),
                    func.count(DevicePowerSample.id).label("sample_count"),
                    func.avg(DevicePowerSample.p_w).label("avg_w"),
                    func.min(DevicePowerSample.p_w).label("min_w"),
                    func.max(DevicePowerSample.p_w).label("max_w"),
                    (
                        (func.max(DevicePowerSample.energy_wh)
                         - func.min(DevicePowerSample.energy_wh))
                        / 1000.0
                    ).label("kwh"),
                )
                .where(
                    DevicePowerSample.channel_id == 0,
                    DevicePowerSample.sampled_at >= day,
                    DevicePowerSample.sampled_at < day_end,
                )
                .group_by(DevicePowerSample.device_id)
            )
        )
        stats["device_count"] = len(agg)
        for row in agg:
            # Upsert via raw SQL — Postgres ON CONFLICT keeps the path
            # idempotent under re-runs (backfill after a samples
            # correction is the expected workflow). SQLite test path
            # uses INSERT OR REPLACE.
            dialect_name = session.bind.dialect.name if session.bind else "postgresql"
            if dialect_name == "sqlite":
                stmt = text(
                    "INSERT OR REPLACE INTO device_power_rollups "
                    "(device_id, day_bucket, computed_at, sample_count, "
                    " avg_w, min_w, max_w, kwh) "
                    "VALUES (:device_id, :day_bucket, :computed_at, "
                    " :sample_count, :avg_w, :min_w, :max_w, :kwh)"
                )
            else:
                stmt = text(
                    "INSERT INTO device_power_rollups "
                    "(device_id, day_bucket, computed_at, sample_count, "
                    " avg_w, min_w, max_w, kwh) "
                    "VALUES (:device_id, :day_bucket, :computed_at, "
                    " :sample_count, :avg_w, :min_w, :max_w, :kwh) "
                    "ON CONFLICT (device_id, day_bucket) DO UPDATE SET "
                    " computed_at = EXCLUDED.computed_at, "
                    " sample_count = EXCLUDED.sample_count, "
                    " avg_w = EXCLUDED.avg_w, "
                    " min_w = EXCLUDED.min_w, "
                    " max_w = EXCLUDED.max_w, "
                    " kwh = EXCLUDED.kwh"
                )
            session.execute(stmt, {
                "device_id": row.device_id,
                "day_bucket": day,
                "computed_at": now,
                "sample_count": int(row.sample_count or 0),
                "avg_w": float(row.avg_w) if row.avg_w is not None else None,
                "min_w": float(row.min_w) if row.min_w is not None else None,
                "max_w": float(row.max_w) if row.max_w is not None else None,
                "kwh": float(row.kwh) if row.kwh is not None else None,
            })
            stats["rollups_written"] += 1

    return stats
