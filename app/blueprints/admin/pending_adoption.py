"""Pending-adoption — v0.4.20 admin surface.

Lists devices that have announced themselves to the hub but haven't
been adopted yet. Operator clicks Adopt → fresh enrolment token is
delivered on the device's next announce poll.
"""

from __future__ import annotations

from flask import flash, g, redirect, render_template, request, url_for

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
from app.services.announcements import (
    AnnouncementError,
    adopt as svc_adopt,
    delete as svc_delete,
    list_announcements as svc_list,
    reject as svc_reject,
)


# ── UI ─────────────────────────────────────────────────────────────


@admin_ui_bp.get("/pending-adoption")
@admin_required_ui
def pending_adoption_page():
    show_all = (request.args.get("show_all") or "").lower() in ("1", "true", "on", "yes")
    announcements = svc_list(include_consumed=show_all)
    return render_template(
        "pending_adoption.html",
        **_ctx({
            "active": "devices",
            "announcements": announcements,
            "show_all": show_all,
        }),
    )


@admin_ui_bp.post("/pending-adoption/<announcement_id>/adopt")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def pending_adoption_adopt_submit(announcement_id: str):
    display_name = (request.form.get("display_name") or "").strip() or None
    try:
        result = svc_adopt(
            announcement_id,
            by_user_id=g.current_user.id,
            display_name=display_name,
        )
    except AnnouncementError as e:
        flash(f"Adopt failed: {e.message}", "error")
        return redirect(url_for("admin_ui.pending_adoption_page"))
    audit_service.record(
        "device_announcement.adopted",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device_announcement",
        target_id=announcement_id,
        details={
            "mac_address": result["mac_address"],
            "enrollment_token_id": result["enrollment_token_id"],
            "display_name": display_name,
        },
    )
    flash(
        f"Adopted MAC {result['mac_address']}. "
        f"Enrolment token will be delivered on the device's next announce poll "
        f"(within ~30 s). Then the device will register itself.",
        "info",
    )
    return redirect(url_for("admin_ui.pending_adoption_page"))


@admin_ui_bp.post("/pending-adoption/<announcement_id>/reject")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def pending_adoption_reject_submit(announcement_id: str):
    result = svc_reject(announcement_id, by_user_id=g.current_user.id)
    if result is None:
        flash("Announcement not found.", "error")
    else:
        audit_service.record(
            "device_announcement.rejected",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="device_announcement",
            target_id=announcement_id,
            details={"mac_address": result["mac_address"]},
        )
        flash(f"Rejected MAC {result['mac_address']}.", "info")
    return redirect(url_for("admin_ui.pending_adoption_page"))


@admin_ui_bp.post("/pending-adoption/<announcement_id>/delete")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def pending_adoption_delete_submit(announcement_id: str):
    if svc_delete(announcement_id):
        audit_service.record(
            "device_announcement.deleted",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="device_announcement",
            target_id=announcement_id,
            details={},
        )
        flash("Announcement deleted.", "info")
    return redirect(url_for("admin_ui.pending_adoption_page"))


# ── API ────────────────────────────────────────────────────────────


@admin_api_bp.get("/pending-adoption")
@admin_required_api
def list_pending_api():
    show_all = (request.args.get("show_all") or "").lower() in ("1", "true", "yes")
    return ok(svc_list(include_consumed=show_all))


@admin_api_bp.post("/pending-adoption/<announcement_id>/adopt")
@role_required_api(*ADMIN_AND_UP)
def adopt_api(announcement_id: str):
    body = request.get_json(silent=True) or {}
    try:
        result = svc_adopt(
            announcement_id,
            by_user_id=g.current_user.id,
            display_name=body.get("display_name"),
        )
    except AnnouncementError as e:
        status = 404 if e.code == "not_found" else 409 if e.code == "rejected" else 400
        return err(e.code, e.message, status=status)
    audit_service.record(
        "device_announcement.adopted",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device_announcement",
        target_id=announcement_id,
        details={
            "mac_address": result["mac_address"],
            "via": "api",
        },
    )
    return ok(result)


@admin_api_bp.post("/pending-adoption/<announcement_id>/reject")
@role_required_api(*ADMIN_AND_UP)
def reject_api(announcement_id: str):
    result = svc_reject(announcement_id, by_user_id=g.current_user.id)
    if result is None:
        return err("not_found", "Announcement not found.", status=404)
    audit_service.record(
        "device_announcement.rejected",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device_announcement",
        target_id=announcement_id,
        details={"via": "api"},
    )
    return ok(result)
