"""Write operations on the device aggregate.

Every public function in this module mutates state under a
`session_scope()`. Pure presentation and read-only queries live in
`_serialize.py` and `_query.py` respectively.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.db import session_scope
from app.models import Device
from app.services.devices._serialize import serialize_device

log = logging.getLogger(__name__)


_PATCHABLE = {
    "display_name",
    "site_id",
    "notes",
    "central_management_enabled",
    "is_protected",  # v0.3.2 (P3)
}


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
    """
    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is None:
            return False
        session.delete(d)
        session.flush()
        return True


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


def update_device(device_id: str, patch: dict) -> dict | None:
    unknown = set(patch.keys()) - _PATCHABLE
    if unknown:
        raise UnknownPatchFieldError(unknown)

    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is None:
            return None
        # Only bump updated_at when a real change occurs (BUG-011).
        changed = False
        for k, v in patch.items():
            if getattr(d, k) != v:
                setattr(d, k, v)
                changed = True
        if changed:
            d.updated_at = datetime.now(timezone.utc)
            session.add(d)
        session.flush()
        return serialize_device(d)


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
