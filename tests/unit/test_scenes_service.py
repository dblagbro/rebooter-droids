"""Unit tests — the named scene library (Stage C).

`app/services/scenes.py` — CRUD over reusable, named device scenes — and
the wiring that lets a watchdog `apply_scene` action reference a scene
by `scene_id` instead of inlining its items. DB-backed cases use the
`hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import Command, Device
from app.services.scenes import (
    SceneError,
    create_scene,
    delete_scene,
    get_scene,
    list_scenes,
    scene_items,
    update_scene,
    validate_scene_items,
)
from app.services.watchdog import (
    WatchdogValidationError,
    _validate_action,
    create_rule,
)
from app.services.watchdog_runtime._actions import _fire_scene


# ── validate_scene_items (pure) ────────────────────────────────────────

def test_validate_scene_items_cleans_and_returns():
    out = validate_scene_items([
        {"device_id": "d1", "relay": "off", "junk": "dropped"},
        {"device_id": "d2", "config": {"device_name": "Y"}},
    ])
    assert out == [
        {"device_id": "d1", "relay": "off"},
        {"device_id": "d2", "config": {"device_name": "Y"}},
    ]


def test_validate_scene_items_rejects_empty_list():
    with pytest.raises(SceneError):
        validate_scene_items([])


def test_validate_scene_items_rejects_missing_device_id():
    with pytest.raises(SceneError):
        validate_scene_items([{"relay": "off"}])


def test_validate_scene_items_rejects_bad_relay():
    with pytest.raises(SceneError):
        validate_scene_items([{"device_id": "d1", "relay": "spin"}])


def test_validate_scene_items_rejects_item_with_nothing_to_do():
    with pytest.raises(SceneError):
        validate_scene_items([{"device_id": "d1"}])


# ── CRUD ───────────────────────────────────────────────────────────────

def test_create_scene(hub_db):
    scene = create_scene(
        name="Erica TV", description="audio off while she watches",
        items=[{"device_id": "surround", "relay": "off"},
               {"device_id": "subwoofer", "relay": "off"}],
    )
    assert scene["id"].startswith("scn_")
    assert scene["device_count"] == 2


def test_create_scene_rejects_blank_name(hub_db):
    with pytest.raises(SceneError):
        create_scene(name="  ", description=None,
                     items=[{"device_id": "d1", "relay": "off"}])


def test_create_scene_rejects_duplicate_name(hub_db):
    create_scene(name="Dup", description=None,
                 items=[{"device_id": "d1", "relay": "off"}])
    with pytest.raises(SceneError) as exc:
        create_scene(name="Dup", description=None,
                     items=[{"device_id": "d2", "relay": "on"}])
    assert exc.value.code == "name_conflict"


def test_create_scene_rejects_invalid_items(hub_db):
    with pytest.raises(SceneError):
        create_scene(name="Bad", description=None, items=[])


def test_list_scenes_sorted_by_name(hub_db):
    create_scene(name="Zeta", description=None,
                 items=[{"device_id": "d", "relay": "on"}])
    create_scene(name="Alpha", description=None,
                 items=[{"device_id": "d", "relay": "on"}])
    assert [s["name"] for s in list_scenes()] == ["Alpha", "Zeta"]


def test_get_scene_known_and_unknown(hub_db):
    sid = create_scene(name="S", description=None,
                       items=[{"device_id": "d", "relay": "on"}])["id"]
    assert get_scene(sid)["name"] == "S"
    assert get_scene("scn_nope") is None


def test_update_scene(hub_db):
    sid = create_scene(name="S", description=None,
                       items=[{"device_id": "d", "relay": "on"}])["id"]
    updated = update_scene(
        sid, name="S2", description="renamed",
        items=[{"device_id": "d", "relay": "off"},
               {"device_id": "e", "relay": "off"}],
    )
    assert updated["name"] == "S2"
    assert updated["device_count"] == 2
    assert update_scene("scn_nope", name="X", description=None,
                        items=[{"device_id": "d", "relay": "on"}]) is None


def test_delete_scene(hub_db):
    sid = create_scene(name="S", description=None,
                       items=[{"device_id": "d", "relay": "on"}])["id"]
    assert delete_scene(sid) is True
    assert get_scene(sid) is None
    assert delete_scene("scn_nope") is False


def test_scene_items_returns_raw_items(hub_db):
    sid = create_scene(name="S", description=None,
                       items=[{"device_id": "d1", "relay": "off"}])["id"]
    assert scene_items(sid) == [{"device_id": "d1", "relay": "off"}]
    assert scene_items("scn_nope") is None


# ── apply_scene referencing a saved scene ──────────────────────────────

def test_validate_action_accepts_apply_scene_with_scene_id():
    _validate_action({"kind": "apply_scene", "scene_id": "scn_abc"})


def test_validate_action_rejects_apply_scene_with_neither_id_nor_items():
    with pytest.raises(WatchdogValidationError):
        _validate_action({"kind": "apply_scene"})


def test_create_rule_binding_referencing_a_saved_scene(hub_db):
    sid = create_scene(name="Erica TV", description=None,
                       items=[{"device_id": "surround", "relay": "off"}])["id"]
    rule = create_rule(
        name="Erica Jeopardy",
        probe={"kind": "epg_show_airing", "show": "Jeopardy"},
        target={"kind": "tag", "tag": "audio"},
        action={"kind": "binding",
                "on_active": {"kind": "apply_scene", "scene_id": sid},
                "on_clear": {"kind": "apply_scene", "scene_id": sid}},
    )
    assert rule["action"]["on_active"]["scene_id"] == sid


def test_fire_scene_resolves_a_saved_scene(hub_db):
    with session_scope() as s:
        s.add(Device(id="surround"))
        s.add(Device(id="subwoofer"))
    sid = create_scene(name="Erica TV", description=None, items=[
        {"device_id": "surround", "relay": "off"},
        {"device_id": "subwoofer", "relay": "off"}])["id"]
    result = _fire_scene(SimpleNamespace(id="r1"),
                         {"kind": "apply_scene", "scene_id": sid})
    assert len(result["applied"]) == 2
    with session_scope() as s:
        types = sorted(c.type for c in s.scalars(select(Command)))
    assert types == ["relay_off", "relay_off"]


def test_fire_scene_missing_scene_is_reported_not_raised(hub_db):
    result = _fire_scene(SimpleNamespace(id="r1"),
                         {"kind": "apply_scene", "scene_id": "scn_gone"})
    assert "error" in result
    assert result["applied"] == []
