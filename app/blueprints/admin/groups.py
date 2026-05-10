"""Admin UI + API for groups: list, create, detail, members, delete,
fan-out commands (with mass-action confirmation gate)."""

from __future__ import annotations

from flask import abort, flash, g, redirect, render_template, request, url_for

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import (
    ADMIN_AND_UP,
    admin_required_api,
    admin_required_ui,
    role_required_api,
    role_required_ui,
)
from app.middleware.response import err, ok
from app.models.users import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.services import audit as audit_service
from app.services import mass_action
from app.services.commands import enqueue_for_group
from app.services.devices import list_devices as svc_list_devices
from app.services.groups import (
    DuplicateNameError as DuplicateGroupName,
    add_members,
    create_group as svc_create_group,
    delete_group as svc_delete_group,
    delete_groups_bulk as svc_delete_groups_bulk,
    get_group_detail,
    list_groups as svc_list_groups,
    remove_member,
)


# ── UI ─────────────────────────────────────────────────────────────────────

@admin_ui_bp.get("/groups")
@admin_required_ui
def list_groups_page():
    groups = svc_list_groups()
    return render_template("groups_list.html", **_ctx({"groups": groups}))


@admin_ui_bp.post("/groups")
@admin_required_ui
def create_group_submit():
    name = (request.form.get("name") or "").strip()
    if not name:
        abort(400)
    try:
        svc_create_group(
            name=name,
            description=(request.form.get("description") or "").strip() or None,
            site_id=None,
        )
    except DuplicateGroupName:
        groups = svc_list_groups()
        return (
            render_template(
                "groups_list.html",
                **_ctx({"groups": groups, "error": f"A group named '{name}' already exists."}),
            ),
            409,
        )
    return redirect(url_for("admin_ui.list_groups_page"))


@admin_ui_bp.get("/groups/<group_id>")
@admin_required_ui
def group_detail_page(group_id: str):
    detail = get_group_detail(group_id)
    if detail is None:
        abort(404)
    available = svc_list_devices()
    member_ids = {m["id"] for m in detail.get("members", [])}
    available = [d for d in available if d["id"] not in member_ids]
    return render_template(
        "group_detail.html", **_ctx({"group": detail, "available_devices": available})
    )


@admin_ui_bp.post("/groups/<group_id>/members")
@admin_required_ui
def group_add_member_submit(group_id: str):
    device_id = (request.form.get("device_id") or "").strip()
    if not device_id:
        abort(400)
    try:
        add_members(group_id, [device_id])
    except LookupError:
        abort(404)
    return redirect(url_for("admin_ui.group_detail_page", group_id=group_id))


@admin_ui_bp.post("/groups/<group_id>/members/<device_id>/delete")
@admin_required_ui
def group_remove_member_submit(group_id: str, device_id: str):
    remove_member(group_id, device_id)
    return redirect(url_for("admin_ui.group_detail_page", group_id=group_id))


@admin_ui_bp.post("/groups/<group_id>/delete")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def group_delete_submit(group_id: str):
    if svc_delete_group(group_id):
        audit_service.record(
            "group.deleted",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="group",
            target_id=group_id,
        )
    return redirect(url_for("admin_ui.list_groups_page"))


# v0.3.4 (P3): bulk-delete groups from the groups list.
@admin_ui_bp.post("/groups/bulk-delete")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def groups_bulk_delete_submit():
    # v0.3.5 fix: dedupe (defense-in-depth against paired checkboxes).
    ids = list(dict.fromkeys(i for i in request.form.getlist("group_id") if i))
    if not ids:
        flash("Select at least one group first.", "warning")
        return redirect(url_for("admin_ui.list_groups_page"))
    try:
        mass_action.validate(
            target_count=len(ids),
            expected_typed_value="delete",
            confirmation_level=request.form.get("confirmation_level"),
            confirmation_typed_value=request.form.get("confirmation_typed_value"),
        )
    except mass_action.ConfirmationRequired as e:
        flash(
            f"Bulk delete affects {len(ids)} groups and requires "
            f"confirmation ({e.required_level}).",
            "error",
        )
        return redirect(url_for("admin_ui.list_groups_page"))
    result = svc_delete_groups_bulk(ids)
    audit_service.record(
        "group.bulk_deleted",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="group",
        target_id=None,
        details={
            "deleted_count": len(result["deleted"]),
            "skipped_unknown": len(result["skipped_unknown"]),
            "deleted_ids": result["deleted"],
            "confirmation_level": mass_action.required_level(len(ids)),
            "reason": "operator",
        },
    )
    flash(
        f"Deleted {len(result['deleted'])} group(s)."
        + (f" {len(result['skipped_unknown'])} unknown id(s) skipped." if result["skipped_unknown"] else ""),
        "info",
    )
    return redirect(url_for("admin_ui.list_groups_page"))


@admin_ui_bp.post("/groups/<group_id>/commands")
@admin_required_ui
def group_send_command_submit(group_id: str):
    cmd_type = (request.form.get("type") or "").strip()
    if not cmd_type:
        abort(400)
    payload: dict = {}
    if cmd_type == "relay_cycle":
        try:
            payload["power_off_seconds"] = int(request.form.get("power_off_seconds") or 5)
        except ValueError:
            payload["power_off_seconds"] = 5

    target_count = mass_action.count_group_members(group_id)
    try:
        mass_action.validate(
            target_count=target_count,
            expected_typed_value=cmd_type,
            confirmation_level=request.form.get("confirmation_level"),
            confirmation_typed_value=request.form.get("confirmation_typed_value"),
        )
    except mass_action.ConfirmationRequired as e:
        flash(
            f"Mass action requires confirmation: {target_count} devices "
            f"would be affected; please re-submit with the required confirmation. "
            f"(required level: {e.required_level})",
            "error",
        )
        return redirect(url_for("admin_ui.group_detail_page", group_id=group_id))

    override_lockout = (request.form.get("override_lockout") or "").lower() in ("1", "true", "yes")
    try:
        cmds, skipped = enqueue_for_group(
            group_id=group_id,
            cmd_type=cmd_type,
            payload=payload,
            issued_by_user_id=g.current_user.id,
            override_lockout=override_lockout,
        )
    except ValueError:
        abort(400)
    if skipped:
        flash(
            f"{len(skipped)} protected device{'s were' if len(skipped) != 1 else ' was'} "
            f"skipped. Re-submit with override_lockout=1 to include them.",
            "warning",
        )
    audit_service.record(
        "group.mass_command_issued",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="group",
        target_id=group_id,
        details={
            "type": cmd_type,
            "target_count": target_count,
            "fan_out_count": len(cmds),
            "skipped_protected": len(skipped),
            "override_lockout": override_lockout,
            "reason": "operator",
            "confirmation_level": mass_action.required_level(target_count),
        },
    )
    # v0.4.9 (B14): per-device audit fanout for the group command.
    audit_service.record_per_device(
        "device.mass_command_issued_per_device",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        device_ids=[c.device_id for c in cmds if getattr(c, "device_id", None)],
        base_details={"via": "group_mass_command", "group_id": group_id, "type": cmd_type},
    )
    if skipped:
        audit_service.record_per_device(
            "device.mass_command_skipped_per_device",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            device_ids=skipped,
            base_details={"via": "group_mass_command", "group_id": group_id, "type": cmd_type, "reason": "is_protected"},
        )
    return redirect(url_for("admin_ui.group_detail_page", group_id=group_id))


# ── API ────────────────────────────────────────────────────────────────────

@admin_api_bp.get("/groups")
@admin_required_api
def list_groups_api():
    rows = svc_list_groups()
    return ok({"groups": rows, "total": len(rows)})


@admin_api_bp.get("/groups/<group_id>")
@admin_required_api
def get_group(group_id: str):
    detail = get_group_detail(group_id)
    if detail is None:
        return err("group_unknown", "Group not found.", status=404)
    return ok(detail)


@admin_api_bp.post("/groups")
@admin_required_api
def create_group():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return err("validation_failed", "name is required", status=400)
    try:
        new_group = svc_create_group(
            name=name,
            description=body.get("description"),
            site_id=body.get("site_id"),
        )
    except DuplicateGroupName as e:
        return err("name_conflict", str(e), status=409)
    return ok(new_group, status=201)


@admin_api_bp.post("/groups/<group_id>/members")
@admin_required_api
def add_group_members(group_id: str):
    body = request.get_json(silent=True) or {}
    device_ids = body.get("device_ids") or []
    if not isinstance(device_ids, list):
        return err("validation_failed", "device_ids must be a list", status=400)
    try:
        n = add_members(group_id, device_ids)
    except LookupError:
        return err("group_unknown", "Group not found.", status=404)
    return ok({"added": n})


@admin_api_bp.delete("/groups/<group_id>/members/<device_id>")
@admin_required_api
def remove_group_member(group_id: str, device_id: str):
    if not remove_member(group_id, device_id):
        return err("not_a_member", "Device is not a member of this group.", status=404)
    audit_service.record(
        "group.member_removed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="group",
        target_id=group_id,
        details={"device_id": device_id},
    )
    return ok({"removed": True})


@admin_api_bp.delete("/groups/<group_id>")
@role_required_api(*ADMIN_AND_UP)
def delete_group_api(group_id: str):
    if not svc_delete_group(group_id):
        return err("group_unknown", "Group not found.", status=404)
    audit_service.record(
        "group.deleted",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="group",
        target_id=group_id,
    )
    return ok({"deleted": True})


@admin_api_bp.post("/groups/<group_id>/commands")
@admin_required_api
def send_group_command(group_id: str):
    body = request.get_json(silent=True) or {}
    cmd_type = body.get("type")
    if not cmd_type:
        return err("validation_failed", "type is required", status=400)

    target_count = mass_action.count_group_members(group_id)
    try:
        mass_action.validate(
            target_count=target_count,
            expected_typed_value=cmd_type,
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

    override_lockout = bool(body.get("override_lockout"))
    try:
        cmds, skipped = enqueue_for_group(
            group_id=group_id,
            cmd_type=cmd_type,
            payload=body.get("payload"),
            issued_by_user_id=g.current_user.id,
            ttl_seconds=body.get("ttl_seconds"),
            override_lockout=override_lockout,
        )
    except ValueError as e:
        return err("validation_failed", str(e), status=400)
    audit_service.record(
        "group.mass_command_issued",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="group",
        target_id=group_id,
        details={
            "type": cmd_type,
            "target_count": target_count,
            "fan_out_count": len(cmds),
            "skipped_protected": len(skipped),
            "override_lockout": override_lockout,
            "reason": "operator",
            "confirmation_level": mass_action.required_level(target_count),
        },
    )
    # v0.4.9 (B14): per-device audit fanout for the group command.
    audit_service.record_per_device(
        "device.mass_command_issued_per_device",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        device_ids=[c.device_id for c in cmds if getattr(c, "device_id", None)],
        base_details={"via": "group_mass_command", "group_id": group_id, "type": cmd_type},
    )
    if skipped:
        audit_service.record_per_device(
            "device.mass_command_skipped_per_device",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            device_ids=skipped,
            base_details={"via": "group_mass_command", "group_id": group_id, "type": cmd_type, "reason": "is_protected"},
        )
    return ok(
        {
            "fan_out_count": len(cmds),
            "target_count": target_count,
            "command_ids": [c.id for c in cmds],
            "skipped_protected_device_ids": skipped,
        },
        status=201,
    )
