"""Unit tests for #210 power-topology guards (BUG-062, BUG-063).

The 0.6.40 fix adds three guards in `update_device`:
  - self-parent (A → A) rejected
  - cycle (A → B → A) rejected
  - parent_missing (FK target doesn't exist) rejected

Each surfaces as a typed `PowerTopologyError` with `.subtype` set to
the failure class. The blueprint handler catches and flashes; without
these guards the FK IntegrityError bubbled to a 500 (BUG-063) and any
cycle silently persisted (BUG-062).
"""
from __future__ import annotations

import pytest

from app.services.devices import (
    PowerTopologyError,
    update_device,
)


def _create_device(hub_db, *, display_name: str, site_id: str | None = None) -> str:
    """Insert a Device row through SQLAlchemy and return its id."""
    from datetime import datetime, timezone
    from app.models import Device
    from app.db import session_scope

    with session_scope() as s:
        d = Device(
            display_name=display_name,
            registration_state="active",
            site_id=site_id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        s.add(d)
        s.flush()
        return d.id


@pytest.mark.usefixtures("hub_db")
def test_self_parent_rejected(hub_db):
    """A → A makes no physical sense and would short-circuit the
    reboot-classifier walk that uses this relationship."""
    a = _create_device(hub_db, display_name="A")
    with pytest.raises(PowerTopologyError) as excinfo:
        update_device(a, {"power_source_device_id": a})
    assert excinfo.value.subtype == "self_parent"


@pytest.mark.usefixtures("hub_db")
def test_cycle_rejected(hub_db):
    """A → B is fine. Then B → A would close the cycle — must reject
    so the chain has a definite root (the wall outlet)."""
    a = _create_device(hub_db, display_name="A")
    b = _create_device(hub_db, display_name="B")
    update_device(a, {"power_source_device_id": b})  # A draws from B — OK
    with pytest.raises(PowerTopologyError) as excinfo:
        update_device(b, {"power_source_device_id": a})  # would close cycle
    assert excinfo.value.subtype == "cycle"


@pytest.mark.usefixtures("hub_db")
def test_longer_cycle_rejected(hub_db):
    """A → B → C → A — the walk must follow > 1 hop."""
    a = _create_device(hub_db, display_name="A")
    b = _create_device(hub_db, display_name="B")
    c = _create_device(hub_db, display_name="C")
    update_device(a, {"power_source_device_id": b})
    update_device(b, {"power_source_device_id": c})
    with pytest.raises(PowerTopologyError) as excinfo:
        update_device(c, {"power_source_device_id": a})  # closes 3-hop cycle
    assert excinfo.value.subtype == "cycle"


@pytest.mark.usefixtures("hub_db")
def test_parent_missing_rejected(hub_db):
    """Form-tamper supplies a device id that doesn't exist. Pre-fix
    this fell through to the FK IntegrityError → Flask 500. Now: typed
    PowerTopologyError → blueprint flashes friendly message."""
    a = _create_device(hub_db, display_name="A")
    with pytest.raises(PowerTopologyError) as excinfo:
        update_device(a, {"power_source_device_id": "dev_doesnotexist"})
    assert excinfo.value.subtype == "parent_missing"


@pytest.mark.usefixtures("hub_db")
def test_clearing_parent_with_none_succeeds(hub_db):
    """Setting parent to None is the "make this device independent"
    operation. Must NOT raise (no guards apply to a clear)."""
    a = _create_device(hub_db, display_name="A")
    b = _create_device(hub_db, display_name="B")
    update_device(a, {"power_source_device_id": b})
    # Now clear A's parent — should succeed.
    updated = update_device(a, {"power_source_device_id": None})
    assert updated is not None
    assert updated.get("power_source_device_id") is None


@pytest.mark.usefixtures("hub_db")
def test_valid_parent_chain_succeeds(hub_db):
    """A → B and B → C (a non-circular chain) is the legitimate use
    case — the brand-new feature must still let the operator build
    this topology."""
    a = _create_device(hub_db, display_name="A")
    b = _create_device(hub_db, display_name="B")
    c = _create_device(hub_db, display_name="C")
    update_device(b, {"power_source_device_id": c})
    update_device(a, {"power_source_device_id": b})
    # Verify both saved.
    from app.models import Device
    from app.db import session_scope
    with session_scope() as s:
        assert s.get(Device, a).power_source_device_id == b
        assert s.get(Device, b).power_source_device_id == c
