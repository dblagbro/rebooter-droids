# Central Server Specification

## Purpose

This document defines the backend contract for the optional central
coordination platform for Rebooter devices.

The backend lives under:

```
https://www.voipguru.org/rebooter/
```

This spec is the canonical hand-off document for the backend developer and
the firmware integration team. The implementation in this repo follows it
faithfully; deviations are documented in [API.md](API.md).

## Product Rules

- Devices remain local-first.
- Local control must continue to work if the central server is down.
- Central control is an optional coordination layer.
- Devices poll for commands; the server never opens inbound connections to a
  device.
- Central firmware deployment must not remove the device's ability to do local
  OTA.

## URL Layout

- Admin web app: `https://www.voipguru.org/rebooter/app/`
- API root: `https://www.voipguru.org/rebooter/api/v1/`
- Firmware assets: `https://www.voipguru.org/rebooter/firmware/`

## Roles

### Device

An enrolled Sonoff-based Rebooter unit that:

- registers itself
- sends heartbeats
- uploads events
- polls for commands
- reports command results
- checks for firmware rollout instructions

### Admin User

A human user managing devices through the web app or future mobile app.

### Mobile App User

A human user using the same backend APIs through the future mobile app
(JWT-based auth).

## Auth Model

### Admin Auth

- email + password login
- session cookie for the web app
- bearer (JWT) token for API/mobile callers
- endpoints:
  - `POST /rebooter/api/v1/auth/login`
  - `POST /rebooter/api/v1/auth/logout`
  - `POST /rebooter/api/v1/auth/refresh`
  - `GET  /rebooter/api/v1/auth/me`

### Device Auth

- Device is provisioned with a single-use `enrollment_token`.
- Device calls `POST /device/register` once to exchange that token for a
  permanent `device_id` + `device_token` pair.
- All subsequent device endpoints require `Authorization: Bearer <device_token>`.
- Device auth is independent from local device admin credentials.

## Device Identity

Each device record includes:

- `device_id`
- `hardware_model`
- `hardware_revision`
- `firmware_version`
- `mac_address`
- `serial_number`
- `local_ip`
- `display_name`
- `site_id`
- `group_ids`
- `device_secret_status`
- `registration_state`

## Device Lifecycle

1. Enrollment — device is flashed and configured locally; the user enters
   the central server URL, the enrollment token, and any site/group defaults.
2. Registration — device calls `POST /device/register` and receives a
   permanent identity credential.
3. Heartbeat — device periodically reports health and state.
4. Command poll — device polls for pending commands.
5. Result reporting — device reports execution results.
6. Firmware check — device receives rollout instructions and downloads
   firmware if instructed.

## API Conventions

### Base path

`/rebooter/api/v1`

### Content type

- request: `application/json`
- response: `application/json`
- firmware binary delivery is separate and uses raw file download.

### Timestamps

ISO 8601 UTC strings, e.g. `2026-05-08T22:15:04Z`.

### IDs

ULID with a stable prefix (e.g. `dev_…`, `cmd_…`, `fwr_…`).

### Standard response shape

Success:

```json
{ "ok": true, "data": {} }
```

Error:

```json
{
  "ok": false,
  "error": { "code": "string_code", "message": "Human readable message" }
}
```

## Device API

See [API.md](API.md) for full request/response examples for:

- `POST /device/register`
- `POST /device/heartbeat`
- `GET  /device/commands`
- `POST /device/command-result`
- `POST /device/events`
- `GET  /device/firmware`

## Admin API

See [API.md](API.md) for:

- `GET  /admin/devices`
- `GET  /admin/devices/{device_id}`
- `PATCH /admin/devices/{device_id}`
- `POST /admin/groups`
- `POST /admin/groups/{group_id}/members`
- `DELETE /admin/groups/{group_id}/members/{device_id}`
- `POST /admin/devices/{device_id}/commands`
- `POST /admin/groups/{group_id}/commands`
- `POST /admin/firmware/releases`
- `POST /admin/firmware/deployments`
- `GET  /admin/events`
- `POST /admin/sites`
- `POST /admin/enrollment-tokens`

## Command Types

- `relay_on`
- `relay_off`
- `relay_toggle`
- `relay_cycle`
- `device_restart`
- `factory_reset`
- `set_mode` — payload `{ "mode": "smart_plug" | "internet_watchdog" | "device_watchdog" }` (v0.1 locked)
- `apply_config` — partial-update object, locked top-level keys (v0.1; see
  [DEVICE_INTEGRATION.md](DEVICE_INTEGRATION.md))
- `check_firmware`
- `start_firmware_update`

## Firmware Hosting Rules

- Binaries live under `/rebooter/firmware/` and are served by nginx
  directly, with no app round-trip and no auth challenge.
- Stable filename, stable Content-Length, stable file contents matching the
  published `sha256`.

## Database Model

Core tables:

`users · sites · devices · device_credentials · enrollment_tokens · groups ·
group_memberships · device_heartbeats · device_events · commands ·
command_results · firmware_releases · firmware_deployments ·
deployment_assignments`

## Polling Recommendations

- Heartbeat every 60 s.
- Command poll every 30 s.
- A faster poll window immediately after issuing a command is permitted but
  not implemented as of v0.1.

## Local + Central Coexistence

Device firmware must support both local direct browser/API use and central
polling. Local control must continue when:

- the central server is down
- the WAN is down
- registration is disabled

## MVP Definition

Backend MVP is complete when:

- a device can register
- a device can send a heartbeat
- an admin can see the device list
- an admin can place devices into groups
- an admin can send `relay_on` / `relay_off` / `relay_cycle` to one device
  or a group
- a device can report a command result
- an admin can publish a firmware release entry
- a device can be instructed to update firmware from a hosted binary

All MVP criteria are met as of v0.1.0.
