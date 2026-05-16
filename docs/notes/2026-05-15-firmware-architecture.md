# Firmware Architecture

Last updated: 2026-05-14

## Purpose

This repo contains the local-first firmware for the Rebooter Sonoff S31
platform. The firmware owns relay control, watchdog logic, safe fallback,
local configuration, local web/API surfaces, OTA updates, event logging,
and central-hub integration.

## Primary runtime domains

1. `main.cpp`
   - boots the device
   - wires managers together
   - drives the main runtime loop

2. configuration and recovery
   - `src/config_manager.cpp`
   - owns persisted config, boot-state tracking, recovery requests,
     last-known-good restore, and config validation
   - full factory reset must clear persisted config, recovery markers,
     and local event history
   - intentional OTA / reboot / recovery transitions must be distinguishable
     from crash-like early boots so planned restarts do not accumulate
     recovery strikes

3. local control plane
   - `src/web_server_manager.cpp`
   - serves the local web UI, local API, OTA upload endpoint, and
     recovery/system actions
   - public surfaces must stay intentionally limited
   - protected surfaces use `X-Rebooter-Auth`

4. auth
   - `src/auth_manager.cpp`
   - owns local admin credential storage and request authorization

5. monitoring and relay behavior
   - `src/monitor_engine.cpp`
   - `src/relay_controller.cpp`
   - `src/button_handler.cpp`
   - `src/wifi_manager.cpp`
   - explicit recovery provisioning may intentionally move the device into
     setup AP mode, but a successful explicit recovery reprovision should
     reboot back into a normal boot instead of lingering in recovery mode

6. central integration
   - `src/central_client.cpp`
   - heartbeat, announce/register, command polling, firmware checks,
     and power upload

7. status and reporting
   - `src/status_payload.cpp`
   - `src/event_log.cpp`
   - local status, central heartbeat payloads, and persisted event history

8. time synchronization
   - `src/time_sync_manager.cpp`
   - owns lightweight NTP-based UTC wall-clock acquisition for telemetry
     correlation work
   - status and uploaded power samples may include both uptime-relative and
     wall-clock timestamps; uptime remains the fallback when sync is absent

9. power telemetry
   - `src/power_monitor.cpp`
   - owns CSE7766 frame parsing and live electrical readings
   - real metering is delayed until the device is stably up on the LAN to
     reduce boot-path risk on the bench/dev line
   - low standby loads may have trustworthy voltage and power but only
     estimated current; the API should make that distinction explicit

## Security model

The local device intentionally exposes a small public read surface:

- `GET /api/status`
- `GET /api/config` with redacted central identity/secrets
- `GET /api/events`

Protected local actions and protected config export require the local
admin password through the `X-Rebooter-Auth` header:

- relay control
- config save
- OTA upload
- reboot/recovery/factory reset
- heartbeat preview
- central diagnostics
- protected config backup

The UI must never depend on secret-bearing public reads.

## UI serving model

- Preferred UI source: LittleFS assets under `data/`
- Fallback UI source: embedded copies in `src/web_server_manager.cpp`

The fallback UI must stay behaviorally aligned with `data/` assets.
UI/auth regressions must be verified against the actual device-served assets
after OTA, not only against repo files or proxy-served copies.

## Current known architectural debt

1. `src/web_server_manager.cpp` is still too large and mixes route
   registration, response shaping, and embedded assets.
2. `src/central_client.cpp` still mixes enrollment, transport, command
   handling, heartbeat, and firmware work.
3. `src/config_manager.cpp` still combines validation, persistence, and
   recovery orchestration.

## Near-term architectural direction

1. keep public vs protected surfaces explicit
2. keep local UI behavior aligned with API/auth intent
3. progressively split oversized modules without changing behavior
