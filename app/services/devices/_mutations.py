"""Write operations on the device aggregate.

Every public function in this module mutates state under a
`session_scope()`. Pure presentation and read-only queries live in
`_serialize.py` and `_query.py` respectively.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import Device, Site
from app.services.devices._serialize import serialize_device

log = logging.getLogger(__name__)


# 0.6.45 Batch D (#211): typed validator map. Pre-fix _PATCHABLE was a
# flat whitelist — any new field added to the set inherited zero
# validation by default. That's how BUG-064 (cross-scope RBAC bypass)
# and BUG-062/063 (cycle / missing FK) all became latent bugs as soon
# as power_source_device_id joined the set. Now every patchable field
# carries a validator. The default (`_accept_any`) preserves current
# behaviour for fields where any value is fine; FK fields get a
# `_resolve_*` validator that pre-flights existence + invariants.
#
# Each validator: (value, *, device, session) -> normalized_value.
# Raises PowerTopologyError or similar typed exception on rejection.
# Returns the value to store (may differ from input if normalized).

def _accept_any(value, *, device, session):  # noqa: ARG001
    return value


def _validate_site_id(value, *, device, session):  # noqa: ARG001
    """0.6.47 BUG-073 fix: pre-flight site existence. site_id was the
    last FK column in _PATCHABLE still mapped to `_accept_any`, which
    meant a stale dropdown / form-tamper would land an invalid id on
    update_device → DB FK rejects with IntegrityError → 500. The
    SiteScopeError lets the handler render a flash instead.
    """
    if value is None or value == "":
        return None
    site = session.get(Site, value)
    if site is None:
        raise SiteScopeError(
            "missing",
            f"Selected site no longer exists (id={value}).",
        )
    return value


def _validate_power_source_device_id(value, *, device, session):
    """0.6.45 Batch D: migration of the cycle/self/missing checks from
    inline-in-update_device into the typed validator. Same logic, same
    PowerTopologyError subtypes — the move is purely organizational.
    """
    if value is None:
        return None
    if value == device.id:
        raise PowerTopologyError(
            "self_parent",
            "A device cannot be powered by itself.",
        )
    parent = session.get(Device, value)
    if parent is None:
        raise PowerTopologyError(
            "parent_missing",
            f"Selected power source no longer exists (id={value}).",
        )
    # Walk parent → grandparent → ... refuse if device.id appears.
    cur = parent
    for _ in range(50):
        if cur.id == device.id:
            raise PowerTopologyError(
                "cycle",
                f"Assignment would create a power-source cycle "
                f"through {parent.display_name or parent.id}.",
            )
        if cur.power_source_device_id is None:
            break
        cur = session.get(Device, cur.power_source_device_id)
        if cur is None:
            break
    else:
        raise PowerTopologyError(
            "cycle",
            "Power-source chain exceeds 50 hops — refusing to extend "
            "a likely-corrupt graph.",
        )
    return value


_PATCHABLE = {
    "display_name": _accept_any,
    "site_id": _validate_site_id,  # 0.6.47 BUG-073
    "notes": _accept_any,
    "central_management_enabled": _accept_any,
    "is_protected": _accept_any,  # v0.3.2 (P3)
    "power_source_device_id": _validate_power_source_device_id,  # 0.6.39/0.6.45
}


class SiteScopeError(ValueError):
    """0.6.47 BUG-073: typed error for invalid site_id assignment.
    Mirrors PowerTopologyError so the handler can flash + redirect
    instead of returning 500 from a bare IntegrityError.

    Subtypes:
      - 'missing' — referenced site id doesn't exist
    """
    def __init__(self, subtype: str, message: str) -> None:
        super().__init__(message)
        self.subtype = subtype


class PowerTopologyError(ValueError):
    """0.6.40 BUG-062/063/064 fix: typed error for invalid power_source
    assignments. The blueprint handler catches this and renders a
    user-facing flash instead of letting the bare IntegrityError
    propagate to a 500.

    Subtypes:
      - 'self_parent'   — A → A
      - 'cycle'         — A → B → ... → A
      - 'parent_missing' — referenced device id doesn't exist
    """
    def __init__(self, subtype: str, message: str) -> None:
        super().__init__(message)
        self.subtype = subtype


class UnknownPatchFieldError(ValueError):
    def __init__(self, fields: set[str]):
        super().__init__(
            f"unsupported PATCH fields: {sorted(fields)}. Allowed: {sorted(_PATCHABLE)}"
        )
        self.fields = fields


class MergeRetireError(ValueError):
    """Raised by `merge_retire_device` for an invalid merge request."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def delete_device(device_id: str) -> bool:
    """Hard-delete a device + cascade (credentials, heartbeats, events,
    commands, deployment_assignments, group memberships).

    Note: the device's enrollment_token row is preserved
    (consumed_by_device_id becomes NULL via the SET NULL FK rule), so
    audit history is intact.

    Returns True if the device existed and was deleted, False otherwise.
    """
    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is None:
            return False
        session.delete(d)
        session.flush()
        return True


def delete_device_with_audit_context(device_id: str) -> dict | None:
    """0.6.43 Batch B (#211 BUG-067): enumerate the children whose
    power_source_device_id is about to go NULL via ON DELETE SET NULL,
    THEN delete the parent. The handler logs the orphaned-children list
    on the `device.deleted` audit row so the reboot classifier (and
    future post-mortem walks) know that any `Power On` reset on the
    listed children right after the delete is a known-cause cascade.

    Returns {"deleted_id": ..., "orphaned_children": [{"id", "display_name"}]}
    if the device existed, or None if not found.
    """
    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is None:
            return None
        # Snapshot the children BEFORE the cascade fires.
        children = session.execute(
            select(Device.id, Device.display_name).where(
                Device.power_source_device_id == device_id
            )
        ).all()
        orphaned = [
            {"id": cid, "display_name": cname or cid}
            for cid, cname in children
        ]
        session.delete(d)
        session.flush()
        return {
            "deleted_id": device_id,
            "orphaned_children": orphaned,
        }


def delete_devices_bulk(
    device_ids: list[str], override_lockout: bool = False
) -> dict:
    """v0.3.4 (P3): bulk-delete a list of devices.

    Mirrors the single-device delete contract per row but:
    - Skips protected devices unless override_lockout=True; the skipped
      IDs are returned to the caller for surfacing.
    - Skips IDs that don't exist (silently — returned as `unknown`).
    - Applies the cascade per device (same as delete_device).

    Returns: {"deleted": [...ids...], "skipped_protected": [...],
              "skipped_unknown": [...]}.
    """
    deleted: list[str] = []
    skipped_protected: list[str] = []
    skipped_unknown: list[str] = []
    with session_scope() as session:
        for did in device_ids:
            d = session.get(Device, did)
            if d is None:
                skipped_unknown.append(did)
                continue
            if d.is_protected and not override_lockout:
                skipped_protected.append(did)
                continue
            session.delete(d)
            deleted.append(did)
        session.flush()
    return {
        "deleted": deleted,
        "skipped_protected": skipped_protected,
        "skipped_unknown": skipped_unknown,
    }


def _apply_patch(session, d: Device, patch: dict) -> tuple[dict, dict]:
    """0.6.47 BUG-076: shared core for update_device + _with_diff.
    Runs inside the caller's session_scope so the wrappers don't each
    open their own (the diff variant was paying 2 round-trips).
    Returns (serialized_device, diff). Validators run BEFORE setattr.
    """
    old_snapshot = {k: getattr(d, k) for k in patch.keys()}
    normalized: dict = {}
    for k, v in patch.items():
        normalized[k] = _PATCHABLE[k](v, device=d, session=session)
    changed = False
    for k, v in normalized.items():
        if getattr(d, k) != v:
            setattr(d, k, v)
            changed = True
    if changed:
        d.updated_at = datetime.now(timezone.utc)
        session.add(d)
    session.flush()
    diff: dict[str, dict] = {}
    for k, new_v in normalized.items():
        old_v = old_snapshot.get(k)
        if old_v != new_v:
            diff[k] = {"old": old_v, "new": new_v}
    return serialize_device(d), diff


def update_device_with_diff(device_id: str, patch: dict) -> tuple[dict | None, dict]:
    """0.6.43 Batch B (#211 BUG-066): caller can audit-log the OLD
    value alongside the NEW for every changed field. The diff is
    captured against the NORMALIZED value (post-validator) so an audit
    row for e.g. `site_id` records the same value that was actually
    stored, even when a future validator normalizes the input.

    Returns (updated_dict_or_None, diff). When `updated_dict` is None
    the device wasn't found and diff is empty.
    """
    unknown = set(patch.keys()) - _PATCHABLE.keys()
    if unknown:
        raise UnknownPatchFieldError(unknown)
    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is None:
            return None, {}
        return _apply_patch(session, d, patch)


def update_device(device_id: str, patch: dict) -> dict | None:
    unknown = set(patch.keys()) - _PATCHABLE.keys()
    if unknown:
        raise UnknownPatchFieldError(unknown)
    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is None:
            return None
        serialized, _diff = _apply_patch(session, d, patch)
        return serialized


def merge_retire_device(keep_device_id: str, retire_device_id: str) -> dict:
    """S1-7: merge/retire one of two duplicate Device rows.

    The `.69` symptom: a device that re-registered through the fresh-
    adopt path produced a *second* Device row for the same physical
    MAC. The operator needs to consolidate on one row.

    This action keeps `keep_device_id` and retires `retire_device_id`
    by setting its `registration_state='decommissioned'`. The retired
    row is NOT hard-deleted — heartbeats, events, commands, deployment
    assignments and audit rows FK-reference it; deleting would cascade
    away real history. Decommissioning takes it out of the active
    fleet views (`find_by_mac`, list filters) while preserving the
    record.

    Guards:
    - both ids must resolve to a Device,
    - the two must be distinct,
    - both must share a MAC (verified via the normalized `find_by_mac`),
    - the keep row must not itself already be decommissioned.

    Returns a dict describing the merge for the caller's audit event.
    Raises `MergeRetireError` on any guard failure.
    """
    if not keep_device_id or not retire_device_id:
        raise MergeRetireError(
            "validation_failed", "keep_device_id and retire_device_id are required"
        )
    if keep_device_id == retire_device_id:
        raise MergeRetireError(
            "validation_failed", "keep and retire device must be different"
        )

    # Deferred import — _query imports _serialize from this package.
    from app.services.devices._query import find_by_mac

    with session_scope() as session:
        keep = session.get(Device, keep_device_id)
        retire = session.get(Device, retire_device_id)
        if keep is None:
            raise MergeRetireError("not_found", f"keep device {keep_device_id} not found")
        if retire is None:
            raise MergeRetireError(
                "not_found", f"retire device {retire_device_id} not found"
            )
        if keep.registration_state == "decommissioned":
            raise MergeRetireError(
                "validation_failed",
                "keep device is decommissioned; pick an active row to keep",
            )
        if retire.registration_state == "decommissioned":
            raise MergeRetireError(
                "already_retired",
                f"device {retire_device_id} is already decommissioned",
            )

        # Verify both rows share a MAC. `find_by_mac` normalizes the
        # MAC and excludes already-decommissioned rows, so a positive
        # match here confirms the two are the same physical hardware.
        keep_mac = (keep.mac_address or "").strip()
        retire_mac = (retire.mac_address or "").strip()
        if not keep_mac or not retire_mac:
            raise MergeRetireError(
                "mac_mismatch",
                "both devices must have a MAC address to merge",
            )
        matches = {d["id"] for d in find_by_mac(keep_mac)}
        if retire_device_id not in matches or keep_device_id not in matches:
            raise MergeRetireError(
                "mac_mismatch",
                "the two devices do not share a MAC address; refusing to merge",
            )

        retire.registration_state = "decommissioned"
        retire.updated_at = datetime.now(timezone.utc)
        session.add(retire)
        session.flush()
        return {
            "keep_device_id": keep_device_id,
            "retire_device_id": retire_device_id,
            "mac_address": keep_mac,
            "keep_display_name": keep.display_name,
            "retired_display_name": retire.display_name,
        }


def enqueue_display_name_sync(
    device_id: str,
    *,
    display_name: str | None,
    issued_by_user_id: str | None,
    reason: str,
) -> bool:
    """Best-effort hub->device name sync for centrally managed units.

    Today the hub's device row display_name and the device's local
    `device_name` are separate truths unless we explicitly enqueue an
    `apply_config` command. Restore-after-reflash already does this.
    Ordinary operator renames must do it too, or the local web UI keeps
    the stale name indefinitely.
    """
    if not device_id or not display_name:
        return False

    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is None:
            return False
        if not d.central_management_enabled:
            return False

    try:
        # Deferred import to avoid a cycle: commands.py imports models
        # transitively from this package via audit/device-lockout helpers.
        from app.services.commands import enqueue_for_device

        enqueue_for_device(
            device_id=device_id,
            cmd_type="apply_config",
            payload={"device_name": display_name},
            issued_by_user_id=issued_by_user_id,
            ttl_seconds=600,
        )
        return True
    except Exception as e:
        log.warning(
            "display-name sync enqueue failed for %s (%s): %s",
            device_id, reason, e,
        )
        return False
