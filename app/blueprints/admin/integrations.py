"""Settings → Integrations tab + management endpoints (v0.5.17 / B17).

Today supports `kind=roku` (Roku ECP poll at port 8060). Architectural
shape extends to Home-Assistant / MQTT / Plex / weather / calendar etc.
without further blueprint changes — only `services.external_sensors`
gets new `kind` branches.

Endpoint names follow the admin pattern: every URL keeps stable across
refactors so templates that use `url_for(...)` survive.
"""

from __future__ import annotations

from flask import (
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_ui
from app.services import audit as audit_service
from app.services import external_sensors as ext_svc


@admin_ui_bp.get("/settings/integrations")
@admin_required_ui
def settings_integrations_page():
    sources = ext_svc.list_sources()
    # v0.5.64 (B17 Layer 2): EPG is a global schedule cache, not a
    # per-operator source row — surface its status separately.
    from app.services import epg as epg_svc

    epg_status = epg_svc.epg_status()
    # v0.5.94 (B17): Google Calendar OAuth — surface whether the
    # operator has set the Google Cloud OAuth client credentials, so
    # the template shows "Connect" vs a setup hint.
    from app.services import google_oauth

    return render_template(
        "settings/integrations.html",
        **_ctx({
            "active": "settings",
            "settings_tab": "integrations",
            "sources": sources,
            "epg_status": epg_status,
            "google_oauth_configured": google_oauth.is_configured(),
        }),
    )


# ── Google Calendar OAuth (v0.5.94 / B17) ──────────────────────────────

@admin_ui_bp.get("/settings/integrations/google/connect")
@admin_required_ui
def google_calendar_connect():
    """Kick off the Google Calendar OAuth consent flow. Stores a CSRF
    `state` (and the operator-chosen source name) in the session, then
    redirects to Google's consent screen."""
    import secrets

    from app.services import google_oauth

    if not google_oauth.is_configured():
        flash(
            "Set the Google OAuth client credentials first "
            "(REBOOTER_GOOGLE_OAUTH_CLIENT_ID / _SECRET).",
            "error",
        )
        return redirect(url_for("admin_ui.settings_integrations_page"))

    state = secrets.token_urlsafe(24)
    session["google_oauth_state"] = state
    session["google_oauth_name"] = (
        (request.args.get("name") or "Google Calendar").strip()[:120]
        or "Google Calendar"
    )
    redirect_uri = url_for("admin_ui.google_calendar_callback", _external=True)
    try:
        consent_url = google_oauth.build_consent_url(redirect_uri, state)
    except google_oauth.GoogleOAuthError as e:
        flash(f"Could not start Google sign-in: {e.message}", "error")
        return redirect(url_for("admin_ui.settings_integrations_page"))
    return redirect(consent_url)


@admin_ui_bp.get("/settings/integrations/google/callback")
@admin_required_ui
def google_calendar_callback():
    """Google redirects here after consent. Verifies the CSRF `state`,
    exchanges the code for tokens, and creates the `google_calendar`
    external-sensor source."""
    from app.services import google_oauth

    page = url_for("admin_ui.settings_integrations_page")
    expected_state = session.pop("google_oauth_state", None)
    source_name = session.pop("google_oauth_name", None) or "Google Calendar"

    error = request.args.get("error")
    if error:
        flash(f"Google sign-in was cancelled or failed: {error}", "error")
        return redirect(page)
    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state or state != expected_state:
        flash("Google sign-in could not be verified — please retry.", "error")
        return redirect(page)

    redirect_uri = url_for("admin_ui.google_calendar_callback", _external=True)
    try:
        tok = google_oauth.exchange_code(code, redirect_uri)
    except google_oauth.GoogleOAuthError as e:
        flash(f"Google sign-in failed: {e.message}", "error")
        return redirect(page)

    try:
        source = ext_svc.create_source(
            kind="google_calendar",
            display_name=source_name,
            config={
                "refresh_token": tok["refresh_token"],
                "calendar_id": "primary",
                "access_token": tok.get("access_token") or "",
            },
        )
    except ValueError as e:
        flash(f"Could not save the calendar source: {e}", "error")
        return redirect(page)
    audit_service.record(
        "external_sensor.created",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="external_sensor_source",
        target_id=source["id"],
        details={"kind": "google_calendar", "via": "oauth"},
    )
    flash(f"Connected Google Calendar: {source_name}.", "info")
    return redirect(page)


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
    elif kind == "solaredge":
        config["site_id"] = (request.form.get("solaredge_site_id") or "").strip()
        config["api_key"] = (request.form.get("solaredge_api_key") or "").strip()
    elif kind == "enphase_envoy":
        jwt = (request.form.get("envoy_jwt") or "").strip()
        if jwt:
            config["jwt"] = jwt
    elif kind == "snmp":
        version = (request.form.get("snmp_version") or "2c").strip()
        config["version"] = version
        if version == "3":
            config["v3"] = {
                "user": (request.form.get("snmp_v3_user") or "").strip(),
                "auth_proto": (request.form.get("snmp_v3_auth_proto") or "SHA").strip(),
                "auth_key": (request.form.get("snmp_v3_auth_key") or "").strip(),
                "priv_proto": (request.form.get("snmp_v3_priv_proto") or "AES").strip(),
                "priv_key": (request.form.get("snmp_v3_priv_key") or "").strip(),
            }
        else:
            config["community"] = (request.form.get("snmp_community") or "").strip()
        iface_raw = (request.form.get("snmp_interface_filter") or "").strip()
        if iface_raw:
            config["interface_filter"] = [
                s.strip() for s in iface_raw.split(",") if s.strip()
            ]
    elif kind in ("plex", "jellyfin", "ios_shortcut"):
        # v0.5.61 (B17 Ship 2): inbound-webhook kinds. webhook_secret is
        # auto-minted in the service layer; server_name is optional.
        sn = (request.form.get("webhook_server_name") or "").strip()
        if sn:
            config["server_name"] = sn
    elif kind == "mqtt":
        # v0.5.63 (B17 Ship 3): MQTT subscriber. Topics one-per-line.
        topics_raw = (request.form.get("mqtt_topics") or "").strip()
        config["topics"] = [
            t.strip() for t in topics_raw.splitlines() if t.strip()
        ]
        u = (request.form.get("mqtt_username") or "").strip()
        p = request.form.get("mqtt_password") or ""
        if u:
            config["username"] = u
        if p:
            config["password"] = p
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


@admin_ui_bp.get("/settings/integrations/<source_id>/entities")
@admin_required_ui
def settings_integrations_ha_entities_page(source_id: str):
    """v0.5.57 (P2.4): Home Assistant entity browser — lists the
    entities cached from the source's most-recent poll so the operator
    can discover `entity_id`s for `ha_state_is` / `ha_numeric_*` rules.
    Optional `?q=` substring filter on entity_id / friendly_name.
    """
    data = ext_svc.ha_entities(source_id)
    if data is None:
        flash("Entity browser is only available for Home Assistant sources.", "error")
        return redirect(url_for("admin_ui.settings_integrations_page"))
    q = (request.args.get("q") or "").strip().lower()
    if q:
        data["entities"] = [
            e for e in data["entities"]
            if q in (e.get("entity_id") or "").lower()
            or q in (e.get("friendly_name") or "").lower()
        ]
    return render_template(
        "settings/integrations_ha_entities.html",
        **_ctx({
            "active": "settings",
            "settings_tab": "integrations",
            "ha": data,
            "query": q,
        }),
    )
