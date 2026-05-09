"""Public invite-redemption flow at /app/invite/<token>.

Unauthenticated; called by the invitee from their email link. Renders the
invitation form on GET, validates + creates the user + signs them in on
POST.
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask import redirect, render_template, request, session, url_for

from app.blueprints.admin import admin_ui_bp
from app.services import audit as audit_service
from app.services.invitations import (
    InvitationError,
    lookup_pending,
    redeem_invitation,
)
from app.version import __version__


@admin_ui_bp.get("/invite/<token>")
def invite_redeem_page(token: str):
    inv = lookup_pending(token)
    return render_template(
        "invite_redeem.html",
        version=__version__,
        invitation=inv,
        token=token,
        error=None,
    )


@admin_ui_bp.post("/invite/<token>")
def invite_redeem_submit(token: str):
    password = request.form.get("password") or ""
    confirm = request.form.get("password_confirm") or ""
    display_name = (request.form.get("display_name") or "").strip()
    if password != confirm:
        return (
            render_template(
                "invite_redeem.html",
                version=__version__,
                invitation=lookup_pending(token),
                token=token,
                error="Passwords do not match.",
            ),
            400,
        )
    try:
        user = redeem_invitation(
            token=token, password=password, display_name=display_name
        )
    except InvitationError as e:
        return (
            render_template(
                "invite_redeem.html",
                version=__version__,
                invitation=lookup_pending(token),
                token=token,
                error=e.message,
            ),
            400,
        )

    audit_service.record(
        "user.created_via_invite",
        actor_user_id=user["id"],
        actor_email_snapshot=user["email"],
        target_type="user",
        target_id=user["id"],
    )
    # Sign the user in immediately.
    session.clear()
    session["user_id"] = user["id"]
    session["iat"] = int(datetime.now(timezone.utc).timestamp())
    session.permanent = True
    return redirect(url_for("admin_ui.index"))
