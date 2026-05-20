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
    first, with totals + activity feed available below the fold.

    Tier-2 Feature 5: mobile-first restructure. The only Python change
    is the derived `attention_items` list — `_ctx` already injects
    `unregistered_active`, so the list is completed here with that
    per-request count folded in."""
    from app.services import dashboard as dash_service
    from app.services import inbox as inbox_service

    from app.services import runtime_flags

    ctx = _ctx(
        {
            "active": "status",
            "inbox": inbox_service.health_and_attention(limit=50),
            "stats": dash_service.stats(),
            "feed": dash_service.recent_activity(limit=15),
            "maintenance": runtime_flags.maintenance_mode_details(),
        }
    )
    # Complete the "Needs attention" derivation with the per-request
    # unregistered-auth count (which `stats()` cannot see).
    ctx["attention_items"] = dash_service.derive_attention_items(
        ctx["stats"],
        unregistered_active=ctx.get("unregistered_active", 0),
    )
    return render_template("status.html", **ctx)


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


# ── v0.4.22 (Tier-2 E): Status-inbox attention ack/snooze ──────────


@admin_ui_bp.post("/attention/<path:attention_id>/ack")
@role_required_ui(*ADMIN_AND_UP)
def ack_attention_submit(attention_id: str):
    from app.services import attention_acks

    snooze_raw = (request.form.get("snooze_seconds") or "").strip()
    snooze = int(snooze_raw) if snooze_raw.isdigit() else None
    reason = (request.form.get("reason") or "").strip() or None
    result = attention_acks.ack(
        attention_id,
        by_user_id=g.current_user.id,
        snooze_seconds=snooze,
        reason=reason,
    )
    audit_service.record(
        "attention.acked",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="attention",
        target_id=attention_id,
        details={"snooze_seconds": snooze, "reason": reason, "ack_id": result["id"]},
    )
    flash(
        "Acknowledged. " + (
            f"Will re-surface after {snooze} s."
            if snooze else
            "Will stay hidden until manually cleared (or the device's underlying state changes)."
        ),
        "info",
    )
    return redirect(url_for("admin_ui.index"))


@admin_ui_bp.post("/attention/<path:attention_id>/unack")
@role_required_ui(*ADMIN_AND_UP)
def unack_attention_submit(attention_id: str):
    from app.services import attention_acks

    if attention_acks.unack(attention_id):
        audit_service.record(
            "attention.unacked",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="attention",
            target_id=attention_id,
            details={},
        )
        flash("Acknowledgement cleared. Item will re-surface on the next page load.", "info")
    return redirect(url_for("admin_ui.index"))


@admin_api_bp.post("/attention/<path:attention_id>/ack")
@role_required_api(*ADMIN_AND_UP)
def ack_attention_api(attention_id: str):
    from app.services import attention_acks

    body = request.get_json(silent=True) or {}
    snooze = body.get("snooze_seconds")
    reason = body.get("reason")
    try:
        snooze = int(snooze) if snooze is not None else None
    except (TypeError, ValueError):
        snooze = None
    result = attention_acks.ack(
        attention_id,
        by_user_id=g.current_user.id,
        snooze_seconds=snooze,
        reason=reason,
    )
    audit_service.record(
        "attention.acked",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="attention",
        target_id=attention_id,
        details={"snooze_seconds": snooze, "reason": reason, "via": "api"},
    )
    return ok(result)


@admin_api_bp.delete("/attention/<path:attention_id>/ack")
@role_required_api(*ADMIN_AND_UP)
def unack_attention_api(attention_id: str):
    from app.services import attention_acks

    if not attention_acks.unack(attention_id):
        return err("not_found", "No active ack for this attention id.", status=404)
    audit_service.record(
        "attention.unacked",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="attention",
        target_id=attention_id,
        details={"via": "api"},
    )
    return ok({"unacked": True})
