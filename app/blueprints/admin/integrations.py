"""Settings → Integrations tab + management endpoints (v0.5.17 / B17).

Today supports `kind=roku` (Roku ECP poll at port 8060). Architectural
shape extends to Home-Assistant / MQTT / Plex / weather / calendar etc.
without further blueprint changes — only `services.external_sensors`
gets new `kind` branches.

Endpoint names follow the admin pattern: every URL keeps stable across
refactors so templates that use `url_for(...)` survive.
"""

from __future__ import annotations

from flask import abort, flash, g, redirect, render_template, request, url_for

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_ui
from app.services import audit as audit_service
from app.services import external_sensors as ext_svc


@admin_ui_bp.get("/settings/integrations")
@admin_required_ui
def settings_integrations_page():
    sources = ext_svc.list_sources()
    return render_template(
        "settings/integrations.html",
        **_ctx({
            "active": "settings",
            "settings_tab": "integrations",
            "sources": sources,
        }),
    )


@admin_ui_bp.post("/settings/integrations/add")
@admin_required_ui
def settings_integrations_add_submit():
    kind = (request.form.get("kind") or "roku").strip()
    display_name = (request.form.get("display_name") or "").strip()
    host = (request.form.get("host") or "").strip()
    port_raw = (request.form.get("port") or "").strip() or None
    interval_raw = (request.form.get("poll_interval_seconds") or "30").strip()
    # v0.5.23 (B17 adjacent): per-kind config bag.
    config: dict = {}
    if kind == "home_assistant":
        config["token"] = (request.form.get("ha_token") or "").strip()
        if (request.form.get("ha_verify_ssl") or "").strip() in ("0", "false", "off"):
            config["verify_ssl"] = False
    elif kind == "weather":
        try:
            config["lat"] = float(request.form.get("weather_lat") or 0)
            config["lng"] = float(request.form.get("weather_lng") or 0)
        except ValueError:
            flash("Weather lat/lng must be decimal numbers (e.g. 38.9 -77.0).", "error")
            return redirect(url_for("admin_ui.settings_integrations_page"))
    elif kind == "ical":
        config["url"] = (request.form.get("ical_url") or "").strip()
    try:
        out = ext_svc.create_source(
            kind=kind,
            display_name=display_name,
            host=host,
            port=int(port_raw) if port_raw else None,
            poll_interval_seconds=int(interval_raw or "30"),
            config=config,
        )
    except ValueError as e:
        flash(f"Could not add integration: {e}", "error")
        return redirect(url_for("admin_ui.settings_integrations_page"))
    audit_service.record(
        "external_sensor.source_created",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="external_sensor_source",
        target_id=out["id"],
        details={
            "kind": out["kind"],
            "display_name": out["display_name"],
            "host": out["host"],
            "port": out["port"],
        },
    )
    flash(f"Added {out['kind']} source “{out['display_name']}”.", "info")
    return redirect(url_for("admin_ui.settings_integrations_page"))


@admin_ui_bp.post("/settings/integrations/<source_id>/delete")
@admin_required_ui
def settings_integrations_delete_submit(source_id: str):
    if not ext_svc.delete_source(source_id):
        abort(404)
    audit_service.record(
        "external_sensor.source_deleted",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="external_sensor_source",
        target_id=source_id,
    )
    flash("Integration deleted.", "info")
    return redirect(url_for("admin_ui.settings_integrations_page"))


@admin_ui_bp.post("/settings/integrations/<source_id>/probe")
@admin_required_ui
def settings_integrations_probe_submit(source_id: str):
    """Manual one-shot poll for testing. Renders the result on the
    same page via flash."""
    result = ext_svc.poll_source(source_id)
    if "error" in result:
        flash(f"Probe failed: {result['error']}", "error")
    else:
        payload = result.get("payload") or {}
        app_now = payload.get("active_app") or "—"
        ss = payload.get("screensaver_active")
        flash(
            f"Probe OK at {result['sampled_at']}: active app = {app_now!r}"
            f"{' (screensaver active)' if ss else ''}.",
            "info",
        )
    audit_service.record(
        "external_sensor.probed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="external_sensor_source",
        target_id=source_id,
        details={"ok": "error" not in result},
    )
    return redirect(url_for("admin_ui.settings_integrations_page"))


@admin_ui_bp.post("/settings/integrations/<source_id>/toggle")
@admin_required_ui
def settings_integrations_toggle_submit(source_id: str):
    """Flip enabled flag — disabled sources still keep their config +
    sample history but the poller skips them."""
    desired_enabled = (request.form.get("enabled") or "").strip() == "1"
    if not ext_svc.set_enabled(source_id, desired_enabled):
        abort(404)
    audit_service.record(
        "external_sensor.toggled",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="external_sensor_source",
        target_id=source_id,
        details={"enabled": desired_enabled},
    )
    flash(
        f"Integration {'enabled' if desired_enabled else 'disabled'}.",
        "info",
    )
    return redirect(url_for("admin_ui.settings_integrations_page"))
