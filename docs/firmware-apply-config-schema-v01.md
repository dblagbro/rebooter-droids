# Firmware `apply_config` schema — hub-side reconciled reference

> **Status:** reconciled 2026-05-15 (v0.5.53, P0.3 / Phase 4C) against the
> firmware team's source-backed notes for the `0.1.20-dev-central-safe`
> line. This file is the **hub-side** mirror; the firmware team owns the
> upstream contract. Upstream sources of truth:
>
> - `docs/notes/2026-05-14-firmware-config-and-reported-schema.md` — the
>   real `apply_config` / local-save / `reported_config` surfaces.
> - `docs/notes/2026-05-14-firmware-status-and-recovery-contract.md` — the
>   status / recovery / central-state heartbeat contract.
>
> The pre-2026-05-15 version of this file described an aspirational schema
> (Wi-Fi credentials under `internet`, MQTT under `notifications`,
> `device.boot_mode`/`led_brightness`/`timezone`) that the real firmware
> never implemented. Those tables have been corrected below.

## What this doc reconciles

The hub stores operator intent in `devices.desired_config` and pushes it
as an `apply_config` command (`app/services/device_config.py`). The set of
top-level keys the hub accepts is `ALLOWED_DESIRED_CONFIG_KEYS` in that
module. This doc records what each of those keys means and **how far it is
actually validated end-to-end**.

## Command type

```
{ "type": "apply_config", "payload": { ... } }
```

Sent through the standard `commands` queue. Result returns via
`/device/commands/result` with `ok: true|false` and either
`applied_fields: [...]` on success or `error: "..."` on failure. All
payload keys are individually optional; the device applies the keys
present and ignores unknown keys (forward-compat).

## Top-level keys — `ALLOWED_DESIRED_CONFIG_KEYS` ↔ firmware support

`ALLOWED_DESIRED_CONFIG_KEYS` (hub) and the firmware's `apply_config`
top-level keys (per the 2026-05-14 firmware note §3) are **in agreement** —
the hub does not accept any key the firmware cannot parse.

| Key | Type | Hub→firmware support tier |
|---|---|---|
| `device_name` | string | **Validated end-to-end.** Exercised by the restore-after-reflash push (`enrollment.py`) and confirmed in drift round-trip (push → device echoes it in `reported_config` → hub computes `in_sync`). |
| `relay_restore_behavior` | string (`restore_previous`/`always_on`/`always_off`) | Accepted by firmware `apply_config`; hub drift round-trip not yet individually validated. |
| `monitor_interval_seconds` | int | Accepted by firmware `apply_config`; round-trip not yet individually validated. |
| `boot_warmup_seconds` | int | Accepted by firmware `apply_config`; round-trip not yet individually validated. |
| `manual_button_enabled` | bool | Accepted by firmware `apply_config`; round-trip not yet individually validated. |
| `internet` | object | Accepted — watchdog targets/timers (see below). Round-trip not yet validated. |
| `device` | object | Accepted — watchdog target/timers (see below). Round-trip not yet validated. |
| `notifications` | object | Accepted — webhook-oriented (see below). Round-trip not yet validated. |
| `power` | object | Accepted — telemetry config (see below). Round-trip not yet validated. |

**Support tiers defined:**

- **Validated end-to-end** — the hub has confirmed the full loop: the key
  is pushed, the firmware applies it, the device echoes it in heartbeat
  `reported_config`, and the hub's drift computation marks it `in_sync`.
- **Accepted** — the firmware's `apply_config` handler parses the key (per
  the firmware team's source-backed note) and the hub's
  `ALLOWED_DESIRED_CONFIG_KEYS` permits it, but the hub has not yet
  individually verified the drift round-trip. Pushes work; drift detection
  for the key depends on the firmware including it in `reported_config`
  — see the open firmware ask below.

### `internet` subkeys — watchdog (NOT Wi-Fi credentials)

The real firmware uses `internet` for internet-watchdog target/timer
tuning. (The old doc's Wi-Fi-credential table was wrong.)

| Key | Type |
|---|---|
| `targets` | string[] |
| `failure_threshold_seconds` | int |
| `power_off_seconds` | int |
| `post_reboot_holdoff_seconds` | int |
| `max_cycles_per_incident` | int |
| `max_cycles_per_hour` | int |
| `cooldown_seconds` | int |
| `dns_refresh_seconds` | int |
| `recovery_stability_seconds` | int |

### `device` subkeys — device-watchdog (NOT boot_mode/LED/timezone)

| Key | Type |
|---|---|
| `target` | string |
| `failure_threshold_seconds` | int |
| `power_off_seconds` | int |
| `post_reboot_holdoff_seconds` | int |
| `max_cycles_per_incident` | int |
| `max_cycles_per_hour` | int |
| `cooldown_seconds` | int |
| `recovery_stability_seconds` | int |

### `notifications` subkeys — webhook-oriented (NOT MQTT)

`apply_config` parses a wider notification set than local config-save:

| Key | Type |
|---|---|
| `enabled` | bool |
| `type` | string |
| `webhook_url` | string |
| `webhook_method` | string |
| `webhook_auth_token` | string (write-only) |
| `send_on_trigger` | bool |
| `send_on_recovery` | bool |
| `send_on_max_cycles_reached` | bool |
| `send_test_notification_enabled` | bool |

### `power` subkeys — telemetry config

| Key | Type |
|---|---|
| `enabled` | bool |
| `sample_rate_hz` | int |
| `batch_seconds` | int |
| `include_wifi_stats` | bool |
| `include_frequency` | bool |

## Explicitly excluded from `apply_config`

The firmware deliberately does **not** apply these via central
`apply_config`; the hub must not place them in `desired_config`:

| Key | Reason |
|---|---|
| `central.*` | Central identity/config is not recursively rewritten by central management. |
| `admin_username` / `admin_password` | Local security boundary; not hub-driven. |
| `current_mode` | Handled by the separate `set_mode` command. |

`set_mode` payload: `{"mode": "on"|"off"|"toggle"|"cycle", "duration_seconds": 1-300}`
(`duration_seconds` required only for `cycle`).

## Drift detection ↔ `reported_config`

Drift (`device_config.py::compute_drift`) compares `desired_config` against
the heartbeat-echoed `last_reported_config`. The firmware emits
`reported_config` from `0.1.19-dev-central-safe`+ as a non-secret subset:
`device_name`, `relay_restore_behavior`, `monitor_interval_seconds`,
`boot_warmup_seconds`, `manual_button_enabled`, `internet`, `device`,
`notifications` (non-secret), `power`. Secrets (`webhook_auth_token`,
`central.device_token`, admin credentials) are excluded by design.

## Open firmware ask (gates promoting keys to "validated end-to-end")

Per the hub-team plan (`docs/notes/2026-05-15-hub-team-status-sync-and-plan.md`
§8, firmware ask #6): the firmware team should confirm, key by key, which
`reported_config` keys are **honored and echoed end-to-end** vs. merely
accepted by the `apply_config` parser. Until that confirmation lands, only
`device_name` is promoted to "validated end-to-end" in the table above;
the rest stay "accepted." Auto-push paths (restore-after-reflash, the
Phase 4B recovery re-push) remain gated behind the `desired_config.enabled`
feature flag for that reason.
