# Device Integration Guide (firmware-team handoff)

This document is for the firmware team building the Sonoff S31-based
Rebooter unit. It explains exactly what the device should do to talk to
the central server.

The central server is optional — your device must continue to operate as a
local-first appliance even when the server is unreachable. Central
coordination is a strict additive layer.

## Server contract

- Base URL: `https://www.voipguru.org/rebooter/api/v1`
- Firmware downloads: `https://www.voipguru.org/rebooter/firmware/<file>.bin`
- Content-Type: `application/json` for all API calls.
- TLS is required.

The server returns a stable envelope:

```
{ "ok": true,  "data": {…} }
{ "ok": false, "error": { "code": "<machine_code>", "message": "<human>" } }
```

## Device-side config (required)

The device should expose these settings (via the local web UI or CLI):

| Setting | Example |
|---|---|
| `central_enabled` | `true` |
| `central_base_url` | `https://www.voipguru.org/rebooter` |
| `central_enrollment_token` | `et_xxxxxxxxxxxxxxxxxxxx` |
| `central_device_alias` | `Branch 14 Router` |
| `central_site_id` | optional |
| `central_poll_interval_seconds` | `30` |
| `central_heartbeat_interval_seconds` | `60` |

`central_enabled=false` must completely disable all outbound central
traffic.

## Lifecycle

### 1. Registration (one-time)

Once the user enters an enrollment token and saves it, call:

```
POST /api/v1/device/register
Content-Type: application/json
{
  "enrollment_token": "<paste>",
  "hardware_model": "sonoff_s31",
  "hardware_revision": "v1.0",
  "firmware_version": "<your fw>",
  "mac_address": "<colon-separated>",
  "display_name": "<central_device_alias>",
  "local_ip": "<lan ip>",
  "capabilities": {
    "local_web_ui": true,
    "local_ota": true,
    "internet_watchdog": true,
    "device_watchdog": true,
    "relay_control": true
  }
}
```

On success (`201`), persist:

```
device_id        ← from data.device_id
device_token     ← from data.device_token   (write once, never log)
```

The `device_token` is shown ONCE. Store it in NVS / encrypted flash; the
server only stores a SHA-256 hash and cannot recover it.

If the response is:

- `409 enrollment_consumed` → token already redeemed; ask the user for a
  fresh one.
- `410 enrollment_expired` → token is past TTL; ask the user for a fresh
  one.
- network failure → retry with exponential backoff (cap at 5 minutes).

After registration, set every subsequent request's
`Authorization: Bearer <device_token>`.

### 2. Heartbeat (every `heartbeat_interval_seconds`)

```
POST /api/v1/device/heartbeat
Authorization: Bearer <device_token>
{
  "device_id": "<dev_…>",
  "firmware_version": "0.1.0",
  "local_ip": "192.168.1.67",
  "mode": "smart_plug",
  "relay_on": true,
  "wifi_connected": true,
  "health_state": "healthy",
  "uptime_seconds": 8640,
  "incident_cycles": 0,
  "hour_cycles": 0,
  "last_event_type": "boot",
  "last_event_at": "<iso8601>"
}
```

The response includes the suggested next intervals; honour them but cap
poll cadence to a hard local minimum (e.g. 5 s) to protect the server.

### 3. Command poll (every `poll_interval_seconds`)

```
GET /api/v1/device/commands
Authorization: Bearer <device_token>
```

Response:

```
{
  "ok": true,
  "data": {
    "commands": [
      { "command_id":"cmd_…", "type":"relay_cycle", "expires_at":"…",
        "payload": { "power_off_seconds": 5, "post_reboot_holdoff_seconds": 180 } }
    ]
  }
}
```

For each returned command:

1. Acknowledge with status `accepted` (optional but recommended).
2. Execute. For `relay_cycle`: open the relay for `power_off_seconds`,
   close it, then ignore further `relay_cycle` commands for
   `post_reboot_holdoff_seconds`.
3. Report the final result.

```
POST /api/v1/device/command-result
Authorization: Bearer <device_token>
{
  "device_id": "<dev_…>",
  "command_id": "<cmd_…>",
  "status": "completed",
  "completed_at": "<iso8601>",
  "message": "Relay cycle ok",
  "result": { "relay_on": true }
}
```

If the command's `expires_at` has passed before you act on it, drop it and
report `status: expired`.

### 4. Event upload (when something interesting happens)

Batch events; send up to 200 per request. Buffer locally if the server is
unreachable and retry later — this is best-effort.

```
POST /api/v1/device/events
Authorization: Bearer <device_token>
{
  "device_id": "<dev_…>",
  "events": [
    {
      "type": "watchdog_trigger",
      "timestamp": "<iso8601>",
      "message": "All targets failed",
      "mode": "internet_watchdog",
      "details": { … free-form JSON … }
    }
  ]
}
```

### 5. Firmware check

Poll on a slower cadence (e.g. once an hour) and on every boot:

```
GET /api/v1/device/firmware
Authorization: Bearer <device_token>
```

Two shapes:

- `{ ok:true, data:{ assigned:false } }` — nothing to do.
- `{ ok:true, data:{ assigned:true, channel, target_version,
                     download_url, sha256, force } }` — work to do.

Behaviour when `assigned: true`:

1. Compare `target_version` with current firmware. If they match and
   `force=false`, do nothing.
2. Download `download_url` (no auth header needed; just HTTPS GET).
3. Compute SHA-256 of the download. **Refuse to flash if it does not match
   `sha256`.** Log a `firmware_sha256_mismatch` event and abort.
4. Apply the OTA update. Survive a boot loop by retaining the prior image
   if the device fails to come up cleanly.
5. After a successful boot on the new firmware, the next heartbeat will
   pick up the new `firmware_version`. The server tracks rollout progress
   from there.

## Resilience requirements

- All central calls must time out within ~30 seconds and never block local
  control.
- Backoff: exponential, capped at 5 minutes between retries.
- The device MUST work locally with the central server fully unreachable.
- The device MUST work locally with the WAN down.
- Setting `central_enabled=false` MUST stop all outbound central traffic.

## Common error codes you will encounter

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `validation_failed` | malformed body |
| 400 | `device_mismatch` | `device_id` in payload ≠ bearer's device |
| 401 | `auth_invalid` | token revoked or unknown |
| 404 | `command_unknown` | reporting on a command that doesn't exist for you |
| 409 | `enrollment_consumed` | enrollment token already used |
| 410 | `enrollment_expired` | enrollment token past TTL |
| 500 | `internal_error` | server bug — log + retry later |

## Locked command schemas (v0.1, agreed with firmware/design team 2026-05-09)

The backend validates payloads for these command types and rejects
malformed admin requests with `validation_failed`. Devices can therefore
trust that any `set_mode` / `apply_config` they receive is well-formed.

### `set_mode`

```json
{ "mode": "smart_plug" }
```

`mode` must be one of:

- `smart_plug`
- `internet_watchdog`
- `device_watchdog`

### `apply_config`

Partial-update semantics. The backend accepts only these top-level keys
and rejects any payload that contains unknown keys:

- `device_name`
- `relay_restore_behavior`
- `monitor_interval_seconds`
- `boot_warmup_seconds`
- `manual_button_enabled`
- `internet`
- `device`
- `notifications`

Field ranges and constraints inside each section are documented in the
firmware team's `CENTRAL_SERVER_SPEC.md` /
`DEVICE_CENTRAL_INTEGRATION_NOTES.md`. The device should ignore unknown
sub-keys it doesn't understand and log a `apply_config_unknown_key` event
so the admin can see drift between firmware and config schema.

Local admin credentials are explicitly **out of scope** for central
`apply_config` in v0.1.

## Test endpoint

`GET /api/v1/version` returns the running build version of the server and
needs no auth. Useful for health probes during integration.
