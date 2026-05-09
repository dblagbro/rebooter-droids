"""Settings — parent page for system / network / auth / users /
firmware / theme.

v0.3.0 P1 implementation: a tabbed parent page that links into
existing pages where they already exist (Users, Audit, Firmware,
Invitations, Profile, Sites). New surfaces (System, Network, Auth,
Backup, API tokens, Webhooks) ship as stub sub-pages explicitly
marked "Coming in P5/P6 of the redesign plan".

Settings tabs are rendered via the v3-tabs component in the
templates. The actual pages are reachable both directly (their
existing URLs) and via the Settings tab strip.
"""

from __future__ import annotations

from flask import make_response, redirect, render_template, request, url_for

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_ui

ALLOWED_THEMES = ("system", "light", "dark")


@admin_ui_bp.get("/settings")
@admin_required_ui
def settings_page():
    return render_template(
        "settings/index.html",
        **_ctx({"active": "settings", "settings_tab": "overview"}),
    )


@admin_ui_bp.get("/settings/system")
@admin_required_ui
def settings_system_page():
    return render_template(
        "settings/system.html",
        **_ctx({"active": "settings", "settings_tab": "system"}),
    )


@admin_ui_bp.get("/settings/network")
@admin_required_ui
def settings_network_page():
    return render_template(
        "settings/network.html",
        **_ctx({"active": "settings", "settings_tab": "network"}),
    )


@admin_ui_bp.get("/settings/auth")
@admin_required_ui
def settings_auth_page():
    return render_template(
        "settings/auth.html",
        **_ctx({"active": "settings", "settings_tab": "auth"}),
    )


_THEME_COOKIE = "rebooter_theme"


def _theme_from_request() -> str:
    """v0.3.3 prefers `rebooter_theme`; v0.3.0–0.3.2 wrote `theme`.
    Read both during the deprecation window so users don't lose
    their light-mode preference on upgrade."""
    return (
        request.cookies.get(_THEME_COOKIE)
        or request.cookies.get("theme")
        or "system"
    )


@admin_ui_bp.get("/settings/theme")
@admin_required_ui
def settings_theme_page():
    return render_template(
        "settings/theme.html",
        **_ctx(
            {
                "active": "settings",
                "settings_tab": "theme",
                "current_theme": _theme_from_request(),
            }
        ),
    )


@admin_ui_bp.post("/settings/theme")
@admin_required_ui
def settings_theme_submit():
    """Persist the picked theme as a cookie. No DB write — theme is
    a per-browser preference, not a per-user one (a user might want
    light at the office, dark at home).

    v0.3.3: cookie renamed to `rebooter_theme` (avoids collision
    with peer voipguru.org apps) and optionally cross-subdomain
    scoped via REBOOTER_COOKIE_DOMAIN so the operator's choice
    carries between www and www2.
    """
    from flask import current_app

    settings = current_app.config["SETTINGS"]
    picked = (request.form.get("theme") or "system").strip().lower()
    if picked not in ALLOWED_THEMES:
        picked = "system"
    resp = make_response(redirect(url_for("admin_ui.settings_theme_page")))
    cookie_kwargs: dict = {
        "max_age": 60 * 60 * 24 * 365,
        "samesite": "Lax",
        "secure": True,
        "httponly": False,  # the FOUC prevention script reads it
    }
    if settings.cookie_domain:
        cookie_kwargs["domain"] = settings.cookie_domain
    resp.set_cookie(_THEME_COOKIE, picked, **cookie_kwargs)
    # Clear any v0.3.0–0.3.2 host-scoped legacy cookie so the browser
    # doesn't keep sending it.
    resp.delete_cookie("theme")
    return resp
