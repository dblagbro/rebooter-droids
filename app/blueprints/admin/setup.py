"""First-run setup wizard + reusable 3-mode device picker — Tier-2 Feature 1.

Two distinct, deliberately bundled things (design §"Two distinct things"):

  - **First-run wizard** at `/app/setup` — a small server-side state
    machine (welcome → branding → smtp → first-device → done) shown once
    when the hub has no devices / no rules and `system.setup_completed`
    is unset. Wizard state lives in a *signed session* dict
    (`session["setup_wizard"]`) — no new table; the wizard is ephemeral
    and idempotent and only the final step writes.

  - **3-mode picker** at `/app/devices/<id>/configure` — a reusable
    per-device flow ("Set up this device") reachable from the
    device-detail page and embedded in the wizard's `first-device` step.

The picker is a thin HTTP translator (architecture.md module-boundary
rule): it collects plain-language answers, hands them to the pure
`services.setup_wizard` functions, then calls the two existing services
— `device_config.set_desired_config()` and `watchdog.create_rule()`.
"""

from __future__ import annotations

from flask import (
    flash, g, redirect, render_template, request, session, url_for,
)

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_ui
from app.services import audit as audit_service
from app.services import device_config
from app.services import runtime_settings
from app.services import setup_wizard as wiz
from app.services import watchdog as watchdog_svc
from app.services.devices import get_device_detail

# The wizard step order. Each is one GET; POST advances to the next.
WIZARD_STEPS = ("welcome", "branding", "smtp", "first-device", "done")


# ── first-run detection (consumed by the dashboard banner) ──────────────

def is_setup_completed() -> bool:
    """Whether the operator has finished or dismissed the first-run
    wizard. Backed by the `system.setup_completed` runtime-setting."""
    val = runtime_settings.get(
        "system.setup_completed", env_var="REBOOTER_SETUP_COMPLETED", default=None
    )
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


def should_show_first_run() -> bool:
    """True when the first-run banner / redirect should fire: the hub has
    no devices AND no watchdog rules AND `system.setup_completed` is
    unset (design §"a first-run check"). Best-effort — any failure
    resolves to False so a hiccup never forces the wizard on an
    established hub."""
    if is_setup_completed():
        return False
    try:
        from sqlalchemy import func, select

        from app.db import session_scope
        from app.models import Device, WatchdogRule

        with session_scope() as s:
            devices = s.scalar(select(func.count()).select_from(Device)) or 0
            rules = s.scalar(select(func.count()).select_from(WatchdogRule)) or 0
        return devices == 0 and rules == 0
    except Exception:
        return False


def _mark_setup_completed() -> None:
    from datetime import datetime, timezone

    runtime_settings.set_(
        "system.setup_completed", True, user_id=g.current_user.id
    )
    runtime_settings.set_(
        "system.setup_completed_at",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        user_id=g.current_user.id,
    )


# ── the wizard state machine ────────────────────────────────────────────

@admin_ui_bp.get("/setup")
@admin_required_ui
def setup_wizard_entry():
    """Wizard entry — redirect to the first step. (The wizard is small
    enough that "first incomplete step" is just `welcome`; the per-step
    routes handle their own forward/back.)"""
    return redirect(url_for("admin_ui.setup_wizard_step", step="welcome"))


@admin_ui_bp.get("/setup/step/<step>")
@admin_required_ui
def setup_wizard_step(step: str):
    """Render one wizard step. State carried in the signed session."""
    if step not in WIZARD_STEPS:
        return redirect(url_for("admin_ui.setup_wizard_step", step="welcome"))

    state = session.get("setup_wizard") or {}
    idx = WIZARD_STEPS.index(step)
    ctx_extra = {
        "active": "settings",
        "step": step,
        "step_index": idx,
        "steps": WIZARD_STEPS,
        "wizard_state": state,
    }

    if step == "branding":
        ctx_extra["portal_name"] = (
            runtime_settings.get(
                "system.portal_name", env_var="REBOOTER_PORTAL_NAME", default=""
            )
            or ""
        )
    elif step == "smtp":
        from app.services.email import is_configured

        ctx_extra["smtp_configured"] = is_configured()
    elif step == "first-device":
        # Embed the mode picker for the first enrolled device, or point
        # at enrollment if the hub has none yet.
        from app.services.devices import list_devices as svc_list_devices

        devices = svc_list_devices()
        ctx_extra["devices"] = devices
        ctx_extra["first_device"] = devices[0] if devices else None
        if devices:
            ctx_extra["picker_modes"] = _PICKER_MODES

    return render_template(
        f"setup/_step_{step.replace('-', '_')}.html"
        if step != "welcome" and step != "done"
        else f"setup/_step_{step}.html",
        **_ctx(ctx_extra),
    )


@admin_ui_bp.post("/setup/step/<step>")
@admin_required_ui
def setup_wizard_step_submit(step: str):
    """Advance the wizard. Only `branding` / `smtp` reuse
    `runtime_settings`; `welcome` / `first-device` just move on; `done`
    marks `system.setup_completed`. The mode picker on `first-device` is
    its own route (`device_configure_submit`)."""
    if step not in WIZARD_STEPS:
        return redirect(url_for("admin_ui.setup_wizard_step", step="welcome"))

    state = dict(session.get("setup_wizard") or {})

    if step == "branding":
        portal_name = (request.form.get("portal_name") or "").strip()
        if portal_name:
            runtime_settings.set_(
                "system.portal_name", portal_name, user_id=g.current_user.id
            )
            audit_service.record(
                "setup.branding_set",
                actor_user_id=g.current_user.id,
                actor_email_snapshot=g.current_user.email,
                target_type="runtime_settings",
                target_id="system.portal_name",
                details={"portal_name": portal_name},
            )
        state["branding_done"] = True

    elif step == "smtp":
        # SMTP is configured on the dedicated Settings → Notifications
        # page; the wizard step just confirms + records acknowledgement.
        state["smtp_acknowledged"] = True

    elif step == "done":
        _mark_setup_completed()
        audit_service.record(
            "setup.completed",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="runtime_settings",
            target_id="system.setup_completed",
            details={},
        )
        session.pop("setup_wizard", None)
        flash("Setup complete. You can re-run it any time from Settings.", "info")
        return redirect(url_for("admin_ui.index"))

    session["setup_wizard"] = state
    next_step = WIZARD_STEPS[min(WIZARD_STEPS.index(step) + 1, len(WIZARD_STEPS) - 1)]
    return redirect(url_for("admin_ui.setup_wizard_step", step=next_step))


@admin_ui_bp.post("/setup/skip")
@admin_required_ui
def setup_wizard_skip():
    """Dismiss the first-run wizard permanently without finishing it."""
    _mark_setup_completed()
    audit_service.record(
        "setup.skipped",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="runtime_settings",
        target_id="system.setup_completed",
        details={},
    )
    session.pop("setup_wizard", None)
    flash("Setup wizard dismissed. Re-run it any time from Settings.", "info")
    return redirect(url_for("admin_ui.index"))


# ── the reusable 3-mode device picker ───────────────────────────────────

# Plain-language copy for the three big radio cards (design §Templates).
_PICKER_MODES = (
    {
        "value": "smart_plug",
        "label": "Just a smart switch",
        "blurb": "Turn power on and off — by hand, or on a schedule. "
                 "No automatic restarting.",
        "help": "Pick this for a lamp, a fan, a heater — anything you "
                "just want to switch remotely.",
    },
    {
        "value": "internet_watchdog",
        "label": "Keep my internet alive",
        "blurb": "Watch your internet connection and power-cycle the "
                 "modem or router when it drops.",
        "help": "Pick this if the device is plugged into your modem or "
                "router. We check a couple of well-known internet "
                "addresses and restart the box when they all stop "
                "answering.",
    },
    {
        "value": "device_watchdog",
        "label": "Restart one device when it locks up",
        "blurb": "Watch a single device on your network and power-cycle "
                 "it when it stops responding.",
        "help": "Pick this for a camera, a server, a set-top box — "
                "anything that occasionally freezes and needs a power "
                "cycle to come back.",
    },
)


@admin_ui_bp.get("/devices/<device_id>/configure")
@admin_required_ui
def device_configure_page(device_id: str):
    """The reusable 3-mode picker for one device."""
    detail = get_device_detail(device_id)
    if detail is None:
        from flask import abort

        abort(404)
    return render_template(
        "setup/mode_picker.html",
        **_ctx({
            "active": "devices",
            "device": detail,
            "picker_modes": _PICKER_MODES,
            "current_mode": detail.get("desired_mode"),
            "defaults": {
                "internet_targets": "\n".join(wiz.DEFAULT_INTERNET_TARGETS),
                "offline_tolerance_seconds": wiz.DEFAULT_OFFLINE_TOLERANCE_SECONDS,
                "power_off_seconds": wiz.DEFAULT_POWER_OFF_SECONDS,
                "post_reboot_holdoff_seconds": wiz.DEFAULT_POST_REBOOT_HOLDOFF_SECONDS,
                "cooldown_seconds": wiz.DEFAULT_COOLDOWN_SECONDS,
                "max_cycles_per_hour": wiz.DEFAULT_MAX_CYCLES_PER_HOUR,
            },
            "in_wizard": request.args.get("wizard") == "1",
        }),
    )


# create_rule() accepts only this subset of the rule_payload — the rest
# of the payload (max_cycles_per_hour) lives in desired_config only.
_CREATE_RULE_KEYS = (
    "name", "description", "probe", "target", "action",
    "failure_threshold", "window_seconds", "cooldown_seconds",
)


@admin_ui_bp.post("/devices/<device_id>/configure")
@admin_required_ui
def device_configure_submit(device_id: str):
    """Apply a chosen mode to the device. On success: writes the
    `desired_config` via `set_desired_config`, replaces any prior
    wizard-generated rule, creates the new watchdog rule (modes 2 + 3),
    audits `device.setup_mode_applied`, and redirects to device detail."""
    detail = get_device_detail(device_id)
    if detail is None:
        from flask import abort

        abort(404)

    mode = (request.form.get("mode") or "").strip()
    in_wizard = request.form.get("wizard") == "1"

    # Collect every plain-language answer the picker template posts. The
    # pure service layer validates + translates them.
    answers = {
        "device_name": request.form.get("device_name"),
        "relay_restore_behavior": request.form.get("relay_restore_behavior"),
        "internet_targets": request.form.get("internet_targets"),
        "watch_address": request.form.get("watch_address"),
        "offline_tolerance_seconds": request.form.get("offline_tolerance_seconds"),
        "power_off_seconds": request.form.get("power_off_seconds"),
        "post_reboot_holdoff_seconds": request.form.get("post_reboot_holdoff_seconds"),
        "cooldown_seconds": request.form.get("cooldown_seconds"),
        "max_cycles_per_hour": request.form.get("max_cycles_per_hour"),
    }

    def _back():
        if in_wizard:
            return redirect(
                url_for("admin_ui.setup_wizard_step", step="first-device")
            )
        return redirect(
            url_for("admin_ui.device_configure_page", device_id=device_id)
        )

    try:
        result = wiz.apply_picker(device_id, mode, answers)
    except wiz.SetupWizardError as e:
        flash(str(e), "error")
        return _back()

    # 1. Persist the desired_config + desired_mode.
    try:
        device_config.set_desired_config(
            device_id,
            result["desired_config"],
            by_user_id=g.current_user.id,
            desired_mode=result["desired_mode"],
        )
    except device_config.DesiredConfigError as e:
        flash(f"Could not save the device settings: {e}", "error")
        return _back()

    # 2. Replace any prior wizard rule for this mode, then create the new
    #    one (smart-plug mode has no rule).
    rule_payload = result.get("rule_payload")
    replaced_rule_id = None
    new_rule_id = None
    if rule_payload is not None:
        prior = wiz.find_prior_wizard_rule(device_id, result["desired_mode"])
        if prior:
            watchdog_svc.delete_rule(prior)
            replaced_rule_id = prior
        create_kwargs = {
            k: rule_payload[k] for k in _CREATE_RULE_KEYS if k in rule_payload
        }
        try:
            rule = watchdog_svc.create_rule(
                site_id=detail.get("site_id"),
                created_by_user_id=g.current_user.id,
                **create_kwargs,
            )
            new_rule_id = rule.get("id")
        except watchdog_svc.WatchdogValidationError as e:
            flash(
                f"Device settings saved, but the watchdog rule could not "
                f"be created: {e}",
                "error",
            )
            return _back()

    audit_service.record(
        "device.setup_mode_applied",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="device",
        target_id=device_id,
        details={
            "mode": result["desired_mode"],
            "rule_created": new_rule_id,
            "rule_replaced": replaced_rule_id,
            "via": "wizard" if in_wizard else "device_configure",
        },
    )

    if rule_payload is not None:
        flash(
            "Device configured. A watchdog rule is now watching it — "
            "see the Watchdog section below.",
            "info",
        )
    else:
        flash("Device configured as a smart switch.", "info")

    if in_wizard:
        return redirect(url_for("admin_ui.setup_wizard_step", step="done"))
    return redirect(url_for("admin_ui.device_detail_page", device_id=device_id))
