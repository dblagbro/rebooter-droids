"""Regression tests — S1-7 duplicate Device rows for one MAC.

The `.69` symptom: a device that re-registered through the fresh-adopt
path (rather than Restore) produced a *second* Device row for the same
physical MAC.

Two fixes:
1. `consume_enrollment_token` now refuses the fresh-adopt branch when an
   `active` Device already exists for the incoming MAC, with a clear
   error pointing the operator at the Restore path.
2. `merge_retire_device` lets an operator consolidate two existing
   duplicate rows: keep one, decommission (NOT delete) the other.

DB-backed → the `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import Device
from app.services.devices import (
    MergeRetireError,
    find_by_mac,
    merge_retire_device,
)
from app.services.enrollment import (
    EnrollmentError,
    consume_enrollment_token,
    mint_enrollment_token,
)


# ── consume_enrollment_token refuses a duplicate fresh-adopt ─────────────

def test_fresh_adopt_refuses_when_active_device_with_mac_exists(hub_db):
    mac = "AA:BB:CC:DD:EE:69"
    # First registration creates the device.
    _, secret1 = mint_enrollment_token(hub_db, issued_by_user_id=None)
    device, _ = consume_enrollment_token(
        secret1, {"display_name": "Plug 69", "mac_address": mac}
    )
    assert device.registration_state == "active"

    # A second fresh-adopt token for the SAME MAC must be refused.
    _, secret2 = mint_enrollment_token(hub_db, issued_by_user_id=None)
    with pytest.raises(EnrollmentError) as exc:
        consume_enrollment_token(
            secret2, {"display_name": "Plug 69 dup", "mac_address": mac}
        )
    assert exc.value.code == "device_already_registered"

    # No duplicate row was created.
    with session_scope() as session:
        rows = list(session.scalars(select(Device)))
    assert len(rows) == 1


def test_fresh_adopt_without_mac_still_allowed(hub_db):
    # A token with no MAC in the payload can't dup-check — still allowed.
    _, secret = mint_enrollment_token(hub_db, issued_by_user_id=None)
    device, _ = consume_enrollment_token(secret, {"display_name": "No MAC"})
    assert device.registration_state == "active"


def test_fresh_adopt_allowed_when_existing_row_decommissioned(hub_db):
    mac = "AA:BB:CC:DD:EE:70"
    _, secret1 = mint_enrollment_token(hub_db, issued_by_user_id=None)
    device, _ = consume_enrollment_token(
        secret1, {"display_name": "Old", "mac_address": mac}
    )
    # Decommission the first row — a fresh adopt is then legitimate.
    with session_scope() as session:
        d = session.get(Device, device.id)
        d.registration_state = "decommissioned"
        session.flush()

    _, secret2 = mint_enrollment_token(hub_db, issued_by_user_id=None)
    new_device, _ = consume_enrollment_token(
        secret2, {"display_name": "New", "mac_address": mac}
    )
    assert new_device.registration_state == "active"
    assert new_device.id != device.id


# ── merge_retire_device ─────────────────────────────────────────────────

def _make_device(mac: str, name: str, state: str = "active") -> str:
    with session_scope() as session:
        d = Device(display_name=name, mac_address=mac, registration_state=state)
        session.add(d)
        session.flush()
        return d.id


def test_merge_retire_decommissions_the_retire_row(hub_db):
    mac = "11:22:33:44:55:66"
    keep = _make_device(mac, "keep")
    retire = _make_device(mac, "retire")

    result = merge_retire_device(keep, retire)
    assert result["keep_device_id"] == keep
    assert result["retire_device_id"] == retire

    with session_scope() as session:
        assert session.get(Device, keep).registration_state == "active"
        # Retired row is decommissioned, NOT deleted — still present.
        retired_row = session.get(Device, retire)
        assert retired_row is not None
        assert retired_row.registration_state == "decommissioned"


def test_merge_retire_drops_retired_row_from_find_by_mac(hub_db):
    mac = "11:22:33:44:55:77"
    keep = _make_device(mac, "keep")
    retire = _make_device(mac, "retire")
    assert len(find_by_mac(mac)) == 2
    merge_retire_device(keep, retire)
    # find_by_mac excludes decommissioned rows.
    survivors = find_by_mac(mac)
    assert [d["id"] for d in survivors] == [keep]


def test_merge_retire_refuses_mac_mismatch(hub_db):
    keep = _make_device("AA:AA:AA:AA:AA:AA", "keep")
    retire = _make_device("BB:BB:BB:BB:BB:BB", "retire")
    with pytest.raises(MergeRetireError) as exc:
        merge_retire_device(keep, retire)
    assert exc.value.code == "mac_mismatch"


def test_merge_retire_refuses_same_device(hub_db):
    d = _make_device("CC:CC:CC:CC:CC:CC", "self")
    with pytest.raises(MergeRetireError) as exc:
        merge_retire_device(d, d)
    assert exc.value.code == "validation_failed"


def test_merge_retire_unknown_device_raises(hub_db):
    keep = _make_device("DD:DD:DD:DD:DD:DD", "keep")
    with pytest.raises(MergeRetireError) as exc:
        merge_retire_device(keep, "dev_does-not-exist")
    assert exc.value.code == "not_found"


def test_merge_retire_already_retired_raises(hub_db):
    mac = "EE:EE:EE:EE:EE:EE"
    keep = _make_device(mac, "keep")
    retire = _make_device(mac, "retire", state="decommissioned")
    with pytest.raises(MergeRetireError) as exc:
        merge_retire_device(keep, retire)
    assert exc.value.code == "already_retired"
