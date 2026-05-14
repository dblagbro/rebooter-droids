# API Reference

Refreshed v0.4.27. All endpoints below are rooted at
`https://www.voipguru.org/rebooter/api/v1`.

## Conventions

- Request/response: `application/json`
- All responses use the envelope `{ "ok": true|false, "data": {…} }` or
  `{ "ok": false, "error": { "code", "message" } }`.
- Timestamps: ISO 8601 UTC, e.g. `2026-05-08T22:15:04Z`.
- IDs: ULID with type prefix — e.g. `dev_01KR58D15JMR…`, `cmd_01KR58FN…`.
- `GET /version` (unauthenticated) returns
  `{ "ok": true, "data": { "service": "rebooter-droids", "version":"0.4.27", "server_time":"…" } }`.

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

Errors: `auth_invalid` (401), `validation_failed` (400). Rate-limited
`30/minute, 200/hour` per IP.

### `POST /auth/logout`

Clears the session cookie and revokes the current session token
server-side. No body.

### `POST /auth/refresh`

Request:

```json
{ "refresh_token": "…" }
```

Response 200: a fresh `access_token` + `refresh_token` pair. Same
rate-limit as `/auth/login`.

### `GET /auth/me`

Header: `Authorization: Bearer <access_token>` (or session cookie).

Response 200:

```json
{ "ok": true, "data": { "id":"usr_…", "email":"…", "display_name":"…", "role":"super_admin|admin|operator" } }
```

## Device API (Bearer `<device_token>` required, except `/register` and `/announce`)

### `POST /device/announce`

Pre-registration "I'm here, please adopt me" beacon for devices that
were flashed without an enrollment token (v0.4.20).

Request:

```json
{
  "hardware_model": "sonoff_s31",
  "hardware_revision": "v1.0",
  "firmware_version": "0.1.0",
  "mac_address": "c4:d8:d5:0c:f6:b3",
  "display_name_hint": "Router Rebooter 01",
  "local_ip": "192.168.1.67"
}
```

Response 200:

```json
{ "ok": true, "data": { "announcement_id":"ann_…", "poll_interval_seconds":30 } }
```

The device should re-announce on the same interval until either it
sees `{ "adopted": true, "enrollment_token": "et_…" }` come back, or
the operator rejects the announcement (404 `announcement_rejected`).
After receiving an enrollment token the device proceeds to
`/device/register`.

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

The `device_token` is only shown once. Store it durably on the device.
The server only stores a SHA-256 hash.

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

Each pending command is returned once and is marked `accepted`
server-side upon delivery. Re-polling will re-deliver `accepted`
(not yet completed) commands until they expire or are reported.

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

Allowed `status`: `accepted`, `running`, `completed`, `failed`,
`expired`.

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

### `POST /device/power-samples`

Headers: `Authorization: Bearer <device_token>`.

Request:

```json
{
  "device_id": "dev_…",
  "samples": [
    {
      "sampled_at": "2026-05-14T00:00:00Z",
      "channel_id": 0,
      "source": "steady",
      "source_flags": 0,
      "sampled_uptime_seconds": 120,
      "v_v": 120.4,
      "i_ma": 1450,
      "p_w": 175.3,
      "s_va": 178.2,
      "pf": 0.945,
      "hz": 60.01,
      "energy_wh": 1234,
      "rssi_dbm": -61,
      "tx_retry_count": 0,
      "beacon_miss_count": 0,
      "crc_fail_count": 0,
      "chip_type": "CSE7766"
    }
  ]
}
```

`sampled_at` is optional in the first B16 transport slice. If it is
omitted, the hub uses receive-time. `source` must be one of
`steady`, `burst`, or `synthetic`.

Up to 3600 samples per batch.

### `POST /device/failsafe`

Single failsafe ingestion endpoint (v0.3.8). Devices send a compact
last-resort message when normal heartbeats can't go out. Same shape
as `/device/events` but tagged `type:"failsafe_ping"` and counted
separately so dashboards can surface "device fell back to failsafe".

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

The download URL is an unauthenticated direct file download. Verify
the SHA-256 of the downloaded blob against the value returned here
before flashing.

### `GET /firmware/{channel}/latest` (firmware public surface)

Unauthenticated. Returns the latest release on a channel, including
the SHA-256 and the public download URL. Used by bootstrap firmware
to fetch the main image (RFC-005).

## Admin API (Bearer admin JWT or session cookie)

### Devices

- `GET /admin/devices` — query: `site_id`, `group_id`, `status`,
  `search`.
- `GET /admin/devices/{device_id}` — full detail (latest heartbeat,
  groups, recent events, pending commands).
- `PATCH /admin/devices/{device_id}` — body fields:
  `display_name`, `site_id`, `notes`, `central_management_enabled`.
- `POST /admin/devices/{device_id}/commands` — body:
  `{ "type":"relay_on", "payload":{}, "ttl_seconds":600 }`.

  Payloads are validated server-side per the command type. Locked
  schemas (v0.1, see `docs/DEVICE_INTEGRATION.md`):

  - `set_mode`: `{ "mode": "smart_plug" | "internet_watchdog" | "device_watchdog" }`.
  - `apply_config`: partial-update object whose top-level keys are a
    subset of `device_name, relay_restore_behavior,
    monitor_interval_seconds, boot_warmup_seconds, manual_button_enabled,
    internet, device, notifications, power`. Any other top-level key is
    rejected with `validation_failed`.
  - `relay_cycle`: optional integer `power_off_seconds` and
    `post_reboot_holdoff_seconds`.

- `POST /admin/devices/{device_id}/commands/{command_id}/cancel` —
  cancel a pending (un-accepted) command.
- `DELETE /admin/devices/{device_id}` — soft-delete a single device.
- `POST /admin/devices/bulk-delete` — body:
  `{ "device_ids": ["dev_…", …], "reason": "…" }`. Audit-logs one
  `device.bulk_deleted_per_device` row per touched device plus an
  aggregate meta-row.

### Enrollment tokens

- `POST /admin/enrollment-tokens` — body:
  `{ "site_id":"…", "display_name_hint":"…", "note":"…", "ttl_seconds": <opt> }`.
  Returns a single-use `enrollment_token` (shown once). TTL defaults
  from `system.enrollment_token_ttl_seconds` (v0.4.26 runtime setting).
- `GET /admin/enrollment-tokens` — list state of all tokens.
- `DELETE /admin/enrollment-tokens/{token_id}` — invalidate a token
  that hasn't been consumed yet.

### Pending adoption (v0.4.20)

Operator flow for `POST /device/announce` beacons.

- `GET /admin/pending-adoption` — list current announcements awaiting
  operator decision. Includes hardware model, MAC, local IP,
  first/last-seen timestamps.
- `POST /admin/pending-adoption/{announcement_id}/adopt` — body:
  `{ "site_id":"…", "display_name":"…", "note":"…" }`. Generates an
  enrollment token and delivers it on the next announce poll.
- `POST /admin/pending-adoption/{announcement_id}/reject` — body:
  `{ "reason":"…" }`. Device will get a terminal "rejected" response
  and stop announcing.

### Groups

- `GET /admin/groups`, `GET /admin/groups/{group_id}`
- `POST /admin/groups` — body: `{ "name":"…", "description":"…", "site_id":"…" }`
- `POST /admin/groups/{group_id}/members` — body:
  `{ "device_ids":["dev_…", …] }`
- `DELETE /admin/groups/{group_id}/members/{device_id}`
- `DELETE /admin/groups/{group_id}`
- `POST /admin/groups/{group_id}/commands` — body:
  `{ "type":"relay_cycle", "payload":{}, "ttl_seconds":600 }`. Fans
  out to every member as a per-device command row.

### Sites

- `GET /admin/sites`, `POST /admin/sites`,
  `DELETE /admin/sites/{site_id}`.

### Users (super-admin only)

- `GET /admin/users` — list users with role + last-seen.
- `POST /admin/users/{user_id}/role` — body: `{ "role":"admin|operator|super_admin" }`.
- `POST /admin/users/{user_id}/deactivate` — disables login.
- `POST /admin/users/{user_id}/revoke-tokens` — invalidates every
  active session and refresh token for the user.
- `POST /admin/users/{user_id}/display-name` — body:
  `{ "display_name":"…" }`.

### Invitations

- `GET /admin/invitations` — list pending invites.
- `POST /admin/invitations` — body:
  `{ "email":"…", "role":"…" }`. Sends an email if SMTP is configured
  (runtime settings, v0.4.25) and returns the invite URL.
- `DELETE /admin/invitations/{invitation_id}` — revoke.

### Watchdog rules (v0.4.0/0.4.2)

- `GET /admin/rules` — list every rule with `enabled` state.
- `POST /admin/rules` — body:

  ```json
  {
    "name": "router-target ping",
    "probe": { "type":"icmp", "target":"1.1.1.1", "timeout_ms":1500 },
    "schedule": { "every_seconds": 60 },
    "action": { "type":"relay_cycle", "device_id":"dev_…", "ttl_seconds":600 },
    "failure_threshold": 3,
    "enabled": true
  }
  ```

- `GET /admin/rules/{rule_id}/events` — recent probe results +
  triggers for one rule.
- `POST /admin/rules/{rule_id}/probe-now` — fire one probe outside
  the schedule; returns the immediate result.
- `DELETE /admin/rules/{rule_id}`.

### Schedules (v0.4.8)

- `GET /admin/schedules` — list recurring power-cycles + maintenance
  windows.
- `POST /admin/schedules` — body:

  ```json
  {
    "name": "Friday 03:00 reboot",
    "cron": "0 3 * * 5",
    "timezone": "America/New_York",
    "target_type": "device|group|site",
    "target_id": "dev_…",
    "action": { "type":"relay_cycle", "payload":{}, "ttl_seconds":600 },
    "enabled": true
  }
  ```

- `DELETE /admin/schedules/{schedule_id}`.

### Firmware

- `GET /admin/firmware/releases`
- `POST /admin/firmware/releases` — `multipart/form-data`:
  `version`, `channel` (`dev|beta|stable`), `sha256` (optional but
  verified), `release_notes`, `file=@firmware.bin`.
- `DELETE /admin/firmware/releases/{release_id}`
- `POST /admin/firmware/scan` (v0.4.19) — body `{}`. Walks the
  firmware directory + LittleFS JSON metadata and creates release
  rows for any binaries the database hadn't seen yet. Returns
  `{ "added": [...], "skipped": [...] }`.

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

  Materialises one `deployment_assignment` row per matched device.
  Subsequent deployments to the same device supersede pending
  assignments.

### Maintenance mode (v0.4.7)

- `GET /admin/maintenance` — current state.
  Response: `{ "ok": true, "data": { "enabled": <bool>, "since": "…", "actor": "…" } }`.
- `POST /admin/maintenance` — body: `{ "enabled": true|false, "reason":"…" }`.
  Pauses every watchdog rule + schedule when enabled. Audit-logs
  `maintenance_mode.toggled`.

### Attention items (v0.4.22)

The Status page lists "needs attention" items (failed watchdog rules,
devices off-line, deployments stuck, etc.). Operators can ack/un-ack:

- `POST /admin/attention/{attention_id}/ack` — body:
  `{ "note": "<opt>" }`. Audit-logs `attention.acked`.
- `DELETE /admin/attention/{attention_id}/ack` — un-ack. Audit-logs
  `attention.unacked`.

### Audit / history

- `GET /admin/audit` — query params:
  `actor_user_id`, `action`, `action_prefix`, `target_type`,
  `target_id`, `limit` (max 1000, default 200).
  `action_prefix` (v0.4.27) does a `LIKE '<prefix>.%'` match so chip
  filters like `watchdog_rule` cover the whole family.

### Events (device events)

- `GET /admin/events` — query params: `device_id`, `group_id`, `type`,
  `from` (ISO8601), `to` (ISO8601), `limit`.

### Unregistered devices

- `GET /admin/unregistered-devices` — device-shaped rows the hub has
  seen via failsafe pings, ARP scans, or rejected registrations but
  never adopted. Useful for triage.

### Runtime settings (admin UI surface)

v0.4.25 + v0.4.26 introduced `runtime_settings` (key/value with
env-var fallback). Updates flow through dedicated form-post endpoints
on the admin UI blueprint, not REST. Settings tabs:

- `/app/settings/notifications` — SMTP host/port/user/password/from/helo
- `/app/settings/network` — public URLs, CORS allowlist (restart),
  rate-limit exempt IPs (live), cookie domain (restart)
- `/app/settings/system` — portal name, TTLs (invitation,
  password-reset, enrollment-token, session-idle)

Each tab has a "Save settings" form (`POST settings_*_save_submit`)
and a "Revert to env-var defaults" form (`POST settings_*_clear_submit`).
Every save/clear is audit-logged (`smtp.*`, `network.*`, `system.*`).

## Errors (commonly returned codes)

| Code | When |
|------|------|
| `auth_required` | session/bearer missing or invalid |
| `auth_invalid` | bad creds, expired token, inactive user |
| `forbidden` | role insufficient for the requested operation |
| `validation_failed` | payload was malformed or missing required fields |
| `rate_limited` | login/refresh over the 30/min or 200/hour per-IP budget |
| `device_unknown` | device id not found |
| `device_mismatch` | bearer device id ≠ payload device id |
| `command_unknown` | command id not found for device |
| `announcement_unknown` / `announcement_rejected` | announce flow problems |
| `enrollment_invalid` / `enrollment_consumed` / `enrollment_expired` | enrollment token problems |
| `firmware_not_found` | unknown firmware release |
| `maintenance_active` | watchdog/schedule writes refused while paused |
| `internal_error` | server-side bug — check logs |
