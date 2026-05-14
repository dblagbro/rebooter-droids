"""Rules — watchdog rules (v0.4.0 first slice).

v0.4.0 ships data-model + CRUD + plain-English render. The probe
runtime that actually executes rules and writes
watchdog_probe_events is queued for v0.4.1+.

UI: list at /app/rules, create form, per-row enable/disable +
delete actions. No edit page yet (delete-and-recreate is the
flow); advanced JSON editor + per-rule event log + probe-now /
simulate buttons land in subsequent slices.
"""

from __future__ import annotations

from flask import abort, flash, g, redirect, render_template, request, url_for

from app.blueprints.admin import admin_ui_bp, admin_api_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import (
    ADMIN_AND_UP,
    WRITE_ROLES,
    admin_required_api,
    admin_required_ui,
    role_required_api,
    role_required_ui,
)
from app.middleware.response import err, ok
from app.models.users import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.services import audit as audit_service
from app.services.devices import list_devices as svc_list_devices
from app.services.groups import list_groups as svc_list_groups
from app.services.watchdog import (
    WatchdogValidationError,
    create_rule as svc_create_rule,
    delete_rule as svc_delete_rule,
    get_rule as svc_get_rule,
    list_rules as svc_list_rules,
    list_recent_events as svc_list_events,
    probe_now as svc_probe_now,
    set_enabled as svc_set_enabled,
    update_rule as svc_update_rule,
)


# ── UI ─────────────────────────────────────────────────────────────────────

@admin_ui_bp.get("/rules")
@admin_required_ui
def rules_page():
    rules = svc_list_rules()
    devices = svc_list_devices(include_qa_fixtures=False)
    groups = svc_list_groups()
    # v0.4.2: attach the latest 10 events per rule for the inline log.
    rules_with_events = []
    for r in rules:
        r["recent_events"] = svc_list_events(r["id"], limit=10)
        rules_with_events.append(r)
    # v0.5.28 (Phase 2B): kind-filtered source pickers for the
    # integration probes. Lazy import keeps the rules page's import
    # graph small.
    from app.services import external_sensors as ext_svc

    all_sources = ext_svc.list_sources()
    sources_by_kind = {
        "roku": [s for s in all_sources if s["kind"] == "roku"],
        "home_assistant": [s for s in all_sources if s["kind"] == "home_assistant"],
        "weather": [s for s in all_sources if s["kind"] == "weather"],
        "ical": [s for s in all_sources if s["kind"] == "ical"],
    }
    return render_template(
        "rules/index.html",
        **_ctx({
            "active": "rules",
            "rules": rules_with_events,
            "devices": devices,
            "groups": groups,
            "sources_by_kind": sources_by_kind,
        }),
    )


@admin_ui_bp.post("/rules")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def rules_create_submit():
    name = (request.form.get("name") or "").strip()
    probe_kind = (request.form.get("probe_kind") or "").strip()
    probe_arg = (request.form.get("probe_arg") or "").strip()
    target_kind = (request.form.get("target_kind") or "").strip()
    target_id = (request.form.get("target_id") or "").strip()
    action_kind = (request.form.get("action_kind") or "cycle").strip()

    # Build the probe JSON shape from the form's two-input pattern.
    probe: dict
    if probe_kind == "internet":
        # v0.5.9: form posts repeated `internet_target_host[]` +
        # `internet_target_port[]` pairs. Zip and filter to drop the
        # empty placeholder row the UI keeps for "add another".
        hosts = request.form.getlist("internet_target_host[]")
        ports = request.form.getlist("internet_target_port[]")
        targets: list[dict] = []
        for h, p in zip(hosts, ports):
            host = (h or "").strip()
            port_s = (p or "").strip()
            if not host and not port_s:
                continue
            try:
                port_i = int(port_s) if port_s else 0
            except ValueError:
                port_i = 0
            targets.append({"host": host, "port": port_i})
        probe = {"kind": "internet"}
        if targets:
            probe["targets"] = targets
    elif probe_kind == "ping":
        probe = {"kind": "ping", "host": probe_arg}
    elif probe_kind == "tcp":
        host, _, port = probe_arg.partition(":")
        probe = {"kind": "tcp", "host": host, "port": int(port or 0)}
    elif probe_kind == "http":
        probe = {"kind": "http", "url": probe_arg}
    elif probe_kind == "dns":
        probe = {"kind": "dns", "hostname": probe_arg}
    elif probe_kind == "gateway":
        probe = {"kind": "gateway"}
    # v0.5.28 (Phase 2B): per-kind form fields for the four integration
    # probe kinds shipped in v0.5.17 + v0.5.23. Operators no longer
    # need the JSON editor for the common cases — JSON editor stays as
    # an escape hatch for advanced shapes.
    elif probe_kind == "roku_app_active":
        probe = {
            "kind": "roku_app_active",
            "source_id": (request.form.get("roku_source_id") or "").strip(),
            "app_name": (request.form.get("roku_app_name") or "").strip(),
        }
        try:
            max_age = int(request.form.get("roku_max_sample_age_seconds") or 120)
            probe["max_sample_age_seconds"] = max_age
        except ValueError:
            pass
    elif probe_kind == "ha_state_is":
        probe = {
            "kind": "ha_state_is",
            "source_id": (request.form.get("ha_source_id") or "").strip(),
            "entity_id": (request.form.get("ha_entity_id") or "").strip(),
            "expected_state": (request.form.get("ha_expected_state") or "").strip(),
        }
        try:
            max_age = int(request.form.get("ha_max_sample_age_seconds") or 60)
            probe["max_sample_age_seconds"] = max_age
        except ValueError:
            pass
    elif probe_kind == "weather_alert_active":
        probe = {
            "kind": "weather_alert_active",
            "source_id": (request.form.get("weather_source_id") or "").strip(),
        }
        ev = (request.form.get("weather_event_contains") or "").strip()
        if ev:
            probe["event_contains"] = ev
        sev = (request.form.get("weather_min_severity") or "").strip()
        if sev:
            probe["min_severity"] = sev
        try:
            max_age = int(request.form.get("weather_max_sample_age_seconds") or 600)
            probe["max_sample_age_seconds"] = max_age
        except ValueError:
            pass
    elif probe_kind == "ical_event_active":
        probe = {
            "kind": "ical_event_active",
            "source_id": (request.form.get("ical_source_id") or "").strip(),
        }
        summary = (request.form.get("ical_summary_contains") or "").strip()
        if summary:
            probe["summary_contains"] = summary
        try:
            max_age = int(request.form.get("ical_max_sample_age_seconds") or 1800)
            probe["max_sample_age_seconds"] = max_age
        except ValueError:
            pass
    # v0.5.32 (B16 Phase 1D): power-targeted probes.
    elif probe_kind in ("power_above", "power_below"):
        probe = {
            "kind": probe_kind,
            "device_id": (request.form.get("power_device_id") or "").strip(),
        }
        try:
            probe["threshold_w"] = float(request.form.get("power_threshold_w") or 0)
        except ValueError:
            flash("threshold_w must be a number (e.g. 1500 for 1500W).", "error")
            return redirect(url_for("admin_ui.rules_page"))
        try:
            probe["window_seconds"] = int(request.form.get("power_window_seconds") or 300)
        except ValueError:
            probe["window_seconds"] = 300
        try:
            mage = int(request.form.get("power_max_sample_age_seconds") or 600)
            probe["max_sample_age_seconds"] = mage
        except ValueError:
            pass
    elif probe_kind == "power_zero_while_on":
        probe = {
            "kind": "power_zero_while_on",
            "device_id": (request.form.get("power_device_id") or "").strip(),
        }
        try:
            probe["near_zero_threshold_w"] = float(
                request.form.get("power_near_zero_threshold_w") or 0.5
            )
        except ValueError:
            probe["near_zero_threshold_w"] = 0.5
        try:
            probe["window_seconds"] = int(request.form.get("power_window_seconds") or 300)
        except ValueError:
            probe["window_seconds"] = 300
        try:
            mage = int(request.form.get("power_max_sample_age_seconds") or 600)
            probe["max_sample_age_seconds"] = mage
        except ValueError:
            pass
    else:
        flash("Unsupported probe kind.", "error")
        return redirect(url_for("admin_ui.rules_page"))

    target: dict
    if target_kind in ("device", "group"):
        target = {"kind": target_kind, "id": target_id}
    elif target_kind == "tag":
        target = {"kind": "tag", "tag": target_id}
    else:
        flash("Pick a target.", "error")
        return redirect(url_for("admin_ui.rules_page"))

    action: dict
    if action_kind == "cycle":
        action = {
            "kind": "cycle",
            "power_off_seconds": int(request.form.get("power_off_seconds") or 5),
            "post_reboot_holdoff_seconds": int(
                request.form.get("post_reboot_holdoff_seconds") or 180
            ),
        }
    elif action_kind == "hold_off":
        action = {"kind": "hold_off"}
    elif action_kind == "notify_only":
        action = {"kind": "notify_only"}
    else:
        flash("Unsupported action.", "error")
        return redirect(url_for("admin_ui.rules_page"))

    # v0.4.7 (B7): per-rule maintenance window. Form provides
    # `maint_start` and `maint_end` as `datetime-local` (no timezone).
    # Treat as UTC since the operator is global.
    maint_windows: list[dict] = []
    maint_start = (request.form.get("maint_start") or "").strip()
    maint_end = (request.form.get("maint_end") or "").strip()
    if maint_start and maint_end:
        # `datetime-local` produces "YYYY-MM-DDTHH:MM"; tag UTC.
        maint_windows.append({
            "start": maint_start + ":00+00:00" if len(maint_start) == 16 else maint_start,
            "end": maint_end + ":00+00:00" if len(maint_end) == 16 else maint_end,
        })

    try:
        rule = svc_create_rule(
            name=name,
            probe=probe,
            target=target,
            action=action,
            failure_threshold=int(request.form.get("failure_threshold") or 3),
            recovery_threshold=int(request.form.get("recovery_threshold") or 2),
            window_seconds=int(request.form.get("window_seconds") or 60),
            cooldown_seconds=int(request.form.get("cooldown_seconds") or 300),
            maintenance_windows=maint_windows or None,
            created_by_user_id=g.current_user.id,
        )
    except WatchdogValidationError as e:
        flash(str(e), "error")
        return redirect(url_for("admin_ui.rules_page"))

    audit_service.record(
        "watchdog_rule.created",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="watchdog_rule",
        target_id=rule["id"],
        details={"name": name, "reason": "operator"},
    )
    flash(f"Rule created: {rule['sentence']}", "info")
    return redirect(url_for("admin_ui.rules_page"))


@admin_ui_bp.post("/rules/json")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def rules_create_json_submit():
    """v0.4.9 (B9): create a rule from a raw JSON body. Same shape
    as the API. Lets the operator express probe / target / escalation
    combinations the form-builder doesn't surface.

    v0.4.10 (BUG-031): on validation failure, re-render the rules
    page with the operator's JSON pre-filled. Pre-v0.4.10 a redirect
    nuked the textarea contents.
    """
    import json

    raw = (request.form.get("rule_json") or "").strip()

    def _err(msg: str):
        rules = svc_list_rules()
        rules_with_events = []
        for r in rules:
            r["recent_events"] = svc_list_events(r["id"], limit=10)
            rules_with_events.append(r)
        return render_template(
            "rules/index.html",
            **_ctx({
                "active": "rules",
                "rules": rules_with_events,
                "devices": svc_list_devices(include_qa_fixtures=False),
                "groups": svc_list_groups(),
                "json_editor_value": raw,
                "json_editor_error": msg,
            }),
        )

    if not raw:
        return _err("Paste a JSON body first.")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as e:
        return _err(f"JSON parse error: {e}")
    if not isinstance(body, dict):
        return _err("Top-level JSON must be an object.")

    try:
        rule = svc_create_rule(
            name=body.get("name", ""),
            probe=body.get("probe") or {},
            target=body.get("target") or {},
            action=body.get("action") or {},
            failure_threshold=int(body.get("failure_threshold", 3)),
            recovery_threshold=int(body.get("recovery_threshold", 2)),
            window_seconds=int(body.get("window_seconds", 60)),
            cooldown_seconds=int(body.get("cooldown_seconds", 300)),
            max_retries=int(body.get("max_retries", 3)),
            retry_delay_seconds=int(body.get("retry_delay_seconds", 60)),
            escalation=body.get("escalation"),
            maintenance_windows=body.get("maintenance_windows"),
            description=body.get("description"),
            site_id=body.get("site_id"),
            created_by_user_id=g.current_user.id,
        )
    except WatchdogValidationError as e:
        return _err(f"Validation failed: {e}")

    audit_service.record(
        "watchdog_rule.created",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="watchdog_rule",
        target_id=rule["id"],
        details={"name": rule["name"], "via": "json_editor"},
    )
    flash(f"Rule created from JSON: {rule['sentence']}", "info")
    return redirect(url_for("admin_ui.rules_page"))


@admin_ui_bp.get("/rules/<rule_id>/edit")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def rules_edit_page(rule_id: str):
    """v0.5.19 (Rules UX phase): edit a rule via the JSON editor —
    pre-fills the textarea with the current rule body so the operator
    can tweak any field instead of delete-and-recreate.
    """
    import json

    rule = svc_get_rule(rule_id)
    if rule is None:
        abort(404)
    # Build the editor body — strip server-side runtime fields the
    # operator can't and shouldn't override.
    body = {
        "name": rule["name"],
        "description": rule.get("description"),
        "probe": rule["probe"],
        "target": rule["target"],
        "action": rule["action"],
        "failure_threshold": rule["failure_threshold"],
        "recovery_threshold": rule["recovery_threshold"],
        "window_seconds": rule["window_seconds"],
        "cooldown_seconds": rule["cooldown_seconds"],
        "max_retries": rule["max_retries"],
        "retry_delay_seconds": rule["retry_delay_seconds"],
        "escalation": rule.get("escalation") or {"kind": "stop"},
        "maintenance_windows": rule.get("maintenance_windows") or [],
    }
    return render_template(
        "rules/edit.html",
        **_ctx({
            "active": "rules",
            "rule": rule,
            "rule_json": json.dumps(body, indent=2),
        }),
    )


@admin_ui_bp.post("/rules/<rule_id>/edit")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def rules_edit_submit(rule_id: str):
    import json

    raw = (request.form.get("rule_json") or "").strip()

    def _err(msg: str):
        rule = svc_get_rule(rule_id)
        if rule is None:
            abort(404)
        return render_template(
            "rules/edit.html",
            **_ctx({
                "active": "rules",
                "rule": rule,
                "rule_json": raw or "",
                "json_editor_error": msg,
            }),
        )

    if not raw:
        return _err("Paste a JSON body first.")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as e:
        return _err(f"JSON parse error: {e}")
    if not isinstance(body, dict):
        return _err("Top-level JSON must be an object.")

    try:
        rule = svc_update_rule(
            rule_id,
            name=body.get("name", ""),
            probe=body.get("probe") or {},
            target=body.get("target") or {},
            action=body.get("action") or {},
            failure_threshold=int(body.get("failure_threshold", 3)),
            recovery_threshold=int(body.get("recovery_threshold", 2)),
            window_seconds=int(body.get("window_seconds", 60)),
            cooldown_seconds=int(body.get("cooldown_seconds", 300)),
            max_retries=int(body.get("max_retries", 3)),
            retry_delay_seconds=int(body.get("retry_delay_seconds", 60)),
            escalation=body.get("escalation"),
            maintenance_windows=body.get("maintenance_windows"),
            description=body.get("description"),
            site_id=body.get("site_id"),
            updated_by_user_id=g.current_user.id,
        )
    except WatchdogValidationError as e:
        return _err(f"Validation failed: {e}")
    if rule is None:
        abort(404)
    audit_service.record(
        "watchdog_rule.updated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="watchdog_rule",
        target_id=rule_id,
        details={"name": rule["name"], "via": "json_editor"},
    )
    flash(f"Rule updated: {rule['sentence']}", "info")
    return redirect(url_for("admin_ui.rules_page"))


@admin_ui_bp.post("/rules/<rule_id>/toggle")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def rules_set_enabled_submit(rule_id: str):
    enabled = (request.form.get("enabled") or "").lower() in ("1", "true", "on")
    svc_set_enabled(rule_id, enabled)
    audit_service.record(
        "watchdog_rule.enabled_changed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="watchdog_rule",
        target_id=rule_id,
        details={"enabled": enabled, "reason": "operator"},
    )
    return redirect(url_for("admin_ui.rules_page"))


@admin_ui_bp.post("/rules/<rule_id>/probe-now")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def rules_probe_now_submit(rule_id: str):
    """v0.4.2: synchronous probe-now diagnostic. Records an event
    but does NOT advance the state machine or fire any action."""
    from flask import flash

    res = svc_probe_now(rule_id)
    if res is None:
        flash("Rule not found.", "error")
        return redirect(url_for("admin_ui.rules_page"))
    audit_service.record(
        "watchdog_rule.probed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="watchdog_rule",
        target_id=rule_id,
        details={"outcome": res["outcome"], "via": "probe_now"},
    )
    flash(f"Probe ran: {res['outcome']}.", "info")
    return redirect(url_for("admin_ui.rules_page"))


@admin_ui_bp.post("/rules/<rule_id>/delete")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def rules_delete_submit(rule_id: str):
    if svc_delete_rule(rule_id):
        audit_service.record(
            "watchdog_rule.deleted",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="watchdog_rule",
            target_id=rule_id,
            details={"reason": "operator"},
        )
    return redirect(url_for("admin_ui.rules_page"))


# ── API ────────────────────────────────────────────────────────────────────

@admin_api_bp.get("/rules")
@admin_required_api
def list_rules_api():
    return ok(svc_list_rules())


@admin_api_bp.post("/rules")
@role_required_api(*ADMIN_AND_UP)
def create_rule_api():
    body = request.get_json(silent=True) or {}
    try:
        rule = svc_create_rule(
            name=body.get("name", ""),
            probe=body.get("probe") or {},
            target=body.get("target") or {},
            action=body.get("action") or {},
            failure_threshold=body.get("failure_threshold", 3),
            recovery_threshold=body.get("recovery_threshold", 2),
            window_seconds=body.get("window_seconds", 60),
            cooldown_seconds=body.get("cooldown_seconds", 300),
            max_retries=body.get("max_retries", 3),
            retry_delay_seconds=body.get("retry_delay_seconds", 60),
            escalation=body.get("escalation"),
            maintenance_windows=body.get("maintenance_windows"),
            description=body.get("description"),
            site_id=body.get("site_id"),
            created_by_user_id=g.current_user.id,
        )
    except WatchdogValidationError as e:
        return err("validation_failed", str(e), status=400)
    audit_service.record(
        "watchdog_rule.created",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="watchdog_rule",
        target_id=rule["id"],
        details={"name": rule["name"], "reason": "operator"},
    )
    return ok(rule, status=201)


@admin_api_bp.get("/rules/<rule_id>/events")
@admin_required_api
def list_rule_events_api(rule_id: str):
    return ok(svc_list_events(rule_id))


@admin_api_bp.post("/rules/<rule_id>/probe-now")
@role_required_api(*ADMIN_AND_UP)
def probe_now_api(rule_id: str):
    res = svc_probe_now(rule_id)
    if res is None:
        return err("rule_unknown", "Watchdog rule not found.", status=404)
    audit_service.record(
        "watchdog_rule.probed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="watchdog_rule",
        target_id=rule_id,
        details={"outcome": res["outcome"], "via": "probe_now_api"},
    )
    return ok(res)


@admin_api_bp.patch("/rules/<rule_id>")
@role_required_api(*ADMIN_AND_UP)
def update_rule_api(rule_id: str):
    """v0.5.19: full-rule update via JSON. Same shape as POST /rules.
    Resets runtime state-machine counters server-side."""
    body = request.get_json(silent=True) or {}
    try:
        rule = svc_update_rule(
            rule_id,
            name=body.get("name", ""),
            probe=body.get("probe") or {},
            target=body.get("target") or {},
            action=body.get("action") or {},
            failure_threshold=body.get("failure_threshold", 3),
            recovery_threshold=body.get("recovery_threshold", 2),
            window_seconds=body.get("window_seconds", 60),
            cooldown_seconds=body.get("cooldown_seconds", 300),
            max_retries=body.get("max_retries", 3),
            retry_delay_seconds=body.get("retry_delay_seconds", 60),
            escalation=body.get("escalation"),
            maintenance_windows=body.get("maintenance_windows"),
            description=body.get("description"),
            site_id=body.get("site_id"),
            updated_by_user_id=g.current_user.id,
        )
    except WatchdogValidationError as e:
        return err("validation_failed", str(e), status=400)
    if rule is None:
        return err("rule_unknown", "Watchdog rule not found.", status=404)
    audit_service.record(
        "watchdog_rule.updated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="watchdog_rule",
        target_id=rule_id,
        details={"name": rule["name"], "via": "api"},
    )
    return ok(rule)


@admin_api_bp.delete("/rules/<rule_id>")
@role_required_api(*ADMIN_AND_UP)
def delete_rule_api(rule_id: str):
    if not svc_delete_rule(rule_id):
        return err("rule_unknown", "Watchdog rule not found.", status=404)
    audit_service.record(
        "watchdog_rule.deleted",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="watchdog_rule",
        target_id=rule_id,
        details={"reason": "operator"},
    )
    return ok({"deleted": True})
