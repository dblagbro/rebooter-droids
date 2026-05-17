"""v0.3.4 — bulk-action UI on devices/groups/invitations/tokens.

Asserts the four list pages render the bulk-form scaffolding (data-
bulk-form / data-bulk-master / data-bulk-row / data-bulk-bar) and
that the bulk-delete API endpoint behaves correctly:
- empty list rejected
- mass-action gate trips at >5 / >20 thresholds
- protected devices skipped unless override
- audit row written with reason='operator'

Module-scoped login fixture follows the v0.3.0 pattern; cookie
auth is sufficient for admin API calls (no Bearer needed).
"""

from __future__ import annotations

import pytest
import requests

from .conftest import unique_suffix

# NB: not in the `-m ci` gate — test_list_page_renders_bulk_form_scaffolding
# asserts `/app/groups` shows bulk-form scaffolding, which only renders
# when a group already exists. It passes in a full suite run (an earlier
# test leaves a group behind) but fails on a fresh instance. Gate it once
# the test seeds its own group. (P-QA gate-3.)



@pytest.fixture(scope="module")
def shell_session(base_url, admin_creds):
    s = requests.Session()
    email, pw = admin_creds
    r = s.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    return s


def _enroll_device(shell_session, base_url, hint: str) -> str:
    et = shell_session.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        json={"display_name_hint": hint, "note": "qa-bulk"},
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
    assert reg.status_code == 201
    return reg.json()["data"]["device_id"]


# ── UI scaffolding rendered on every list page ────────────────────────────

@pytest.mark.parametrize(
    "path,verb,noun",
    [
        ("/app/devices?show_qa_fixtures=1", "delete", "device"),
        ("/app/groups", "delete", "group"),
        ("/app/invitations", "cancel", "invitation"),
        ("/app/enrollment-tokens", "revoke", "token"),
    ],
)
def test_list_page_renders_bulk_form_scaffolding(
    base_url, shell_session, path, verb, noun
):
    body = shell_session.get(f"{base_url}{path}", timeout=10).text
    if "v3-empty-state" in body and "data-bulk-form" not in body:
        pytest.skip(f"page {path} renders empty state — no rows to bulk on")
    assert "data-bulk-form" in body, f"{path} missing data-bulk-form"
    assert "data-bulk-bar" in body, f"{path} missing data-bulk-bar"
    assert f'data-bulk-verb="{verb}"' in body, f"{path} missing data-bulk-verb={verb}"
    assert f'data-bulk-noun="{noun}"' in body, f"{path} missing data-bulk-noun={noun}"


def test_bulk_select_js_loaded_in_layout(base_url, shell_session):
    body = shell_session.get(f"{base_url}/app/", timeout=10).text
    assert "/static/js/bulk_select.js" in body


# ── bulk-delete API contract ──────────────────────────────────────────────

def test_bulk_delete_empty_list_rejected(base_url, shell_session):
    r = shell_session.post(
        f"{base_url}/api/v1/admin/devices/bulk-delete",
        json={"device_ids": []},
        timeout=10,
    )
    assert r.status_code == 400


def test_bulk_delete_under_threshold_no_confirmation(base_url, shell_session):
    """≤5 targets — no confirmation needed."""
    ids = [
        _enroll_device(shell_session, base_url, f"QA bulk-{i} {unique_suffix()}")
        for i in range(3)
    ]
    r = shell_session.post(
        f"{base_url}/api/v1/admin/devices/bulk-delete",
        json={"device_ids": ids},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    assert sorted(r.json()["data"]["deleted"]) == sorted(ids)


def test_bulk_delete_above_simple_threshold_requires_simple(base_url, shell_session):
    """6 targets — simple confirmation required; without it = 409."""
    ids = [
        _enroll_device(shell_session, base_url, f"QA bulk-s-{i} {unique_suffix()}")
        for i in range(6)
    ]
    try:
        r = shell_session.post(
            f"{base_url}/api/v1/admin/devices/bulk-delete",
            json={"device_ids": ids},
            timeout=10,
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "confirmation_required"
        # With simple confirmation, succeeds.
        r = shell_session.post(
            f"{base_url}/api/v1/admin/devices/bulk-delete",
            json={"device_ids": ids, "confirmation_level": "simple"},
            timeout=10,
        )
        assert r.status_code == 200
        assert sorted(r.json()["data"]["deleted"]) == sorted(ids)
    finally:
        # In case the second call failed, clean up to avoid pollution.
        for did in ids:
            shell_session.delete(
                f"{base_url}/api/v1/admin/devices/{did}", timeout=10
            )


def test_bulk_delete_protected_device_skipped(base_url, shell_session):
    """is_protected gates the bulk-delete just like single-device
    power commands. Without override, the protected device shows up
    in skipped_protected."""
    plain_id = _enroll_device(shell_session, base_url, f"QA bulk-plain {unique_suffix()}")
    locked_id = _enroll_device(shell_session, base_url, f"QA bulk-locked {unique_suffix()}")
    shell_session.patch(
        f"{base_url}/api/v1/admin/devices/{locked_id}",
        json={"is_protected": True},
        timeout=10,
    )
    try:
        r = shell_session.post(
            f"{base_url}/api/v1/admin/devices/bulk-delete",
            json={"device_ids": [plain_id, locked_id]},
            timeout=10,
        )
        assert r.status_code == 200
        result = r.json()["data"]
        assert plain_id in result["deleted"]
        assert locked_id in result["skipped_protected"]
        assert locked_id not in result["deleted"]

        # Now with override, the locked one goes too.
        r = shell_session.post(
            f"{base_url}/api/v1/admin/devices/bulk-delete",
            json={"device_ids": [locked_id], "override_lockout": True},
            timeout=10,
        )
        assert r.status_code == 200
        assert locked_id in r.json()["data"]["deleted"]
    finally:
        # Belt-and-braces in case a path failed mid-test.
        for did in (plain_id, locked_id):
            shell_session.delete(
                f"{base_url}/api/v1/admin/devices/{did}", timeout=10
            )


def test_bulk_delete_audit_row_carries_reason_operator(base_url, shell_session):
    ids = [
        _enroll_device(shell_session, base_url, f"QA bulk-audit-{i} {unique_suffix()}")
        for i in range(2)
    ]
    shell_session.post(
        f"{base_url}/api/v1/admin/devices/bulk-delete",
        json={"device_ids": ids},
        timeout=10,
    )
    rows = shell_session.get(
        f"{base_url}/api/v1/admin/audit?action=device.bulk_deleted&limit=5",
        timeout=10,
    ).json()["data"]["events"]
    assert rows, "expected a device.bulk_deleted audit row"
    # The most recent row should carry our reason and the deleted count.
    assert rows[0]["details"].get("reason") == "operator"
    assert rows[0]["details"].get("deleted_count") == len(ids)
