"""Shared helpers for the admin UI/API submodules.

Anything imported from here is used by ≥2 feature modules. Per-feature
imports stay in the feature module.
"""

from __future__ import annotations

from flask import g, has_request_context, request

from app.version import __version__


# v0.3.0 P1: map URL prefixes to one of the five top-nav slots
# (status / devices / rules / history / settings). A blueprint that
# wants to override the auto-derivation passes `active="..."` in its
# `_ctx()` extras dict.
_ACTIVE_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("/app/rules", "rules"),
    ("/app/history", "history"),
    ("/app/audit", "history"),       # current home of audit; will redirect P6
    ("/app/devices", "devices"),
    ("/app/groups", "devices"),
    ("/app/sites", "devices"),
    ("/app/events", "devices"),
    ("/app/unregistered-devices", "devices"),
    ("/app/enrollment-tokens", "devices"),
    ("/app/settings", "settings"),
    ("/app/users", "settings"),
    ("/app/invitations", "settings"),
    ("/app/firmware", "settings"),
    ("/app/me", "settings"),
)


def _derive_active() -> str | None:
    if not has_request_context():
        return None
    path = request.path
    if path == "/app/" or path == "/app":
        return "status"
    for prefix, slot in _ACTIVE_BY_PREFIX:
        if path == prefix or path.startswith(prefix + "/"):
            return slot
    return None


def _ctx(extra: dict | None = None) -> dict:
    """Build the standard template context: version + current_user + the
    unregistered-auth-attempt badge count for the nav bar +
    auto-derived `active` slot for the v0.3.0 5-item nav.

    Best-effort: the unregistered count is wrapped in try/except so a
    failure in the tracker can never break a page render.
    """
    try:
        from app.services import unregistered as unreg_service

        unregistered_active = unreg_service.count_active(since_minutes=60)
    except Exception:
        unregistered_active = 0

    base = {
        "version": __version__,
        "current_user": g.current_user,
        "unregistered_active": unregistered_active,
        "active": _derive_active(),
    }
    if extra:
        # explicit `active=` in extras still wins over the URL derivation
        base.update(extra)
    return base
