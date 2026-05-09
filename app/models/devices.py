from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column

REGISTRATION_STATES = ("pending", "active", "disabled", "revoked")


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "dev")
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    hardware_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    hardware_revision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mac_address: Mapped[str | None] = mapped_column(String(40), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    local_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    site_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("sites.id", ondelete="SET NULL"), nullable=True
    )

    central_management_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    registration_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )

    capabilities: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_heartbeat_at: Mapped[datetime | None] = ts_column(
        default_now=False, nullable=True
    )

    is_qa_fixture: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()


Index("ix_devices_last_heartbeat_at", Device.last_heartbeat_at)
Index("ix_devices_site_id", Device.site_id)
Index("ix_devices_mac_address", Device.mac_address)


class DeviceCredential(Base):
    __tablename__ = "device_credentials"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "dcr")
    )
    device_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    issued_at: Mapped[datetime] = ts_column()
    last_used_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)


class EnrollmentToken(Base):
    __tablename__ = "enrollment_tokens"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "et")
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    issued_by_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    site_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("sites.id", ondelete="SET NULL"), nullable=True
    )
    display_name_hint: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    consumed_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)
    consumed_by_device_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )

    expires_at: Mapped[datetime] = ts_column(default_now=False, nullable=False)
    created_at: Mapped[datetime] = ts_column()


Index("ix_enrollment_tokens_expires_at", EnrollmentToken.expires_at)


class DeviceHeartbeat(Base):
    __tablename__ = "device_heartbeats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )

    received_at: Mapped[datetime] = ts_column()
    firmware_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    local_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(40), nullable=True)
    relay_on: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    wifi_connected: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    health_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    uptime_seconds: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    incident_cycles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hour_cycles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_event_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_event_at: Mapped[datetime | None] = ts_column(
        default_now=False, nullable=True
    )


Index("ix_device_heartbeats_device_received", DeviceHeartbeat.device_id, DeviceHeartbeat.received_at.desc())
