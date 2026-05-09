"""GET /app/ — admin dashboard with stat cards + activity feed."""

from __future__ import annotations

from flask import render_template

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_ui


@admin_ui_bp.get("/")
@admin_required_ui
def index():
    """v0.3.1 (P2): Status page replaces the v0.2.x stat-grid
    dashboard. The new shape answers "does anything need attention?"
    first, with totals + activity feed available below the fold."""
    from app.services import dashboard as dash_service
    from app.services import inbox as inbox_service

    return render_template(
        "status.html",
        **_ctx(
            {
                "active": "status",
                "inbox": inbox_service.health_and_attention(limit=50),
                "stats": dash_service.stats(),
                "feed": dash_service.recent_activity(limit=15),
            }
        ),
    )
