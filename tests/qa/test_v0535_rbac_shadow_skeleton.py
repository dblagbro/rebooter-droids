"""v0.5.35 (B1 RBAC Phase 1) — RBAC shadow-mode skeleton regression tests.

P1 of the B1 RBAC rollout wires the scope-check primitives onto two
demonstrator routes in **shadow mode**:

  GET  /api/v1/admin/devices/<id>            (read  proof — ROLE_VIEWER)
  POST /api/v1/admin/devices/<id>/commands   (write proof — ROLE_OPERATOR)

This file is the regression net for that slice. It asserts:

- the scope decorator is wired and does NOT break the demonstrator
  routes for the super_admin (who is always exempt — RFC-003 §9.0);
- a non-super-admin user with zero role bindings is *shadow-logged*
  (`rbac.shadow_deny`) but NOT blocked — shadow mode is additive, the
  legacy `role_required_*` decorator stays authoritative;
- super_admin never produces an `rbac.shadow_deny` row;
- per-resource mutations route through `record_scoped()` — the command
  audit row now carries a `scope_claim`;
- legacy auth is unchanged.

NOTE on the no-binding subject: the one-shot RBAC backfill
(`bootstrap.ensure_role_bindings_backfill`) ran once at deploy time.
Every user *created afterwards* — including the `disposable_admin_session`
fixture user — has zero rows in `role_bindings` regardless of legacy
`users.role`. That makes the disposable user a faithful non-super-admin
scope-miss subject without needing to hand-craft bindings.

The enforce-mode flip (design §5 test d) toggles a *global* runtime
setting and is therefore exercised by the v0.5.35 live retest, not this
black-box file — flipping `rbac.enforce_mode` against live production
inside a test would briefly change behaviour for real callers.
"""

from __future__ import annotations

import pytest
import requests


def _ver_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("-")[0].split("."))


def _a_device_id(base_url: str, admin_headers: dict) -> str:
    r = requests.get(
        f"{base_url}/api/v1/admin/devices", headers=admin_headers, timeout=10
    )
    assert r.status_code == 200, r.text
    devices = r.json()["data"]["devices"]
    assert devices, "need at least one device to exercise the scope decorator"
    return devices[0]["id"]


def _a_qa_fixture_device_id(base_url: str, admin_headers: dict) -> str | None:
    r = requests.get(
        f"{base_url}/api/v1/admin/devices",
        headers=admin_headers,
        params={"show_qa_fixtures": "true"},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    fixtures = [d for d in r.json()["data"]["devices"] if d.get("is_qa_fixture")]
    return fixtures[0]["id"] if fixtures else None


def _admin_email(base_url: str, admin_headers: dict) -> str:
    r = requests.get(f"{base_url}/api/v1/auth/me", headers=admin_headers, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["data"]["email"]


# ── version probe ──────────────────────────────────────────────────────


def test_version_is_at_least_0535(base_url, admin_headers):
    r = requests.get(f"{base_url}/api/v1/version", headers=admin_headers, timeout=10)
    assert r.status_code == 200, r.text
    version = r.json()["data"]["version"]
    assert _ver_tuple(version) >= (0, 5, 35), version


# ── super_admin escape hatch ───────────────────────────────────────────


def test_get_device_scope_decorator_serves_in_scope_caller(base_url, admin_headers):
    """GET /devices/<id> is wired through scope_required_api. The
    super_admin is exempt, so the route must still 200 unchanged."""
    device_id = _a_device_id(base_url, admin_headers)
    r = requests.get(
        f"{base_url}/api/v1/admin/devices/{device_id}",
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["id"] == device_id


def test_super_admin_produces_no_shadow_deny(base_url, admin_headers):
    """RFC-003 §9.0 super_admin escape hatch: hitting a scope-decorated
    route as super_admin must never emit an `rbac.shadow_deny` row."""
    device_id = _a_device_id(base_url, admin_headers)
    email = _admin_email(base_url, admin_headers)
    # Exercise the wired route as super_admin.
    requests.get(
        f"{base_url}/api/v1/admin/devices/{device_id}",
        headers=admin_headers,
        timeout=10,
    )
    audit = requests.get(
        f"{base_url}/api/v1/admin/audit",
        headers=admin_headers,
        params={"action": "rbac.shadow_deny", "limit": 200},
        timeout=10,
    )
    assert audit.status_code == 200, audit.text
    mine = [
        e
        for e in audit.json()["data"]["events"]
        if e.get("actor_email_snapshot") == email
    ]
    assert not mine, f"super_admin must never shadow-deny; found {mine}"


# ── shadow mode logs but does not block ────────────────────────────────


def test_no_binding_user_is_shadow_logged_not_blocked(
    base_url, admin_headers, disposable_admin_session
):
    """A non-super-admin user with zero role bindings hits the
    scope-decorated GET /devices/<id>. Shadow mode must NOT block the
    request (still 200), but it must emit an `rbac.shadow_deny` audit
    row naming that user + device."""
    device_id = _a_device_id(base_url, admin_headers)
    sess = disposable_admin_session["session"]
    email = disposable_admin_session["email"]

    r = sess.get(f"{base_url}/api/v1/admin/devices/{device_id}", timeout=10)
    assert r.status_code == 200, (
        f"shadow mode must NOT block: {r.status_code} {r.text[:200]}"
    )

    audit = requests.get(
        f"{base_url}/api/v1/admin/audit",
        headers=admin_headers,
        params={"action": "rbac.shadow_deny", "target_id": device_id, "limit": 200},
        timeout=10,
    )
    assert audit.status_code == 200, audit.text
    mine = [
        e
        for e in audit.json()["data"]["events"]
        if e.get("actor_email_snapshot") == email
    ]
    assert mine, (
        f"expected an rbac.shadow_deny row for {email} on device {device_id}"
    )
    details = mine[0].get("details", {})
    assert details.get("enforce_mode") == "shadow", details
    assert details.get("scope_type") == "device", details
    assert details.get("role_needed") == "viewer", details
    assert details.get("reason") == "out_of_scope", details


# ── record_scoped() choke-point ────────────────────────────────────────


def test_command_audit_row_carries_scope_claim(base_url, admin_headers):
    """v0.5.35: POST /devices/<id>/commands routes its audit row through
    `record_scoped()`, so the `device.command_issued` row carries a
    `scope_claim`. Exercised against a QA fixture device with a benign
    `check_firmware` command (no power action)."""
    device_id = _a_qa_fixture_device_id(base_url, admin_headers)
    if device_id is None:
        pytest.skip("no QA fixture device available to exercise the command path")

    r = requests.post(
        f"{base_url}/api/v1/admin/devices/{device_id}/commands",
        headers={**admin_headers, "Content-Type": "application/json"},
        json={"type": "check_firmware"},
        timeout=10,
    )
    assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
    command_id = r.json()["data"]["command_id"]
    # Best-effort: cancel the queued command so it doesn't sit pending.
    requests.post(
        f"{base_url}/api/v1/admin/devices/{device_id}/commands/{command_id}/cancel",
        headers=admin_headers,
        timeout=10,
    )

    audit = requests.get(
        f"{base_url}/api/v1/admin/audit",
        headers=admin_headers,
        params={
            "action": "device.command_issued",
            "target_id": device_id,
            "limit": 50,
        },
        timeout=10,
    )
    assert audit.status_code == 200, audit.text
    match = [
        e
        for e in audit.json()["data"]["events"]
        if e.get("details", {}).get("command_id") == command_id
    ]
    assert match, f"no device.command_issued audit row for command {command_id}"
    claim = match[0]["details"].get("scope_claim")
    assert claim == {"scope_type": "device", "scope_id": device_id}, (
        f"record_scoped() must attach the scope claim; got {claim!r}"
    )


# ── legacy auth unchanged ──────────────────────────────────────────────


def test_legacy_auth_paths_still_work(base_url, admin_headers):
    """Shadow mode is additive — the legacy decorator-based auth on the
    admin API must keep 200-ing exactly as on v0.5.34."""
    for path in (
        "/api/v1/admin/devices",
        "/api/v1/admin/firmware/releases",
        "/api/v1/admin/audit?limit=5",
    ):
        r = requests.get(f"{base_url}{path}", headers=admin_headers, timeout=10)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"
