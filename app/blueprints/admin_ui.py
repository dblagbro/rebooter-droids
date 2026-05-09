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

from app.middleware.admin_auth import admin_required_ui
from app.middleware.rate_limit import limiter
from app.services.auth import authenticate
from app.services.commands import enqueue_for_device, enqueue_for_group
from app.services.devices import get_device_detail, list_devices, update_device
from app.services.enrollment import list_enrollment_tokens, mint_enrollment_token
from app.services.events import query_events as svc_query_events
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
    base = {"version": __version__, "current_user": g.current_user}
    if extra:
        base.update(extra)
    return base


# ── auth ───────────────────────────────────────────────────────────────

@bp.get("/")
@admin_required_ui
def index():
    devices = list_devices()
    online = sum(1 for d in devices if d.get("online"))
    return render_template(
        "dashboard.html",
        **_ctx(
            {
                "device_count": len(devices),
                "online_count": online,
                "offline_count": len(devices) - online,
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
    session.permanent = True
    return redirect(url_for("admin_ui.index"))


@bp.get("/logout")
def logout():
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
    return render_template("device_detail.html", **_ctx({"device": detail}))


@bp.post("/devices/<device_id>")
@admin_required_ui
def device_update_submit(device_id: str):
    patch = {
        "display_name": request.form.get("display_name") or "",
        "notes": request.form.get("notes") or None,
        "central_management_enabled": "central_management_enabled" in request.form,
    }
    updated = update_device(device_id, patch)
    if updated is None:
        abort(404)
    return redirect(url_for("admin_ui.device_detail_page", device_id=device_id))


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
    name = (request.form.get("name") or "").strip()
    if not name:
        abort(400)
    svc_create_group(
        name=name,
        description=(request.form.get("description") or "").strip() or None,
        site_id=None,
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
    try:
        enqueue_for_group(
            group_id=group_id,
            cmd_type=cmd_type,
            payload=payload,
            issued_by_user_id=g.current_user.id,
        )
    except ValueError:
        abort(400)
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
    name = (request.form.get("name") or "").strip()
    if not name:
        abort(400)
    svc_create_site(name=name, description=(request.form.get("description") or "").strip() or None)
    return redirect(url_for("admin_ui.list_sites_page"))


@bp.post("/sites/<site_id>/delete")
@admin_required_ui
def delete_site_submit(site_id: str):
    svc_delete_site(site_id)
    return redirect(url_for("admin_ui.list_sites_page"))


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
