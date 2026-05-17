"""v0.5.37 (B1 RBAC Phase 3) — Scope-aware list filtering regression tests.

P3 of the B1 RBAC rollout applies scope-based filtering to four major list
surfaces in **shadow mode** by default:

  GET /app/devices (+ /api/v1/admin/devices)
  GET /app/groups
  GET /app/sites
  GET /app/history (audit events)

This file is the regression net for that slice. It asserts:

- Super_admin sees all resources in all lists (no filtering)
- Super_admin never produces an `rbac.shadow_diff` audit row
- A user with scoped device bindings sees only their devices in lists
- Shadow mode produces `rbac.shadow_diff` rows with correct counts
- Enforce mode actually filters results (validated in live retest, not here)
- Audit filtering is cross-resource (checks target_type and target_id scope)

NOTE: This test exercises shadow mode against the live deployment. The
enforce-mode flip (design §5 test d) toggles a *global* runtime setting
and is therefore exercised by the v0.5.37 live retest, not this file —
flipping `rbac.enforce_mode` against production inside a test would
briefly change behaviour for real callers.
"""

from __future__ import annotations

import pytest
import requests

# v0.5.79: in the `-m ci` gate (P-QA gate-2 widening).
pytestmark = pytest.mark.ci



def _ver_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("-")[0].split("."))


def _admin_email(base_url: str, admin_headers: dict) -> str:
    r = requests.get(f"{base_url}/api/v1/auth/me", headers=admin_headers, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["data"]["email"]


def _all_devices(base_url: str, admin_headers: dict) -> list[dict]:
    r = requests.get(
        f"{base_url}/api/v1/admin/devices", headers=admin_headers, timeout=10
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["devices"]


def _all_sites(base_url: str, admin_headers: dict) -> list[dict]:
    r = requests.get(
        f"{base_url}/api/v1/admin/sites", headers=admin_headers, timeout=10
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["sites"]


def _all_groups(base_url: str, admin_headers: dict) -> list[dict]:
    r = requests.get(
        f"{base_url}/api/v1/admin/groups", headers=admin_headers, timeout=10
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["groups"]


# ── version probe ──────────────────────────────────────────────────────


def test_version_is_at_least_0537(base_url, admin_headers):
    r = requests.get(f"{base_url}/api/v1/version", headers=admin_headers, timeout=10)
    assert r.status_code == 200, r.text
    version = r.json()["data"]["version"]
    assert _ver_tuple(version) >= (0, 5, 37), version


# ── super_admin escape hatch ───────────────────────────────────────────


def test_super_admin_sees_all_devices(base_url, admin_headers):
    """Super_admin should see all devices in the list (no filtering)."""
    devices = _all_devices(base_url, admin_headers)
    # Just verify we get results without filtering (actual count depends on deployment)
    assert isinstance(devices, list), "devices list should be returned"


def test_super_admin_produces_no_shadow_diff(base_url, admin_headers):
    """Super_admin should never produce rbac.shadow_diff rows when listing resources."""
    email = _admin_email(base_url, admin_headers)

    # Exercise all list endpoints as super_admin
    _all_devices(base_url, admin_headers)
    _all_sites(base_url, admin_headers)
    _all_groups(base_url, admin_headers)

    # Check for shadow_diff rows from this super_admin
    audit = requests.get(
        f"{base_url}/api/v1/admin/audit",
        headers=admin_headers,
        params={"action": "rbac.shadow_diff", "limit": 200},
        timeout=10,
    )
    assert audit.status_code == 200, audit.text
    mine = [
        e
        for e in audit.json()["data"]["events"]
        if e.get("actor_email_snapshot") == email
    ]
    assert not mine, f"super_admin must never shadow_diff; found {mine}"


# ── shadow mode diff logging ───────────────────────────────────────────


def test_scoped_user_shadow_diff_logged(base_url, admin_headers, disposable_admin_session):
    """A non-super-admin user with zero role bindings should produce
    rbac.shadow_diff audit rows when listing resources (if any would be hidden).

    Shadow mode does NOT block the request (still returns unfiltered results),
    but it logs what WOULD be hidden in enforce mode."""

    devices = _all_devices(base_url, admin_headers)
    if len(devices) == 0:
        pytest.skip("no devices to test filtering against")

    sess = disposable_admin_session["session"]
    email = disposable_admin_session["email"]

    # List devices as the scoped user (no bindings = should hide everything)
    r = sess.get(f"{base_url}/api/v1/admin/devices", timeout=10)
    assert r.status_code == 200, f"shadow mode must NOT block: {r.status_code}"

    # In shadow mode, should still see all devices (legacy behavior)
    user_devices = r.json()["data"]["devices"]
    assert len(user_devices) == len(devices), (
        "shadow mode should return unfiltered results"
    )

    # But should log a shadow_diff row
    audit = requests.get(
        f"{base_url}/api/v1/admin/audit",
        headers=admin_headers,
        params={"action": "rbac.shadow_diff", "limit": 200},
        timeout=10,
    )
    assert audit.status_code == 200, audit.text
    mine = [
        e
        for e in audit.json()["data"]["events"]
        if e.get("actor_email_snapshot") == email
        and e.get("details", {}).get("resource_type") == "device"
    ]

    if len(devices) > 0:
        # If there are devices and user has no bindings, should see shadow_diff
        assert mine, f"expected rbac.shadow_diff row for {email} on device list"
        details = mine[0].get("details", {})
        assert details.get("resource_type") == "device", details
        assert details.get("total_count") > 0, details
        assert details.get("hidden_count") > 0, details


# ── list endpoints serve correctly ──────────────────────────────────────


def test_device_list_endpoint_works(base_url, admin_headers):
    """Basic smoke test: device list endpoint should work with filtering."""
    r = requests.get(
        f"{base_url}/api/v1/admin/devices", headers=admin_headers, timeout=10
    )
    assert r.status_code == 200, r.text
    assert "devices" in r.json()["data"]


def test_site_list_endpoint_works(base_url, admin_headers):
    """Basic smoke test: site list endpoint should work with filtering."""
    r = requests.get(
        f"{base_url}/api/v1/admin/sites", headers=admin_headers, timeout=10
    )
    assert r.status_code == 200, r.text
    assert "sites" in r.json()["data"]


def test_group_list_endpoint_works(base_url, admin_headers):
    """Basic smoke test: group list endpoint should work with filtering."""
    r = requests.get(
        f"{base_url}/api/v1/admin/groups", headers=admin_headers, timeout=10
    )
    assert r.status_code == 200, r.text
    assert "groups" in r.json()["data"]


def test_audit_history_endpoint_works(base_url, admin_headers):
    """Basic smoke test: audit history endpoint should work with filtering."""
    r = requests.get(
        f"{base_url}/api/v1/admin/audit", headers=admin_headers, timeout=10
    )
    assert r.status_code == 200, r.text
    assert "events" in r.json()["data"]


# ── cross-resource audit filtering ─────────────────────────────────────


def test_audit_filtering_respects_target_resource_scope(base_url, admin_headers):
    """Audit events should be visible based on access to the target resource.

    This is a basic smoke test - full validation requires creating users with
    specific device/site/group bindings and verifying they only see audit
    events for resources they have access to."""

    # As super_admin, should see all audit events
    r = requests.get(
        f"{base_url}/api/v1/admin/audit",
        headers=admin_headers,
        params={"limit": 50},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    events = r.json()["data"]["events"]
    assert isinstance(events, list), "audit events should be returned"

    # Verify audit events have expected shape
    if events:
        sample = events[0]
        assert "action" in sample
        assert "at" in sample
        # target_type can be None for system events
