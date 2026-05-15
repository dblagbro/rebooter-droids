# Firmware Status And Recovery Contract - 2026-05-14

Purpose:
- lock the firmware-side truth for status, recovery, and central/rebind state
- give the hub team one clear contract to implement against
- separate **current emitted fields** from **recommended next fields**

This note reflects the current `0.1.20-dev-central-safe` line.

## Scope

There are two different firmware status surfaces today:

1. local device status
   - `GET /api/status`
   - richer and more recovery-aware
2. central heartbeat
   - `POST /api/v1/device/heartbeat`
   - narrower and still missing some recovery/rebind truth

The hub must not assume these two surfaces currently match.

---

## 1. Current local status contract (`GET /api/status`)

Source of truth:
- `src/web_server_manager.cpp`
- `include/app_state.h`

Current emitted fields:

| Field | Type | Meaning |
|---|---|---|
| `device_name` | string | Local configured device name |
| `firmware_version` | string | Running firmware version string |
| `mode` | string enum | `smart_plug`, `internet_watchdog`, `device_watchdog` |
| `relay_on` | bool | Current relay state |
| `wifi_connected` | bool | Whether station Wi-Fi is connected |
| `in_captive_portal` | bool | Whether the device is currently in setup/captive portal mode |
| `recovery_mode` | bool | Device booted into recovery-mode behavior |
| `auto_recovery_triggered` | bool | Recovery path was entered due to unhealthy boot/recovery logic, not just operator request |
| `last_known_good_restored` | bool | Last-known-good config overlay/restore was applied on this boot path |
| `consecutive_unhealthy_boots` | int | Count of consecutive boots not yet marked healthy |
| `setup_ap_name` | string | Current setup AP SSID if applicable |
| `health_state` | string enum | `unknown`, `healthy`, `partial_failure`, `failed`, `holdoff`, `cooldown` |
| `uptime_seconds` | int | Device uptime in seconds |
| `free_heap` | int | Current free heap bytes |
| `incident_cycles` | int | Recovery cycles in current incident |
| `hour_cycles` | int | Recovery cycles in current hour window |
| `holdoff_remaining_seconds` | int | Holdoff time remaining |
| `cooldown_remaining_seconds` | int | Cooldown time remaining |
| `central_enabled` | bool | Local device config says central is enabled |
| `central_registered` | bool | Device currently has non-empty central device id + token |
| `central_state` | string | Current central client state machine string |
| `auth_required` | bool | Whether local protected actions require `X-Rebooter-Auth` |
| `central_identity_present` | bool | Whether the device currently has a central identity bound locally |
| `central_last_heartbeat_seconds` | int | Device uptime-second stamp of last successful central heartbeat |
| `central_last_heartbeat_uptime_seconds` | int | Same value as above today; duplicate naming for readability |
| `central_heartbeat_age_seconds` | int | `uptime_seconds - central_last_heartbeat_seconds` |
| `power_analytics_enabled` | bool | Local power telemetry enabled flag |
| `power_chip_type` | string | Currently hard-coded `CSE7766` |
| `power_sample_rate_hz` | int | Current configured power sample rate |
| `power_batch_seconds` | int | Current configured power batch window |

### Current `central_state` values observed or set in firmware

This is a free-string state machine today, not an enum.

Important values already used:
- `disabled`
- `idle`
- `announce`
- `announce_pending`
- `announce_adopted`
- `announce_transport_failed`
- `awaiting_register`
- `awaiting_register_no_token`
- `registered`
- `registered_no_token`
- `registering`
- `register_transport_failed`
- `register_failed`
- `heartbeat`
- `heartbeat_ok`
- `heartbeat_transport_failed`
- `heartbeat_failed`
- `polling`
- `poll_transport_failed`
- `poll_failed`
- `commands_received`
- `reauth_required`
- `recovery_mode`
- `firmware_updating`
- `firmware_check_transport_failed`
- `firmware_check_failed`

Important interpretation:
- this field is already useful for diagnostics
- it is **not yet normalized** enough for the hub to expose raw to ordinary operators without mapping

---

## 2. Current central heartbeat contract (`POST /api/v1/device/heartbeat`)

Source of truth:
- `src/central_client.cpp`

Current emitted fields:

| Field | Type | Meaning |
|---|---|---|
| `device_id` | string | Registered central device id |
| `firmware_version` | string | Running firmware version |
| `local_ip` | string | Current local IP |
| `mode` | string enum | Current operating mode |
| `relay_on` | bool | Current relay state |
| `wifi_connected` | bool | Current Wi-Fi station state |
| `health_state` | string enum | Current health state |
| `uptime_seconds` | int | Uptime |
| `incident_cycles` | int | Current incident cycle count |
| `hour_cycles` | int | Current hour cycle count |
| `last_event_type` | string | Last event label |
| `last_event_at` | string | Currently blank in practice |

### Current state

The heartbeat now carries the richer recovery/status truth that was missing
earlier in the day, including `reported_config`. The hub can still lag if it
does not consume those newer fields yet, but the firmware payload is no longer
the limiting factor for those items.

---

## 3. Current additive heartbeat fields

These fields are now emitted by the current firmware line and should be treated
as stable hub-consumption candidates.

### 3.1 Strongly recommended fields

| Field | Type | Why the hub needs it |
|---|---|---|
| `central_enabled` | bool | Distinguish centrally-disabled devices from stale/offline ones |
| `central_registered` | bool | Distinguish pre-registration/rebind states cleanly |
| `central_state` | string | Preserve device-side central state machine truth for mapping |
| `recovery_mode` | bool | Tell hub the device is alive but in recovery |
| `auto_recovery_triggered` | bool | Distinguish automatic recovery from operator-invoked recovery |
| `last_known_good_restored` | bool | Tell hub that recovery restored config successfully |
| `consecutive_unhealthy_boots` | int | Show boot-loop pressure / recovery severity |
| `reported_config` | object | Keep desired-config drift truth current without separate side-channel |

### 3.2 Recommended operational fields

| Field | Type | Why useful |
|---|---|---|
| `holdoff_remaining_seconds` | int | Explain why device is not acting yet |
| `cooldown_remaining_seconds` | int | Explain why device is intentionally quiet |
| `power_analytics_enabled` | bool | Let hub know whether missing power data is expected |
| `power_sample_rate_hz` | int | Support future power UI/debug |
| `power_batch_seconds` | int | Support future power UI/debug |

### 3.3 Optional future fields

| Field | Type | Notes |
|---|---|---|
| `safe_fallback_reason` | string | Human-friendly recovery reason if we later normalize cause strings |
| `central_last_heartbeat_age_seconds` | int | Could be useful, but hub can infer transport freshness from heartbeat cadence |
| `setup_ap_name` | string | Useful mainly in local UI; hub can omit unless we want explicit provisioning status |

---

## 4. Hub-side rendering guidance

The hub should not treat all degraded states as generic offline.

At minimum, the hub should map these cases separately:

### Case A: local central disabled

Firmware truth:
- `central_enabled = false`

Meaning:
- device may be fully healthy and reachable locally
- operator action is configuration/enrollment, not outage triage

Recommended hub label:
- `central disabled on device`

### Case B: recovery mode

Firmware truth:
- `recovery_mode = true`

Meaning:
- device is alive
- operator action is recovery follow-up, not generic transport debugging

Recommended hub label:
- `recovery mode`

### Case C: token/rebind trouble

Firmware truth:
- `central_state` like `registered_no_token`, `awaiting_register_no_token`, or `reauth_required`

Meaning:
- device is alive
- central identity needs repair or is in-flight

Recommended hub label:
- `rebind needed`

### Case D: transport stale

Firmware truth:
- device locally healthy
- central client stuck in heartbeat/poll transport failure states

Recommended hub label:
- `transport stale`

### Case E: actually offline/unreachable

Firmware truth:
- no fresh heartbeat and no newer device-side evidence

Recommended hub label:
- `offline`

Concrete motivating example:
- `.69`-type device should render as **central disabled on device**, not plain offline

---

## 5. Current firmware recommendation

Short recommendation:

1. keep `/api/status` as the richest diagnostic surface
2. make heartbeat more truthful by adding the strongly recommended fields above
3. keep the additions additive and backward-compatible
4. let the hub map `central_state` into operator-facing chips rather than exposing raw state strings directly

---

## 6. Immediate next firmware tasks after this contract

1. keep local `/api/status` and heartbeat field names stable
2. keep public vs protected local surfaces aligned with the documented contract
3. re-test hub consumption of the newer heartbeat fields
4. continue normalizing operator-facing mappings for `central_state`

---

## 7. Source files

Primary firmware sources:
- `C:\dev\rebooter-firmware\src\web_server_manager.cpp`
- `C:\dev\rebooter-firmware\src\central_client.cpp`
- `C:\dev\rebooter-firmware\include\app_state.h`
- `C:\dev\rebooter-firmware\include\types.h`

Related hub references:
- `S:\code\rebooter-droids\app\services\heartbeats.py`
- `S:\code\rebooter-droids\docs\firmware-apply-config-schema-v01.md`
