from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Iterable

from sqlalchemy import select

from app.db import session_scope
from app.models import (
    Command,
    Device,
    DeviceEvent,
    DeviceHeartbeat,
    DeploymentAssignment,
    FirmwareRelease,
    Group,
    GroupMembership,
)

log = logging.getLogger(__name__)


def serialize_device(d: Device, include_secret_status: bool = True) -> dict:
    result = {
        "id": d.id,
        "display_name": d.display_name,
        "hardware_model": d.hardware_model,
        "hardware_revision": d.hardware_revision,
        "firmware_version": d.firmware_version,
        "mac_address": d.mac_address,
        "serial_number": d.serial_number,
        "local_ip": d.local_ip,
        "site_id": d.site_id,
        "registration_state": d.registration_state,
        "central_management_enabled": d.central_management_enabled,
        "capabilities": d.capabilities or {},
        "notes": d.notes,
        "last_heartbeat_at": _iso(d.last_heartbeat_at),
        "is_qa_fixture": bool(d.is_qa_fixture),
        "is_protected": bool(d.is_protected),
        "is_held_off": bool(d.is_held_off),
        "created_at": _iso(d.created_at),
        "updated_at": _iso(d.updated_at),
    }
    if include_secret_status:
        result["device_secret_status"] = "issued"
    return result


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _heartbeat_state_for(
    last_heartbeat_at: datetime | None,
    *,
    now: datetime,
    offline_threshold_seconds: int,
) -> str:
    if last_heartbeat_at is None:
        return "never"
    if (now - last_heartbeat_at).total_seconds() < offline_threshold_seconds:
        return "online"
    return "offline"


def _serialize_assignment(
    a: DeploymentAssignment, release: FirmwareRelease | None
) -> dict:
    return {
        "assignment_id": a.id,
        "deployment_id": a.deployment_id,
        "state": a.state,
        "target_version": release.version if release else None,
        "last_reported_version": a.last_reported_version,
        "error_message": a.error_message,
        "updated_at": _iso(a.updated_at),
    }


def _active_assignments_by_device(session, device_ids: Iterable[str]) -> dict[str, dict]:
    ids = [d for d in device_ids if d]
    if not ids:
        return {}
    rows = list(
        session.scalars(
            select(DeploymentAssignment)
            .where(
                DeploymentAssignment.device_id.in_(ids),
                DeploymentAssignment.state.in_(("pending", "delivered")),
            )
            .order_by(
                DeploymentAssignment.device_id.asc(),
                DeploymentAssignment.created_at.desc(),
            )
        )
    )
    latest_by_device: dict[str, DeploymentAssignment] = {}
    for row in rows:
        latest_by_device.setdefault(row.device_id, row)

    release_ids = {row.release_id for row in latest_by_device.values()}
    releases = {
        r.id: r
        for r in session.scalars(
            select(FirmwareRelease).where(FirmwareRelease.id.in_(release_ids))
        )
    } if release_ids else {}
    return {
        device_id: _serialize_assignment(row, releases.get(row.release_id))
        for device_id, row in latest_by_device.items()
    }


def _derive_central_status(
    d: Device,
    *,
    heartbeat_state: str,
    latest_health_state: str | None = None,
    active_assignment: dict | None = None,
) -> dict:
    if not d.central_management_enabled:
        return {
            "code": "local_only",
            "label": "local-only",
            "reason": "Device opts out of central management.",
        }

    current_version = (d.firmware_version or "").strip() or None
    target_version = (
        (active_assignment or {}).get("target_version") or ""
    ).strip() or None
    assignment_state = (active_assignment or {}).get("state")

    if target_version and current_version != target_version:
        if heartbeat_state == "offline":
            return {
                "code": "transport_stale",
                "label": "transport stale",
                "reason": (
                    f"Device is assigned {target_version} but last reported "
                    f"{current_version or 'unknown'} and is no longer heartbeating."
                ),
            }
        return {
            "code": "upgrade_pending",
            "label": "upgrade pending",
            "reason": (
                f"Device is assigned {target_version} but still reports "
                f"{current_version or 'unknown'}."
            ),
        }

    if heartbeat_state == "never":
        return {
            "code": "awaiting_first_heartbeat",
            "label": "awaiting first heartbeat",
            "reason": "Device is enrolled for central management but has not heartbeated yet.",
        }

    if heartbeat_state == "offline":
        return {
            "code": "central_stale",
            "label": "stale",
            "reason": "Central has not heard from this device within the heartbeat window.",
        }

    if latest_health_state and latest_health_state not in ("healthy", "ok"):
        return {
            "code": "attention",
            "label": "attention",
            "reason": f"Latest heartbeat reported health_state={latest_health_state}.",
        }

    if assignment_state in ("pending", "delivered") and target_version:
        return {
            "code": "upgrade_pending",
            "label": "upgrade pending",
            "reason": f"Waiting for device to report target firmware {target_version}.",
        }

    return {
        "code": "central_ok",
        "label": "central",
        "reason": "Central management is enabled and the device is reporting normally.",
    }


# v0.5.4: version-comparison helpers moved to app/services/_versions.py
# so unit tests can import them without booting the full Flask app.
# Re-exported here for back-compat with existing callers (templates'
# `is_upgrade=` Jinja global, blueprint imports).
from app.services._versions import _version_sort_key, is_upgrade  # noqa: F401


def find_by_mac(mac_address: str | None) -> list[dict]:
    """v0.5.7 (B20): lookup existing devices by MAC. Used by
    pending-adoption to surface "this hardware is already in the
    fleet" matches so the operator can pick rebind vs fresh-adopt
    explicitly. Normalised to uppercase + stripped. Excludes rows
    in registration_state='decommissioned' (the dead-row marker
    we use post-replacement).
    """
    if not mac_address:
        return []
    mac = mac_address.strip().upper()
    if not mac:
        return []
    with session_scope() as session:
        rows = list(session.scalars(
            select(Device).where(
                # Postgres case-insensitive compare via UPPER() works
                # whether the existing rows are upper, lower, or mixed.
                Device.mac_address.is_not(None),
            )
        ))
        out = []
        for d in rows:
            if (d.mac_address or "").strip().upper() != mac:
                continue
            if d.registration_state == "decommissioned":
                continue
            out.append(serialize_device(d, include_secret_status=False))
        return out


def latest_stable_release_dict() -> dict | None:
    """v0.4.29: helper for the devices page to know what version
    a device "should" be on. Returns the **highest-version**
    release in the `stable` channel, or None if there isn't one.

    Before v0.4.29 this returned the most-recently-*uploaded*
    release, which created the operator-visible "upgrade" button
    that actually pointed at a downgrade when an older release was
    re-uploaded after a newer one (e.g. 0.1.2 re-pushed while the
    fleet was already on 0.1.5).
    """
    from app.models import FirmwareRelease
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(FirmwareRelease).where(FirmwareRelease.channel == "stable")
            )
        )
        if not rows:
            return None
        rel = max(rows, key=lambda r: _version_sort_key(r.version))
        return {
            "id": rel.id,
            "version": rel.version,
            "channel": rel.channel,
            "sha256": rel.sha256,
            "size_bytes": rel.size_bytes,
            "filename": rel.filename,
        }


def firmware_version_breakdown(*, include_qa_fixtures: bool = False) -> list[dict]:
    """v0.4.19 (B14 follow-up / Tier-1 A): group the fleet by
    `firmware_version`. Surfaces "which devices on which version"
    so the operator can spot upgrade outliers at a glance.

    Returns a list of {version, count, devices: [{id,display_name}],
    is_majority} sorted by count descending; the largest cohort is
    flagged `is_majority=true` so the UI can mark outliers.

    Devices with no firmware_version (just enrolled, never reported)
    are bucketed under the literal string "(unknown)" so they don't
    silently vanish.
    """
    with session_scope() as session:
        stmt = select(Device)
        if not include_qa_fixtures:
            stmt = stmt.where(Device.is_qa_fixture.is_(False))
        rows = list(session.scalars(stmt))

    buckets: dict[str, list[dict]] = {}
    for d in rows:
        ver = (d.firmware_version or "(unknown)").strip() or "(unknown)"
        buckets.setdefault(ver, []).append({
            "id": d.id,
            "display_name": d.display_name or d.id,
        })

    if not buckets:
        return []

    breakdown = [
        {
            "version": ver,
            "count": len(devs),
            "devices": sorted(devs, key=lambda x: x["display_name"]),
        }
        for ver, devs in buckets.items()
    ]
    breakdown.sort(key=lambda b: (-b["count"], b["version"]))
    majority_count = breakdown[0]["count"]
    for b in breakdown:
        b["is_majority"] = (b["count"] == majority_count and len(breakdown) > 1)
    return breakdown


def list_devices(
    site_id: str | None = None,
    group_id: str | None = None,
    search: str | None = None,
    status: str | None = None,
    offline_threshold_seconds: int = 180,
    include_qa_fixtures: bool = True,
    chips: tuple[str, ...] | list[str] | None = None,
) -> list[dict]:
    """v0.3.1 (P2): saved-filter `chips` arg accepts a list of named
    filter shortcuts that are AND-composed with the other filters.
    Recognised chips:
      - "offline_24h"   — last_heartbeat_at older than 24 h (had history)
      - "never"         — last_heartbeat_at IS NULL
      - "pending_cmds"  — has a command in pending/accepted/running state
      - "qa_fixtures"   — only QA-fixture-tagged rows (overrides
                          include_qa_fixtures=False to *show* them)

    Unrecognised chip names are silently ignored so a stale URL
    doesn't 500.
    """
    now = datetime.now(timezone.utc)
    chips = tuple(c for c in (chips or ()) if c)
    with session_scope() as session:
        stmt = select(Device)
        if site_id:
            stmt = stmt.where(Device.site_id == site_id)
        if status == "active":
            stmt = stmt.where(Device.registration_state == "active")
        if status == "disabled":
            stmt = stmt.where(Device.registration_state == "disabled")

        # Saved-filter chips (R-DEV-4).
        from datetime import timedelta as _td

        if "qa_fixtures" in chips:
            # Show ONLY QA fixtures — overrides the include flag.
            stmt = stmt.where(Device.is_qa_fixture.is_(True))
        elif not include_qa_fixtures:
            stmt = stmt.where(Device.is_qa_fixture.is_(False))

        if "offline_24h" in chips:
            cutoff_24h = now - _td(hours=24)
            stmt = stmt.where(
                Device.last_heartbeat_at.is_not(None),
                Device.last_heartbeat_at < cutoff_24h,
            )

        if "never" in chips:
            stmt = stmt.where(Device.last_heartbeat_at.is_(None))

        if "pending_cmds" in chips:
            from sqlalchemy import exists

            stmt = stmt.where(
                exists().where(
                    (Command.device_id == Device.id)
                    & (Command.status.in_(("pending", "accepted", "running")))
                )
            )

        if search:
            like = f"%{search.lower()}%"
            from sqlalchemy import or_, func

            stmt = stmt.where(
                or_(
                    func.lower(Device.display_name).like(like),
                    func.lower(Device.mac_address).like(like),
                    func.lower(Device.id).like(like),
                )
            )

        if group_id:
            stmt = stmt.join(
                GroupMembership, GroupMembership.device_id == Device.id
            ).where(GroupMembership.group_id == group_id)

        stmt = stmt.order_by(Device.created_at.desc())
        rows = list(session.scalars(stmt))
        assignments_by_device = _active_assignments_by_device(
            session, [d.id for d in rows]
        )
        out = []
        for d in rows:
            obj = serialize_device(d)
            hb_state = _heartbeat_state_for(
                d.last_heartbeat_at,
                now=now,
                offline_threshold_seconds=offline_threshold_seconds,
            )
            obj["heartbeat_state"] = hb_state
            obj["online"] = hb_state == "online"
            assignment = assignments_by_device.get(d.id)
            if assignment:
                obj["active_firmware_assignment"] = assignment
            central_status = _derive_central_status(
                d,
                heartbeat_state=hb_state,
                active_assignment=assignment,
            )
            obj["central_status"] = central_status["code"]
            obj["central_status_label"] = central_status["label"]
            obj["central_status_reason"] = central_status["reason"]
            out.append(obj)
        return out


def get_device_detail(device_id: str) -> dict | None:
    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is None:
            return None
        out = serialize_device(d)
        now = datetime.now(timezone.utc)
        hb_state = _heartbeat_state_for(
            d.last_heartbeat_at,
            now=now,
            offline_threshold_seconds=180,
        )
        out["heartbeat_state"] = hb_state
        out["online"] = hb_state == "online"

        latest_hb = session.scalar(
            select(DeviceHeartbeat)
            .where(DeviceHeartbeat.device_id == device_id)
            .order_by(DeviceHeartbeat.received_at.desc())
            .limit(1)
        )
        out["latest_heartbeat"] = (
            {
                "received_at": _iso(latest_hb.received_at),
                "mode": latest_hb.mode,
                "relay_on": latest_hb.relay_on,
                "wifi_connected": latest_hb.wifi_connected,
                "health_state": latest_hb.health_state,
                "uptime_seconds": latest_hb.uptime_seconds,
                "incident_cycles": latest_hb.incident_cycles,
                "hour_cycles": latest_hb.hour_cycles,
                "last_event_type": latest_hb.last_event_type,
                "last_event_at": _iso(latest_hb.last_event_at),
            }
            if latest_hb
            else None
        )
        assignment = _active_assignments_by_device(session, [device_id]).get(device_id)
        if assignment:
            out["active_firmware_assignment"] = assignment
        central_status = _derive_central_status(
            d,
            heartbeat_state=hb_state,
            latest_health_state=(latest_hb.health_state if latest_hb else None),
            active_assignment=assignment,
        )
        out["central_status"] = central_status["code"]
        out["central_status_label"] = central_status["label"]
        out["central_status_reason"] = central_status["reason"]

        group_rows = list(
            session.execute(
                select(Group)
                .join(GroupMembership, GroupMembership.group_id == Group.id)
                .where(GroupMembership.device_id == device_id)
            )
        )
        out["groups"] = [{"id": g[0].id, "name": g[0].name} for g in group_rows]

        recent_events = list(
            session.scalars(
                select(DeviceEvent)
                .where(DeviceEvent.device_id == device_id)
                .order_by(DeviceEvent.timestamp.desc())
                .limit(20)
            )
        )
        out["recent_events"] = [
            {
                "type": e.type,
                "timestamp": _iso(e.timestamp),
                "message": e.message,
                "mode": e.mode,
                "details": e.details,
            }
            for e in recent_events
        ]

        pending_cmds = list(
            session.scalars(
                select(Command)
                .where(
                    Command.device_id == device_id,
                    Command.status.in_(("pending", "accepted", "running")),
                )
                .order_by(Command.created_at.desc())
            )
        )
        out["pending_commands"] = [
            {
                "id": c.id,
                "type": c.type,
                "status": c.status,
                "created_at": _iso(c.created_at),
                "expires_at": _iso(c.expires_at),
                "payload": c.payload,
            }
            for c in pending_cmds
        ]

        # v0.2.9: per-record audit slice. Last 25 audit events that target
        # this device. Composite index ix_audit_target makes this cheap.
        from app.services import audit as audit_service

        out["audit_history"] = audit_service.query(
            target_type="device", target_id=device_id, limit=25
        )

        # v0.3.8 (RFC-005 P1): per-device failsafe history. Last 25
        # B → C fallback events from the device.
        from app.services import failsafe as failsafe_service

        out["failsafe_events"] = failsafe_service.list_for_device(
            device_id, limit=25
        )
        return out


_PATCHABLE = {
    "display_name",
    "site_id",
    "notes",
    "central_management_enabled",
    "is_protected",  # v0.3.2 (P3)
}


def delete_device(device_id: str) -> bool:
    """Hard-delete a device + cascade (credentials, heartbeats, events,
    commands, deployment_assignments, group memberships).

    Note: the device's enrollment_token row is preserved (consumed_by_device_id
    becomes NULL via the SET NULL FK rule), so audit history is intact.
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
    - Skips protected devices unless override_lockout=True; the
      skipped IDs are returned to the caller for surfacing.
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


class UnknownPatchFieldError(ValueError):
    def __init__(self, fields: set[str]):
        super().__init__(
            f"unsupported PATCH fields: {sorted(fields)}. Allowed: {sorted(_PATCHABLE)}"
        )
        self.fields = fields


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
