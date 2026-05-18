"""Watchdog-rule create-form → JSON-shape mappers — v0.5.67.

Extracted from `rules.py::rules_create_submit` (which was a 211-line
handler) so the blueprint stays a thin HTTP translator — per
`architecture.md` §"Module-boundary principles": *no business logic in
blueprints*.

Each builder turns the rule-create form's flat fields into the
probe / target / action / maintenance-window JSON shapes the watchdog
service (`services.watchdog.create_rule`) consumes. On malformed input
a builder raises `RuleFormError`; the handler catches it, flashes the
message, and redirects back to the rules page — exactly the behavior
the inline code had.

`form` is the Flask `request.form` MultiDict (`.get` / `.getlist`).
"""

from __future__ import annotations


class RuleFormError(ValueError):
    """Malformed rule-form input. The blueprint catches this →
    `flash(str(e), "error")` + redirect to the rules page."""


def build_probe_from_form(form) -> dict:
    """Map the create-form's `probe_kind` + per-kind fields to a probe
    JSON dict. Raises `RuleFormError` for an unsupported kind or a
    non-numeric power threshold."""
    probe_kind = (form.get("probe_kind") or "").strip()
    probe_arg = (form.get("probe_arg") or "").strip()

    if probe_kind == "internet":
        # v0.5.9: form posts repeated `internet_target_host[]` +
        # `internet_target_port[]` pairs. Zip and filter to drop the
        # empty placeholder row the UI keeps for "add another".
        hosts = form.getlist("internet_target_host[]")
        ports = form.getlist("internet_target_port[]")
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
        probe: dict = {"kind": "internet"}
        if targets:
            probe["targets"] = targets
        return probe
    if probe_kind == "ping":
        return {"kind": "ping", "host": probe_arg}
    if probe_kind == "tcp":
        host, _, port = probe_arg.partition(":")
        return {"kind": "tcp", "host": host, "port": int(port or 0)}
    if probe_kind == "http":
        return {"kind": "http", "url": probe_arg}
    if probe_kind == "dns":
        return {"kind": "dns", "hostname": probe_arg}
    if probe_kind == "gateway":
        return {"kind": "gateway"}
    # v0.5.28 (Phase 2B): per-kind form fields for the integration probe
    # kinds. Operators no longer need the JSON editor for the common
    # cases — the JSON editor stays as an escape hatch for advanced
    # shapes (and for the kinds not surfaced in the structured form).
    if probe_kind == "roku_app_active":
        probe = {
            "kind": "roku_app_active",
            "source_id": (form.get("roku_source_id") or "").strip(),
            "app_name": (form.get("roku_app_name") or "").strip(),
        }
        try:
            probe["max_sample_age_seconds"] = int(
                form.get("roku_max_sample_age_seconds") or 120
            )
        except ValueError:
            pass
        return probe
    if probe_kind == "ha_state_is":
        probe = {
            "kind": "ha_state_is",
            "source_id": (form.get("ha_source_id") or "").strip(),
            "entity_id": (form.get("ha_entity_id") or "").strip(),
            "expected_state": (form.get("ha_expected_state") or "").strip(),
        }
        try:
            probe["max_sample_age_seconds"] = int(
                form.get("ha_max_sample_age_seconds") or 60
            )
        except ValueError:
            pass
        return probe
    if probe_kind == "weather_alert_active":
        probe = {
            "kind": "weather_alert_active",
            "source_id": (form.get("weather_source_id") or "").strip(),
        }
        ev = (form.get("weather_event_contains") or "").strip()
        if ev:
            probe["event_contains"] = ev
        sev = (form.get("weather_min_severity") or "").strip()
        if sev:
            probe["min_severity"] = sev
        try:
            probe["max_sample_age_seconds"] = int(
                form.get("weather_max_sample_age_seconds") or 600
            )
        except ValueError:
            pass
        return probe
    if probe_kind == "ical_event_active":
        probe = {
            "kind": "ical_event_active",
            "source_id": (form.get("ical_source_id") or "").strip(),
        }
        summary = (form.get("ical_summary_contains") or "").strip()
        if summary:
            probe["summary_contains"] = summary
        try:
            probe["max_sample_age_seconds"] = int(
                form.get("ical_max_sample_age_seconds") or 1800
            )
        except ValueError:
            pass
        return probe
    # v0.5.92 (Stage C form-builder): EPG "show airing now" probe.
    # EPG reads the shared TVMaze cache — no per-source picker.
    if probe_kind == "epg_show_airing":
        probe = {
            "kind": "epg_show_airing",
            "show": (form.get("epg_show") or "").strip(),
        }
        network = (form.get("epg_network") or "").strip()
        if network:
            probe["network"] = network
        return probe
    # v0.5.32 (B16 Phase 1D): power-targeted probes.
    if probe_kind in ("power_above", "power_below"):
        probe = {
            "kind": probe_kind,
            "device_id": (form.get("power_device_id") or "").strip(),
        }
        try:
            probe["threshold_w"] = float(form.get("power_threshold_w") or 0)
        except ValueError:
            raise RuleFormError(
                "threshold_w must be a number (e.g. 1500 for 1500W)."
            ) from None
        try:
            probe["window_seconds"] = int(form.get("power_window_seconds") or 300)
        except ValueError:
            probe["window_seconds"] = 300
        try:
            probe["max_sample_age_seconds"] = int(
                form.get("power_max_sample_age_seconds") or 600
            )
        except ValueError:
            pass
        return probe
    if probe_kind == "power_zero_while_on":
        probe = {
            "kind": "power_zero_while_on",
            "device_id": (form.get("power_device_id") or "").strip(),
        }
        try:
            probe["near_zero_threshold_w"] = float(
                form.get("power_near_zero_threshold_w") or 0.5
            )
        except ValueError:
            probe["near_zero_threshold_w"] = 0.5
        try:
            probe["window_seconds"] = int(form.get("power_window_seconds") or 300)
        except ValueError:
            probe["window_seconds"] = 300
        try:
            probe["max_sample_age_seconds"] = int(
                form.get("power_max_sample_age_seconds") or 600
            )
        except ValueError:
            pass
        return probe
    raise RuleFormError("Unsupported probe kind.")


def build_target_from_form(form) -> dict:
    """Map `target_kind` + `target_id` to a target JSON dict."""
    target_kind = (form.get("target_kind") or "").strip()
    target_id = (form.get("target_id") or "").strip()
    if target_kind in ("device", "group"):
        return {"kind": target_kind, "id": target_id}
    if target_kind == "tag":
        return {"kind": "tag", "tag": target_id}
    raise RuleFormError("Pick a target.")


def build_action_from_form(form) -> dict:
    """Map `action_kind` + its fields to an action JSON dict."""
    action_kind = (form.get("action_kind") or "cycle").strip()
    if action_kind == "cycle":
        return {
            "kind": "cycle",
            "power_off_seconds": int(form.get("power_off_seconds") or 5),
            "post_reboot_holdoff_seconds": int(
                form.get("post_reboot_holdoff_seconds") or 180
            ),
        }
    if action_kind == "hold_off":
        return {"kind": "hold_off"}
    if action_kind == "notify_only":
        return {"kind": "notify_only"}
    # v0.5.92 (Stage C form-builder): set-state + scene + binding actions.
    if action_kind == "relay_on":
        return {"kind": "relay_on"}
    if action_kind == "relay_off":
        return {"kind": "relay_off"}
    if action_kind == "apply_scene":
        scene_id = (form.get("scene_id") or "").strip()
        if not scene_id:
            raise RuleFormError("Pick a scene for the apply_scene action.")
        return {"kind": "apply_scene", "scene_id": scene_id}
    if action_kind == "binding":
        # The form expresses a binding as two saved scenes — the one to
        # apply while the probe holds, and the one to restore when it
        # clears. (Non-scene bindings stay on the JSON editor.)
        active = (form.get("binding_active_scene_id") or "").strip()
        clear = (form.get("binding_clear_scene_id") or "").strip()
        if not active or not clear:
            raise RuleFormError(
                "A binding needs a scene for both the active and "
                "cleared states. Create scenes under Scenes first."
            )
        return {
            "kind": "binding",
            "on_active": {"kind": "apply_scene", "scene_id": active},
            "on_clear": {"kind": "apply_scene", "scene_id": clear},
        }
    raise RuleFormError("Unsupported action.")


def build_maintenance_windows_from_form(form) -> list[dict] | None:
    """v0.4.7 (B7): per-rule maintenance window. The form provides
    `maint_start` / `maint_end` as `datetime-local` values (no
    timezone) — treated as UTC since the operator is global. Returns
    None when no window is set."""
    maint_start = (form.get("maint_start") or "").strip()
    maint_end = (form.get("maint_end") or "").strip()
    if not (maint_start and maint_end):
        return None
    # `datetime-local` produces "YYYY-MM-DDTHH:MM" (length 16) — tag UTC.
    return [{
        "start": maint_start + ":00+00:00" if len(maint_start) == 16 else maint_start,
        "end": maint_end + ":00+00:00" if len(maint_end) == 16 else maint_end,
    }]
