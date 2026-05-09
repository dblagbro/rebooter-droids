"""Shared helpers for the admin UI/API submodules.

Anything imported from here is used by ≥2 feature modules. Per-feature
imports stay in the feature module.
"""

from __future__ import annotations

from flask import g

from app.version import __version__


def _ctx(extra: dict | None = None) -> dict:
    """Build the standard template context: version + current_user + the
    unregistered-auth-attempt badge count for the nav bar.

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
    }
    if extra:
        base.update(extra)
    return base
