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
    """v0.4.26: editable system settings (DB → env-var fallback).
    v0.5.44 (P5): added RBAC enforce mode toggle."""
    from app.services import runtime_settings

    cfg = runtime_settings.system_config()
    rbac_enforce_mode = runtime_settings.get(
        "rbac.enforce_mode",
        env_var="REBOOTER_RBAC_ENFORCE_MODE",
        default="shadow",
    )
    return render_template(
        "settings/system.html",
        **_ctx(
            {
                "active": "settings",
                "settings_tab": "system",
                "system": {
                    "portal_name":                cfg.get("system.portal_name") or "",
                    "invitation_ttl_seconds":     cfg.get("system.invitation_ttl_seconds"),
                    "password_reset_ttl_seconds": cfg.get("system.password_reset_ttl_seconds"),
                    "session_idle_timeout_seconds": cfg.get("system.session_idle_timeout_seconds"),
                    "enrollment_token_ttl_seconds": cfg.get("system.enrollment_token_ttl_seconds"),
                    "overrides": {
                        k.split(".", 1)[1]: runtime_settings.has_db_value(k)
                        for k, _ in runtime_settings.SYSTEM_KEYS
                    },
                },
                "rbac": {
                    "enforce_mode": str(rbac_enforce_mode).strip().lower(),
                    "is_override": runtime_settings.has_db_value("rbac.enforce_mode"),
                },
            }
        ),
    )


@admin_ui_bp.post("/settings/system/save")
@admin_required_ui
def settings_system_save_submit():
    from flask import flash, g

    from app.services import audit as audit_service
    from app.services import runtime_settings

    fields = (
        ("system.portal_name",                "portal_name", str),
        ("system.invitation_ttl_seconds",     "invitation_ttl_seconds", int),
        ("system.password_reset_ttl_seconds", "password_reset_ttl_seconds", int),
        ("system.session_idle_timeout_seconds", "session_idle_timeout_seconds", int),
        ("system.enrollment_token_ttl_seconds", "enrollment_token_ttl_seconds", int),
    )
    changed: list[str] = []
    for key, form, coerce in fields:
        raw = (request.form.get(form) or "").strip()
        if not raw:
            if runtime_settings.has_db_value(key):
                runtime_settings.delete(key)
                changed.append(f"{key}=cleared")
            continue
        try:
            value = coerce(raw)
        except (TypeError, ValueError):
            flash(f"Invalid value for {form}: {raw!r}", "error")
            return redirect(url_for("admin_ui.settings_system_page"))
        runtime_settings.set_(key, value, user_id=g.current_user.id)
        changed.append(f"{key}=set")

    # v0.5.44 (P5): RBAC enforce mode toggle
    rbac_mode = (request.form.get("rbac_enforce_mode") or "").strip().lower()
    if rbac_mode in ("shadow", "enforce"):
        old_mode = runtime_settings.get("rbac.enforce_mode", env_var="REBOOTER_RBAC_ENFORCE_MODE", default="shadow")
        if rbac_mode != old_mode:
            runtime_settings.set_("rbac.enforce_mode", rbac_mode, user_id=g.current_user.id)
            changed.append(f"rbac.enforce_mode={rbac_mode}")
    elif rbac_mode == "":
        # Empty = revert to env-var default
        if runtime_settings.has_db_value("rbac.enforce_mode"):
            runtime_settings.delete("rbac.enforce_mode")
            changed.append("rbac.enforce_mode=cleared")

    audit_service.record(
        "system.config_updated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="runtime_settings",
        target_id="system",
        details={"changed": changed},
    )
    flash("System settings saved. TTLs take effect immediately.", "info")
    return redirect(url_for("admin_ui.settings_system_page"))


@admin_ui_bp.post("/settings/system/clear")
@admin_required_ui
def settings_system_clear_submit():
    from flask import flash, g

    from app.services import audit as audit_service
    from app.services import runtime_settings

    cleared = 0
    for key, _ in runtime_settings.SYSTEM_KEYS:
        if runtime_settings.delete(key):
            cleared += 1
    audit_service.record(
        "system.config_cleared",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="runtime_settings",
        target_id="system",
        details={"cleared_count": cleared},
    )
    flash(
        f"Cleared {cleared} DB override{'s' if cleared != 1 else ''}; reverted to env-var defaults.",
        "info",
    )
    return redirect(url_for("admin_ui.settings_system_page"))


@admin_ui_bp.get("/settings/network")
@admin_required_ui
def settings_network_page():
    """v0.4.26: editable network settings (DB → env-var fallback).
    CORS + cookie_domain take effect on next container restart;
    public URLs + rate-limit exempt IPs are live."""
    from app.services import runtime_settings

    cfg = runtime_settings.network_config()
    return render_template(
        "settings/network.html",
        **_ctx(
            {
                "active": "settings",
                "settings_tab": "network",
                "network": {
                    "public_base_url":      cfg.get("network.public_base_url") or "",
                    "firmware_public_base": cfg.get("network.firmware_public_base") or "",
                    "cors_allowed_origins": cfg.get("network.cors_allowed_origins") or "",
                    "rate_limit_exempt_ips": cfg.get("network.rate_limit_exempt_ips") or "",
                    "cookie_domain":        cfg.get("network.cookie_domain") or "",
                    "overrides": {
                        k.split(".", 1)[1]: runtime_settings.has_db_value(k)
                        for k, _ in runtime_settings.NETWORK_KEYS
                    },
                    "live_editable": {
                        k.split(".", 1)[1]: runtime_settings.is_live_editable(k)
                        for k, _ in runtime_settings.NETWORK_KEYS
                    },
                },
            }
        ),
    )


@admin_ui_bp.post("/settings/network/save")
@admin_required_ui
def settings_network_save_submit():
    from flask import flash, g

    from app.services import audit as audit_service
    from app.services import runtime_settings

    fields = (
        ("network.public_base_url", "public_base_url"),
        ("network.firmware_public_base", "firmware_public_base"),
        ("network.cors_allowed_origins", "cors_allowed_origins"),
        ("network.rate_limit_exempt_ips", "rate_limit_exempt_ips"),
        ("network.cookie_domain", "cookie_domain"),
    )
    changed: list[str] = []
    for key, form in fields:
        raw = (request.form.get(form) or "").strip()
        if not raw:
            if runtime_settings.has_db_value(key):
                runtime_settings.delete(key)
                changed.append(f"{key}=cleared")
            continue
        runtime_settings.set_(key, raw, user_id=g.current_user.id)
        changed.append(f"{key}=set")

    audit_service.record(
        "network.config_updated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="runtime_settings",
        target_id="network",
        details={"changed": changed},
    )
    flash(
        "Network settings saved. Public URLs + rate-limit exempt IPs take effect immediately. "
        "CORS allowlist + cookie domain require a container restart.",
        "info",
    )
    return redirect(url_for("admin_ui.settings_network_page"))


@admin_ui_bp.post("/settings/network/clear")
@admin_required_ui
def settings_network_clear_submit():
    from flask import flash, g

    from app.services import audit as audit_service
    from app.services import runtime_settings

    cleared = 0
    for key, _ in runtime_settings.NETWORK_KEYS:
        if runtime_settings.delete(key):
            cleared += 1
    audit_service.record(
        "network.config_cleared",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="runtime_settings",
        target_id="network",
        details={"cleared_count": cleared},
    )
    flash(
        f"Cleared {cleared} DB override{'s' if cleared != 1 else ''}; reverted to env-var defaults.",
        "info",
    )
    return redirect(url_for("admin_ui.settings_network_page"))


@admin_ui_bp.get("/settings/auth")
@admin_required_ui
def settings_auth_page():
    return render_template(
        "settings/auth.html",
        **_ctx({"active": "settings", "settings_tab": "auth"}),
    )



@admin_ui_bp.get("/settings/notifications")
@admin_required_ui
def settings_notifications_page():
    """v0.4.1: Notifications / SMTP read-only.
    v0.4.25: SMTP fields are now editable via DB-backed runtime
    settings. DB rows win; env-var fallback for fields with no DB
    override. Operator can rotate creds without recreating the
    container."""
    from flask import g

    from app.services import runtime_settings
    from app.services.email import is_configured

    cfg = runtime_settings.smtp_config()
    return render_template(
        "settings/notifications.html",
        **_ctx(
            {
                "active": "settings",
                "settings_tab": "notifications",
                "smtp": {
                    "host": cfg.get("smtp.host") or "",
                    "port": cfg.get("smtp.port"),
                    "user": cfg.get("smtp.user") or "",
                    "from_addr": cfg.get("smtp.from") or "",
                    "helo": cfg.get("smtp.helo") or "",
                    "configured": is_configured(),
                    # Never echo the password back. Render fingerprint
                    # only so the operator can verify it's set without
                    # the page leaking it.
                    "password_set": bool(cfg.get("smtp.password")),
                    # Tell the operator which knobs are DB overrides
                    # vs env-var fallbacks. Helps them understand
                    # state at a glance.
                    "overrides": {
                        k.split(".")[1]: runtime_settings.has_db_value(k)
                        for k, _ in runtime_settings.SMTP_KEYS
                    },
                },
                "current_user_email": getattr(g.current_user, "email", None),
            }
        ),
    )


@admin_ui_bp.post("/settings/notifications/save")
@admin_required_ui
def settings_notifications_save_submit():
    """v0.4.25: persist SMTP creds to runtime_settings."""
    from flask import flash, g

    from app.services import audit as audit_service
    from app.services import runtime_settings

    fields = (
        ("smtp.host", "host"),
        ("smtp.port", "port"),
        ("smtp.user", "user"),
        ("smtp.password", "password"),
        ("smtp.from", "from"),
        ("smtp.helo", "helo"),
    )
    changed: list[str] = []
    for key, form_field in fields:
        raw = (request.form.get(form_field) or "").strip()
        # If operator submits empty AND there's no env-var fallback
        # for this field, we treat empty as "delete the override".
        # If operator wants the field empty when env-var has a value,
        # they should clear via the explicit "Clear override" button.
        if not raw:
            # Empty form input → delete the DB override (revert to env)
            if runtime_settings.has_db_value(key):
                runtime_settings.delete(key)
                changed.append(f"{key}=cleared")
            continue
        if key == "smtp.password" and raw == "********":
            # Form pre-filled with masked placeholder; ignore.
            continue
        runtime_settings.set_(key, raw, user_id=g.current_user.id)
        changed.append(f"{key}=set")

    audit_service.record(
        "smtp.config_updated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="runtime_settings",
        target_id="smtp",
        details={"changed": changed},
    )
    flash("SMTP settings saved. Use 'Send test email' below to verify.", "info")
    return redirect(url_for("admin_ui.settings_notifications_page"))


@admin_ui_bp.post("/settings/notifications/clear")
@admin_required_ui
def settings_notifications_clear_submit():
    """Operator-initiated 'revert to env-var defaults'."""
    from flask import flash, g

    from app.services import audit as audit_service
    from app.services import runtime_settings

    cleared = 0
    for key, _ in runtime_settings.SMTP_KEYS:
        if runtime_settings.delete(key):
            cleared += 1
    audit_service.record(
        "smtp.config_cleared",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="runtime_settings",
        target_id="smtp",
        details={"cleared_count": cleared},
    )
    flash(
        f"Cleared {cleared} DB override{'s' if cleared != 1 else ''}; "
        f"reverted to env-var defaults.",
        "info",
    )
    return redirect(url_for("admin_ui.settings_notifications_page"))


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


# ─────────────────────────────────────────────────────────────────────────────
# Sync settings (v0.5.48 / B11 Phase 7)
# ─────────────────────────────────────────────────────────────────────────────

@admin_ui_bp.get("/settings/sync")
@admin_required_ui
def settings_sync_page():
    """Settings → Sync tab (RFC-004 Option C multi-hub sync config).

    v0.5.48 (B11 Phase 7): operator-facing UI for:
    - Enable/disable sync replicator
    - Configure this hub's identifier + HMAC key
    - Configure peer hubs (JSON list)
    - View sync status (outbox depth, peer cursors)
    """
    import json
    import requests

    from app.services import runtime_settings as rs

    # Get sync configuration
    sync_enabled = rs.get("sync.enabled", default=False)
    hub_id = rs.get("sync.hub_id", default="www")
    hmac_key = rs.get("sync.hmac_key", default="")
    peer_hubs_json = rs.get("sync.peer_hubs", default="[]")

    # Parse peer_hubs for display
    if isinstance(peer_hubs_json, str):
        peer_hubs = peer_hubs_json
    else:
        peer_hubs = json.dumps(peer_hubs_json, indent=2)

    # Fetch sync status from API
    sync_status = None
    try:
        resp = requests.get(
            "http://localhost:8090/api/v1/sync/status",
            headers={"Authorization": f"Bearer {g.current_user.get_bearer_token()}"},
            timeout=5,
        )
        if resp.ok:
            sync_status = resp.json()
    except Exception:
        pass  # Sync status unavailable

    return render_template(
        "settings/sync.html",
        **_ctx(
            {
                "active": "settings",
                "settings_tab": "sync",
                "sync_enabled": sync_enabled,
                "hub_id": hub_id,
                "hmac_key": hmac_key,
                "peer_hubs": peer_hubs,
                "sync_status": sync_status,
            }
        ),
    )


@admin_ui_bp.post("/settings/sync")
@admin_required_ui
def settings_sync_save_submit():
    """Save sync settings from the Settings → Sync form."""
    import json

    from flask import flash, g
    from app.services import runtime_settings as rs

    # Parse and validate inputs
    sync_enabled = request.form.get("sync_enabled", "false").strip().lower() == "true"
    hub_id = request.form.get("hub_id", "").strip() or "www"
    hmac_key = request.form.get("hmac_key", "").strip()
    peer_hubs_raw = request.form.get("peer_hubs", "").strip()

    # Validate peer_hubs JSON
    try:
        if peer_hubs_raw:
            peer_hubs = json.loads(peer_hubs_raw)
            if not isinstance(peer_hubs, list):
                raise ValueError("peer_hubs must be a JSON array")
        else:
            peer_hubs = []
    except (json.JSONDecodeError, ValueError) as e:
        flash(f"Invalid peer_hubs JSON: {e}", "error")
        return redirect(url_for("admin_ui.settings_sync_page"))

    # Validate HMAC key format (if provided)
    if hmac_key:
        try:
            bytes.fromhex(hmac_key)
            if len(hmac_key) != 64:
                raise ValueError("HMAC key must be 64 hex characters (32 bytes)")
        except (ValueError, TypeError) as e:
            flash(f"Invalid HMAC key: {e}", "error")
            return redirect(url_for("admin_ui.settings_sync_page"))

    # Save settings
    rs.set_("sync.enabled", sync_enabled, user_id=g.current_user.id)
    rs.set_("sync.hub_id", hub_id, user_id=g.current_user.id)
    if hmac_key:
        rs.set_("sync.hmac_key", hmac_key, user_id=g.current_user.id)
    rs.set_("sync.peer_hubs", json.dumps(peer_hubs), user_id=g.current_user.id)

    flash("Sync settings saved", "success")
    return redirect(url_for("admin_ui.settings_sync_page"))
