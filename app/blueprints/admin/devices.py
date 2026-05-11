"""Devices blueprint — moved to ``devices_ui`` and ``devices_api`` in v0.5.5.

The original 630-line file mixed UI handlers (under ``admin_ui_bp``) and
JSON-API handlers (under ``admin_api_bp``). It was the heaviest blueprint
in the project and growing every time the devices list got a new feature
(upgrade button, bulk-delete, mass-action confirm, etc.).

v0.5.5 split it into:

  - ``app/blueprints/admin/devices_ui.py``   — UI handlers (~395 lines)
  - ``app/blueprints/admin/devices_api.py``  — API handlers (~210 lines)

This file remains as a back-compat shim so any external import of
``app.blueprints.admin.devices`` (e.g. test introspection) still works
and the side-effect route registration happens via the two new modules.
"""

from __future__ import annotations

# Side-effect imports — each module registers routes against the shared
# admin_ui_bp / admin_api_bp blueprint objects on import.
from app.blueprints.admin import devices_ui  # noqa: F401
from app.blueprints.admin import devices_api  # noqa: F401
