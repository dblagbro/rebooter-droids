"""v0.3.1 P2 — Status (Inbox) feed + device list/detail restructure.

Asserts the new shape:
- Status page renders the verdict banner + attention feed +
  emergency controls.
- Devices list renders saved-filter chips with URL round-trip.
- Devices list renders BOTH desktop-table layout (.v3-devices-table)
  AND mobile-card layout (.v3-device-cards) — CSS swaps which one is
  visible per breakpoint.
- Devices list renders the central-vs-local cue.
- Device detail page has a tab-strip (.v3-tabbar) with all 7
  sections and an Open-local-UI link when local_ip is set.
- /app/devices/new wizard renders + mints + shows the token once.

Module-scoped login fixture follows the v0.3.0 pattern.
"""

from __future__ import annotations

import pytest
import requests

from .conftest import unique_suffix

# v0.5.79: in the `-m ci` gate (P-QA gate-2 widening).
pytestmark = pytest.mark.ci



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




# ── Status page ────────────────────────────────────────────────────────────

def test_status_page_renders_verdict_banner(base_url, shell_session):
    s = shell_session
    body = s.get(f"{base_url}/app/", timeout=10).text
    # The verdict CSS class must appear.
    assert "v3-verdict v3-verdict-" in body, "Status page missing verdict banner"


def test_status_page_renders_emergency_controls(base_url, shell_session):
    s = shell_session
    body = s.get(f"{base_url}/app/", timeout=10).text
    assert "Manual controls" in body
    # Emergency-affordance buttons land in the actions block.
    assert "Open devices" in body
    # 0.6.24 #187 copy sweep: accept the new "Add device" CTA in addition
    # to the legacy strings. Test just needs to confirm a "go enroll one"
    # affordance exists, not the exact wording.
    assert any(s in body for s in ("Add device", "Add a device", "Enrol a device", "Enroll"))


def test_status_page_links_into_history(base_url, shell_session):
    s = shell_session
    body = s.get(f"{base_url}/app/", timeout=10).text
    assert "/app/history" in body, "Status page must link to History"


# ── Saved-filter chips ────────────────────────────────────────────────────

def test_devices_list_renders_chip_strip(base_url, shell_session):
    s = shell_session
    body = s.get(f"{base_url}/app/devices", timeout=10).text
    assert 'class="v3-chips"' in body
    # All four named chips must render.
    assert "Offline &gt; 24" in body or "Offline > 24" in body
    assert "Never heartbeated" in body
    assert "Has pending commands" in body
    assert "QA fixtures only" in body


def test_chip_url_roundtrip_marks_chip_active(base_url, shell_session):
    s = shell_session
    body = s.get(f"{base_url}/app/devices?chip=never", timeout=10).text
    # The "never" chip is active when ?chip=never is in the URL.
    assert "v3-chip-active" in body, (
        "selecting a chip must mark it active in the rendered chip strip"
    )


def test_chip_filter_excludes_devices_with_heartbeat(base_url, shell_session):
    """Sanity: if a fresh device with a heartbeat exists, ?chip=never
    should not include it. We make one to assert the contract.

    Uses shell_session's cookie auth instead of per-test admin_headers
    so the suite doesn't trip the login rate limiter."""
    et = shell_session.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        json={"display_name_hint": f"QA chip-test {unique_suffix()}", "note": "qa-chip"},
        timeout=10,
    ).json()["data"]["enrollment_token"]
    reg = requests.post(
        f"{base_url}/api/v1/device/register",
        json={
            "enrollment_token": et,
            "hardware_model": "sonoff_s31",
            "firmware_version": "0.1.0-qa",
            "display_name": f"QA chip-test {unique_suffix()}",
        },
        timeout=10,
    )
    assert reg.status_code == 201
    dev = reg.json()["data"]
    try:
        hb = requests.post(
            f"{base_url}/api/v1/device/heartbeat",
            headers={"Authorization": f"Bearer {dev['device_token']}"},
            json={
                "device_id": dev["device_id"],
                "firmware_version": "0.1.0-qa",
                "mode": "smart_plug",
                "relay_on": True,
                "wifi_connected": True,
                "health_state": "healthy",
                "uptime_seconds": 60,
            },
            timeout=10,
        )
        assert hb.status_code == 200

        api_body = shell_session.get(
            f"{base_url}/api/v1/admin/devices?chip=never&show_qa_fixtures=1",
            timeout=10,
        ).json()["data"]
        ids = {d["id"] for d in api_body["devices"]}
        assert dev["device_id"] not in ids, (
            "device with a heartbeat should not match chip=never"
        )
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/devices/{dev['device_id']}",
            timeout=10,
        )


def test_chips_compose_with_AND_semantics(base_url, shell_session):
    """Two chips on the URL must AND-compose. Asserting via the
    device-list page render — both chips visible as active."""
    s = shell_session
    body = s.get(
        f"{base_url}/app/devices?chip=never&chip=qa_fixtures", timeout=10
    ).text
    # Both chips marked active.
    assert body.count("v3-chip-active") >= 2, (
        "two chips must both be marked active when both are on the URL"
    )


# ── Mobile vs. desktop layout markup ───────────────────────────────────────

def test_devices_list_renders_both_layouts_in_dom(base_url, shell_session):
    """Both layouts are always in the DOM; CSS swaps which one is
    visible per breakpoint. (R-DEV-3 mobile-card + desktop-table.)
    The empty-state replaces both wrappers when fleet=0; create a
    fixture device so this assertion is meaningful."""
    s = shell_session
    et = s.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        json={"display_name_hint": f"QA layout {unique_suffix()}", "note": "qa-v031-layout"},
        timeout=10,
    ).json()["data"]["enrollment_token"]
    reg = requests.post(
        f"{base_url}/api/v1/device/register",
        json={
            "enrollment_token": et,
            "hardware_model": "sonoff_s31",
            "firmware_version": "0.1.0-qa",
            "display_name": f"QA layout {unique_suffix()}",
        },
        timeout=10,
    )
    assert reg.status_code == 201
    dev_id = reg.json()["data"]["device_id"]
    try:
        body = s.get(f"{base_url}/app/devices?show_qa_fixtures=1", timeout=10).text
        assert 'class="v3-devices-table"' in body, "missing desktop-table wrapper"
        assert 'class="v3-device-cards"' in body, "missing mobile-card wrapper"
    finally:
        s.delete(f"{base_url}/api/v1/admin/devices/{dev_id}", timeout=10)


# ── Central vs local cue ──────────────────────────────────────────────────

def test_devices_list_shows_central_vs_local_badges(base_url, shell_session):
    """Render at least one device's central-vs-local cue. Self-creates
    a fixture so the assertion is meaningful even on an empty fleet."""
    s = shell_session
    et = s.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        json={"display_name_hint": f"QA cue {unique_suffix()}", "note": "qa-v031-cue"},
        timeout=10,
    ).json()["data"]["enrollment_token"]
    reg = requests.post(
        f"{base_url}/api/v1/device/register",
        json={
            "enrollment_token": et,
            "hardware_model": "sonoff_s31",
            "firmware_version": "0.1.0-qa",
            "display_name": f"QA cue {unique_suffix()}",
        },
        timeout=10,
    )
    assert reg.status_code == 201
    dev_id = reg.json()["data"]["device_id"]
    try:
        body = s.get(f"{base_url}/app/devices?show_qa_fixtures=1", timeout=10).text
        assert ">central<" in body or ">local-only<" in body, (
            "devices list must display the central-vs-local cue badges"
        )
    finally:
        s.delete(f"{base_url}/api/v1/admin/devices/{dev_id}", timeout=10)


# ── Device detail tab strip ──────────────────────────────────────────────

def test_device_detail_renders_tab_strip(base_url, shell_session):
    s = shell_session
    devs = s.get(
        f"{base_url}/api/v1/admin/devices?show_qa_fixtures=1",
        timeout=10,
    ).json()["data"]["devices"]
    if not devs:
        pytest.skip("no devices exist on live to test detail page against")
    body = s.get(f"{base_url}/app/devices/{devs[0]['id']}", timeout=10).text
    assert 'class="v3-tabbar"' in body, "device-detail page missing tab strip"
    # All 7 section anchors must be present.
    for anchor in ("#overview", "#power", "#watchdog", "#schedule", "#audit", "#events", "#settings"):
        assert anchor in body, f"missing tab anchor {anchor}"


def test_device_detail_open_local_ui_link_when_ip_known(base_url, shell_session):
    """When a device has a local_ip set, the detail page renders an
    'Open local UI' link to http://<ip>/."""
    s = shell_session
    devs = s.get(
        f"{base_url}/api/v1/admin/devices?show_qa_fixtures=1",
        timeout=10,
    ).json()["data"]["devices"]
    with_ip = [d for d in devs if d.get("local_ip")]
    if not with_ip:
        pytest.skip("no devices with local_ip set on the live fleet right now")
    body = s.get(f"{base_url}/app/devices/{with_ip[0]['id']}", timeout=10).text
    assert f"http://{with_ip[0]['local_ip']}/" in body


# ── Enrollment wizard ─────────────────────────────────────────────────────

def test_enroll_device_wizard_renders(base_url, shell_session):
    s = shell_session
    r = s.get(f"{base_url}/app/devices/new", timeout=10)
    assert r.status_code == 200
    # 0.6.24 #187 copy sweep: "Enrol a device" → "Add a device" (US
    # spelling + verb+noun pattern). Route names + Python identifiers
    # stay (enroll_device_wizard) — only display strings changed.
    assert "Add a device" in r.text
    assert 'name="display_name_hint"' in r.text


def test_enroll_device_wizard_mints_and_displays_token(base_url, shell_session):
    s = shell_session
    r = s.post(
        f"{base_url}/app/devices/new",
        data={"display_name_hint": "QA wizard test", "note": "qa-wizard"},
        timeout=10,
        allow_redirects=True,
    )
    assert r.status_code == 200
    # The minted-token confirmation page must show the actual secret.
    assert "Enrollment token issued" in r.text
    assert "central_base_url" in r.text
    assert "https://www.voipguru.org/rebooter" in r.text
    assert "https://www2.voipguru.org/rebooter" in r.text
    assert "et_" in r.text  # enrollment-token prefix from the service
