"""Watchdog-rule data model — v0.4.0 (P4 first slice).

Per `docs/webui-redesign-plan.md` §7.1. The schema is shaped so a
future probe runtime (v0.4.1+) can read off these rows directly
without further migrations.

v0.4.0 ships ONLY the data-model + CRUD + plain-English render.
The probe runtime that actually executes rules + writes events to
`watchdog_probe_events` lands in v0.4.1+.
"""

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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column
from app.services.tenant_scope import TenantScoped, tenant_scoped_org_column


# Documented probe kinds. The advanced editor (P4 iteration 2)
# accepts JSON with these `kind` values; the plain-English builder
# is shaped around them.
#
# v0.5.25 (Phase 2A): the four external-source probe kinds shipped
# with v0.5.17 (Roku) + v0.5.23 (HA / weather / iCal) are now
# canonical — they were already runtime-supported in
# `watchdog_runtime/_probes.py::run_probe` but the model validation
# gate rejected them, which made it impossible to create such a rule
# via the API or the JSON editor. Form-builder fields for these
# kinds are still Phase 2B (v0.5.28); the JSON editor at
# `/app/rules` is the escape hatch until then.
PROBE_KIND_INTERNET = "internet"
PROBE_KIND_PING = "ping"
PROBE_KIND_TCP = "tcp"
PROBE_KIND_HTTP = "http"
PROBE_KIND_DNS = "dns"
PROBE_KIND_GATEWAY = "gateway"
# v0.5.34 (BUG-054 fix): `PROBE_KIND_CUSTOM` was in KNOWN_PROBE_KINDS
# since v0.4.0 but the runtime _run_probe dispatcher never had a
# branch for it — operators could save a rule that failed-perpetually
# with `reason="unknown probe kind: custom"`. The integration probes
# (roku/ha/weather/ical) and power probes
# (power_above/_below/_zero_while_on) are the actual extensibility
# surface today; the placeholder was never implemented. Removed from
# canonical list so validation rejects cleanly. The constant itself
# stays defined as a string-literal alias for any third-party code
# that imported it, but it's no longer in KNOWN_PROBE_KINDS.
PROBE_KIND_CUSTOM = "custom"  # deprecated — never had a runtime branch
# External-source probe kinds (B17 — see `services/external_sensors.py`).
PROBE_KIND_ROKU_APP_ACTIVE = "roku_app_active"
PROBE_KIND_HA_STATE_IS = "ha_state_is"
PROBE_KIND_WEATHER_ALERT_ACTIVE = "weather_alert_active"
PROBE_KIND_ICAL_EVENT_ACTIVE = "ical_event_active"
# Power-targeted probe kinds (B16 Phase 1D — v0.5.32).
# Read recent device_power_samples for `probe.device_id` over
# `probe.window_seconds` and compare against `probe.threshold_w`.
PROBE_KIND_POWER_ABOVE = "power_above"
PROBE_KIND_POWER_BELOW = "power_below"
PROBE_KIND_POWER_ZERO_WHILE_ON = "power_zero_while_on"

# v0.5.89 (BUG-058): the remaining runtime-supported probe kinds. The
# watchdog runtime (`watchdog_runtime/_probes.py::run_probe`) has
# dispatched all of these for several releases, but they were never
# added to KNOWN_PROBE_KINDS — so `create_rule` rejected them and they
# could not be created via the API or the JSON editor despite working
# at runtime. `host_awake` is a TCP-connect alias (v0.5.62); the rest
# are external-source integration probes. KNOWN_PROBE_KINDS is now the
# canonical kind registry — `run_probe` is pinned to it by a contract
# test (`tests/unit/test_probe_kind_registry.py`).
PROBE_KIND_HA_NUMERIC_ABOVE = "ha_numeric_above"
PROBE_KIND_HA_NUMERIC_BELOW = "ha_numeric_below"
PROBE_KIND_SOLAR_PRODUCTION_ABOVE = "solar_production_above"
PROBE_KIND_SOLAR_PRODUCTION_BELOW = "solar_production_below"
PROBE_KIND_SNMP_INTERFACE_DOWN = "snmp_interface_down"
PROBE_KIND_SNMP_THROUGHPUT_ABOVE = "snmp_throughput_above"
PROBE_KIND_SNMP_THROUGHPUT_BELOW = "snmp_throughput_below"
PROBE_KIND_SNMP_ERROR_RATE_ABOVE = "snmp_error_rate_above"
PROBE_KIND_MEDIA_SESSION_ACTIVE = "media_session_active"
PROBE_KIND_WEBHOOK_FIELD_EQUALS = "webhook_field_equals"
PROBE_KIND_MQTT_TOPIC_EQUALS = "mqtt_topic_equals"
PROBE_KIND_EPG_SHOW_AIRING = "epg_show_airing"
PROBE_KIND_HOST_AWAKE = "host_awake"
# Fleet-presence probe — fails when a managed device's heartbeat goes
# stale (the first probe that watches the devices the hub manages,
# rather than an outbound target or a power sample).
PROBE_KIND_DEVICE_HEARTBEAT_STALE = "device_heartbeat_stale"

KNOWN_PROBE_KINDS = (
    PROBE_KIND_INTERNET,
    PROBE_KIND_PING,
    PROBE_KIND_TCP,
    PROBE_KIND_HTTP,
    PROBE_KIND_DNS,
    PROBE_KIND_GATEWAY,
    PROBE_KIND_HOST_AWAKE,
    PROBE_KIND_ROKU_APP_ACTIVE,
    PROBE_KIND_HA_STATE_IS,
    PROBE_KIND_HA_NUMERIC_ABOVE,
    PROBE_KIND_HA_NUMERIC_BELOW,
    PROBE_KIND_WEATHER_ALERT_ACTIVE,
    PROBE_KIND_ICAL_EVENT_ACTIVE,
    PROBE_KIND_POWER_ABOVE,
    PROBE_KIND_POWER_BELOW,
    PROBE_KIND_POWER_ZERO_WHILE_ON,
    PROBE_KIND_SOLAR_PRODUCTION_ABOVE,
    PROBE_KIND_SOLAR_PRODUCTION_BELOW,
    PROBE_KIND_SNMP_INTERFACE_DOWN,
    PROBE_KIND_SNMP_THROUGHPUT_ABOVE,
    PROBE_KIND_SNMP_THROUGHPUT_BELOW,
    PROBE_KIND_SNMP_ERROR_RATE_ABOVE,
    PROBE_KIND_MEDIA_SESSION_ACTIVE,
    PROBE_KIND_WEBHOOK_FIELD_EQUALS,
    PROBE_KIND_MQTT_TOPIC_EQUALS,
    PROBE_KIND_EPG_SHOW_AIRING,
    PROBE_KIND_DEVICE_HEARTBEAT_STALE,
)

# Rule status enum — mirrors webui-redesign-plan.md §7.1.
RULE_STATUS_ARMED = "armed"
RULE_STATUS_FIRING = "firing"
RULE_STATUS_COOLED_DOWN = "cooled-down"
RULE_STATUS_SUSPENDED = "suspended"
RULE_STATUS_DISABLED = "disabled"

KNOWN_RULE_STATUSES = (
    RULE_STATUS_ARMED,
    RULE_STATUS_FIRING,
    RULE_STATUS_COOLED_DOWN,
    RULE_STATUS_SUSPENDED,
    RULE_STATUS_DISABLED,
)

# v0.5.90 (Stage A — condition bindings): watchdog rule action kinds.
# `cycle` / `hold_off` / `notify_only` are the original edge-triggered
# remediation actions. `relay_on` / `relay_off` are idempotent
# set-state actions. `binding` is a *level-triggered* meta-action — its
# `on_active` / `on_clear` sub-actions (each a leaf kind) make the
# rule's target track the probe state both ways (see
# `services/watchdog.py::_validate_action` + the binding runtime in
# `watchdog_runtime/_state.py::_binding_tick`). A binding lives wholly
# inside the existing `action` JSON column — no schema change.
ACTION_KIND_CYCLE = "cycle"
ACTION_KIND_HOLD_OFF = "hold_off"
ACTION_KIND_NOTIFY_ONLY = "notify_only"
ACTION_KIND_RELAY_ON = "relay_on"
ACTION_KIND_RELAY_OFF = "relay_off"
# v0.5.91 (Stage B — scenes): a multi-device action. Its `items` list
# sets each named device to a relay state and/or pushes an
# `apply_config` payload — "turn the surround AND subwoofer off and
# apply Erica's audio config". One `apply_scene` reaches several
# devices with *different* states, unlike the single-target leaf
# actions. Lives inside the `action` JSON — no schema change.
ACTION_KIND_SCENE = "apply_scene"
ACTION_KIND_BINDING = "binding"

# Leaf actions — valid as a plain rule action AND as the `on_active` /
# `on_clear` sub-actions of a `binding` action.
LEAF_ACTION_KINDS = (
    ACTION_KIND_CYCLE,
    ACTION_KIND_HOLD_OFF,
    ACTION_KIND_NOTIFY_ONLY,
    ACTION_KIND_RELAY_ON,
    ACTION_KIND_RELAY_OFF,
    ACTION_KIND_SCENE,
)
KNOWN_ACTION_KINDS = LEAF_ACTION_KINDS + (ACTION_KIND_BINDING,)


class WatchdogRule(TenantScoped, Base):
    # org-boundary phase 3: `organization_id` is NOT NULL with an
    # on-delete RESTRICT FK (migration 0005). See design §2.
    # `WatchdogProbeEvent` stays Tier-B (org derived via the rule) —
    # no column.
    __tablename__ = "watchdog_rules"

    organization_id = tenant_scoped_org_column("RESTRICT")

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "wdr")
    )

    # Site scope — nullable until P5 site-as-scope migration lands.
    # The probe runtime + UI list will eventually filter by site.
    site_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("sites.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RULE_STATUS_ARMED
    )

    # Probe shape: {kind: 'ping', host: '192.168.1.1'}
    #              {kind: 'http', url: '...', expect_status: 200, timeout_seconds: 10}
    #              etc.
    probe: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    failure_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    recovery_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)

    # Target shape: {kind: 'device', id: '...'}
    #               {kind: 'group', id: '...'}
    #               {kind: 'tag', tag: '...'}
    target: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Action shape: {kind: 'cycle', power_off_seconds: 5,
    #                post_reboot_holdoff_seconds: 180}
    #               {kind: 'hold_off'}
    #               {kind: 'notify_only'}
    action: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    retry_delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)

    # Escalation shape: {kind: 'stop'} | {kind: 'notify', recipients: [...]}
    #                   | {kind: 'hold_off'} | {kind: 'webhook', url: '...'}
    escalation: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Maintenance windows: array of cron-shape blocks. v0.4.0 stores
    # the JSON; the probe runtime in v0.4.1+ enforces.
    maintenance_windows: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_by_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()

    # ── v0.4.2 runtime state (ADD COLUMN at startup; safe defaults) ──
    # Counters reset by recovery / cooldown logic in the probe runtime.
    failure_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recovery_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_probed_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)
    last_action_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)
    last_outcome: Mapped[str | None] = mapped_column(String(40), nullable=True)


Index("ix_watchdog_rules_site_enabled_status",
      WatchdogRule.site_id, WatchdogRule.enabled, WatchdogRule.status)


class WatchdogProbeEvent(Base):
    """Per-rule probe-event log. v0.4.0 ships the table shape;
    inserts come from the probe runtime in v0.4.1+."""
    __tablename__ = "watchdog_probe_events"

    # BigInteger on Postgres (BIGSERIAL); Integer on SQLite so the PK
    # autoincrements there too — SQLite only treats an INTEGER PK as the
    # auto-rowid alias. Lets the runtime insert events under in-process
    # SQLite tests. No effect on a Postgres deployment.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    rule_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("watchdog_rules.id", ondelete="CASCADE"),
        nullable=False,
    )
    at: Mapped[datetime] = ts_column()

    # Outcome enum: 'success', 'failure', 'threshold_crossed',
    # 'action_fired', 'recovery', 'cooldown_skip',
    # 'suspend_by_window', 'escalated'
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


Index("ix_watchdog_probe_events_rule_at",
      WatchdogProbeEvent.rule_id, WatchdogProbeEvent.at.desc())
