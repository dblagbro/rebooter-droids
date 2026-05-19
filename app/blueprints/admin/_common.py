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


# v0.5.101 — numeric form-field hardening.
# Several blueprint handlers used to call `int(request.form.get(name) or default)`
# inline, which 500'd on any non-numeric operator input. The `_int_field`
# helper below normalises empty/missing → default and raises a typed
# FormValidationError on a bad value; the handler catches it and flashes a
# friendly error instead of returning a 500. See the refactor-log v0.5.67
# entry's "Remaining technical debt" item and the v0.5.101 hardening ship.

class FormValidationError(ValueError):
    """A numeric form/body field couldn't be coerced to an integer.

    The handler's `try/except` translates this into a flash + redirect
    (for form routes) or a `validation_failed` error envelope (for JSON
    API routes). The message is operator-friendly; `field` + `raw` are
    available for richer error rendering if a caller wants them."""

    def __init__(self, field: str, raw, *, lo=None, hi=None):
        self.field = field
        self.raw = raw
        if raw is None or str(raw).strip() == "":
            self.message = f"{field} must be an integer"
        elif lo is not None or hi is not None:
            self.message = (
                f"{field}={raw!r} must be an integer "
                f"in [{lo if lo is not None else '-∞'}, "
                f"{hi if hi is not None else '∞'}]"
            )
        else:
            self.message = f"{field}={raw!r} must be an integer"
        super().__init__(self.message)


def _int_field(source, name: str, *, default: int, lo: int | None = None,
               hi: int | None = None) -> int:
    """Coerce `source[name]` to int with operator-friendly error reporting.

    `source` is anything with `.get(name)` — `request.form`,
    `request.get_json()` dict, a `MultiDict`. Empty / missing values
    fall back to `default` (matching the prior `int(... or default)`
    behaviour). A non-integer raises `FormValidationError`, which the
    blueprint handler catches and turns into a flash + redirect (form
    routes) or a `validation_failed` envelope (JSON routes). Optional
    `lo` / `hi` bounds are enforced when supplied — callers are
    deliberately conservative about adding bounds so this helper is a
    drop-in for existing sites without behaviour change.
    """
    raw = source.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        raise FormValidationError(name, raw)
    if lo is not None and v < lo:
        raise FormValidationError(name, raw, lo=lo, hi=hi)
    if hi is not None and v > hi:
        raise FormValidationError(name, raw, lo=lo, hi=hi)
    return v
