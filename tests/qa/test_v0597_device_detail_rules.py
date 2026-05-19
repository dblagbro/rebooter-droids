"""v0.5.97 — device-detail Watchdog / Schedule sections.

The device-detail page's Watchdog and Schedule sections were
unconditional empty-state stubs ("…ship in P4") that never queried the
backend. They now list the watchdog rules and schedules whose target
resolves to the device — directly, or through a group it belongs to.

Verified against a live instance. Auth: Bearer headers (the `-m ci`
gate runs over `http://localhost`, no Secure cookie). Runs in `-m ci`.
"""

from __future__ import annotations

import pytest
import requests

from .conftest import unique_suffix

pytestmark = pytest.mark.ci


def _register_device(base_url, admin_headers):
    """Mint an enrollment token, register a device, return its id."""
    et = requests.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        headers=admin_headers,
        json={"display_name_hint": f"QA {unique_suffix()}", "note": "qa0597"},
        timeout=10,
    ).json()["data"]["enrollment_token"]
    reg = requests.post(
        f"{base_url}/api/v1/device/register",
        json={
            "enrollment_token": et,
            "hardware_model": "sonoff_s31",
            "firmware_version": "0.1.0",
            "display_name": f"QA0597 {unique_suffix()}",
        },
        timeout=10,
    )
    assert reg.status_code == 201, reg.text
    return reg.json()["data"]["device_id"]


def _create_rule(base_url, admin_headers, name, target):
    r = requests.post(
        f"{base_url}/api/v1/admin/rules",
        json={"name": name, "probe": {"kind": "internet"},
              "target": target, "action": {"kind": "notify_only"}},
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _device_detail(base_url, admin_headers, device_id):
    r = requests.get(f"{base_url}/app/devices/{device_id}",
                     headers=admin_headers, timeout=10)
    assert r.status_code == 200, r.text
    return r.text


def test_device_detail_lists_a_direct_device_targeted_rule(base_url, admin_headers):
    device_id = _register_device(base_url, admin_headers)
    name = f"qa0597-direct-{unique_suffix()}"
    rule_id = _create_rule(base_url, admin_headers, name,
                           {"kind": "device", "id": device_id})
    try:
        body = _device_detail(base_url, admin_headers, device_id)
        # the rule is listed, and the stale stub copy is gone
        assert name in body
        assert "watchdogs ship in P4" not in body
        assert "ship in P4" not in body
    finally:
        requests.delete(f"{base_url}/api/v1/admin/rules/{rule_id}",
                        headers=admin_headers, timeout=10)
        requests.delete(f"{base_url}/api/v1/admin/devices/{device_id}",
                        headers=admin_headers, timeout=10)


def test_device_detail_lists_a_group_targeted_rule(base_url, admin_headers):
    """A rule targeting a group shows on the detail page of every
    device in that group — the page resolves group membership."""
    device_id = _register_device(base_url, admin_headers)
    group = requests.post(
        f"{base_url}/api/v1/admin/groups",
        json={"name": f"qa0597-grp-{unique_suffix()}"},
        headers=admin_headers, timeout=10,
    )
    assert group.status_code == 201, group.text
    group_id = group.json()["data"]["id"]
    added = requests.post(
        f"{base_url}/api/v1/admin/groups/{group_id}/members",
        json={"device_ids": [device_id]},
        headers=admin_headers, timeout=10,
    )
    assert added.status_code == 200, added.text
    name = f"qa0597-group-rule-{unique_suffix()}"
    rule_id = _create_rule(base_url, admin_headers, name,
                           {"kind": "group", "id": group_id})
    try:
        body = _device_detail(base_url, admin_headers, device_id)
        assert name in body, "group-targeted rule must list on a member's page"
    finally:
        requests.delete(f"{base_url}/api/v1/admin/rules/{rule_id}",
                        headers=admin_headers, timeout=10)
        requests.delete(f"{base_url}/api/v1/admin/groups/{group_id}",
                        headers=admin_headers, timeout=10)
        requests.delete(f"{base_url}/api/v1/admin/devices/{device_id}",
                        headers=admin_headers, timeout=10)


def test_device_detail_shows_empty_state_when_nothing_targets_it(base_url, admin_headers):
    device_id = _register_device(base_url, admin_headers)
    try:
        body = _device_detail(base_url, admin_headers, device_id)
        assert "No watchdog rules targeting this device" in body
        assert "No schedules targeting this device" in body
        # the empty state must not carry the old "ship in P4" copy
        assert "ship in P4" not in body
    finally:
        requests.delete(f"{base_url}/api/v1/admin/devices/{device_id}",
                        headers=admin_headers, timeout=10)


def test_device_detail_lists_a_device_targeted_schedule(base_url, admin_headers):
    device_id = _register_device(base_url, admin_headers)
    name = f"qa0597-sched-{unique_suffix()}"
    sched = requests.post(
        f"{base_url}/api/v1/admin/schedules",
        json={"name": name, "kind": "power_cycle", "recurrence": "daily",
              "at_time_utc": "03:00",
              "target": {"kind": "device", "id": device_id}},
        headers=admin_headers, timeout=10,
    )
    assert sched.status_code == 201, sched.text
    sched_id = sched.json()["data"]["id"]
    try:
        body = _device_detail(base_url, admin_headers, device_id)
        assert name in body
    finally:
        requests.delete(f"{base_url}/api/v1/admin/schedules/{sched_id}",
                        headers=admin_headers, timeout=10)
        requests.delete(f"{base_url}/api/v1/admin/devices/{device_id}",
                        headers=admin_headers, timeout=10)
