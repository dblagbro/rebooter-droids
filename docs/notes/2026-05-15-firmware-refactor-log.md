# Refactor Log

## 2026-05-14

- scope of refactor:
  - maintainability and remediation pass after QA findings
- key changes:
  - created architecture and design docs
  - tightened public vs protected config behavior
  - added local UI auth/session flow for protected actions
  - improved event-log chronology metadata with `seq`, `boot_id`,
    and `ts_basis`
  - removed unhealthy secondary central default from shipped config validation
  - added API and UI-auth regression scripts
  - fixed a follow-up auth-header merge bug in the browser helper during live retest
- architectural decisions:
  - public local config reads stay available, but central identity/secrets
    are redacted
  - full secret-bearing config export stays on a protected endpoint
  - current-tab session storage is the default local UI auth persistence model
- files impacted:
  - `src/web_server_manager.cpp`
  - `data/index.html`
  - `data/style.css`
  - `data/app.js`
  - `src/config_manager.cpp`
  - `include/types.h`
  - `src/event_log.cpp`
  - `include/event_log.h`
  - `include/firmware_version.h`
- risks:
  - fallback UI and LittleFS UI must stay aligned
  - live-device verification still required for OTA-served assets
- remaining issues:
  - full route-level 405 coverage is still partial
  - button/recovery destructive paths still need hardware-assisted retest
- next recommended actions:
  - finish regression automation
  - validate on `.48`
  - then mirror the artifact and notes to shared locations

## 2026-05-14 Deep Retest Follow-up

- scope of refactor:
  - release-hardening retest on the live `0.1.21-dev-central-safe` artifact
- key changes:
  - reran the API regression sweep on the live bench device
  - reran the deep API retest on the live bench device
  - ran a 3-cycle OTA stress pass on `.48`
  - verified the actual device-served UI end to end for unlock, relay control,
    config save, clear-lock, and failed unlock behavior
  - documented and fixed a live-artifact mismatch where the served `app.js`
    lagged the repo-side auth-header merge fix until the `0.1.21` rebuild
- architectural decisions:
  - actual device-served UI assets are the authoritative release surface for
    UI/auth validation; repo-source and proxy checks are supporting evidence
- files impacted:
  - `bug-log.md`
  - `test-plan.md`
  - `qa-notes.md`
  - `architecture.md`
  - `design.md`
- risks:
  - button/recovery destructive paths still need hardware-assisted retest
  - power-sample upload path still lacks a live receiving-hub validation pass
- remaining issues:
  - no confirmed regression remained in the remediated bug set after the 0.1.21 pass
  - OTA recovery-mode quirk was not reproduced, but should still be watched
- next recommended actions:
  - mirror the `0.1.21` artifact and retest notes to shared locations
  - do the hardware button/recovery retest next

## 2026-05-15 Recovery/Reset Hardening

- scope of refactor:
  - cleanup of destructive-path semantics after bench proof exposed an
    ambiguous recovery/factory-reset progression
- key changes:
  - explicit recovery provisioning now forces a clean reboot into a normal boot
  - factory reset now clears recovery markers, event log history, and
    provisioned Wi-Fi credentials before restart
  - central-command and API-triggered factory reset paths now share the
    same Wi-Fi-clearing behavior as the button path
  - destructive proof harness now requires auth up front and stops with
    explicit manual-action-needed output when the device leaves LAN reachability
- architectural decisions:
  - destructive-path QA must acknowledge that some proof steps intentionally
    leave the LAN and require out-of-band reprovisioning
  - explicit recovery mode is a temporary provisioning state, not a steady
    post-provisioning operating mode
- files impacted:
  - `src/main.cpp`
  - `src/wifi_manager.cpp`
  - `include/wifi_manager.h`
  - `src/config_manager.cpp`
  - `src/web_server_manager.cpp`
  - `include/web_server_manager.h`
  - `src/central_client.cpp`
  - `include/central_client.h`
  - `scripts/qa-destructive-path-proof.ps1`
- risks:
  - successful explicit recovery reprovision now introduces an automatic reboot
  - full factory reset is intentionally more destructive because Wi-Fi
    credentials are now cleared as well
- remaining issues:
  - live destructive reprovision retest still needs phone-assisted execution
  - physical button hold timings still need hands-on confirmation
- next recommended actions:
  - OTA `0.1.22-dev-central-safe` to the shared test target set as needed
  - run the next assisted recovery/factory-reset proof with phone reprovisioning ready

## 2026-05-15 Assisted Destructive Proof

- scope of refactor:
  - live validation of the new `0.1.22-dev-central-safe` recovery/reset semantics
- key changes:
  - captured a protected baseline backup from `.48`
  - proved explicit recovery boot now returns to a normal boot after phone-assisted reprovisioning
  - proved factory reset now returns a clean unauthenticated/unregistered state
  - restored the bench unit from protected backup after the destructive proof
- architectural decisions:
  - dev-safe bench firmware may return to the LAN automatically after factory reset
    via built-in dev Wi-Fi, which is acceptable for bench validation but must not be
    confused with production-factory provisioning assumptions
- files impacted:
  - `bug-log.md`
  - `qa-notes.md`
  - `test-plan.md`
- risks:
  - physical button hold behavior is still not proven from actual hardware input
- remaining issues:
  - button short/3s/10s/30s proof still pending
  - live power-sample upload validation still pending
- next recommended actions:
  - complete the hardware button proof on `.48`
  - then move to live power-sample upload validation

## 2026-05-15 Real CSE7766 Telemetry

- scope of refactor:
  - replace synthetic bench-only power samples with real CSE7766 reads on the
    Sonoff S31 path while keeping telemetry uploads stable
- key changes:
  - added `src/power_monitor.cpp` and `include/power_monitor.h`
  - parse CSE7766 24-byte UART frames and derive voltage, current, power,
    apparent power, power factor, and cumulative energy
  - expose live power telemetry through `/api/status` and heartbeat preview
  - teach `src/central_client.cpp` to upload real `steady` samples when fresh
    readings are available and fall back to synthetic transport probes otherwise
  - delay power-monitor startup until after the device is stably up on the LAN
    to avoid destabilizing the boot path
  - replace the flaky `curl`-based OTA upload path in `scripts/qa-ota-stress.ps1`
    with a `HttpClient` multipart upload
- architectural decisions:
  - real chip parsing lives in its own module instead of being folded directly
    into the central client
  - boot safety wins over earliest-possible telemetry; real metering can start
    slightly later if that keeps the device out of recovery loops
- files impacted:
  - `include/app_state.h`
  - `include/pins.h`
  - `include/power_monitor.h`
  - `src/power_monitor.cpp`
  - `src/main.cpp`
  - `include/central_client.h`
  - `src/central_client.cpp`
  - `include/status_payload.h`
  - `src/status_payload.cpp`
  - `src/web_server_manager.cpp`
  - `scripts/qa-ota-stress.ps1`
- risks:
  - Sonoff S31 power telemetry still depends on bench/dev wiring assumptions for
    GPIO3 / CSE7766 RX
  - no-load current and power values are intentionally normalized to zero, which
    is correct for the bench case but should still be spot-checked under load
- remaining issues:
  - live loaded-power validation is still pending
  - hub-side power UI still needs an operator glance to confirm the new real
    fields render as expected
- next recommended actions:
  - verify the hub power view now shows real voltage for `.48`
  - validate one loaded outlet/device so current and watts move off zero

## 2026-05-15 Power Capture And Field Characterization

- scope of refactor:
  - turn the real CSE7766 work into a sustained multi-device characterization pass
    covering loaded-power behavior, invalid-frame quality, and the beginnings of
    the G2 timing question
- key changes:
  - added `scripts/qa-power-telemetry-capture.ps1` for background multi-device
    status capture plus periodic event snapshots
  - started a 24-hour capture against `.30`, `.225`, `.67`, and `.48`
  - upgraded and enabled power telemetry on additional field devices to move
    beyond the bench-only `.48` result
  - verified real loaded-power readings on `.30` and `.225`
- architectural decisions:
  - use the long-running capture as both the analytics trace source and the
    anomaly recorder for recovery-state outliers
  - keep anomalous devices (`.48`, `.67`) in the same capture set instead of
    hiding them, so the regression signal is preserved alongside healthy subjects
- files impacted:
  - `scripts/qa-power-telemetry-capture.ps1`
  - `bug-log.md`
  - `qa-notes.md`
- risks:
  - first-boot-after-OTA recovery behavior on some field devices is now a
    release-significant concern for any wider `0.1.25` rollout
  - low-load current remains clamped to zero, which can confuse analytics if the
    hub treats `i_ma` as authoritative at standby loads
- remaining issues:
  - some field devices enter recovery on the first observed boot after OTA to `0.1.25`
  - `.48` remains stuck in recovery mode and is not currently a healthy telemetry source
  - true G2 timing measurement is blocked by the lack of synchronized wall-clock
    timestamps in firmware
- next recommended actions:
  - keep the 24-hour capture running and review the trace tomorrow
  - investigate the OTA/recovery interaction before promoting `0.1.25` broadly
  - decide how low-load current should be represented before analytics rely on it

## 2026-05-15 OTA Recovery Guardrails And G2 Instrumentation

- scope of refactor:
  - harden boot-state accounting and status initialization after field devices
    showed false recovery strikes and stale recovery-state reporting after OTA
    and planned reboots
  - make low-load power semantics clearer for analytics
  - add the first real wall-clock timing instrumentation for G2
- key changes:
  - `ConfigManager` boot-state now tracks firmware-version transitions and
    a `planned_restart` marker
  - intentional reboot / OTA / recovery paths now call
    `prepareForPlannedRestart()` instead of relying only on `markBootHealthy()`
  - `main.cpp` now explicitly reinitializes `RuntimeStatus` and boot-local
    flags at startup so soft restarts do not leak stale recovery-mode state
  - added `src/time_sync_manager.cpp` and `include/time_sync_manager.h`
  - `/api/status`, heartbeat preview, and central power uploads can now expose
    UTC wall-clock timestamps when NTP sync is available
  - low-load current now distinguishes measured current from estimated standby
    current instead of silently overloading `i_ma`
- architectural decisions:
  - planned restarts are a distinct boot-state class and must not be counted as
    crash-like incomplete boots
  - wall-clock telemetry is additive; uptime remains the baseline timing field
  - estimated current is acceptable as long as it is explicitly labeled
- files impacted:
  - `include/config_manager.h`
  - `src/config_manager.cpp`
  - `include/app_state.h`
  - `include/time_sync_manager.h`
  - `src/time_sync_manager.cpp`
  - `src/main.cpp`
  - `src/power_monitor.cpp`
  - `include/central_client.h`
  - `src/central_client.cpp`
  - `src/status_payload.cpp`
  - `src/web_server_manager.cpp`
  - `include/firmware_version.h`
- risks:
  - broader live verification is still pending because several field devices are
    still stuck on the `0.1.25` recovery line
  - `.225` proved the stale-runtime-state portion of the bug, but later
    re-entered recovery, so the OTA/recovery investigation is not fully closed
  - recovery-mode OTA behavior itself may still need a separate transport fix
- remaining issues:
  - event-log corruption under sustained upload load is still open
  - `/api/events` becomes unreliable under larger noisy logs and still needs
    its own focused pass
- next recommended actions:
  - continue moving the remaining recovery-line devices from `0.1.25` to the
    validated `0.1.27` line
  - verify loaded-standby estimated-current fields on a live powered target
  - continue the 24-hour capture and use the new telemetry semantics in the
    analytics handoff notes
