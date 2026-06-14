"""Unit tests for #201 next-tier fixes (BUG-072..076).

Coverage:
- BUG-072: empty display_name MUST NOT blank the stored value.
- BUG-073: site_id validator rejects non-existent site with
           typed SiteScopeError instead of bare IntegrityError.
- BUG-076: update_device_with_diff shares one session_scope with
           the validators (and returns the diff against the
           normalized value, not the raw input).

BUG-074 (schedules) and BUG-075 (helper extraction) are exercised
in the QA / integration suites where the Flask request context is
available; this file covers the service-layer surface only.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.devices import (
    SiteScopeError,
    update_device,
    update_device_with_diff,
)


def _create_site(name: str) -> str:
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


def _create_device(*, display_name: str, site_id: str | None = None) -> str:
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


# ─── BUG-072 ────────────────────────────────────────────────────────────────

@pytest.mark.usefixtures("hub_db")
def test_update_device_does_not_blank_name_when_display_name_omitted():
    """0.6.47 BUG-072 fix is at the handler layer (it pops the key from
    patch when the cleaned name is empty). At the SERVICE layer the
    contract is: omitting `display_name` from the patch dict must leave
    the stored value intact. Pre-fix the handler always included it.
    """
    did = _create_device(display_name="original")
    updated = update_device(did, {"notes": "edit"})
    assert updated["display_name"] == "original"
    assert updated["notes"] == "edit"


@pytest.mark.usefixtures("hub_db")
def test_update_device_preserves_name_when_other_fields_change():
    """Sanity: a partial patch that touches site_id but not
    display_name preserves the name, even when site_id is normalized
    by the validator."""
    site_a = _create_site("A")
    did = _create_device(display_name="keepme", site_id=None)
    updated = update_device(did, {"site_id": site_a})
    assert updated["display_name"] == "keepme"
    assert updated["site_id"] == site_a


# ─── BUG-073 ────────────────────────────────────────────────────────────────

@pytest.mark.usefixtures("hub_db")
def test_update_device_rejects_unknown_site_id():
    """A stale dropdown / form-tamper submits a deleted-site UUID.
    Pre-fix _accept_any passed it through → IntegrityError → 500.
    Now SiteScopeError fires before any setattr."""
    did = _create_device(display_name="rebooter")
    with pytest.raises(SiteScopeError) as e:
        update_device(did, {"site_id": "ste_DOES_NOT_EXIST"})
    assert e.value.subtype == "missing"


@pytest.mark.usefixtures("hub_db")
def test_update_device_accepts_real_site_id():
    site_a = _create_site("Real Site")
    did = _create_device(display_name="rebooter")
    updated = update_device(did, {"site_id": site_a})
    assert updated["site_id"] == site_a


@pytest.mark.usefixtures("hub_db")
def test_update_device_accepts_clearing_site_id():
    site_a = _create_site("A")
    did = _create_device(display_name="rebooter", site_id=site_a)
    # Clearing the site via None or empty string is legitimate.
    updated = update_device(did, {"site_id": None})
    assert updated["site_id"] is None


@pytest.mark.usefixtures("hub_db")
def test_update_device_with_diff_surfaces_site_scope_error():
    """The diff variant must propagate SiteScopeError, not 'eat' it
    inside the snapshot scope. Pre-fix this was two separate sessions
    so an exception in update_device could in principle leave the
    snapshot session dangling; with the BUG-076 restructure both run
    in one scope."""
    did = _create_device(display_name="rebooter")
    with pytest.raises(SiteScopeError):
        update_device_with_diff(did, {"site_id": "ste_NOPE"})


# ─── BUG-076 ────────────────────────────────────────────────────────────────

@pytest.mark.usefixtures("hub_db")
def test_update_device_with_diff_single_session_no_orphan_snapshot():
    """The diff captures the pre-mutation value of every patched key
    that changed. The BUG-076 restructure put the snapshot inside the
    same session_scope as the mutation; the visible contract is
    unchanged but the wire-level cost is halved (one round-trip, not
    two). This test pins the contract."""
    site_a = _create_site("A")
    site_b = _create_site("B")
    did = _create_device(display_name="rebooter", site_id=site_a)
    updated, diff = update_device_with_diff(did, {"site_id": site_b})
    assert updated["site_id"] == site_b
    assert diff == {"site_id": {"old": site_a, "new": site_b}}


@pytest.mark.usefixtures("hub_db")
def test_update_device_with_diff_omits_unchanged_fields():
    site_a = _create_site("A")
    did = _create_device(display_name="rebooter", site_id=site_a)
    # site_id stays the same; only notes changes.
    updated, diff = update_device_with_diff(
        did, {"site_id": site_a, "notes": "new note"}
    )
    assert "site_id" not in diff
    assert diff["notes"]["old"] is None
    assert diff["notes"]["new"] == "new note"
    assert updated["notes"] == "new note"
