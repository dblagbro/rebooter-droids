# Firmware Config And Reported Schema - 2026-05-14

Purpose:
- document what the firmware actually accepts today
- separate:
  - local config save contract
  - central `apply_config` contract
  - currently reported config/status truth
- replace guesswork with a source-backed schema note

Firmware baseline:
- `0.1.20-dev-central-safe`

Primary sources:
- `C:\dev\rebooter-firmware\src\web_server_manager.cpp`
- `C:\dev\rebooter-firmware\src\central_client.cpp`
- `C:\dev\rebooter-firmware\src\config_manager.cpp`
- `C:\dev\rebooter-firmware\include\types.h`

---

## 1. Important distinction

There are three related but different schema surfaces:

1. local config API
   - `POST /api/config/save`
   - richest configuration surface
2. central command config API
   - `apply_config` command payload handled in `central_client.cpp`
   - narrower than local config save
3. reported config back to hub
   - **not fully implemented today**
   - hub drift logic is ahead of the current firmware heartbeat payload

The hub must not assume these are identical.

---

## 2. Current local config save contract

Endpoint:
- `POST /api/config/save`

This is the richest currently implemented config surface.

### Supported top-level keys

| Key | Type | Notes |
|---|---|---|
| `current_mode` | string | `smart_plug`, `internet_watchdog`, `device_watchdog` |
| `relay_restore_behavior` | string | `restore_previous`, `always_on`, `always_off` |
| `device_name` | string | Friendly name |
| `admin_username` | string | Only meaningful when paired with `admin_password` |
| `admin_password` | string | Sets local auth if valid |
| `monitor_interval_seconds` | int | Global monitor loop interval |
| `boot_warmup_seconds` | int | Boot warmup |
| `manual_button_enabled` | bool | Short-press relay toggle enable |
| `internet` | object | See below |
| `device` | object | See below |
| `notifications` | object | See below |
| `central` | object | See below |
| `power` | object | See below |

### `internet` object

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

### `device` object

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

### `notifications` object

Currently honored by local config save:

| Key | Type | Notes |
|---|---|---|
| `enabled` | bool | |
| `webhook_url` | string | |
| `webhook_auth_token` | string | |

Important note:
- local config save does **not** currently parse every notification key that
  `apply_config` can parse

### `central` object

| Key | Type | Notes |
|---|---|---|
| `enabled` | bool | |
| `base_urls` | string[] | preferred modern form |
| `base_url` | string | accepted as compatibility fallback; converted into `base_urls` |
| `enrollment_token` | string | if changed to a new non-empty token, cached `device_id` and `device_token` are cleared |
| `device_alias` | string | |
| `site_id` | string | |
| `device_id` | string | local restore/import path only |
| `device_token` | string | local restore/import path only |
| `poll_interval_seconds` | int | |
| `heartbeat_interval_seconds` | int | |

### `power` object

| Key | Type |
|---|---|
| `enabled` | bool |
| `sample_rate_hz` | int |
| `batch_seconds` | int |
| `include_wifi_stats` | bool |
| `include_frequency` | bool |

---

## 3. Current central `apply_config` contract

Handled in:
- `src/central_client.cpp`

This is the hub-driven central config surface today.

### Supported top-level keys

| Key | Type | Notes |
|---|---|---|
| `device_name` | string | |
| `monitor_interval_seconds` | int | |
| `boot_warmup_seconds` | int | |
| `manual_button_enabled` | bool | |
| `relay_restore_behavior` | string | |
| `internet` | object | supported |
| `device` | object | supported |
| `notifications` | object | supported |
| `power` | object | supported |

### Important exclusions from `apply_config`

These are **not** currently applied through central `apply_config`:

| Key | Reason |
|---|---|
| `central.*` | intentionally excluded; central identity/config should not be recursively rewritten by central management |
| `admin_username` / `admin_password` | local security boundary; not hub-driven |
| `current_mode` | separate `set_mode` command exists |

### `notifications` object in `apply_config`

`apply_config` currently supports more notification fields than local config save:

| Key | Type |
|---|---|
| `enabled` | bool |
| `type` | string |
| `webhook_url` | string |
| `webhook_method` | string |
| `webhook_auth_token` | string |
| `send_on_trigger` | bool |
| `send_on_recovery` | bool |
| `send_on_max_cycles_reached` | bool |
| `send_test_notification_enabled` | bool |

This means:
- local edit/save and central `apply_config` are **not yet fully symmetric**

---

## 4. Current config readback contract

Endpoint:
- `GET /api/config`

This returns a safe public config shape intended for local UI hydration,
including:
- top-level watchdog settings
- `internet`
- `device`
- redacted `central`
- `power`
- local admin username

Sensitive behavior:
- `GET /api/config` does **not** include:
  - `central.enrollment_token`
  - `central.site_id`
  - `central.device_id`
  - `central.device_token`
- `GET /api/config` does include safe central summary fields such as:
  - `enabled`
  - `base_urls`
  - `device_alias`
  - `registered`
  - `has_enrollment_token`
  - polling/heartbeat intervals
- `GET /api/system/config-backup` **does** include full central identity fields
  after auth, for protected backup/restore

---

## 5. Current reported-config truth

Important current reality:

- the hub has desired-config drift machinery
- the firmware now includes `reported_config` in heartbeat
- therefore hub-side `last_reported_config` truth can be kept current by the
  present firmware line if the hub consumes that field

That means:
- firmware-side reporting is no longer the blocker for desired-config drift truth
- remaining drift gaps are now mostly hub-consumption and UX work

### Recommended `reported_config` shape

When added, it should mirror the safe non-secret subset of persisted config:

- `device_name`
- `relay_restore_behavior`
- `monitor_interval_seconds`
- `boot_warmup_seconds`
- `manual_button_enabled`
- `internet`
- `device`
- `notifications` non-secret subset
- `power`

It should **exclude**:
- admin password material
- central `device_token`
- notification secrets like raw webhook auth token if we decide those should stay write-only

---

## 6. Reconciliation with the stale hub schema doc

The older hub-side doc:
- `S:\code\rebooter-droids\docs\firmware-apply-config-schema-v01.md`

is no longer accurate in several important ways:

### Outdated claims

- it describes `internet` as Wi-Fi SSID/password/static-IP fields
  - current firmware does **not** use `internet` for Wi-Fi credentials
  - current firmware uses `internet` for watchdog targets/timers/retry behavior
- it describes `device.boot_mode`, `led_brightness`, `timezone`, `ntp_server`
  as central `apply_config` fields
  - these are **not** current firmware `apply_config` fields
- it describes `notifications` in MQTT-first terms
  - current firmware implements webhook-oriented notification config instead
- it implies `reported_config` is already a reliable heartbeat echo
  - this is **not** true today

### Current recommendation

Treat this 2026-05-14 note as the truth baseline for the current firmware line.

The hub-side doc should be updated to match:
- actual `apply_config` support
- actual local config surface
- actual `reported_config` gap

---

## 7. Practical hub guidance

For hub-side desired-config and drift work:

### Safe to treat as currently supported for `apply_config`

- `device_name`
- `relay_restore_behavior`
- `monitor_interval_seconds`
- `boot_warmup_seconds`
- `manual_button_enabled`
- watchdog `internet.*`
- watchdog `device.*`
- `notifications.*` subset that the firmware currently parses
- `power.*`

### Not safe to assume as central `apply_config`

- `central.*`
- local auth credentials
- Wi-Fi credential rewrites through `internet.*`
- any stale legacy keys from the old schema doc

---

## 8. Immediate next firmware tasks after this reconciliation

1. keep the `reported_config` shape aligned to the safe non-secret subset
2. update the stale hub-side schema doc to match the real firmware contract
3. verify hub-side drift rendering against the emitted heartbeat payload
4. continue reducing asymmetry between local config save and central `apply_config`
