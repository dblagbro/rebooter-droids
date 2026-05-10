"""History — unified log feed.

v0.3.0 P1 implementation: renders the existing audit-event data
under the new `/app/history` URL. P6 of the redesign plan extends
this into the unified view spanning audit + watchdog probe events
+ power events + schedule fires + notification sends.

`/app/audit` continues to serve its current (audit-only) page for
URL stability; in P6 it becomes a redirect to /app/history.
"""

from __future__ import annotations

from flask import render_template, request

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import role_required_ui
from app.models.users import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.services import audit as audit_service


@admin_ui_bp.get("/history")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def history_page():
    action_prefix = request.args.get("action_prefix") or None
    rows = audit_service.query(
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
                "source": request.args.get("source", "audit"),
                "action_prefix": action_prefix,
            }
        ),
    )
