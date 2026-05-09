"""v0.3.5 — bulk-delete dual-checkbox regression fix.

Operator hit a bug in v0.3.4: master-select-all then unchecking
the non-target rows still deleted ALL of them, including the
unchecked ones. Root cause was that the devices list renders the
same row in two layouts (desktop table + mobile cards), each with
its own `name="device_id"` checkbox; the master toggle checked
both copies, and unchecking only the visible one left the hidden
pair checked and submitted.

The fix has two parts:
1. Frontend: `static/js/bulk_select.js` syncs paired checkboxes by
   `name + value`. (UI test deferred to a Playwright pass; this
   bucket asserts the server-side defense.)
2. Server: every bulk handler dedupes its incoming id list, so
   even a future regression that re-introduces dual submissions
   doesn't inflate counts or skew audit details.

These tests assert the server-side dedupe.
"""

from __future__ import annotations

import pytest
import requests

from .conftest import unique_suffix


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
        json={"display_name_hint": hint, "note": "qa-bulk-dedupe"},
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


def test_bulk_delete_api_dedupes_duplicate_ids(base_url, shell_session):
    """If a client sends device_ids=[A, A, B], the response's
    `deleted` list MUST contain each id at most once. Pre-v0.3.5
    a duplicate would either delete-then-skip-as-unknown or
    inflate counts in the audit row."""
    a = _enroll_device(shell_session, base_url, f"QA dedupe-A {unique_suffix()}")
    b = _enroll_device(shell_session, base_url, f"QA dedupe-B {unique_suffix()}")
    try:
        r = shell_session.post(
            f"{base_url}/api/v1/admin/devices/bulk-delete",
            json={"device_ids": [a, a, b, b]},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        result = r.json()["data"]
        assert sorted(result["deleted"]) == sorted([a, b]), result
        # No id should appear in skipped_unknown (the dedupe means
        # we only attempt each delete once, so the second copy isn't
        # a not-found).
        assert result["skipped_unknown"] == [], result
    finally:
        for did in (a, b):
            shell_session.delete(
                f"{base_url}/api/v1/admin/devices/{did}", timeout=10
            )


def test_bulk_delete_audit_count_matches_unique_ids(base_url, shell_session):
    """The audit row's deleted_count must equal the number of unique
    ids actually deleted, not the number of form rows submitted."""
    a = _enroll_device(shell_session, base_url, f"QA dedupe-audit-A {unique_suffix()}")
    b = _enroll_device(shell_session, base_url, f"QA dedupe-audit-B {unique_suffix()}")
    try:
        shell_session.post(
            f"{base_url}/api/v1/admin/devices/bulk-delete",
            json={"device_ids": [a, b, a]},
            timeout=10,
        )
        rows = shell_session.get(
            f"{base_url}/api/v1/admin/audit?action=device.bulk_deleted&limit=3",
            timeout=10,
        ).json()["data"]["events"]
        assert rows
        # Most-recent audit row should report 2 deleted (not 3).
        assert rows[0]["details"]["deleted_count"] == 2, rows[0]["details"]
    finally:
        for did in (a, b):
            shell_session.delete(
                f"{base_url}/api/v1/admin/devices/{did}", timeout=10
            )
