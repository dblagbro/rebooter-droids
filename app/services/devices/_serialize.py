"""Pure-function serialization + presentation helpers.

No DB writes; no session_scope() calls. Anything that turns a SQLAlchemy
row into the dict shape the API + templates consume lives here.

Public symbols are re-exported from `app.services.devices` so external
callers keep importing from the package root.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.models import Device, DeploymentAssignment, FirmwareRelease


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


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


def _derive_central_status(
    d: Device,
    *,
    heartbeat_state: str,
    latest_health_state: str | None = None,
    active_assignment: dict | None = None,
) -> dict:
    """Map (device row, heartbeat state, active firmware assignment) ->
    {code, label, reason} tuple for the devices list + detail UI.

    See architecture.md §"Source layout" for the code taxonomy.
    """
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
