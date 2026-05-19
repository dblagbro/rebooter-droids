"""Unit tests — `list_rules_for_device` / `list_for_device` (v0.5.97).

The device-detail page's Watchdog / Schedule sections list the rules
and schedules whose target resolves to the device. Both helpers reuse
the runtime's `resolve_target_devices`. DB-backed — `hub_db` fixture.
"""

from __future__ import annotations

from app.services.schedules import create as create_schedule
from app.services.schedules import list_for_device as schedules_for_device
from app.services.watchdog import create_rule, list_rules_for_device, set_enabled

_PROBE = {"kind": "internet"}
_ACTION = {"kind": "notify_only"}


def _rule(name, target):
    return create_rule(name=name, probe=_PROBE, action=_ACTION, target=target)


# ── watchdog rules ──────────────────────────────────────────────────────

def test_direct_device_target_is_listed(hub_db):
    _rule("targets dev_1", {"kind": "device", "id": "dev_1"})
    rules = list_rules_for_device("dev_1")
    assert [r["name"] for r in rules] == ["targets dev_1"]


def test_a_rule_for_another_device_is_not_listed(hub_db):
    _rule("targets dev_2", {"kind": "device", "id": "dev_2"})
    assert list_rules_for_device("dev_1") == []


def test_tag_targeted_rule_matches_no_device(hub_db):
    # tag targets resolve to no devices (no device-tag store yet) —
    # consistent with the runtime treating them as a no-op.
    _rule("tag rule", {"kind": "tag", "tag": "edge"})
    assert list_rules_for_device("dev_1") == []


def test_a_disabled_rule_is_still_listed(hub_db):
    rule = _rule("disabled rule", {"kind": "device", "id": "dev_1"})
    set_enabled(rule["id"], False)
    rules = list_rules_for_device("dev_1")
    assert len(rules) == 1
    assert rules[0]["enabled"] is False


def test_only_the_matching_rules_are_returned(hub_db):
    _rule("for dev_1", {"kind": "device", "id": "dev_1"})
    _rule("for dev_2", {"kind": "device", "id": "dev_2"})
    _rule("for dev_1 again", {"kind": "device", "id": "dev_1"})
    assert sorted(r["name"] for r in list_rules_for_device("dev_1")) == [
        "for dev_1", "for dev_1 again"
    ]


# ── schedules ───────────────────────────────────────────────────────────

def test_schedule_for_device_is_listed(hub_db):
    create_schedule(
        name="nightly cycle", kind="power_cycle", recurrence="daily",
        at_time_utc="03:00", target={"kind": "device", "id": "dev_1"},
    )
    scheds = schedules_for_device("dev_1")
    assert [s["name"] for s in scheds] == ["nightly cycle"]
    assert schedules_for_device("dev_2") == []
