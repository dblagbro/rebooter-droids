"""External-source integrations (B17 Layer 1 — Roku ECP first).

`ExternalSensorSource` is an operator-registered external system the
hub polls on a schedule (initially Roku ECP at port 8060). Each poll
appends a row to `ExternalSensorSample`. Watchdog rules can read
either via the new `roku_app_active` probe kind.

Other source kinds (Home Assistant, MQTT, EPG, Plex, etc.) reuse the
same shape. See `docs/BACKLOG.md` B17 for the integration roadmap.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column

# Allowed `kind` values. New integrations append to this list and the
# poller dispatcher in `services/external_sensors.py::poll_source`.
EXTERNAL_SOURCE_KINDS = ("roku",)


class ExternalSensorSource(Base):
    __tablename__ = "external_sensor_sources"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "ext")
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    host: Mapped[str] = mapped_column(String(200), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=8060)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    poll_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30
    )

    last_polled_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)
    last_success_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()


class ExternalSensorSample(Base):
    __tablename__ = "external_sensor_samples"

    # SQLite compatibility: BIGINT PK without ROWID-alias doesn't
    # autoincrement; see app/models/devices.py::DeviceHeartbeat for
    # the same defensive variant. Postgres production behavior is
    # unchanged.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    source_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("external_sensor_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    sampled_at: Mapped[datetime] = ts_column()
    # The shape inside `payload` is source-kind-specific. For Roku
    # ECP: {"active_app": "Spectrum TV", "active_app_id": "31",
    #       "screensaver": false}.
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


Index(
    "ix_external_sensor_samples_source_sampled",
    ExternalSensorSample.source_id,
    ExternalSensorSample.sampled_at.desc(),
)
