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
    if session.get("user_id"):
        return redirect(url_for("admin_ui.index"))
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
            pass
    return redirect(url_for("admin_ui.login_page"))
