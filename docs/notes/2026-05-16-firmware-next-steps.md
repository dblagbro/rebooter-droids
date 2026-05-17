# Next Steps

## Current Goal

Build a production-capable local-first Sonoff S31 firmware prototype for:

1. Smart plug mode
2. Internet watchdog mode
3. Device watchdog mode

## Current Status

- Initial proof-of-concept files are preserved under `docs/poc/`.
- The authoritative firmware design spec is now in `SPECS.md`.
- PlatformIO scaffold has been split into real source, include, and data files.
- PlatformIO CLI is installed: PlatformIO Core 6.1.19.
- Git for Windows is installed: 2.53.0.windows.3. The current shell may need a restart for PATH updates; use `C:\Program Files\Git\cmd\git.exe` meanwhile.

## Immediate Tasks

1. Treat the `0.1.29-dev-central-safe` soak instability as a broader
   central-enabled transport / heap problem, not a `.225`-only outlier:
   - `.225` stays healthy with `central.enabled=false` and `power.enabled=false`
   - `.225` still falls back into `recovery_mode` with `central.enabled=true`
     even when `power.enabled=false`
   - overnight soak later showed `.48`, `.67`, `.69`, and `.30` also landing in
     `recovery_mode`
2. Rework the central failure path before any wider rollout:
   repeated heartbeat / poll / firmware-check / power-upload transport failures
   are driving free heap down across long runs and appear to be the best current
   explanation for the later recovery transitions.
3. Reconcile the `.69` hub identity split:
   - live row: `dev_01KRQBRSG1BZ5SR87QQ2KSSVFT` (`lab-69`) is online and sending
     real power rows
   - stale row: `dev_01KR9VZKGW72DS7DAQEFAV3T58` (`Erica's R.L. Speaker`) is still
     offline
   Decide whether the hub should merge/archive the stale row or keep it as history.
4. Follow the completed overnight readout with a tighter soak retest after the
   next central-path mitigation:
   - 24-hour capture completed at `2026-05-16 15:49 -04:00`
   - G2 timing capture completed earlier at `2026-05-15 23:19 -04:00`
5. Keep the OTA harness on the new interpretation path:
   `scripts/qa-ota-stress.ps1` now treats a reboot-cutoff upload as accepted
   when the device clearly reboots and comes back, but the revised harness still
   needs one real rerun on the next candidate line.
6. Re-verify live CSE7766 metering on `.48` with the serial adapter detached,
   since the current `.48` validation proves central stability but not real
   power-chip reads on that exact unit.

## Backlog

### Post-v0.1.18 safe follow-ups

1. Expand the central heartbeat payload to include the current
   status/recovery truth already present in local `/api/status`,
   including `central_enabled`, `central_registered`,
   `central_state`, `recovery_mode`,
   `auto_recovery_triggered`,
   `last_known_good_restored`,
   `consecutive_unhealthy_boots`, and useful holdoff/cooldown and
   power-config flags.
2. Add a non-secret `reported_config` block to heartbeat so hub-side
   desired-config drift can reconcile against real device truth
   instead of guessing from stale or partial state.
3. Reconcile the remaining asymmetry between local
   `POST /api/config/save` and central `apply_config`, especially
   around notifications and any fields the hub may assume are
   centrally writable when they are not.
4. Keep the recovery / status contract docs and the hub-side schema
   notes in sync whenever firmware status or config fields change,
   so the hub UI and drift logic do not fall behind device reality
   again.
5. After physical verification on the bench, record the final truth
   for short press, 3s reboot, 10s recovery entry, and 30s factory
   reset in both firmware docs and the hub-facing notes.

### B16 multimodal / enhanced power analytics follow-ups

1. Validate real-power telemetry under a known downstream load so `i_ma`
   and `p_w` move off zero, not just live-voltage/no-load proof.
2. Preserve and document power-sampler quality diagnostics
   (`valid_frame_count`, `invalid_frame_count`, source flags) because they
   are operationally meaningful for B16.
3. Capture at least 24 hours of real CSE7766 telemetry from a known mixed-load
   deployment and share the raw data plus observed noise/jitter behavior with
   analytics.
4. Confirm the actual atomic-snapshot behavior and 1 Hz sample-boundary jitter
   on the live power path; do not leave this at the spec/theory level.
5. Measure realistic fleet time-sync behavior across supported ESP8266 /
   ESP32 / Shelly families; do not estimate the `G2` timing window.
6. Provide firmware-side input for multimodal schema direction:
   common ingest envelope plus modality-specific stores/views, not one giant
   sparse raw-sample table.
7. Keep direct HTTPS ingest for constrained firmware devices; do not force
   MQTT into the plug firmware just to unify the hub's internal event bus.
8. Treat Home Assistant, router telemetry, and managed-switch telemetry as
   high-leverage zero-hardware-cost covariates and elevate them in planning
   ahead of most BLE/SDR work.
9. Treat Enphase PLC link-quality discovery (`A4`) as an explicit research
   deliverable, not a "probably out of scope" nice-to-have.
10. Elevate Theengs / free-BLE-covariate investigation (`E5`) into the near-term
    research queue because it may yield zero-hardware-cost environmental data
    for homes that already have BLE sensors.
11. Keep the cross-modal correlation storage layer in view early:
    common envelope + modality-specific physical tables is correct, but the
    design must leave room for a cross-modal materialized view / fast time-bucket
    lookup path and should be reviewed with analytics before lock-in.
12. Document the Sonoff S31 bench-debug constraint clearly: when a serial adapter
    is attached to the shared UART pins, `.48` can validate boot / central behavior
    but may not validate real CSE7766 metering at the same time.
13. Treat the current `0.1.29` G2 timing capture as an early positive signal, not
    a finished conclusion:
    - `.48` currently averages about `+10.6 ms` offset with low-RTT samples
    - `.69` currently averages about `-21.7 ms`
    - keep measuring before locking architecture assumptions

1. Move hosted firmware delivery under the central server path tree, preferably under `/rebooter/`, instead of depending on a root-level site file.
2. Add bootstrap/main firmware support for trying multiple firmware download locations in order, not just one fixed URL.
3. Include at least one backup firmware-hosting location that is operationally independent from the primary business infrastructure so firmware recovery still works if the primary service is unavailable or the project is no longer maintained.
4. Evaluate practical fallback hosts for long-term resilience, such as public object storage, GitHub Releases, or other durable public file hosting. If a consumer-friendly option such as Google Drive is considered, verify direct-download stability, file size limits, rate limiting, and OTA compatibility before relying on it.
5. Define a canonical firmware URL strategy that cleanly separates:
   - bootstrap/stage-1 firmware source
   - current stable production firmware
   - development/canary firmware
   - fallback/disaster-recovery firmware source

## Completed Checkpoints

- Initial scaffold committed and build-verified.
- Added config schema/default validation, last-known-good config recovery, relay restore persistence, boot warm-up, Wi-Fi-loss watchdog pause, retry limits, and cooldown lockout.
- Added persisted recent event-log storage across reboots.
- Added OTA manager plus local reboot, factory-reset, and firmware upload API endpoints.
- Added local AuthManager with salted password hashes and protected mutating API routes.
