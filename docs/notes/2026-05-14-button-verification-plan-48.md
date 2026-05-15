# Rebooter `.48` Button Verification Plan

Date: 2026-05-14
Target: `http://192.168.1.48`
Firmware under test: `0.1.18-dev-central-safe`

## Why this test matters

We already changed the firmware-side mapping to:

- short press: relay toggle
- 3s hold: reboot
- 10s hold: recovery mode
- 30s hold: factory reset

This test is the hardware proof pass so we can stop relying on code
inspection alone.

## Backlogged follow-ups parked for later

- Expand heartbeat with the richer status/recovery fields already
  present in local `/api/status`.
- Add non-secret `reported_config` to heartbeat for hub drift truth.
- Reconcile any remaining `config/save` vs `apply_config` field
  asymmetry.
- Refresh hub-facing docs whenever firmware status/config fields
  change.

## Baseline before physical button test

Captured from `/api/status` at `2026-05-14T19:15:01-04:00`:

- `relay_on: false`
- `wifi_connected: true`
- `recovery_mode: false`
- `health_state: healthy`
- `central_enabled: true`
- `central_registered: true`
- `central_state: idle`
- `firmware_version: 0.1.18-dev-central-safe`

## Expected outcomes

### 1. Short press

- Relay should toggle.
- `uptime_seconds` should continue increasing.
- `recovery_mode` should remain `false`.

### 2. Hold about 3 seconds

- Device should reboot normally.
- HTTP may drop briefly.
- After reboot:
  - `uptime_seconds` resets low
  - `recovery_mode` stays `false`
  - Wi-Fi and central should come back normally

### 3. Hold about 10 seconds

- Device should enter recovery mode.
- After reboot:
  - `recovery_mode` should become `true`
  - local HTTP should return
  - Wi-Fi should ideally remain intact

### 4. Hold about 30 seconds

- Device should factory reset.
- This is destructive.
- Expected result:
  - fresh or recovery-style config state
  - possible AP-mode / provisioning path
  - requires restore/re-enroll afterward

## Monitoring harness

Script:

- `C:\dev\rebooter-firmware\scripts\watch-button-test-status.ps1`

It polls `/api/status` and writes NDJSON with timestamps so we can
see the exact reboot/recovery transitions instead of relying on
memory.

## Suggested execution order

1. Run monitor.
2. Perform short press.
3. Perform 3s hold.
4. Perform 10s hold.
5. Pause and confirm state.
6. Only then perform 30s hold.
