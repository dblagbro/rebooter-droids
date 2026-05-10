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
    list_rules as svc_list_rules,
    list_recent_events as svc_list_events,
    probe_now as svc_probe_now,
    set_enabled as svc_set_enabled,
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
    return render_template(
        "rules/index.html",
        **_ctx({
            "active": "rules",
            "rules": rules_with_events,
            "devices": devices,
            "groups": groups,
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
        probe = {"kind": "internet"}
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
    combinations the form-builder doesn't surface."""
    import json

    raw = (request.form.get("rule_json") or "").strip()
    if not raw:
        flash("Paste a JSON body first.", "error")
        return redirect(url_for("admin_ui.rules_page"))
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as e:
        flash(f"JSON parse error: {e}", "error")
        return redirect(url_for("admin_ui.rules_page"))
    if not isinstance(body, dict):
        flash("Top-level JSON must be an object.", "error")
        return redirect(url_for("admin_ui.rules_page"))

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
        flash(str(e), "error")
        return redirect(url_for("admin_ui.rules_page"))

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
