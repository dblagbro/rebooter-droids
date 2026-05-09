from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.middleware.admin_auth import (
    admin_required_ui,
    role_required_ui,
)
from app.models.users import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_SUPER_ADMIN,
    ROLE_VIEWER,
    ALL_ROLES,
)
from app.middleware.rate_limit import limiter
from app.services.auth import authenticate
from app.services.commands import enqueue_for_device, enqueue_for_group
from app.services import mass_action
from app.services.devices import (
    delete_device as svc_delete_device_ui,
    get_device_detail,
    list_devices,
    update_device,
)
from app.services.enrollment import list_enrollment_tokens, mint_enrollment_token
from app.services import audit as audit_service
from app.services.events import query_events as svc_query_events
from app.services.invitations import (
    InvitationError,
    cancel_invitation as svc_cancel_invitation_ui,
    list_invitations,
    lookup_pending,
    mint_invitation,
    redeem_invitation,
)
from app.services.users import (
    UserError,
    change_own_display_name,
    change_own_password,
    deactivate_user,
    list_users as svc_list_users,
    revoke_all_tokens,
    update_user_display_name,
    update_user_role,
)
from app.services.sites import list_sites as svc_list_sites_only  # noqa: F401
from app.services.deployments import (
    create_deployment,
    list_deployments as svc_list_deployments,
)
from app.services.firmware import (
    delete_release,
    list_releases as svc_list_firmware,
    upload_release,
)
from app.services.groups import list_groups as svc_list_groups_only  # noqa: F401
from app.services.groups import (
    add_members,
    create_group as svc_create_group,
    delete_group as svc_delete_group_ui,
    get_group_detail,
    list_groups as svc_list_groups,
    remove_member,
)
from app.services.sites import (
    create_site as svc_create_site,
    delete_site as svc_delete_site,
    list_sites as svc_list_sites,
)
from app.version import __version__

bp = Blueprint("admin_ui", __name__)


def _ctx(extra: dict | None = None) -> dict:
    # v0.2.5: surface unregistered-auth-attempts count in the layout nav badge.
    # Best-effort — never break the page render.
    try:
        from app.services import unregistered as unreg_service

        unregistered_active = unreg_service.count_active(since_minutes=60)
    except Exception:
        unregistered_active = 0
    base = {
        "version": __version__,
        "current_user": g.current_user,
        "unregistered_active": unregistered_active,
    }
    if extra:
        base.update(extra)
    return base


# ── auth ───────────────────────────────────────────────────────────────

@bp.get("/")
@admin_required_ui
def index():
    from app.services import dashboard as dash_service

    return render_template(
        "dashboard.html",
        **_ctx(
            {
                "stats": dash_service.stats(),
                "feed": dash_service.recent_activity(limit=25),
            }
        ),
    )


@bp.get("/login")
def login_page():
    if session.get("user_id"):
        return redirect(url_for("admin_ui.index"))
    return render_template("login.html", version=__version__, error=None)


@bp.post("/login")
@limiter.limit("30 per minute; 200 per hour")
def login_submit():
    from datetime import datetime, timezone

    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    user = authenticate(email, password)
    if user is None:
        return (
            render_template(
                "login.html",
                version=__version__,
                error="Invalid email or password.",
                email=email,
            ),
            401,
        )
    session.clear()
    session["user_id"] = user.id
    session["iat"] = int(datetime.now(timezone.utc).timestamp())
    session.permanent = True
    return redirect(url_for("admin_ui.index"))


# ── self-service profile ────────────────────────────────────────────────

@bp.get("/me")
@admin_required_ui
def me_page():
    return render_template(
        "me.html",
        **_ctx({"flash_msg": session.pop("_me_flash", None)}),
    )


@bp.post("/me/display-name")
@admin_required_ui
def me_display_name_submit():
    name = (request.form.get("display_name") or "").strip()
    if not name:
        session["_me_flash"] = ("error", "Display name is required.")
        return redirect(url_for("admin_ui.me_page"))
    try:
        change_own_display_name(g.current_user.id, name)
    except UserError as e:
        session["_me_flash"] = ("error", str(e))
        return redirect(url_for("admin_ui.me_page"))
    audit_service.record(
        "user.display_name_changed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="user",
        target_id=g.current_user.id,
        details={"new_display_name": name, "self_service": True},
    )
    session["_me_flash"] = ("ok", "Display name updated.")
    return redirect(url_for("admin_ui.me_page"))


@bp.post("/me/password")
@admin_required_ui
def me_password_submit():
    current = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("new_password_confirm") or ""
    if new != confirm:
        session["_me_flash"] = ("error", "New password and confirmation do not match.")
        return redirect(url_for("admin_ui.me_page"))
    try:
        change_own_password(g.current_user.id, current, new)
    except UserError as e:
        session["_me_flash"] = ("error", str(e))
        return redirect(url_for("admin_ui.me_page"))

    audit_service.record(
        "user.password_changed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="user",
        target_id=g.current_user.id,
        details={"self_service": True},
    )
    # change_own_password bumped tokens_valid_after — kick this session too
    # so the user re-authenticates with the new password (standard pattern).
    session.clear()
    return redirect(url_for("admin_ui.login_page"))


@bp.post("/me/revoke-everywhere")
@admin_required_ui
def me_revoke_everywhere_submit():
    revoke_all_tokens(g.current_user.id)
    audit_service.record(
        "user.tokens_revoked",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="user",
        target_id=g.current_user.id,
        details={"self_service": True},
    )
    session.clear()
    return redirect(url_for("admin_ui.login_page"))


@bp.get("/logout")
def logout():
    """Sign out of THIS browser session.

    Note: this does NOT invalidate any other JWT or cookie the user may
    have. Use the explicit "revoke all tokens" action (super-admin only)
    to log a user out everywhere — see /admin/users.
    """
    session.clear()
    return redirect(url_for("admin_ui.login_page"))


# ── devices ─────────────────────────────────────────────────────────────

@bp.get("/devices")
@admin_required_ui
def list_devices_page():
    devices = list_devices(
        site_id=request.args.get("site_id"),
        group_id=request.args.get("group_id"),
        search=request.args.get("search"),
        status=request.args.get("status"),
    )
    return render_template(
        "devices_list.html",
        **_ctx(
            {
                "devices": devices,
                "filters": {
                    "search": request.args.get("search", ""),
                    "status": request.args.get("status", ""),
                },
            }
        ),
    )


@bp.get("/devices/<device_id>")
@admin_required_ui
def device_detail_page(device_id: str):
    detail = get_device_detail(device_id)
    if detail is None:
        abort(404)
    sites = svc_list_sites_only()
    return render_template(
        "device_detail.html", **_ctx({"device": detail, "sites": sites})
    )


@bp.post("/devices/<device_id>")
@admin_required_ui
def device_update_submit(device_id: str):
    site_id = (request.form.get("site_id") or "").strip()
    patch = {
        "display_name": request.form.get("display_name") or "",
        "notes": request.form.get("notes") or None,
        "central_management_enabled": "central_management_enabled" in request.form,
        "site_id": site_id or None,
    }
    from app.services.devices import UnknownPatchFieldError

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


@bp.post("/devices/<device_id>/delete")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def device_delete_submit(device_id: str):
    if svc_delete_device_ui(device_id):
        audit_service.record(
            "device.deleted",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="device",
            target_id=device_id,
        )
    return redirect(url_for("admin_ui.list_devices_page"))


@bp.post("/devices/<device_id>/commands")
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


# ── enrollment tokens ────────────────────────────────────────────────────

@bp.get("/enrollment-tokens")
@admin_required_ui
def enrollment_tokens_page():
    tokens = list_enrollment_tokens()
    return render_template(
        "enrollment_tokens.html",
        **_ctx({"tokens": tokens, "new_token": session.pop("_new_enrollment_token", None)}),
    )


@bp.post("/enrollment-tokens")
@admin_required_ui
def enrollment_tokens_create():
    settings = current_app.config["SETTINGS"]
    record, raw_secret = mint_enrollment_token(
        settings,
        issued_by_user_id=g.current_user.id,
        display_name_hint=(request.form.get("display_name_hint") or "").strip() or None,
        note=(request.form.get("note") or "").strip() or None,
    )
    session["_new_enrollment_token"] = {
        "id": record.id,
        "enrollment_token": raw_secret,
        "expires_at": record.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return redirect(url_for("admin_ui.enrollment_tokens_page"))


# ── stubs for routes that land in subsequent tasks ──────────────────────

@bp.get("/groups")
@admin_required_ui
def list_groups_page():
    groups = svc_list_groups()
    return render_template("groups_list.html", **_ctx({"groups": groups}))


@bp.post("/groups")
@admin_required_ui
def create_group_submit():
    from app.services.groups import DuplicateNameError as _DupG

    name = (request.form.get("name") or "").strip()
    if not name:
        abort(400)
    try:
        svc_create_group(
            name=name,
            description=(request.form.get("description") or "").strip() or None,
            site_id=None,
        )
    except _DupG:
        groups = svc_list_groups()
        return (
            render_template(
                "groups_list.html",
                **_ctx({"groups": groups, "error": f"A group named '{name}' already exists."}),
            ),
            409,
        )
    return redirect(url_for("admin_ui.list_groups_page"))


@bp.get("/groups/<group_id>")
@admin_required_ui
def group_detail_page(group_id: str):
    detail = get_group_detail(group_id)
    if detail is None:
        abort(404)
    available = list_devices()
    member_ids = {m["id"] for m in detail.get("members", [])}
    available = [d for d in available if d["id"] not in member_ids]
    return render_template(
        "group_detail.html", **_ctx({"group": detail, "available_devices": available})
    )


@bp.post("/groups/<group_id>/members")
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


@bp.post("/groups/<group_id>/members/<device_id>/delete")
@admin_required_ui
def group_remove_member_submit(group_id: str, device_id: str):
    remove_member(group_id, device_id)
    return redirect(url_for("admin_ui.group_detail_page", group_id=group_id))


@bp.post("/groups/<group_id>/delete")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def group_delete_submit(group_id: str):
    if svc_delete_group_ui(group_id):
        audit_service.record(
            "group.deleted",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="group",
            target_id=group_id,
        )
    return redirect(url_for("admin_ui.list_groups_page"))


@bp.post("/groups/<group_id>/commands")
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

    try:
        cmds = enqueue_for_group(
            group_id=group_id,
            cmd_type=cmd_type,
            payload=payload,
            issued_by_user_id=g.current_user.id,
        )
    except ValueError:
        abort(400)
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
            "confirmation_level": mass_action.required_level(target_count),
        },
    )
    return redirect(url_for("admin_ui.group_detail_page", group_id=group_id))


@bp.get("/firmware")
@admin_required_ui
def list_firmware_page():
    releases = svc_list_firmware()
    deployments = svc_list_deployments()
    groups = svc_list_groups()
    devices = list_devices()
    return render_template(
        "firmware_list.html",
        **_ctx(
            {
                "releases": releases,
                "deployments": deployments,
                "groups": groups,
                "devices": devices,
                "all_active_devices_count": mass_action.count_all_active_devices(),
                "uploaded": session.pop("_new_firmware", None),
            }
        ),
    )


@bp.post("/firmware/deployments")
@admin_required_ui
def firmware_deploy_submit():
    release_id = (request.form.get("release_id") or "").strip()
    target_type = (request.form.get("target_type") or "").strip()
    target_id = (request.form.get("target_id") or "").strip() or None
    if target_type == "all_devices":
        target_id = None
    if not release_id or not target_type:
        abort(400)

    if target_type == "all_devices":
        target_count = mass_action.count_all_active_devices()
    elif target_type == "group" and target_id:
        target_count = mass_action.count_group_members(target_id)
    else:
        target_count = 1  # single device deployment is not a mass action
    try:
        mass_action.validate(
            target_count=target_count,
            expected_typed_value="deploy_firmware",
            confirmation_level=request.form.get("confirmation_level"),
            confirmation_typed_value=request.form.get("confirmation_typed_value"),
        )
    except mass_action.ConfirmationRequired as e:
        flash(
            f"Firmware deployment requires confirmation: {target_count} devices "
            f"would be affected. (required level: {e.required_level})",
            "error",
        )
        return redirect(url_for("admin_ui.list_firmware_page"))

    try:
        create_deployment(
            release_id=release_id,
            target_type=target_type,
            target_id=target_id,
            channel=None,
            force=("force" in request.form),
            issued_by_user_id=g.current_user.id,
        )
    except (LookupError, ValueError):
        abort(400)
    audit_service.record(
        "firmware.mass_deployment_issued",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="firmware_deployment",
        target_id=release_id,
        details={
            "release_id": release_id,
            "target_type": target_type,
            "target_id": target_id,
            "target_count": target_count,
            "force": "force" in request.form,
            "confirmation_level": mass_action.required_level(target_count),
        },
    )
    return redirect(url_for("admin_ui.list_firmware_page"))


@bp.post("/firmware")
@admin_required_ui
def firmware_upload_submit():
    settings = current_app.config["SETTINGS"]
    if "file" not in request.files:
        abort(400)
    f = request.files["file"]
    version = (request.form.get("version") or "").strip()
    channel = (request.form.get("channel") or "dev").strip()
    expected_sha = (request.form.get("sha256") or "").strip() or None
    notes = (request.form.get("release_notes") or "").strip() or None
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
        session["_new_firmware_error"] = str(e)
        return redirect(url_for("admin_ui.list_firmware_page"))
    session["_new_firmware"] = out
    return redirect(url_for("admin_ui.list_firmware_page"))


@bp.post("/firmware/<release_id>/delete")
@admin_required_ui
def firmware_delete_submit(release_id: str):
    settings = current_app.config["SETTINGS"]
    delete_release(release_id, settings)
    return redirect(url_for("admin_ui.list_firmware_page"))


@bp.get("/sites")
@admin_required_ui
def list_sites_page():
    sites = svc_list_sites()
    return render_template("sites_list.html", **_ctx({"sites": sites}))


@bp.post("/sites")
@admin_required_ui
def create_site_submit():
    from app.services.sites import DuplicateNameError as _DupS

    name = (request.form.get("name") or "").strip()
    if not name:
        abort(400)
    try:
        svc_create_site(
            name=name,
            description=(request.form.get("description") or "").strip() or None,
        )
    except _DupS:
        sites = svc_list_sites()
        return (
            render_template(
                "sites_list.html",
                **_ctx({"sites": sites, "error": f"A site named '{name}' already exists."}),
            ),
            409,
        )
    return redirect(url_for("admin_ui.list_sites_page"))


@bp.post("/sites/<site_id>/delete")
@admin_required_ui
def delete_site_submit(site_id: str):
    svc_delete_site(site_id)
    return redirect(url_for("admin_ui.list_sites_page"))


# ── users + invitations (super-admin) ───────────────────────────────────

@bp.get("/users")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def users_page():
    users = svc_list_users()
    return render_template("users_list.html", **_ctx({"users": users}))


@bp.post("/users/<user_id>/role")
@role_required_ui(ROLE_SUPER_ADMIN)
def change_user_role_submit(user_id: str):
    role = request.form.get("role") or ""
    if role not in ALL_ROLES:
        abort(400)
    try:
        update_user_role(user_id, role)
    except UserError:
        abort(400)
    audit_service.record(
        "user.role_changed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="user",
        target_id=user_id,
        details={"new_role": role},
    )
    return redirect(url_for("admin_ui.users_page"))


@bp.post("/users/<user_id>/deactivate")
@role_required_ui(ROLE_SUPER_ADMIN)
def deactivate_user_submit(user_id: str):
    if user_id == g.current_user.id:
        abort(403)
    deactivate_user(user_id)
    audit_service.record(
        "user.deactivated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="user",
        target_id=user_id,
    )
    return redirect(url_for("admin_ui.users_page"))


@bp.post("/users/<user_id>/revoke-tokens")
@role_required_ui(ROLE_SUPER_ADMIN)
def revoke_tokens_submit(user_id: str):
    revoke_all_tokens(user_id)
    audit_service.record(
        "user.tokens_revoked",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="user",
        target_id=user_id,
    )
    # If the architect revoked their OWN tokens, kick them out of this session
    # too (otherwise they'd be confused about why their cookie still appears
    # to work for one more request).
    if user_id == g.current_user.id:
        session.clear()
        return redirect(url_for("admin_ui.login_page"))
    return redirect(url_for("admin_ui.users_page"))


@bp.post("/users/<user_id>/display-name")
@role_required_ui(ROLE_SUPER_ADMIN)
def change_display_name_submit(user_id: str):
    name = (request.form.get("display_name") or "").strip()
    if not name:
        abort(400)
    try:
        update_user_display_name(user_id, name)
    except UserError:
        abort(400)
    audit_service.record(
        "user.display_name_changed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="user",
        target_id=user_id,
        details={"new_display_name": name},
    )
    return redirect(url_for("admin_ui.users_page"))


@bp.get("/invitations")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def invitations_page():
    invs = list_invitations()
    return render_template(
        "invitations_list.html",
        **_ctx({"invitations": invs, "new_invite": session.pop("_new_invite", None)}),
    )


@bp.post("/invitations/<invitation_id>/cancel")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def invitations_cancel_submit(invitation_id: str):
    if svc_cancel_invitation_ui(invitation_id):
        audit_service.record(
            "invitation.cancelled",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="invitation",
            target_id=invitation_id,
        )
    return redirect(url_for("admin_ui.invitations_page"))


@bp.post("/invitations")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def invitations_create_submit():
    settings = current_app.config["SETTINGS"]
    email = (request.form.get("email") or "").strip()
    role = request.form.get("role") or "admin"
    note = (request.form.get("note") or "").strip() or None
    if role == ROLE_SUPER_ADMIN and g.current_user.role != ROLE_SUPER_ADMIN:
        abort(403)
    try:
        record, raw = mint_invitation(
            settings,
            email=email,
            role=role,
            issued_by_user_id=g.current_user.id,
            note=note,
        )
    except InvitationError:
        abort(400)
    public_base = settings.public_base_url.rstrip("/")
    redeem_url = f"{public_base}/app/invite/{raw}"
    try:
        from app.services.email import send_invite_email

        sent = send_invite_email(to=email, role=role, redeem_url=redeem_url, note=note)
    except Exception:
        sent = False
    audit_service.record(
        "user.invited",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="invitation",
        target_id=record.id,
        details={"email": email, "role": role, "email_sent": sent},
    )
    session["_new_invite"] = {
        "redeem_url": redeem_url,
        "email": email,
        "role": role,
        "email_sent": sent,
    }
    return redirect(url_for("admin_ui.invitations_page"))


@bp.get("/audit")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def audit_page():
    rows = audit_service.query(
        actor_user_id=request.args.get("actor_user_id") or None,
        action=request.args.get("action") or None,
        target_type=request.args.get("target_type") or None,
        limit=int(request.args.get("limit") or 200),
    )
    return render_template("audit_list.html", **_ctx({"events": rows}))


# ── unregistered-heartbeat visibility (v0.2.5) ──────────────────────────

@bp.get("/unregistered-devices")
@admin_required_ui
def unregistered_devices_page():
    from app.services import unregistered as unreg_service

    since_minutes = int(request.args.get("since_minutes") or 0) or None
    rows = unreg_service.list_recent(limit=200, since_minutes=since_minutes)
    return render_template(
        "unregistered_devices.html",
        **_ctx({"rows": rows, "since_minutes": since_minutes}),
    )


# ── public invite redeem flow (no auth) ──────────────────────────────────

@bp.get("/invite/<token>")
def invite_redeem_page(token: str):
    inv = lookup_pending(token)
    return render_template(
        "invite_redeem.html",
        version=__version__,
        invitation=inv,
        token=token,
        error=None,
    )


@bp.post("/invite/<token>")
def invite_redeem_submit(token: str):
    password = request.form.get("password") or ""
    confirm = request.form.get("password_confirm") or ""
    display_name = (request.form.get("display_name") or "").strip()
    if password != confirm:
        return render_template(
            "invite_redeem.html",
            version=__version__,
            invitation=lookup_pending(token),
            token=token,
            error="Passwords do not match.",
        ), 400
    try:
        user = redeem_invitation(
            token=token, password=password, display_name=display_name
        )
    except InvitationError as e:
        return render_template(
            "invite_redeem.html",
            version=__version__,
            invitation=lookup_pending(token),
            token=token,
            error=e.message,
        ), 400

    audit_service.record(
        "user.created_via_invite",
        actor_user_id=user["id"],
        actor_email_snapshot=user["email"],
        target_type="user",
        target_id=user["id"],
    )
    # Sign the user in immediately.
    from datetime import datetime, timezone
    session.clear()
    session["user_id"] = user["id"]
    session["iat"] = int(datetime.now(timezone.utc).timestamp())
    session.permanent = True
    return redirect(url_for("admin_ui.index"))


@bp.get("/events")
@admin_required_ui
def events_page():
    rows = svc_query_events(
        device_id=request.args.get("device_id") or None,
        type_=request.args.get("type") or None,
        from_ts=request.args.get("from") or None,
        to_ts=request.args.get("to") or None,
        limit=int(request.args.get("limit") or 100),
    )
    return render_template(
        "events.html",
        **_ctx(
            {
                "events": rows,
                "filters": {
                    "device_id": request.args.get("device_id", ""),
                    "type": request.args.get("type", ""),
                    "from": request.args.get("from", ""),
                    "to": request.args.get("to", ""),
                },
            }
        ),
    )
