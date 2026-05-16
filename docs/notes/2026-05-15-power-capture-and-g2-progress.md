# Power Capture And G2 Progress

Date: 2026-05-15

## What is running

- 24-hour capture directory:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\power-capture-2026-05-15-live`
- capture script:
  - `C:\dev\rebooter-firmware\scripts\qa-power-telemetry-capture.ps1`
- current capture set:
  - `http://192.168.1.30`
  - `http://192.168.1.225`
  - `http://192.168.1.67`
  - `http://192.168.1.48`

## What we proved

### Loaded-power telemetry is real off the bench

- `.30` (`Erica's Subwoofer`) is now on `0.1.25-dev-central-safe` and producing
  steady real telemetry around:
  - `118.8-119.2V`
  - `4.9-5.0W`
- `.225` is also on `0.1.25-dev-central-safe` and producing real telemetry around:
  - `118.8-119.0V`
  - between `0W` and about `2.2W`

This moves us from a bench-only no-load proof to live field devices with real
steady power data.

### Invalid-frame rates are high enough to matter

20-second delta sample:

- `.30`
  - `127` new valid frames
  - `86` new invalid frames
  - `213` total classified frames
- `.225`
  - `112` new valid frames
  - `101` new invalid frames
  - `213` total classified frames

This is enough to justify treating frame quality as a first-class analytics and
operator signal.

## New issues uncovered

### Some field devices enter recovery on the first observed boot after OTA to `0.1.25`

- `.30` and `.67` both showed first-boot recovery behavior after OTA from
  `0.1.18-dev-central-safe`
- `.30` recovered to a healthy normal boot after a plain reboot and then
  immediately started real telemetry
- `.67` remains a live anomaly subject and is still being monitored in the 24h capture
- `.48` currently remains stuck in `recovery_mode` with no live power frames

### Low-load current is intentionally clamped to zero

`src/power_monitor.cpp` suppresses measured current below `50mA`, so lightly
loaded devices can show nonzero watts with `power_current_ma = 0`.

This is a design/representation decision that needs to be made explicit before
the hub treats current as authoritative for low standby loads.

### Event log corruption appeared under sustained power-upload logging

One `.30` event entry contained binary garbage inside a central upload message.
That points to an event-log string-handling problem under sustained telemetry load.

## G2 timing status

True cross-device timing measurement was **blocked at the start of this pass**
because firmware only exposed uptime-relative timing.

That blocker is now addressed in the current candidate cut:

- `0.1.27-dev-central-safe` adds:
  - `time_synced`
  - `wall_clock_unix_ms`
  - `power_last_sample_unix_ms`
  - `sampled_unix_ms` on uploaded power rows when sync is present

Until a device is live on `0.1.26`, we can still only measure:

- local sample cadence
- upload cadence
- batch-size jitter

The firmware-side instrumentation is ready; the remaining step is live rollout to
one controlled target and then the actual G2 measurement.

## New firmware response in progress

To address the OTA/recovery edge and the low-load-current ambiguity, the next
cut is now built as:

- `0.1.27-dev-central-safe`

What it adds:

1. boot-state guardrails
   - firmware-version-aware early-boot strike handling
   - explicit `planned_restart` handling for OTA, API reboot, button reboot,
     and recovery transitions
2. clearer low-load current semantics
   - keep measured `power_current_ma`
   - add estimated-current fields for standby-load cases
3. real G2 timing instrumentation
   - additive UTC wall-clock fields alongside existing uptime timing

Current verification state:

- built successfully
- mirrored to `S:\code\rebooter-droids\data\firmware\dev\rebooter-0.1.27-dev-central-safe.bin`
- SHA256:
  - pending mirror refresh in this note
- live verification on `.225` now confirms:
  - OTA to `0.1.27-dev-central-safe` succeeded
  - a controlled reboot temporarily returned to normal mode
  - `time_synced` and `wall_clock_unix_ms` populated as designed
  - later, `.225` re-entered `recovery_mode`, so the stale-runtime-state fix is
    proven but the broader OTA/recovery instability is not yet closed

## Immediate next actions

1. Let the 24-hour capture continue.
2. Review the capture tomorrow for long-run behavior, loaded-power stability,
   and whether `.67` or `.48` self-correct.
3. Move one more recovery-line device onto `0.1.27-dev-central-safe`.
4. Verify the new estimated-current fields under a real low standby load.
5. Then run the real G2 measurement instead of the current pre-instrumentation approximation.

## 2026-05-15 Evening Narrowing Pass

- `.69` is now our clean control on `0.1.28-dev-central-safe`
  - upgraded cleanly from `0.1.18`
  - healthy, `recovery_mode=false`, `reset_reason="Software/System restart"`
  - new timing fields are live there
- `.225` is now the primary recovery-line probe on `0.1.28-dev-central-safe`
  - one OTA cycle reproduced a delayed `Exception` reset in the `60-75s` window
    after a normal boot, followed by auto-recovery
  - with `central.enabled=false`, `.225` stayed healthy for 130+ seconds
  - after a central disable/enable round-trip, `.225` later survived multiple
    heartbeat windows past 140 seconds
  - current best diagnosis: the remaining fault is in the central-enabled path,
    and it is intermittent / heap-sensitive rather than a guaranteed crash
- low-load current semantics are now live-verified on `.225`
  - example standby reading:
    - about `118.6-118.7V`
    - about `2.26-2.32W`
    - `power_current_ma = 0`
    - `power_current_estimated = true`
    - `power_estimated_current_ma = 20`
- the long-running capture still shows:
  - `.225` currently healthy on `0.1.28-dev-central-safe`
  - `.30` and `.67` still pinned on the `0.1.25` recovery line
  - `.48` still timing out on `/api/status`

## 2026-05-15 Late Evening Stabilization Pass

- `.48` was serial-reflashed successfully and then moved forward again over LAN OTA
  to `0.1.29-dev-central-safe`
- crash decoding on `.48` finally gave us concrete hot spots instead of generic
  "Exception" blame:
  - BearSSL validator allocation
  - `LittleFS.open(...)`
  - event-log JSON serialization to file
- that led to a focused mitigation in `0.1.29`:
  - event-log persistence is now deferred / coalesced
  - explicit flushes remain for reboot / recovery / factory-reset transitions
  - routine successful power-upload messages are no longer persisted every batch
  - central heartbeat / poll / firmware-check / power-upload work is staggered
    so the ESP8266 does not burst multiple TLS-heavy operations in one pass
- live `.48` verification on `0.1.29`:
  - OTA accepted and rebooted cleanly
  - with `central.enabled=false`, it stayed healthy through the normal post-OTA
    window
  - after central was re-enabled again, it stayed healthy for 210+ seconds and
    did not reproduce the old `60-90s` crash window
  - current note: the serial adapter still suppresses real CSE7766 visibility on
    `.48`, so this run validates central stability more than live power metering
- live `.69` verification on `0.1.29`:
  - OTA accepted and rebooted cleanly
  - remained healthy past 118 seconds
  - this clears `.69` as the second verified device on the new line
- G2 timing capture has been restarted on the stable pair:
  - `http://192.168.1.48`
  - `http://192.168.1.69`
  - output:
    `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\g2-time-sync-48-69-live-2026-05-15.ndjson`
- the 24-hour power capture remains running in parallel
