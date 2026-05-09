# API Reference (v0.1)

All endpoints below are rooted at `https://www.voipguru.org/rebooter/api/v1`.

## Conventions

- Request/response: `application/json`
- All responses use the envelope `{ "ok": true|false, "data": {…} }` or
  `{ "ok": false, "error": { "code", "message" } }`.
- Timestamps: ISO 8601 UTC, e.g. `2026-05-08T22:15:04Z`.
- IDs: ULID with type prefix — e.g. `dev_01KR58D15JMR…`, `cmd_01KR58FN…`.

## Auth (admin/mobile)

### `POST /auth/login`

Request:

```json
{ "email": "admin@example.com", "password": "…" }
```

Response 200:

```json
{
  "ok": true,
  "data": {
    "user": { "id": "usr_…", "email": "…", "display_name": "…" },
    "access_token": "<JWT, 8h>",
    "refresh_token": "<JWT, 14d>",
    "token_type": "Bearer"
  }
}
```

Errors: `auth_invalid` (401), `validation_failed` (400).

### `POST /auth/logout`

Clears the session cookie. No body.

### `POST /auth/refresh`

Request:

```json
{ "refresh_token": "…" }
```

Response 200: a fresh `access_token` + `refresh_token` pair.

### `GET /auth/me`

Header: `Authorization: Bearer <access_token>` (or session cookie).

Response 200:

```json
{ "ok": true, "data": { "id":"usr_…", "email":"…", "display_name":"…", "is_admin": true } }
```

## Device API (Bearer `<device_token>` required, except `/register`)

### `POST /device/register`

Request:

```json
{
  "enrollment_token": "et_…",
  "hardware_model": "sonoff_s31",
  "hardware_revision": "v1.0",
  "firmware_version": "0.1.0",
  "mac_address": "c4:d8:d5:0c:f6:b3",
  "display_name": "Router Rebooter 01",
  "local_ip": "192.168.1.67",
  "capabilities": {
    "local_web_ui": true,
    "local_ota": true,
    "internet_watchdog": true,
    "device_watchdog": true,
    "relay_control": true
  }
}
```

Response 201:

```json
{
  "ok": true,
  "data": {
    "device_id": "dev_…",
    "device_token": "dt_<long secret>",
    "poll_interval_seconds": 30,
    "heartbeat_interval_seconds": 60,
    "server_time": "2026-05-08T22:15:04Z"
  }
}
```

Errors: `enrollment_invalid` (400), `enrollment_consumed` (409),
`enrollment_expired` (410), `validation_failed` (400).

The `device_token` is only shown once. Store it durably on the device. The
server only stores a SHA-256 hash.

### `POST /device/heartbeat`

Headers: `Authorization: Bearer <device_token>`.

Request:

```json
{
  "device_id": "dev_…",
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
  "last_event_at": "2026-05-08T22:13:00Z"
}
```

Response 200:

```json
{ "ok": true, "data": { "next_poll_after_seconds": 30, "next_heartbeat_after_seconds": 60 } }
```

### `GET /device/commands`

Headers: `Authorization: Bearer <device_token>`.

Response 200:

```json
{
  "ok": true,
  "data": {
    "commands": [
      {
        "command_id": "cmd_…",
        "type": "relay_cycle",
        "created_at": "2026-05-08T22:18:00Z",
        "expires_at": "2026-05-08T22:28:00Z",
        "payload": { "power_off_seconds": 5, "post_reboot_holdoff_seconds": 180 }
      }
    ]
  }
}
```

Each pending command is returned once and is marked `accepted` server-side
upon delivery. Re-polling will re-deliver `accepted` (not yet completed)
commands until they expire or are reported.

### `POST /device/command-result`

Headers: `Authorization: Bearer <device_token>`.

Request:

```json
{
  "device_id": "dev_…",
  "command_id": "cmd_…",
  "status": "completed",
  "completed_at": "2026-05-08T22:19:00Z",
  "message": "Relay cycle completed",
  "result": { "relay_on": true }
}
```

Allowed `status`: `accepted`, `running`, `completed`, `failed`, `expired`.

### `POST /device/events`

Headers: `Authorization: Bearer <device_token>`.

Request:

```json
{
  "device_id": "dev_…",
  "events": [
    {
      "type": "watchdog_trigger",
      "timestamp": "2026-05-08T22:20:00Z",
      "message": "All targets failed",
      "mode": "internet_watchdog",
      "details": { "targets_failed": ["1.1.1.1","8.8.8.8"], "cycle_number": 1 }
    }
  ]
}
```

Up to 200 events per batch.

### `GET /device/firmware`

Headers: `Authorization: Bearer <device_token>`.

Response 200 (no rollout assigned):

```json
{ "ok": true, "data": { "assigned": false } }
```

Response 200 (rollout assigned):

```json
{
  "ok": true,
  "data": {
    "assigned": true,
    "channel": "dev",
    "target_version": "0.1.2",
    "download_url": "https://www.voipguru.org/rebooter/firmware/rebooter-0.1.2-dev.bin",
    "sha256": "<hex>",
    "force": false
  }
}
```

The download URL is an unauthenticated direct file download. Verify the
SHA-256 of the downloaded blob against the value returned here before
flashing.

## Admin API (Bearer admin JWT or session cookie)

### Devices

- `GET /admin/devices` — query: `site_id`, `group_id`, `status`, `search`.
- `GET /admin/devices/{device_id}` — full detail (latest heartbeat, groups,
  recent events, pending commands).
- `PATCH /admin/devices/{device_id}` — body fields:
  `display_name`, `site_id`, `notes`, `central_management_enabled`.
- `POST /admin/devices/{device_id}/commands` — body:
  `{ "type":"relay_on", "payload":{}, "ttl_seconds":600 }`.

  Payloads are validated server-side per the command type. Locked schemas
  (v0.1, see `docs/DEVICE_INTEGRATION.md`):

  - `set_mode`: `{ "mode": "smart_plug" | "internet_watchdog" | "device_watchdog" }`.
  - `apply_config`: partial-update object whose top-level keys are a
    subset of `device_name, relay_restore_behavior,
    monitor_interval_seconds, boot_warmup_seconds, manual_button_enabled,
    internet, device, notifications`. Any other top-level key is
    rejected with `validation_failed`.
  - `relay_cycle`: optional integer `power_off_seconds` and
    `post_reboot_holdoff_seconds`.

### Enrollment tokens

- `POST /admin/enrollment-tokens` — body:
  `{ "site_id":"…", "display_name_hint":"…", "note":"…" }`.
  Returns a single-use `enrollment_token` (shown once).
- `GET /admin/enrollment-tokens` — list state of all tokens.

### Groups

- `GET /admin/groups`, `GET /admin/groups/{group_id}`
- `POST /admin/groups` — body: `{ "name":"…", "description":"…", "site_id":"…" }`
- `POST /admin/groups/{group_id}/members` — body: `{ "device_ids":["dev_…", …] }`
- `DELETE /admin/groups/{group_id}/members/{device_id}`
- `POST /admin/groups/{group_id}/commands` — body:
  `{ "type":"relay_cycle", "payload":{}, "ttl_seconds":600 }`. Fans out to
  every member as a per-device command row.

### Sites

- `GET /admin/sites`, `POST /admin/sites`, `DELETE /admin/sites/{site_id}`.

### Firmware

- `GET /admin/firmware/releases`
- `POST /admin/firmware/releases` — `multipart/form-data`:
  `version`, `channel` (`dev|beta|stable`), `sha256` (optional but
  verified), `release_notes`, `file=@firmware.bin`.
- `DELETE /admin/firmware/releases/{release_id}`

- `GET /admin/firmware/deployments`
- `POST /admin/firmware/deployments` — body:

  ```json
  {
    "release_id": "fwr_…",
    "target_type": "device|group|site|all_devices",
    "target_id": "<id when target_type != all_devices>",
    "channel": "dev",
    "force": false
  }
  ```

  Materializes one `deployment_assignment` row per matched device.
  Subsequent deployments to the same device supersede pending assignments.

### Events

- `GET /admin/events` — query params: `device_id`, `group_id`, `type`,
  `from` (ISO8601), `to` (ISO8601), `limit`.

## Errors (commonly returned codes)

| Code | When |
|------|------|
| `auth_required` | session/bearer missing or invalid |
| `auth_invalid` | bad creds, expired token, inactive user |
| `validation_failed` | payload was malformed or missing required fields |
| `device_unknown` | device id not found |
| `device_mismatch` | bearer device id ≠ payload device id |
| `command_unknown` | command id not found for device |
| `enrollment_invalid` / `enrollment_consumed` / `enrollment_expired` | enrollment token problems |
| `firmware_not_found` | unknown firmware release |
| `internal_error` | server-side bug — check logs |
