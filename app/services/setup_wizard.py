"""First-run setup wizard + 3-mode picker service — Tier-2 Feature 1.

This module is the *translator* half of the setup wizard: it turns the
plain-language answers a non-technical operator gives ("keep my internet
alive", "restart this when it locks up", "just a smart switch") into the
two artefacts the rest of the hub already understands:

  - a `desired_config` dict   → `device_config.set_desired_config()`
  - a watchdog `rule_payload` → `watchdog.create_rule()`

Per the Tier-2 design (Feature 1), the picker adds **no new persistence
logic** — it is a pure function layer. The three `apply_*` functions are
deliberately I/O-free so they are independently unit-testable; the
blueprint route calls them, then hands the results to the two existing
services. `apply_picker()` dispatches on the chosen mode.

The three modes (design §"The three modes in plain language"):

  smart_plug        — `relay_restore_behavior` (+ optional schedule);
                      NO watchdog rule.
  internet_watchdog — a `desired_config.internet` block AND a hub-side
                      `internet` watchdog rule with a `cycle` action.
  device_watchdog   — a `desired_config.device` block AND a hub-side
                      `ping`/`tcp`/`http` watchdog rule with `cycle`.

Re-running the picker on a device must replace its own prior rule rather
than duplicate it — every generated rule carries a stable, predictable
`name` and the `RULE_MARKER` description so the route can detect and
delete the previous one. `find_prior_wizard_rule()` does that lookup.
"""

from __future__ import annotations

from typing import Any

# Marker stamped on the description of every wizard-generated rule so a
# re-run of the picker can find + replace its own prior rule instead of
# stacking duplicates (design §Approach).
RULE_MARKER = "Created by the setup wizard"

VALID_MODES = ("smart_plug", "internet_watchdog", "device_watchdog")

# Default ping targets for internet mode when the operator accepts the
# "check it for me" default (design §"how do you want us to check?").
DEFAULT_INTERNET_TARGETS = ("1.1.1.1", "8.8.8.8")

# Advanced power-cycle timers — sensible defaults that live behind the
# "Fine-tune" disclosure. Shared by both watchdog modes.
DEFAULT_POWER_OFF_SECONDS = 5
DEFAULT_POST_REBOOT_HOLDOFF_SECONDS = 180
DEFAULT_COOLDOWN_SECONDS = 600
DEFAULT_MAX_CYCLES_PER_HOUR = 6

# How "how long offline before we restart?" maps onto the rule engine.
# The plain-language answer is a tolerance in seconds; the rule probes
# every WINDOW_SECONDS and fires after FAILURE_THRESHOLD consecutive
# failures. We pick a fixed probe window and derive the threshold so the
# operator's seconds-answer is honoured (rounded up, min 1 probe).
PROBE_WINDOW_SECONDS = 30
DEFAULT_OFFLINE_TOLERANCE_SECONDS = 180


class SetupWizardError(ValueError):
    """A plain-language answer could not be translated into a valid
    config / rule. The blueprint catches this and flashes the message."""


# ── helpers ─────────────────────────────────────────────────────────────

def _coerce_int(value: Any, label: str, *, default: int,
                lo: int, hi: int) -> int:
    """Coerce an optional fine-tune answer to a bounded int. Blank /
    missing → `default`. Out-of-range or non-numeric → SetupWizardError
    with an operator-friendly message."""
    if value is None or str(value).strip() == "":
        return default
    try:
        v = int(str(value).strip())
    except (TypeError, ValueError):
        raise SetupWizardError(
            f"{label} must be a whole number (got {value!r})."
        ) from None
    if v < lo or v > hi:
        raise SetupWizardError(
            f"{label} must be between {lo} and {hi} (got {v})."
        )
    return v


def _clean_name(answers: dict, *, required: bool = True) -> str:
    """The friendly device name every mode collects."""
    name = str(answers.get("device_name") or "").strip()
    if not name and required:
        raise SetupWizardError("Please give the device a friendly name.")
    if len(name) > 120:
        raise SetupWizardError("The device name must be 120 characters or fewer.")
    return name


def _tolerance_to_threshold(tolerance_seconds: int) -> int:
    """Map an offline-tolerance in seconds onto a `failure_threshold`
    given the fixed `PROBE_WINDOW_SECONDS` probe cadence. Always ≥ 1 so
    the rule actually fires."""
    return max(1, -(-int(tolerance_seconds) // PROBE_WINDOW_SECONDS))


def _wizard_rule_name(device_id: str, mode: str) -> str:
    """A stable, predictable rule name so a picker re-run can find and
    replace its own prior rule. Includes the device id + mode."""
    suffix = "internet" if mode == "internet_watchdog" else "device"
    return f"Setup wizard — {suffix} watchdog for {device_id}"


def _cycle_action(answers: dict) -> dict:
    """The `cycle` (power-cycle) leaf action both watchdog modes use.
    `power_off_seconds` is the only per-action fine-tune timer."""
    return {
        "kind": "cycle",
        "power_off_seconds": _coerce_int(
            answers.get("power_off_seconds"),
            "Power-off duration",
            default=DEFAULT_POWER_OFF_SECONDS, lo=1, hi=600,
        ),
    }


def _watchdog_timers(answers: dict) -> dict:
    """The advanced power-cycle timers shared by both watchdog modes —
    they go into the `desired_config` block, behind the "Fine-tune"
    disclosure in the UI."""
    return {
        "power_off_seconds": _coerce_int(
            answers.get("power_off_seconds"), "Power-off duration",
            default=DEFAULT_POWER_OFF_SECONDS, lo=1, hi=600,
        ),
        "post_reboot_holdoff_seconds": _coerce_int(
            answers.get("post_reboot_holdoff_seconds"),
            "Post-reboot hold-off",
            default=DEFAULT_POST_REBOOT_HOLDOFF_SECONDS, lo=0, hi=3600,
        ),
        "cooldown_seconds": _coerce_int(
            answers.get("cooldown_seconds"), "Cooldown",
            default=DEFAULT_COOLDOWN_SECONDS, lo=0, hi=86400,
        ),
        "max_cycles_per_hour": _coerce_int(
            answers.get("max_cycles_per_hour"), "Max cycles per hour",
            default=DEFAULT_MAX_CYCLES_PER_HOUR, lo=1, hi=60,
        ),
    }


# ── mode 1: just a smart switch ─────────────────────────────────────────

def apply_smart_plug(device_id: str, answers: dict) -> dict:
    """"Just a smart switch" — turn power on/off by hand / on a schedule.

    Generates a `desired_config` with `device_name` + `relay_restore_behavior`
    and NO watchdog rule (design §table). `relay_restore_behavior` answers
    "what should it do after a power outage?" — one of
    `restore_previous` / `always_on` / `always_off`.

    Returns `{"desired_mode", "desired_config", "rule_payload": None}`.
    """
    name = _clean_name(answers)
    behavior = str(answers.get("relay_restore_behavior") or "restore_previous").strip()
    if behavior not in ("restore_previous", "always_on", "always_off"):
        raise SetupWizardError(
            "Power-restore behaviour must be one of "
            "'restore_previous', 'always_on' or 'always_off'."
        )
    desired_config: dict = {
        "device_name": name,
        "relay_restore_behavior": behavior,
    }
    return {
        "desired_mode": "smart_plug",
        "desired_config": desired_config,
        "rule_payload": None,
    }


# ── mode 2: keep my internet alive ──────────────────────────────────────

def apply_internet_watchdog(device_id: str, answers: dict) -> dict:
    """"Keep my internet alive" — restart the modem/router when the
    internet drops.

    Generates a `desired_config.internet` block AND a hub-side `internet`
    watchdog rule targeting this device with a `cycle` action — the
    dependable half of the mode (design §Risks: the hub-side rule works
    regardless of firmware support).

    Plain-language answers:
      - `internet_targets` — newline/comma list of hosts to ping;
        blank → the DEFAULT_INTERNET_TARGETS pair.
      - `offline_tolerance_seconds` — "how long offline before we
        restart?" → `failure_threshold` × `PROBE_WINDOW_SECONDS`.
      - fine-tune timers (power_off / holdoff / cooldown / max-cycles).
    """
    name = _clean_name(answers)
    targets = _parse_targets(answers.get("internet_targets"))
    if not targets:
        targets = list(DEFAULT_INTERNET_TARGETS)
    if len(targets) > 10:
        raise SetupWizardError(
            "Please list at most 10 things to check for internet connectivity."
        )

    tolerance = _coerce_int(
        answers.get("offline_tolerance_seconds"),
        "Offline tolerance",
        default=DEFAULT_OFFLINE_TOLERANCE_SECONDS, lo=PROBE_WINDOW_SECONDS,
        hi=3600,
    )
    threshold = _tolerance_to_threshold(tolerance)
    timers = _watchdog_timers(answers)

    desired_config: dict = {
        "device_name": name,
        "internet": {
            "targets": targets,
            "failure_threshold_seconds": tolerance,
            **timers,
        },
    }

    rule_payload = {
        "name": _wizard_rule_name(device_id, "internet_watchdog"),
        "description": RULE_MARKER,
        "probe": {
            "kind": "internet",
            "targets": [{"host": h, "port": 53} for h in targets],
        },
        "target": {"kind": "device", "id": device_id},
        "action": _cycle_action(answers),
        "failure_threshold": threshold,
        "window_seconds": PROBE_WINDOW_SECONDS,
        "cooldown_seconds": timers["cooldown_seconds"],
        "max_cycles_per_hour": timers["max_cycles_per_hour"],
    }
    return {
        "desired_mode": "internet_watchdog",
        "desired_config": desired_config,
        "rule_payload": rule_payload,
    }


# ── mode 3: restart one device when it locks up ─────────────────────────

def apply_device_watchdog(device_id: str, answers: dict) -> dict:
    """"Restart one device when it locks up" — watch a single device and
    power-cycle it.

    Generates a `desired_config.device` block AND a hub-side
    `ping`/`tcp`/`http` watchdog rule with a `cycle` action.

    Plain-language answers:
      - `watch_address` — "what's the address of the thing to watch?"
        A bare host → `ping` probe; `host:port` → `tcp`; an http(s) URL
        → `http`.
      - `offline_tolerance_seconds` + the fine-tune timers, as in
        internet mode.
    """
    name = _clean_name(answers)
    address = str(answers.get("watch_address") or "").strip()
    if not address:
        raise SetupWizardError(
            "Please tell us the address of the device to watch "
            "(an IP, a hostname, or a web address)."
        )

    probe = _address_to_probe(address)

    tolerance = _coerce_int(
        answers.get("offline_tolerance_seconds"),
        "Offline tolerance",
        default=DEFAULT_OFFLINE_TOLERANCE_SECONDS, lo=PROBE_WINDOW_SECONDS,
        hi=3600,
    )
    threshold = _tolerance_to_threshold(tolerance)
    timers = _watchdog_timers(answers)

    desired_config: dict = {
        "device_name": name,
        "device": {
            "target": address,
            "failure_threshold_seconds": tolerance,
            **timers,
        },
    }

    rule_payload = {
        "name": _wizard_rule_name(device_id, "device_watchdog"),
        "description": RULE_MARKER,
        "probe": probe,
        "target": {"kind": "device", "id": device_id},
        "action": _cycle_action(answers),
        "failure_threshold": threshold,
        "window_seconds": PROBE_WINDOW_SECONDS,
        "cooldown_seconds": timers["cooldown_seconds"],
        "max_cycles_per_hour": timers["max_cycles_per_hour"],
    }
    return {
        "desired_mode": "device_watchdog",
        "desired_config": desired_config,
        "rule_payload": rule_payload,
    }


def _parse_targets(raw: Any) -> list[str]:
    """Split a free-text targets answer (one per line, or comma-
    separated) into a clean host list."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        text = str(raw).replace(",", "\n")
        items = text.splitlines()
    return [t.strip() for t in items if t.strip()]


def _address_to_probe(address: str) -> dict:
    """Turn the device-mode "address of the thing to watch" answer into
    a watchdog probe dict: http(s) URL → `http`; `host:port` → `tcp`;
    a bare host → `ping`."""
    low = address.lower()
    if low.startswith(("http://", "https://")):
        return {"kind": "http", "url": address}
    # host:port → tcp. Guard against IPv6 literals (multiple colons) and
    # a trailing-colon typo.
    if address.count(":") == 1:
        host, _, port_s = address.partition(":")
        host = host.strip()
        port_s = port_s.strip()
        if host and port_s:
            try:
                port = int(port_s)
            except ValueError:
                raise SetupWizardError(
                    f"The port in {address!r} must be a number."
                ) from None
            if port < 1 or port > 65535:
                raise SetupWizardError(
                    f"The port in {address!r} must be between 1 and 65535."
                )
            return {"kind": "tcp", "host": host, "port": port}
    return {"kind": "ping", "host": address}


# ── dispatcher ──────────────────────────────────────────────────────────

def apply_picker(device_id: str, mode: str, answers: dict) -> dict:
    """Dispatch the chosen mode to its `apply_*` function.

    Returns the same `{"desired_mode", "desired_config", "rule_payload"}`
    shape. The blueprint route consumes that: `set_desired_config()` with
    the config + mode, then `create_rule()` if `rule_payload` is not None.
    """
    if mode == "smart_plug":
        return apply_smart_plug(device_id, answers)
    if mode == "internet_watchdog":
        return apply_internet_watchdog(device_id, answers)
    if mode == "device_watchdog":
        return apply_device_watchdog(device_id, answers)
    raise SetupWizardError(
        f"Unknown setup mode {mode!r} — pick one of "
        "'just a smart switch', 'keep my internet alive', or "
        "'restart one device when it locks up'."
    )


def find_prior_wizard_rule(device_id: str, mode: str) -> str | None:
    """Look up this device's prior wizard-generated rule (if any) so a
    picker re-run can delete + replace it rather than stacking a
    duplicate. Matches on the predictable `name` + the `RULE_MARKER`
    description. Returns the rule id or None.

    The lookup is DB-backed — kept out of the pure `apply_*` functions so
    those stay I/O-free and unit-testable on their own.
    """
    from app.services.watchdog import list_rules

    want_name = _wizard_rule_name(device_id, mode)
    for rule in list_rules():
        if (
            rule.get("name") == want_name
            and (rule.get("description") or "") == RULE_MARKER
        ):
            return rule.get("id")
    return None
