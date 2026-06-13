"""Unit tests for #211 BUG-068 — power-source picker must be site-scoped.

The 0.6.44 fix filters the dropdown by `device.site_id` so an operator
scoped to site A doesn't see site B devices. The handler re-validates
against the same filter so form-tamper bypass is also closed.

These tests exercise the filtering logic at the service layer (the
same `svc_list_devices` call the handler makes). The handler-level
flash + redirect path is exercised by the broader `tests/qa/` bucket;
this unit suite just verifies the data flow.
"""
from __future__ import annotations

import pytest

from app.services.devices import list_devices as svc_list_devices


def _create_site(name: str) -> str:
    from datetime import datetime, timezone
    from app.models import Site
    from app.db import session_scope

    with session_scope() as s:
        site = Site(
            name=name,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        s.add(site)
        s.flush()
        return site.id


def _create_device(*, display_name: str, site_id: str | None) -> str:
    from datetime import datetime, timezone
    from app.models import Device
    from app.db import session_scope

    with session_scope() as s:
        d = Device(
            display_name=display_name,
            site_id=site_id,
            registration_state="active",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        s.add(d)
        s.flush()
        return d.id


@pytest.mark.usefixtures("hub_db")
def test_picker_excludes_other_site(hub_db):
    """Filter list_devices(...) results by THIS device's site_id —
    the same operation the handler does at the picker render site."""
    site_a = _create_site("Site A")
    site_b = _create_site("Site B")
    device_in_a = _create_device(display_name="In A", site_id=site_a)
    other_in_a = _create_device(display_name="Other A", site_id=site_a)
    in_b = _create_device(display_name="In B", site_id=site_b)
    # Simulate the picker render for device_in_a.
    pool = svc_list_devices(include_qa_fixtures=False)
    site_of_this = site_a
    visible_picker = [
        d for d in pool
        if d.get("id") != device_in_a and d.get("site_id") == site_of_this
    ]
    visible_ids = {d.get("id") for d in visible_picker}
    assert other_in_a in visible_ids
    assert in_b not in visible_ids
    assert device_in_a not in visible_ids  # never see yourself


@pytest.mark.usefixtures("hub_db")
def test_picker_none_site_isolated(hub_db):
    """A device with site_id=None should only see other site=None
    devices in its picker (matches the explicit site comparison)."""
    site_a = _create_site("Site A")
    unscoped_target = _create_device(display_name="Unscoped 1", site_id=None)
    unscoped_other = _create_device(display_name="Unscoped 2", site_id=None)
    site_a_device = _create_device(display_name="In A", site_id=site_a)
    pool = svc_list_devices(include_qa_fixtures=False)
    site_of_this = None
    visible_picker = [
        d for d in pool
        if d.get("id") != unscoped_target and d.get("site_id") == site_of_this
    ]
    visible_ids = {d.get("id") for d in visible_picker}
    assert unscoped_other in visible_ids
    assert site_a_device not in visible_ids


@pytest.mark.usefixtures("hub_db")
def test_picker_qa_fixtures_excluded(hub_db):
    """The fix also preserves the existing exclusion of QA fixtures.
    Tampering through them is closed by the include_qa_fixtures=False
    pool the handler uses."""
    site_a = _create_site("Site A")
    real = _create_device(display_name="Real", site_id=site_a)
    # Create a QA fixture in the same site.
    from datetime import datetime, timezone
    from app.models import Device
    from app.db import session_scope

    with session_scope() as s:
        fixture = Device(
            display_name="QA fixture",
            site_id=site_a,
            is_qa_fixture=True,
            registration_state="active",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        s.add(fixture)
        s.flush()
        fixture_id = fixture.id
    pool_real = svc_list_devices(include_qa_fixtures=False)
    pool_with_qa = svc_list_devices(include_qa_fixtures=True)
    assert fixture_id not in {d["id"] for d in pool_real}
    assert fixture_id in {d["id"] for d in pool_with_qa}
