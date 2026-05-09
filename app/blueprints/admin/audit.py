"""Admin UI + API for the audit log."""

from __future__ import annotations

from flask import render_template, request

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import ADMIN_AND_UP, role_required_api, role_required_ui
from app.middleware.response import ok
from app.models.users import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.services import audit as audit_service


# ── UI ─────────────────────────────────────────────────────────────────────

@admin_ui_bp.get("/audit")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def audit_page():
    rows = audit_service.query(
        actor_user_id=request.args.get("actor_user_id") or None,
        action=request.args.get("action") or None,
        target_type=request.args.get("target_type") or None,
        limit=int(request.args.get("limit") or 200),
    )
    return render_template("audit_list.html", **_ctx({"events": rows}))


# ── API ────────────────────────────────────────────────────────────────────

@admin_api_bp.get("/audit")
@role_required_api(*ADMIN_AND_UP)
def audit_api():
    rows = audit_service.query(
        actor_user_id=request.args.get("actor_user_id"),
        action=request.args.get("action"),
        target_type=request.args.get("target_type"),
        target_id=request.args.get("target_id"),
        limit=int(request.args.get("limit") or 200),
    )
    return ok({"events": rows, "count": len(rows)})
