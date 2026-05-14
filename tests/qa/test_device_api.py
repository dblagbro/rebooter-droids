"""Full device-API round-trip + negative paths."""

import time
import uuid

import requests

from .conftest import unique_suffix


def _mint_enrollment(base_url, admin_headers, hint=None):
    r = requests.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        headers=admin_headers,
        json={"display_name_hint": hint or f"QA {unique_suffix()}", "note": "qa"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["data"]


def _register(base_url, et, **extra):
    payload = {
        "enrollment_token": et,
        "hardware_model": "sonoff_s31",
        "firmware_version": "0.1.0",
        "display_name": f"QA Device {unique_suffix()}",
        **extra,
    }
    r = requests.post(f"{base_url}/api/v1/device/register", json=payload, timeout=10)
    return r


def test_register_then_heartbeat_then_command_round_trip(base_url, admin_headers):
    et = _mint_enrollment(base_url, admin_headers)["enrollment_token"]
    reg = _register(base_url, et)
    assert reg.status_code == 201, reg.text
    d = reg.json()["data"]
    assert d["device_id"].startswith("dev_")
    assert d["device_token"].startswith("dt_")
    assert d["poll_interval_seconds"] == 30
    assert d["heartbeat_interval_seconds"] == 60

    dev_id = d["device_id"]
    dev_token = d["device_token"]
    H = {"Authorization": f"Bearer {dev_token}"}

    # heartbeat
    r = requests.post(
        f"{base_url}/api/v1/device/heartbeat",
        headers=H,
        json={
            "device_id": dev_id,
            "firmware_version": "0.1.0",
            "mode": "smart_plug",
            "relay_on": True,
            "wifi_connected": True,
            "health_state": "healthy",
            "uptime_seconds": 60,
        },
        timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["data"]["next_poll_after_seconds"] == 30

    # admin enqueue relay_on
    r = requests.post(
        f"{base_url}/api/v1/admin/devices/{dev_id}/commands",
        headers=admin_headers,
        json={"type": "relay_on"},
        timeout=10,
    )
    assert r.status_code == 201
    cmd_id = r.json()["data"]["command_id"]

    # device polls
    r = requests.get(f"{base_url}/api/v1/device/commands", headers=H, timeout=10)
    assert r.status_code == 200
    cmds = r.json()["data"]["commands"]
    assert any(c["command_id"] == cmd_id for c in cmds)

    # device reports completed
    r = requests.post(
        f"{base_url}/api/v1/device/command-result",
        headers=H,
        json={
            "device_id": dev_id,
            "command_id": cmd_id,
            "status": "completed",
            "completed_at": "2026-05-09T03:00:00Z",
            "result": {"relay_on": True},
        },
        timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "completed"

    # admin sees zero pending now (the command is completed)
    r = requests.get(
        f"{base_url}/api/v1/admin/devices/{dev_id}", headers=admin_headers, timeout=10
    )
    detail = r.json()["data"]
    assert all(c["id"] != cmd_id for c in detail.get("pending_commands", []))


def test_register_rejects_consumed_token(base_url, admin_headers):
    et = _mint_enrollment(base_url, admin_headers)["enrollment_token"]
    assert _register(base_url, et).status_code == 201
    second = _register(base_url, et)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "enrollment_consumed"


def test_register_rejects_unknown_token(base_url):
    r = _register(base_url, "et_definitely-not-real-xxxxxxxxxxxxxx")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "enrollment_invalid"


def test_register_rejects_missing_token(base_url):
    r = requests.post(f"{base_url}/api/v1/device/register", json={}, timeout=10)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_failed"


def test_heartbeat_requires_device_token(base_url):
    r = requests.post(
        f"{base_url}/api/v1/device/heartbeat", json={"device_id": "dev_x"}, timeout=10
    )
    assert r.status_code == 401


def test_heartbeat_rejects_admin_token_at_device_endpoint(base_url, admin_headers):
    """An admin JWT must not authenticate at a device endpoint."""
    r = requests.post(
        f"{base_url}/api/v1/device/heartbeat",
        headers=admin_headers,
        json={"device_id": "dev_x"},
        timeout=10,
    )
    assert r.status_code == 401, (
        "admin JWT should not pass device-token check"
    )


def test_heartbeat_rejects_mismatched_device_id(base_url, admin_headers):
    et = _mint_enrollment(base_url, admin_headers)["enrollment_token"]
    reg = _register(base_url, et).json()["data"]
    H = {"Authorization": f"Bearer {reg['device_token']}"}
    r = requests.post(
        f"{base_url}/api/v1/device/heartbeat",
        headers=H,
        json={"device_id": "dev_someone_else"},
        timeout=10,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "device_mismatch"


def test_command_result_unknown_command(base_url, admin_headers):
    et = _mint_enrollment(base_url, admin_headers)["enrollment_token"]
    reg = _register(base_url, et).json()["data"]
    H = {"Authorization": f"Bearer {reg['device_token']}"}
    r = requests.post(
        f"{base_url}/api/v1/device/command-result",
        headers=H,
        json={
            "device_id": reg["device_id"],
            "command_id": "cmd_does_not_exist",
            "status": "completed",
        },
        timeout=10,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "command_unknown"


def test_events_batch_ingest(base_url, admin_headers):
    et = _mint_enrollment(base_url, admin_headers)["enrollment_token"]
    reg = _register(base_url, et).json()["data"]
    H = {"Authorization": f"Bearer {reg['device_token']}"}
    events = [
        {"type": "boot", "timestamp": "2026-05-08T22:00:00Z"},
        {
            "type": "watchdog_trigger",
            "timestamp": "2026-05-08T22:01:00Z",
            "details": {"targets_failed": ["1.1.1.1"]},
        },
    ]
    r = requests.post(
        f"{base_url}/api/v1/device/events",
        headers=H,
        json={"device_id": reg["device_id"], "events": events},
        timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["data"]["ingested"] == 2


def test_events_over_max_batch_rejected(base_url, admin_headers):
    et = _mint_enrollment(base_url, admin_headers)["enrollment_token"]
    reg = _register(base_url, et).json()["data"]
    H = {"Authorization": f"Bearer {reg['device_token']}"}
    events = [
        {"type": "test", "timestamp": "2026-05-08T22:00:00Z"} for _ in range(201)
    ]
    r = requests.post(
        f"{base_url}/api/v1/device/events",
        headers=H,
        json={"device_id": reg["device_id"], "events": events},
        timeout=10,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_failed"


def test_firmware_assignment_initially_none(base_url, admin_headers):
    et = _mint_enrollment(base_url, admin_headers)["enrollment_token"]
    reg = _register(base_url, et).json()["data"]
    H = {"Authorization": f"Bearer {reg['device_token']}"}
    r = requests.get(f"{base_url}/api/v1/device/firmware", headers=H, timeout=10)
    assert r.status_code == 200
    assert r.json()["data"] in (
        {"assigned": False},
    ) or not r.json()["data"]["assigned"], (
        "freshly registered device should not have a firmware assignment"
    )


def test_power_samples_batch_ingest(base_url, admin_headers):
    et = _mint_enrollment(base_url, admin_headers)["enrollment_token"]
    reg = _register(base_url, et).json()["data"]
    H = {"Authorization": f"Bearer {reg['device_token']}"}
    samples = [
        {
            "sampled_at": "2026-05-14T00:00:00Z",
            "source": "steady",
            "v_v": 120.4,
            "i_ma": 1450,
            "p_w": 175.3,
            "rssi_dbm": -61,
            "chip_type": "CSE7766",
        },
        {
            "sampled_uptime_seconds": 120,
            "source": "synthetic",
            "source_flags": 1,
            "rssi_dbm": -63,
            "chip_type": "CSE7766",
        },
    ]
    r = requests.post(
        f"{base_url}/api/v1/device/power-samples",
        headers=H,
        json={"device_id": reg["device_id"], "samples": samples},
        timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["data"]["ingested"] == 2


def test_power_samples_over_max_batch_rejected(base_url, admin_headers):
    et = _mint_enrollment(base_url, admin_headers)["enrollment_token"]
    reg = _register(base_url, et).json()["data"]
    H = {"Authorization": f"Bearer {reg['device_token']}"}
    samples = [{"source": "synthetic", "rssi_dbm": -60} for _ in range(3601)]
    r = requests.post(
        f"{base_url}/api/v1/device/power-samples",
        headers=H,
        json={"device_id": reg["device_id"], "samples": samples},
        timeout=10,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_failed"
