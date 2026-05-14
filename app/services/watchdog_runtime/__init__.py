"""Watchdog probe runtime — v0.4.2 (B6).

A single APScheduler job (`watchdog_probe_tick`) runs every 10 s:

1. Loads enabled rules whose `last_probed_at + window_seconds` has
   elapsed (or `last_probed_at IS NULL`).
2. For each due rule, dispatches the probe (one function per
   probe-kind), records a `WatchdogProbeEvent`, updates the rule's
   runtime counters.
3. On `failure_streak >= failure_threshold` (and outside cooldown),
   fires the rule's action (cycle / hold_off / notify_only) and
   records an `action_fired` event.
4. On `recovery_streak >= recovery_threshold`, resets the counters
   and records a `recovery` event.

The runtime never raises out of the tick — any unhandled per-rule
exception is recorded as `outcome = 'probe_error'` and the rule keeps
marching. Operator-stop via `REBOOTER_WATCHDOG_DISABLED=1`.

This package was split out of the original `watchdog_runtime.py`
single file in v0.5.15. Internal layout:

- `_probes.py`  — probe implementations (`run_probe` dispatcher +
  `_probe_internet`, `_probe_ping`, `_probe_tcp`, `_probe_http`,
  `_probe_dns`, `_probe_roku_app_active`)
- `_state.py`   — scheduling, event log, state machine (`_rule_is_due`,
  `_in_maintenance_window`, `record_event`,
  `_update_state_and_maybe_fire`)
- `_actions.py` — action dispatch + target resolution (`_fire_action`,
  `_fire_cycle`, `_fire_hold_off`, `resolve_target_devices`)

v0.5.18 (#3 naming cleanup): the cross-module helpers shed their
underscore prefix. The new public names are `run_probe`,
`record_event`, `resolve_target_devices`. Old `_underscore` names are
kept as aliases for one release for callers mid-rollout.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import WatchdogRule
from app.services.watchdog_runtime._actions import (
    _fire_action,
    _fire_cycle,
    _fire_hold_off,
    resolve_target_devices,
)
from app.services.watchdog_runtime._probes import (
    DEFAULT_INTERNET_TARGETS,
    MAX_INTERNET_TARGETS,
    PROBE_TIMEOUT_SECONDS,
    _probe_dns,
    _probe_http,
    _probe_internet,
    _probe_ping,
    _probe_tcp,
    run_probe,
)
from app.services.watchdog_runtime._state import (
    _in_maintenance_window,
    _rule_is_due,
    _update_state_and_maybe_fire,
    record_event,
)

log = logging.getLogger(__name__)


def tick() -> dict:
    """Top-of-tick dispatcher. Returns a tiny stats dict for the
    APScheduler job log line + tests."""
    if os.environ.get("REBOOTER_WATCHDOG_DISABLED") == "1":
        return {"disabled": True}

    # v0.4.7 (B7): portal-wide maintenance mode short-circuits all
    # probes. Operator toggles via `/app/maintenance` (super-admin only).
    from app.services import runtime_flags

    if runtime_flags.is_maintenance_mode_active():
        return {"disabled": False, "maintenance_mode": True, "considered": 0}

    now = datetime.now(timezone.utc)
    stats = {"considered": 0, "probed": 0, "fired": 0, "errors": 0, "in_window": 0}

    with session_scope() as session:
        rules = list(
            session.scalars(
                select(WatchdogRule).where(
                    WatchdogRule.enabled.is_(True),
                    WatchdogRule.status != "disabled",
                )
            )
        )
        for rule in rules:
            stats["considered"] += 1
            if not _rule_is_due(rule, now):
                continue

            # v0.4.7 (B7): per-rule maintenance windows. Skip the probe
            # entirely if `now` falls inside any window — log the skip
            # as a `maintenance_skip` event so the operator sees the
            # rule was suppressed (not silently dropped).
            if _in_maintenance_window(rule, now):
                record_event(
                    session, rule, "maintenance_skip",
                    {"reason": "rule maintenance window"},
                    now,
                )
                rule.last_probed_at = now
                rule.last_outcome = "maintenance_skip"
                stats["in_window"] += 1
                continue

            try:
                outcome, details = run_probe(rule)
                stats["probed"] += 1
            except Exception as e:
                outcome, details = "probe_error", {"error": str(e)}
                stats["errors"] += 1

            record_event(session, rule, outcome, details, now)
            fired = _update_state_and_maybe_fire(session, rule, outcome, details, now)
            if fired:
                stats["fired"] += 1

        session.flush()

    return stats


__all__ = [
    # Public entrypoint
    "tick",
    # Constants
    "PROBE_TIMEOUT_SECONDS",
    "DEFAULT_INTERNET_TARGETS",
    "MAX_INTERNET_TARGETS",
    # Probes
    "run_probe",
    "_probe_internet",
    "_probe_ping",
    "_probe_tcp",
    "_probe_http",
    "_probe_dns",
    # State machine
    "record_event",
    "_rule_is_due",
    "_in_maintenance_window",
    "_update_state_and_maybe_fire",
    # Actions
    "resolve_target_devices",
    "_fire_action",
    "_fire_cycle",
    "_fire_hold_off",
]
