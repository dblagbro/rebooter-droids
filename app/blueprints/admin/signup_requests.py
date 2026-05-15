"""v0.5.39: Admin UI + API for reviewing signup requests."""

from __future__ import annotations

from flask import current_app, g, redirect, render_template, request, url_for

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import (
    ADMIN_AND_UP,
    role_required_api,
    role_required_ui,
)
from app.middleware.response import err, ok
from app.models.users import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.services import audit as audit_service
from app.services.signup_requests import (
    SignupRequestError,
    approve_signup_request,
    get_signup_request,
    list_signup_requests,
    reject_signup_request,
)


# ── UI ─────────────────────────────────────────────────────────────────────

@admin_ui_bp.get("/signup-requests")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def signup_requests_page():
    """List all signup requests with filter by status."""
    status_filter = request.args.get("status", "pending")
    if status_filter == "all":
        status_filter = None

    requests_list = list_signup_requests(status=status_filter)
    return render_template(
        "signup_requests_list.html",
        **_ctx({
            "requests": requests_list,
            "status_filter": status_filter or "all",
        }),
    )


@admin_ui_bp.post("/signup-requests/<request_id>/approve")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def signup_request_approve_submit(request_id: str):
    """Approve a signup request and send invitation."""
    from flask import flash

    signup_req = get_signup_request(request_id)
    if not signup_req:
        flash("Signup request not found.", "error")
        return redirect(url_for("admin_ui.signup_requests_page"))

    if signup_req.status != "pending":
        flash(f"Request already {signup_req.status}.", "warning")
        return redirect(url_for("admin_ui.signup_requests_page"))

    # Get role from form, default to viewer
    role = request.form.get("role", "viewer")

    # Create invitation
    settings = current_app.config["SETTINGS"]
    from app.services.invitations import InvitationError, mint_invitation

    try:
        invitation, raw = mint_invitation(
            settings,
            email=signup_req.email,
            role=role,
            issued_by_user_id=g.current_user.id,
            note=f"Approved signup request from {signup_req.display_name}",
        )
    except InvitationError as e:
        flash(f"Failed to create invitation: {e.message}", "error")
        return redirect(url_for("admin_ui.signup_requests_page"))

    # Update signup request status
    try:
        approve_signup_request(
            request_id=request_id,
            reviewer_user_id=g.current_user.id,
            invitation_id=invitation.id,
        )
    except SignupRequestError as e:
        flash(f"Failed to approve: {e.message}", "error")
        return redirect(url_for("admin_ui.signup_requests_page"))

    # Send invitation email
    public_base = settings.public_base_url.rstrip("/")
    redeem_url = f"{public_base}/app/invite/{raw}"

    try:
        from app.services.email import send_invite_email
        send_invite_email(
            to=signup_req.email,
            role=role,
            redeem_url=redeem_url,
            note=None,
        )
        flash(f"Signup request approved and invitation sent to {signup_req.email}", "success")
    except Exception:
        current_app.logger.exception("invite email failed")
        flash(
            f"Signup request approved but email failed. Invitation URL: {redeem_url}",
            "warning"
        )

    audit_service.record(
        "signup_request.approved",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="signup_request",
        target_id=request_id,
        details={
            "email": signup_req.email,
            "role": role,
            "invitation_id": invitation.id,
        },
    )

    return redirect(url_for("admin_ui.signup_requests_page"))


@admin_ui_bp.post("/signup-requests/<request_id>/reject")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def signup_request_reject_submit(request_id: str):
    """Reject a signup request."""
    from flask import flash

    signup_req = get_signup_request(request_id)
    if not signup_req:
        flash("Signup request not found.", "error")
        return redirect(url_for("admin_ui.signup_requests_page"))

    if signup_req.status != "pending":
        flash(f"Request already {signup_req.status}.", "warning")
        return redirect(url_for("admin_ui.signup_requests_page"))

    try:
        reject_signup_request(
            request_id=request_id,
            reviewer_user_id=g.current_user.id,
        )
    except SignupRequestError as e:
        flash(f"Failed to reject: {e.message}", "error")
        return redirect(url_for("admin_ui.signup_requests_page"))

    flash(f"Signup request from {signup_req.email} rejected.", "info")

    audit_service.record(
        "signup_request.rejected",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="signup_request",
        target_id=request_id,
        details={"email": signup_req.email},
    )

    return redirect(url_for("admin_ui.signup_requests_page"))


# ── API ────────────────────────────────────────────────────────────────────

@admin_api_bp.get("/signup-requests")
@role_required_api(*ADMIN_AND_UP)
def list_signup_requests_api():
    """List signup requests, optionally filtered by status."""
    status = request.args.get("status")
    requests_list = list_signup_requests(status=status)

    return ok({
        "requests": [
            {
                "id": r.id,
                "email": r.email,
                "display_name": r.display_name,
                "message": r.message,
                "status": r.status,
                "reviewed_by_user_id": r.reviewed_by_user_id,
                "reviewed_at": r.reviewed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                if r.reviewed_at
                else None,
                "invitation_id": r.invitation_id,
                "created_at": r.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            for r in requests_list
        ]
    })


@admin_api_bp.post("/signup-requests/<request_id>/approve")
@role_required_api(*ADMIN_AND_UP)
def approve_signup_request_api(request_id: str):
    """Approve a signup request and create invitation."""
    body = request.get_json(silent=True) or {}
    role = body.get("role", "viewer")

    signup_req = get_signup_request(request_id)
    if not signup_req:
        return err("not_found", "Signup request not found", status=404)

    if signup_req.status != "pending":
        return err("already_reviewed", f"Request already {signup_req.status}", status=400)

    # Create invitation
    settings = current_app.config["SETTINGS"]
    from app.services.invitations import InvitationError, mint_invitation

    try:
        invitation, raw = mint_invitation(
            settings,
            email=signup_req.email,
            role=role,
            issued_by_user_id=g.current_user.id,
            note=f"Approved signup request from {signup_req.display_name}",
        )
    except InvitationError as e:
        return err(e.code, e.message, status=400)

    # Update signup request status
    try:
        approve_signup_request(
            request_id=request_id,
            reviewer_user_id=g.current_user.id,
            invitation_id=invitation.id,
        )
    except SignupRequestError as e:
        return err(e.code, e.message, status=400)

    # Send invitation email
    public_base = settings.public_base_url.rstrip("/")
    redeem_url = f"{public_base}/app/invite/{raw}"

    sent = False
    try:
        from app.services.email import send_invite_email
        sent = send_invite_email(
            to=signup_req.email,
            role=role,
            redeem_url=redeem_url,
            note=None,
        )
    except Exception:
        current_app.logger.exception("invite email failed")

    audit_service.record(
        "signup_request.approved",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="signup_request",
        target_id=request_id,
        details={
            "email": signup_req.email,
            "role": role,
            "invitation_id": invitation.id,
            "email_sent": sent,
        },
    )

    return ok({
        "approved": True,
        "invitation_id": invitation.id,
        "redeem_url": redeem_url,
        "email_sent": sent,
    })


@admin_api_bp.post("/signup-requests/<request_id>/reject")
@role_required_api(*ADMIN_AND_UP)
def reject_signup_request_api(request_id: str):
    """Reject a signup request."""
    signup_req = get_signup_request(request_id)
    if not signup_req:
        return err("not_found", "Signup request not found", status=404)

    if signup_req.status != "pending":
        return err("already_reviewed", f"Request already {signup_req.status}", status=400)

    try:
        reject_signup_request(
            request_id=request_id,
            reviewer_user_id=g.current_user.id,
        )
    except SignupRequestError as e:
        return err(e.code, e.message, status=400)

    audit_service.record(
        "signup_request.rejected",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="signup_request",
        target_id=request_id,
        details={"email": signup_req.email},
    )

    return ok({"rejected": True})
