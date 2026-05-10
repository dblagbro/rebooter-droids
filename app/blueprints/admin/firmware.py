"""Admin UI + API for firmware — releases, uploads, deployments
(with mass-action confirmation gate on multi-device deploys)."""

from __future__ import annotations

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import (
    ADMIN_AND_UP,
    admin_required_api,
    admin_required_ui,
    role_required_api,
)
from app.middleware.response import err, ok
from app.services import audit as audit_service
from app.services import mass_action
from app.services.deployments import (
    create_deployment,
    list_deployments as svc_list_deployments,
)
from app.services.devices import list_devices as svc_list_devices
from app.services.firmware import (
    delete_release,
    discover_on_disk_releases,
    list_releases as svc_list_firmware,
    upload_release,
)
from app.services.groups import list_groups as svc_list_groups


# ── UI ─────────────────────────────────────────────────────────────────────

# v0.4.33 (D3): firmware UI lives under /app/settings/firmware as a
# Settings tab. The legacy /app/firmware path is preserved as a 302
# redirect so existing bookmarks, scripts, and external docs keep
# working.
@admin_ui_bp.get("/settings/firmware")
@admin_required_ui
def list_firmware_page():
    releases = svc_list_firmware()
    deployments = svc_list_deployments()
    groups = svc_list_groups()
    devices = svc_list_devices()
    return render_template(
        "firmware_list.html",
        **_ctx(
            {
                "active": "settings",
                "settings_tab": "firmware",
                "releases": releases,
                "deployments": deployments,
                "groups": groups,
                "devices": devices,
                "all_active_devices_count": mass_action.count_all_active_devices(),
                "uploaded": session.pop("_new_firmware", None),
            }
        ),
    )


@admin_ui_bp.get("/firmware")
@admin_required_ui
def legacy_firmware_page_redirect():
    return redirect(url_for("admin_ui.list_firmware_page"), code=302)


@admin_ui_bp.post("/firmware/deployments")
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
        target_count = 1  # single-device deployment is not a mass action
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


@admin_ui_bp.post("/firmware")
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


@admin_ui_bp.post("/firmware/scan")
@admin_required_ui
def firmware_scan_submit():
    """v0.4.19 (Tier-1 B): scan `data/firmware/<channel>/` for `.bin`
    files that aren't yet tracked in `firmware_releases` and
    backfill DB rows + mirror records. Lets the operator surface
    artifacts the firmware team placed direct-to-disk via SCP /
    CI/CD without going through the admin upload API."""
    from flask import flash
    settings = current_app.config["SETTINGS"]
    result = discover_on_disk_releases(
        settings, issued_by_user_id=g.current_user.id
    )
    audit_service.record(
        "firmware.scanned",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="firmware_release",
        target_id=None,
        details={
            "discovered_count": len(result["discovered"]),
            "discovered_versions": [r["version"] for r in result["discovered"]],
            "skipped_existing": result["skipped_existing"],
            "errors": len(result["errors"]),
        },
    )
    if result["discovered"]:
        flash(
            f"Discovered {len(result['discovered'])} new release"
            f"{'' if len(result['discovered']) == 1 else 's'}: "
            + ", ".join(r['version'] for r in result['discovered']),
            "info",
        )
    elif result["errors"]:
        flash(
            f"Scan completed with {len(result['errors'])} error(s); see audit log.",
            "warning",
        )
    else:
        flash(
            f"Scan complete — no new releases. "
            f"{result['skipped_existing']} already tracked, "
            f"{result['skipped_pointer']} channel-pointer files skipped.",
            "info",
        )
    return redirect(url_for("admin_ui.list_firmware_page"))


@admin_api_bp.post("/firmware/scan")
@admin_required_api
def firmware_scan_api():
    settings = current_app.config["SETTINGS"]
    result = discover_on_disk_releases(
        settings, issued_by_user_id=g.current_user.id
    )
    audit_service.record(
        "firmware.scanned",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="firmware_release",
        target_id=None,
        details={
            "discovered_count": len(result["discovered"]),
            "via": "api",
        },
    )
    return ok(result)


@admin_ui_bp.post("/firmware/<release_id>/delete")
@admin_required_ui
def firmware_delete_submit(release_id: str):
    settings = current_app.config["SETTINGS"]
    delete_release(release_id, settings)
    return redirect(url_for("admin_ui.list_firmware_page"))


# ── API ────────────────────────────────────────────────────────────────────

@admin_api_bp.get("/firmware/releases")
@admin_required_api
def list_firmware_releases_api():
    return ok({"releases": svc_list_firmware()})


@admin_api_bp.post("/firmware/releases")
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


@admin_api_bp.delete("/firmware/releases/<release_id>")
@role_required_api(*ADMIN_AND_UP)
def delete_firmware_release(release_id: str):
    settings = current_app.config["SETTINGS"]
    if not delete_release(release_id, settings):
        return err("firmware_not_found", "Firmware release not found.", status=404)
    return ok({"deleted": True})


@admin_api_bp.get("/firmware/deployments")
@admin_required_api
def list_firmware_deployments_api():
    return ok({"deployments": svc_list_deployments()})


@admin_api_bp.post("/firmware/deployments")
@role_required_api(*ADMIN_AND_UP)
def create_firmware_deployment():
    body = request.get_json(silent=True) or {}
    release_id = (body.get("release_id") or body.get("version") or "").strip()
    if not release_id:
        return err("validation_failed", "release_id is required", status=400)
    target_type = (body.get("target_type") or "").strip()
    target_id = body.get("target_id")

    if target_type == "all_devices":
        target_count = mass_action.count_all_active_devices()
    elif target_type == "group" and target_id:
        target_count = mass_action.count_group_members(target_id)
    else:
        target_count = 1
    try:
        mass_action.validate(
            target_count=target_count,
            expected_typed_value="deploy_firmware",
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
            "force": bool(body.get("force") or False),
            "confirmation_level": mass_action.required_level(target_count),
        },
    )
    return ok({**out, "target_count": target_count}, status=201)
