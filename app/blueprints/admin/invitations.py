"""Admin UI + API for invitations — mint, list, cancel, redeem-by-email."""

from __future__ import annotations

from flask import abort, current_app, g, redirect, render_template, request, session, url_for

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
from app.services.invitations import (
    InvitationError,
    cancel_invitation as svc_cancel_invitation,
    list_invitations,
    mint_invitation,
)


# ── UI ─────────────────────────────────────────────────────────────────────

@admin_ui_bp.get("/invitations")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def invitations_page():
    invs = list_invitations()
    return render_template(
        "invitations_list.html",
        **_ctx({"invitations": invs, "new_invite": session.pop("_new_invite", None)}),
    )


@admin_ui_bp.post("/invitations/<invitation_id>/cancel")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def invitations_cancel_submit(invitation_id: str):
    if svc_cancel_invitation(invitation_id):
        audit_service.record(
            "invitation.cancelled",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="invitation",
            target_id=invitation_id,
        )
    return redirect(url_for("admin_ui.invitations_page"))


@admin_ui_bp.post("/invitations")
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


# ── API ────────────────────────────────────────────────────────────────────

@admin_api_bp.get("/invitations")
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


@admin_api_bp.post("/invitations")
@role_required_api(*ADMIN_AND_UP)
def create_invitation():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip()
    role = body.get("role") or "admin"

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

    sent = False
    try:
        from app.services.email import send_invite_email

        sent = send_invite_email(
            to=email, role=role, redeem_url=redeem_url, note=body.get("note")
        )
    except Exception:
        current_app.logger.exception("invite email failed")

    audit_service.record(
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
            # Only returned to the inviter; never persisted in cleartext.
            "invitation_token": raw,
        },
        status=201,
    )


@admin_api_bp.delete("/invitations/<invitation_id>")
@role_required_api(*ADMIN_AND_UP)
def cancel_invitation_api(invitation_id: str):
    if not svc_cancel_invitation(invitation_id):
        return err(
            "invitation_unknown_or_consumed",
            "Invitation not found or already consumed.",
            status=404,
        )
    audit_service.record(
        "invitation.cancelled",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="invitation",
        target_id=invitation_id,
    )
    return ok({"cancelled": True})
