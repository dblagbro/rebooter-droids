"""v0.5.101 — form-input hardening: numeric fields can't 500 the page.

Before v0.5.101, several blueprint handlers used unwrapped
`int(request.form.get(name) or default)` / `int(body.get(name) or
default)` calls. A non-numeric operator input raised an uncaught
`ValueError` → HTTP 500 instead of a friendly flash + form re-render.
The refactor-log v0.5.67 entry called this out as "carried over
unchanged; fixing it would be a behaviour change, deferred".

v0.5.101 wired every site through a typed `_int_field()` helper in
`app/blueprints/admin/_common.py` that raises a typed
`FormValidationError`; each handler catches it and either flashes +
redirects (form routes) or returns a `validation_failed` envelope
(JSON API routes). These tests pin the new behaviour against every
fixed site so a future regression can't reintroduce the 500.

Runs in the `-m ci` gate.
"""

from __future__ import annotations

import json

import pytest
import requests

from .conftest import unique_suffix

pytestmark = pytest.mark.ci


# ── rules — structured create form ──────────────────────────────────────

def test_rules_create_form_rejects_a_non_numeric_failure_threshold(
        base_url, admin_headers):
    r = requests.post(
        f"{base_url}/app/rules",
        data={
            "name": f"qa0101-{unique_suffix()}",
            "probe_kind": "internet",
            "action_kind": "notify_only",
            "target_kind": "tag",
            "target_id": "qa0101-form",
            # the defect — non-numeric where an int was unwrapped
            "failure_threshold": "not-a-number",
            "recovery_threshold": "2",
            "window_seconds": "60",
            "cooldown_seconds": "300",
        },
        headers=admin_headers,
        allow_redirects=False,
        timeout=10,
    )
    # was 500 pre-v0.5.101; must now be a flash + redirect
    assert r.status_code in (302, 303), (
        f"expected redirect (flash), got {r.status_code}: {r.text[:200]}"
    )


# ── rules — JSON-editor create ──────────────────────────────────────────

def test_rules_create_json_editor_rejects_a_non_numeric_threshold(
        base_url, admin_headers):
    body = {
        "name": f"qa0101-json-{unique_suffix()}",
        "probe": {"kind": "internet"},
        "target": {"kind": "tag", "tag": "qa0101-json"},
        "action": {"kind": "notify_only"},
        "failure_threshold": "abc",  # the defect
    }
    r = requests.post(
        f"{base_url}/app/rules/json",
        data={"rule_json": json.dumps(body)},
        headers=admin_headers,
        allow_redirects=False,
        timeout=10,
    )
    # `_err()` re-renders the page (200), not a 500
    assert r.status_code == 200, (
        f"expected 200 with error rendered, got {r.status_code}"
    )
    assert "must be an integer" in r.text or "failure_threshold" in r.text


# ── rules — JSON-editor edit ────────────────────────────────────────────

@pytest.fixture
def _rule(base_url, admin_headers):
    """A minimal rule we can edit + tear down."""
    r = requests.post(
        f"{base_url}/api/v1/admin/rules",
        json={
            "name": f"qa0101-edit-{unique_suffix()}",
            "probe": {"kind": "internet"},
            "target": {"kind": "tag", "tag": "qa0101"},
            "action": {"kind": "notify_only"},
        },
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 201, r.text
    rid = r.json()["data"]["id"]
    yield rid
    requests.delete(f"{base_url}/api/v1/admin/rules/{rid}",
                    headers=admin_headers, timeout=10)


def test_rules_edit_json_editor_rejects_a_non_numeric_threshold(
        base_url, admin_headers, _rule):
    body = {
        "name": "edited",
        "probe": {"kind": "internet"},
        "target": {"kind": "tag", "tag": "qa0101"},
        "action": {"kind": "notify_only"},
        "failure_threshold": "xyz",  # the defect
    }
    r = requests.post(
        f"{base_url}/app/rules/{_rule}/edit",
        data={"rule_json": json.dumps(body)},
        headers=admin_headers,
        allow_redirects=False,
        timeout=10,
    )
    assert r.status_code == 200, (
        f"expected 200 with error rendered, got {r.status_code}"
    )
    assert "must be an integer" in r.text or "failure_threshold" in r.text


# ── rules — structured edit form ────────────────────────────────────────

def test_rules_edit_form_rejects_a_non_numeric_threshold(
        base_url, admin_headers, _rule):
    r = requests.post(
        f"{base_url}/app/rules/{_rule}/edit-form",
        data={
            "name": "qa0101-edit-form",
            "probe_kind": "internet",
            "action_kind": "notify_only",
            "target_kind": "tag",
            "target_id": "qa0101",
            "failure_threshold": "not-a-number",  # the defect
            "recovery_threshold": "2",
            "window_seconds": "60",
            "cooldown_seconds": "300",
        },
        headers=admin_headers,
        allow_redirects=False,
        timeout=10,
    )
    assert r.status_code in (302, 303), (
        f"expected redirect (flash), got {r.status_code}"
    )


# ── schedules — form + API ──────────────────────────────────────────────

def test_schedules_create_form_rejects_a_non_numeric_duration(
        base_url, admin_headers):
    r = requests.post(
        f"{base_url}/app/schedules",
        data={
            "name": f"qa0101-sched-{unique_suffix()}",
            "kind": "maintenance",
            "recurrence": "once",
            "start_at": "2026-12-31T03:00",
            "duration_seconds": "not-a-number",  # the defect
        },
        headers=admin_headers,
        allow_redirects=False,
        timeout=10,
    )
    assert r.status_code in (302, 303), (
        f"expected redirect (flash), got {r.status_code}"
    )


def test_schedules_create_api_rejects_a_non_numeric_power_off_seconds(
        base_url, admin_headers):
    r = requests.post(
        f"{base_url}/api/v1/admin/schedules",
        json={
            "name": f"qa0101-sched-api-{unique_suffix()}",
            "kind": "power_cycle",
            "recurrence": "daily",
            "at_time_utc": "03:00",
            "target": {"kind": "tag", "tag": "qa0101"},
            "power_off_seconds": "abc",  # the defect
        },
        headers=admin_headers,
        timeout=10,
    )
    # 400 with a validation_failed envelope, NOT a 500
    assert r.status_code == 400, (
        f"expected 400 validation_failed, got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body.get("ok") is False
    assert body.get("error", {}).get("code") == "validation_failed"
