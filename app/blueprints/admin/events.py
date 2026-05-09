"""Admin UI + API for the device-events query surface."""

from __future__ import annotations

from flask import render_template, request

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_api, admin_required_ui
from app.middleware.response import ok
from app.services.events import query_events


# ── UI ─────────────────────────────────────────────────────────────────────

@admin_ui_bp.get("/events")
@admin_required_ui
def events_page():
    rows = query_events(
        device_id=request.args.get("device_id") or None,
        type_=request.args.get("type") or None,
        from_ts=request.args.get("from") or None,
        to_ts=request.args.get("to") or None,
        limit=int(request.args.get("limit") or 100),
    )
    return render_template(
        "events.html",
        **_ctx(
            {
                "events": rows,
                "filters": {
                    "device_id": request.args.get("device_id", ""),
                    "type": request.args.get("type", ""),
                    "from": request.args.get("from", ""),
                    "to": request.args.get("to", ""),
                },
            }
        ),
    )


# ── API ────────────────────────────────────────────────────────────────────

@admin_api_bp.get("/events")
@admin_required_api
def query_events_api():
    rows = query_events(
        device_id=request.args.get("device_id"),
        group_id=request.args.get("group_id"),
        type_=request.args.get("type"),
        from_ts=request.args.get("from"),
        to_ts=request.args.get("to"),
        limit=int(request.args.get("limit") or 200),
    )
    return ok({"events": rows, "count": len(rows)})
