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
from app.services.events import ingest_power_samples
from app.services.heartbeats import record_heartbeat

bp = Blueprint("device_api", __name__)


def _server_time_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@bp.post("/announce")
def announce():
    """v0.4.20 — Pending-adoption announce poll.

    Unauthenticated. A device boots without an enrolment token,
    POSTs its claims here every ~30s. The hub records the
    announcement; the operator sees it on
    `/app/pending-adoption` and clicks Adopt; the next announce
    poll returns the freshly-minted enrolment token for the
    device to use against `/register`.

    See `docs/notes/2026-05-10-firmware-team-announce-adopt-contract.md`
    for the firmware-side contract.
    """
    from app.services.announcements import (
        AnnouncementError, upsert_announcement,
    )

    body = request.get_json(silent=True) or {}
    source_ip = (
        request.headers.get("X-Forwarded-For", request.remote_addr or "")
        .split(",")[0].strip()
    )
    user_agent = request.headers.get("User-Agent")
    try:
        result = upsert_announcement(
            mac_address=body.get("mac_address", ""),
            hardware_model=body.get("hardware_model"),
            hardware_revision=body.get("hardware_revision"),
            firmware_version=body.get("firmware_version"),
            local_ip=body.get("local_ip"),
            serial_number=body.get("serial_number"),
            display_name_hint=body.get("display_name_hint"),
            source_ip=source_ip,
            user_agent=user_agent,
        )
    except AnnouncementError as e:
        return err(e.code, e.message, status=400)
    return ok(result)


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
        # S1-7: `device_already_registered` is a 409 Conflict — the
        # operator should use the Restore path, not retry /register.
        if e.code == "enrollment_expired":
            status = 410
        elif e.code in ("enrollment_consumed", "device_already_registered"):
            status = 409
        else:
            status = 400
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


def _parse_prefer_wait(prefer_header: str | None) -> int:
    """v0.5.20 (#1): minimal RFC 7240 Prefer-header parser, scoped to
    `wait=<seconds>`. Returns the requested wait, clamped to
    [0, LONG_POLL_MAX_WAIT_SECONDS]. Returns 0 for missing/malformed
    header → caller falls back to legacy no-wait behaviour.
    """
    if not prefer_header:
        return 0
    # Header value is comma-separated preferences; only `wait=N` matters here.
    for token in prefer_header.split(","):
        token = token.strip().lower()
        if token.startswith("wait="):
            value = token.split("=", 1)[1].strip().strip('"')
            try:
                n = int(value)
            except ValueError:
                return 0
            if n < 0:
                return 0
            return min(n, LONG_POLL_MAX_WAIT_SECONDS)
    return 0


LONG_POLL_MAX_WAIT_SECONDS = 30
LONG_POLL_CHECK_INTERVAL_SECONDS = 1.0


@bp.get("/commands")
@device_auth_required
def poll_commands():
    """v0.5.20 (#1, B17 follow-on / firmware-team responsiveness ask):
    optional long-poll via RFC 7240 `Prefer: wait=<seconds>`.

    Behaviour:
    - Missing/zero `wait` → legacy no-wait response (back-compat with
      0.1.x firmware that doesn't know about long-poll).
    - `wait > 0` → check immediately for pending commands; if any,
      return them with `Preference-Applied: wait=<n>`. Otherwise hold
      the request open, re-checking the DB every 1 s until either a
      command appears or `wait` seconds elapse. Server caps `wait` at
      30 s regardless of what the client asks.
    - Each check opens + closes its own session_scope() so the
      Postgres connection pool isn't held across the wait window.
      The gunicorn worker is `gthread` (default 8 threads); each
      open long-poll consumes one thread, so the practical concurrent
      ceiling is `threads - <reserve for healthchecks + UI traffic>`.
      Bump via REBOOTER_GUNICORN_THREADS if the fleet grows.
    - Operator-stop: `REBOOTER_LONG_POLL_DISABLED=1` forces the
      legacy no-wait path. Useful if a runaway thread storm needs
      to be defused without a code change.
    """
    import os
    import time

    device = g.current_device
    body_id = request.args.get("device_id")
    if body_id and body_id != device.id:
        return err(
            "device_mismatch",
            "device_id in query does not match authenticated device.",
            status=400,
        )

    long_poll_disabled = os.environ.get("REBOOTER_LONG_POLL_DISABLED") == "1"
    requested_wait = 0 if long_poll_disabled else _parse_prefer_wait(
        request.headers.get("Prefer")
    )

    cmds = list_pending_for_device(device.id, mark_delivered=True)

    def _serialize() -> dict:
        return {
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

    def _maybe_long_poll_headers(applied_wait: int) -> dict | None:
        if applied_wait <= 0:
            return None
        return {"Preference-Applied": f"wait={applied_wait}"}

    # Fast path — commands are already pending OR long-poll not requested.
    if cmds or requested_wait <= 0:
        return ok(_serialize(), headers=_maybe_long_poll_headers(requested_wait))

    # Slow path — hold the request open. Each loop iteration opens a
    # fresh session_scope() so the DB connection isn't pinned for the
    # whole wait. Sleep releases the GIL so other threads keep
    # serving requests in this worker.
    deadline = time.monotonic() + requested_wait
    while time.monotonic() < deadline:
        time.sleep(LONG_POLL_CHECK_INTERVAL_SECONDS)
        cmds = list_pending_for_device(device.id, mark_delivered=True)
        if cmds:
            return ok(
                _serialize(),
                headers=_maybe_long_poll_headers(requested_wait),
            )
    # Timeout — return the empty list. Device's next poll picks up
    # any commands that arrived between this return and the next call.
    return ok(_serialize(), headers=_maybe_long_poll_headers(requested_wait))


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


@bp.post("/power-samples")
@device_auth_required
def upload_power_samples():
    body = request.get_json(silent=True) or {}
    device = g.current_device
    if body.get("device_id") and body["device_id"] != device.id:
        return err("device_mismatch", "device_id mismatch.", status=400)
    samples = body.get("samples") or []
    if not isinstance(samples, list):
        return err("validation_failed", "samples must be a list", status=400)
    try:
        n = ingest_power_samples(device.id, samples)
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
