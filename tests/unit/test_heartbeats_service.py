"""Unit tests — the heartbeat-ingest service.

`app/services/heartbeats.py` records each device heartbeat: a
`DeviceHeartbeat` history row, the Device hot-column refresh, the
firmware/IP update, recovery-transition detection and the
`reported_config` stash. All DB-backed → the `hub_db` isolated-SQLite
fixture.

`record_heartbeat` does a deferred import of
`device_config.maybe_push_after_recovery` on a recovery transition —
that pulls in the commands + audit services, so an autouse fixture
swaps it for a spy. The transition tests assert against the spy; the
rest simply keep the heavy path out.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import session_scope
from app.models import Device, DeviceHeartbeat
from app.services.heartbeats import latest_heartbeat, record_heartbeat


@pytest.fixture(autouse=True)
def recovery_push_spy(monkeypatch):
    """Swap the deferred `maybe_push_after_recovery` for a spy — keeps
    the device_config/commands/audit chain out of these tests and lets
    the transition tests assert what (device_id, trigger) fired."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.services.device_config.maybe_push_after_recovery",
        lambda device_id, *, trigger: calls.append((device_id, trigger)) or {},
    )
    return calls


def _device(session, device_id, **kw):
    session.add(Device(id=device_id, **kw))


# ── record_heartbeat — errors + history row ────────────────────────────

def test_record_heartbeat_unknown_device_raises(hub_db):
    with pytest.raises(LookupError):
        record_heartbeat("dev-nope", {})


def test_record_heartbeat_writes_history_row(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    out = record_heartbeat("dev-1", {
        "mode": "smart_plug", "relay_on": True, "health_state": "healthy",
        "uptime_seconds": 1234,
    })
    assert "recorded_at" in out
    with session_scope() as s:
        hb = latest_heartbeat(s, "dev-1")
        assert hb is not None
        assert hb.mode == "smart_plug"
        assert hb.relay_on is True
        assert hb.health_state == "healthy"
        assert hb.uptime_seconds == 1234


def test_record_heartbeat_stores_wifi_rssi_dbm(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"wifi_connected": True, "wifi_rssi_dbm": -47})
    with session_scope() as s:
        hb = latest_heartbeat(s, "dev-1")
        assert hb.wifi_rssi_dbm == -47


def test_record_heartbeat_wifi_rssi_absent_is_null(hub_db):
    # Pre-0.2.7 firmware omits the field -> column stays NULL, no crash.
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"wifi_connected": True})
    with session_scope() as s:
        assert latest_heartbeat(s, "dev-1").wifi_rssi_dbm is None


def test_record_heartbeat_updates_device_firmware_and_ip(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"firmware_version": "0.1.20", "local_ip": "10.0.0.9"})
    with session_scope() as s:
        d = s.get(Device, "dev-1")
        assert d.firmware_version == "0.1.20"
        assert d.local_ip == "10.0.0.9"
        assert d.last_heartbeat_at is not None


def test_record_heartbeat_blank_firmware_does_not_clobber(hub_db):
    with session_scope() as s:
        _device(s, "dev-1", firmware_version="0.1.19")
    # Payload omits firmware_version → the last-known value is kept.
    record_heartbeat("dev-1", {"local_ip": "10.0.0.9"})
    with session_scope() as s:
        assert s.get(Device, "dev-1").firmware_version == "0.1.19"


# ── status fields → history row ────────────────────────────────────────

def test_record_heartbeat_copies_status_fields(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {
        "recovery_mode": True, "central_state": "online",
        "consecutive_unhealthy_boots": 2,
    })
    with session_scope() as s:
        hb = latest_heartbeat(s, "dev-1")
        assert hb.recovery_mode is True
        assert hb.central_state == "online"
        assert hb.consecutive_unhealthy_boots == 2


def test_record_heartbeat_partial_payload_leaves_status_null(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"mode": "smart_plug"})  # no status fields
    with session_scope() as s:
        hb = latest_heartbeat(s, "dev-1")
        assert hb.recovery_mode is None
        assert hb.central_state is None


# ── Device hot-column refresh ──────────────────────────────────────────

def test_record_heartbeat_refreshes_device_hot_columns(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"recovery_mode": True, "central_state": "online"})
    with session_scope() as s:
        d = s.get(Device, "dev-1")
        assert d.reported_recovery_mode is True
        assert d.reported_central_state == "online"


def test_record_heartbeat_partial_payload_keeps_last_known_hot_columns(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"recovery_mode": True})
    # A later partial heartbeat without the field must not NULL it out.
    record_heartbeat("dev-1", {"mode": "smart_plug"})
    with session_scope() as s:
        assert s.get(Device, "dev-1").reported_recovery_mode is True


# ── last_event_at parsing ──────────────────────────────────────────────

def test_record_heartbeat_parses_last_event_at(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"last_event_at": "2026-05-17T08:00:00Z"})
    with session_scope() as s:
        hb = latest_heartbeat(s, "dev-1")
        assert hb.last_event_at is not None
        assert hb.last_event_at.year == 2026


def test_record_heartbeat_ignores_malformed_last_event_at(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    # A bad timestamp must not crash the heartbeat — it lands NULL.
    record_heartbeat("dev-1", {"last_event_at": "not-a-timestamp"})
    with session_scope() as s:
        assert latest_heartbeat(s, "dev-1").last_event_at is None


# ── reported_config stash ──────────────────────────────────────────────

def test_record_heartbeat_stashes_reported_config(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"reported_config": {"device_name": "Bench plug"}})
    with session_scope() as s:
        assert s.get(Device, "dev-1").last_reported_config == {"device_name": "Bench plug"}


def test_record_heartbeat_ignores_non_dict_reported_config(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"reported_config": "not-a-dict"})
    with session_scope() as s:
        assert s.get(Device, "dev-1").last_reported_config is None


# ── recovery-transition detection ──────────────────────────────────────

def test_record_heartbeat_detects_lkg_restored_transition(hub_db, recovery_push_spy):
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"last_known_good_restored": True})
    assert recovery_push_spy == [("dev-1", "last_known_good_restored")]


def test_record_heartbeat_detects_recovery_exit_transition(hub_db, recovery_push_spy):
    # Device was in recovery; this heartbeat reports it has left.
    with session_scope() as s:
        _device(s, "dev-1", reported_recovery_mode=True)
    record_heartbeat("dev-1", {"recovery_mode": False})
    assert recovery_push_spy == [("dev-1", "recovery_exit")]


def test_record_heartbeat_steady_state_triggers_no_push(hub_db, recovery_push_spy):
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"recovery_mode": False, "health_state": "healthy"})
    assert recovery_push_spy == []


# ── latest_heartbeat ───────────────────────────────────────────────────

def test_latest_heartbeat_none_when_no_rows(hub_db):
    with session_scope() as s:
        assert latest_heartbeat(s, "dev-1") is None


def test_latest_heartbeat_returns_newest_by_received_at(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _device(s, "dev-1")
        s.add(DeviceHeartbeat(device_id="dev-1", received_at=now - timedelta(hours=2),
                              firmware_version="old"))
        s.add(DeviceHeartbeat(device_id="dev-1", received_at=now,
                              firmware_version="new"))
        s.add(DeviceHeartbeat(device_id="dev-1", received_at=now - timedelta(hours=1),
                              firmware_version="mid"))
    with session_scope() as s:
        assert latest_heartbeat(s, "dev-1").firmware_version == "new"


# ── reboot detection — device.rebooted event on uptime regression ──────

from app.models import DeviceEvent  # noqa: E402
from sqlalchemy import select  # noqa: E402


def _device_events(s, device_id, type_=None):
    q = select(DeviceEvent).where(DeviceEvent.device_id == device_id)
    if type_ is not None:
        q = q.where(DeviceEvent.type == type_)
    return list(s.scalars(q.order_by(DeviceEvent.received_at)))


def test_reboot_event_emitted_when_uptime_regresses(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    # First heartbeat — 5000s uptime.
    record_heartbeat("dev-1", {"uptime_seconds": 5000, "health_state": "healthy"})
    # Second heartbeat — uptime regressed to 30s = device rebooted between them.
    record_heartbeat("dev-1", {
        "uptime_seconds": 30,
        "health_state": "healthy",
        "reset_reason": "Software/System restart",
    })
    with session_scope() as s:
        events = _device_events(s, "dev-1", type_="device.rebooted")
    assert len(events) == 1
    ev = events[0]
    assert ev.details["prior_uptime_seconds"] == 5000
    assert ev.details["new_uptime_seconds"] == 30
    assert ev.details["reset_reason"] == "Software/System restart"
    assert "rebooted" in ev.message.lower()


def test_no_reboot_event_on_monotonic_uptime(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"uptime_seconds": 100})
    record_heartbeat("dev-1", {"uptime_seconds": 200})
    record_heartbeat("dev-1", {"uptime_seconds": 260})
    with session_scope() as s:
        events = _device_events(s, "dev-1", type_="device.rebooted")
    assert events == []


def test_no_reboot_event_on_first_heartbeat(hub_db):
    # No prior heartbeat → nothing to compare against → no event.
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"uptime_seconds": 30})
    with session_scope() as s:
        events = _device_events(s, "dev-1", type_="device.rebooted")
    assert events == []


def test_no_reboot_event_when_uptime_missing(hub_db):
    # A payload that omits uptime_seconds entirely shouldn't crash or emit.
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"uptime_seconds": 1000})
    record_heartbeat("dev-1", {"health_state": "healthy"})  # no uptime_seconds
    with session_scope() as s:
        events = _device_events(s, "dev-1", type_="device.rebooted")
    assert events == []


def test_reboot_event_captures_planned_restart_reason(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"uptime_seconds": 3000})
    record_heartbeat("dev-1", {
        "uptime_seconds": 5,
        "reset_reason": "Software/System restart",
        "last_planned_restart_reason": "button_reboot",
    })
    with session_scope() as s:
        events = _device_events(s, "dev-1", type_="device.rebooted")
    assert len(events) == 1
    assert events[0].details["last_planned_restart_reason"] == "button_reboot"
    assert "button_reboot" in events[0].message


def test_multiple_reboots_each_emit_one_event(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    record_heartbeat("dev-1", {"uptime_seconds": 100})
    record_heartbeat("dev-1", {"uptime_seconds": 200})
    record_heartbeat("dev-1", {"uptime_seconds": 30})  # reboot 1
    record_heartbeat("dev-1", {"uptime_seconds": 90})
    record_heartbeat("dev-1", {"uptime_seconds": 15})  # reboot 2
    with session_scope() as s:
        events = _device_events(s, "dev-1", type_="device.rebooted")
    assert len(events) == 2
    assert events[0].details["prior_uptime_seconds"] == 200
    assert events[1].details["prior_uptime_seconds"] == 90
