from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps

from flask import current_app, g, redirect, request, session, url_for

from app.middleware.response import err
from app.models.users import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_SUPER_ADMIN,
    ROLE_VIEWER,
)
from app.services.auth import decode_token, load_user

# Convenience role sets used throughout the blueprints.
ANY_AUTHENTICATED = {ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER}
WRITE_ROLES = {ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATOR}
ADMIN_AND_UP = {ROLE_SUPER_ADMIN, ROLE_ADMIN}
SUPER_ADMIN_ONLY = {ROLE_SUPER_ADMIN}


def _is_jti_revoked(jti: str | None) -> bool:
    """v0.4.10 (BUG-005 enforce-mode): consult the server-side session
    table. Returns True only when an explicit revoked_at row exists.
    Missing rows fall through as not-revoked so legacy cookies/tokens
    that predate server-side bookkeeping still authenticate.
    """
    if not jti:
        return False
    try:
        from app.db import session_scope
        from app.models import Session as SessionRow
        with session_scope() as db:
            row = db.scalar(
                __import__("sqlalchemy").select(SessionRow)
                .where(SessionRow.jti == jti)
            )
            return bool(row and row.revoked_at is not None)
    except Exception:
        # Best-effort: never block auth on a session-store hiccup.
        return False


def _resolve_user_and_iat() -> tuple[object | None, int | None]:
    """Returns (user, iat_seconds_epoch) or (None, None)."""
    # Cookie session
    user_id = session.get("user_id")
    if user_id:
        user = load_user(user_id)
        if user is None:
            return None, None
        # v0.4.10: enforce server-side cookie revocation (BUG-005).
        if _is_jti_revoked(session.get("sid")):
            return None, None
        iat = session.get("iat")
        return user, iat

    # Bearer token
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1]
        try:
            payload = decode_token(
                current_app.config["SETTINGS"], token, expected_kind="access"
            )
        except Exception:
            return None, None
        user = load_user(payload.get("sub", ""))
        if user is None:
            return None, None
        # v0.4.10: enforce JWT-jti revocation (BUG-005).
        if _is_jti_revoked(payload.get("jti")):
            return None, None
        return user, payload.get("iat")
    return None, None


def _resolve_user(check_token_freshness: bool = True):
    user, iat = _resolve_user_and_iat()
    if user is None:
        # v0.3.7: defensively clear any stale cookie session so the
        # next request doesn't loop. Without this, a cookie carrying
        # a user_id pointing at a deleted-or-deactivated user would
        # make /app/* redirect to /app/login while /app/login then
        # redirects back to /app/ (because session.user_id is still
        # truthy) — ERR_TOO_MANY_REDIRECTS in the browser.
        if session.get("user_id"):
            session.clear()
        return None
    if not user.is_active:
        if session.get("user_id"):
            session.clear()
        return None

    if check_token_freshness and iat is not None:
        cutoff = getattr(user, "tokens_valid_after", None)
        if cutoff is not None and iat < int(cutoff.replace(tzinfo=timezone.utc).timestamp()):
            # Token / cookie was issued before the user's revocation cutoff.
            # Clear the cookie too — same reason as above; without it,
            # /app/login would see user_id and redirect back to /app/.
            if session.get("user_id"):
                session.clear()
            return None
    return user


def role_required_api(*allowed_roles: str):
    """Require any of the given roles for an API endpoint."""
    allowed = set(allowed_roles) if allowed_roles else ADMIN_AND_UP

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = _resolve_user()
            if user is None:
                return err("auth_required", "Authentication required.", status=401)
            if user.role not in allowed:
                return err(
                    "forbidden",
                    f"role '{user.role}' is not permitted for this action.",
                    status=403,
                )
            g.current_user = user
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def role_required_ui(*allowed_roles: str):
    allowed = set(allowed_roles) if allowed_roles else ANY_AUTHENTICATED

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = _resolve_user()
            if user is None:
                return redirect(url_for("admin_ui.login_page"))
            if user.role not in allowed:
                from flask import render_template, abort
                # 403 with friendly UI
                return (
                    render_template("forbidden.html", current_user=user, version=__import__("app.version", fromlist=["__version__"]).__version__),
                    403,
                )
            g.current_user = user
            return fn(*args, **kwargs)

        return wrapper

    return decorator


# Backwards-compatible aliases — every existing call site permitted any
# admin/operator. We narrow case-by-case as we migrate the routes.
def admin_required_api(fn):
    return role_required_api(*ADMIN_AND_UP, ROLE_OPERATOR, ROLE_VIEWER)(fn)


def admin_required_ui(fn):
    return role_required_ui(*ANY_AUTHENTICATED)(fn)


# ── RBAC SCOPE DECORATORS (B1 Phase 1 — v0.5.35) ────────────────────────
# These pair with `role_required_*`: the role decorator authenticates and
# sets `g.current_user`; the scope decorator then checks that user's role
# bindings against the resource id carried in the route URL. Apply the
# scope decorator *below* (inner to) the role decorator so `g.current_user`
# is set before it runs. In shadow mode a miss is logged as an
# `rbac.shadow_deny` audit row and the request proceeds; in enforce mode
# it returns 403 / renders forbidden.html. See app/services/role_bindings.

_SCOPE_REQUIRERS = ("device", "site", "group")


def _resolve_scope_requirer(scope: str):
    """Map a scope keyword to its require_can_act_on_* helper. Imported
    lazily so this module stays import-light at app start."""
    from app.services import role_bindings

    if scope == "device":
        return role_bindings.require_can_act_on_device
    if scope == "site":
        return role_bindings.require_can_act_on_site
    if scope == "group":
        return role_bindings.require_can_act_on_group
    raise ValueError(f"scope must be one of {_SCOPE_REQUIRERS}, got {scope!r}")


def scope_required_api(role_needed: str, *, scope: str, id_kwarg: str):
    """Require the caller's role bindings to cover the resource named by
    ``kwargs[id_kwarg]``. ``scope`` ∈ {"device","site","group"}. Apply
    below a `role_required_*` decorator on a JSON API route."""

    def decorator(fn):
        requirer = _resolve_scope_requirer(scope)

        @wraps(fn)
        def wrapper(*args, **kwargs):
            from app.services.role_bindings import RbacScopeDenied

            user = g.get("current_user")
            try:
                requirer(kwargs.get(id_kwarg), role_needed, user=user)
            except RbacScopeDenied:
                return err(
                    "forbidden",
                    "You do not have access to this resource.",
                    status=403,
                )
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def scope_required_ui(role_needed: str, *, scope: str, id_kwarg: str):
    """UI sibling of `scope_required_api` — renders forbidden.html on an
    enforce-mode deny instead of a JSON 403. Shipped in v0.5.35 alongside
    the API variant so the P3 list/detail-filtering work has the
    primitive ready; not yet wired to any route (the P1 demonstrators are
    both API routes)."""

    def decorator(fn):
        requirer = _resolve_scope_requirer(scope)

        @wraps(fn)
        def wrapper(*args, **kwargs):
            from app.services.role_bindings import RbacScopeDenied

            user = g.get("current_user")
            try:
                requirer(kwargs.get(id_kwarg), role_needed, user=user)
            except RbacScopeDenied:
                from flask import render_template

                from app.version import __version__

                return (
                    render_template(
                        "forbidden.html", current_user=user, version=__version__
                    ),
                    403,
                )
            return fn(*args, **kwargs)

        return wrapper

    return decorator
