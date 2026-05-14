"""Fleet power view — v0.5.27 (B16 Phase 1B).

Operator-visible surface over `services.device_power.fleet_summary()`,
which shipped in v0.5.26 (B16 Phase 1A). One read-only page; rollups
+ charts + cost calc + alerting are Phase 1C+ (later ships).
"""

from __future__ import annotations

from flask import render_template, request

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_ui
from app.services import device_power


WINDOW_PRESETS = (
    ("24h", 24 * 60 * 60),
    ("7d", 7 * 24 * 60 * 60),
    ("30d", 30 * 24 * 60 * 60),
)


@admin_ui_bp.get("/power")
@admin_required_ui
def fleet_power_page():
    """Fleet power dashboard — biggest-hogs-first per-device summary
    over a configurable window (24h / 7d / 30d).

    `?window=24h|7d|30d` query param; defaults to 24h. Unknown values
    fall back to 24h rather than erroring so a stale bookmark doesn't 500.
    """
    requested = (request.args.get("window") or "24h").strip()
    chosen_key = "24h"
    chosen_seconds = 24 * 60 * 60
    for key, seconds in WINDOW_PRESETS:
        if requested == key:
            chosen_key = key
            chosen_seconds = seconds
            break

    summary = device_power.fleet_summary(window_seconds=chosen_seconds)
    # v0.5.29 (B16 Phase 1C): daily rollups for the fleet timeseries
    # chart. 30 days so the chart shows a meaningful month-over-month
    # trend regardless of the window-selector choice above.
    fleet_rollups = device_power.fleet_daily_rollups(days=30)
    return render_template(
        "power.html",
        **_ctx({
            "active": "power",
            "summary": summary,
            "window_key": chosen_key,
            "window_presets": WINDOW_PRESETS,
            "fleet_rollups": fleet_rollups,
        }),
    )
