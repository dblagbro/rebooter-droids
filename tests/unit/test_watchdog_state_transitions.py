"""Unit tests — watchdog state machine + scheduling helpers (v0.5.99).

`_update_state_and_maybe_fire` is the heart of the non-binding rule
runtime: streak tracking, threshold-cross fire, cooldown gate,
recovery-streak rearm. Drove with a `notify_only` action so no command
enqueuing / device fixtures are needed; the state transitions are the
same shape for cycle / hold_off (which only differ in `_fire_action`'s
return-payload, not the streak logic).

`_rule_is_due` and `_in_maintenance_window` are pure helpers — tested
in-memory against a constructed `WatchdogRule` without the DB.

DB-backed cases use the `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import WatchdogProbeEvent, WatchdogRule
from app.models.watchdog import RULE_STATUS_ARMED, RULE_STATUS_FIRING
from app.services.watchdog import create_rule
from app.services.watchdog_runtime._state import (
    _in_maintenance_window,
    _rule_is_due,
    _update_state_and_maybe_fire,
)

_T0 = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


# ── helpers ────────────────────────────────────────────────────────────

def _make_rule(*, failure_threshold=3, recovery_threshold=2,
               window_seconds=60, cooldown_seconds=300):
    """A `notify_only` rule — exercises the state machine without
    enqueuing any commands."""
    return create_rule(
        name=f"qa-state-{uuid.uuid4().hex[:8]}",
        probe={"kind": "internet"},
        target={"kind": "tag", "tag": "qa-state"},
        action={"kind": "notify_only"},
        failure_threshold=failure_threshold,
        recovery_threshold=recovery_threshold,
        window_seconds=window_seconds,
        cooldown_seconds=cooldown_seconds,
    )["id"]


def _step(rule_id: str, outcome: str, now: datetime) -> bool:
    """One state-machine step — fresh session per tick (the real
    scheduler pattern). Returns True if the rule's action fired."""
    with session_scope() as s:
        rule = s.get(WatchdogRule, rule_id)
        return _update_state_and_maybe_fire(s, rule, outcome, {}, now)


def _snapshot(rule_id: str) -> dict:
    """Read-only snapshot of the rule's runtime fields."""
    with session_scope() as s:
        r = s.get(WatchdogRule, rule_id)
        return {
            "failure_streak": r.failure_streak,
            "recovery_streak": r.recovery_streak,
            "status": r.status,
            "last_outcome": r.last_outcome,
            "last_action_at": r.last_action_at,
            "last_probed_at": r.last_probed_at,
        }


def _event_kinds(rule_id: str) -> list[str]:
    with session_scope() as s:
        return [
            row.outcome for row in s.scalars(
                select(WatchdogProbeEvent)
                .where(WatchdogProbeEvent.rule_id == rule_id)
                .order_by(WatchdogProbeEvent.at.asc())
            )
        ]


# ── _update_state_and_maybe_fire — non-binding path ────────────────────

def test_success_when_armed_is_a_noop(hub_db):
    rid = _make_rule(failure_threshold=3, recovery_threshold=2)
    assert _step(rid, "success", _T0) is False
    s = _snapshot(rid)
    assert s["failure_streak"] == 0
    assert s["recovery_streak"] == 0
    assert s["status"] == RULE_STATUS_ARMED
    assert s["last_outcome"] == "success"


def test_failure_below_threshold_increments_streak_no_fire(hub_db):
    rid = _make_rule(failure_threshold=3)
    assert _step(rid, "failure", _T0) is False
    assert _step(rid, "failure", _T0 + timedelta(seconds=10)) is False
    s = _snapshot(rid)
    assert s["failure_streak"] == 2
    assert s["status"] == RULE_STATUS_ARMED  # not yet firing
    assert s["last_action_at"] is None


def test_failure_at_threshold_fires_and_records_action_event(hub_db):
    rid = _make_rule(failure_threshold=3)
    _step(rid, "failure", _T0)
    _step(rid, "failure", _T0 + timedelta(seconds=10))
    assert _step(rid, "failure", _T0 + timedelta(seconds=20)) is True
    s = _snapshot(rid)
    assert s["failure_streak"] == 3
    assert s["status"] == RULE_STATUS_FIRING
    assert s["last_action_at"] is not None
    # one action_fired event was recorded.
    assert "action_fired" in _event_kinds(rid)


def test_failure_during_cooldown_records_cooldown_skip_no_action(hub_db):
    """Once an action fires, further failures inside `cooldown_seconds`
    log a `cooldown_skip` event and DO NOT re-fire."""
    rid = _make_rule(failure_threshold=2, cooldown_seconds=300)
    _step(rid, "failure", _T0)
    assert _step(rid, "failure", _T0 + timedelta(seconds=10)) is True
    # Another failure 30 s later — well inside the 300 s cooldown.
    assert _step(rid, "failure", _T0 + timedelta(seconds=40)) is False
    assert _step(rid, "failure", _T0 + timedelta(seconds=70)) is False
    kinds = _event_kinds(rid)
    assert kinds.count("action_fired") == 1
    assert kinds.count("cooldown_skip") == 2


def test_failure_after_cooldown_elapses_fires_again(hub_db):
    rid = _make_rule(failure_threshold=2, cooldown_seconds=120)
    _step(rid, "failure", _T0)
    _step(rid, "failure", _T0 + timedelta(seconds=10))  # fires (action #1)
    # 130 s later — cooldown elapsed. Failure_streak is still above
    # the threshold, so the next failure refires.
    assert _step(rid, "failure", _T0 + timedelta(seconds=140)) is True
    assert _event_kinds(rid).count("action_fired") == 2


def test_success_after_firing_increments_recovery_streak(hub_db):
    rid = _make_rule(failure_threshold=2, recovery_threshold=2)
    _step(rid, "failure", _T0)
    _step(rid, "failure", _T0 + timedelta(seconds=10))  # fires
    _step(rid, "success", _T0 + timedelta(seconds=20))
    s = _snapshot(rid)
    assert s["recovery_streak"] == 1
    assert s["status"] == RULE_STATUS_FIRING  # not yet recovered
    assert "recovery" not in _event_kinds(rid)


def test_recovery_threshold_returns_to_armed_resets_streaks(hub_db):
    rid = _make_rule(failure_threshold=2, recovery_threshold=2)
    _step(rid, "failure", _T0)
    _step(rid, "failure", _T0 + timedelta(seconds=10))  # fires
    _step(rid, "success", _T0 + timedelta(seconds=20))
    _step(rid, "success", _T0 + timedelta(seconds=30))  # recovery_threshold met
    s = _snapshot(rid)
    assert s["failure_streak"] == 0
    assert s["recovery_streak"] == 0
    assert s["status"] == RULE_STATUS_ARMED
    assert "recovery" in _event_kinds(rid)


def test_probe_error_outcome_does_not_change_streaks(hub_db):
    """`probe_error` / any non-success/non-failure outcome holds the
    state machine — neither streak moves."""
    rid = _make_rule(failure_threshold=3)
    _step(rid, "failure", _T0)                     # streak 1
    _step(rid, "probe_error", _T0 + timedelta(seconds=10))
    _step(rid, "probe_error", _T0 + timedelta(seconds=20))
    s = _snapshot(rid)
    assert s["failure_streak"] == 1
    assert s["recovery_streak"] == 0
    assert s["status"] == RULE_STATUS_ARMED


def test_last_probed_at_and_last_outcome_update_each_call(hub_db):
    rid = _make_rule()
    _step(rid, "success", _T0)
    s1 = _snapshot(rid)
    assert s1["last_outcome"] == "success"
    assert s1["last_probed_at"] is not None

    later = _T0 + timedelta(seconds=120)
    _step(rid, "failure", later)
    s2 = _snapshot(rid)
    assert s2["last_outcome"] == "failure"
    assert s2["last_probed_at"] > s1["last_probed_at"]


# ── _rule_is_due — pure, no DB needed ──────────────────────────────────

def test_rule_with_no_last_probed_at_is_due():
    rule = WatchdogRule(last_probed_at=None, window_seconds=60)
    assert _rule_is_due(rule, _T0) is True


def test_rule_within_window_is_not_due():
    rule = WatchdogRule(
        last_probed_at=_T0,
        window_seconds=60,
    )
    assert _rule_is_due(rule, _T0 + timedelta(seconds=30)) is False


def test_rule_past_window_is_due():
    rule = WatchdogRule(
        last_probed_at=_T0,
        window_seconds=60,
    )
    assert _rule_is_due(rule, _T0 + timedelta(seconds=70)) is True


# ── _in_maintenance_window — pure ──────────────────────────────────────

def test_no_windows_returns_false():
    rule = WatchdogRule(maintenance_windows=None)
    assert _in_maintenance_window(rule, _T0) is False
    rule.maintenance_windows = []
    assert _in_maintenance_window(rule, _T0) is False


def test_now_inside_window_returns_true():
    rule = WatchdogRule(maintenance_windows=[
        {"start": "2026-05-19T11:00:00+00:00",
         "end": "2026-05-19T13:00:00+00:00"},
    ])
    assert _in_maintenance_window(rule, _T0) is True


def test_now_outside_all_windows_returns_false():
    rule = WatchdogRule(maintenance_windows=[
        {"start": "2026-05-19T08:00:00+00:00",
         "end": "2026-05-19T09:00:00+00:00"},
        {"start": "2026-05-19T14:00:00+00:00",
         "end": "2026-05-19T15:00:00+00:00"},
    ])
    assert _in_maintenance_window(rule, _T0) is False


def test_malformed_window_returns_false_does_not_raise():
    """Bad-shape windows are silently treated as 'no window' — never
    block a probe due to malformed config."""
    rule = WatchdogRule(maintenance_windows=[
        {"start": "not-an-iso", "end": "neither"},        # bad ISO
        {"begin": "...", "finish": "..."},                # wrong keys
        {"start": "2026-05-19T08:00:00+00:00"},           # missing end
    ])
    assert _in_maintenance_window(rule, _T0) is False


def test_naive_window_timestamps_are_treated_as_utc():
    """A window without timezone info is interpreted as UTC — the
    helper coerces it so naive Postgres-shaped strings still match."""
    rule = WatchdogRule(maintenance_windows=[
        {"start": "2026-05-19T11:00:00", "end": "2026-05-19T13:00:00"},
    ])
    assert _in_maintenance_window(rule, _T0) is True
