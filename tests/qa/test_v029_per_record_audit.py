"""v0.2.9 — per-record audit slice on device-detail + group-detail.

A device's recent audit events are now embedded directly in its detail
page (and the API equivalent), so an operator can ask "what happened
to this device this week" without leaving the device page.

Tests cover (a) the existing API endpoint accepts target_type +
target_id and returns only matching rows, (b) the device-detail
service returns an audit_history field, (c) the device-detail HTML
renders an Audit history section that includes a link into the global
audit page pre-filtered to this device, (d) the same for groups.
"""

from __future__ import annotations

import requests

from .conftest import unique_suffix
import pytest

# v0.5.79: in the `-m ci` gate (P-QA gate-2 widening).
pytestmark = pytest.mark.ci



def _enroll_device(base_url, admin_headers, hint: str) -> dict:
    et = requests.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        headers=admin_headers,
        json={"display_name_hint": hint, "note": "qa-audit-slice"},
        timeout=10,
    ).json()["data"]["enrollment_token"]
    reg = requests.post(
        f"{base_url}/api/v1/device/register",
        json={
            "enrollment_token": et,
            "hardware_model": "sonoff_s31",
            "firmware_version": "0.1.0-qa",
            "display_name": hint,
        },
        timeout=10,
    )
    assert reg.status_code == 201, reg.text
    return reg.json()["data"]


def _delete_device(base_url, admin_headers, dev_id: str) -> None:
    requests.delete(
        f"{base_url}/api/v1/admin/devices/{dev_id}",
        headers=admin_headers,
        timeout=10,
    )


# ── API ────────────────────────────────────────────────────────────────────

def test_audit_api_filters_by_target_type_and_target_id(base_url, admin_headers):
    """`GET /api/v1/admin/audit?target_type=device&target_id=<id>` returns
    only rows that match that target tuple. We create a device, mutate
    it (so the audit log gains a `device.updated` row), then confirm the
    filter narrows correctly."""
    reg = _enroll_device(base_url, admin_headers, f"QA audit-filter {unique_suffix()}")
    try:
        # Mutate the device so an audit row is recorded.
        r = requests.patch(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            headers=admin_headers,
            json={"notes": "audit slice probe"},
            timeout=10,
        )
        assert r.status_code in (200, 204), r.text

        rows = requests.get(
            f"{base_url}/api/v1/admin/audit"
            f"?target_type=device&target_id={reg['device_id']}",
            headers=admin_headers,
            timeout=10,
        ).json()["data"]["events"]
        # Every row must target THIS device — that's the point of the slice.
        assert rows, "expected at least one audit row for the mutated device"
        for r in rows:
            assert r["target_type"] == "device"
            assert r["target_id"] == reg["device_id"]
        actions = {r["action"] for r in rows}
        assert "device.updated" in actions
    finally:
        _delete_device(base_url, admin_headers, reg["device_id"])


def test_device_detail_returns_audit_history(base_url, admin_headers):
    """The single-device admin API endpoint returns an `audit_history`
    list alongside the existing latest_heartbeat / pending_commands /
    recent_events / groups fields."""
    reg = _enroll_device(base_url, admin_headers, f"QA audit-history {unique_suffix()}")
    try:
        # Mutate so there's something to audit.
        requests.patch(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            headers=admin_headers,
            json={"notes": "history probe"},
            timeout=10,
        )
        detail = requests.get(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            headers=admin_headers,
            timeout=10,
        ).json()["data"]
        assert "audit_history" in detail, detail
        assert isinstance(detail["audit_history"], list)
        assert any(a["action"] == "device.updated" for a in detail["audit_history"])
    finally:
        _delete_device(base_url, admin_headers, reg["device_id"])


# ── UI ────────────────────────────────────────────────────────────────────

def test_device_detail_renders_audit_history_section(
    logged_in_page, base_url, admin_headers
):
    """The device-detail page contains an "Audit history" heading and a
    deep link into the global audit page pre-filtered to this device."""
    reg = _enroll_device(base_url, admin_headers, f"QA UI-audit {unique_suffix()}")
    try:
        # Mutate so the section actually has rows to render.
        requests.patch(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            headers=admin_headers,
            json={"notes": "UI audit probe"},
            timeout=10,
        )
        page = logged_in_page
        page.goto(f"{base_url}/app/devices/{reg['device_id']}")
        page.wait_for_load_state("networkidle")
        body = page.content()
        assert "Audit history" in body, "device-detail page missing Audit history heading"
        # The deep link to the filtered audit page must be present.
        assert (
            f"target_type=device&amp;target_id={reg['device_id']}" in body
            or f"target_type=device&target_id={reg['device_id']}" in body
        ), "device-detail page is missing the 'Full audit history' deep link"
    finally:
        _delete_device(base_url, admin_headers, reg["device_id"])


def test_audit_page_honours_target_id_query_param(logged_in_page, base_url, admin_headers):
    """The /app/audit page now respects `target_id`; previously it parsed
    every other filter except this one."""
    reg = _enroll_device(base_url, admin_headers, f"QA audit-target {unique_suffix()}")
    try:
        requests.patch(
            f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
            headers=admin_headers,
            json={"notes": "filter probe"},
            timeout=10,
        )
        page = logged_in_page
        page.goto(
            f"{base_url}/app/audit"
            f"?target_type=device&target_id={reg['device_id']}"
        )
        page.wait_for_load_state("networkidle")
        body = page.content()
        # The filtered page must render this device id (since we just
        # mutated it) and must NOT render unrelated audit rows for a
        # different target. We check it shows up at all.
        assert reg["device_id"] in body, (
            "audit page filter did not include the mutated device's id"
        )
    finally:
        _delete_device(base_url, admin_headers, reg["device_id"])


def test_group_detail_returns_audit_history_field(base_url, admin_headers):
    """Same per-record audit slice on group-detail for symmetry. Group
    creation isn't audited today (separate gap), so the freshly-created
    group's audit_history is empty — but the field MUST be present on
    the response, and every row that is present MUST target this group."""
    grp = requests.post(
        f"{base_url}/api/v1/admin/groups",
        headers=admin_headers,
        json={"name": f"QA audit grp {unique_suffix()}"},
        timeout=10,
    )
    assert grp.status_code in (200, 201), grp.text
    gid = grp.json()["data"]["id"]
    try:
        detail = requests.get(
            f"{base_url}/api/v1/admin/groups/{gid}",
            headers=admin_headers,
            timeout=10,
        ).json()["data"]
        assert "audit_history" in detail, detail
        assert isinstance(detail["audit_history"], list)
        for row in detail["audit_history"]:
            assert row["target_type"] == "group"
            assert row["target_id"] == gid
    finally:
        requests.delete(
            f"{base_url}/api/v1/admin/groups/{gid}",
            headers=admin_headers,
            timeout=10,
        )
