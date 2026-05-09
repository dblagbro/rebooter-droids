from __future__ import annotations

from flask import Blueprint, current_app, g, request, session

from app.middleware.admin_auth import admin_required_api
from app.middleware.response import err, ok
from app.services.auth import (
    authenticate,
    decode_token,
    issue_access_token,
    issue_refresh_token,
    load_user,
)

bp = Blueprint("auth", __name__)


@bp.post("/login")
def login():
    body = request.get_json(silent=True) or request.form.to_dict()
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if not email or not password:
        return err("validation_failed", "email and password are required", status=400)

    user = authenticate(email, password)
    if user is None:
        return err("auth_invalid", "Invalid email or password.", status=401)

    settings = current_app.config["SETTINGS"]
    session.clear()
    session["user_id"] = user.id
    session.permanent = True

    access = issue_access_token(settings, user.id)
    refresh = issue_refresh_token(settings, user.id)

    return ok(
        {
            "user": {"id": user.id, "email": user.email, "display_name": user.display_name},
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
        }
    )


@bp.post("/logout")
def logout():
    session.clear()
    return ok({"logged_out": True})


@bp.post("/refresh")
def refresh():
    body = request.get_json(silent=True) or {}
    token = body.get("refresh_token")
    if not token:
        return err("validation_failed", "refresh_token is required", status=400)
    settings = current_app.config["SETTINGS"]
    try:
        payload = decode_token(settings, token, expected_kind="refresh")
    except Exception:
        return err("auth_invalid", "Invalid or expired refresh token.", status=401)

    user = load_user(payload.get("sub", ""))
    if user is None or not user.is_active:
        return err("auth_invalid", "User no longer active.", status=401)

    return ok(
        {
            "access_token": issue_access_token(settings, user.id),
            "refresh_token": issue_refresh_token(settings, user.id),
            "token_type": "Bearer",
        }
    )


@bp.get("/me")
@admin_required_api
def me():
    u = g.current_user
    return ok(
        {
            "id": u.id,
            "email": u.email,
            "display_name": u.display_name,
            "is_admin": u.is_admin,
            "is_super_admin": getattr(u, "is_super_admin", False),
        }
    )
