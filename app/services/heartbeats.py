from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import Device, DeviceHeartbeat


def record_heartbeat(device_id: str, payload: dict) -> dict:
    now = datetime.now(timezone.utc)
    last_event_at = payload.get("last_event_at")
    last_event_dt = None
    if last_event_at:
        try:
            last_event_dt = datetime.fromisoformat(last_event_at.replace("Z", "+00:00"))
        except ValueError:
            last_event_dt = None

    with session_scope() as session:
        device = session.get(Device, device_id)
        if device is None:
            raise LookupError(device_id)

        device.last_heartbeat_at = now
        if payload.get("firmware_version"):
            device.firmware_version = payload["firmware_version"]
        if payload.get("local_ip"):
            device.local_ip = payload["local_ip"]
        session.add(device)

        hb = DeviceHeartbeat(
            device_id=device_id,
            received_at=now,
            firmware_version=payload.get("firmware_version"),
            local_ip=payload.get("local_ip"),
            mode=payload.get("mode"),
            relay_on=payload.get("relay_on"),
            wifi_connected=payload.get("wifi_connected"),
            health_state=payload.get("health_state"),
            uptime_seconds=payload.get("uptime_seconds"),
            incident_cycles=payload.get("incident_cycles"),
            hour_cycles=payload.get("hour_cycles"),
            last_event_type=payload.get("last_event_type"),
            last_event_at=last_event_dt,
        )
        session.add(hb)
        session.flush()

    return {"recorded_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}


def latest_heartbeat(session, device_id: str) -> DeviceHeartbeat | None:
    return session.scalar(
        select(DeviceHeartbeat)
        .where(DeviceHeartbeat.device_id == device_id)
        .order_by(DeviceHeartbeat.received_at.desc())
        .limit(1)
    )
