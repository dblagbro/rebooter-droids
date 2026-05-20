"""Regression tests — S1-6 announce/register MAC-format mismatch.

Root cause: `mark_consumed` matched the announce row against the
/register MAC with an exact-string `==`. When a device announced with
one MAC format (e.g. `AA:BB:CC:DD:EE:FF`) and later registered with
another (`aabbccddeeff` / `AABB.CCDD.EEFF`), the lookup missed the row,
so `consumed_at` was never stamped and the announcement stayed stuck in
`awaiting_register` forever (the `.225` symptom).

Fix: `_normalize_mac()` canonicalises the MAC — uppercase, separators
and whitespace stripped — applied on write in `upsert_announcement` and
on BOTH sides of the comparison in `mark_consumed`. Plus a
`force_adopt_reconcile` operator action to clear rows that were already
stranded by the pre-fix behaviour.

DB-backed → the `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

import pytest

from app.db import session_scope
from app.models import Device
from app.services.announcements import (
    AnnouncementError,
    _normalize_mac,
    adopt,
    force_adopt_reconcile,
    list_announcements,
    mark_consumed,
    upsert_announcement,
)


# ── _normalize_mac ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("AA:BB:CC:DD:EE:FF", "AABBCCDDEEFF"),
        ("aa:bb:cc:dd:ee:ff", "AABBCCDDEEFF"),
        ("AA-BB-CC-DD-EE-FF", "AABBCCDDEEFF"),
        ("AABB.CCDD.EEFF", "AABBCCDDEEFF"),
        ("  aabbccddeeff  ", "AABBCCDDEEFF"),
        ("aa bb cc dd ee ff", "AABBCCDDEEFF"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_mac_canonicalises(raw, expected):
    assert _normalize_mac(raw) == expected


# ── upsert_announcement stores canonical form ───────────────────────────

def test_announce_stores_normalized_mac(hub_db):
    upsert_announcement(mac_address="AA:BB:CC:11:22:33")
    rows = list_announcements(include_consumed=True)
    assert len(rows) == 1
    assert rows[0]["mac_address"] == "AABBCC112233"


def test_announce_same_mac_different_format_upserts_one_row(hub_db):
    # Same physical device, two textual MAC formats — must be one row.
    upsert_announcement(mac_address="AA:BB:CC:44:55:66")
    upsert_announcement(mac_address="aabbcc445566")
    upsert_announcement(mac_address="AABB.CC44.5566")
    rows = list_announcements(include_consumed=True)
    assert len(rows) == 1
    assert rows[0]["announce_count"] == 3


# ── mark_consumed cross-links despite format mismatch ───────────────────

def test_mark_consumed_matches_across_mac_formats(hub_db):
    """The core S1-6 bug: announce with colons, register without."""
    upsert_announcement(mac_address="AA:BB:CC:77:88:99")
    aid = list_announcements(include_consumed=True)[0]["id"]
    adopt(aid, by_user_id=None)
    upsert_announcement(mac_address="AA:BB:CC:77:88:99")  # delivers token

    # Device registers reporting the MAC in a *different* format.
    mark_consumed("aabbcc778899")

    row = list_announcements(include_consumed=True)[0]
    assert row["consumed_at"] is not None, "consumed_at must be stamped"
    assert row["state"] == "registered"


def test_mark_consumed_matches_legacy_unnormalized_row(hub_db):
    """A row written before on-write normalisation (separators still
    present) must still cross-link via the normalised slow-path scan."""
    upsert_announcement(mac_address="DD:EE:FF:00:11:22")
    # Simulate a legacy row: rewrite the stored MAC with separators.
    with session_scope() as session:
        from app.models import DeviceAnnouncement
        from sqlalchemy import select

        legacy = session.scalar(select(DeviceAnnouncement))
        legacy.mac_address = "DD-EE-FF-00-11-22"
        session.flush()

    mark_consumed("ddeeff001122")
    row = list_announcements(include_consumed=True)[0]
    assert row["consumed_at"] is not None


# ── force_adopt_reconcile ───────────────────────────────────────────────

def _make_active_device(mac: str) -> str:
    with session_scope() as session:
        d = Device(
            display_name="stuck device",
            mac_address=mac,
            registration_state="active",
        )
        session.add(d)
        session.flush()
        return d.id


def test_force_adopt_reconciles_stuck_row_against_active_device(hub_db):
    upsert_announcement(mac_address="AA:00:00:00:00:01")
    aid = list_announcements(include_consumed=True)[0]["id"]
    adopt(aid, by_user_id=None)
    upsert_announcement(mac_address="AA:00:00:00:00:01")  # awaiting_register

    # An active Device with the same MAC already exists — the device
    # registered, the cross-link was just missed.
    dev_id = _make_active_device("aa0000000001")

    result = force_adopt_reconcile(aid)
    assert result["consumed_at"] is not None
    assert result["state"] == "registered"
    assert result["reconciled_device_id"] == dev_id


def test_force_adopt_refuses_when_no_active_device(hub_db):
    upsert_announcement(mac_address="BB:00:00:00:00:02")
    aid = list_announcements(include_consumed=True)[0]["id"]
    with pytest.raises(AnnouncementError) as exc:
        force_adopt_reconcile(aid)
    assert exc.value.code == "no_active_device"


def test_force_adopt_refuses_already_consumed(hub_db):
    upsert_announcement(mac_address="CC:00:00:00:00:03")
    aid = list_announcements(include_consumed=True)[0]["id"]
    adopt(aid, by_user_id=None)
    upsert_announcement(mac_address="CC:00:00:00:00:03")
    mark_consumed("CC:00:00:00:00:03")
    with pytest.raises(AnnouncementError) as exc:
        force_adopt_reconcile(aid)
    assert exc.value.code == "already_consumed"


def test_force_adopt_unknown_announcement_raises(hub_db):
    with pytest.raises(AnnouncementError) as exc:
        force_adopt_reconcile("ann_does-not-exist")
    assert exc.value.code == "not_found"
