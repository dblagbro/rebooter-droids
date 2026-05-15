from __future__ import annotations

from flask import Blueprint, current_app, g, request, session

from app.middleware.admin_auth import admin_required_api
from app.middleware.rate_limit import limiter
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
@limiter.limit("30 per minute; 200 per hour")
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
    from datetime import datetime, timezone

    session.clear()
    session["user_id"] = user.id
    session["iat"] = int(datetime.now(timezone.utc).timestamp())
    session.permanent = True
    # v0.2.10 shadow-mode: record cookie + JWT sessions server-side.
    from app.services import sessions as sessions_service

    cookie_jti = sessions_service.new_jti()
    session["sid"] = cookie_jti
    sessions_service.record(
        user_id=user.id,
        kind=sessions_service.KIND_COOKIE,
        jti=cookie_jti,
        ttl_seconds=60 * 60 * 24 * 31,
    )

    access = issue_access_token(settings, user.id)
    refresh = issue_refresh_token(settings, user.id)

    return ok(
        {
            "user": {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name,
                "role": getattr(user, "role", "admin"),
            },
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
        }
    )


@bp.post("/logout")
def logout():
    user_id = session.get("user_id")
    sid = session.get("sid")
    session.clear()
    if user_id:
        try:
            from app.services.users import revoke_all_tokens

            revoke_all_tokens(user_id)
        except Exception:
            pass
    if sid:
        try:
            from app.services import sessions as sessions_service

            sessions_service.revoke_one(sid)
        except Exception:
            pass
    return ok({"logged_out": True})


@bp.post("/refresh")
@limiter.limit("30 per minute; 200 per hour")
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
            "role": getattr(u, "role", "admin"),
        }
    )


# ── Signup Requests (v0.5.39) ──────────────────────────────────────────────

@bp.post("/signup-request")
@limiter.limit("10 per hour")
def create_signup_request_api():
    """v0.5.39: Public endpoint for self-service signup requests.

    Rate-limited to prevent spam. Sends email notification to admins.
    """
    body = request.get_json(silent=True) or request.form.to_dict()
    email = (body.get("email") or "").strip()
    display_name = (body.get("display_name") or "").strip()
    message = (body.get("message") or "").strip() or None

    from app.services.signup_requests import (
        SignupRequestError,
        create_signup_request,
    )

    try:
        signup_req = create_signup_request(
            email=email,
            display_name=display_name,
            message=message,
        )
    except SignupRequestError as e:
        return err(e.code, e.message, status=400)

    # Send email notification to all admins
    try:
        from app.services.email import notify_admins_of_signup_request
        notify_admins_of_signup_request(signup_req)
    except Exception:
        current_app.logger.exception("Failed to send signup request notification")

    return ok(
        {
            "id": signup_req.id,
            "email": signup_req.email,
            "status": "pending",
            "message": "Your request has been submitted. An admin will review it shortly.",
        },
        status=201,
    )
