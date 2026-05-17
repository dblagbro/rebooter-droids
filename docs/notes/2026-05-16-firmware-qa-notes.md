# QA Notes

## Environment

- Repo: `C:\dev\rebooter-firmware`
- Live bench device: `http://192.168.1.48`
- Local admin auth in current bench setup:
  - header: `X-Rebooter-Auth`
- Current known admin secret is being used during this QA pass for protected endpoint checks.

## Constraints

- Physical button tests require operator hands on the bench device.
- Some destructive recovery/reset scenarios should be deferred until non-destructive regression coverage is complete.
- Hub-side persistence of new heartbeat fields can only be partially validated from the firmware side unless the hub is explicitly included in scope.

## Notes From This Run

- Playwright browser automation was run through installed Edge channel because the
  default Playwright browser cache was not already populated in the Node REPL environment.
- Useful raw artifacts:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\2026-05-14-firmware-api-regression-results.json`
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\2026-05-14-firmware-browser-regression-results.json`
- The local UI renders current status correctly, including firmware version, but
  protected actions fail because the frontend does not attach auth.
- `GET /api/config` currently leaks central enrollment data without auth.
- The secondary default central URL appears unhealthy and creates noisy event-log churn.
- Event log chronology is hard to interpret after reboot/recovery because timestamps
  appear boot-relative rather than globally ordered.

## Remediation Validation Notes

- The live remediation/retest build is now `0.1.21-dev-central-safe`.
- Verified on the live bench device:
  - `/api/status` now exposes `auth_required`
  - unauthenticated `/api/config` is redacted
  - authenticated `/api/system/config-backup` still returns full config
  - `/api/events` now exposes `seq`, `boot_id`, and `ts_basis`
  - `GET /api/relay/on` returns `405`
  - `/favicon.ico` no longer 404s
  - central diagnostics no longer include the unhealthy `www2` default
- Browser-side auth flow was verified through a local proxy server that serves repo
  `data/` assets while proxying `/api/*` to the live bench device:
  - unlock succeeded
  - relay off/on succeeded
  - config save succeeded
- Deep live retest then verified the actual device-served UI on `.48`, not just the
  proxy-served repo assets:
  - unlock succeeded with the correct local password
  - relay off/on succeeded
  - config save succeeded with a real `manual_button_enabled` false/true round-trip
  - clear-lock restored the disabled protected controls
  - wrong-password unlock attempt stayed locked and surfaced an unauthorized message
- The first deep retest uncovered a live-artifact mismatch: repo `data/app.js` had
  the auth-header merge fix, but the actual device-served `/app.js` was still one
  build behind until `0.1.21-dev-central-safe` was rebuilt and OTA'd.
- Three consecutive OTA cycles on `0.1.21-dev-central-safe` did not reproduce the
  earlier temporary `recovery_mode` return; each cycle came back reachable, stayed
  out of recovery, and returned to `health_state=healthy`.
- A later destructive-path proof attempt on `0.1.21-dev-central-safe` exposed a
  different edge than the OTA-only quirk:
  - explicit recovery boot intentionally moved the device into setup AP /
    phone-assisted reprovisioning territory
  - the LAN-only proof harness wrongly kept marching after reachability was lost
  - manual reprovisioning brought `.48` back with prior config/auth intact,
    proving the factory-reset portion had not actually completed
- Recovery/reset hardening landed in `0.1.22-dev-central-safe`:
  - successful explicit recovery provisioning now forces a clean reboot back
    into a normal boot
  - factory reset now clears recovery markers, persisted event log, and
    provisioned Wi-Fi credentials before restart
  - the destructive proof harness now requires auth up front and stops with
    an explicit manual-action-needed result when the device has left LAN reachability
- Post-fix smoke validation on `.48`:
  - OTA to `0.1.22-dev-central-safe` succeeded
  - plain authenticated reboot returned to the LAN with `recovery_mode=false`
  - `scripts/qa-api-regression.ps1` remained 21/21 green on `0.1.22`
- Full assisted destructive proof on `.48` passed on 2026-05-15:
  - explicit recovery boot required phone reprovision on `Rebooter-Setup-0D0246`
  - after reprovision, the device auto-rebooted into a normal boot with
    `recovery_mode=false`
  - factory reset returned on the LAN in a clean state:
    - `device_name = Rebooter`
    - `auth_required = false`
    - `central_enabled = false`
    - `central_registered = false`
    - `recovery_mode = false`
  - restore from protected backup returned the bench unit to its named/authenticated state
- Real CSE7766 metering landed in `0.1.25-dev-central-safe` and was verified on `.48`:
  - local `/api/status` now exposes live power fields and frame counters
  - bench device reported steady real voltage around `119.6V` from a live outlet
    with no downstream load attached
  - no-load behavior now reports `0 mA`, `0 W`, `0 VA`, and `PF = 1`
  - live power uploads continued successfully after the real sampler came online
- OTA tooling note:
  - ad hoc `curl -F update=@...` uploads were observed to reboot the device while
    leaving the old firmware in place, which created false-positive OTA acceptance
    signals
  - PowerShell `HttpClient` multipart upload produced a clean `200` and successfully
    moved `.48` to `0.1.25-dev-central-safe`
  - `scripts/qa-ota-stress.ps1` should use the `HttpClient` path for reliable OTA QA
  - Browser automation note: direct fill/type helpers in the in-app browser hit a
  clipboard capability limitation for password entry, so the deep retest used a
  clipboard-paste plus click workflow for the local auth field.
- Fresh reload after the OTA stress pass cleared the transient background-refresh
  failures that appeared while the device was intentionally rebooting.
- The browser log surface exposed historical fetch failures across tabs and should
  be treated as advisory only, not as a strict per-tab verdict.

## 2026-05-15 Power / G2 Characterization Notes

- 24-hour capture is running in the background from:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\power-capture-2026-05-15-live`
  - script: `scripts/qa-power-telemetry-capture.ps1`
- Current capture set:
  - `.30` `http://192.168.1.30`
  - `.225` `http://192.168.1.225`
  - `.67` `http://192.168.1.67`
  - `.48` `http://192.168.1.48`
- Healthy real-telemetry devices so far:

## 2026-05-16 Overnight Soak / Root-Cause Update

- The 24-hour power capture completed at `2026-05-16 15:49 -04:00`.
- The separate G2 timing run for `.48` / `.69` completed earlier at
  `2026-05-15 23:19 -04:00`.
- The headline finding changed materially overnight:
  `0.1.29-dev-central-safe` is not just dealing with a `.225`-only outlier.
  By the end of the soak, `.48`, `.67`, `.69`, `.30`, and `.225` were all
  reporting `recovery_mode=true`.
- Focused split-test on `.225`:
  - with `central.enabled=false` and `power.enabled=false`, the device stayed
    healthy through the 90-second healthy-mark window and beyond
  - with `central.enabled=true` and `power.enabled=false`, the device still
    returned to `recovery_mode`
  - this narrows the remaining trigger to the broader central-enabled path,
    not specifically the power-upload path
- Captured event snapshots from the soak show repeated central / firmware
  transport failures followed by steadily declining free heap on affected
  devices, for example on `.30`:
  - firmware assignment check transport failures
  - heartbeat transport failures
  - command poll transport failures
  - power-sample transport failures
  - free heap stepping down from roughly `19 KB` toward `11 KB`
- Current best diagnosis:
  the central-enabled failure path under repeated HTTPS transport failure is
  still causing enough heap churn and/or fragmentation to drive later early-boot
  failures and auto-recovery, even after the earlier event-log persistence
  mitigation in `0.1.29`.
- OTA QA tooling follow-up completed:
  `scripts/qa-ota-stress.ps1` now distinguishes a true upload rejection from
  the common case where the target has already accepted firmware and rebooted
  before the HTTP response finishes cleanly.
  - `.30` steady around `118.8-119.2V` and about `4.9-5.0W`
  - `.225` steady around `118.8-119.0V` and observed between `0W` and about `2.2W`
- Important nuance on low loads:
  - both `.30` and `.225` can show nonzero watts with `power_current_ma = 0`
  - this is caused by the current clamp in `src/power_monitor.cpp`, not by a dead meter
- Invalid-frame characterization from a 20-second delta sample:
  - `.30`: `127` valid and `86` invalid new frames (`213` classified total)
  - `.225`: `112` valid and `101` invalid new frames (`213` classified total)
  - this is enough to say invalid-frame rate is material and should be treated
    as a real quality signal, not just a curiosity
- Recovery-state OTA edge:
  - `.30` and `.67` both entered recovery mode on the first observed boot after
    OTA to `0.1.25-dev-central-safe`
  - `.30` cleared with a plain reboot and then started real telemetry cleanly
  - `.67` has been less well-behaved and remains a live anomaly subject in the capture
  - `.48` currently remains stuck in `recovery_mode` with no live CSE7766 frames
  - likely partial explanation from git history:
    - the `markBootHealthy()` call in the OTA endpoint was added on 2026-05-14
    - field devices upgraded from `0.1.18-dev-central-safe` therefore start the
      first `0.1.25` boot with at least one inherited incomplete-boot strike
    - some devices then appear to cross into full auto-recovery instead of
      stopping at the expected `count=1` warning state
- G2 timing-measurement blocker:
  - the firmware currently emits `sampled_uptime_seconds`, not a synchronized
    wall-clock timestamp
  - there is no NTP / `configTime()` path in the current firmware tree, so true
    cross-device drift measurement cannot be done honestly yet without additional
    instrumentation
- Adjacent issue found under sustained power-upload logging:
  - one `.30` event message captured binary garbage inside a central upload URL,
    suggesting event-log string corruption under load
- Follow-on remediation work in progress on 2026-05-15:
  - `0.1.27-dev-central-safe` adds two layers of protection against false
    post-OTA / post-reboot recovery strikes:
    - firmware-version-aware boot-state handling
    - explicit `planned_restart` boot-state marking for OTA, API reboot,
      button reboot, and recovery transitions
    - plus an explicit `RuntimeStatus` reset at boot so soft restarts do not
      leak stale recovery-state fields into later normal boots
  - `0.1.27` also adds additive G2 instrumentation:
    - `time_synced`
    - `wall_clock_unix_ms`
    - `power_last_sample_unix_ms`
    - `sampled_unix_ms` on uploaded power rows when sync is available
  - low-load current is now represented more honestly:
    - `power_current_ma` stays measured current
    - `power_current_estimated` / `power_estimated_current_ma` expose the
      standby-load estimate when the measured-current clamp suppresses a noisy
      sub-50mA reading
  - current operational state of the capture set:
    - `.30`, `.67`, and `.48` remain on the `0.1.25` recovery line
    - `.225` was the first device moved forward for live verification
    - `.48` is timing out on protected endpoints, so it is not the right
      verification target for the next OTA pass until it is cleared
    - no phone-assisted Wi-Fi reprovisioning is required at the moment because
      the devices remain on the LAN despite being in recovery mode
  - live verification update:
    - `.225` successfully moved to `0.1.27-dev-central-safe`
    - after OTA and a controlled reboot, it temporarily returned in normal mode with:
      - `recovery_mode = false`
      - `central_state = idle`
      - `health_state = healthy`
      - `time_synced = true`
      - `wall_clock_unix_ms` populated
    - this confirmed that the user-facing `recovery_mode` loop on `.225` was
      not purely a boot-state issue; stale runtime status across soft restarts
      was also part of the problem
    - however, `.225` later re-entered `recovery_mode`, so the OTA/recovery
      investigation is still open even though the stale-status portion is now better understood
  - `0.1.28-dev-central-safe` follow-up:
    - `.69` upgraded cleanly from `0.1.18-dev-central-safe` to `0.1.28-dev-central-safe`
      and stayed healthy with:
      - `recovery_mode = false`
      - `reset_reason = "Software/System restart"`
      - `time_synced = true`
      - `wall_clock_unix_ms` populated
    - `.225` upgraded cleanly to `0.1.28-dev-central-safe` via the `HttpClient`
      OTA harness and initially returned in normal mode, but one cycle reproduced
      a delayed crash:
      - first reachable at about 18 seconds
      - normal/healthy through roughly uptime `72s`
      - then a spontaneous reboot into recovery with `reset_reason = "Exception"`
    - central split-test results on `.225`:
      - with `central.enabled = false`, the device stayed healthy past `130s`
      - during that stable run, low-load telemetry looked correct:
        - about `118.7V`
        - about `2.3W`
        - `power_current_ma = 0`
        - `power_current_estimated = true`
        - `power_estimated_current_ma = 20`
      - after re-enabling central and rebooting again, `.225` survived multiple
        heartbeat windows past `140s`, so the remaining failure is currently best
        described as central-path-related and intermittent rather than a simple
        deterministic boot bug
    - `.48` remains reachable on the LAN only as a timeout target:
      - root `/` times out
      - `/api/status` times out
      - it is still not a good OTA target for widening rollout
  - `0.1.29-dev-central-safe` follow-up:
    - root-cause narrowing on `.48` moved from "generic recovery weirdness" to
      a concrete heap-churn pattern:
      - serial/ELF decode showed delayed failures in BearSSL allocation,
        `LittleFS.open(...)`, and event-log JSON-to-file serialization
      - this matches the central-enabled / power-upload path much better than a
        pure boot-health bug
    - firmware changes in `0.1.29`:
      - event-log writes are now deferred / coalesced instead of rewriting the
        file on every event
      - explicit `flush()` is used before reboot / recovery / factory-reset
        transitions so user-visible critical events are still persisted
      - successful steady-state power-upload events are no longer persisted every
        batch
      - central heartbeat / poll / firmware-check / power-upload work is now
        staggered so the ESP8266 avoids back-to-back TLS-heavy bursts
    - live `.48` OTA to `0.1.29` passed:
      - first reachable about 14 seconds after upload
      - healthy through 118+ seconds with `central.enabled=false`
      - after re-enabling central, the device stayed healthy for 210+ seconds,
        returned `central_state=heartbeat_ok` and then `idle`, and did not
        reproduce the old `60-90s` crash window
      - note: `.48` still reported `power_chip_seen=false` during serial-adapter
        attachment, so this specific run validates central stability, not live
        CSE7766 capture on `.48`
    - live `.69` OTA to `0.1.29` also passed:
      - first reachable about 15 seconds after upload
      - no recovery mode
      - healthy through 118+ seconds on the new build
    - additional widening pass:
      - `.67` moved from the `0.1.25` recovery line to `0.1.29`
        - first OTA attempt was ambiguous from the client side, but the second
          retry landed cleanly
        - current long-running capture now shows `.67` healthy with live low-load
          speaker telemetry around `119.6V` / `2.3W`
      - `.30` moved from the `0.1.25` recovery line to `0.1.29`
        - the OTA harness reported a canceled-task upload failure, but the device
          did in fact boot into `0.1.29` and stay healthy
        - current long-running capture shows `.30` healthy with live power around
          `119.8V` / `4.9W`
      - `.225` remains the outlier on `0.1.29`
        - after re-enabling both central and power, it returned to
          `recovery_mode=true` with `reset_reason=\"Exception\"`
        - `power_chip_seen=false` and `power_source=\"none\"` in the failure state
        - this means `0.1.29` improved the line overall, but did not fully close
          the delayed exception path on `.225`
    - G2 timing capture has been restarted against the stable pair:
      - `.48`
      - `.69`
