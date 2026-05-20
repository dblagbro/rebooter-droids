"""Device desired-config structured-form ↔ JSON-shape mappers — Tier-2.

The device-detail "Desired config" card historically posted one raw
`desired_config_json` `<textarea>`. Tier-2 Feature 2 replaces that with a
real field-based form; the raw textarea stays behind an "Advanced"
`<details>` escape hatch (exactly how `rules/edit.html` keeps its JSON
editor).

This module is the structured-form translator — the same pattern as
`_rules_forms.py`. It keeps the HTTP blueprint a thin translator
(per `architecture.md` §"Module-boundary principles"):

- `build_desired_config_from_form(form)` — flat form fields →
  the `desired_config` dict the service (`device_config.set_desired_config`)
  consumes. Raises `DeviceConfigFormError` with an operator-facing
  message on malformed input.
- `desired_config_to_form_values(cfg)` — the inverse, for pre-populating
  the form from a stored config.
- `is_form_representable(cfg)` — the round-trip gate. If the stored
  config carries a key or nested shape the structured form cannot
  represent without loss, the device-detail page falls back to
  JSON-only — the same `form_supported` discipline `rules.py` uses.

`form` is the Flask `request.form` MultiDict (`.get` / `.getlist`).

The allowed shape is `ALLOWED_DESIRED_CONFIG_KEYS` in
`app/services/device_config.py` plus the sub-key tables in
`docs/firmware-apply-config-schema-v01.md`. The structured form is a
*strict subset* of the JSON editor — a structured save must never
silently drop a key the JSON had.
"""

from __future__ import annotations

from app.services.device_config import ALLOWED_DESIRED_CONFIG_KEYS


class DeviceConfigFormError(ValueError):
    """Malformed device-config-form input. The blueprint catches this →
    `flash(str(e), "error")` + redirect to the device-detail page."""


# ── schema the structured form covers ──────────────────────────────────
#
# Scalar top-level keys the structured form renders as flat fields.
RELAY_RESTORE_CHOICES = ("restore_previous", "always_on", "always_off")

# Object-valued top-level keys the structured form renders as field-sets.
# Each maps to the int sub-keys it surfaces (per the schema doc). Only
# numeric sub-keys are surfaced as fields — anything else keeps the
# device on JSON-only via `is_form_representable`.
INTERNET_INT_SUBKEYS = (
    "failure_threshold_seconds",
    "power_off_seconds",
    "post_reboot_holdoff_seconds",
    "max_cycles_per_incident",
    "max_cycles_per_hour",
    "cooldown_seconds",
    "dns_refresh_seconds",
    "recovery_stability_seconds",
)
DEVICE_INT_SUBKEYS = (
    "failure_threshold_seconds",
    "power_off_seconds",
    "post_reboot_holdoff_seconds",
    "max_cycles_per_incident",
    "max_cycles_per_hour",
    "cooldown_seconds",
    "recovery_stability_seconds",
)
POWER_INT_SUBKEYS = ("sample_rate_hz", "batch_seconds")
NOTIFICATIONS_BOOL_SUBKEYS = (
    "enabled",
    "send_on_trigger",
    "send_on_recovery",
    "send_on_max_cycles_reached",
    "send_test_notification_enabled",
)
NOTIFICATIONS_STR_SUBKEYS = ("type", "webhook_url", "webhook_method")
# Write-only secret — never echoed back to the form (same trick as the
# SMTP password field: blank-on-save means "unchanged").
NOTIFICATIONS_SECRET_SUBKEY = "webhook_auth_token"


# ── helpers ─────────────────────────────────────────────────────────────

def _raw(form, field: str) -> str:
    """Fetch a form field as a stripped string. A real Flask
    `request.form` always yields strings, but the inverse helper
    (`desired_config_to_form_values`) emits native ints/bools — coerce
    so the builder is robust when fed its own output."""
    val = form.get(field)
    if val is None:
        return ""
    return str(val).strip()


def _form_bool(form, field: str) -> bool:
    """An unchecked HTML checkbox posts nothing; a checked one posts
    its value. Treat presence-with-a-truthy-value as True."""
    return _raw(form, field).lower() in ("1", "true", "on", "yes")


def _form_int(form, field: str, label: str) -> int | None:
    """Parse an optional integer field. Blank → None (key omitted).
    A non-numeric value is an operator error → `DeviceConfigFormError`."""
    raw = _raw(form, field)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise DeviceConfigFormError(
            f"{label} must be a whole number (got {raw!r})."
        ) from None


def _form_str(form, field: str) -> str | None:
    raw = _raw(form, field)
    return raw or None


# ── builder: form → desired_config dict ─────────────────────────────────

def build_desired_config_from_form(form, *, existing: dict | None = None) -> dict:
    """Map the structured device-config form's flat fields to a
    `desired_config` dict.

    `existing` is the device's current `desired_config` — used only to
    preserve the write-only `notifications.webhook_auth_token` when the
    operator leaves that field blank ("blank means unchanged").

    Raises `DeviceConfigFormError` on malformed input. The result is
    still validated against `ALLOWED_DESIRED_CONFIG_KEYS` by the service
    layer as the backstop.
    """
    existing = existing or {}
    cfg: dict = {}

    # ── scalar top-level keys ──────────────────────────────────────────
    device_name = _form_str(form, "cfg_device_name")
    if device_name is not None:
        if len(device_name) > 120:
            raise DeviceConfigFormError(
                "Device name must be 120 characters or fewer."
            )
        cfg["device_name"] = device_name

    relay = _form_str(form, "cfg_relay_restore_behavior")
    if relay is not None:
        if relay not in RELAY_RESTORE_CHOICES:
            raise DeviceConfigFormError(
                "Relay restore behavior must be one of "
                f"{', '.join(RELAY_RESTORE_CHOICES)}."
            )
        cfg["relay_restore_behavior"] = relay

    monitor = _form_int(form, "cfg_monitor_interval_seconds", "Monitor interval")
    if monitor is not None:
        cfg["monitor_interval_seconds"] = monitor

    warmup = _form_int(form, "cfg_boot_warmup_seconds", "Boot warm-up")
    if warmup is not None:
        cfg["boot_warmup_seconds"] = warmup

    # A checkbox is tri-state here only if we let it be — keep it simple:
    # the checkbox is always submitted as a hidden+checkbox pair so an
    # explicit False is distinguishable from "field absent". The form
    # partial posts a hidden `cfg_manual_button_enabled_present=1` so an
    # unchecked box still records an explicit False.
    if (form.get("cfg_manual_button_enabled_present") or "").strip():
        cfg["manual_button_enabled"] = _form_bool(form, "cfg_manual_button_enabled")

    # ── object field-sets ──────────────────────────────────────────────
    internet = _build_watchdog_block(
        form, prefix="internet", int_subkeys=INTERNET_INT_SUBKEYS,
        targets_field="cfg_internet_targets",
    )
    if internet:
        cfg["internet"] = internet

    device = _build_watchdog_block(
        form, prefix="device", int_subkeys=DEVICE_INT_SUBKEYS,
        target_field="cfg_device_target",
    )
    if device:
        cfg["device"] = device

    power = {}
    if (form.get("cfg_power_enabled_present") or "").strip():
        power["enabled"] = _form_bool(form, "cfg_power_enabled")
    for sub in POWER_INT_SUBKEYS:
        val = _form_int(form, f"cfg_power_{sub}", f"Power {sub.replace('_', ' ')}")
        if val is not None:
            power[sub] = val
    if power:
        cfg["power"] = power

    notifications = _build_notifications_block(form, existing.get("notifications"))
    if notifications:
        cfg["notifications"] = notifications

    # Backstop: never emit a key outside the allowed set. The service
    # re-checks this, but failing here gives a clearer message.
    unknown = set(cfg.keys()) - ALLOWED_DESIRED_CONFIG_KEYS
    if unknown:
        raise DeviceConfigFormError(
            f"Internal error: form produced unsupported keys {sorted(unknown)}."
        )
    return cfg


def _build_watchdog_block(
    form,
    *,
    prefix: str,
    int_subkeys: tuple[str, ...],
    targets_field: str | None = None,
    target_field: str | None = None,
) -> dict:
    """Shared builder for the `internet` and `device` watchdog blocks.

    `internet` carries a `targets` string list (one host per line);
    `device` carries a single `target` string. Both carry the int
    timer sub-keys."""
    block: dict = {}
    if targets_field is not None:
        raw = form.get(targets_field)
        raw = "" if raw is None else str(raw)
        targets = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if targets:
            block["targets"] = targets
    if target_field is not None:
        target = _form_str(form, target_field)
        if target is not None:
            block["target"] = target
    for sub in int_subkeys:
        val = _form_int(
            form, f"cfg_{prefix}_{sub}",
            f"{prefix.title()} {sub.replace('_', ' ')}",
        )
        if val is not None:
            block[sub] = val
    return block


def _build_notifications_block(form, existing_notifications) -> dict:
    """The `notifications` field-set. `webhook_auth_token` is write-only:
    a blank field keeps whatever was stored; a non-blank field replaces
    it (same masking trick as the SMTP password)."""
    block: dict = {}
    for sub in NOTIFICATIONS_BOOL_SUBKEYS:
        if (form.get(f"cfg_notifications_{sub}_present") or "").strip():
            block[sub] = _form_bool(form, f"cfg_notifications_{sub}")
    for sub in NOTIFICATIONS_STR_SUBKEYS:
        val = _form_str(form, f"cfg_notifications_{sub}")
        if val is not None:
            block[sub] = val
    # Write-only secret: blank → keep existing, non-blank → replace.
    token = (form.get(f"cfg_notifications_{NOTIFICATIONS_SECRET_SUBKEY}") or "").strip()
    if token:
        block[NOTIFICATIONS_SECRET_SUBKEY] = token
    elif isinstance(existing_notifications, dict) and existing_notifications.get(
        NOTIFICATIONS_SECRET_SUBKEY
    ):
        block[NOTIFICATIONS_SECRET_SUBKEY] = existing_notifications[
            NOTIFICATIONS_SECRET_SUBKEY
        ]
    return block


# ── inverse: desired_config dict → form-field values ────────────────────

def desired_config_to_form_values(cfg: dict | None) -> dict:
    """Flatten a stored `desired_config` into a `cfg_*`-keyed dict the
    template uses to pre-populate the structured form. The write-only
    `webhook_auth_token` is deliberately *not* echoed — the field
    renders empty and a blank save keeps it unchanged."""
    cfg = cfg or {}
    values: dict = {}

    if "device_name" in cfg:
        values["cfg_device_name"] = cfg["device_name"]
    if "relay_restore_behavior" in cfg:
        values["cfg_relay_restore_behavior"] = cfg["relay_restore_behavior"]
    if "monitor_interval_seconds" in cfg:
        values["cfg_monitor_interval_seconds"] = cfg["monitor_interval_seconds"]
    if "boot_warmup_seconds" in cfg:
        values["cfg_boot_warmup_seconds"] = cfg["boot_warmup_seconds"]
    if "manual_button_enabled" in cfg:
        values["cfg_manual_button_enabled"] = bool(cfg["manual_button_enabled"])

    internet = cfg.get("internet")
    if isinstance(internet, dict):
        if isinstance(internet.get("targets"), list):
            values["cfg_internet_targets"] = "\n".join(
                str(t) for t in internet["targets"]
            )
        for sub in INTERNET_INT_SUBKEYS:
            if sub in internet:
                values[f"cfg_internet_{sub}"] = internet[sub]

    device = cfg.get("device")
    if isinstance(device, dict):
        if "target" in device:
            values["cfg_device_target"] = device["target"]
        for sub in DEVICE_INT_SUBKEYS:
            if sub in device:
                values[f"cfg_device_{sub}"] = device[sub]

    power = cfg.get("power")
    if isinstance(power, dict):
        if "enabled" in power:
            values["cfg_power_enabled"] = bool(power["enabled"])
        for sub in POWER_INT_SUBKEYS:
            if sub in power:
                values[f"cfg_power_{sub}"] = power[sub]

    notifications = cfg.get("notifications")
    if isinstance(notifications, dict):
        for sub in NOTIFICATIONS_BOOL_SUBKEYS:
            if sub in notifications:
                values[f"cfg_notifications_{sub}"] = bool(notifications[sub])
        for sub in NOTIFICATIONS_STR_SUBKEYS:
            if sub in notifications:
                values[f"cfg_notifications_{sub}"] = notifications[sub]
        # webhook_auth_token intentionally omitted (write-only secret).

    return values


# ── round-trip gate ─────────────────────────────────────────────────────

def is_form_representable(cfg: dict | None) -> bool:
    """True when the structured form can edit `cfg` without data loss.

    The form is a strict subset of the JSON editor. If the stored config
    carries an unknown top-level key, a non-dict object block, or a
    nested sub-key the structured form doesn't surface, the device-detail
    page must fall back to JSON-only — never let a structured save
    silently drop a key the JSON had.
    """
    if not cfg:
        return True  # empty config → form starts blank, nothing to lose
    if not isinstance(cfg, dict):
        return False

    if set(cfg.keys()) - ALLOWED_DESIRED_CONFIG_KEYS:
        return False

    # Scalar keys: any JSON-typed value is fine — the form coerces.
    # Object keys must be dicts whose sub-keys the form fully surfaces.
    representable_subkeys = {
        "internet": set(INTERNET_INT_SUBKEYS) | {"targets"},
        "device": set(DEVICE_INT_SUBKEYS) | {"target"},
        "power": set(POWER_INT_SUBKEYS) | {"enabled"},
        "notifications": (
            set(NOTIFICATIONS_BOOL_SUBKEYS)
            | set(NOTIFICATIONS_STR_SUBKEYS)
            | {NOTIFICATIONS_SECRET_SUBKEY}
        ),
    }
    for key, allowed_subkeys in representable_subkeys.items():
        block = cfg.get(key)
        if block is None:
            continue
        if not isinstance(block, dict):
            return False
        if set(block.keys()) - allowed_subkeys:
            return False
        # `internet.targets` must be a flat list of scalars (one per line).
        if key == "internet" and "targets" in block:
            tg = block["targets"]
            if not isinstance(tg, list) or any(
                isinstance(t, (dict, list)) for t in tg
            ):
                return False

    return True
