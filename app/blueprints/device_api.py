from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, current_app, g, request

from app.middleware.device_auth import device_auth_required
from app.middleware.response import err, ok
from app.services.commands import (
    list_pending_for_device,
    record_result,
)
from app.services.deployments import (
    assignment_for_device,
    mark_assignment_delivered,
)
from app.services.enrollment import EnrollmentError, consume_enrollment_token
from app.services.events import ingest_events
from app.services.heartbeats import record_heartbeat

bp = Blueprint("device_api", __name__)


def _server_time_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@bp.post("/register")
def register():
    body = request.get_json(silent=True) or {}
    token = body.get("enrollment_token")
    if not token:
        return err("validation_failed", "enrollment_token is required", status=400)

    settings = current_app.config["SETTINGS"]
    try:
        device, raw_secret = consume_enrollment_token(token, body)
    except EnrollmentError as e:
        status = 410 if e.code == "enrollment_expired" else 409 if e.code == "enrollment_consumed" else 400
        return err(e.code, e.message, status=status)

    return ok(
        {
            "device_id": device.id,
            "device_token": raw_secret,
            "poll_interval_seconds": settings.poll_interval_seconds,
            "heartbeat_interval_seconds": settings.heartbeat_interval_seconds,
            "server_time": _server_time_iso(),
        },
        status=201,
    )


@bp.post("/heartbeat")
@device_auth_required
def heartbeat():
    body = request.get_json(silent=True) or {}
    device = g.current_device
    body_id = body.get("device_id")
    if body_id and body_id != device.id:
        return err(
            "device_mismatch",
            "device_id in body does not match authenticated device.",
            status=400,
        )
    try:
        record_heartbeat(device.id, body)
    except LookupError:
        return err("device_unknown", "Device not found.", status=404)

    settings = current_app.config["SETTINGS"]
    return ok(
        {
            "next_poll_after_seconds": settings.poll_interval_seconds,
            "next_heartbeat_after_seconds": settings.heartbeat_interval_seconds,
        }
    )


@bp.get("/commands")
@device_auth_required
def poll_commands():
    device = g.current_device
    body_id = request.args.get("device_id")
    if body_id and body_id != device.id:
        return err(
            "device_mismatch",
            "device_id in query does not match authenticated device.",
            status=400,
        )
    cmds = list_pending_for_device(device.id, mark_delivered=True)
    return ok(
        {
            "commands": [
                {
                    "command_id": c.id,
                    "type": c.type,
                    "created_at": c.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "expires_at": c.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "payload": c.payload,
                }
                for c in cmds
            ]
        }
    )


@bp.post("/command-result")
@device_auth_required
def command_result():
    body = request.get_json(silent=True) or {}
    device = g.current_device
    body_id = body.get("device_id")
    if body_id and body_id != device.id:
        return err("device_mismatch", "device_id mismatch.", status=400)

    command_id = body.get("command_id")
    status = body.get("status")
    if not (command_id and status):
        return err(
            "validation_failed",
            "command_id and status are required.",
            status=400,
        )

    completed_at = body.get("completed_at")
    completed_dt = None
    if completed_at:
        from datetime import datetime as _dt

        try:
            completed_dt = _dt.fromisoformat(completed_at.replace("Z", "+00:00"))
        except ValueError:
            completed_dt = None

    try:
        result = record_result(
            device_id=device.id,
            command_id=command_id,
            status=status,
            message=body.get("message"),
            result=body.get("result"),
            completed_at=completed_dt,
        )
    except LookupError:
        return err("command_unknown", "Command not found for this device.", status=404)
    except ValueError as e:
        return err("validation_failed", str(e), status=400)

    return ok(
        {
            "command_id": result.command_id,
            "status": result.status,
            "received_at": result.received_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )


@bp.post("/events")
@device_auth_required
def upload_events():
    body = request.get_json(silent=True) or {}
    device = g.current_device
    if body.get("device_id") and body["device_id"] != device.id:
        return err("device_mismatch", "device_id mismatch.", status=400)
    events = body.get("events") or []
    if not isinstance(events, list):
        return err("validation_failed", "events must be a list", status=400)
    try:
        n = ingest_events(device.id, events)
    except ValueError as e:
        return err("validation_failed", str(e), status=400)
    return ok({"ingested": n})


@bp.get("/firmware")
@device_auth_required
def firmware_assignment():
    device = g.current_device
    a = assignment_for_device(device.id)
    if a is None:
        return ok({"assigned": False})
    mark_assignment_delivered(device.id)
    return ok(
        {
            "assigned": True,
            "channel": a["channel"],
            "target_version": a["target_version"],
            "download_url": a["download_url"],
            "sha256": a["sha256"],
            "force": a["force"],
        }
    )


# v0.3.8 (RFC-005 P1): device reports it had to fall back from
# slot B → slot C after a failed update. Body shape per
# RFC-005 §5.2.
@bp.post("/failsafe")
@device_auth_required
def failsafe_report():
    from app.services import failsafe as failsafe_service

    device = g.current_device
    body = request.get_json(silent=True) or {}
    failed_version = (body.get("failed_version") or "").strip() or None
    fallback_to_version = (body.get("fallback_to_version") or "").strip() or None
    reason = (body.get("reason") or "other").strip()
    details = body.get("details") if isinstance(body.get("details"), dict) else None

    result = failsafe_service.record(
        device_id=device.id,
        failed_version=failed_version,
        fallback_to_version=fallback_to_version,
        reason=reason,
        details=details,
    )
    return ok(result, status=201)
