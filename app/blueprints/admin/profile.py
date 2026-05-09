"""Self-service /app/me/* — every authenticated user can manage their
own display name, password, and revoke their own active sessions.

UI-only; no JSON API surface.
"""

from __future__ import annotations

from flask import g, redirect, render_template, request, session, url_for

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_ui
from app.services import audit as audit_service
from app.services.users import (
    UserError,
    change_own_display_name,
    change_own_password,
    revoke_all_tokens,
)


@admin_ui_bp.get("/me")
@admin_required_ui
def me_page():
    return render_template(
        "me.html",
        **_ctx({"flash_msg": session.pop("_me_flash", None)}),
    )


@admin_ui_bp.post("/me/display-name")
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


@admin_ui_bp.post("/me/password")
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
    # so the user re-authenticates with the new password.
    session.clear()
    return redirect(url_for("admin_ui.login_page"))


@admin_ui_bp.post("/me/revoke-everywhere")
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
