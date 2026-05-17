"""Admin web-session login/logout pages.

The JSON `/api/v1/auth/*` API lives in `app/blueprints/auth.py` — this is
only the cookie-session HTML surface.
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask import redirect, render_template, request, session, url_for

from app.blueprints.admin import admin_ui_bp
from app.middleware.rate_limit import limiter
from app.services.auth import authenticate
from app.version import __version__


@admin_ui_bp.get("/login")
def login_page():
    """v0.3.7: don't redirect-on-cookie-truthiness alone. Verify the
    cookie's user_id resolves to an active, freshness-valid user
    before bouncing them to /app/. Otherwise a stale cookie
    (deleted user, deactivated, or `tokens_valid_after`-bumped
    cutoff) creates a redirect loop with admin_required_ui:
       /app/  → 302 /app/login (middleware can't load user)
       /app/login → 302 /app/  (we saw user_id in the cookie)
    Verify-or-clear breaks the loop."""
    user_id = session.get("user_id")
    if user_id:
        from app.middleware.admin_auth import _resolve_user

        if _resolve_user() is not None:
            return redirect(url_for("admin_ui.index"))
        # Stale cookie — _resolve_user already cleared the session.
        # Fall through to render the login form.
    return render_template("login.html", version=__version__, error=None)


@admin_ui_bp.post("/login")
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
    session["iat"] = int(datetime.now(timezone.utc).timestamp())
    session.permanent = True
    # v0.2.10 shadow-mode: record this cookie session server-side so a
    # future enforce path can revoke it independently of cookie expiry.
    from app.services import sessions as sessions_service

    jti = sessions_service.new_jti()
    session["sid"] = jti
    sessions_service.record(
        user_id=user.id,
        kind=sessions_service.KIND_COOKIE,
        jti=jti,
        ttl_seconds=60 * 60 * 24 * 31,  # match Flask permanent-session lifetime
    )
    return redirect(url_for("admin_ui.index"))


# ── v0.4.1: forgot-password + reset-password flow ─────────────────────────


@admin_ui_bp.get("/forgot-password")
def forgot_password_page():
    return render_template("forgot_password.html", version=__version__, sent=False)


@admin_ui_bp.post("/forgot-password")
@limiter.limit("10 per minute; 50 per hour")
def forgot_password_submit():
    from app.config import load_settings
    from app.services import audit as audit_service
    from app.services.email import send_password_reset_email
    from app.services.password_resets import request_reset

    email = (request.form.get("email") or "").strip()
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    token, masked = request_reset(email, ip=ip)
    smtp_error: str | None = None
    if token:
        settings = load_settings()
        url = f"{settings.public_base_url.rstrip('/')}/app/reset-password?token={token}"
        # BUG-030 (v0.4.6): never let an SMTP-side failure 500 the
        # forgot-password handler. The token IS minted in the DB
        # already; if email delivery fails the request still
        # succeeds-shaped, but the page tells the user (BUG-045
        # v0.4.15) so they don't sit waiting for an email that
        # never arrives.
        smtp_ok = False
        try:
            smtp_ok = send_password_reset_email(email, url)
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception(
                "password-reset email failed for %s: %s", email, e
            )
            smtp_error = type(e).__name__
        audit_service.record(
            "password_reset.requested",
            actor_user_id=None,
            actor_email_snapshot=email,
            target_type="user",
            target_id=None,
            details={"ip": ip, "smtp_ok": smtp_ok, "smtp_error": smtp_error},
        )
    # v0.4.15 (BUG-045): if SMTP failed AND we know the email is
    # registered (token was minted), surface the delivery failure
    # in the response. Pre-fix the page lied — claimed "we've
    # emailed" even when the SMTP send blew up. Users sat waiting
    # for an email that never arrived. The disclosure delta is
    # acceptable (an attacker can already see the masked email
    # echo, which proves the form processed their input).
    return render_template(
        "forgot_password.html",
        version=__version__,
        sent=True,
        masked=masked,
        delivery_warning=(smtp_error if token else None),
    )


@admin_ui_bp.get("/reset-password")
def reset_password_page():
    token = request.args.get("token", "")
    return render_template(
        "reset_password.html", version=__version__, token=token, error=None, done=False
    )


@admin_ui_bp.post("/reset-password")
@limiter.limit("10 per minute; 50 per hour")
def reset_password_submit():
    from app.services import audit as audit_service
    from app.services.password_resets import consume_reset

    token = (request.form.get("token") or "").strip()
    pw = request.form.get("password") or ""
    pw2 = request.form.get("password_confirm") or ""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    if not pw or pw != pw2:
        return render_template(
            "reset_password.html",
            version=__version__,
            token=token,
            error="Passwords don't match or are empty.",
            done=False,
        )
    if len(pw) < 8:
        return render_template(
            "reset_password.html",
            version=__version__,
            token=token,
            error="Password must be at least 8 characters.",
            done=False,
        )
    user = consume_reset(token, pw, ip=ip)
    if user is None:
        return render_template(
            "reset_password.html",
            version=__version__,
            token=token,
            error="This reset link is invalid or has expired. Request a new one.",
            done=False,
        )
    audit_service.record(
        "password_reset.consumed",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        target_type="user",
        target_id=user.id,
        details={"ip": ip},
    )
    return render_template(
        "reset_password.html",
        version=__version__,
        token="",
        error=None,
        done=True,
    )


@admin_ui_bp.get("/logout")
def logout():
    """Sign out of THIS browser session.

    Note: this does NOT invalidate any other JWT or cookie the user may
    have. Use the explicit "revoke all tokens" action (super-admin only)
    on /app/users to log a user out everywhere.

    v0.2.10 shadow-mode: also marks this cookie session row revoked so
    the future enforce path can short-circuit any leaked cookie.
    """
    sid = session.get("sid")
    session.clear()
    if sid:
        try:
            from app.services import sessions as sessions_service

            sessions_service.revoke_one(sid)
        except Exception:
            # BUG-060: a failed cookie-session revoke must not be
            # silent — log it (logout still redirects to login).
            import logging

            logging.getLogger(__name__).exception(
                "logout: cookie-session revoke failed for sid %s", sid
            )
    return redirect(url_for("admin_ui.login_page"))


# ── v0.5.39: signup request form ───────────────────────────────────────────

@admin_ui_bp.get("/signup-request")
def signup_request_page():
    """Public signup request form."""
    return render_template(
        "signup_request.html",
        version=__version__,
        submitted=False,
        error=None,
    )


@admin_ui_bp.post("/signup-request")
@limiter.limit("10 per hour")
def signup_request_submit():
    """Handle public signup request submission."""
    from app.services.signup_requests import (
        SignupRequestError,
        create_signup_request,
    )
    from app.services.email import notify_admins_of_signup_request

    email = (request.form.get("email") or "").strip()
    display_name = (request.form.get("display_name") or "").strip()
    message = (request.form.get("message") or "").strip() or None

    try:
        signup_req = create_signup_request(
            email=email,
            display_name=display_name,
            message=message,
        )
    except SignupRequestError as e:
        return render_template(
            "signup_request.html",
            version=__version__,
            submitted=False,
            error=e.message,
            email=email,
            display_name=display_name,
            message=message,
        )

    # Send email notification to all admins
    try:
        notify_admins_of_signup_request(signup_req)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Failed to send signup request notification")

    return render_template(
        "signup_request.html",
        version=__version__,
        submitted=True,
        error=None,
    )
