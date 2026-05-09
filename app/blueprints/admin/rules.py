"""Rules — watchdog rules + schedules + notification rules.

v0.3.0 P1 ships only the route + an empty-state page; the data
model and rule-builder UX land in P4 of the redesign plan
(`docs/webui-redesign-plan.md` §9).

The route exists in P1 so the new top-nav has a real destination
and the layout's `url_for("admin_ui.rules_page")` resolves.
"""

from __future__ import annotations

from flask import render_template

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_ui


@admin_ui_bp.get("/rules")
@admin_required_ui
def rules_page():
    return render_template(
        "rules/index.html",
        **_ctx({"active": "rules"}),
    )
