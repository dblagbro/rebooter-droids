"""Unit tests for #211 BUG-066 — audit diff contains old/new.

The 0.6.43 fix wraps `update_device` with `update_device_with_diff`
which returns a per-field `{old, new}` diff alongside the updated
dict. The blueprint handler writes that diff into the audit row's
`details.diff` field.
"""
from __future__ import annotations

import pytest

from app.services.devices import (
    update_device,
    update_device_with_diff,
)


def _create_device(*, display_name: str, notes: str | None = None) -> str:
    from datetime import datetime, timezone
    from app.models import Device
    from app.db import session_scope

    with session_scope() as s:
        d = Device(
            display_name=display_name,
            notes=notes,
            registration_state="active",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        s.add(d)
        s.flush()
        return d.id


@pytest.mark.usefixtures("hub_db")
def test_diff_captures_display_name_change(hub_db):
    a = _create_device(display_name="Original")
    updated, diff = update_device_with_diff(a, {"display_name": "Renamed"})
    assert updated is not None
    assert updated["display_name"] == "Renamed"
    assert "display_name" in diff
    assert diff["display_name"]["old"] == "Original"
    assert diff["display_name"]["new"] == "Renamed"


@pytest.mark.usefixtures("hub_db")
def test_diff_empty_when_nothing_changes(hub_db):
    """Patch with the SAME value as current — diff must be empty (no
    audit entry should claim a change happened)."""
    a = _create_device(display_name="Stable")
    _, diff = update_device_with_diff(a, {"display_name": "Stable"})
    assert diff == {}


@pytest.mark.usefixtures("hub_db")
def test_diff_captures_power_source_change(hub_db):
    """The motivating case: operator changes the parent and the audit
    row must show what it was BEFORE so the reboot classifier knows
    when the topology flipped."""
    a = _create_device(display_name="A")
    b = _create_device(display_name="B")
    update_device(a, {"power_source_device_id": b})
    # Now clear it. Diff must show old=b, new=None.
    _, diff = update_device_with_diff(a, {"power_source_device_id": None})
    assert "power_source_device_id" in diff
    assert diff["power_source_device_id"]["old"] == b
    assert diff["power_source_device_id"]["new"] is None


@pytest.mark.usefixtures("hub_db")
def test_diff_multi_field_atomic(hub_db):
    """Two fields changed in one patch — both must appear in the diff."""
    a = _create_device(display_name="A", notes="old notes")
    updated, diff = update_device_with_diff(
        a,
        {"display_name": "Renamed A", "notes": "new notes"},
    )
    assert updated is not None
    assert set(diff.keys()) == {"display_name", "notes"}
    assert diff["display_name"]["old"] == "A"
    assert diff["display_name"]["new"] == "Renamed A"
    assert diff["notes"]["old"] == "old notes"
    assert diff["notes"]["new"] == "new notes"


@pytest.mark.usefixtures("hub_db")
def test_diff_returns_empty_for_missing_device(hub_db):
    updated, diff = update_device_with_diff("dev_doesnotexist", {"notes": "x"})
    assert updated is None
    assert diff == {}
