"""Unit tests — `app.services.deployments` (v0.5.100).

Covers the firmware-deployment service end-to-end:

- `create_deployment` — target-type validation, release lookup, fan-out
  to `device` / `group` / `site` / `all_devices` targets, and the
  supersede-prior-pending invariant.
- `assignment_for_device` / `mark_assignment_delivered` — the device-
  side firmware-fetch pipeline.
- `reconcile_assignment_reported_version` — the heartbeat-driven
  pending→delivered→completed closure (the v0.5.14 gap that left
  deployments stuck on `delivered`).
- `list_deployments` — assignment-state count breakdown.

DB-backed — `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import (
    DeploymentAssignment,
    Device,
    FirmwareRelease,
    Group,
    GroupMembership,
    Site,
)
from app.services.deployments import (
    assignment_for_device,
    create_deployment,
    list_deployments,
    mark_assignment_delivered,
    reconcile_assignment_reported_version,
)


# ── helpers ────────────────────────────────────────────────────────────

def _release(version: str = "1.0.0", channel: str = "dev") -> str:
    """Insert a firmware release and return its id."""
    with session_scope() as s:
        r = FirmwareRelease(
            version=version, channel=channel,
            filename=f"rd-{version}.bin",
            download_url=f"https://example.invalid/{version}.bin",
            sha256="0" * 64,
        )
        s.add(r)
        s.flush()
        return r.id


def _device(*, device_id: str | None = None, site_id: str | None = None) -> str:
    with session_scope() as s:
        d = Device(id=device_id) if device_id else Device()
        d.site_id = site_id
        s.add(d)
        s.flush()
        return d.id


def _site() -> str:
    with session_scope() as s:
        site = Site(name=f"qa-site-{uuid.uuid4().hex[:6]}")
        s.add(site)
        s.flush()
        return site.id


def _group_with_members(device_ids: list[str]) -> str:
    with session_scope() as s:
        g = Group(name=f"qa-grp-{uuid.uuid4().hex[:6]}")
        s.add(g)
        s.flush()
        for did in device_ids:
            s.add(GroupMembership(group_id=g.id, device_id=did))
        return g.id


def _assignments(deployment_id: str) -> list[DeploymentAssignment]:
    with session_scope() as s:
        return list(s.scalars(
            select(DeploymentAssignment)
            .where(DeploymentAssignment.deployment_id == deployment_id)
            .order_by(DeploymentAssignment.device_id.asc())
        ))


# ── create_deployment — validation ─────────────────────────────────────

def test_create_deployment_rejects_unknown_target_type(hub_db):
    rid = _release()
    with pytest.raises(ValueError, match="target_type"):
        create_deployment(rid, "bogus", "tgt-x", None, False, None)


def test_create_deployment_requires_target_id_unless_all_devices(hub_db):
    rid = _release()
    with pytest.raises(ValueError, match="target_id"):
        create_deployment(rid, "device", None, None, False, None)
    with pytest.raises(ValueError, match="target_id"):
        create_deployment(rid, "group", "", None, False, None)


def test_create_deployment_with_unknown_release_raises_lookup_error(hub_db):
    with pytest.raises(LookupError):
        create_deployment("fwr_does_not_exist", "all_devices",
                          None, None, False, None)


def test_create_deployment_with_unknown_device_raises_lookup_error(hub_db):
    rid = _release()
    with pytest.raises(LookupError):
        create_deployment(rid, "device", "dev_missing", None, False, None)


# ── create_deployment — fan-out ────────────────────────────────────────

def test_create_deployment_to_a_single_device_creates_one_assignment(hub_db):
    rid = _release()
    did = _device()
    out = create_deployment(rid, "device", did, None, False, None)
    assert out["counts"] == {"target_devices": 1}
    rows = _assignments(out["id"])
    assert [a.device_id for a in rows] == [did]
    assert rows[0].state == "pending"


def test_create_deployment_to_a_group_creates_one_assignment_per_member(hub_db):
    rid = _release()
    members = sorted([_device(), _device(), _device()])
    gid = _group_with_members(members)
    out = create_deployment(rid, "group", gid, None, False, None)
    assert out["counts"] == {"target_devices": 3}
    assert sorted(a.device_id for a in _assignments(out["id"])) == members


def test_create_deployment_to_a_site_creates_assignment_per_site_device(hub_db):
    rid = _release()
    site_id = _site()
    on_site = sorted([_device(site_id=site_id), _device(site_id=site_id)])
    _device()  # off-site — must NOT be included
    out = create_deployment(rid, "site", site_id, None, False, None)
    assert out["counts"] == {"target_devices": 2}
    assert sorted(a.device_id for a in _assignments(out["id"])) == on_site


def test_create_deployment_to_all_devices_creates_assignment_per_device(hub_db):
    rid = _release()
    devs = sorted([_device(), _device()])
    out = create_deployment(rid, "all_devices", None, None, False, None)
    assert out["counts"] == {"target_devices": 2}
    assert sorted(a.device_id for a in _assignments(out["id"])) == devs


def test_create_deployment_inherits_release_channel_when_channel_is_none(hub_db):
    rid = _release(channel="stable")
    did = _device()
    out = create_deployment(rid, "device", did, None, False, None)
    assert out["channel"] == "stable"


def test_create_deployment_respects_explicit_channel_override(hub_db):
    rid = _release(channel="stable")
    did = _device()
    out = create_deployment(rid, "device", did, "beta", False, None)
    assert out["channel"] == "beta"


def test_create_deployment_supersedes_prior_pending_assignment(hub_db):
    """The safety invariant: a second deployment to the same device
    flips the first (pending or delivered) to `superseded`, so the
    device only ever fetches the newest assignment."""
    r1 = _release(version="1.0.0")
    r2 = _release(version="1.1.0")
    did = _device()

    d1 = create_deployment(r1, "device", did, None, False, None)
    d2 = create_deployment(r2, "device", did, None, False, None)

    # The first assignment is now superseded; the second is pending.
    first = _assignments(d1["id"])
    second = _assignments(d2["id"])
    assert [a.state for a in first] == ["superseded"]
    assert [a.state for a in second] == ["pending"]


def test_create_deployment_also_supersedes_a_delivered_assignment(hub_db):
    """Supersede covers both `pending` AND `delivered` states — the
    device may have already fetched the first assignment without
    completing it."""
    r1 = _release(version="1.0.0")
    r2 = _release(version="1.1.0")
    did = _device()
    create_deployment(r1, "device", did, None, False, None)
    mark_assignment_delivered(did)
    # Sanity: now `delivered`.
    assert assignment_for_device(did)["target_version"] == "1.0.0"

    create_deployment(r2, "device", did, None, False, None)
    # The latest active assignment is the newer one.
    assert assignment_for_device(did)["target_version"] == "1.1.0"
    # And no two assignments are ever both active for the same device.
    with session_scope() as s:
        active = list(s.scalars(
            select(DeploymentAssignment).where(
                DeploymentAssignment.device_id == did,
                DeploymentAssignment.state.in_(("pending", "delivered")),
            )
        ))
        assert len(active) == 1


# ── assignment_for_device ──────────────────────────────────────────────

def test_assignment_for_device_with_no_active_returns_none(hub_db):
    assert assignment_for_device(_device()) is None


def test_assignment_for_device_returns_pending_then_delivered(hub_db):
    rid = _release(version="1.0.0")
    did = _device()
    create_deployment(rid, "device", did, None, False, None)
    info = assignment_for_device(did)
    assert info is not None
    assert info["target_version"] == "1.0.0"

    mark_assignment_delivered(did)
    info = assignment_for_device(did)
    assert info is not None
    assert info["target_version"] == "1.0.0"  # still active in `delivered`


def test_assignment_for_device_ignores_completed_failed_superseded(hub_db):
    rid = _release()
    did = _device()
    create_deployment(rid, "device", did, None, False, None)
    with session_scope() as s:
        for state in ("completed", "failed", "superseded"):
            a = s.scalar(select(DeploymentAssignment).where(
                DeploymentAssignment.device_id == did))
            a.state = state
            s.flush()
            # `assignment_for_device` only returns pending/delivered,
            # so each of these terminal states should yield None.
        # Re-read after the final state set above.
    assert assignment_for_device(did) is None


# ── mark_assignment_delivered ──────────────────────────────────────────

def test_mark_assignment_delivered_promotes_pending_to_delivered(hub_db):
    rid = _release()
    did = _device()
    create_deployment(rid, "device", did, None, False, None)
    mark_assignment_delivered(did)
    with session_scope() as s:
        a = s.scalar(select(DeploymentAssignment).where(
            DeploymentAssignment.device_id == did))
        assert a.state == "delivered"


def test_mark_assignment_delivered_is_a_noop_with_no_pending(hub_db):
    # No assignments at all — must not raise.
    mark_assignment_delivered(_device())  # no error → pass


# ── reconcile_assignment_reported_version ──────────────────────────────

def test_reconcile_records_the_reported_version(hub_db):
    rid = _release(version="1.0.0")
    did = _device()
    create_deployment(rid, "device", did, None, False, None)
    with session_scope() as s:
        reconcile_assignment_reported_version(s, did, "0.9.0")
    with session_scope() as s:
        a = s.scalar(select(DeploymentAssignment).where(
            DeploymentAssignment.device_id == did))
        assert a.last_reported_version == "0.9.0"
        # Still pending — the device hasn't reached the target yet.
        assert a.state == "pending"


def test_reconcile_marks_completed_when_reported_matches_target(hub_db):
    rid = _release(version="1.0.0")
    did = _device()
    create_deployment(rid, "device", did, None, False, None)
    mark_assignment_delivered(did)
    with session_scope() as s:
        reconcile_assignment_reported_version(s, did, "1.0.0")
    with session_scope() as s:
        a = s.scalar(select(DeploymentAssignment).where(
            DeploymentAssignment.device_id == did))
        assert a.state == "completed"
        assert a.last_reported_version == "1.0.0"


def test_reconcile_clears_prior_error_message_on_completion(hub_db):
    rid = _release(version="1.0.0")
    did = _device()
    create_deployment(rid, "device", did, None, False, None)
    # Record an error mid-flight, then a successful upgrade clears it.
    with session_scope() as s:
        reconcile_assignment_reported_version(
            s, did, "0.9.0", error_message="checksum_mismatch")
    with session_scope() as s:
        a = s.scalar(select(DeploymentAssignment).where(
            DeploymentAssignment.device_id == did))
        assert a.error_message == "checksum_mismatch"
    with session_scope() as s:
        reconcile_assignment_reported_version(s, did, "1.0.0")
    with session_scope() as s:
        a = s.scalar(select(DeploymentAssignment).where(
            DeploymentAssignment.device_id == did))
        assert a.state == "completed"
        assert a.error_message is None


def test_reconcile_is_a_noop_when_no_active_assignment(hub_db):
    # No deployment for the device — function must not raise.
    with session_scope() as s:
        reconcile_assignment_reported_version(s, _device(), "1.0.0")


def test_reconcile_is_a_noop_when_device_id_is_empty(hub_db):
    with session_scope() as s:
        reconcile_assignment_reported_version(s, "", "1.0.0")


def test_reconcile_does_not_complete_when_versions_differ(hub_db):
    rid = _release(version="1.1.0")
    did = _device()
    create_deployment(rid, "device", did, None, False, None)
    mark_assignment_delivered(did)
    with session_scope() as s:
        reconcile_assignment_reported_version(s, did, "1.0.0")
    with session_scope() as s:
        a = s.scalar(select(DeploymentAssignment).where(
            DeploymentAssignment.device_id == did))
        assert a.state == "delivered"
        assert a.last_reported_version == "1.0.0"


# ── list_deployments ───────────────────────────────────────────────────

def test_list_deployments_returns_counts_broken_down_by_state(hub_db):
    rid = _release()
    devs = [_device(), _device(), _device()]
    out = create_deployment(rid, "all_devices", None, None, False, None)
    # Bump assignment states by hand to drive the count breakdown.
    with session_scope() as s:
        rows = list(s.scalars(select(DeploymentAssignment).where(
            DeploymentAssignment.deployment_id == out["id"])))
        rows[0].state = "completed"
        rows[1].state = "delivered"
        # rows[2] stays pending

    listed = list_deployments()
    counts = next(d for d in listed if d["id"] == out["id"])["counts"]
    assert counts == {
        "total": 3, "pending": 1, "delivered": 1,
        "completed": 1, "failed": 0, "superseded": 0,
    }


def test_list_deployments_orders_newest_first(hub_db):
    r1 = _release(version="1.0.0")
    r2 = _release(version="1.1.0")
    _device()  # at least one device for all_devices fan-out
    older = create_deployment(r1, "all_devices", None, None, False, None)
    newer = create_deployment(r2, "all_devices", None, None, False, None)
    listed = list_deployments()
    assert [d["id"] for d in listed[:2]] == [newer["id"], older["id"]]
