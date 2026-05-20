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
from app.services.tenant_scope import TenantScoped

# Allowed `kind` values. Each one has a `_poll_<kind>` branch in
# `services/external_sensors.py::_poll_kind`, optional kind-specific
# config in `external_sensor_sources.config` (JSONB), and an optional
# matching watchdog probe kind in `services/watchdog_runtime/_probes.py`.
EXTERNAL_SOURCE_KINDS = (
    "roku",          # v0.5.17 (B17 L1) — Roku ECP active-app
    "home_assistant", # v0.5.23 — HA REST API /api/states
    "weather",        # v0.5.23 — NWS alerts/active
    "ical",           # v0.5.23 — iCal/WebCal .ics feed
    "solaredge",     # v0.5.56 (P2.1) — SolarEdge cloud monitoring API
    "enphase_envoy", # v0.5.56 (P2.1) — Enphase Envoy local /production.json
    "snmp",          # v0.5.58 (P2.2/P2.3) — router/switch SNMP IF-MIB poll
    "plex",          # v0.5.61 (B17 Ship 2) — Plex Media Server webhook
    "jellyfin",      # v0.5.61 (B17 Ship 2) — Jellyfin webhook
    "ios_shortcut",  # v0.5.61 (B17 Ship 2) — generic iOS Shortcuts webhook
    "mqtt",          # v0.5.63 (B17 Ship 3) — MQTT broker subscriber
    "google_calendar",  # v0.5.94 — Google Calendar API (OAuth poll)
)

# v0.5.61 (B17 Ship 2): inbound-webhook kinds. These are NOT polled —
# the external service POSTs to /api/v1/integrations/webhook/<id>. The
# poller (`poll_all_due`) skips them; there is no `_poll_<kind>` branch.
WEBHOOK_KINDS = ("plex", "jellyfin", "ios_shortcut")

# v0.5.63 (B17 Ship 3): long-lived-subscriber kinds. Also NOT polled —
# a background thread holds an open connection (MQTT broker) and writes
# samples as messages arrive. `poll_all_due` skips these too.
SUBSCRIBER_KINDS = ("mqtt",)

# v0.5.60 (P3a / RFC-006): the authoritative source-kind → modality map.
# RFC-006 Decision 1 makes `modality` the cross-modal query key; §4 picks
# this code-side map over a denormalized DB column (zero migration). Power
# samples (a separate table, not an external sensor) are modality "power".
KIND_TO_MODALITY = {
    "roku": "media",
    "home_assistant": "appliance_state",
    "weather": "weather",
    "ical": "calendar",
    "solaredge": "solar",
    "enphase_envoy": "solar",
    "snmp": "network",
    "plex": "media",
    "jellyfin": "media",
    "ios_shortcut": "automation",
    "mqtt": "appliance_state",
    "google_calendar": "calendar",
}


class ExternalSensorSource(TenantScoped, Base):
    # TODO(org-phase2): flip `organization_id` to NOT NULL (RESTRICT FK).
    # See design §2. `ExternalSensorSample` stays Tier-B (org derived via
    # the source) — no column.
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

    # v0.5.23: per-kind extras. Generic JSONB so each new integration
    # can carry the bag-of-fields it needs without a schema change.
    # Examples:
    #   home_assistant → {"token": "...", "verify_ssl": true}
    #   weather        → {"lat": 38.9, "lng": -77.0}
    #   ical           → {"url": "https://..."}
    #   solaredge      → {"site_id": "12345", "api_key": "..."}
    #   enphase_envoy  → {"jwt": "...optional, 7.0+ Envoys only..."}
    #   snmp           → {"version": "2c", "community": "...",
    #                     "v3": {...}, "interface_filter": [...]}
    # `host` + `port` keep their meaning for kinds that have an HTTP
    # base (roku, home_assistant, enphase_envoy) or an SNMP agent
    # (snmp → port 161). Kinds that just need a URL (ical), coordinates
    # (weather), or a cloud account (solaredge) can leave host empty.
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

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
