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
PROBE_KIND_CUSTOM = "custom"
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

KNOWN_PROBE_KINDS = (
    PROBE_KIND_INTERNET,
    PROBE_KIND_PING,
    PROBE_KIND_TCP,
    PROBE_KIND_HTTP,
    PROBE_KIND_DNS,
    PROBE_KIND_GATEWAY,
    PROBE_KIND_CUSTOM,
    PROBE_KIND_ROKU_APP_ACTIVE,
    PROBE_KIND_HA_STATE_IS,
    PROBE_KIND_WEATHER_ALERT_ACTIVE,
    PROBE_KIND_ICAL_EVENT_ACTIVE,
    PROBE_KIND_POWER_ABOVE,
    PROBE_KIND_POWER_BELOW,
    PROBE_KIND_POWER_ZERO_WHILE_ON,
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


class WatchdogRule(Base):
    __tablename__ = "watchdog_rules"

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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
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
