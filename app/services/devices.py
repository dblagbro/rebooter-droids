from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select

from app.db import session_scope
from app.models import (
    Command,
    Device,
    DeviceEvent,
    DeviceHeartbeat,
    Group,
    GroupMembership,
)


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
        "created_at": _iso(d.created_at),
        "updated_at": _iso(d.updated_at),
    }
    if include_secret_status:
        result["device_secret_status"] = "issued"
    return result


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def list_devices(
    site_id: str | None = None,
    group_id: str | None = None,
    search: str | None = None,
    status: str | None = None,
    offline_threshold_seconds: int = 180,
) -> list[dict]:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        stmt = select(Device)
        if site_id:
            stmt = stmt.where(Device.site_id == site_id)
        if status == "active":
            stmt = stmt.where(Device.registration_state == "active")
        if status == "disabled":
            stmt = stmt.where(Device.registration_state == "disabled")
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
        out = []
        for d in rows:
            obj = serialize_device(d)
            online = (
                d.last_heartbeat_at is not None
                and (now - d.last_heartbeat_at).total_seconds() < offline_threshold_seconds
            )
            obj["online"] = online
            out.append(obj)
        return out


def get_device_detail(device_id: str) -> dict | None:
    with session_scope() as session:
        d = session.get(Device, device_id)
        if d is None:
            return None
        out = serialize_device(d)

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
        return out


_PATCHABLE = {"display_name", "site_id", "notes", "central_management_enabled"}


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
