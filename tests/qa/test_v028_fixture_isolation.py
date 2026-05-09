"""v0.2.8 — first-class QA-fixture isolation.

Devices created by the QA test suite are now auto-tagged
`is_qa_fixture = true` and the admin devices view exposes a
"show QA fixtures" toggle. In v0.2.8 the default is *show*, so this
suite asserts (a) every QA-suite-created device carries the flag,
(b) the toggle hides them when explicitly set, and (c) the row badge
renders.

v0.2.9 will flip the default to *hide*; this test file should still
pass when that happens — assertions are written against the explicit
toggle URL, not against the default behaviour.
"""

from __future__ import annotations

import requests

from .conftest import unique_suffix


def _enroll_device(base_url, admin_headers, hint: str) -> dict:
    et = requests.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        headers=admin_headers,
        json={"display_name_hint": hint, "note": "qa-fixture-isolation"},
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


def _delete(base_url, admin_headers, dev_id: str) -> None:
    requests.delete(
        f"{base_url}/api/v1/admin/devices/{dev_id}",
        headers=admin_headers,
        timeout=10,
    )


# ── API contract ──────────────────────────────────────────────────────────

def test_qa_prefixed_device_auto_tagged_as_fixture(base_url, admin_headers):
    """A device registered via the QA flow with a `QA …` display-name
    auto-acquires `is_qa_fixture = true` without any explicit flag."""
    reg = _enroll_device(base_url, admin_headers, f"QA fixture {unique_suffix()}")
    try:
        # show_qa_fixtures=1 to ensure we can see it for verification.
        devs = requests.get(
            f"{base_url}/api/v1/admin/devices?show_qa_fixtures=1",
            headers=admin_headers,
            timeout=10,
        ).json()["data"]["devices"]
        row = next((d for d in devs if d["id"] == reg["device_id"]), None)
        assert row is not None
        assert row["is_qa_fixture"] is True, row
    finally:
        _delete(base_url, admin_headers, reg["device_id"])


def test_devices_serializer_always_returns_is_qa_fixture(base_url, admin_headers):
    """Every device row must include `is_qa_fixture` for back-compat-safe
    mobile-app + hub-helper consumers."""
    devs = requests.get(
        f"{base_url}/api/v1/admin/devices?show_qa_fixtures=1",
        headers=admin_headers,
        timeout=10,
    ).json()["data"]["devices"]
    for d in devs:
        assert "is_qa_fixture" in d, f"missing is_qa_fixture on {d['id']}"
        assert isinstance(d["is_qa_fixture"], bool)


def test_show_qa_fixtures_zero_hides_fixtures(base_url, admin_headers):
    """When the toggle is explicitly off, fixture-tagged devices are
    excluded from the admin devices list."""
    reg = _enroll_device(base_url, admin_headers, f"QA hide-test {unique_suffix()}")
    try:
        # Explicit hide: ?show_qa_fixtures=0
        devs_hidden = requests.get(
            f"{base_url}/api/v1/admin/devices?show_qa_fixtures=0",
            headers=admin_headers,
            timeout=10,
        ).json()["data"]["devices"]
        assert not any(d["id"] == reg["device_id"] for d in devs_hidden), (
            "fixture device leaked through the show_qa_fixtures=0 filter"
        )

        # Explicit show: ?show_qa_fixtures=1
        devs_shown = requests.get(
            f"{base_url}/api/v1/admin/devices?show_qa_fixtures=1",
            headers=admin_headers,
            timeout=10,
        ).json()["data"]["devices"]
        assert any(d["id"] == reg["device_id"] for d in devs_shown), (
            "fixture device should be visible with show_qa_fixtures=1"
        )
    finally:
        _delete(base_url, admin_headers, reg["device_id"])


# ── UI rendering ──────────────────────────────────────────────────────────

def test_devices_list_renders_qa_badge_on_fixture_row(
    logged_in_page, base_url, admin_headers
):
    """A fixture-tagged device shows a small `QA` badge next to its name
    on the devices list page."""
    reg = _enroll_device(base_url, admin_headers, f"QA UI-badge {unique_suffix()}")
    try:
        page = logged_in_page
        page.goto(f"{base_url}/app/devices?show_qa_fixtures=1")
        page.wait_for_load_state("networkidle")
        body = page.content()

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(body, "html.parser")
        rows = soup.find_all("tr")
        match = None
        for tr in rows:
            link = tr.find("a", href=True)
            if link and reg["device_id"] in link["href"]:
                match = tr
                break
        assert match is not None, "fixture row missing from devices list"
        text = match.get_text(" ", strip=True)
        assert "QA" in text, f"expected QA badge in row text, got: {text!r}"
    finally:
        _delete(base_url, admin_headers, reg["device_id"])


def test_devices_list_show_qa_fixtures_zero_hides_row_in_html(
    logged_in_page, base_url, admin_headers
):
    """When the operator toggles QA-fixtures off, the fixture row is
    absent from the rendered devices-list HTML."""
    reg = _enroll_device(base_url, admin_headers, f"QA UI-hide {unique_suffix()}")
    try:
        page = logged_in_page
        page.goto(f"{base_url}/app/devices?show_qa_fixtures=0")
        page.wait_for_load_state("networkidle")
        body = page.content()
        assert reg["device_id"] not in body, (
            "fixture device id leaked into HTML with show_qa_fixtures=0"
        )
    finally:
        _delete(base_url, admin_headers, reg["device_id"])


def test_explicit_qa_fixture_flag_in_register_payload(base_url, admin_headers):
    """An operator-friendly display_name that does NOT match the prefix
    auto-detect can still be tagged by sending `qa_fixture: true`."""
    et = requests.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        headers=admin_headers,
        json={"display_name_hint": "operator named me", "note": "qa-explicit-flag"},
        timeout=10,
    ).json()["data"]["enrollment_token"]
    reg = requests.post(
        f"{base_url}/api/v1/device/register",
        json={
            "enrollment_token": et,
            "hardware_model": "sonoff_s31",
            "firmware_version": "0.1.0-qa",
            "display_name": f"operator named me {unique_suffix()}",
            "qa_fixture": True,
        },
        timeout=10,
    )
    assert reg.status_code == 201, reg.text
    dev_id = reg.json()["data"]["device_id"]
    try:
        devs = requests.get(
            f"{base_url}/api/v1/admin/devices?show_qa_fixtures=1",
            headers=admin_headers,
            timeout=10,
        ).json()["data"]["devices"]
        row = next((d for d in devs if d["id"] == dev_id), None)
        assert row is not None
        assert row["is_qa_fixture"] is True, (
            f"explicit qa_fixture: true was not honoured: {row}"
        )
    finally:
        _delete(base_url, admin_headers, dev_id)
