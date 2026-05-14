"""Scheduling + event-log + state-machine helpers for the watchdog.

`record_event` and `_rule_is_due` are imported elsewhere
(`services/watchdog.py` uses both for the probe-now diagnostic) — they
remain accessible at the package root via `__init__.py` re-export.

`_update_state_and_maybe_fire` defers the import of `_fire_action`
inside the function body to avoid an `_state` ↔ `_actions` import
cycle.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import WatchdogProbeEvent, WatchdogRule
from app.models.watchdog import RULE_STATUS_ARMED, RULE_STATUS_FIRING


def _rule_is_due(rule: WatchdogRule, now: datetime) -> bool:
    if rule.last_probed_at is None:
        return True
    return (now - rule.last_probed_at) >= timedelta(seconds=rule.window_seconds)


def _in_maintenance_window(rule: WatchdogRule, now: datetime) -> bool:
    """v0.4.7: each window is `{"start": ISO8601, "end": ISO8601}`.
    Returns True if `now` is between any window's start and end.

    Errors in window-shape (bad ISO, missing keys) are treated as
    "no window" — never block a probe due to malformed config."""
    windows = rule.maintenance_windows or []
    if not windows:
        return False
    for w in windows:
        try:
            start = datetime.fromisoformat(w["start"])
            end = datetime.fromisoformat(w["end"])
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            if start <= now <= end:
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


def record_event(
    session, rule: WatchdogRule, outcome: str, details: dict, at: datetime
) -> None:
    session.add(
        WatchdogProbeEvent(
            rule_id=rule.id,
            at=at,
            outcome=outcome,
            details=details or {},
        )
    )


def _update_state_and_maybe_fire(
    session, rule: WatchdogRule, outcome: str, details: dict, now: datetime
) -> bool:
    """Streak-tracking + cooldown + fire decision.

    Returns True if the rule's action fired this tick.
    """
    rule.last_probed_at = now
    rule.last_outcome = outcome

    if outcome == "success":
        if rule.failure_streak > 0 or rule.status == RULE_STATUS_FIRING:
            rule.recovery_streak += 1
            if rule.recovery_streak >= rule.recovery_threshold:
                rule.failure_streak = 0
                rule.recovery_streak = 0
                rule.status = RULE_STATUS_ARMED
                record_event(
                    session, rule, "recovery",
                    {"reason": "recovery_threshold reached"}, now,
                )
        else:
            rule.recovery_streak = 0
        return False

    if outcome != "failure":
        return False

    rule.recovery_streak = 0
    rule.failure_streak += 1

    if rule.failure_streak < rule.failure_threshold:
        return False

    # Cooldown gate.
    if rule.last_action_at is not None:
        if (now - rule.last_action_at) < timedelta(seconds=rule.cooldown_seconds):
            record_event(
                session, rule, "cooldown_skip",
                {"failure_streak": rule.failure_streak}, now,
            )
            return False

    # Threshold crossed and not in cooldown — fire.
    # Deferred import: `_actions` imports back from `_state` would
    # circulate at module-load time; lazy import keeps both modules
    # importable independently.
    from app.services.watchdog_runtime._actions import _fire_action

    fired_details = _fire_action(rule)
    rule.status = RULE_STATUS_FIRING
    rule.last_action_at = now
    record_event(session, rule, "action_fired", fired_details, now)
    return True


# v0.5.18 (#3 naming cleanup): the public name is `record_event`. The
# underscore alias is kept for one release for back-compat with
# `from app.services.watchdog_runtime import _record_event` callers.
# Remove after v0.6.x.
_record_event = record_event
