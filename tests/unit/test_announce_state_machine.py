"""Unit tests — the device-announcement state machine.

`app/services/announcements.py::upsert_announcement` is the lifecycle:

    pending → adopted → registered   (plus the rejected branch)

The key contract, post-v0.5.68 (the P-REG strand fix): the adoption
token is **re-deliverable on every poll** until the device actually
registers — it is NOT cleared on first delivery. So a repeat announce
of an adopted-not-yet-registered device returns `adopted` again, with
the same token. This is what makes a device that loses one announce
response self-heal instead of stranding.

DB-backed (upsert touches `device_announcements`) → `hub_db`.
"""

from __future__ import annotations

import pytest

from app.services.announcements import (
    AnnouncementError,
    adopt,
    list_announcements,
    mark_consumed,
    reject,
    upsert_announcement,
)


def _announcement_id(mac: str) -> str:
    for a in list_announcements(include_consumed=True):
        if a["mac_address"] == mac:
            return a["id"]
    raise AssertionError(f"no announcement row for {mac}")


# ── pending ────────────────────────────────────────────────────────────

def test_first_announce_is_pending(hub_db):
    resp = upsert_announcement(mac_address="AA:BB:CC:00:00:01")
    assert resp["status"] == "pending"
    assert "enrollment_token" not in resp


def test_pending_retry_after_is_the_configured_default(hub_db):
    resp = upsert_announcement(mac_address="AA:BB:CC:00:00:02")
    # REBOOTER_ANNOUNCE_PENDING_RETRY_AFTER_SECONDS — config default is 5.
    assert resp["retry_after_seconds"] == 5


def test_repeat_announce_before_adopt_stays_pending_and_counts(hub_db):
    mac = "AA:BB:CC:00:00:03"
    upsert_announcement(mac_address=mac)
    upsert_announcement(mac_address=mac)
    third = upsert_announcement(mac_address=mac)
    assert third["status"] == "pending"
    rows = [a for a in list_announcements(include_consumed=True)
            if a["mac_address"] == mac]
    assert len(rows) == 1, "announce upserts one row per MAC"
    assert rows[0]["announce_count"] == 3


# ── adopted ────────────────────────────────────────────────────────────

def test_adopt_then_announce_delivers_the_token(hub_db):
    mac = "AA:BB:CC:00:00:04"
    upsert_announcement(mac_address=mac)
    adopt(_announcement_id(mac), by_user_id=None)
    resp = upsert_announcement(mac_address=mac)
    assert resp["status"] == "adopted"
    assert resp["enrollment_token"].startswith("et_")
    assert resp["central_register_url"].endswith("/api/v1/device/register")
    assert resp["retry_after_seconds"] == 0


def test_token_is_redeliverable_until_register(hub_db):
    """v0.5.68 P-REG fix: the adoption token is re-delivered on every
    poll until the device registers — a repeat announce is `adopted`
    again, NOT `awaiting_register`."""
    mac = "AA:BB:CC:00:00:05"
    upsert_announcement(mac_address=mac)
    adopt(_announcement_id(mac), by_user_id=None)
    first = upsert_announcement(mac_address=mac)
    second = upsert_announcement(mac_address=mac)
    assert first["status"] == "adopted"
    assert second["status"] == "adopted"
    assert second["enrollment_token"] == first["enrollment_token"]


def test_adopt_is_idempotent(hub_db):
    mac = "AA:BB:CC:00:00:06"
    upsert_announcement(mac_address=mac)
    aid = _announcement_id(mac)
    first = adopt(aid, by_user_id=None)
    second = adopt(aid, by_user_id=None)
    # No second token minted — same enrollment_token_id.
    assert first["enrollment_token_id"] == second["enrollment_token_id"]


# ── registered ─────────────────────────────────────────────────────────

def test_registered_after_token_consumed(hub_db):
    mac = "AA:BB:CC:00:00:07"
    upsert_announcement(mac_address=mac)
    adopt(_announcement_id(mac), by_user_id=None)
    upsert_announcement(mac_address=mac)  # delivers the token
    mark_consumed(mac)  # device completed /register
    resp = upsert_announcement(mac_address=mac)
    assert resp["status"] == "registered"


# ── rejected ───────────────────────────────────────────────────────────

def test_rejected_announce_backs_off_an_hour(hub_db):
    mac = "AA:BB:CC:00:00:08"
    upsert_announcement(mac_address=mac)
    reject(_announcement_id(mac), by_user_id=None)
    resp = upsert_announcement(mac_address=mac)
    assert resp["status"] == "rejected"
    assert resp["retry_after_seconds"] == 3600


# ── validation ─────────────────────────────────────────────────────────

def test_empty_mac_rejected(hub_db):
    with pytest.raises(AnnouncementError):
        upsert_announcement(mac_address="")


def test_malformed_mac_rejected(hub_db):
    with pytest.raises(AnnouncementError):
        upsert_announcement(mac_address="not-a-valid-mac ###")
