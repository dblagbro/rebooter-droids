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


@admin_ui_bp.get("/settings/sync")
@admin_required_ui
def settings_sync_page():
    """v0.3.6 (P0 of RFC-004): stub Sync tab.

    Today's deployment is single-hub (one rebooter-droids container,
    one Postgres, two URLs proxying to it). True multi-hub sync is
    designed in `docs/RFC-004-multi-hub-sync.md` and not yet
    implemented. This page exists so the operator's mental model
    has a UI surface to land on.
    """
    import os

    return render_template(
        "settings/sync.html",
        **_ctx(
            {
                "active": "settings",
                "settings_tab": "sync",
                "hub_role": os.environ.get("REBOOTER_HUB_ROLE", "single"),
            }
        ),
    )


@admin_ui_bp.get("/settings/notifications")
@admin_required_ui
def settings_notifications_page():
    """v0.4.1: Notifications / SMTP. Read-only display of env-var
    SMTP config + 'send test email' action."""
    from flask import g

    from app.config import load_settings
    from app.services.email import is_configured

    s = load_settings()
    return render_template(
        "settings/notifications.html",
        **_ctx(
            {
                "active": "settings",
                "settings_tab": "notifications",
                "smtp": {
                    "host": s.smtp_host or "",
                    "port": s.smtp_port,
                    "user": s.smtp_user or "",
                    "from_addr": s.smtp_from or "",
                    "helo": s.smtp_helo or "",
                    "configured": is_configured(s),
                    # Never echo the password back. Render fingerprint
                    # only so the operator can verify it's set without
                    # the page leaking it.
                    "password_set": bool(s.smtp_password),
                },
                "current_user_email": getattr(g.current_user, "email", None),
            }
        ),
    )


@admin_ui_bp.post("/settings/notifications/test")
@admin_required_ui
def settings_notifications_test_submit():
    from flask import flash, g

    from app.services import audit as audit_service
    from app.services.email import send_test_email

    to = (request.form.get("to") or g.current_user.email or "").strip()
    if not to or "@" not in to:
        flash("Enter a valid email address.", "error")
        return redirect(url_for("admin_ui.settings_notifications_page"))
    try:
        ok_send = send_test_email(to)
    except Exception as e:
        flash(f"SMTP send failed: {e}", "error")
        return redirect(url_for("admin_ui.settings_notifications_page"))
    audit_service.record(
        "smtp.test_sent",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="smtp",
        target_id=None,
        details={"to": to, "ok": bool(ok_send)},
    )
    if ok_send:
        flash(f"Test email sent to {to}.", "info")
    else:
        flash(
            "SMTP not configured — set REBOOTER_SMTP_* env vars and recreate the container.",
            "error",
        )
    return redirect(url_for("admin_ui.settings_notifications_page"))


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
