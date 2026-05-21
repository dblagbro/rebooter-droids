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
from app.blueprints.admin._common import FormValidationError, _ctx, _int_field
from app.blueprints.admin._rules_forms import (
    RuleFormError,
    build_action_from_form,
    build_maintenance_windows_from_form,
    build_probe_from_form,
    build_target_from_form,
)
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
from app.services.conflict_detection import (
    detect_conflicts as svc_detect_conflicts,
    has_blocking as svc_has_blocking,
)
from app.services.devices import list_devices as svc_list_devices
from app.services.groups import list_groups as svc_list_groups
from app.services.scenes import list_scenes as svc_list_scenes
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


# v0.5.77 (#15): probe kinds the structured rule form can round-trip.
# A rule whose probe.kind is outside this set (host_awake,
# mqtt_topic_equals, …) gets the JSON editor only — the structured form
# has no field block for it and would silently rebuild it as `internet`
# on save. v0.5.95: epg_show_airing joined the set (edit-form parity).
STRUCTURED_PROBE_KINDS = frozenset({
    "internet", "ping", "tcp", "http", "dns", "gateway",
    "roku_app_active", "ha_state_is", "weather_alert_active",
    "ical_event_active", "epg_show_airing",
    "power_above", "power_below", "power_zero_while_on",
})


def _action_form_supported(action: dict) -> bool:
    """True when the structured edit form can round-trip this action
    without data loss — the action-side analogue of STRUCTURED_PROBE_KINDS.

    `apply_scene` / `binding` are form-editable only when they reference
    saved scenes by `scene_id`; an `apply_scene` carrying inline `items`
    or a `binding` with non-scene edges has no field block and falls back
    to the JSON editor rather than being silently flattened on save."""
    a = action or {}
    kind = a.get("kind")
    if kind in ("cycle", "hold_off", "notify_only", "relay_on", "relay_off"):
        return True
    if kind == "apply_scene":
        return bool(a.get("scene_id"))
    if kind == "binding":
        for edge in (a.get("on_active"), a.get("on_clear")):
            e = edge or {}
            if e.get("kind") != "apply_scene" or not e.get("scene_id"):
                return False
        return True
    return False


def _sources_by_kind() -> dict:
    """Kind-filtered integration-source pickers shared by the rule
    create + edit forms. Lazy import keeps the rules import graph small."""
    from app.services import external_sensors as ext_svc

    all_sources = ext_svc.list_sources()
    return {
        "roku": [s for s in all_sources if s["kind"] == "roku"],
        "home_assistant": [s for s in all_sources if s["kind"] == "home_assistant"],
        "weather": [s for s in all_sources if s["kind"] == "weather"],
        "ical": [s for s in all_sources if s["kind"] == "ical"],
    }


def _render_rule_edit(rule: dict, *, rule_json: str, json_editor_error: str | None = None):
    """Render the rule edit page — structured form + JSON editor.

    Single render path so every entry point (initial GET, JSON-editor
    validation failure) gives the structured form the same context."""
    p = rule.get("probe") or {}
    pk = p.get("kind")
    # The structured form has one `probe_arg` text field; which probe key
    # it maps to depends on the probe kind.
    if pk == "ping":
        probe_arg = p.get("host") or ""
    elif pk == "tcp":
        probe_arg = f"{p.get('host', '')}:{p.get('port', '')}" if p.get("host") else ""
    elif pk == "http":
        probe_arg = p.get("url") or ""
    elif pk == "dns":
        probe_arg = p.get("hostname") or ""
    else:
        probe_arg = ""
    probe_ok = pk in STRUCTURED_PROBE_KINDS
    action_ok = _action_form_supported(rule.get("action") or {})
    return render_template(
        "rules/edit.html",
        **_ctx({
            "active": "rules",
            "rule": rule,
            "rule_json": rule_json,
            "json_editor_error": json_editor_error,
            "devices": svc_list_devices(include_qa_fixtures=False),
            "groups": svc_list_groups(),
            "sources_by_kind": _sources_by_kind(),
            "scenes": svc_list_scenes(),
            "probe_arg": probe_arg,
            "probe_form_supported": probe_ok,
            "action_form_supported": action_ok,
            # The structured form is offered only when it can round-trip
            # both the probe and the action without data loss.
            "form_supported": probe_ok and action_ok,
        }),
    )


# ── conflict-detection confirm step ────────────────────────────────────────
#
# v1.x: `detect_conflicts()` runs the cross-rule / cross-schedule
# semantic checks on top of `create_rule()`'s structural validation.
# `warn` / `info` findings are informational — the save proceeds. A
# `block` finding requires the operator to explicitly acknowledge before
# the rule is saved: the form re-renders with the conflicts shown and a
# hidden `conflicts_acknowledged` field; re-submitting confirms. This
# mirrors the codebase's existing confirm patterns (mass_action typed
# confirmation, `data-confirm-message`) — it is a confirm step, NOT an
# unbypassable hard block, because the findings are heuristics.

CONFLICTS_ACK_FIELD = "conflicts_acknowledged"


def _conflicts_acknowledged(form) -> bool:
    return (form.get(CONFLICTS_ACK_FIELD) or "").lower() in ("1", "true", "on")


def _render_rules_page_with_conflicts(conflicts, *, pending_form, edit_rule_id=None):
    """Re-render the rules page showing `block`-severity conflicts and a
    confirm form that carries the operator's original input plus a
    `conflicts_acknowledged` flag so the resubmit goes through."""
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
            "sources_by_kind": _sources_by_kind(),
            "scenes": svc_list_scenes(),
            # The conflict-confirm banner reads these.
            "conflicts": [c.as_dict() for c in conflicts],
            "conflicts_pending_form": pending_form,
            "conflicts_edit_rule_id": edit_rule_id,
        }),
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
    # integration probes.
    return render_template(
        "rules/index.html",
        **_ctx({
            "active": "rules",
            "rules": rules_with_events,
            "devices": devices,
            "groups": groups,
            "sources_by_kind": _sources_by_kind(),
            "scenes": svc_list_scenes(),
        }),
    )


@admin_ui_bp.post("/rules")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def rules_create_submit():
    # v0.5.67: the form→JSON-shape mapping (probe / target / action /
    # maintenance window) lives in `_rules_forms.py` — this handler is
    # now a thin HTTP translator per architecture.md §"Module-boundary
    # principles". A `RuleFormError` carries the operator-facing message.
    name = (request.form.get("name") or "").strip()
    try:
        probe = build_probe_from_form(request.form)
        target = build_target_from_form(request.form)
        action = build_action_from_form(request.form)
        maint_windows = build_maintenance_windows_from_form(request.form)
        failure_threshold = _int_field(request.form, "failure_threshold", default=3)
        recovery_threshold = _int_field(request.form, "recovery_threshold", default=2)
        window_seconds = _int_field(request.form, "window_seconds", default=60)
        cooldown_seconds = _int_field(request.form, "cooldown_seconds", default=300)
    except (RuleFormError, FormValidationError) as e:
        flash(str(e), "error")
        return redirect(url_for("admin_ui.rules_page"))

    # v1.x: cross-rule / cross-schedule conflict detection. `block`
    # findings require an explicit acknowledgment; `warn` / `info` are
    # surfaced as flash messages but do not stop the save.
    proposed = {
        "probe": probe, "target": target, "action": action,
        "failure_threshold": failure_threshold,
        "window_seconds": window_seconds,
        "cooldown_seconds": cooldown_seconds,
    }
    conflicts = svc_detect_conflicts(proposed)
    if (
        svc_has_blocking(conflicts)
        and not _conflicts_acknowledged(request.form)
    ):
        return _render_rules_page_with_conflicts(
            conflicts, pending_form=request.form.to_dict(flat=False)
        )

    try:
        rule = svc_create_rule(
            name=name,
            probe=probe,
            target=target,
            action=action,
            failure_threshold=failure_threshold,
            recovery_threshold=recovery_threshold,
            window_seconds=window_seconds,
            cooldown_seconds=cooldown_seconds,
            maintenance_windows=maint_windows,
            created_by_user_id=g.current_user.id,
        )
    except WatchdogValidationError as e:
        flash(str(e), "error")
        return redirect(url_for("admin_ui.rules_page"))

    for c in conflicts:
        flash(f"Conflict ({c.severity}): {c.message}", "warning")

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
                # The create form's integration-probe blocks need this;
                # omitting it 500s the page under StrictUndefined when a
                # bad-JSON submit re-renders here (latent since v0.5.28).
                "sources_by_kind": _sources_by_kind(),
                "scenes": svc_list_scenes(),
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
            failure_threshold=_int_field(body, "failure_threshold", default=3),
            recovery_threshold=_int_field(body, "recovery_threshold", default=2),
            window_seconds=_int_field(body, "window_seconds", default=60),
            cooldown_seconds=_int_field(body, "cooldown_seconds", default=300),
            max_retries=_int_field(body, "max_retries", default=3),
            retry_delay_seconds=_int_field(body, "retry_delay_seconds", default=60),
            escalation=body.get("escalation"),
            maintenance_windows=body.get("maintenance_windows"),
            description=body.get("description"),
            site_id=body.get("site_id"),
            created_by_user_id=g.current_user.id,
        )
    except FormValidationError as e:
        return _err(str(e))
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
    """v0.5.77 (#15): structured edit form mirroring the create form,
    pre-populated from the rule. The JSON editor stays as the advanced
    escape hatch — and the only editor for probe kinds the structured
    form can't round-trip (`probe_form_supported` is False).
    """
    import json

    rule = svc_get_rule(rule_id)
    if rule is None:
        abort(404)
    # Build the JSON-editor body — strip server-side runtime fields the
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
    return _render_rule_edit(rule, rule_json=json.dumps(body, indent=2))


@admin_ui_bp.post("/rules/<rule_id>/edit")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def rules_edit_submit(rule_id: str):
    import json

    raw = (request.form.get("rule_json") or "").strip()

    def _err(msg: str):
        rule = svc_get_rule(rule_id)
        if rule is None:
            abort(404)
        return _render_rule_edit(rule, rule_json=raw or "", json_editor_error=msg)

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
            failure_threshold=_int_field(body, "failure_threshold", default=3),
            recovery_threshold=_int_field(body, "recovery_threshold", default=2),
            window_seconds=_int_field(body, "window_seconds", default=60),
            cooldown_seconds=_int_field(body, "cooldown_seconds", default=300),
            max_retries=_int_field(body, "max_retries", default=3),
            retry_delay_seconds=_int_field(body, "retry_delay_seconds", default=60),
            escalation=body.get("escalation"),
            maintenance_windows=body.get("maintenance_windows"),
            description=body.get("description"),
            site_id=body.get("site_id"),
            updated_by_user_id=g.current_user.id,
        )
    except FormValidationError as e:
        return _err(str(e))
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


@admin_ui_bp.post("/rules/<rule_id>/edit-form")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def rules_edit_form_submit(rule_id: str):
    """v0.5.77 (#15): structured-form rule update. Mirrors
    `rules_create_submit` — reuses the same `_rules_forms` builders —
    but calls `update_rule`. Fields the structured form doesn't surface
    (escalation, retry tuning, description, site) are carried over from
    the existing rule, so a structured save never silently drops them.
    """
    existing = svc_get_rule(rule_id)
    if existing is None:
        abort(404)
    name = (request.form.get("name") or "").strip()
    try:
        probe = build_probe_from_form(request.form)
        target = build_target_from_form(request.form)
        action = build_action_from_form(request.form)
        maint_windows = build_maintenance_windows_from_form(request.form)
        failure_threshold = _int_field(request.form, "failure_threshold", default=3)
        recovery_threshold = _int_field(request.form, "recovery_threshold", default=2)
        window_seconds = _int_field(request.form, "window_seconds", default=60)
        cooldown_seconds = _int_field(request.form, "cooldown_seconds", default=300)
    except (RuleFormError, FormValidationError) as e:
        flash(str(e), "error")
        return redirect(url_for("admin_ui.rules_edit_page", rule_id=rule_id))

    # v1.x: conflict detection — exclude this rule's own id so an edit
    # is never flagged as conflicting with itself. A `block` finding
    # requires acknowledgment before the update is saved.
    proposed = {
        "probe": probe, "target": target, "action": action,
        "failure_threshold": failure_threshold,
        "window_seconds": window_seconds,
        "cooldown_seconds": cooldown_seconds,
    }
    conflicts = svc_detect_conflicts(proposed, exclude_rule_id=rule_id)
    if (
        svc_has_blocking(conflicts)
        and not _conflicts_acknowledged(request.form)
    ):
        return _render_rules_page_with_conflicts(
            conflicts,
            pending_form=request.form.to_dict(flat=False),
            edit_rule_id=rule_id,
        )

    try:
        rule = svc_update_rule(
            rule_id,
            name=name,
            probe=probe,
            target=target,
            action=action,
            failure_threshold=failure_threshold,
            recovery_threshold=recovery_threshold,
            window_seconds=window_seconds,
            cooldown_seconds=cooldown_seconds,
            # Not surfaced in the structured form — preserve as-is.
            max_retries=existing["max_retries"],
            retry_delay_seconds=existing["retry_delay_seconds"],
            escalation=existing.get("escalation"),
            description=existing.get("description"),
            site_id=existing.get("site_id"),
            maintenance_windows=maint_windows,
            updated_by_user_id=g.current_user.id,
        )
    except WatchdogValidationError as e:
        flash(str(e), "error")
        return redirect(url_for("admin_ui.rules_edit_page", rule_id=rule_id))
    if rule is None:
        abort(404)

    for c in conflicts:
        flash(f"Conflict ({c.severity}): {c.message}", "warning")

    audit_service.record(
        "watchdog_rule.updated",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="watchdog_rule",
        target_id=rule_id,
        details={"name": rule["name"], "via": "edit_form"},
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
