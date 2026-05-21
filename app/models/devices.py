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
from app.services.tenant_scope import TenantScoped, tenant_scoped_org_column

REGISTRATION_STATES = (
    "pending",
    "active",
    "disabled",
    "revoked",
    # v0.5.7 (B20): set by the "Decommission old + adopt fresh" flow
    # on /app/pending-adoption when MAC dupe is intentional (operator
    # replaces a physical device with a different one that happens to
    # share a MAC, or wants to abandon an old logical device row in
    # favour of the freshly-registered one). Decommissioned rows are
    # hidden from find_by_mac dupe-detection so future re-flashes of
    # the SAME physical box don't surface the abandoned row.
    "decommissioned",
)


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

    # v0.3.2 (P3): operator-set lockout. When True, every power
    # command (relay_on/off/toggle/cycle/restart/hold_off) is
    # rejected unless the caller explicitly opts in via
    # override_lockout=True. Watchdog rules + schedules (P4) MUST
    # honour this flag — no override path for them in v1.
    is_protected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # v0.3.2 (P3): operator's "stay off until I say otherwise"
    # intent. Set by relay_hold_off; cleared by any power-on
    # (relay_on, relay_toggle, relay_cycle). Distinct from the
    # transient `relay_on` field on the latest heartbeat — that's
    # device-side state; this is the operator's intended state.
    is_held_off: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # v0.5.22 (B21): operator-set intended config for centrally managed
    # units. `desired_config` shape matches the locked v0.1 apply_config
    # schema (device_name, relay_restore_behavior, monitor_interval_seconds,
    # boot_warmup_seconds, manual_button_enabled, internet, device,
    # notifications, power — see docs/firmware-apply-config-schema-v01.md).
    # `desired_mode` is the intended top-level mode (smart_plug /
    # internet_watchdog / device_watchdog). `last_reported_config` is the
    # device's most-recent self-reported config (populated from heartbeat
    # payload if present, or from apply_config command-result echoes).
    # All four are additive nullable JSON columns — Base.metadata.create_all()
    # picks them up on next container start. NULL = no operator intent set;
    # behaviour stays identical to today.
    desired_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    desired_mode: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_reported_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    desired_config_updated_at: Mapped[datetime | None] = ts_column(
        default_now=False, nullable=True
    )
    last_config_pushed_at: Mapped[datetime | None] = ts_column(
        default_now=False, nullable=True
    )

    # v0.5.51 (P0.1): device-self-reported recovery/central truth, refreshed
    # on every heartbeat. These are "what is it right now" hot columns — they
    # mirror the firmware fields stored as history on DeviceHeartbeat, and
    # exist so the devices list + state computation can filter without a
    # per-device latest-heartbeat join. `reported_` prefix marks them as
    # device-asserted, distinct from the hub-owned `central_management_enabled`
    # (hub intent) and `registration_state` (hub-side enrollment lifecycle).
    # NULL = device has never reported this field (pre-0.1.19 firmware, or
    # never heartbeated). P0.2 maps these into operator-facing state chips.
    reported_recovery_mode: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reported_auto_recovery_triggered: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    reported_last_known_good_restored: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    reported_consecutive_unhealthy_boots: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    reported_in_captive_portal: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    reported_central_enabled: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    reported_central_registered: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    reported_central_state: Mapped[str | None] = mapped_column(String(40), nullable=True)

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


class EnrollmentToken(TenantScoped, Base):
    # org-boundary phase 3: `organization_id` is NOT NULL with an
    # on-delete RESTRICT FK (migration 0005). A token mints a device
    # into an org's site. See design §2. `Device`, `DeviceCredential`,
    # `DeviceHeartbeat` stay Tier-B (org derived via `device -> site`) —
    # no column.
    __tablename__ = "enrollment_tokens"

    organization_id = tenant_scoped_org_column("RESTRICT")

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

    # v0.5.7 (B20): when set, /device/register REBINDS this device row
    # instead of creating a new one. Used for the
    # restore-after-reflash flow on /app/pending-adoption when the
    # incoming announcement's MAC matches an existing device row.
    target_device_id: Mapped[str | None] = mapped_column(
        String(40),
        ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True,
    )


Index("ix_enrollment_tokens_expires_at", EnrollmentToken.expires_at)
Index("ix_enrollment_tokens_target_device", EnrollmentToken.target_device_id)


class DeviceHeartbeat(Base):
    __tablename__ = "device_heartbeats"

    # `BigInteger().with_variant(Integer, 'sqlite')`: Postgres uses BIGINT
    # with a sequence (the production target); SQLite test paths get a
    # plain INTEGER PK so the ROWID-alias autoincrement actually fires.
    # Without the variant, SQLite emits `id BIGINT PRIMARY KEY` which is
    # NOT a ROWID alias and refuses NULL inserts (causing
    # `IntegrityError: NOT NULL constraint failed: device_heartbeats.id`
    # in test_v0514_*).
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

    # v0.5.51 (P0.1): richer status/recovery/central truth emitted by
    # firmware 0.1.19-dev-central-safe+. Per the firmware status contract
    # (docs/notes/2026-05-14-firmware-status-and-recovery-contract.md), the
    # heartbeat now carries recovery/central-state fields the hub previously
    # discarded. All nullable — older firmware that omits a field lands NULL
    # for that heartbeat. These rows are the *history* (timelines / flap
    # detection); the matching `reported_*` hot columns on Device hold the
    # *current* truth for fast filtering. See P0.2 for state rendering.
    recovery_mode: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    auto_recovery_triggered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_known_good_restored: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    consecutive_unhealthy_boots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    in_captive_portal: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    holdoff_remaining_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cooldown_remaining_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    central_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    central_registered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    central_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    central_device_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    central_heartbeat_age_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    power_analytics_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    power_chip_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    power_sample_rate_hz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    power_batch_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)


Index("ix_device_heartbeats_device_received", DeviceHeartbeat.device_id, DeviceHeartbeat.received_at.desc())
