"""v0.5.95 — rule-edit form-builder parity.

The structured rule-edit form (#15, v0.5.78) only ever covered the
network/integration/power probes and the cycle/hold_off/notify_only
actions. v0.5.95 brings it to parity with the create form:

- `epg_show_airing` joins `STRUCTURED_PROBE_KINDS`, so an EPG rule now
  edits in the structured form (was JSON-editor-only);
- the `relay_on` / `relay_off` / `apply_scene` / `binding` actions get
  field blocks on the edit page, pre-populated from the rule;
- `_action_form_supported` gates the form off for actions it can't
  round-trip (inline-`items` scenes, non-scene binding edges).

Verified against a live instance. Auth: Bearer headers throughout — the
`-m ci` gate runs over `http://localhost` where the Secure session
cookie isn't sent, and the auth resolver accepts Bearer on `/app/*`.

Runs in the `-m ci` gate.
"""

from __future__ import annotations

import pytest
import requests

from .conftest import unique_suffix

pytestmark = pytest.mark.ci


def _get_rule(base_url, admin_headers, rule_id):
    rows = requests.get(
        f"{base_url}/api/v1/admin/rules", headers=admin_headers, timeout=10
    ).json()["data"]
    return next((x for x in rows if x["id"] == rule_id), None)


# ── EPG probe — was JSON-editor-only, now structured ────────────────────

@pytest.fixture
def epg_rule(base_url, admin_headers):
    name = f"qa0595-epg-{unique_suffix()}"
    r = requests.post(
        f"{base_url}/api/v1/admin/rules",
        json={
            "name": name,
            "probe": {"kind": "epg_show_airing", "show": "Jeopardy",
                      "network": "ABC"},
            "target": {"kind": "tag", "tag": "qa0595-tv"},
            "action": {"kind": "notify_only"},
            "failure_threshold": 1,
        },
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 201, r.text
    created = r.json()["data"]
    yield created
    requests.delete(f"{base_url}/api/v1/admin/rules/{created['id']}",
                    headers=admin_headers, timeout=10)


def test_epg_rule_renders_structured_edit_form(base_url, admin_headers, epg_rule):
    r = requests.get(f"{base_url}/app/rules/{epg_rule['id']}/edit",
                     headers=admin_headers, timeout=10)
    assert r.status_code == 200, r.text
    body = r.text
    # structured form, not the amber "isn't available" fallback
    assert "Structured editing isn't available" not in body
    assert 'name="probe_kind"' in body
    assert 'id="probe_epg_show_airing_block"' in body
    # the epg option is the selected one + the show title is pre-filled
    assert '<option value="epg_show_airing"' in body
    assert 'name="epg_show" value="Jeopardy"' in body
    assert 'name="epg_network" value="ABC"' in body


def test_epg_rule_structured_save_updates_probe(base_url, admin_headers, epg_rule):
    r = requests.post(
        f"{base_url}/app/rules/{epg_rule['id']}/edit-form",
        data={
            "name": epg_rule["name"],
            "probe_kind": "epg_show_airing",
            "epg_show": "Wheel of Fortune",
            "epg_network": "NBC",
            "action_kind": "notify_only",
            "target_kind": "tag",
            "target_id": "qa0595-tv",
            "failure_threshold": "1",
            "recovery_threshold": "1",
            "window_seconds": "60",
            "cooldown_seconds": "300",
        },
        headers=admin_headers,
        allow_redirects=False,
        timeout=10,
    )
    assert r.status_code in (302, 303), r.text
    updated = _get_rule(base_url, admin_headers, epg_rule["id"])
    assert updated is not None
    assert updated["probe"]["kind"] == "epg_show_airing"
    assert updated["probe"]["show"] == "Wheel of Fortune"
    assert updated["probe"]["network"] == "NBC"


# ── binding action — scene pickers, pre-selected ────────────────────────

@pytest.fixture
def binding_rule(base_url, admin_headers):
    """Two scenes + a binding rule whose edges reference them by id."""
    made = []

    def _scene(label):
        r = requests.post(
            f"{base_url}/api/v1/admin/scenes",
            json={"name": f"qa0595-{label}-{unique_suffix()}",
                  "items": [{"device_id": "dev_qa0595", "relay": "off"}]},
            headers=admin_headers,
            timeout=10,
        )
        assert r.status_code == 201, r.text
        sc = r.json()["data"]
        made.append(sc["id"])
        return sc["id"]

    active_id, clear_id = _scene("active"), _scene("clear")
    r = requests.post(
        f"{base_url}/api/v1/admin/rules",
        json={
            "name": f"qa0595-bind-{unique_suffix()}",
            "probe": {"kind": "epg_show_airing", "show": "Jeopardy"},
            "target": {"kind": "tag", "tag": "qa0595-tv"},
            "action": {
                "kind": "binding",
                "on_active": {"kind": "apply_scene", "scene_id": active_id},
                "on_clear": {"kind": "apply_scene", "scene_id": clear_id},
            },
            "failure_threshold": 1,
        },
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 201, r.text
    rule = r.json()["data"]
    yield {"rule": rule, "active_id": active_id, "clear_id": clear_id}
    requests.delete(f"{base_url}/api/v1/admin/rules/{rule['id']}",
                    headers=admin_headers, timeout=10)
    for sid in made:
        requests.delete(f"{base_url}/api/v1/admin/scenes/{sid}",
                        headers=admin_headers, timeout=10)


def test_binding_rule_renders_structured_form_with_scenes_preselected(
        base_url, admin_headers, binding_rule):
    r = requests.get(f"{base_url}/app/rules/{binding_rule['rule']['id']}/edit",
                     headers=admin_headers, timeout=10)
    assert r.status_code == 200, r.text
    body = r.text
    assert "Structured editing isn't available" not in body
    assert 'id="action_kind"' in body
    assert 'id="action_binding_block"' in body
    assert 'rules_create_action.js' in body
    # the binding action option is selected + both edge scenes pre-selected
    assert '<option value="binding"' in body
    assert (f'<option value="{binding_rule["active_id"]}" selected'
            in body)
    assert (f'<option value="{binding_rule["clear_id"]}" selected'
            in body)


def test_binding_rule_structured_save_round_trips(
        base_url, admin_headers, binding_rule):
    """Saving a binding rule through the structured form keeps it a
    binding — the edges are not silently flattened to `cycle`."""
    r = requests.post(
        f"{base_url}/app/rules/{binding_rule['rule']['id']}/edit-form",
        data={
            "name": binding_rule["rule"]["name"],
            "probe_kind": "epg_show_airing",
            "epg_show": "Jeopardy",
            "action_kind": "binding",
            "binding_active_scene_id": binding_rule["active_id"],
            "binding_clear_scene_id": binding_rule["clear_id"],
            "target_kind": "tag",
            "target_id": "qa0595-tv",
            "failure_threshold": "1",
            "recovery_threshold": "1",
            "window_seconds": "60",
            "cooldown_seconds": "300",
        },
        headers=admin_headers,
        allow_redirects=False,
        timeout=10,
    )
    assert r.status_code in (302, 303), r.text
    updated = _get_rule(base_url, admin_headers, binding_rule["rule"]["id"])
    assert updated is not None
    assert updated["action"]["kind"] == "binding"
    assert updated["action"]["on_active"]["scene_id"] == binding_rule["active_id"]
    assert updated["action"]["on_clear"]["scene_id"] == binding_rule["clear_id"]
