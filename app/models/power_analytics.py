from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import ts_column


class DevicePowerSample(Base):
    __tablename__ = "device_power_samples"

    # Same SQLite-variant trick as DevicePowerRollup / DeviceHeartbeat —
    # Postgres production uses BIGINT with a sequence; the SQLite test
    # path needs INTEGER PRIMARY KEY for ROWID-alias autoincrement.
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
    channel_id: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    sampled_at: Mapped[datetime] = ts_column(default_now=False, nullable=False)
    received_at: Mapped[datetime] = ts_column()
    sampled_uptime_seconds: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="steady")
    source_flags: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    v_v: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    i_ma: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # v0.5.66 (P1.3): the CSE7766 firmware clamps measured current below
    # ~50 mA to zero, so a real standby load reports `i_ma=0` with a
    # non-zero `p_w`. Firmware 0.1.27+ disambiguates: `i_ma_estimated`
    # True means `i_ma` was clamped and `i_ma_estimate` holds the
    # firmware's standby estimate. Consumers MUST NOT read `i_ma=0` as
    # "no activity" when `i_ma_estimated` is True. Both nullable —
    # pre-0.1.27 firmware omits them.
    i_ma_estimated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    i_ma_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    p_w: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    # Heartbeat-folded power summary (firmware retires the dedicated
    # /device/power-samples endpoint and folds a compact `power` object
    # into the heartbeat). A `source="heartbeat"` row carries the
    # interval's min/avg/max watts: `p_w` holds the average, `min_w` /
    # `max_w` the extremes. Both nullable — a per-sample upload from the
    # dedicated endpoint leaves them NULL (one sample has no min/max).
    min_w: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    max_w: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    s_va: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    pf: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    hz: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    energy_wh: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    rssi_dbm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tx_retry_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    beacon_miss_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crc_fail_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chip_type: Mapped[str | None] = mapped_column(String(32), nullable=True)


Index(
    "ix_device_power_samples_device_channel_sampled",
    DevicePowerSample.device_id,
    DevicePowerSample.channel_id,
    DevicePowerSample.sampled_at.desc(),
)
Index("ix_device_power_samples_received", DevicePowerSample.received_at.desc())
