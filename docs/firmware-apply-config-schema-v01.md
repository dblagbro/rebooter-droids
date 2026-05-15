# Firmware `apply_config` schema — v0.1 (historical)

> Warning: this file is now **historical context**, not the current
> authoritative firmware truth. The current reconciled contract for the
> `0.1.18-dev-central-safe` line is:
>
> - `S:\code\rebooter-droids\docs\notes\2026-05-14-firmware-config-and-reported-schema.md`
> - `S:\code\rebooter-droids\docs\notes\2026-05-14-firmware-status-and-recovery-contract.md`
>
> This older file reflects an earlier planning model and no longer matches
> the real firmware in several places, especially around `internet`,
> `notifications`, and heartbeat `reported_config`.

Source: firmware team delivery 2026-05-13, captured here for B21
(`desired_config` blob + drift detection) reference. The hub-side
config editor MUST only surface fields the firmware actually honors
at the device's installed firmware version; everything else stays
greyed-out with a "shipped in fw >= X.Y.Z" hint.

This document is the canonical contract. Treat it as read-only —
the firmware team owns the schema, the hub side mirrors it.

## Command type

```
{
  "type": "apply_config",
  "payload": { ... }
}
```

Sent through the standard `commands` queue. Result returns via
`/device/commands/result` with `ok: true|false` and either
`applied_fields: [...]` on success or `error: "..."` on failure.

## Supported top-level keys

The post-local-patch firmware accepts these top-level keys in the
payload object. All fields are individually optional — the device
applies only the keys present and ignores unknown keys (forward-
compat).

| Key             | Type   | Notes                                                              |
|-----------------|--------|--------------------------------------------------------------------|
| `device_name`   | string | The unit's display name in its local web UI / MQTT-broadcast meta. |
| `internet`      | object | See [internet subkeys](#internet-subkeys).                         |
| `device`        | object | See [device subkeys](#device-subkeys).                             |
| `notifications` | object | See [notifications subkeys](#notifications-subkeys).               |

### internet subkeys

| Key            | Type    | Notes                                                |
|----------------|---------|------------------------------------------------------|
| `ssid`         | string  | WiFi SSID. Triggers reconnect on apply.              |
| `password`     | string  | WiFi password. Write-only — never returned by query. |
| `static_ip`    | string  | Optional. Empty/omitted = DHCP.                      |
| `gateway`      | string  | Required iff `static_ip` is set.                     |
| `subnet_mask`  | string  | Required iff `static_ip` is set.                     |
| `dns_primary`  | string  | Optional.                                            |
| `dns_secondary`| string  | Optional.                                            |

Applying `internet.*` causes a reconnect. The device will appear
offline for ~10–30 seconds while reassociating.

### device subkeys

| Key             | Type    | Notes                                                                  |
|-----------------|---------|------------------------------------------------------------------------|
| `boot_mode`     | string  | Enum: `on`, `off`, `last`. Power state on cold boot.                   |
| `led_brightness`| integer | 0–100. Status LED.                                                     |
| `timezone`      | string  | IANA TZ name, e.g. `America/New_York`.                                 |
| `ntp_server`    | string  | Hostname or IP.                                                        |

### notifications subkeys

| Key                  | Type    | Notes                                            |
|----------------------|---------|--------------------------------------------------|
| `mqtt_enabled`       | boolean | Master toggle for MQTT publishing.               |
| `mqtt_broker`        | string  | Hostname or IP.                                  |
| `mqtt_port`          | integer | Default 1883 / 8883.                             |
| `mqtt_username`      | string  |                                                  |
| `mqtt_password`      | string  | Write-only — never returned by query.            |
| `mqtt_topic_prefix`  | string  | e.g. `rebooter/lab/`.                            |
| `webhook_url`        | string  | POST destination for state-change events.        |
| `webhook_secret`     | string  | HMAC key for the webhook. Write-only.            |

## Companion command: `set_mode`

The schema also locks the contract for the existing `set_mode`
command (separate from `apply_config`, included here for the
hub-side editor's reference):

```
{
  "type": "set_mode",
  "payload": {
    "mode": "on" | "off" | "toggle" | "cycle",
    "duration_seconds": 5    // required only for cycle
  }
}
```

`cycle` performs an off-on power cycle of `duration_seconds` (1–300).
For `on`/`off`/`toggle` the `duration_seconds` field is ignored.

## v0.5.8 hub-side usage

v0.5.8 currently only emits `apply_config{device_name: <hub display_name>}`
on the restore-after-reflash path inside
`app/services/enrollment.py::consume_enrollment_token`. All other
schema fields are documented here for B21 implementation work and
are NOT yet surfaced in the hub UI.

## B21 (medium-term) requirements

When B21 lands:

1. `devices.desired_config` (JSONB) — the operator-edited blob,
   shaped exactly like the `apply_config` payload above.
2. `devices.last_reported_config` (JSONB) — last `apply_config`
   result echo from the device.
3. Operator-edit UI on `/app/devices/<id>` — surfaces ONLY the
   fields the device's installed firmware version honors. Older
   firmware versions get the unsupported fields greyed-out with
   the minimum-supported-firmware hint.
4. Drift detection — periodic compare of desired vs reported;
   surface diff on the device detail page.
5. Optional auto-repair-on-drift — feature-flagged; enqueues
   `apply_config` with the drift delta when enabled.

Field-by-field minimum-fw matrix to be populated as the firmware
team confirms each field's introduction version.
