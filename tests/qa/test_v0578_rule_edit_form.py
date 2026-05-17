"""v0.5.78 (#15) — structured rule-edit form.

Rule editing was raw-JSON-only. v0.5.78 adds a structured edit form
mirroring the create form, posting to `POST /app/rules/<id>/edit-form`.

These verify, against a live instance:
- the edit page renders the structured form (probe-kind select + the
  edit-form action) for a form-supported probe kind;
- a structured-form submit updates the probe / name / threshold;
- fields the structured form doesn't surface (max_retries, description)
  survive a structured save untouched — no silent data loss.

Auth: Bearer headers throughout. The `-m ci` gate runs over
`http://localhost`, where the Secure session cookie isn't sent — so
cookie auth would 401. The shared auth resolver accepts Bearer on the
`/app/*` UI routes too, so Bearer works everywhere.

Runs in the `-m ci` gate.
"""

from __future__ import annotations

import pytest
import requests

from .conftest import unique_suffix

pytestmark = pytest.mark.ci


@pytest.fixture
def rule(base_url, admin_headers):
    """A ping-probe rule with a non-default max_retries + a description —
    neither is surfaced by the structured form, so they let us prove the
    structured save preserves un-surfaced fields."""
    name = f"qa0578-{unique_suffix()}"
    r = requests.post(
        f"{base_url}/api/v1/admin/rules",
        json={
            "name": name,
            "probe": {"kind": "ping", "host": "192.168.1.1"},
            "target": {"kind": "tag", "tag": "qa0578-before"},
            "action": {"kind": "cycle", "power_off_seconds": 5},
            "failure_threshold": 3,
            "max_retries": 7,
            "description": "qa0578-preserve-me",
        },
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 201, r.text
    created = r.json()["data"]
    yield created
    requests.delete(
        f"{base_url}/api/v1/admin/rules/{created['id']}",
        headers=admin_headers,
        timeout=10,
    )


def _get_rule(base_url, admin_headers, rule_id):
    rows = requests.get(
        f"{base_url}/api/v1/admin/rules", headers=admin_headers, timeout=10
    ).json()["data"]
    return next((x for x in rows if x["id"] == rule_id), None)


def test_edit_page_renders_structured_form(base_url, admin_headers, rule):
    r = requests.get(
        f"{base_url}/app/rules/{rule['id']}/edit",
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # structured form present + wired to the new edit-form route
    assert 'name="probe_kind"' in body
    assert f"/app/rules/{rule['id']}/edit-form" in body
    # pre-populated with the rule's current name
    assert rule["name"] in body
    # JSON editor still available as the escape hatch
    assert 'name="rule_json"' in body


def test_structured_edit_updates_rule(base_url, admin_headers, rule):
    new_name = f"{rule['name']}-edited"
    r = requests.post(
        f"{base_url}/app/rules/{rule['id']}/edit-form",
        data={
            "name": new_name,
            "probe_kind": "ping",
            "probe_arg": "10.9.9.9",
            "action_kind": "cycle",
            "power_off_seconds": "5",
            "post_reboot_holdoff_seconds": "180",
            "target_kind": "tag",
            "target_id": "qa0578-after",
            "failure_threshold": "5",
            "recovery_threshold": "2",
            "window_seconds": "60",
            "cooldown_seconds": "300",
        },
        headers=admin_headers,
        allow_redirects=False,
        timeout=10,
    )
    assert r.status_code in (302, 303), r.text  # redirect → rules page
    updated = _get_rule(base_url, admin_headers, rule["id"])
    assert updated is not None
    assert updated["name"] == new_name
    assert updated["probe"]["kind"] == "ping"
    assert updated["probe"]["host"] == "10.9.9.9"
    assert updated["target"]["kind"] == "tag"
    assert updated["target"]["tag"] == "qa0578-after"
    assert updated["failure_threshold"] == 5


def test_structured_edit_preserves_unsurfaced_fields(base_url, admin_headers, rule):
    """max_retries + description aren't in the structured form — a
    structured save must carry them through untouched."""
    r = requests.post(
        f"{base_url}/app/rules/{rule['id']}/edit-form",
        data={
            "name": rule["name"],
            "probe_kind": "ping",
            "probe_arg": "192.168.1.1",
            "action_kind": "cycle",
            "power_off_seconds": "5",
            "post_reboot_holdoff_seconds": "180",
            "target_kind": "tag",
            "target_id": "qa0578-before",
            "failure_threshold": "3",
            "recovery_threshold": "2",
            "window_seconds": "60",
            "cooldown_seconds": "300",
        },
        headers=admin_headers,
        allow_redirects=False,
        timeout=10,
    )
    assert r.status_code in (302, 303), r.text
    updated = _get_rule(base_url, admin_headers, rule["id"])
    assert updated is not None
    assert updated["max_retries"] == 7, "max_retries must survive a structured save"
    assert updated["description"] == "qa0578-preserve-me"
