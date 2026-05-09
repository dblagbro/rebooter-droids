"""GET /app/ — admin dashboard with stat cards + activity feed."""

from __future__ import annotations

from flask import render_template

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_ui


@admin_ui_bp.get("/")
@admin_required_ui
def index():
    from app.services import dashboard as dash_service

    return render_template(
        "dashboard.html",
        **_ctx(
            {
                "active": "status",
                "stats": dash_service.stats(),
                "feed": dash_service.recent_activity(limit=25),
            }
        ),
    )
