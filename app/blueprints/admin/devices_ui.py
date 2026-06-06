"""Admin UI handlers for devices — list page, detail page, edit, send-command,
delete, upgrade-to-latest, bulk-delete (UI), protection-toggle, cancel-command.

Split out of ``devices.py`` in v0.5.5; the original 630-line file mixed UI
and API handlers. The two sub-modules import the shared admin_ui_bp /
admin_api_bp objects and register routes against them as side-effects on
import. Endpoint names preserved across the split:

  admin_ui.list_devices_page
  admin_ui.device_detail_page
  admin_ui.device_update_submit
  admin_ui.device_delete_submit
  admin_ui.device_send_command
  admin_ui.device_cancel_command
  admin_ui.device_upgrade_to_latest_submit
  admin_ui.devices_bulk_delete_submit
  admin_ui.device_set_protection
"""

from __future__ import annotations

from flask import abort, g, redirect, render_template, request, url_for

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import (
    admin_required_ui,
    role_required_ui,
)
from app.models.users import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.services import audit as audit_service
from app.services import mass_action
from app.services.announcements import count_pending_announcements
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
    firmware_version_breakdown,
    get_device_detail,
    is_upgrade as _is_upgrade,
    latest_stable_release_dict,
    list_devices as svc_list_devices,
    merge_retire_device as svc_merge_retire_device,
    update_device,
)
from app.services.sites import list_sites as svc_list_sites_only
from app.services import schedules as schedules_svc
from app.services import watchdog as watchdog_svc


def _show_qa_fixtures(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


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
    pending_count = count_pending_announcements()

    # 0.6.24 PR-1: recent-reboots line for the hero. Pulls up to 3 most
    # recent device.rebooted events in the last 24h, joined to display
    # name + classifies ghost vs planned via details.last_planned_restart_reason.
    # Keeps the query small (LIMIT 6, in-Python uniquify by device).
    recent_reboots = _recent_reboot_summary(limit=3)

    return render_template(
        "devices_list.html",
        **_ctx(
            {
                "devices": devices,
                "fw_breakdown": fw_breakdown,
                "latest_stable": latest_stable,
                # v0.4.29: callable for the template to ask "would
                # going from <current> to <target> be a real upgrade
                # (numerically newer)?". Replaces the old `!=` check.
                "is_upgrade": _is_upgrade,
                "pending_adoption_count": pending_count,
                "recent_reboots": recent_reboots,
                "filters": {
                    "search": request.args.get("search", ""),
                    "status": request.args.get("status", ""),
                    "show_qa_fixtures": show_qa,
                    "chips": list(chips),
                },
            }
        ),
    )


def _recent_reboot_summary(limit: int = 3) -> list[dict]:
    """0.6.24 PR-1: top-`limit` most-recent reboots in the fleet, one
    row per device. Used by the hero recents line — operator's morning
    glance shows what actually rebooted overnight without scrolling."""
    from datetime import datetime, timezone, timedelta
    from app.db import session_scope
    from app.models import Device, DeviceEvent
    from sqlalchemy import select

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    seen: set[str] = set()
    out: list[dict] = []
    with session_scope() as s:
        rows = s.execute(
            select(DeviceEvent.device_id, DeviceEvent.timestamp, DeviceEvent.details,
                   Device.display_name)
            .join(Device, Device.id == DeviceEvent.device_id)
            .where(
                DeviceEvent.type == "device.rebooted",
                DeviceEvent.timestamp >= cutoff,
            )
            .order_by(DeviceEvent.timestamp.desc())
            .limit(limit * 4)  # over-fetch a bit so duplicates per device collapse
        ).all()
        for dev_id, ts, details, display_name in rows:
            if dev_id in seen:
                continue
            seen.add(dev_id)
            planned = (details or {}).get("last_planned_restart_reason") or ""
            out.append({
                "device_id": dev_id,
                "display_name": display_name or dev_id,
                "reason": planned if planned else "ghost",
                "is_ghost": not planned,
                "ts": ts,
            })
            if len(out) >= limit:
                break
    return out


@admin_ui_bp.get("/devices/<device_id>")
@admin_required_ui
def device_detail_page(device_id: str):
    detail = get_device_detail(device_id)
    if detail is None:
        abort(404)
    sites = svc_list_sites_only()
    # Tier-2 Feature 2: pre-populate the structured desired-config form
    # and decide whether it can round-trip the stored config without
    # loss (else the partial falls back to JSON-only).
    from app.blueprints.admin._device_config_forms import (
        desired_config_to_form_values,
        is_form_representable,
    )

    desired_cfg = detail.get("desired_config") or {}
    return render_template(
        "device_detail.html",
        **_ctx({
            "device": detail,
            "sites": sites,
            # v0.5.97: the Watchdog / Schedule sections were stubs —
            # now list the rules / schedules whose target resolves to
            # this device (directly or via a group).
            "watchdog_rules": watchdog_svc.list_rules_for_device(device_id),
            "device_schedules": schedules_svc.list_for_device(device_id),
            # Tier-2 Feature 2: friendly device-config form.
            "cfg_form": desired_config_to_form_values(desired_cfg),
            "form_supported": is_form_representable(desired_cfg),
            "config_support_badges": CONFIG_SUPPORT_BADGES,
        }),
    )


# Tier-2 Feature 2: per-key support tier for the device-config form,
# mirroring the support tiers in docs/firmware-apply-config-schema-v01.md.
# `device_name` is the only key validated end-to-end with the firmware;
# every other key is "accepted" — parsed by apply_config but the drift
# round-trip is not yet individually verified.
CONFIG_SUPPORT_BADGES = {
    "device_name": "verified",
    "relay_restore_behavior": "accepted",
    "monitor_interval_seconds": "accepted",
    "boot_warmup_seconds": "accepted",
    "manual_button_enabled": "accepted",
    "internet": "accepted",
    "device": "accepted",
    "notifications": "accepted",
    "power": "accepted",
}


@admin_ui_bp.post("/devices/<device_id>")
@admin_required_ui
def device_update_submit(device_id: str):
    before = get_device_detail(device_id)
    if before is None:
        abort(404)
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
    renamed = before.get("display_name") != updated.get("display_name")
    sync_enqueued = False
    if renamed:
        sync_enqueued = enqueue_display_name_sync(
            device_id,
            display_name=updated.get("display_name"),
            issued_by_user_id=g.current_user.id,
            reason="device_update_submit",
        )
    audit_service.record(
        "device.updated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
        details={
            "fields": [k for k, v in patch.items() if v is not None],
            "display_name_sync_enqueued": sync_enqueued,
        },
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


# S1-7: merge/retire duplicate device rows. Operator picks which of two
# same-MAC rows to keep; the other is decommissioned (NOT hard-deleted,
# so FK-dependent history is preserved).
@admin_ui_bp.post("/devices/merge-retire")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def device_merge_retire_submit():
    from flask import flash

    keep_id = (request.form.get("keep_device_id") or "").strip()
    retire_id = (request.form.get("retire_device_id") or "").strip()
    try:
        result = svc_merge_retire_device(keep_id, retire_id)
    except MergeRetireError as e:
        flash(f"Merge failed: {e.message}", "error")
        return redirect(url_for("admin_ui.list_devices_page"))
    audit_service.record(
        "device.merge_retired",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=retire_id,
        details=result,
    )
    flash(
        f"Retired duplicate device {retire_id} "
        f"(decommissioned); kept {keep_id} for MAC {result['mac_address']}.",
        "info",
    )
    return redirect(url_for("admin_ui.list_devices_page"))


@admin_ui_bp.post("/devices/<device_id>/upgrade-to-latest")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def device_upgrade_to_latest_submit(device_id: str):
    """v0.4.21: one-click upgrade. Deploys the current latest
    ``stable`` channel release to a single device. Operator sees
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
    # v0.5.14 (B18): when the toggle is invoked from the devices list,
    # honour `next=list` so the operator stays on the list view
    # instead of getting punted to the detail page on every click.
    next_target = (request.form.get("next") or "").strip()
    if next_target == "list":
        success_redirect = url_for("admin_ui.list_devices_page")
    else:
        success_redirect = url_for(
            "admin_ui.device_detail_page", device_id=device_id
        )
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
        return redirect(success_redirect)
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
            "via": "list_inline_toggle" if next_target == "list" else "detail_form",
        },
    )
    if next_target == "list":
        flash(
            f"{cmd_type} queued for {device_id}. Command id: {cmd.id}",
            "info",
        )
    return redirect(success_redirect)


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
        "info",
    )
    return redirect(url_for("admin_ui.device_detail_page", device_id=device_id))


# v0.5.22 (B21): desired-config + drift detection endpoints.

@admin_ui_bp.post("/devices/<device_id>/desired-config")
@admin_required_ui
def device_desired_config_save_submit(device_id: str):
    """Operator submits the device-detail 'Desired config' card.

    Tier-2 Feature 2: the form posts an `editor` discriminator —
    `form` runs the structured-form builder, `json` (or absent, for
    backwards compatibility) runs the original raw-JSON parse path.
    Either way the service (`set_desired_config`) is the validation
    backstop and the audit event is the same.
    """
    import json
    from flask import flash

    from app.services import device_config

    desired_mode = (request.form.get("desired_mode") or "").strip() or None
    editor = (request.form.get("editor") or "json").strip().lower()

    if editor == "form":
        # Structured form: build the payload from the flat cfg_* fields.
        from app.blueprints.admin._device_config_forms import (
            DeviceConfigFormError,
            build_desired_config_from_form,
        )

        existing = device_config.get_desired_config(device_id) or {}
        try:
            payload = build_desired_config_from_form(
                request.form, existing=existing
            )
        except DeviceConfigFormError as e:
            flash(str(e), "error")
            return redirect(
                url_for("admin_ui.device_detail_page", device_id=device_id)
            )
    else:
        # Raw-JSON escape hatch (the original v0.5.22 path).
        raw = (request.form.get("desired_config_json") or "").strip()
        if not raw:
            payload = {}
        else:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as e:
                flash(f"Invalid JSON: {e}", "error")
                return redirect(
                    url_for("admin_ui.device_detail_page", device_id=device_id)
                )
            if not isinstance(payload, dict):
                flash("Desired config must be a JSON object.", "error")
                return redirect(
                    url_for("admin_ui.device_detail_page", device_id=device_id)
                )

    if not payload:
        # Empty submit → clear the desired_config.
        try:
            device_config.set_desired_config(
                device_id, {}, by_user_id=g.current_user.id, desired_mode=desired_mode
            )
        except device_config.DesiredConfigError as e:
            flash(str(e), "error")
            return redirect(url_for("admin_ui.device_detail_page", device_id=device_id))
        flash("Desired config cleared.", "info")
        audit_service.record(
            "device.desired_config_cleared",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="device",
            target_id=device_id,
        )
        return redirect(url_for("admin_ui.device_detail_page", device_id=device_id))

    try:
        out = device_config.set_desired_config(
            device_id, payload, by_user_id=g.current_user.id, desired_mode=desired_mode
        )
    except device_config.DesiredConfigError as e:
        flash(str(e), "error")
        return redirect(url_for("admin_ui.device_detail_page", device_id=device_id))
    if out is None:
        abort(404)
    audit_service.record(
        "device.desired_config_set",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
        details={
            "keys": sorted(payload.keys()),
            "desired_mode": desired_mode,
            "editor": editor,
        },
    )
    flash(
        f"Desired config saved ({len(payload)} key{'' if len(payload)==1 else 's'}).",
        "info",
    )
    return redirect(url_for("admin_ui.device_detail_page", device_id=device_id))


@admin_ui_bp.post("/devices/<device_id>/desired-config/push")
@admin_required_ui
def device_desired_config_push_submit(device_id: str):
    """Manual 'Push to device now' button on the Desired-config card.
    Manual push always fires regardless of the desired_config.enabled
    feature flag — explicit operator intent."""
    from flask import flash

    from app.services import device_config

    result = device_config.push_desired_config(
        device_id,
        source="manual",
        issued_by_user_id=g.current_user.id,
    )
    audit_service.record(
        "device.desired_config_pushed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
        details=result,
    )
    if result.get("enqueued"):
        flash(
            f"Push enqueued as command {result.get('command_id')}; "
            f"device will apply on next /device/commands poll.",
            "info",
        )
    else:
        flash(
            f"Push skipped: {result.get('reason')}",
            "warning" if result.get("reason") else "info",
        )
    return redirect(url_for("admin_ui.device_detail_page", device_id=device_id))
