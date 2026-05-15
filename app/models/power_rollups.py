"""Daily power rollups — v0.5.29 (B16 Phase 1C).

One row per (device, day) pair. Aggregated nightly from
`device_power_samples` by `services.device_power_rollups
.compute_daily_rollups()`. Keeps the raw-samples table small
(rollups are stable; raw can be retention-pruned later — see B16
design doc) and makes charts cheap (Device-detail sparkline + fleet
timeseries both read this table, not the raw samples).

`day_bucket` is the midnight-UTC timestamp of the rollup day.
Unique on `(device_id, day_bucket)` so re-running the same day
upserts cleanly (an idempotent re-run after a sample backfill is
the expected operator workflow).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import ts_column


class DevicePowerRollup(Base):
    __tablename__ = "device_power_rollups"

    # Same SQLite-variant trick as DeviceHeartbeat — Postgres production
    # uses BIGINT with a sequence; the SQLite test path needs INTEGER
    # PRIMARY KEY for ROWID-alias autoincrement.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    device_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Midnight-UTC timestamp of the rollup day. Using TIMESTAMPTZ
    # instead of DATE to stay consistent with the rest of the schema's
    # tz-aware columns.
    day_bucket: Mapped[datetime] = ts_column(default_now=False, nullable=False)
    computed_at: Mapped[datetime] = ts_column()
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # v0.5.55 (P1.2): how many of `sample_count` were synthetic-fallback
    # samples (source='synthetic'). Lets consumers flag a rollup day whose
    # avg/min/max is partly or wholly synthetic rather than real CSE7766
    # data. Nullable — rollups computed before P1.2 leave it NULL.
    synthetic_sample_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    # All numeric, all nullable — devices may report power without
    # energy and vice versa.
    avg_w: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    min_w: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    max_w: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    # kWh integrated over the day. Source-of-truth for cost calc
    # (planned v0.5.30): cumulative energy_wh diff when the device
    # reports it; otherwise null and the cost widget shows "—".
    kwh: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)


UniqueConstraint(
    DevicePowerRollup.device_id,
    DevicePowerRollup.day_bucket,
    name="uq_device_power_rollups_device_day",
)
Index(
    "ix_device_power_rollups_device_day",
    DevicePowerRollup.device_id,
    DevicePowerRollup.day_bucket.desc(),
)
