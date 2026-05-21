"""Unit tests — `app/services/conflict_detection.py::detect_conflicts`.

`detect_conflicts()` is the deterministic cross-rule / cross-schedule
semantic conflict engine pulled forward from the v2 AI-layer design
(§4). It extends `create_rule()`'s single-rule structural validation
with cross-rule awareness. DB-backed — `hub_db` fixture (it queries
existing rules / schedules / devices in the org).

One test per conflict kind (v2 design §4.2 check set):
  1. contradictory actions on one device      → block
  2. watchdog vs. schedule fighting           → block / warn
  3. overlapping schedules                    → warn
  4. duplicate / redundant rule               → info
  5. cooldown-vs-holdoff power-cycle loop      → warn
  6. mode / rule mismatch                     → warn
"""

from __future__ import annotations

from app.db import session_scope
from app.models import Device
from app.services.conflict_detection import (
    KIND_CONTRADICTORY_ACTIONS,
    KIND_DUPLICATE_RULE,
    KIND_MODE_RULE_MISMATCH,
    KIND_OVERLAPPING_SCHEDULES,
    KIND_POWER_CYCLE_LOOP,
    KIND_WATCHDOG_VS_SCHEDULE,
    SEVERITY_BLOCK,
    SEVERITY_INFO,
    SEVERITY_WARN,
    detect_conflicts,
    has_blocking,
)
from app.services.schedules import create as create_schedule
from app.services.watchdog import create_rule

_PROBE = {"kind": "internet"}


def _kinds(findings):
    return {f.kind for f in findings}


def _by_kind(findings, kind):
    return [f for f in findings if f.kind == kind]


# ── no-conflict baseline ────────────────────────────────────────────────

def test_no_conflict_on_a_clean_rule(hub_db):
    findings = detect_conflicts({
        "probe": _PROBE,
        "target": {"kind": "device", "id": "dev_clean"},
        "action": {"kind": "notify_only"},
        "failure_threshold": 3, "window_seconds": 60, "cooldown_seconds": 300,
    })
    assert findings == []
    assert has_blocking(findings) is False


# ── Check 1 — contradictory actions on the same device ──────────────────

def test_contradictory_actions_on_same_device_blocks(hub_db):
    # Existing rule keeps the device powered ON.
    create_rule(
        name="keep router on", probe=_PROBE,
        target={"kind": "device", "id": "dev_router"},
        action={"kind": "relay_on"},
    )
    # Proposed rule holds the same device OFF — they fight.
    findings = detect_conflicts({
        "probe": _PROBE,
        "target": {"kind": "device", "id": "dev_router"},
        "action": {"kind": "hold_off"},
        "failure_threshold": 3, "window_seconds": 60, "cooldown_seconds": 300,
    })
    blockers = _by_kind(findings, KIND_CONTRADICTORY_ACTIONS)
    assert blockers, "expected a contradictory-actions finding"
    assert blockers[0].severity == SEVERITY_BLOCK
    assert has_blocking(findings) is True
    assert "dev_router" in blockers[0].related_ids


def test_same_intent_rules_do_not_contradict(hub_db):
    # Two rules that both power the device ON are not contradictory.
    create_rule(
        name="keep on A", probe=_PROBE,
        target={"kind": "device", "id": "dev_x"},
        action={"kind": "relay_on"},
    )
    findings = detect_conflicts({
        "probe": _PROBE,
        "target": {"kind": "device", "id": "dev_x"},
        "action": {"kind": "relay_on"},
        "failure_threshold": 3, "window_seconds": 60, "cooldown_seconds": 300,
    })
    assert KIND_CONTRADICTORY_ACTIONS not in _kinds(findings)


# ── Check 2 — watchdog vs. schedule fighting ────────────────────────────

def test_hold_off_rule_vs_power_cycle_schedule_blocks(hub_db):
    create_schedule(
        name="nightly cycle", kind="power_cycle", recurrence="daily",
        at_time_utc="03:00", target={"kind": "device", "id": "dev_sched"},
    )
    findings = detect_conflicts({
        "probe": _PROBE,
        "target": {"kind": "device", "id": "dev_sched"},
        "action": {"kind": "hold_off"},
        "failure_threshold": 3, "window_seconds": 60, "cooldown_seconds": 300,
    })
    hits = _by_kind(findings, KIND_WATCHDOG_VS_SCHEDULE)
    assert hits, "expected a watchdog-vs-schedule finding"
    assert hits[0].severity == SEVERITY_BLOCK


def test_recovery_rule_vs_power_cycle_schedule_warns(hub_db):
    create_schedule(
        name="nightly cycle", kind="power_cycle", recurrence="daily",
        at_time_utc="03:00", target={"kind": "device", "id": "dev_warn"},
    )
    findings = detect_conflicts({
        "probe": _PROBE,
        "target": {"kind": "device", "id": "dev_warn"},
        "action": {"kind": "cycle", "power_off_seconds": 5},
        "failure_threshold": 3, "window_seconds": 60, "cooldown_seconds": 300,
    })
    hits = _by_kind(findings, KIND_WATCHDOG_VS_SCHEDULE)
    assert hits, "expected a watchdog-vs-schedule finding"
    assert hits[0].severity == SEVERITY_WARN


# ── Check 3 — overlapping schedules ─────────────────────────────────────

def test_overlapping_schedules_on_target_device_warns(hub_db):
    # Two power-cycle schedules at the SAME time on the same device.
    create_schedule(
        name="cycle A", kind="power_cycle", recurrence="daily",
        at_time_utc="03:00", target={"kind": "device", "id": "dev_ov"},
    )
    create_schedule(
        name="cycle B", kind="power_cycle", recurrence="daily",
        at_time_utc="03:00", target={"kind": "device", "id": "dev_ov"},
    )
    findings = detect_conflicts({
        "probe": _PROBE,
        "target": {"kind": "device", "id": "dev_ov"},
        "action": {"kind": "notify_only"},
        "failure_threshold": 3, "window_seconds": 60, "cooldown_seconds": 300,
    })
    hits = _by_kind(findings, KIND_OVERLAPPING_SCHEDULES)
    assert hits, "expected an overlapping-schedules finding"
    assert hits[0].severity == SEVERITY_WARN


def test_schedules_at_different_times_do_not_overlap(hub_db):
    create_schedule(
        name="cycle A", kind="power_cycle", recurrence="daily",
        at_time_utc="03:00", target={"kind": "device", "id": "dev_nov"},
    )
    create_schedule(
        name="cycle B", kind="power_cycle", recurrence="daily",
        at_time_utc="05:00", target={"kind": "device", "id": "dev_nov"},
    )
    findings = detect_conflicts({
        "probe": _PROBE,
        "target": {"kind": "device", "id": "dev_nov"},
        "action": {"kind": "notify_only"},
        "failure_threshold": 3, "window_seconds": 60, "cooldown_seconds": 300,
    })
    assert KIND_OVERLAPPING_SCHEDULES not in _kinds(findings)


# ── Check 4 — duplicate / redundant rule ────────────────────────────────

def test_duplicate_rule_is_flagged_info(hub_db):
    create_rule(
        name="existing dup", probe={"kind": "ping", "host": "10.0.0.1"},
        target={"kind": "device", "id": "dev_dup"},
        action={"kind": "cycle", "power_off_seconds": 5},
    )
    findings = detect_conflicts({
        "probe": {"kind": "ping", "host": "10.0.0.1"},
        "target": {"kind": "device", "id": "dev_dup"},
        "action": {"kind": "cycle", "power_off_seconds": 5},
        "failure_threshold": 3, "window_seconds": 60, "cooldown_seconds": 300,
    })
    hits = _by_kind(findings, KIND_DUPLICATE_RULE)
    assert hits, "expected a duplicate-rule finding"
    assert hits[0].severity == SEVERITY_INFO
    assert has_blocking(findings) is False


def test_different_probe_is_not_a_duplicate(hub_db):
    create_rule(
        name="ping rule", probe={"kind": "ping", "host": "10.0.0.1"},
        target={"kind": "device", "id": "dev_nd"},
        action={"kind": "cycle", "power_off_seconds": 5},
    )
    findings = detect_conflicts({
        "probe": {"kind": "ping", "host": "10.0.0.99"},
        "target": {"kind": "device", "id": "dev_nd"},
        "action": {"kind": "cycle", "power_off_seconds": 5},
        "failure_threshold": 3, "window_seconds": 60, "cooldown_seconds": 300,
    })
    assert KIND_DUPLICATE_RULE not in _kinds(findings)


# ── Check 5 — cooldown-vs-holdoff power-cycle loop ──────────────────────

def test_cooldown_shorter_than_holdoff_warns(hub_db):
    findings = detect_conflicts({
        "probe": _PROBE,
        "target": {"kind": "device", "id": "dev_loop"},
        "action": {"kind": "cycle", "power_off_seconds": 5,
                   "post_reboot_holdoff_seconds": 300},
        "failure_threshold": 3, "window_seconds": 60,
        "cooldown_seconds": 60,  # shorter than the 300s hold-off
    })
    hits = _by_kind(findings, KIND_POWER_CYCLE_LOOP)
    assert hits, "expected a power-cycle-loop finding"
    assert all(h.severity == SEVERITY_WARN for h in hits)


def test_reboot_window_shorter_than_holdoff_warns(hub_db):
    # failure_threshold x window_seconds (1 x 30 = 30s) < hold-off 240s.
    findings = detect_conflicts({
        "probe": _PROBE,
        "target": {"kind": "device", "id": "dev_loop2"},
        "action": {"kind": "cycle", "power_off_seconds": 5,
                   "post_reboot_holdoff_seconds": 240},
        "failure_threshold": 1, "window_seconds": 30,
        "cooldown_seconds": 600,  # cooldown itself is fine
    })
    hits = _by_kind(findings, KIND_POWER_CYCLE_LOOP)
    assert hits, "expected a reboot-window power-cycle-loop finding"
    assert hits[0].severity == SEVERITY_WARN


def test_healthy_cycle_timings_do_not_loop(hub_db):
    findings = detect_conflicts({
        "probe": _PROBE,
        "target": {"kind": "device", "id": "dev_ok"},
        "action": {"kind": "cycle", "power_off_seconds": 5,
                   "post_reboot_holdoff_seconds": 120},
        "failure_threshold": 3, "window_seconds": 60,  # 180s > 120s
        "cooldown_seconds": 600,  # > 120s
    })
    assert KIND_POWER_CYCLE_LOOP not in _kinds(findings)


# ── Check 6 — mode / rule mismatch ──────────────────────────────────────

def test_watchdog_rule_on_smart_plug_device_warns(hub_db):
    with session_scope() as s:
        s.add(Device(
            id="dev_plug", display_name="Lamp",
            registration_state="active", desired_mode="smart_plug",
        ))
    findings = detect_conflicts({
        "probe": _PROBE,
        "target": {"kind": "device", "id": "dev_plug"},
        "action": {"kind": "cycle", "power_off_seconds": 5},
        "failure_threshold": 3, "window_seconds": 60, "cooldown_seconds": 300,
    })
    hits = _by_kind(findings, KIND_MODE_RULE_MISMATCH)
    assert hits, "expected a mode-rule-mismatch finding"
    assert hits[0].severity == SEVERITY_WARN
    assert "dev_plug" in hits[0].related_ids


def test_watchdog_rule_on_watchdog_mode_device_is_fine(hub_db):
    with session_scope() as s:
        s.add(Device(
            id="dev_wd", display_name="Modem",
            registration_state="active", desired_mode="internet_watchdog",
        ))
    findings = detect_conflicts({
        "probe": _PROBE,
        "target": {"kind": "device", "id": "dev_wd"},
        "action": {"kind": "cycle", "power_off_seconds": 5},
        "failure_threshold": 3, "window_seconds": 60, "cooldown_seconds": 300,
    })
    assert KIND_MODE_RULE_MISMATCH not in _kinds(findings)


# ── editing a rule must not conflict with itself ────────────────────────

def test_editing_a_rule_excludes_itself_from_duplicate_check(hub_db):
    rule = create_rule(
        name="self", probe={"kind": "ping", "host": "10.0.0.5"},
        target={"kind": "device", "id": "dev_self"},
        action={"kind": "cycle", "power_off_seconds": 5},
    )
    # Re-evaluating the same rule, excluding its own id, finds no
    # duplicate — it would otherwise flag itself.
    findings = detect_conflicts(
        {
            "probe": {"kind": "ping", "host": "10.0.0.5"},
            "target": {"kind": "device", "id": "dev_self"},
            "action": {"kind": "cycle", "power_off_seconds": 5},
            "failure_threshold": 3, "window_seconds": 60,
            "cooldown_seconds": 300,
        },
        exclude_rule_id=rule["id"],
    )
    assert KIND_DUPLICATE_RULE not in _kinds(findings)


# ── severity ordering — blockers first ──────────────────────────────────

def test_findings_are_sorted_blockers_first(hub_db):
    # Set up both a duplicate (info) and a contradiction (block) on one
    # device, then assert the block sorts ahead of the info.
    create_rule(
        name="keep on", probe=_PROBE,
        target={"kind": "device", "id": "dev_multi"},
        action={"kind": "relay_on"},
    )
    create_rule(
        name="dup off", probe=_PROBE,
        target={"kind": "device", "id": "dev_multi"},
        action={"kind": "relay_off"},
    )
    findings = detect_conflicts({
        "probe": _PROBE,
        "target": {"kind": "device", "id": "dev_multi"},
        "action": {"kind": "relay_off"},
        "failure_threshold": 3, "window_seconds": 60, "cooldown_seconds": 300,
    })
    assert findings, "expected findings"
    assert findings[0].severity == SEVERITY_BLOCK
