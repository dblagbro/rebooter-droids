"""Unit tests for #211 BUG-067 — parent-delete enumerates children.

The 0.6.43 fix adds `delete_device_with_audit_context` which captures
the list of children whose `power_source_device_id` is about to go
NULL (via `ON DELETE SET NULL`) BEFORE the cascade fires. The
blueprint handler logs the list in the `device.deleted` audit row +
writes per-child `device.power_source_cleared_by_parent_delete` rows.
"""
from __future__ import annotations

import pytest

from app.services.devices import (
    delete_device_with_audit_context,
    update_device,
)


def _create_device(*, display_name: str) -> str:
    from datetime import datetime, timezone
    from app.models import Device
    from app.db import session_scope

    with session_scope() as s:
        d = Device(
            display_name=display_name,
            registration_state="active",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        s.add(d)
        s.flush()
        return d.id


@pytest.mark.usefixtures("hub_db")
def test_orphaned_children_captured_in_outcome(hub_db):
    """A powers B and C. Delete A. Outcome enumerates both B and C
    with their display_names."""
    a = _create_device(display_name="Parent")
    b = _create_device(display_name="Child B")
    c = _create_device(display_name="Child C")
    update_device(b, {"power_source_device_id": a})
    update_device(c, {"power_source_device_id": a})
    outcome = delete_device_with_audit_context(a)
    assert outcome is not None
    assert outcome["deleted_id"] == a
    ids = {child["id"] for child in outcome["orphaned_children"]}
    assert ids == {b, c}
    names = {child["display_name"] for child in outcome["orphaned_children"]}
    assert names == {"Child B", "Child C"}


@pytest.mark.usefixtures("hub_db")
def test_no_children_returns_empty_list(hub_db):
    """Deleting a device with no children produces an empty
    orphaned_children list — not None, not an error."""
    a = _create_device(display_name="Solo")
    outcome = delete_device_with_audit_context(a)
    assert outcome is not None
    assert outcome["deleted_id"] == a
    assert outcome["orphaned_children"] == []


@pytest.mark.usefixtures("hub_db")
def test_missing_device_returns_none(hub_db):
    outcome = delete_device_with_audit_context("dev_doesnotexist")
    assert outcome is None


@pytest.mark.usefixtures("hub_db")
def test_children_actually_orphaned_after_delete(hub_db):
    """Sanity: after the parent is deleted, the FK cascade really did
    set the child's power_source_device_id to NULL. This isn't part of
    the audit shape but it's the underlying mechanism the fix sits on."""
    from app.models import Device
    from app.db import session_scope

    a = _create_device(display_name="Parent")
    b = _create_device(display_name="Child")
    update_device(b, {"power_source_device_id": a})
    delete_device_with_audit_context(a)
    with session_scope() as s:
        child = s.get(Device, b)
        assert child is not None  # child still exists
        assert child.power_source_device_id is None  # but orphaned
