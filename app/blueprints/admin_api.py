from __future__ import annotations

from flask import Blueprint, current_app, g, request

from app.middleware.admin_auth import (
    ADMIN_AND_UP,
    SUPER_ADMIN_ONLY,
    WRITE_ROLES,
    admin_required_api,
    role_required_api,
)
from app.models.users import (
    ALL_ROLES,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_SUPER_ADMIN,
    ROLE_VIEWER,
)
from app.services import audit
from app.services.invitations import (
    InvitationError,
    list_invitations,
    mint_invitation,
)
from app.services.users import (
    UserError,
    deactivate_user,
    list_users,
    revoke_all_tokens,
    update_user_role,
)
from app.middleware.response import err, ok
from app.services.commands import enqueue_for_device, enqueue_for_group
from app.services.devices import (
    UnknownPatchFieldError,
    get_device_detail,
    list_devices as svc_list_devices,
    update_device,
)
from app.services.enrollment import list_enrollment_tokens, mint_enrollment_token
from app.services.events import query_events
from app.services.deployments import (
    create_deployment,
    list_deployments,
)
from app.services.firmware import (
    delete_release,
    list_releases as svc_list_releases,
    upload_release,
)
from app.services.groups import (
    DuplicateNameError as DuplicateGroupName,
    add_members,
    create_group as svc_create_group,
    get_group_detail,
    list_groups,
    remove_member,
)
from app.services.sites import (
    DuplicateNameError as DuplicateSiteName,
    create_site as svc_create_site,
    delete_site as svc_delete_site,
    list_sites,
)

bp = Blueprint("admin_api", __name__)


def _device_to_dict(d) -> dict:
    return {
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
        "capabilities": d.capabilities,
        "last_heartbeat_at": d.last_heartbeat_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        if d.last_heartbeat_at
        else None,
        "created_at": d.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── enrollment tokens ────────────────────────────────────────────────────

@bp.post("/enrollment-tokens")
@admin_required_api
def create_enrollment_token():
    body = request.get_json(silent=True) or {}
    settings = current_app.config["SETTINGS"]
    record, raw_secret = mint_enrollment_token(
        settings,
        issued_by_user_id=g.current_user.id,
        site_id=body.get("site_id"),
        display_name_hint=body.get("display_name_hint"),
        note=body.get("note"),
    )
    return ok(
        {
            "id": record.id,
            "enrollment_token": raw_secret,
            "expires_at": record.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "display_name_hint": record.display_name_hint,
            "site_id": record.site_id,
            "note": record.note,
        },
        status=201,
    )


@bp.get("/enrollment-tokens")
@admin_required_api
def list_enrollment_tokens_api():
    rows = list_enrollment_tokens()
    return ok(
        {
            "tokens": [
                {
                    "id": r.id,
                    "consumed_at": r.consumed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if r.consumed_at
                    else None,
                    "consumed_by_device_id": r.consumed_by_device_id,
                    "expires_at": r.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "display_name_hint": r.display_name_hint,
                    "site_id": r.site_id,
                    "note": r.note,
                    "created_at": r.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                for r in rows
            ]
        }
    )


# ── devices ──────────────────────────────────────────────────────────────

@bp.get("/devices")
@admin_required_api
def list_devices():
    devices = svc_list_devices(
        site_id=request.args.get("site_id"),
        group_id=request.args.get("group_id"),
        search=request.args.get("search"),
        status=request.args.get("status"),
    )
    return ok({"devices": devices, "total": len(devices)})


@bp.get("/devices/<device_id>")
@admin_required_api
def get_device(device_id: str):
    detail = get_device_detail(device_id)
    if detail is None:
        return err("device_unknown", "Device not found.", status=404)
    return ok(detail)


@bp.patch("/devices/<device_id>")
@role_required_api(*ADMIN_AND_UP)
def patch_device(device_id: str):
    body = request.get_json(silent=True) or {}
    try:
        updated = update_device(device_id, body)
    except UnknownPatchFieldError as e:
        return err("validation_failed", str(e), status=400)
    if updated is None:
        return err("device_unknown", "Device not found.", status=404)
    audit.record(
        "device.updated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
        details={"fields": sorted(body.keys())},
    )
    return ok(updated)


@bp.post("/devices/<device_id>/commands")
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
    audit.record(
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


# ── groups ────────────────────────────────────────────────────────────────

@bp.get("/groups")
@admin_required_api
def list_groups_api():
    rows = list_groups()
    return ok({"groups": rows, "total": len(rows)})


@bp.get("/groups/<group_id>")
@admin_required_api
def get_group(group_id: str):
    detail = get_group_detail(group_id)
    if detail is None:
        return err("group_unknown", "Group not found.", status=404)
    return ok(detail)


@bp.post("/groups")
@admin_required_api
def create_group():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return err("validation_failed", "name is required", status=400)
    try:
        g = svc_create_group(
            name=name,
            description=body.get("description"),
            site_id=body.get("site_id"),
        )
    except DuplicateGroupName as e:
        return err("name_conflict", str(e), status=409)
    return ok(g, status=201)


@bp.post("/groups/<group_id>/members")
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


@bp.delete("/groups/<group_id>/members/<device_id>")
@admin_required_api
def remove_group_member(group_id: str, device_id: str):
    if not remove_member(group_id, device_id):
        return err("not_a_member", "Device is not a member of this group.", status=404)
    return ok({"removed": True})


@bp.post("/groups/<group_id>/commands")
@admin_required_api
def send_group_command(group_id: str):
    body = request.get_json(silent=True) or {}
    cmd_type = body.get("type")
    if not cmd_type:
        return err("validation_failed", "type is required", status=400)
    try:
        cmds = enqueue_for_group(
            group_id=group_id,
            cmd_type=cmd_type,
            payload=body.get("payload"),
            issued_by_user_id=g.current_user.id,
            ttl_seconds=body.get("ttl_seconds"),
        )
    except ValueError as e:
        return err("validation_failed", str(e), status=400)
    return ok(
        {
            "fan_out_count": len(cmds),
            "command_ids": [c.id for c in cmds],
        },
        status=201,
    )


# ── firmware ──────────────────────────────────────────────────────────────

@bp.get("/firmware/releases")
@admin_required_api
def list_firmware_releases_api():
    return ok({"releases": svc_list_releases()})


@bp.post("/firmware/releases")
@role_required_api(*ADMIN_AND_UP)
def create_firmware_release():
    settings = current_app.config["SETTINGS"]
    if "file" not in request.files:
        return err("validation_failed", "file (multipart upload) is required", status=400)
    f = request.files["file"]
    version = (request.form.get("version") or request.values.get("version") or "").strip()
    channel = (request.form.get("channel") or "dev").strip()
    expected_sha = (request.form.get("sha256") or "").strip() or None
    notes = request.form.get("release_notes") or None

    try:
        out = upload_release(
            settings,
            upload_stream=f.stream,
            version=version,
            channel=channel,
            expected_sha256=expected_sha,
            release_notes=notes,
            issued_by_user_id=g.current_user.id,
        )
    except ValueError as e:
        return err("validation_failed", str(e), status=400)
    return ok(out, status=201)


@bp.delete("/firmware/releases/<release_id>")
@role_required_api(*ADMIN_AND_UP)
def delete_firmware_release(release_id: str):
    settings = current_app.config["SETTINGS"]
    if not delete_release(release_id, settings):
        return err("firmware_not_found", "Firmware release not found.", status=404)
    return ok({"deleted": True})


@bp.get("/firmware/deployments")
@admin_required_api
def list_firmware_deployments_api():
    return ok({"deployments": list_deployments()})


@bp.post("/firmware/deployments")
@role_required_api(*ADMIN_AND_UP)
def create_firmware_deployment():
    body = request.get_json(silent=True) or {}
    release_id = (body.get("release_id") or body.get("version") or "").strip()
    if not release_id:
        return err("validation_failed", "release_id is required", status=400)
    target_type = (body.get("target_type") or "").strip()
    target_id = body.get("target_id")
    try:
        out = create_deployment(
            release_id=release_id,
            target_type=target_type,
            target_id=target_id,
            channel=body.get("channel"),
            force=bool(body.get("force") or False),
            issued_by_user_id=g.current_user.id,
        )
    except LookupError:
        return err("not_found", "Release or target not found.", status=404)
    except ValueError as e:
        return err("validation_failed", str(e), status=400)
    return ok(out, status=201)


# ── sites ─────────────────────────────────────────────────────────────────

@bp.get("/sites")
@admin_required_api
def list_sites_api():
    return ok({"sites": list_sites()})


@bp.post("/sites")
@admin_required_api
def create_site_api():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return err("validation_failed", "name is required", status=400)
    try:
        return ok(svc_create_site(name=name, description=body.get("description")), status=201)
    except DuplicateSiteName as e:
        return err("name_conflict", str(e), status=409)


@bp.delete("/sites/<site_id>")
@admin_required_api
def delete_site_api(site_id: str):
    if not svc_delete_site(site_id):
        return err("site_unknown", "Site not found.", status=404)
    return ok({"deleted": True})


# ── users + invitations + audit (super-admin or admin) ──────────────────

@bp.get("/users")
@role_required_api(*ADMIN_AND_UP)
def list_users_api():
    return ok({"users": list_users()})


@bp.post("/users/<user_id>/role")
@role_required_api(*SUPER_ADMIN_ONLY)
def change_user_role(user_id: str):
    body = request.get_json(silent=True) or {}
    role = body.get("role") or ""
    if role not in ALL_ROLES:
        return err("validation_failed", f"role must be one of {ALL_ROLES}", status=400)
    try:
        out = update_user_role(user_id, role)
    except UserError as e:
        return err("validation_failed", str(e), status=400)
    if out is None:
        return err("user_unknown", "User not found.", status=404)
    audit.record(
        "user.role_changed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="user",
        target_id=user_id,
        details={"new_role": role},
    )
    return ok(out)


@bp.post("/users/<user_id>/deactivate")
@role_required_api(*SUPER_ADMIN_ONLY)
def deactivate_user_api(user_id: str):
    if user_id == g.current_user.id:
        return err("forbidden", "You cannot deactivate your own account.", status=403)
    if not deactivate_user(user_id):
        return err("user_unknown", "User not found.", status=404)
    audit.record(
        "user.deactivated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="user",
        target_id=user_id,
    )
    return ok({"deactivated": True})


@bp.post("/users/<user_id>/revoke-tokens")
@role_required_api(*SUPER_ADMIN_ONLY)
def revoke_tokens_api(user_id: str):
    if not revoke_all_tokens(user_id):
        return err("user_unknown", "User not found.", status=404)
    audit.record(
        "user.tokens_revoked",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="user",
        target_id=user_id,
    )
    return ok({"revoked": True})


@bp.get("/invitations")
@role_required_api(*ADMIN_AND_UP)
def list_invitations_api():
    rows = list_invitations()
    return ok(
        {
            "invitations": [
                {
                    "id": r.id,
                    "email": r.email,
                    "role": r.role,
                    "expires_at": r.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "consumed_at": r.consumed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if r.consumed_at
                    else None,
                    "consumed_by_user_id": r.consumed_by_user_id,
                    "issued_by_user_id": r.issued_by_user_id,
                    "note": r.note,
                    "created_at": r.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                for r in rows
            ]
        }
    )


@bp.post("/invitations")
@role_required_api(*ADMIN_AND_UP)
def create_invitation():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip()
    role = body.get("role") or "admin"

    # Only super_admin can mint a super_admin invitation.
    if role == ROLE_SUPER_ADMIN and g.current_user.role != ROLE_SUPER_ADMIN:
        return err(
            "forbidden",
            "only a super_admin can invite another super_admin",
            status=403,
        )

    settings = current_app.config["SETTINGS"]
    try:
        record, raw = mint_invitation(
            settings,
            email=email,
            role=role,
            issued_by_user_id=g.current_user.id,
            note=body.get("note"),
        )
    except InvitationError as e:
        return err(e.code, e.message, status=400)

    public_base = settings.public_base_url.rstrip("/")
    redeem_url = f"{public_base}/app/invite/{raw}"

    # Try to send the email. If SMTP isn't configured we still return the
    # token so an admin can deliver it manually.
    sent = False
    try:
        from app.services.email import send_invite_email

        sent = send_invite_email(
            to=email, role=role, redeem_url=redeem_url, note=body.get("note")
        )
    except Exception:
        current_app.logger.exception("invite email failed")

    audit.record(
        "user.invited",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="invitation",
        target_id=record.id,
        details={"email": email, "role": role, "email_sent": sent},
    )

    return ok(
        {
            "id": record.id,
            "email": email,
            "role": role,
            "redeem_url": redeem_url,
            "expires_at": record.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "email_sent": sent,
            # Only returned to the inviter, never persisted in cleartext.
            "invitation_token": raw,
        },
        status=201,
    )


@bp.get("/audit")
@role_required_api(*ADMIN_AND_UP)
def audit_api():
    rows = audit.query(
        actor_user_id=request.args.get("actor_user_id"),
        action=request.args.get("action"),
        target_type=request.args.get("target_type"),
        target_id=request.args.get("target_id"),
        limit=int(request.args.get("limit") or 200),
    )
    return ok({"events": rows, "count": len(rows)})


# ── events ────────────────────────────────────────────────────────────────

@bp.get("/events")
@admin_required_api
def query_events_api():
    rows = query_events(
        device_id=request.args.get("device_id"),
        group_id=request.args.get("group_id"),
        type_=request.args.get("type"),
        from_ts=request.args.get("from"),
        to_ts=request.args.get("to"),
        limit=int(request.args.get("limit") or 200),
    )
    return ok({"events": rows, "count": len(rows)})
