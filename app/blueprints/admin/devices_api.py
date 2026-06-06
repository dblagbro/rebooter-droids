"""Admin JSON API handlers for devices — list, get, patch, send-command,
delete, bulk-delete, cancel-command.

Split out of ``devices.py`` in v0.5.5; the original 630-line file mixed
UI and API handlers. Endpoint names preserved across the split:

  admin_api.list_devices
  admin_api.get_device
  admin_api.patch_device
  admin_api.send_device_command
  admin_api.delete_device_api
  admin_api.devices_bulk_delete_api
  admin_api.cancel_device_command_api
"""

from __future__ import annotations

from flask import g, request

from app.blueprints.admin import admin_api_bp
from app.middleware.admin_auth import (
    ADMIN_AND_UP,
    WRITE_ROLES,
    admin_required_api,
    role_required_api,
    scope_required_api,
)
from app.middleware.response import err, ok
from app.models.users import ROLE_OPERATOR, ROLE_VIEWER
from app.services import audit as audit_service
from app.services import mass_action
from app.services.commands import (
    DeviceLockedError,
    cancel_pending_command,
    enqueue_for_device,
)
from app.services.devices import (
    enqueue_display_name_sync,
    MergeRetireError,
    UnknownPatchFieldError,
    delete_device as svc_delete_device,
    delete_devices_bulk as svc_delete_devices_bulk,
    get_device_detail,
    list_devices as svc_list_devices,
    merge_retire_device as svc_merge_retire_device,
    update_device,
)


def _show_qa_fixtures(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


# #167 / 0.6.12: slim live-state endpoint the device list/detail pages
# poll every ~3s for real-time online/offline + relay state without a
# full page render. Returns one row per device with just the fields the
# UI flips: heartbeat_state, latest_relay_on, last_seen_at, heap +
# fragmentation, pending command count. Excludes deprovisioned devices.
@admin_api_bp.get("/devices/live")
@admin_required_api
def devices_live():
    """0.6.18 review fixes (#3 + #7):
    - Was: `select(DeviceHeartbeat).where(device_id.in_(ids))` with no LIMIT
      pulled the full append-only heartbeat history for every device on
      every 3s poll. Replaced with `_latest_heartbeat_by_device`, which is
      a correlated subquery on the indexed (device_id, received_at DESC).
    - Was: hand-rolled `Device.registration_state != 'deprovisioned'`
      filter bypassed the per-tenant scope filter that list_devices
      applies, leaking other tenants' devices into the live feed.
      Replaced with the same `filter_devices_with_shadow_logging` call.
    """
    from datetime import datetime, timezone
    from app.db import session_scope
    from app.models.commands import Command
    from app.models.devices import Device
    from app.services.devices._query import _latest_heartbeat_by_device
    from app.services.devices._serialize import _heartbeat_state_for
    from app.services.rbac_filter import filter_devices_with_shadow_logging
    from sqlalchemy import func, select

    now = datetime.now(timezone.utc)
    out = []
    with session_scope() as s:
        stmt = (
            select(Device.id, Device.last_seen_at, Device.last_heartbeat_at)
            .where(Device.registration_state != "deprovisioned")
        )
        device_rows = filter_devices_with_shadow_logging(stmt, s)
        device_ids = [r.id for r in device_rows]
        hbs = _latest_heartbeat_by_device(s, device_ids) if device_ids else {}
        pending: dict[str, int] = {}
        if device_ids:
            for dev_id, n in s.execute(
                select(Command.device_id, func.count(Command.id))
                .where(Command.device_id.in_(device_ids), Command.status == "pending")
                .group_by(Command.device_id)
            ).all():
                pending[dev_id] = int(n)
        for d in device_rows:
            hb = hbs.get(d.id)
            hb_state = _heartbeat_state_for(
                d.last_heartbeat_at, now=now, offline_threshold_seconds=180,
                last_seen_at=d.last_seen_at,
            )
            # Latest heap snapshot from the trajectory ring (fw 0.2.10+);
            # None for older firmware that doesn't carry the ring.
            traj = (hb.heap_trajectory if hb else None) or []
            last_sample = traj[-1] if isinstance(traj, list) and traj else {}
            if not isinstance(last_sample, dict):
                last_sample = {}
            out.append({
                "id": d.id,
                "heartbeat_state": hb_state,
                "online": hb_state == "online",
                "latest_relay_on": (bool(hb.relay_on) if hb else None),
                "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
                "uptime_seconds": (hb.uptime_seconds if hb else None),
                "health_state": (hb.health_state if hb else None),
                "wifi_rssi_dbm": (hb.wifi_rssi_dbm if hb else None),
                "free_heap": last_sample.get("fh"),
                "max_free_block": last_sample.get("mfb"),
                "heap_fragmentation_pct": last_sample.get("fp"),
                "pending_commands": pending.get(d.id, 0),
            })
    return ok({"devices": out, "ts": now.isoformat()})


@admin_api_bp.get("/devices")
@admin_required_api
def list_devices():
    show_qa = _show_qa_fixtures(request.args.get("show_qa_fixtures"), default=True)
    chips = tuple(request.args.getlist("chip"))
    devices = svc_list_devices(
        site_id=request.args.get("site_id"),
        group_id=request.args.get("group_id"),
        search=request.args.get("search"),
        status=request.args.get("status"),
        include_qa_fixtures=show_qa,
        chips=chips,
    )
    return ok({"devices": devices, "total": len(devices)})


# v0.5.35 (B1 RBAC Phase 1): demonstrator route #1 — read path. The
# scope decorator runs after admin_required_api sets g.current_user; in
# shadow mode an out-of-scope caller is logged but still served.
@admin_api_bp.get("/devices/<device_id>")
@admin_required_api
@scope_required_api(ROLE_VIEWER, scope="device", id_kwarg="device_id")
def get_device(device_id: str):
    detail = get_device_detail(device_id)
    if detail is None:
        return err("device_unknown", "Device not found.", status=404)
    return ok(detail)


@admin_api_bp.patch("/devices/<device_id>")
@role_required_api(*ADMIN_AND_UP)
def patch_device(device_id: str):
    body = request.get_json(silent=True) or {}
    before = get_device_detail(device_id)
    if before is None:
        return err("device_unknown", "Device not found.", status=404)
    try:
        updated = update_device(device_id, body)
    except UnknownPatchFieldError as e:
        return err("validation_failed", str(e), status=400)
    if updated is None:
        return err("device_unknown", "Device not found.", status=404)
    renamed = before.get("display_name") != updated.get("display_name")
    sync_enqueued = False
    if renamed:
        sync_enqueued = enqueue_display_name_sync(
            device_id,
            display_name=updated.get("display_name"),
            issued_by_user_id=g.current_user.id,
            reason="patch_device_api",
        )
    audit_service.record(
        "device.updated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
        details={
            "fields": sorted(body.keys()),
            "display_name_sync_enqueued": sync_enqueued,
        },
    )
    return ok(updated)


# v0.5.35 (B1 RBAC Phase 1): demonstrator route #2 — write path.
@admin_api_bp.post("/devices/<device_id>/commands")
@role_required_api(*WRITE_ROLES)
@scope_required_api(ROLE_OPERATOR, scope="device", id_kwarg="device_id")
def send_device_command(device_id: str):
    body = request.get_json(silent=True) or {}
    cmd_type = body.get("type")
    if not cmd_type:
        return err("validation_failed", "type is required", status=400)
    override_lockout = bool(body.get("override_lockout"))
    set_hold_off = bool(body.get("hold_off"))
    try:
        cmd = enqueue_for_device(
            device_id=device_id,
            cmd_type=cmd_type,
            payload=body.get("payload"),
            issued_by_user_id=g.current_user.id,
            ttl_seconds=body.get("ttl_seconds"),
            override_lockout=override_lockout,
            set_hold_off=set_hold_off,
        )
    except LookupError:
        return err("device_unknown", "Device not found.", status=404)
    except DeviceLockedError as e:
        return err("device_locked", str(e), status=423)  # 423 Locked
    except ValueError as e:
        return err("validation_failed", str(e), status=400)
    # v0.5.35 (B1 RBAC Phase 1): per-resource mutation routed through the
    # record_scoped() choke-point so the audit row carries its RBAC scope
    # claim — this is the seam B11 multi-hub sync writes outbox events from.
    audit_service.record_scoped(
        "device.command_issued",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
        scope_claim={"scope_type": "device", "scope_id": device_id},
        details={
            "type": cmd_type,
            "command_id": cmd.id,
            "reason": "operator",
            "override_lockout": override_lockout,
            "set_hold_off": set_hold_off,
        },
    )
    return ok(
        {
            "command_id": cmd.id,
            "type": cmd.type,
            "status": cmd.status,
            "expires_at": cmd.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        status=201,
    )


@admin_api_bp.delete("/devices/<device_id>")
@role_required_api(*ADMIN_AND_UP)
def delete_device_api(device_id: str):
    if not svc_delete_device(device_id):
        return err("device_unknown", "Device not found.", status=404)
    audit_service.record(
        "device.deleted",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
    )
    return ok({"deleted": True})


# v0.3.4 (P3): bulk delete (API). Body: {"device_ids": [...],
# "override_lockout": bool, "confirmation_level": ..., "confirmation_typed_value": ...}
@admin_api_bp.post("/devices/bulk-delete")
@role_required_api(*ADMIN_AND_UP)
def devices_bulk_delete_api():
    body = request.get_json(silent=True) or {}
    raw_ids = body.get("device_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        return err("validation_failed", "device_ids must be a non-empty list", status=400)
    # Dedupe defensively (mirrors the UI handler).
    ids = list(dict.fromkeys(i for i in raw_ids if i))
    override_lockout = bool(body.get("override_lockout"))
    try:
        mass_action.validate(
            target_count=len(ids),
            expected_typed_value="delete",
            confirmation_level=body.get("confirmation_level"),
            confirmation_typed_value=body.get("confirmation_typed_value"),
        )
    except mass_action.ConfirmationRequired as e:
        return err(
            "confirmation_required",
            str(e),
            status=409,
            extra={
                "target_count": e.target_count,
                "required_level": e.required_level,
                "expected_typed_value": e.expected_typed_value,
            },
        )
    result = svc_delete_devices_bulk(ids, override_lockout=override_lockout)
    audit_service.record(
        "device.bulk_deleted",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=None,
        details={
            "deleted_count": len(result["deleted"]),
            "skipped_protected": len(result["skipped_protected"]),
            "skipped_unknown": len(result["skipped_unknown"]),
            "deleted_ids": result["deleted"],
            "skipped_protected_ids": result["skipped_protected"],
            "override_lockout": override_lockout,
            "confirmation_level": mass_action.required_level(len(ids)),
            "reason": "operator",
        },
    )
    # v0.4.9 (B14): per-device audit row for every device touched.
    audit_service.record_per_device(
        "device.bulk_deleted_per_device",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        device_ids=result["deleted"],
        base_details={"via": "bulk_delete", "override_lockout": override_lockout, "outcome": "deleted"},
    )
    audit_service.record_per_device(
        "device.bulk_delete_skipped_per_device",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        device_ids=result["skipped_protected"],
        base_details={"via": "bulk_delete", "outcome": "skipped", "reason": "is_protected"},
    )
    return ok(result, status=200)


# S1-7: merge/retire duplicate device rows (JSON API). Body:
# {"keep_device_id": "...", "retire_device_id": "..."}. The retired row
# is decommissioned, never hard-deleted — FK-dependent history stays.
@admin_api_bp.post("/devices/merge-retire")
@role_required_api(*ADMIN_AND_UP)
def devices_merge_retire_api():
    body = request.get_json(silent=True) or {}
    keep_id = (body.get("keep_device_id") or "").strip()
    retire_id = (body.get("retire_device_id") or "").strip()
    try:
        result = svc_merge_retire_device(keep_id, retire_id)
    except MergeRetireError as e:
        status = 404 if e.code == "not_found" else 409
        return err(e.code, e.message, status=status)
    audit_service.record(
        "device.merge_retired",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=retire_id,
        details={**result, "via": "api"},
    )
    return ok(result)


# v0.3.2 (P3): cancel a queued (pending-status) command (R-CTRL-8).
@admin_api_bp.post("/devices/<device_id>/commands/<command_id>/cancel")
@role_required_api(*WRITE_ROLES)
def cancel_device_command_api(device_id: str, command_id: str):
    if not cancel_pending_command(command_id, by_user_id=g.current_user.id):
        return err(
            "not_cancellable",
            "Command not found or no longer cancellable (already accepted by device).",
            status=409,
        )
    audit_service.record(
        "device.command_cancelled",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
        details={"command_id": command_id, "reason": "operator"},
    )
    return ok({"cancelled": True, "command_id": command_id})
