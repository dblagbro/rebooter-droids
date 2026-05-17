"""Unit tests — the device-command service.

`app/services/commands.py` is the command queue: payload validation,
enqueue (per-device + group fan-out) with the `is_protected` lockout
gate and `is_held_off` side effects, cancel, the device-poll
delivery path, result recording and TTL expiry. `_validate_payload`
is pure; everything else takes the `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import Command, Device, GroupMembership
from app.services import commands
from app.services.commands import DeviceLockedError, _validate_payload


def _device(session, device_id, **kw):
    session.add(Device(id=device_id, **kw))


# ── _validate_payload (pure) ───────────────────────────────────────────

def test_validate_set_mode():
    assert _validate_payload("set_mode", {"mode": "smart_plug"}) == {"mode": "smart_plug"}
    with pytest.raises(ValueError):
        _validate_payload("set_mode", {"mode": "teleport"})
    with pytest.raises(ValueError):
        _validate_payload("set_mode", {})  # missing mode


def test_validate_apply_config():
    assert _validate_payload("apply_config", {"device_name": "Plug"}) == {"device_name": "Plug"}
    with pytest.raises(ValueError):
        _validate_payload("apply_config", {})  # must be non-empty
    with pytest.raises(ValueError):
        _validate_payload("apply_config", {"bogus_key": 1})  # unsupported key


def test_validate_relay_cycle_type_checks_seconds():
    assert _validate_payload("relay_cycle", {"power_off_seconds": 5}) == {"power_off_seconds": 5}
    assert _validate_payload("relay_cycle", {}) == {}
    with pytest.raises(ValueError):
        _validate_payload("relay_cycle", {"power_off_seconds": "5"})


def test_validate_lan_scan_range():
    assert _validate_payload("lan_scan", {"start": 1, "end": 10}) == {"start": 1, "end": 10}
    with pytest.raises(ValueError):
        _validate_payload("lan_scan", {"start": "1", "end": 10})  # non-int
    with pytest.raises(ValueError):
        _validate_payload("lan_scan", {"start": 10, "end": 5})    # start > end
    with pytest.raises(ValueError):
        _validate_payload("lan_scan", {"start": 0, "end": 10})    # out of 1..254


def test_validate_lan_proxy():
    out = _validate_payload("lan_proxy", {"ip": "10.0.0.5", "path": "/status",
                                          "method": "post"})
    assert out == {"ip": "10.0.0.5", "path": "/status", "method": "POST"}
    with pytest.raises(ValueError):
        _validate_payload("lan_proxy", {"path": "/x"})            # missing ip
    with pytest.raises(ValueError):
        _validate_payload("lan_proxy", {"ip": "10.0.0.5", "path": "no-slash"})
    with pytest.raises(ValueError):
        _validate_payload("lan_proxy", {"ip": "10.0.0.5", "path": "/x", "method": "DELETE"})


def test_validate_lan_ota_push():
    out = _validate_payload("lan_ota_push", {"ip": "10.0.0.5",
                                             "url": "https://h/fw.bin"})
    assert out == {"ip": "10.0.0.5", "url": "https://h/fw.bin"}
    with pytest.raises(ValueError):
        _validate_payload("lan_ota_push", {"url": "https://h/fw.bin"})  # missing ip
    with pytest.raises(ValueError):
        _validate_payload("lan_ota_push", {"ip": "10.0.0.5", "url": "ftp://h/fw.bin"})


def test_validate_passthrough_for_simple_types():
    assert _validate_payload("relay_on", None) == {}
    assert _validate_payload("relay_on", {"anything": 1}) == {"anything": 1}


# ── enqueue_for_device ─────────────────────────────────────────────────

def test_enqueue_for_device_creates_pending_command(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    cmd = commands.enqueue_for_device("dev-1", "relay_on", None, issued_by_user_id=None)
    assert cmd.type == "relay_on"
    assert cmd.status == "pending"
    assert cmd.expires_at is not None


def test_enqueue_unsupported_type_raises(hub_db):
    with pytest.raises(ValueError):
        commands.enqueue_for_device("dev-1", "teleport", None, issued_by_user_id=None)


def test_enqueue_unknown_device_raises_lookup(hub_db):
    with pytest.raises(LookupError):
        commands.enqueue_for_device("dev-nope", "relay_on", None, issued_by_user_id=None)


def test_enqueue_protected_device_blocks_power_command(hub_db):
    with session_scope() as s:
        _device(s, "dev-prot", is_protected=True)
    with pytest.raises(DeviceLockedError):
        commands.enqueue_for_device("dev-prot", "relay_on", None, issued_by_user_id=None)
    # A non-power command is unaffected by the lockout.
    cmd = commands.enqueue_for_device("dev-prot", "check_firmware", None,
                                      issued_by_user_id=None)
    assert cmd.type == "check_firmware"


def test_enqueue_protected_device_honours_override(hub_db):
    with session_scope() as s:
        _device(s, "dev-prot", is_protected=True)
    cmd = commands.enqueue_for_device("dev-prot", "relay_off", None,
                                      issued_by_user_id=None, override_lockout=True)
    assert cmd.status == "pending"


def test_enqueue_set_hold_off_flips_the_flag(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    commands.enqueue_for_device("dev-1", "relay_off", None, issued_by_user_id=None,
                                set_hold_off=True)
    with session_scope() as s:
        assert s.get(Device, "dev-1").is_held_off is True


def test_enqueue_power_on_clears_hold_off(hub_db):
    with session_scope() as s:
        _device(s, "dev-1", is_held_off=True)
    commands.enqueue_for_device("dev-1", "relay_on", None, issued_by_user_id=None)
    with session_scope() as s:
        assert s.get(Device, "dev-1").is_held_off is False


def test_enqueue_custom_ttl(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    cmd = commands.enqueue_for_device("dev-1", "relay_on", None, issued_by_user_id=None,
                                      ttl_seconds=60)
    delta = cmd.expires_at - datetime.now(timezone.utc)
    assert timedelta(seconds=30) < delta <= timedelta(seconds=61)


# ── enqueue_for_group ──────────────────────────────────────────────────

def test_enqueue_for_group_fans_out_to_members(hub_db):
    with session_scope() as s:
        _device(s, "dev-a")
        _device(s, "dev-b")
        s.add(GroupMembership(group_id="grp-1", device_id="dev-a"))
        s.add(GroupMembership(group_id="grp-1", device_id="dev-b"))
    created, skipped = commands.enqueue_for_group("grp-1", "relay_on", None,
                                                  issued_by_user_id=None)
    assert len(created) == 2
    assert skipped == []


def test_enqueue_for_group_skips_protected_devices(hub_db):
    with session_scope() as s:
        _device(s, "dev-ok")
        _device(s, "dev-prot", is_protected=True)
        s.add(GroupMembership(group_id="grp-1", device_id="dev-ok"))
        s.add(GroupMembership(group_id="grp-1", device_id="dev-prot"))
    created, skipped = commands.enqueue_for_group("grp-1", "relay_on", None,
                                                  issued_by_user_id=None)
    assert [c.device_id for c in created] == ["dev-ok"]
    assert skipped == ["dev-prot"]


# ── cancel_pending_command ─────────────────────────────────────────────

def test_cancel_pending_command(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    cmd = commands.enqueue_for_device("dev-1", "relay_on", None, issued_by_user_id=None)
    assert commands.cancel_pending_command(cmd.id, by_user_id=None) is True
    with session_scope() as s:
        assert s.get(Command, cmd.id).status == "cancelled"
    # Already cancelled → no longer in `pending` → second cancel is a no-op.
    assert commands.cancel_pending_command(cmd.id, by_user_id=None) is False


def test_cancel_unknown_command_returns_false(hub_db):
    assert commands.cancel_pending_command("cmd-nope", by_user_id=None) is False


# ── list_pending_for_device ────────────────────────────────────────────

def test_list_pending_marks_delivered(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    cmd = commands.enqueue_for_device("dev-1", "relay_on", None, issued_by_user_id=None)
    rows = commands.list_pending_for_device("dev-1", mark_delivered=True)
    assert [r.id for r in rows] == [cmd.id]
    with session_scope() as s:
        stored = s.get(Command, cmd.id)
        assert stored.status == "accepted"
        assert stored.delivered_at is not None


def test_list_pending_without_marking_leaves_status(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    cmd = commands.enqueue_for_device("dev-1", "relay_on", None, issued_by_user_id=None)
    commands.list_pending_for_device("dev-1", mark_delivered=False)
    with session_scope() as s:
        assert s.get(Command, cmd.id).status == "pending"


def test_list_pending_excludes_expired(hub_db):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    with session_scope() as s:
        _device(s, "dev-1")
        s.add(Command(device_id="dev-1", type="relay_on", status="pending",
                      expires_at=past))
    assert commands.list_pending_for_device("dev-1") == []


# ── record_result ──────────────────────────────────────────────────────

def test_record_result_stores_result_and_updates_command(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    cmd = commands.enqueue_for_device("dev-1", "relay_on", None, issued_by_user_id=None)
    cr = commands.record_result("dev-1", cmd.id, "completed", "ok", {"relay": "on"},
                                completed_at=None)
    assert cr.status == "completed"
    with session_scope() as s:
        assert s.get(Command, cmd.id).status == "completed"


def test_record_result_rejects_bad_status(hub_db):
    with pytest.raises(ValueError):
        commands.record_result("dev-1", "cmd-x", "bogus", None, None, completed_at=None)


def test_record_result_unknown_command_raises_lookup(hub_db):
    with session_scope() as s:
        _device(s, "dev-1")
    cmd = commands.enqueue_for_device("dev-1", "relay_on", None, issued_by_user_id=None)
    with pytest.raises(LookupError):
        commands.record_result("dev-1", "cmd-nope", "completed", None, None,
                               completed_at=None)
    # Right command id, wrong device → still a LookupError.
    with pytest.raises(LookupError):
        commands.record_result("dev-other", cmd.id, "completed", None, None,
                               completed_at=None)


# ── expire_overdue_commands ────────────────────────────────────────────

def test_expire_overdue_commands(hub_db):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    with session_scope() as s:
        _device(s, "dev-1")
        s.add(Command(device_id="dev-1", type="relay_on", status="pending",
                      expires_at=past))
    fresh = commands.enqueue_for_device("dev-1", "relay_off", None, issued_by_user_id=None)

    assert commands.expire_overdue_commands() == 1  # only the overdue one

    with session_scope() as s:
        states = {c.id: c.status for c in s.scalars(select(Command))}
        assert states[fresh.id] == "pending"          # in-window command untouched
        assert "expired" in states.values()
