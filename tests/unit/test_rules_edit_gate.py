"""Unit tests — `_action_form_supported` (v0.5.95).

The structured rule-edit form is offered only when it can round-trip
the rule without data loss. The probe side is gated by the
`STRUCTURED_PROBE_KINDS` frozenset; the action side by
`_action_form_supported` — `apply_scene` / `binding` are form-editable
only when they reference saved scenes by `scene_id`. Pure function, no
DB, no app context.
"""

from __future__ import annotations

from app.blueprints.admin.rules import (
    STRUCTURED_PROBE_KINDS,
    _action_form_supported,
)


# ── leaf actions — always form-supported ───────────────────────────────

def test_leaf_actions_are_supported():
    for kind in ("cycle", "hold_off", "notify_only", "relay_on", "relay_off"):
        assert _action_form_supported({"kind": kind}) is True


# ── apply_scene — scene_id only ─────────────────────────────────────────

def test_apply_scene_with_scene_id_is_supported():
    assert _action_form_supported(
        {"kind": "apply_scene", "scene_id": "scn_abc"}
    ) is True


def test_apply_scene_with_inline_items_is_not_supported():
    # inline `items` has no field block — JSON-editor only.
    assert _action_form_supported(
        {"kind": "apply_scene", "items": [{"device_id": "dev_1", "relay": True}]}
    ) is False


# ── binding — both edges must be scene-backed apply_scene ───────────────

def test_binding_with_two_scene_edges_is_supported():
    assert _action_form_supported({
        "kind": "binding",
        "on_active": {"kind": "apply_scene", "scene_id": "scn_a"},
        "on_clear": {"kind": "apply_scene", "scene_id": "scn_b"},
    }) is True


def test_binding_with_a_non_scene_edge_is_not_supported():
    assert _action_form_supported({
        "kind": "binding",
        "on_active": {"kind": "apply_scene", "scene_id": "scn_a"},
        "on_clear": {"kind": "relay_off"},
    }) is False


def test_binding_with_a_scene_edge_missing_scene_id_is_not_supported():
    assert _action_form_supported({
        "kind": "binding",
        "on_active": {"kind": "apply_scene", "items": []},
        "on_clear": {"kind": "apply_scene", "scene_id": "scn_b"},
    }) is False


def test_binding_with_a_missing_edge_is_not_supported():
    assert _action_form_supported({
        "kind": "binding",
        "on_active": {"kind": "apply_scene", "scene_id": "scn_a"},
    }) is False


# ── degenerate / unknown ────────────────────────────────────────────────

def test_empty_or_unknown_action_is_not_supported():
    assert _action_form_supported({}) is False
    assert _action_form_supported(None) is False
    assert _action_form_supported({"kind": "host_awake"}) is False


# ── probe-side gate — epg joined the set in v0.5.95 ─────────────────────

def test_epg_show_airing_is_a_structured_probe_kind():
    assert "epg_show_airing" in STRUCTURED_PROBE_KINDS
