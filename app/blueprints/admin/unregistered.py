"""Admin UI + API for the unregistered-auth-attempt tracker (v0.2.5).

Surfaces firmware that's hitting `/api/v1/device/*` with a stale or
unrecognised device token — most often a registration loop after a
re-enrollment, or a typo in the device's `central_base_url`.
"""

from __future__ import annotations

from flask import render_template, request

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_api, admin_required_ui
from app.middleware.response import err, ok


# ── UI ─────────────────────────────────────────────────────────────────────

@admin_ui_bp.get("/unregistered-devices")
@admin_required_ui
def unregistered_devices_page():
    from app.services import unregistered as unreg_service

    since_minutes = int(request.args.get("since_minutes") or 0) or None
    rows = unreg_service.list_recent(limit=200, since_minutes=since_minutes)
    return render_template(
        "unregistered_devices.html",
        **_ctx({"rows": rows, "since_minutes": since_minutes}),
    )


# ── API ────────────────────────────────────────────────────────────────────

@admin_api_bp.get("/unregistered-devices")
@admin_required_api
def list_unregistered_attempts_api():
    from app.services import unregistered as unreg_service

    since_minutes_raw = request.args.get("since_minutes")
    since_minutes: int | None = None
    if since_minutes_raw:
        try:
            since_minutes = int(since_minutes_raw)
        except ValueError:
            return err("validation_failed", "since_minutes must be an integer", status=400)
    limit_raw = request.args.get("limit")
    try:
        limit = int(limit_raw) if limit_raw else 200
    except ValueError:
        return err("validation_failed", "limit must be an integer", status=400)
    rows = unreg_service.list_recent(limit=limit, since_minutes=since_minutes)
    return ok(
        {
            "attempts": rows,
            "active_60min": unreg_service.count_active(since_minutes=60),
        }
    )
