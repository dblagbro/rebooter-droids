"""Unit tests — `watchdog_runtime.tick()` orchestration (v0.5.99).

The tick is the APScheduler entry point — it loads enabled rules,
filters by due-ness + maintenance windows, dispatches `run_probe`,
records events, and updates per-rule state. Drove deterministically
via the injectable `now` argument (v0.5.82); `run_probe` and
`runtime_flags.is_maintenance_mode_active` are monkeypatched so no
real network / config is touched.

DB-backed — `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import WatchdogProbeEvent, WatchdogRule
from app.models.watchdog import RULE_STATUS_FIRING
from app.services import watchdog_runtime
from app.services.watchdog import create_rule, set_enabled

_T0 = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


def _rule(**overrides) -> str:
    base = dict(
        name=f"qa-tick-{uuid.uuid4().hex[:8]}",
        probe={"kind": "internet"},
        target={"kind": "tag", "tag": "qa-tick"},
        action={"kind": "notify_only"},
        failure_threshold=2,
        recovery_threshold=2,
        window_seconds=60,
        cooldown_seconds=300,
    )
    base.update(overrides)
    return create_rule(**base)["id"]


def _events(rule_id: str) -> list[str]:
    with session_scope() as s:
        return [
            r.outcome for r in s.scalars(
                select(WatchdogProbeEvent)
                .where(WatchdogProbeEvent.rule_id == rule_id)
                .order_by(WatchdogProbeEvent.at.asc())
            )
        ]


def _patch_probe(monkeypatch, fn):
    """Replace `run_probe` for the duration of the test. `tick()`
    looks up `run_probe` in the package namespace at call time, so
    patching the attribute is enough."""
    monkeypatch.setattr(watchdog_runtime, "run_probe", fn)


def _patch_maintenance(monkeypatch, *, active: bool):
    from app.services import runtime_flags
    monkeypatch.setattr(
        runtime_flags, "is_maintenance_mode_active", lambda: active
    )


# ── short-circuit paths ─────────────────────────────────────────────────

def test_tick_disabled_by_env_var_returns_disabled_shape(monkeypatch, hub_db):
    monkeypatch.setenv("REBOOTER_WATCHDOG_DISABLED", "1")
    assert watchdog_runtime.tick(now=_T0) == {"disabled": True}


def test_tick_short_circuits_in_portal_maintenance_mode(monkeypatch, hub_db):
    _rule()  # at least one rule exists — must still short-circuit
    _patch_maintenance(monkeypatch, active=True)

    def _exploder(rule):
        raise AssertionError("run_probe must not be called in maintenance mode")

    _patch_probe(monkeypatch, _exploder)
    out = watchdog_runtime.tick(now=_T0)
    assert out == {"disabled": False, "maintenance_mode": True, "considered": 0}


# ── filtering: disabled rules / not-due rules ───────────────────────────

def test_tick_does_not_consider_a_disabled_rule(monkeypatch, hub_db):
    rid = _rule()
    set_enabled(rid, False)
    _patch_maintenance(monkeypatch, active=False)

    def _exploder(rule):
        raise AssertionError("disabled rules must not be probed")

    _patch_probe(monkeypatch, _exploder)
    stats = watchdog_runtime.tick(now=_T0)
    assert stats["considered"] == 0
    assert stats["probed"] == 0


def test_tick_skips_rule_inside_its_window_without_probing(monkeypatch, hub_db):
    rid = _rule(window_seconds=60)
    # Mark it as just-probed 10 s ago — well inside the 60 s window.
    with session_scope() as s:
        s.get(WatchdogRule, rid).last_probed_at = _T0 - timedelta(seconds=10)
    _patch_maintenance(monkeypatch, active=False)

    calls = []
    _patch_probe(monkeypatch, lambda rule: (calls.append(rule.id), ("success", {}))[1])
    stats = watchdog_runtime.tick(now=_T0)
    assert stats["considered"] == 1
    assert stats["probed"] == 0
    assert calls == []


# ── maintenance windows ─────────────────────────────────────────────────

def test_tick_records_maintenance_skip_for_a_windowed_rule(monkeypatch, hub_db):
    rid = _rule(window_seconds=60)
    # Window covers _T0.
    with session_scope() as s:
        s.get(WatchdogRule, rid).maintenance_windows = [{
            "start": "2026-05-19T11:00:00+00:00",
            "end": "2026-05-19T13:00:00+00:00",
        }]
    _patch_maintenance(monkeypatch, active=False)
    _patch_probe(monkeypatch, lambda rule: ("success", {}))

    stats = watchdog_runtime.tick(now=_T0)
    assert stats["in_window"] == 1
    assert stats["probed"] == 0
    assert _events(rid) == ["maintenance_skip"]
    # last_probed_at advances even when the probe is skipped — so the
    # window-skip itself respects the rule's cadence.
    with session_scope() as s:
        assert s.get(WatchdogRule, rid).last_outcome == "maintenance_skip"


# ── probe error recovery + stats wiring ─────────────────────────────────

def test_tick_records_a_raising_probe_as_probe_error(monkeypatch, hub_db):
    rid = _rule()
    _patch_maintenance(monkeypatch, active=False)

    def _boom(rule):
        raise RuntimeError("simulated probe outage")

    _patch_probe(monkeypatch, _boom)
    stats = watchdog_runtime.tick(now=_T0)
    assert stats["errors"] == 1
    assert stats["probed"] == 0
    assert _events(rid) == ["probe_error"]


def test_tick_dispatches_success_outcome_to_the_state_machine(monkeypatch, hub_db):
    rid = _rule(failure_threshold=2, window_seconds=60)
    _patch_maintenance(monkeypatch, active=False)
    _patch_probe(monkeypatch, lambda rule: ("success", {"latency_ms": 12}))

    stats = watchdog_runtime.tick(now=_T0)
    assert stats == {
        "considered": 1, "probed": 1, "fired": 0,
        "errors": 0, "in_window": 0,
    }
    assert _events(rid) == ["success"]


def test_tick_fires_when_failure_threshold_crosses(monkeypatch, hub_db):
    rid = _rule(failure_threshold=2, window_seconds=60)
    _patch_maintenance(monkeypatch, active=False)
    _patch_probe(monkeypatch, lambda rule: ("failure", {}))

    s1 = watchdog_runtime.tick(now=_T0)
    s2 = watchdog_runtime.tick(now=_T0 + timedelta(seconds=70))
    assert s1["fired"] == 0
    assert s2["fired"] == 1
    # Two failures + one action_fired across the two ticks (the action
    # is recorded at the same `at` as the failure that triggered it,
    # so don't pin the relative order of the second-tick events).
    events = _events(rid)
    assert events.count("failure") == 2
    assert events.count("action_fired") == 1
    with session_scope() as s:
        assert s.get(WatchdogRule, rid).status == RULE_STATUS_FIRING
