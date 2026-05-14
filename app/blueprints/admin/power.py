"""Fleet power view — v0.5.27 (B16 Phase 1B) + cost calc + CSV (v0.5.30).

Operator-visible surface over `services.device_power.fleet_summary()`
shipped in v0.5.26 (Phase 1A). Charts + rollups shipped v0.5.29
(Phase 1C). Cost calc + CSV export landed v0.5.30.
"""

from __future__ import annotations

import csv
import io

from flask import Response, flash, g, redirect, render_template, request, url_for

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_ui
from app.services import audit as audit_service
from app.services import device_power
from app.services import runtime_settings


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


# v0.5.30: $/kWh setter — landing on /app/power keeps the cost-calc
# UX self-contained (operator sees cost rendered + the form sits in
# the same page). Could move to Settings → System later if it grows.

@admin_ui_bp.post("/power/rate")
@admin_required_ui
def fleet_power_rate_submit():
    raw_rate = (request.form.get("rate_per_kwh") or "").strip()
    currency = (request.form.get("currency") or device_power.DEFAULT_CURRENCY).strip()[:8]
    if not raw_rate:
        # Clear → revert to "no rate set" + cost rendering hides.
        if runtime_settings.has_db_value(device_power.RATE_PER_KWH_KEY):
            runtime_settings.delete(device_power.RATE_PER_KWH_KEY)
        flash("Cleared rate-per-kWh. Cost rendering hidden.", "info")
        audit_service.record(
            "power.rate_per_kwh_cleared",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="runtime_settings",
            target_id=device_power.RATE_PER_KWH_KEY,
        )
        return redirect(url_for("admin_ui.fleet_power_page"))
    try:
        rate = float(raw_rate)
    except ValueError:
        flash("Rate must be a number (e.g. 0.15 for 15¢/kWh).", "error")
        return redirect(url_for("admin_ui.fleet_power_page"))
    if rate < 0 or rate > 10:
        flash("Rate must be between 0 and 10 (USD/kWh-equivalent).", "error")
        return redirect(url_for("admin_ui.fleet_power_page"))
    runtime_settings.set_(
        device_power.RATE_PER_KWH_KEY, str(rate), user_id=g.current_user.id
    )
    if currency and currency != device_power.DEFAULT_CURRENCY:
        runtime_settings.set_(
            device_power.CURRENCY_KEY, currency, user_id=g.current_user.id
        )
    flash(f"Set rate to {rate} {currency}/kWh.", "info")
    audit_service.record(
        "power.rate_per_kwh_set",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="runtime_settings",
        target_id=device_power.RATE_PER_KWH_KEY,
        details={"rate_per_kwh": rate, "currency": currency},
    )
    return redirect(url_for("admin_ui.fleet_power_page"))


# v0.5.30: CSV export of per-device aggregates for the selected window.

@admin_ui_bp.get("/power/export.csv")
@admin_required_ui
def fleet_power_export_csv():
    requested = (request.args.get("window") or "24h").strip()
    chosen_key = "24h"
    chosen_seconds = 24 * 60 * 60
    for key, seconds in WINDOW_PRESETS:
        if requested == key:
            chosen_key = key
            chosen_seconds = seconds
            break

    summary = device_power.fleet_summary(window_seconds=chosen_seconds)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "device_id", "display_name", "sample_count",
        "avg_w", "min_w", "max_w",
        "kwh_window", "cost_window", "currency",
        "first_sample_at", "last_sample_at",
    ])
    for d in summary["per_device"]:
        w.writerow([
            d["device_id"],
            d["display_name"],
            d["sample_count"],
            d.get("avg_w"),
            d.get("min_w"),
            d.get("max_w"),
            d.get("kwh_window"),
            d.get("cost_window"),
            summary.get("currency") or "",
            d.get("first_sample_at") or "",
            d.get("last_sample_at") or "",
        ])

    audit_service.record(
        "power.csv_exported",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="power",
        target_id=None,
        details={"window": chosen_key, "row_count": len(summary["per_device"])},
    )

    filename = f"rebooter-power-{chosen_key}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
