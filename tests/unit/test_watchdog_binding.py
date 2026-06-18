"""Unit tests — condition bindings (Stage A).

A *binding* rule is level-triggered: its `action` is
`{kind:'binding', on_active, on_clear}` and the target device-state
follows the probe both ways — `on_active` applied once the probe is
stably `success`, `on_clear` once stably `failure`. This is the
primitive behind "while Jeopardy is airing, surround off; when it
clears, surround on".

Covers `validate_action` (pure), `create_rule` with a binding, and
the `_binding_tick` runtime (edges, idempotency, debounce, the
`relay_on`/`relay_off` set-state actions, protected-device skip).
DB-backed cases use the `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import Command, Device, WatchdogRule
from app.services.watchdog import (
    WatchdogValidationError,
    validate_action,
    create_rule,
)
from app.services.watchdog_runtime._state import _update_state_and_maybe_fire


# ── validate_action (pure) ────────────────────────────────────────────

@pytest.mark.parametrize(
    "kind", ["cycle", "hold_off", "notify_only", "relay_on", "relay_off"]
)
def testvalidate_action_accepts_leaf_kinds(kind):
    validate_action({"kind": kind})  # no raise


def testvalidate_action_rejects_unknown_kind():
    with pytest.raises(WatchdogValidationError):
        validate_action({"kind": "teleport"})


def testvalidate_action_accepts_well_formed_binding():
    validate_action({
        "kind": "binding",
        "on_active": {"kind": "relay_off"},
        "on_clear": {"kind": "relay_on"},
    })


def testvalidate_action_rejects_binding_missing_sub_action():
    with pytest.raises(WatchdogValidationError):
        validate_action({"kind": "binding", "on_active": {"kind": "relay_off"}})


def testvalidate_action_rejects_binding_with_non_leaf_sub():
    # a binding sub-action must be a leaf — no binding-in-binding
    with pytest.raises(WatchdogValidationError):
        validate_action({
            "kind": "binding",
            "on_active": {"kind": "relay_off"},
            "on_clear": {"kind": "binding"},
        })


# ── create_rule with a binding action ──────────────────────────────────

def _binding_rule(*, name="binding-rule", on_active="relay_off",
                  on_clear="relay_on", recovery_threshold=2,
                  failure_threshold=2, device_id="dev-1") -> str:
    return create_rule(
        name=name,
        probe={"kind": "epg_show_airing", "show": "Jeopardy"},
        target={"kind": "device", "id": device_id},
        action={
            "kind": "binding",
            "on_active": {"kind": on_active},
            "on_clear": {"kind": on_clear},
        },
        recovery_threshold=recovery_threshold,
        failure_threshold=failure_threshold,
    )["id"]


def test_create_rule_accepts_a_binding_and_renders_a_level_sentence(hub_db):
    with session_scope() as s:
        s.add(Device(id="dev-1"))
    rule = create_rule(
        name="Erica TV",
        probe={"kind": "epg_show_airing", "show": "Jeopardy"},
        target={"kind": "device", "id": "dev-1"},
        action={
            "kind": "binding",
            "on_active": {"kind": "relay_off"},
            "on_clear": {"kind": "relay_on"},
        },
    )
    assert rule["action"]["kind"] == "binding"
    # The sentence reads as a level-triggered binding, not a
    # failure-streak remediation.
    assert rule["sentence"].startswith("While ")
    assert "fails" not in rule["sentence"]


def test_create_rule_accepts_relay_set_as_a_plain_action(hub_db):
    with session_scope() as s:
        s.add(Device(id="dev-1"))
    rule = create_rule(
        name="plain relay_off",
        probe={"kind": "ping", "host": "10.0.0.1"},
        target={"kind": "device", "id": "dev-1"},
        action={"kind": "relay_off"},
    )
    assert rule["action"]["kind"] == "relay_off"


# ── _binding_tick runtime ──────────────────────────────────────────────

def _evaluate(rule_id: str, outcome: str) -> bool:
    """One binding evaluation, mirroring a watchdog tick — fresh
    session per tick, as the real scheduler does."""
    with session_scope() as s:
        rule = s.get(WatchdogRule, rule_id)
        return _update_state_and_maybe_fire(
            s, rule, outcome, {}, datetime.now(timezone.utc)
        )


def _commands(device_id: str) -> list[str]:
    with session_scope() as s:
        return [
            c.type for c in s.scalars(
                select(Command).where(Command.device_id == device_id)
                .order_by(Command.created_at.asc())
            )
        ]


def test_binding_applies_on_active_edge_then_is_idempotent(hub_db):
    with session_scope() as s:
        s.add(Device(id="dev-1"))
    rid = _binding_rule(recovery_threshold=2)
    assert _evaluate(rid, "success") is False   # streak 1 — settling
    assert _evaluate(rid, "success") is True    # streak 2 — edge → relay_off
    assert _commands("dev-1") == ["relay_off"]
    # Steady success does not re-fire.
    assert _evaluate(rid, "success") is False
    assert _evaluate(rid, "success") is False
    assert _commands("dev-1") == ["relay_off"]


def test_binding_applies_on_clear_edge(hub_db):
    with session_scope() as s:
        s.add(Device(id="dev-1"))
    rid = _binding_rule(recovery_threshold=1, failure_threshold=1)
    _evaluate(rid, "success")                   # active → relay_off
    assert _evaluate(rid, "failure") is True    # cleared → relay_on
    assert _commands("dev-1") == ["relay_off", "relay_on"]


def test_binding_refires_on_a_fresh_active_edge(hub_db):
    with session_scope() as s:
        s.add(Device(id="dev-1"))
    rid = _binding_rule(recovery_threshold=1, failure_threshold=1)
    _evaluate(rid, "success")    # active  → relay_off
    _evaluate(rid, "failure")    # cleared → relay_on
    _evaluate(rid, "success")    # active  → relay_off again
    assert _commands("dev-1") == ["relay_off", "relay_on", "relay_off"]


def test_binding_debounces_until_threshold(hub_db):
    with session_scope() as s:
        s.add(Device(id="dev-1"))
    rid = _binding_rule(recovery_threshold=3)
    assert _evaluate(rid, "success") is False
    assert _evaluate(rid, "success") is False
    assert _commands("dev-1") == []             # not stable yet
    assert _evaluate(rid, "success") is True    # 3rd consecutive → edge
    assert _commands("dev-1") == ["relay_off"]


def test_binding_probe_error_holds_the_current_state(hub_db):
    with session_scope() as s:
        s.add(Device(id="dev-1"))
    rid = _binding_rule(recovery_threshold=1, failure_threshold=1)
    _evaluate(rid, "success")                       # active → relay_off
    assert _evaluate(rid, "probe_error") is False   # transient — no flip
    assert _commands("dev-1") == ["relay_off"]


def test_binding_notify_only_clear_enqueues_no_command(hub_db):
    with session_scope() as s:
        s.add(Device(id="dev-1"))
    rid = _binding_rule(on_active="relay_off", on_clear="notify_only",
                        recovery_threshold=1, failure_threshold=1)
    _evaluate(rid, "success")    # → relay_off
    _evaluate(rid, "failure")    # → notify_only (no command)
    assert _commands("dev-1") == ["relay_off"]


def test_binding_status_reflects_the_edge(hub_db):
    with session_scope() as s:
        s.add(Device(id="dev-1"))
    rid = _binding_rule(recovery_threshold=1, failure_threshold=1)
    _evaluate(rid, "success")
    with session_scope() as s:
        assert s.get(WatchdogRule, rid).status == "firing"
    _evaluate(rid, "failure")
    with session_scope() as s:
        assert s.get(WatchdogRule, rid).status == "armed"


def test_binding_skips_a_protected_device(hub_db):
    # A protected device is a soft gate — the binding edge still fires
    # (and is recorded) but the relay command is skipped, like _fire_cycle.
    with session_scope() as s:
        s.add(Device(id="dev-1", is_protected=True))
    rid = _binding_rule(recovery_threshold=1, failure_threshold=1)
    assert _evaluate(rid, "success") is True   # edge applied
    assert _commands("dev-1") == []            # but command was skipped
