"""Unit tests — the v0.6.3 devices-page correctness + perf fixes.

Three defects on the operator-facing `/app/devices` list, all exercised
here directly against the service layer on the isolated-SQLite `hub_db`
fixture:

  DEFECT 1 (correctness) — a device whose only recent contact is a
  command-poll (or /announce) was rendered 'offline' because online/
  offline was computed from `Device.last_heartbeat_at` alone, and that
  column moves only on a full `/api/v1/device/heartbeat`. The fix adds
  `Device.last_seen_at`, refreshed on every authenticated device request
  and on /announce, and measures online/offline against the most-recent
  of the two. `test_command_poll_only_device_renders_online` is the
  mandatory regression test: it FAILS on current `main` (no
  `last_seen_at`) and PASSES with the fix.

  DEFECT 2 (perf) — `firmware_version_breakdown`, `latest_stable_release_dict`
  and `find_by_mac` did whole-table scans / Python filtering. The fixes
  must not change observable output — these tests pin the behaviour.

  DEFECT 3 (UX) — `announcements.serialize` now surfaces honest
  staleness (`is_stale`, `last_seen_age`, `seconds_since_last_seen`) so
  the pending-adoption page stops showing a long-silent adopted device
  as a live, imminent pickup.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import session_scope
from app.models import Device, DeviceAnnouncement, FirmwareRelease
from app.services import announcements as ann
from app.services.devices import (
    find_by_mac,
    firmware_version_breakdown,
    latest_stable_release_dict,
    list_devices,
)
from app.services.devices._serialize import (
    _heartbeat_state_for,
    effective_last_contact,
)


def _device(session, device_id, **kw):
    session.add(Device(id=device_id, **kw))


# ── DEFECT 1 — online/offline reflects real last contact ───────────────


def test_command_poll_only_device_renders_online(hub_db):
    """MANDATORY regression test (DEFECT 1).

    A device whose ONLY recent contact is a command-poll — modelled by a
    fresh `last_seen_at` with a STALE `last_heartbeat_at` (it is not yet
    due for a full heartbeat) — must render 'online'.

    On current `main` `Device` has no `last_seen_at` column and the
    state is computed from `last_heartbeat_at` alone, so this device
    renders 'offline' — the bug. With the fix it renders 'online'.
    """
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _device(
            s,
            "dev-poll",
            registration_state="active",
            # Last full heartbeat was 20 min ago — well past the 180 s
            # window, so heartbeat-only logic calls this 'offline'.
            last_heartbeat_at=now - timedelta(minutes=20),
            # But the device long-polled /commands 10 s ago: real,
            # current contact.
            last_seen_at=now - timedelta(seconds=10),
        )

    rows = list_devices()
    by_id = {d["id"]: d for d in rows}
    dev = by_id["dev-poll"]

    assert dev["heartbeat_state"] == "online", (
        "a device actively long-polling /commands must render online"
    )
    assert dev["online"] is True


def test_announce_only_contact_renders_online(hub_db):
    """A device whose only recent contact is an /announce poll (e.g. one
    on the auto-rebind path) must also render 'online'."""
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _device(
            s,
            "dev-announce",
            registration_state="active",
            last_heartbeat_at=now - timedelta(hours=3),
            last_seen_at=now - timedelta(seconds=30),
        )
    dev = {d["id"]: d for d in list_devices()}["dev-announce"]
    assert dev["heartbeat_state"] == "online"
    assert dev["online"] is True


def test_genuinely_silent_device_still_offline(hub_db):
    """A device with NO recent contact on any path stays 'offline' — the
    fix must not paper over a real outage."""
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _device(
            s,
            "dev-dead",
            registration_state="active",
            last_heartbeat_at=now - timedelta(hours=2),
            last_seen_at=now - timedelta(hours=2),
        )
    dev = {d["id"]: d for d in list_devices()}["dev-dead"]
    assert dev["heartbeat_state"] == "offline"
    assert dev["online"] is False


def test_never_contacted_device_renders_never(hub_db):
    """A device with neither a heartbeat nor any other contact still
    reports 'never' — distinct from 'offline'."""
    with session_scope() as s:
        _device(s, "dev-new", registration_state="active")
    dev = {d["id"]: d for d in list_devices()}["dev-new"]
    assert dev["heartbeat_state"] == "never"
    assert dev["online"] is False


def test_recent_heartbeat_still_online_without_last_seen(hub_db):
    """Back-compat: a row with a recent `last_heartbeat_at` and a NULL
    `last_seen_at` (a pre-0.6.3 row that has not been seen since the
    column shipped) still renders 'online' — the heartbeat-only path is
    preserved."""
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _device(
            s,
            "dev-hb",
            registration_state="active",
            last_heartbeat_at=now - timedelta(seconds=30),
            last_seen_at=None,
        )
    dev = {d["id"]: d for d in list_devices()}["dev-hb"]
    assert dev["heartbeat_state"] == "online"


def test_effective_last_contact_picks_the_newest():
    """`effective_last_contact` returns the most-recent of the two
    timestamps, tolerates NULLs, and is None only when both are absent."""
    now = datetime.now(timezone.utc)
    older = now - timedelta(hours=1)
    assert effective_last_contact(older, now) == now
    assert effective_last_contact(now, older) == now
    assert effective_last_contact(None, now) == now
    assert effective_last_contact(now, None) == now
    assert effective_last_contact(None, None) is None


def test_heartbeat_state_for_measures_against_last_seen():
    """`_heartbeat_state_for` measures freshness against the newest of
    heartbeat / last-seen, not heartbeat alone."""
    now = datetime.now(timezone.utc)
    stale_hb = now - timedelta(hours=1)
    fresh_seen = now - timedelta(seconds=5)
    assert (
        _heartbeat_state_for(
            stale_hb, now=now, offline_threshold_seconds=180,
            last_seen_at=fresh_seen,
        )
        == "online"
    )
    # No last_seen passed → legacy heartbeat-only behaviour.
    assert (
        _heartbeat_state_for(stale_hb, now=now, offline_threshold_seconds=180)
        == "offline"
    )


# ── DEFECT 2 — perf fixes preserve observable behaviour ────────────────


def test_firmware_version_breakdown_groups_correctly(hub_db):
    """`firmware_version_breakdown` still groups the fleet by firmware
    version, counts each cohort, lists the devices, and flags the
    majority — unchanged output after the column-projection perf fix."""
    with session_scope() as s:
        _device(s, "d1", display_name="Alpha", firmware_version="0.1.5")
        _device(s, "d2", display_name="Bravo", firmware_version="0.1.5")
        _device(s, "d3", display_name="Charlie", firmware_version="0.1.4")
        _device(s, "d4", display_name="Delta", firmware_version=None)
    breakdown = firmware_version_breakdown()
    by_ver = {b["version"]: b for b in breakdown}
    assert by_ver["0.1.5"]["count"] == 2
    assert by_ver["0.1.5"]["is_majority"] is True
    assert by_ver["0.1.4"]["count"] == 1
    assert by_ver["0.1.4"]["is_majority"] is False
    assert by_ver["(unknown)"]["count"] == 1
    # devices list carries id + display_name, sorted by display_name.
    assert by_ver["0.1.5"]["devices"] == [
        {"id": "d1", "display_name": "Alpha"},
        {"id": "d2", "display_name": "Bravo"},
    ]


def test_firmware_version_breakdown_excludes_qa_fixtures(hub_db):
    """QA fixtures stay excluded unless explicitly included."""
    with session_scope() as s:
        _device(s, "real", display_name="Real", firmware_version="0.1.5")
        _device(
            s, "fix", display_name="Fixture", firmware_version="0.1.5",
            is_qa_fixture=True,
        )
    assert firmware_version_breakdown()[0]["count"] == 1
    assert firmware_version_breakdown(include_qa_fixtures=True)[0]["count"] == 2


def test_firmware_version_breakdown_empty_fleet(hub_db):
    assert firmware_version_breakdown() == []


def test_latest_stable_release_dict_picks_highest_version(hub_db):
    """`latest_stable_release_dict` returns the numerically-highest
    stable release — NOT the most recently uploaded one. The perf fix
    (column projection + LIMIT) must preserve that ordering, or the
    v0.4.29 downgrade-button bug returns."""
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        # 0.1.9 uploaded AFTER 0.1.10 — a re-upload. Correct answer is
        # still 0.1.10 (numerically higher), not the newest upload.
        s.add(FirmwareRelease(
            version="0.1.10", channel="stable", filename="f10.bin",
            download_url="http://x/f10", sha256="a" * 64,
            created_at=now - timedelta(hours=2),
        ))
        s.add(FirmwareRelease(
            version="0.1.9", channel="stable", filename="f9.bin",
            download_url="http://x/f9", sha256="b" * 64,
            created_at=now,
        ))
        # A dev-channel release must never be picked.
        s.add(FirmwareRelease(
            version="0.2.0", channel="dev", filename="f20.bin",
            download_url="http://x/f20", sha256="c" * 64,
            created_at=now,
        ))
    latest = latest_stable_release_dict()
    assert latest is not None
    assert latest["version"] == "0.1.10"
    assert latest["channel"] == "stable"


def test_latest_stable_release_dict_none_when_no_stable(hub_db):
    with session_scope() as s:
        s.add(FirmwareRelease(
            version="0.2.0", channel="dev", filename="f.bin",
            download_url="http://x/f", sha256="d" * 64,
        ))
    assert latest_stable_release_dict() is None


def test_find_by_mac_filters_in_sql(hub_db):
    """`find_by_mac` returns only the devices whose MAC matches, is
    case-insensitive, and excludes decommissioned rows — unchanged after
    the move from Python filtering to a SQL WHERE."""
    with session_scope() as s:
        _device(s, "m1", mac_address="AA:BB:CC:DD:EE:FF")
        # Same MAC, lower-case — must still match.
        _device(s, "m2", mac_address="aa:bb:cc:dd:ee:ff")
        # Same MAC but decommissioned — must be excluded.
        _device(
            s, "m3", mac_address="AA:BB:CC:DD:EE:FF",
            registration_state="decommissioned",
        )
        # Different MAC — must not match.
        _device(s, "m4", mac_address="11:22:33:44:55:66")
        # NULL MAC — must not match / not crash.
        _device(s, "m5", mac_address=None)
    hits = {d["id"] for d in find_by_mac("AA:BB:CC:DD:EE:FF")}
    assert hits == {"m1", "m2"}


def test_find_by_mac_blank_returns_empty(hub_db):
    assert find_by_mac(None) == []
    assert find_by_mac("   ") == []


# ── DEFECT 3 — pending-adoption staleness honesty ──────────────────────


def _announcement(session, mac, **kw):
    now = datetime.now(timezone.utc)
    row = DeviceAnnouncement(
        mac_address=mac,
        first_seen_at=kw.pop("first_seen_at", now),
        last_seen_at=kw.pop("last_seen_at", now),
        announce_count=kw.pop("announce_count", 1),
        **kw,
    )
    session.add(row)
    session.flush()
    return row.id


def test_fresh_adopted_announcement_is_not_stale(hub_db):
    """An adopted announcement whose device polled recently is NOT stale
    — the page should still show it as a live, imminent pickup."""
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        aid = _announcement(
            s, "AABBCCDDEEFF",
            last_seen_at=now - timedelta(seconds=20),
            adopted_at=now - timedelta(seconds=30),
            adoption_token_secret="et_secret",
        )
        row = s.get(DeviceAnnouncement, aid)
        out = ann.serialize(row, now=now)
    assert out["state"] == "awaiting_pickup"
    assert out["is_stale"] is False


def test_long_silent_adopted_announcement_is_stale(hub_db):
    """The motivating DEFECT 3 case: a device adopted but silent for ~6
    days is shown honestly as stale, with a 'time since' string — not as
    an active 'token will deliver on next poll' pickup."""
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        aid = _announcement(
            s, "AABBCCDDEE01",
            first_seen_at=now - timedelta(days=7),
            last_seen_at=now - timedelta(days=6),
            adopted_at=now - timedelta(days=5),
            adoption_token_secret="et_secret",
        )
        row = s.get(DeviceAnnouncement, aid)
        out = ann.serialize(row, now=now)
    assert out["state"] == "awaiting_pickup"
    assert out["is_stale"] is True
    assert out["seconds_since_last_seen"] >= 6 * 24 * 3600
    assert out["last_seen_age"] == "6 d"


def test_terminal_state_announcement_never_flagged_stale(hub_db):
    """A registered (terminal) announcement is history — its age is not a
    broken promise, so it is never flagged stale even if old."""
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        aid = _announcement(
            s, "AABBCCDDEE02",
            last_seen_at=now - timedelta(days=30),
            adopted_at=now - timedelta(days=30),
            delivered_at=now - timedelta(days=30),
            consumed_at=now - timedelta(days=30),
        )
        row = s.get(DeviceAnnouncement, aid)
        out = ann.serialize(row, now=now)
    assert out["state"] == "registered"
    assert out["is_stale"] is False


def test_humanize_age_buckets():
    """`_humanize_age` renders compact, honest 'time since' strings."""
    assert ann._humanize_age(None) is None
    assert ann._humanize_age(30) == "30 s"
    assert ann._humanize_age(600) == "10 m"
    assert ann._humanize_age(3 * 3600) == "3 h"
    assert ann._humanize_age(6 * 24 * 3600) == "6 d"
