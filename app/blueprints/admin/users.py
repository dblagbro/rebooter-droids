"""Admin UI + API for users (super-admin only for write actions)."""

from __future__ import annotations

from flask import abort, g, redirect, render_template, request, session, url_for

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import (
    ADMIN_AND_UP,
    SUPER_ADMIN_ONLY,
    role_required_api,
    role_required_ui,
)
from app.middleware.response import err, ok
from app.models.users import ALL_ROLES, ROLE_SUPER_ADMIN
from app.services import audit as audit_service
from app.services.users import (
    UserError,
    deactivate_user,
    list_users as svc_list_users,
    revoke_all_tokens,
    update_user_display_name,
    update_user_role,
)


# ── UI ─────────────────────────────────────────────────────────────────────

@admin_ui_bp.get("/users")
@role_required_ui(ROLE_SUPER_ADMIN, "admin")
def users_page():
    users = svc_list_users()
    return render_template("users_list.html", **_ctx({"users": users}))


@admin_ui_bp.post("/users/<user_id>/role")
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


@admin_ui_bp.post("/users/<user_id>/deactivate")
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


@admin_ui_bp.post("/users/<user_id>/revoke-tokens")
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
    # If the architect revoked their OWN tokens, kick this session too.
    if user_id == g.current_user.id:
        session.clear()
        return redirect(url_for("admin_ui.login_page"))
    return redirect(url_for("admin_ui.users_page"))


@admin_ui_bp.post("/users/<user_id>/display-name")
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


# ── API ────────────────────────────────────────────────────────────────────

@admin_api_bp.get("/users")
@role_required_api(*ADMIN_AND_UP)
def list_users_api():
    return ok({"users": svc_list_users()})


@admin_api_bp.post("/users/<user_id>/role")
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
    audit_service.record(
        "user.role_changed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="user",
        target_id=user_id,
        details={"new_role": role},
    )
    return ok(out)


@admin_api_bp.post("/users/<user_id>/deactivate")
@role_required_api(*SUPER_ADMIN_ONLY)
def deactivate_user_api(user_id: str):
    if user_id == g.current_user.id:
        return err("forbidden", "You cannot deactivate your own account.", status=403)
    if not deactivate_user(user_id):
        return err("user_unknown", "User not found.", status=404)
    audit_service.record(
        "user.deactivated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="user",
        target_id=user_id,
    )
    return ok({"deactivated": True})


@admin_api_bp.post("/users/<user_id>/revoke-tokens")
@role_required_api(*SUPER_ADMIN_ONLY)
def revoke_tokens_api(user_id: str):
    if not revoke_all_tokens(user_id):
        return err("user_unknown", "User not found.", status=404)
    audit_service.record(
        "user.tokens_revoked",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="user",
        target_id=user_id,
    )
    return ok({"revoked": True})


@admin_api_bp.post("/users/<user_id>/display-name")
@role_required_api(*SUPER_ADMIN_ONLY)
def change_user_display_name_api(user_id: str):
    body = request.get_json(silent=True) or {}
    name = (body.get("display_name") or "").strip()
    if not name:
        return err("validation_failed", "display_name is required", status=400)
    try:
        out = update_user_display_name(user_id, name)
    except UserError as e:
        return err("validation_failed", str(e), status=400)
    if out is None:
        return err("user_unknown", "User not found.", status=404)
    audit_service.record(
        "user.display_name_changed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="user",
        target_id=user_id,
        details={"new_display_name": name},
    )
    return ok(out)
