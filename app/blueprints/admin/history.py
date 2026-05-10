"""History — unified log feed.

v0.3.0 P1: rendered audit-event data under the new ``/app/history`` URL.
v0.4.27: added action_prefix chip filters across the audit slice.
v0.4.30 (C1): extended with multi-source support — pass
``?source=watchdog_probe`` / ``?source=device_event`` / ``?source=all``
to surface non-audit event streams. Defaults to ``source=audit`` for
back-compat with anyone who already bookmarked a filter URL.

``/app/audit`` continues to serve its current (audit-only) page for
URL stability; in P6 it becomes a redirect to /app/history.
"""

from __future__ import annotations

from flask import render_template, request

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import role_required_ui
from app.models.users import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.services import history as history_service


_ALLOWED_SOURCES = ("audit", "watchdog_probe", "device_event", "all")


@admin_ui_bp.get("/history")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def history_page():
    source = (request.args.get("source") or "audit").lower()
    if source not in _ALLOWED_SOURCES:
        source = "audit"
    action_prefix = request.args.get("action_prefix") or None
    rows = history_service.query_unified(
        source=source,
        actor_user_id=request.args.get("actor_user_id") or None,
        action=request.args.get("action") or None,
        action_prefix=action_prefix,
        target_type=request.args.get("target_type") or None,
        target_id=request.args.get("target_id") or None,
        limit=int(request.args.get("limit") or 200),
    )
    return render_template(
        "history/index.html",
        **_ctx(
            {
                "active": "history",
                "events": rows,
                "source": source,
                "action_prefix": action_prefix,
            }
        ),
    )
