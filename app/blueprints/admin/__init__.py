"""Admin blueprints — both the JSON API at ``/api/v1/admin/*`` and the
HTML UI at ``/app/*``.

Each feature lives in its own submodule that decorates routes against the
two ``Blueprint`` objects defined here. The submodule imports at the
bottom of this file are what cause those decorators to run when the
package is loaded, so the route table is populated before
``app.register_blueprint`` is called.

Endpoint names (``admin_api.<feature>`` / ``admin_ui.<feature>``) are
preserved across the v0.2.5 → v0.2.6 split so every existing
``url_for("admin_ui.list_devices_page")`` (etc.) call in the templates
continues to resolve.
"""

from __future__ import annotations

from flask import Blueprint

admin_api_bp = Blueprint("admin_api", __name__)
admin_ui_bp = Blueprint("admin_ui", __name__)


# Side-effect imports: each submodule attaches routes to the two blueprints
# above when imported. Keep at the bottom of this file to avoid circulars.
from app.blueprints.admin import (  # noqa: E402,F401
    auth_ui,
    dashboard,
    profile,
    public_invite,
    devices,
    enrollment_tokens,
    groups,
    sites,
    firmware,
    users,
    invitations,
    audit,
    unregistered,
    events,
    rules,           # v0.3.0 P1
    history,         # v0.3.0 P1
    settings,        # v0.3.0 P1
    schedules,       # v0.4.8 (B8)
    pending_adoption, # v0.4.20
    integrations,    # v0.5.17 (B17 Layer 1 — Roku ECP)
    power,           # v0.5.27 (B16 Phase 1B — fleet /app/power)
    power_api,       # v0.5.54 (P1.1 — JSON power query API)
    signup_requests, # v0.5.39 (P4b — signup request system)
    scenes,          # v0.5.92 (Stage C — named device scenes)
    webhooks,        # Tier-2 Feature 6 — outbound notifications/webhooks
    backup,          # Hub Tier-2 Feature 3 — config backup / restore
    api_tokens,      # Hub Tier-2 Feature 4a — scoped API tokens
    setup,           # Tier-2 Feature 1 — first-run wizard + 3-mode picker
)
