"""Unit tests — multi-device scene actions (Stage B).

`apply_scene` sets several named devices to a relay state and/or an
`apply_config` payload in one action — "while Jeopardy airs, surround
OFF and subwoofer OFF; when it clears, both back ON". It is usable as
a plain rule action and, paired with Stage A, as a binding's
`on_active` / `on_clear` edge — the complete TV-scheduling shape.

Covers `validate_action` (pure; recurses through `_validate_leaf`),
`create_rule`,
the `_fire_scene` runtime, and the binding+scene integration.
DB-backed cases use the `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import Command, Device, WatchdogRule
from app.services.watchdog import (
    WatchdogValidationError,
    validate_action,
    create_rule,
)
from app.services.watchdog_runtime._actions import _fire_scene
from app.services.watchdog_runtime._state import _update_state_and_maybe_fire


def _types(device_id: str) -> list[str]:
    with session_scope() as s:
        return [
            c.type for c in s.scalars(
                select(Command).where(Command.device_id == device_id)
                .order_by(Command.created_at.asc())
            )
        ]


# ── validate_action — apply_scene (pure) ──────────────────────────────

def test_validate_accepts_apply_scene():
    validate_action({"kind": "apply_scene",
                      "items": [{"device_id": "d1", "relay": "off"}]})


def test_validate_rejects_apply_scene_with_no_items():
    with pytest.raises(WatchdogValidationError):
        validate_action({"kind": "apply_scene", "items": []})
    with pytest.raises(WatchdogValidationError):
        validate_action({"kind": "apply_scene"})


def test_validate_rejects_item_without_device_id():
    with pytest.raises(WatchdogValidationError):
        validate_action({"kind": "apply_scene", "items": [{"relay": "off"}]})


def test_validate_rejects_item_with_bad_relay_value():
    with pytest.raises(WatchdogValidationError):
        validate_action({"kind": "apply_scene",
                          "items": [{"device_id": "d1", "relay": "sideways"}]})


def test_validate_rejects_item_with_neither_relay_nor_config():
    with pytest.raises(WatchdogValidationError):
        validate_action({"kind": "apply_scene",
                          "items": [{"device_id": "d1"}]})


def test_validate_accepts_config_only_item():
    validate_action({"kind": "apply_scene",
                      "items": [{"device_id": "d1",
                                 "config": {"device_name": "X"}}]})


def test_validate_accepts_binding_with_apply_scene_edges():
    validate_action({
        "kind": "binding",
        "on_active": {"kind": "apply_scene",
                      "items": [{"device_id": "d1", "relay": "off"}]},
        "on_clear": {"kind": "apply_scene",
                     "items": [{"device_id": "d1", "relay": "on"}]},
    })


def test_validate_rejects_binding_with_malformed_scene_edge():
    # Proves _validate_leaf recurses through the binding sub-actions —
    # a binding edge with an empty apply_scene must be rejected.
    with pytest.raises(WatchdogValidationError):
        validate_action({
            "kind": "binding",
            "on_active": {"kind": "apply_scene", "items": []},
            "on_clear": {"kind": "relay_on"},
        })


# ── create_rule ────────────────────────────────────────────────────────

def test_create_rule_with_apply_scene_binding(hub_db):
    with session_scope() as s:
        s.add(Device(id="surround"))
        s.add(Device(id="subwoofer"))
    rule = create_rule(
        name="Erica Jeopardy scene",
        probe={"kind": "epg_show_airing", "show": "Jeopardy"},
        target={"kind": "tag", "tag": "audio"},
        action={
            "kind": "binding",
            "on_active": {"kind": "apply_scene", "items": [
                {"device_id": "surround", "relay": "off"},
                {"device_id": "subwoofer", "relay": "off"},
            ]},
            "on_clear": {"kind": "apply_scene", "items": [
                {"device_id": "surround", "relay": "on"},
                {"device_id": "subwoofer", "relay": "on"},
            ]},
        },
    )
    assert rule["action"]["kind"] == "binding"
    assert "scene" in rule["sentence"]


# ── _fire_scene runtime ────────────────────────────────────────────────

def test_fire_scene_sets_each_device_relay(hub_db):
    with session_scope() as s:
        s.add(Device(id="surround"))
        s.add(Device(id="subwoofer"))
    result = _fire_scene(SimpleNamespace(id="r1"), {
        "kind": "apply_scene", "items": [
            {"device_id": "surround", "relay": "off"},
            {"device_id": "subwoofer", "relay": "off"},
        ],
    })
    assert len(result["applied"]) == 2
    assert _types("surround") == ["relay_off"]
    assert _types("subwoofer") == ["relay_off"]


def test_fire_scene_pushes_apply_config(hub_db):
    with session_scope() as s:
        s.add(Device(id="soundbar"))
    _fire_scene(SimpleNamespace(id="r1"), {
        "kind": "apply_scene",
        "items": [{"device_id": "soundbar",
                   "config": {"device_name": "Soundbar"}}],
    })
    assert _types("soundbar") == ["apply_config"]


def test_fire_scene_relay_and_config_on_one_item(hub_db):
    with session_scope() as s:
        s.add(Device(id="d1"))
    _fire_scene(SimpleNamespace(id="r1"), {
        "kind": "apply_scene",
        "items": [{"device_id": "d1", "relay": "off",
                   "config": {"device_name": "D1"}}],
    })
    assert sorted(_types("d1")) == ["apply_config", "relay_off"]


def test_fire_scene_relay_cycle(hub_db):
    with session_scope() as s:
        s.add(Device(id="d1"))
    _fire_scene(SimpleNamespace(id="r1"), {
        "kind": "apply_scene",
        "items": [{"device_id": "d1", "relay": "cycle"}],
    })
    assert _types("d1") == ["relay_cycle"]


def test_fire_scene_skips_a_protected_device(hub_db):
    with session_scope() as s:
        s.add(Device(id="prot", is_protected=True))
        s.add(Device(id="ok"))
    result = _fire_scene(SimpleNamespace(id="r1"), {
        "kind": "apply_scene", "items": [
            {"device_id": "prot", "relay": "off"},
            {"device_id": "ok", "relay": "off"},
        ],
    })
    assert [a["device_id"] for a in result["applied"]] == ["ok"]
    assert len(result["skipped"]) == 1
    assert _types("prot") == []        # protection wins
    assert _types("ok") == ["relay_off"]


# ── the full TV-scheduling shape: binding + scene ──────────────────────

def test_binding_with_scene_edges_drives_the_whole_audio_group(hub_db):
    with session_scope() as s:
        s.add(Device(id="surround"))
        s.add(Device(id="subwoofer"))
    rid = create_rule(
        name="Erica Jeopardy",
        probe={"kind": "epg_show_airing", "show": "Jeopardy"},
        target={"kind": "tag", "tag": "audio"},
        action={
            "kind": "binding",
            "on_active": {"kind": "apply_scene", "items": [
                {"device_id": "surround", "relay": "off"},
                {"device_id": "subwoofer", "relay": "off"}]},
            "on_clear": {"kind": "apply_scene", "items": [
                {"device_id": "surround", "relay": "on"},
                {"device_id": "subwoofer", "relay": "on"}]},
        },
        recovery_threshold=1, failure_threshold=1,
    )["id"]

    # Jeopardy airing → on_active scene: both devices off.
    with session_scope() as s:
        _update_state_and_maybe_fire(
            s, s.get(WatchdogRule, rid), "success", {},
            datetime.now(timezone.utc))
    assert _types("surround") == ["relay_off"]
    assert _types("subwoofer") == ["relay_off"]

    # Jeopardy ends → on_clear scene: both back on.
    with session_scope() as s:
        _update_state_and_maybe_fire(
            s, s.get(WatchdogRule, rid), "failure", {},
            datetime.now(timezone.utc))
    assert _types("surround") == ["relay_off", "relay_on"]
    assert _types("subwoofer") == ["relay_off", "relay_on"]
