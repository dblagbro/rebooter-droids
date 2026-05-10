"""GET /app/ — admin dashboard with stat cards + activity feed."""

from __future__ import annotations

from flask import flash, g, redirect, render_template, request, url_for

from app.blueprints.admin import admin_ui_bp, admin_api_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import (
    admin_required_ui,
    role_required_api,
    role_required_ui,
    ADMIN_AND_UP,
)
from app.middleware.response import err, ok
from app.models.users import ROLE_SUPER_ADMIN
from app.services import audit as audit_service


@admin_ui_bp.get("/")
@admin_required_ui
def index():
    """v0.3.1 (P2): Status page replaces the v0.2.x stat-grid
    dashboard. The new shape answers "does anything need attention?"
    first, with totals + activity feed available below the fold."""
    from app.services import dashboard as dash_service
    from app.services import inbox as inbox_service

    from app.services import runtime_flags

    return render_template(
        "status.html",
        **_ctx(
            {
                "active": "status",
                "inbox": inbox_service.health_and_attention(limit=50),
                "stats": dash_service.stats(),
                "feed": dash_service.recent_activity(limit=15),
                "maintenance": runtime_flags.maintenance_mode_details(),
            }
        ),
    )


# ── v0.4.7 (B7): portal-wide watchdog maintenance toggle ──────────────────


@admin_ui_bp.post("/maintenance")
@role_required_ui(ROLE_SUPER_ADMIN)
def toggle_maintenance_submit():
    from app.services import runtime_flags

    on = (request.form.get("on") or "").lower() in ("1", "true", "on")
    reason = (request.form.get("reason") or "").strip() or None
    runtime_flags.set_maintenance_mode(on, user_id=g.current_user.id, reason=reason)
    audit_service.record(
        "maintenance_mode.toggled",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="runtime_flag",
        target_id="maintenance_mode_active",
        details={"on": on, "reason": reason},
    )
    flash(
        "Watchdog maintenance mode is now ON — all rules paused."
        if on
        else "Maintenance mode is OFF — watchdog rules will fire normally on the next tick.",
        "info",
    )
    return redirect(url_for("admin_ui.index"))


@admin_api_bp.post("/maintenance")
@role_required_api(ROLE_SUPER_ADMIN)
def toggle_maintenance_api():
    from app.services import runtime_flags

    body = request.get_json(silent=True) or {}
    if "on" not in body:
        return err("validation_failed", "`on` (bool) is required", status=400)
    on = bool(body["on"])
    reason = body.get("reason")
    runtime_flags.set_maintenance_mode(on, user_id=g.current_user.id, reason=reason)
    audit_service.record(
        "maintenance_mode.toggled",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="runtime_flag",
        target_id="maintenance_mode_active",
        details={"on": on, "reason": reason, "via": "api"},
    )
    return ok(runtime_flags.maintenance_mode_details())


@admin_api_bp.get("/maintenance")
@role_required_api(*ADMIN_AND_UP)
def get_maintenance_api():
    from app.services import runtime_flags

    return ok(runtime_flags.maintenance_mode_details())
