"""Admin UI + API for devices — list, detail, edit, command, delete.

Endpoint names preserved across the v0.2.5 → v0.2.6 split:
  admin_ui.list_devices_page, admin_ui.device_detail_page,
  admin_ui.device_update_submit, admin_ui.device_delete_submit,
  admin_ui.device_send_command,
  admin_api.list_devices, admin_api.get_device, admin_api.patch_device,
  admin_api.send_device_command, admin_api.delete_device_api.
"""

from __future__ import annotations

from flask import abort, g, redirect, render_template, request, url_for

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import (
    ADMIN_AND_UP,
    WRITE_ROLES,
    admin_required_api,
    admin_required_ui,
    role_required_api,
    role_required_ui,
)
from app.middleware.response import err, ok
from app.models.users import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.services import audit as audit_service
from app.services.commands import enqueue_for_device
from app.services.devices import (
    UnknownPatchFieldError,
    delete_device as svc_delete_device,
    get_device_detail,
    list_devices as svc_list_devices,
    update_device,
)
from app.services.sites import list_sites as svc_list_sites_only


# ── UI ─────────────────────────────────────────────────────────────────────

@admin_ui_bp.get("/devices")
@admin_required_ui
def list_devices_page():
    # v0.2.8: QA-fixture toggle. Default in v0.2.8 is to *show* fixtures
    # (include_qa_fixtures=True) so operators see the new toggle without
    # data disappearing under them; v0.2.9 will flip the default to hide.
    show_qa = _show_qa_fixtures(request.args.get("show_qa_fixtures"), default=True)
    devices = svc_list_devices(
        site_id=request.args.get("site_id"),
        group_id=request.args.get("group_id"),
        search=request.args.get("search"),
        status=request.args.get("status"),
        include_qa_fixtures=show_qa,
    )
    return render_template(
        "devices_list.html",
        **_ctx(
            {
                "devices": devices,
                "filters": {
                    "search": request.args.get("search", ""),
                    "status": request.args.get("status", ""),
                    "show_qa_fixtures": show_qa,
                },
            }
        ),
    )


def _show_qa_fixtures(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


@admin_ui_bp.get("/devices/<device_id>")
@admin_required_ui
def device_detail_page(device_id: str):
    detail = get_device_detail(device_id)
    if detail is None:
        abort(404)
    sites = svc_list_sites_only()
    return render_template(
        "device_detail.html", **_ctx({"device": detail, "sites": sites})
    )


@admin_ui_bp.post("/devices/<device_id>")
@admin_required_ui
def device_update_submit(device_id: str):
    site_id = (request.form.get("site_id") or "").strip()
    patch = {
        "display_name": request.form.get("display_name") or "",
        "notes": request.form.get("notes") or None,
        "central_management_enabled": "central_management_enabled" in request.form,
        "site_id": site_id or None,
    }
    try:
        updated = update_device(device_id, patch)
    except UnknownPatchFieldError:
        abort(400)
    if updated is None:
        abort(404)
    audit_service.record(
        "device.updated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
        details={"fields": [k for k, v in patch.items() if v is not None]},
    )
    return redirect(url_for("admin_ui.device_detail_page", device_id=device_id))


@admin_ui_bp.post("/devices/<device_id>/delete")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def device_delete_submit(device_id: str):
    if svc_delete_device(device_id):
        audit_service.record(
            "device.deleted",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="device",
            target_id=device_id,
        )
    return redirect(url_for("admin_ui.list_devices_page"))


@admin_ui_bp.post("/devices/<device_id>/commands")
@admin_required_ui
def device_send_command(device_id: str):
    cmd_type = (request.form.get("type") or "").strip()
    if not cmd_type:
        abort(400)
    payload: dict = {}
    if cmd_type == "relay_cycle":
        try:
            payload["power_off_seconds"] = int(request.form.get("power_off_seconds") or 5)
        except ValueError:
            payload["power_off_seconds"] = 5
        try:
            payload["post_reboot_holdoff_seconds"] = int(
                request.form.get("post_reboot_holdoff_seconds") or 180
            )
        except ValueError:
            payload["post_reboot_holdoff_seconds"] = 180
    try:
        enqueue_for_device(
            device_id=device_id,
            cmd_type=cmd_type,
            payload=payload,
            issued_by_user_id=g.current_user.id,
        )
    except (LookupError, ValueError):
        abort(400)
    return redirect(url_for("admin_ui.device_detail_page", device_id=device_id))


# ── API ────────────────────────────────────────────────────────────────────

@admin_api_bp.get("/devices")
@admin_required_api
def list_devices():
    show_qa = _show_qa_fixtures(request.args.get("show_qa_fixtures"), default=True)
    devices = svc_list_devices(
        site_id=request.args.get("site_id"),
        group_id=request.args.get("group_id"),
        search=request.args.get("search"),
        status=request.args.get("status"),
        include_qa_fixtures=show_qa,
    )
    return ok({"devices": devices, "total": len(devices)})


@admin_api_bp.get("/devices/<device_id>")
@admin_required_api
def get_device(device_id: str):
    detail = get_device_detail(device_id)
    if detail is None:
        return err("device_unknown", "Device not found.", status=404)
    return ok(detail)


@admin_api_bp.patch("/devices/<device_id>")
@role_required_api(*ADMIN_AND_UP)
def patch_device(device_id: str):
    body = request.get_json(silent=True) or {}
    try:
        updated = update_device(device_id, body)
    except UnknownPatchFieldError as e:
        return err("validation_failed", str(e), status=400)
    if updated is None:
        return err("device_unknown", "Device not found.", status=404)
    audit_service.record(
        "device.updated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
        details={"fields": sorted(body.keys())},
    )
    return ok(updated)


@admin_api_bp.post("/devices/<device_id>/commands")
@role_required_api(*WRITE_ROLES)
def send_device_command(device_id: str):
    body = request.get_json(silent=True) or {}
    cmd_type = body.get("type")
    if not cmd_type:
        return err("validation_failed", "type is required", status=400)
    try:
        cmd = enqueue_for_device(
            device_id=device_id,
            cmd_type=cmd_type,
            payload=body.get("payload"),
            issued_by_user_id=g.current_user.id,
            ttl_seconds=body.get("ttl_seconds"),
        )
    except LookupError:
        return err("device_unknown", "Device not found.", status=404)
    except ValueError as e:
        return err("validation_failed", str(e), status=400)
    audit_service.record(
        "device.command_issued",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
        details={"type": cmd_type, "command_id": cmd.id},
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
