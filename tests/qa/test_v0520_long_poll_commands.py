"""v0.5.20 (#1): /api/v1/device/commands long-poll behaviour.

Contract:
- Missing/zero Prefer: wait → legacy no-wait response (immediate).
- Prefer: wait=N → server holds the request open until either a
  command is enqueued for the device or N seconds elapse (capped at
  30s server-side). Response includes `Preference-Applied: wait=N`.
- Empty result on timeout is `{commands: []}` (not 304/never).

This test is **slow** (it deliberately holds requests for 4-6 s)
so it's marked `slow` and excluded from the default `pytest -m 'not
slow'` run.
"""

from __future__ import annotations

import time

import pytest
import requests


def _mint_enrollment(base_url, admin_headers):
    """Mint a one-shot enrollment token via the admin API. Reused from
    test_device_api.py — duplicated here to keep this file self-
    contained for the long-poll smoke."""
    r = requests.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        headers=admin_headers,
        json={"reason": "v0520 long-poll smoke"},
        timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


def _register(base_url, enrollment_token):
    return requests.post(
        f"{base_url}/api/v1/device/register",
        json={
            "enrollment_token": enrollment_token,
            "mac_address": "AA:BB:CC:DD:EE:FF",
            "hardware_model": "qa-test",
            "firmware_version": "0.5.20-qa",
        },
        timeout=10,
    )


@pytest.fixture
def fresh_device(base_url, admin_headers):
    et = _mint_enrollment(base_url, admin_headers)["enrollment_token"]
    reg = _register(base_url, et).json()["data"]
    yield {
        "device_id": reg["device_id"],
        "headers": {"Authorization": f"Bearer {reg['device_token']}"},
    }
    # Best-effort cleanup
    requests.delete(
        f"{base_url}/api/v1/admin/devices/{reg['device_id']}",
        headers=admin_headers,
        timeout=10,
    )


def test_legacy_no_wait_immediate_return(base_url, fresh_device):
    """No Prefer header → response returns immediately (under 2 s for
    the live HTTPS round-trip)."""
    start = time.monotonic()
    r = requests.get(
        f"{base_url}/api/v1/device/commands",
        headers=fresh_device["headers"],
        timeout=10,
    )
    elapsed = time.monotonic() - start
    assert r.status_code == 200, r.text
    assert r.json()["data"]["commands"] == []
    assert elapsed < 2.0, f"legacy no-wait should be near-instant; took {elapsed:.2f}s"
    # No Preference-Applied for the no-wait path.
    assert "Preference-Applied" not in r.headers


@pytest.mark.slow
def test_long_poll_times_out_with_empty_result(base_url, fresh_device):
    """Prefer: wait=3 with no commands enqueued → holds for ~3 s,
    returns `{commands: []}` + Preference-Applied header."""
    start = time.monotonic()
    r = requests.get(
        f"{base_url}/api/v1/device/commands",
        headers={**fresh_device["headers"], "Prefer": "wait=3"},
        timeout=15,
    )
    elapsed = time.monotonic() - start
    assert r.status_code == 200, r.text
    assert r.json()["data"]["commands"] == []
    # Should have held open for at least the wait window (allow 0.5s
    # slop for the 1-s check cadence + network).
    assert 2.5 <= elapsed <= 6.0, (
        f"expected long-poll to hold ~3s; elapsed={elapsed:.2f}s"
    )
    assert r.headers.get("Preference-Applied") == "wait=3"


@pytest.mark.slow
def test_long_poll_returns_early_when_command_enqueued(
    base_url, admin_headers, fresh_device
):
    """Prefer: wait=10 → while the device is waiting, the operator
    enqueues a command via the admin API. Long-poll should return
    within ~2 s of the enqueue, well before the 10-s deadline."""
    import threading

    def _enqueue_after_delay():
        time.sleep(2.0)
        requests.post(
            f"{base_url}/api/v1/admin/devices/{fresh_device['device_id']}/commands",
            headers={**admin_headers, "Content-Type": "application/json"},
            json={"type": "ping"},
            timeout=10,
        )

    threading.Thread(target=_enqueue_after_delay, daemon=True).start()

    start = time.monotonic()
    r = requests.get(
        f"{base_url}/api/v1/device/commands",
        headers={**fresh_device["headers"], "Prefer": "wait=10"},
        timeout=15,
    )
    elapsed = time.monotonic() - start
    assert r.status_code == 200, r.text
    # The check cadence is 1s; expect 2-5s (2s enqueue delay + up to
    # the next 1s tick).
    assert 1.5 <= elapsed <= 6.0, (
        f"expected long-poll to return shortly after enqueue; elapsed={elapsed:.2f}s"
    )
    cmds = r.json()["data"]["commands"]
    assert len(cmds) >= 1, "expected at least one queued command"
    assert r.headers.get("Preference-Applied") == "wait=10"
