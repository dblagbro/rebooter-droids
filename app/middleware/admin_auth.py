from __future__ import annotations

from functools import wraps

from flask import current_app, g, redirect, request, session, url_for

from app.middleware.response import err
from app.services.auth import decode_token, load_user


def _resolve_user():
    user_id = session.get("user_id")
    if user_id:
        return load_user(user_id)

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1]
        try:
            payload = decode_token(
                current_app.config["SETTINGS"], token, expected_kind="access"
            )
        except Exception:
            return None
        return load_user(payload.get("sub", ""))
    return None


def admin_required_api(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _resolve_user()
        if user is None or not user.is_admin or not user.is_active:
            return err("auth_required", "Authentication required.", status=401)
        g.current_user = user
        return fn(*args, **kwargs)

    return wrapper


def admin_required_ui(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _resolve_user()
        if user is None or not user.is_admin or not user.is_active:
            return redirect(url_for("admin_ui.login_page"))
        g.current_user = user
        return fn(*args, **kwargs)

    return wrapper
