# Firmware Heartbeat Expansion + `reported_config`

Date: 2026-05-14
Device used for live verification: `http://192.168.1.48`
Verified firmware: `0.1.19-dev-central-safe`

## What changed

Firmware now emits the richer status/recovery fields in the central
heartbeat payload and includes a non-secret `reported_config`
snapshot.

The heartbeat and the new local preview endpoint share the same
serializer, so the preview JSON is the exact shape the device will
POST to the hub.

## New firmware artifact

- Stable named artifact:
  `S:\code\rebooter-droids\data\firmware\stable\rebooter-0.1.19-dev-central-safe.bin`
- Dev named artifact:
  `S:\code\rebooter-droids\data\firmware\dev\rebooter-0.1.19-dev-central-safe.bin`
- SHA256:
  `5C1D7271A5FB8644910E1E39711990E00367817FD16A7343ED123AAF29B205C3`

## New heartbeat fields

In addition to the older top-level heartbeat fields, firmware now
sends:

- `in_captive_portal`
- `recovery_mode`
- `auto_recovery_triggered`
- `last_known_good_restored`
- `consecutive_unhealthy_boots`
- `holdoff_remaining_seconds`
- `cooldown_remaining_seconds`
- `central_enabled`
- `central_registered`
- `central_state`
- `central_device_id`
- `central_last_heartbeat_uptime_seconds`
- `central_heartbeat_age_seconds`
- `power_analytics_enabled`
- `power_chip_type`
- `power_sample_rate_hz`
- `power_batch_seconds`
- `reported_config`

## `reported_config` contents

The emitted `reported_config` is intentionally non-secret.

Included:
- `device_name`
- `current_mode`
- `relay_restore_behavior`
- `monitor_interval_seconds`
- `boot_warmup_seconds`
- `manual_button_enabled`
- full watchdog tuning under `internet.*` and `device.*`
- non-secret notification shape:
  - `enabled`
  - `type`
  - `webhook_method`
  - `send_on_trigger`
  - `send_on_recovery`
  - `send_on_max_cycles_reached`
  - `send_test_notification_enabled`
- non-secret central shape:
  - `enabled`
  - `base_urls`
  - `device_alias`
  - `poll_interval_seconds`
  - `heartbeat_interval_seconds`
- `power.*`

Not included:
- admin username/password hash/salt
- `central.enrollment_token`
- `central.device_token`
- `central.site_id`

## Local verification path

Protected preview endpoint:

- `GET /api/system/heartbeat-preview`
- auth header:
  - `X-Rebooter-Auth: <local admin password>`

This endpoint uses the same serializer as the real heartbeat POST and
exists specifically so the hub side can debug field shape without
waiting for server-side inspection.

## Live verification result on `.48`

Steady-state `/api/status` after OTA:

- firmware: `0.1.19-dev-central-safe`
- `health_state: healthy`
- `wifi_connected: true`
- `recovery_mode: false`
- `central_registered: true`
- `central_state: idle`

Saved artifacts:

- status snapshot:
  `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\2026-05-14-rebooter-48-status-after-heartbeat-expansion.json`
- heartbeat preview:
  `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\2026-05-14-rebooter-48-heartbeat-preview.json`

## What the hub side should do next

1. Persist and expose the new top-level heartbeat fields instead of
   collapsing device truth into generic offline/online buckets.
2. Use `reported_config` for desired-config drift comparison instead
   of inferring from partial or stale state.
3. Render at least these distinct states in UI:
   - central disabled
   - recovery mode
   - registered but unhealthy
   - transport stale
   - rebind-needed
4. Prefer `reported_config` as the canonical device-side readback for
   fields the hub centrally owns.

## Implementation note

The shared serializer lives in:

- `C:\dev\rebooter-firmware\include\status_payload.h`
- `C:\dev\rebooter-firmware\src\status_payload.cpp`

It is used by:

- central heartbeat POST path in
  `C:\dev\rebooter-firmware\src\central_client.cpp`
- local preview endpoint in
  `C:\dev\rebooter-firmware\src\web_server_manager.cpp`
