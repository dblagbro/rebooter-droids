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
from app.models._helpers import as_aware
from app.models.watchdog import RULE_STATUS_ARMED, RULE_STATUS_FIRING


def _rule_is_due(rule: WatchdogRule, now: datetime) -> bool:
    if rule.last_probed_at is None:
        return True
    return (now - as_aware(rule.last_probed_at)) >= timedelta(
        seconds=rule.window_seconds
    )


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
    # v0.5.90 (Stage A): a binding rule is level-triggered — its target
    # device-state follows the probe both ways. Wholly separate state
    # machine; the failure-streak / cooldown logic below does not apply.
    if (rule.action or {}).get("kind") == "binding":
        return _binding_tick(session, rule, outcome, details, now)

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
        if (now - as_aware(rule.last_action_at)) < timedelta(
            seconds=rule.cooldown_seconds
        ):
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

    # v6 (notifications/webhooks): emit the watchdog event onto the
    # outbound-notifications queue, and run the rule's `escalation`
    # webhook — both via the SSRF-guarded paths. Best-effort; a notify
    # failure must never abort the tick.
    _run_escalation_and_emit(rule, fired_details)
    return True


def _run_escalation_and_emit(rule: WatchdogRule, fired_details: dict) -> None:
    """Fire the outbound-notifications hook + the rule's `escalation`
    webhook after an action fires.

    Per `docs/notes/2026-05-20-hub-tier2-design.md` Feature 6:

      * `notifications.emit("watchdog.rule_fired", ...)` queues a
        delivery for every subscribed channel.
      * a rule `escalation` of `{kind:"webhook", url:...}` is sent
        through the SSRF guard — the folded-in security fix that
        re-points the previously-raw escalation URL field.

    Both are best-effort and fully isolated from the tick: any failure
    is logged, never raised.
    """
    try:
        from app.services import notifications

        notifications.emit(
            "watchdog.rule_fired",
            {
                "rule_id": rule.id,
                "rule_name": rule.name,
                "outcome": "action_fired",
                "details": fired_details,
            },
            site_id=getattr(rule, "site_id", None),
        )
    except Exception:  # pragma: no cover - belt and suspenders
        import logging

        logging.getLogger(__name__).exception(
            "notifications.emit failed for rule %s", rule.id
        )

    escalation = rule.escalation or {}
    if escalation.get("kind") == "webhook" and escalation.get("url"):
        try:
            from app.services import notifications
            from app.services.webhook_delivery import send_escalation_webhook

            send_escalation_webhook(
                rule.id,
                escalation["url"],
                {"rule_name": rule.name, "details": fired_details},
            )
            # An escalation is itself a notifiable event.
            notifications.emit(
                "watchdog.rule_escalated",
                {"rule_id": rule.id, "rule_name": rule.name},
                site_id=getattr(rule, "site_id", None),
            )
        except Exception:  # pragma: no cover
            import logging

            logging.getLogger(__name__).exception(
                "escalation webhook failed for rule %s", rule.id
            )


def _binding_tick(
    session, rule: WatchdogRule, outcome: str, details: dict, now: datetime
) -> bool:
    """v0.5.90 (Stage A): level-triggered binding runtime.

    A binding rule's `action` is `{kind:'binding', on_active, on_clear}`.
    The target device-state follows the probe: `on_active` is applied
    once the probe has reported `success` for `recovery_threshold`
    consecutive evaluations, `on_clear` once it has reported `failure`
    for `failure_threshold`. Fires only on a genuine edge — across the
    steady state every tick is an idempotent no-op. A transient
    `probe_error` holds the current edge rather than flipping it.

    `last_outcome` doubles as the binding's applied-state marker:
    `binding:active` / `binding:cleared` once an edge has applied,
    `binding_settling:<outcome>` while the probe is not yet stable.

    Returns True if a binding edge applied this tick.
    """
    rule.last_probed_at = now

    if outcome == "success":
        rule.recovery_streak += 1
        rule.failure_streak = 0
    elif outcome == "failure":
        rule.failure_streak += 1
        rule.recovery_streak = 0
    else:
        # probe_error / probe_exception — never flip on a transient
        # error; keep whatever edge is currently applied.
        rule.last_outcome = outcome
        return False

    if rule.recovery_streak >= max(1, rule.recovery_threshold):
        edge = "active"
    elif rule.failure_streak >= max(1, rule.failure_threshold):
        edge = "cleared"
    else:
        # Probe state not yet stable enough to assert an edge.
        rule.last_outcome = f"binding_settling:{outcome}"
        return False

    marker = f"binding:{edge}"
    if rule.last_outcome == marker:
        # Already in the desired state — idempotent no-op.
        return False

    binding = rule.action or {}
    leaf = binding.get("on_active" if edge == "active" else "on_clear") or {}
    # Deferred import — `_actions` imports back from `_state`.
    from app.services.watchdog_runtime._actions import apply_binding_action

    result = apply_binding_action(rule, leaf)
    rule.last_outcome = marker
    rule.last_action_at = now
    rule.status = RULE_STATUS_FIRING if edge == "active" else RULE_STATUS_ARMED
    record_event(
        session, rule, "binding_applied",
        {"edge": edge, "outcome": outcome,
         "leaf_action": leaf.get("kind"), "result": result},
        now,
    )
    return True
