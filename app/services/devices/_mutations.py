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
