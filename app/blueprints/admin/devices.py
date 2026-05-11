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
from app.services.commands import (
    DeviceLockedError,
    cancel_pending_command,
    enqueue_for_device,
)
from app.services.announcements import count_pending_announcements
from app.services.devices import (
    UnknownPatchFieldError,
    delete_device as svc_delete_device,
    delete_devices_bulk as svc_delete_devices_bulk,
    firmware_version_breakdown,
    get_device_detail,
    is_upgrade as _is_upgrade,
    latest_stable_release_dict,
    list_devices as svc_list_devices,
    update_device,
)
from app.services import mass_action
from app.services.sites import list_sites as svc_list_sites_only


# ── UI ─────────────────────────────────────────────────────────────────────

@admin_ui_bp.get("/devices")
@admin_required_ui
def list_devices_page():
    # v0.2.8: QA-fixture toggle. Default in v0.2.8 is to *show* fixtures
    # (include_qa_fixtures=True) so operators see the new toggle without
    # data disappearing under them; v0.2.9 will flip the default to hide.
    show_qa = _show_qa_fixtures(request.args.get("show_qa_fixtures"), default=True)
    # v0.3.1 (P2): saved-filter chips. Multiple via repeated `?chip=...`
    # query params; URL round-trips so a saved view is shareable.
    chips = tuple(request.args.getlist("chip"))
    devices = svc_list_devices(
        site_id=request.args.get("site_id"),
        group_id=request.args.get("group_id"),
        search=request.args.get("search"),
        status=request.args.get("status"),
        include_qa_fixtures=show_qa,
        chips=chips,
    )
    # v0.4.19 (Tier-1 A): per-firmware-version breakdown so the
    # operator can spot upgrade outliers at a glance. Excludes QA
    # fixtures regardless of the show_qa toggle — outliers among
    # synthetic test rows aren't meaningful.
    fw_breakdown = firmware_version_breakdown(include_qa_fixtures=False)

    # v0.4.21: latest stable release the templates use to render
    # the per-row "Upgrade to X.Y.Z" button when a device is
    # behind. None when no stable release tracked → no buttons.
    latest_stable = latest_stable_release_dict()

    # v0.5.2: pending-adoption count for the sub-header chip.
    # Pre-fix the page rendered "{{ devices|length }} device · Pending
    # adoption →" which the eye reads as "1 device pending adoption"
    # — bound the count to the link directly so the meaning is
    # unambiguous.
    pending_count = count_pending_announcements()

    return render_template(
        "devices_list.html",
        **_ctx(
            {
                "devices": devices,
                "fw_breakdown": fw_breakdown,
                "latest_stable": latest_stable,
                # v0.4.29: callable for the template to ask "would
                # going from <current> to <target> be a real
                # upgrade (numerically newer)?". Replaces the old
                # `!=` check that fired on downgrades too.
                "is_upgrade": _is_upgrade,
                "pending_adoption_count": pending_count,
                "filters": {
                    "search": request.args.get("search", ""),
                    "status": request.args.get("status", ""),
                    "show_qa_fixtures": show_qa,
                    "chips": list(chips),
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


@admin_ui_bp.post("/devices/<device_id>/upgrade-to-latest")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def device_upgrade_to_latest_submit(device_id: str):
    """v0.4.21: one-click upgrade. Deploys the current latest
    `stable` channel release to a single device. Operator sees
    this button on the devices list when the device's
    firmware_version doesn't match the latest stable's version.

    Equivalent to going to /app/firmware → picking the release →
    selecting target=device → typing the device id, just folded
    into one click on the devices list."""
    from flask import flash
    from app.services.deployments import create_deployment

    latest = latest_stable_release_dict()
    if latest is None:
        flash(
            "No stable firmware release tracked yet. "
            "Upload one via /app/firmware or run the on-disk scan.",
            "error",
        )
        return redirect(url_for("admin_ui.list_devices_page"))

    # v0.4.29: refuse a non-upgrade at the API layer too. The
    # template hides the button when it would be a downgrade, but
    # a stale page or a directly-posted form must not be able to
    # silently push an older firmware to a device.
    detail = get_device_detail(device_id)
    current_fw = detail.get("firmware_version") if detail else None
    if not _is_upgrade(latest["version"], current_fw):
        flash(
            f"Refused: device {device_id} is already on {current_fw}, "
            f"which is not older than the latest stable {latest['version']}. "
            "No deployment created.",
            "warning",
        )
        return redirect(url_for("admin_ui.list_devices_page"))

    try:
        out = create_deployment(
            release_id=latest["id"],
            target_type="device",
            target_id=device_id,
            channel=latest.get("channel", "stable"),
            force=False,
            issued_by_user_id=g.current_user.id,
        )
    except (LookupError, ValueError) as e:
        flash(f"Upgrade failed: {e}", "error")
        return redirect(url_for("admin_ui.list_devices_page"))

    audit_service.record(
        "device.upgrade_initiated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
        details={
            "via": "devices_list_upgrade_button",
            "release_id": latest["id"],
            "release_version": latest["version"],
            "deployment_id": out.get("id"),
        },
    )
    flash(
        f"Upgrade to {latest['version']} queued for the device. "
        f"Device will pick up the deployment on its next command-poll.",
        "info",
    )
    return redirect(url_for("admin_ui.list_devices_page"))


# v0.3.4 (P3): bulk-delete from the devices list.
@admin_ui_bp.post("/devices/bulk-delete")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def devices_bulk_delete_submit():
    from flask import flash

    # v0.3.5 fix: dedupe device_id list. The list page renders both
    # desktop-table and mobile-card layouts in the DOM; without
    # JS pair-sync we used to receive each id twice, and a stray
    # double-submission could otherwise inflate the count.
    ids = list(dict.fromkeys(i for i in request.form.getlist("device_id") if i))
    if not ids:
        flash("Select at least one device first.", "warning")
        return redirect(url_for("admin_ui.list_devices_page"))

    override_lockout = (request.form.get("override_lockout") or "").lower() in ("1", "true", "yes")
    try:
        mass_action.validate(
            target_count=len(ids),
            expected_typed_value="delete",
            confirmation_level=request.form.get("confirmation_level"),
            confirmation_typed_value=request.form.get("confirmation_typed_value"),
        )
    except mass_action.ConfirmationRequired as e:
        flash(
            f"Bulk delete affects {len(ids)} devices and requires "
            f"confirmation ({e.required_level}). "
            f"Re-submit through the confirmation prompt.",
            "error",
        )
        return redirect(url_for("admin_ui.list_devices_page"))

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
    msg_parts = [f"Deleted {len(result['deleted'])} device(s)."]
    if result["skipped_protected"]:
        msg_parts.append(
            f"{len(result['skipped_protected'])} protected — re-submit with "
            f"override to include them."
        )
    if result["skipped_unknown"]:
        msg_parts.append(
            f"{len(result['skipped_unknown'])} unknown id(s) skipped."
        )
    flash(" ".join(msg_parts), "info")
    return redirect(url_for("admin_ui.list_devices_page"))


@admin_ui_bp.post("/devices/<device_id>/commands")
@admin_required_ui
def device_send_command(device_id: str):
    from flask import flash

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
    override_lockout = (request.form.get("override_lockout") or "").lower() in ("1", "true", "yes")
    set_hold_off = (request.form.get("hold_off") or "").lower() in ("1", "true", "yes")
    try:
        cmd = enqueue_for_device(
            device_id=device_id,
            cmd_type=cmd_type,
            payload=payload,
            issued_by_user_id=g.current_user.id,
            override_lockout=override_lockout,
            set_hold_off=set_hold_off,
        )
    except DeviceLockedError:
        flash(
            "This device is protected. Tick 'Override lockout' on the form "
            "to issue power commands against it.",
            "error",
        )
        return redirect(url_for("admin_ui.device_detail_page", device_id=device_id))
    except (LookupError, ValueError):
        abort(400)
    audit_service.record(
        "device.command_issued",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
        details={
            "type": cmd_type,
            "command_id": cmd.id,
            "reason": "operator",
            "override_lockout": override_lockout,
            "set_hold_off": set_hold_off,
        },
    )
    return redirect(url_for("admin_ui.device_detail_page", device_id=device_id))


# v0.3.2 (P3): cancel a queued command before delivery (R-CTRL-8).
@admin_ui_bp.post("/devices/<device_id>/commands/<command_id>/cancel")
@admin_required_ui
def device_cancel_command(device_id: str, command_id: str):
    from flask import flash

    if cancel_pending_command(command_id, by_user_id=g.current_user.id):
        audit_service.record(
            "device.command_cancelled",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="device",
            target_id=device_id,
            details={"command_id": command_id, "reason": "operator"},
        )
        flash("Pending command cancelled.", "info")
    else:
        flash(
            "Could not cancel that command — it may have already been delivered.",
            "warning",
        )
    return redirect(url_for("admin_ui.device_detail_page", device_id=device_id))


# v0.3.2 (P3): toggle the device's `is_protected` lockout (R-DEV-8).
@admin_ui_bp.post("/devices/<device_id>/protection")
@admin_required_ui
def device_set_protection(device_id: str):
    from flask import flash

    raw = (request.form.get("is_protected") or "").lower()
    new_value = raw in ("1", "true", "on", "yes")
    try:
        updated = update_device(device_id, {"is_protected": new_value})
    except UnknownPatchFieldError:
        abort(400)
    if updated is None:
        abort(404)
    audit_service.record(
        "device.protection_changed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
        details={"is_protected": new_value, "reason": "operator"},
    )
    flash(
        "Device is now protected. Power commands require explicit override."
        if new_value
        else "Device protection cleared.",
        "info" if new_value else "info",
    )
    return redirect(url_for("admin_ui.device_detail_page", device_id=device_id))


# ── API ────────────────────────────────────────────────────────────────────

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
    audit_service.record(
        "device.command_issued",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
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
