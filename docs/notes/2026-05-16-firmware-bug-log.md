# Bug Log

This file tracks confirmed bugs, likely bugs needing confirmation,
quality weaknesses, and release-hardening findings for the firmware
project.

## 2026-05-14 QA Regression Pass

### 1. Unauthenticated config endpoint leaks central enrollment data

- date: 2026-05-14
- title: `GET /api/config` exposes central enrollment token without auth
- severity: high
- area/component: local API / auth / configuration
- environment/context: live bench device `http://192.168.1.48` on `0.1.19-dev-central-safe`
- reproduction steps:
  1. Send `GET /api/config` with no auth header.
  2. Inspect returned JSON.
- expected result:
  - unauthenticated callers should either be rejected or receive a redacted config shape.
- actual result:
  - endpoint returns full device configuration including `central.enrollment_token`,
    device identifiers, watchdog configuration, and other internal state.
- evidence:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\2026-05-14-firmware-api-regression-results.json`
- likely cause if known:
  - `/api/config` is intentionally left open while the UI depends on it, but the
    response shape was not reduced when auth-protected central configuration was added.
- recommended fix:
  - either require auth for `/api/config` when provisioned, or add a safe public
    read model that strips enrollment and central identity data.
- status: fixed in `0.1.20-dev-central-safe`, deeply retested on `0.1.21-dev-central-safe`

### 2. Local UI cannot perform protected actions once auth is provisioned

- date: 2026-05-14
- title: browser UI never sends `X-Rebooter-Auth`, so protected actions fail
- severity: high
- area/component: local web UI / auth integration
- environment/context: Edge Playwright-style browser automation against live bench device
- reproduction steps:
  1. Open the device root page after local admin credentials are provisioned.
  2. Click relay toggle.
  3. Attempt config save.
- expected result:
  - UI should support an auth flow or stored session/token and successfully perform protected actions.
- actual result:
  - relay toggle fails with `unauthorized`
  - config save fails with `unauthorized`
  - browser console shows 401s for protected requests
- evidence:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\2026-05-14-firmware-browser-regression-results.json`
  - `C:\dev\rebooter-firmware\data\app.js` does not set `X-Rebooter-Auth`
- likely cause if known:
  - frontend fetch/XHR helpers never attach the auth header used by protected endpoints.
- recommended fix:
  - implement a real local auth flow and attach the auth token/header for protected requests.
- status: fully fixed in `0.1.21-dev-central-safe`

### 3. Default secondary central base URL is misconfigured and produces repeated 404/transport noise

- date: 2026-05-14
- title: `www2.voipguru.org` fallback path is unhealthy by default
- severity: medium
- area/component: central integration / operational defaults
- environment/context: live central diagnostic on bench device
- reproduction steps:
  1. Call `GET /api/system/central-diagnostic` with auth.
  2. Inspect the second entry under `targets`.
- expected result:
  - both default central base URLs should serve the expected API paths.
- actual result:
  - primary URL returns 200 from `/api/v1/version`
  - secondary URL returns 404 from `/rebooter/api/v1/version`
  - event log also contains repeated transport or fallback noise tied to the second URL
- evidence:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\2026-05-14-firmware-api-regression-results.json`
  - `GET /api/events` output on 2026-05-14
- likely cause if known:
  - fallback host is not provisioned with the same route tree as the primary.
- recommended fix:
  - either fix the secondary host or remove it from default firmware config until it is valid.
- status: fixed in `0.1.20-dev-central-safe`, deeply retested on `0.1.21-dev-central-safe`

### 4. Persisted event log uses ambiguous non-monotonic timestamps across reboots

- date: 2026-05-14
- title: event history becomes hard to interpret after reboot or recovery cycles
- severity: medium
- area/component: event log / observability
- environment/context: live `GET /api/events` response
- reproduction steps:
  1. Trigger multiple reboots/OTA/recovery cycles over time.
  2. Fetch `/api/events`.
  3. Observe repeated low `ts` values and interleaved entries from different boots.
- expected result:
  - persisted event history should remain chronologically interpretable.
- actual result:
  - entries appear to use uptime-relative timestamps and are interleaved after reboots,
    producing a non-monotonic timeline that is difficult to reason about.
- evidence:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\2026-05-14-firmware-api-regression-results.json`
- likely cause if known:
  - event log stores relative timestamps without a boot/session discriminator or wall-clock anchor.
- recommended fix:
  - include a boot/session id and/or absolute timestamp when available, or clearly expose
    boot-relative semantics in the API shape.
- status: fixed in `0.1.20-dev-central-safe`, deeply retested on `0.1.21-dev-central-safe`

### 5. UI produces avoidable console noise from missing favicon

- date: 2026-05-14
- title: root page requests missing `/favicon.ico`
- severity: low
- area/component: local web UI / asset completeness
- environment/context: browser automation against live device root page
- reproduction steps:
  1. Open `/` in a browser.
  2. Observe console/network output.
- expected result:
  - page loads without missing-asset noise.
- actual result:
  - browser console records a 404 for `/favicon.ico`
  - direct request returns `Not found: /favicon.ico`
- evidence:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\2026-05-14-firmware-browser-regression-results.json`
- likely cause if known:
  - no favicon route or asset exists.
- recommended fix:
  - add a tiny favicon asset or suppress the request via explicit markup.
- status: fixed in `0.1.20-dev-central-safe`, deeply retested on `0.1.21-dev-central-safe`

### 6. Unsupported methods return generic 404 instead of 405

- date: 2026-05-14
- title: method mismatch on API routes is not distinguished cleanly
- severity: low
- area/component: local API / protocol polish
- environment/context: direct HTTP checks
- reproduction steps:
  1. Send `GET /api/relay/on`.
- expected result:
  - endpoint should ideally return `405 Method Not Allowed`.
- actual result:
  - device returns `404 Not Found`.
- evidence:
  - direct `curl -i -X GET http://192.168.1.48/api/relay/on`
- likely cause if known:
  - routes are only registered per-method and fall through to the generic not-found handler.
- recommended fix:
  - add explicit method handling or a thin router-level 405 response where practical.
- status: fixed in `0.1.20-dev-central-safe`, deeply retested on `0.1.21-dev-central-safe`

### 7. OTA reboot briefly returned to recovery mode once during remediation retest

- date: 2026-05-14
- title: one OTA remediation retest came back in `recovery_mode` until a normal reboot cleared it
- severity: medium
- area/component: OTA / recovery interaction
- environment/context: live bench device `http://192.168.1.48` during `0.1.20-dev-central-safe` remediation pass
- reproduction steps:
  1. Upload the remediation build over local OTA.
  2. Wait for the device to come back.
  3. Inspect `GET /api/status`.
- expected result:
  - device should return in normal mode after successful OTA reboot.
- actual result:
  - one retest returned with `recovery_mode=true`, `last_known_good_restored=true`,
    and `central_state="recovery_mode"` until a normal authenticated reboot was issued.
- evidence:
  - live `GET /api/status` capture during this remediation pass
- likely cause if known:
  - unclear; may be a stale recovery flag or an OTA/restart edge case rather than
    the newly remediated auth/config work.
- recommended fix:
  - reproduce intentionally before changing code; inspect recovery-flag lifecycle
    across OTA completion and restart.
- status: not reproduced in 3-cycle OTA stress on `0.1.21-dev-central-safe`; continue monitoring

### 8. Live device-served UI asset lagged the repo fix for protected fetch headers

- date: 2026-05-14
- title: actual device-served `app.js` still dropped auth headers on option-bearing protected requests
- severity: medium
- area/component: local web UI / LittleFS-served assets / release artifact validation
- environment/context: live bench device `http://192.168.1.48` on `0.1.20-dev-central-safe`
- reproduction steps:
  1. Compare the repo `data/app.js` helper against the actual device-served `/app.js`.
  2. Inspect the `fetchJson(...)` request shape.
  3. Exercise protected UI actions that also supply request headers, such as config save.
- expected result:
  - the device-served UI should match the repo fix and preserve `X-Rebooter-Auth`
    when extra request headers are present.
- actual result:
  - the live device-served `app.js` still used the pre-fix merge order, allowing
    `options.headers` to overwrite the auth-bearing header set on protected requests.
- evidence:
  - live `GET http://192.168.1.48/app.js` inspection during deep retest
  - actual device-served UI retest on `.48`
- likely cause if known:
  - the repo source had been corrected, but the deployed LittleFS-served artifact
    on the device was still one build behind.
- recommended fix:
  - rebuild and OTA a new firmware artifact, then verify the actual device-served
    `/app.js` rather than relying only on repo source or proxy-served assets.
- status: fixed in `0.1.21-dev-central-safe`

### 9. Destructive-path proof exposed ambiguous recovery/factory-reset progression

- date: 2026-05-15
- title: explicit recovery boot plus LAN-only harness produced an ambiguous "half-reset" bench state
- severity: medium
- area/component: recovery / factory reset / destructive QA harness
- environment/context: live bench device `http://192.168.1.48` during destructive-path proof on `0.1.21-dev-central-safe`
- reproduction steps:
  1. Trigger the scripted destructive-path proof against a LAN-reachable bench device.
  2. Let the script continue after the device leaves LAN reachability for setup AP / recovery behavior.
  3. Reprovision the device manually from a phone and inspect returned state.
- expected result:
  - the proof harness should stop cleanly when manual provisioning becomes necessary,
    and the firmware should make recovery-vs-reset progression obvious.
- actual result:
  - the device returned on the LAN still carrying prior config/auth because the
    proof run did not actually prove a full factory reset.
  - explicit recovery provisioning also left the device in a recovery boot until
    a subsequent reboot cleared it.
- evidence:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\destructive-proof-2026-05-15-101206`
  - live `GET /api/status` after manual reprovisioning showed `recovery_mode=true`
- likely cause if known:
  - the proof harness assumed uninterrupted LAN control even after forcing the
    device into setup AP / recovery flows.
  - recovery and factory-reset cleanup semantics were not explicit enough around
    provisioning credentials, recovery markers, and handoff back to a normal boot.
- recommended fix:
  - make successful explicit recovery provisioning auto-reboot back into a normal boot
  - clear recovery markers, event log state, and provisioned Wi-Fi credentials on factory reset
  - make the destructive QA harness fail fast and require manual operator steps when LAN reachability is intentionally lost
- status: fixed in `0.1.22-dev-central-safe`; live assisted destructive proof passed on 2026-05-15

### 10. First boot after OTA to `0.1.25-dev-central-safe` can fall into recovery mode on field devices

- date: 2026-05-15
- title: some healthy long-uptime field devices auto-enter recovery on the first boot after OTA to `0.1.25`
- severity: high
- area/component: OTA / boot-health / recovery interaction
- environment/context:
  - live field devices `http://192.168.1.30`, `http://192.168.1.67`
  - target firmware `0.1.25-dev-central-safe`
- reproduction steps:
  1. Start from a healthy long-uptime device on `0.1.18-dev-central-safe`.
  2. Upload `0.1.25-dev-central-safe` over local OTA.
  3. Wait for the device to come back and inspect `/api/status` and `/api/events`.
- expected result:
  - device should return in normal mode on the first post-OTA boot.
- actual result:
  - `.30` and `.67` both returned with `recovery_mode=true`,
    `last_known_good_restored=true`, and `central_state="recovery_mode"` on the
    first observed boot after OTA.
  - on `.30`, a plain authenticated reboot cleared recovery mode and immediately
    restored live power telemetry.
  - on `.67`, recovery mode also cleared after an explicit reboot, but the path
    remains inconsistent enough that it needs a focused root-cause pass.
- evidence:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\power-capture-2026-05-15-live\events-192.168.1.30-20260515-154926.json`
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\power-capture-2026-05-15-live\events-192.168.1.67-20260515-154926.json`
  - live `/api/status` captures during the 2026-05-15 power-validation pass
- likely cause if known:
  - this turned out to be a layered problem:
    - older firmware images could hand the next image an inherited incomplete-boot strike
    - intentional restarts were not explicitly marked as planned, so some
      software-triggered reboots could still be counted like crashes
    - `RuntimeStatus` was not being explicitly reset at boot, so soft restarts
      could leak stale `recoveryMode` / central-state values into later normal boots
- recommended fix:
  - make boot-state firmware-version-aware
  - mark intentional restarts explicitly in boot-state
  - reinitialize runtime status at startup instead of relying on warm-restart behavior
- status: partially mitigated through `0.1.29-dev-central-safe`; the original boot-state / planned-restart issues were fixed in `0.1.27`, and a new central-path heap-churn mitigation landed in `0.1.29`, but `.225`, `.30`, and `.67` still need fresh revalidation on the new build
  - 2026-05-15 update:
    - `.69` upgraded cleanly to `0.1.28-dev-central-safe` and remained healthy, which
      gives us a good control device for the new boot/timing instrumentation.
    - `.225` was upgraded to `0.1.28-dev-central-safe` and one OTA cycle reproduced a
      delayed `Exception` reset: the device stayed normal until roughly the 60-75s
      window, then rebooted and auto-entered recovery with
      `auto_recovery_triggered=true` / `last_known_good_restored=true`.
    - a split test then showed `.225` is stable for 130+ seconds with
      `central.enabled=false`, which narrows the remaining fault to the central-enabled
      path rather than generic boot, Wi-Fi, or CSE7766 startup.
    - after disabling central, stabilizing, then re-enabling it and rebooting, `.225`
      later survived multiple 60-second heartbeat windows on `0.1.28`, so the
      central-path crash appears intermittent / heap-sensitive rather than strictly
      deterministic.
    - later serial and ELF decoding on `.48` showed the delayed crash pattern was
      landing in:
      - BearSSL validator allocation
      - `LittleFS.open(...)`
      - event-log JSON serialization to file
    - that combination points to heap churn / fragmentation in the central-enabled
      steady-state path rather than a pure boot-health bug.
    - `0.1.29-dev-central-safe` now:
      - defers event-log persistence instead of rewriting the file on every event
      - flushes explicitly only before restart-sensitive transitions
      - removes routine successful power-upload event spam from the persisted log
      - staggers central heartbeat / poll / firmware-check / power-upload work so
        the ESP8266 does not burst several TLS-heavy operations back-to-back
    - live verification on `.48` after OTA to `0.1.29`:
      - device stayed healthy for 210+ seconds with `central.enabled=true`
      - `central_state` advanced through heartbeat and returned to idle
      - the old `60-90s` crash window did not reproduce
    - live verification on `.69` after OTA to `0.1.29`:
      - device upgraded cleanly and stayed healthy through the normal post-OTA window
      - this gives us a second verification target on the new line before widening
        to the rest of Erica's devices
    - live verification on the former recovery-line devices after OTA to `0.1.29`:
      - `.67` upgraded cleanly on retry and is now healthy in normal mode with
        live low-load speaker telemetry (`~2.3W`, `~119.6V`)
      - `.30` also upgraded cleanly and is now healthy in normal mode with
        live power telemetry (`~4.9W`, `~119.8V`)
      - `.225` is the outlier: after `0.1.29` plus `central.enabled=true` and
        `power.enabled=true`, it again returned to `recovery_mode` with
        `reset_reason="Exception"`, `power_chip_seen=false`, and
        `power_source="none"`
    - current best read:
      - `0.1.29` generalized well enough to clear `.67` and `.30`
      - the residual fault is now narrower and should be treated as a `.225` /
        subset-of-devices problem rather than a proven whole-line regression

### 11. Event log can contain corrupted text under sustained power-upload activity

- date: 2026-05-15
- title: one `.30` power-upload event message contained binary garbage in the middle of the URL text
- severity: medium
- area/component: event log / string lifetime / serialization under power telemetry load
- environment/context:
  - live field device `http://192.168.1.30` on `0.1.25-dev-central-safe`
  - sustained 10-second power batch uploads after real telemetry came online
- reproduction steps:
  1. Enable real power telemetry and 10-second batch uploads.
  2. Let the device run under sustained upload traffic.
  3. Fetch `/api/events` and inspect the central-upload messages.
- expected result:
  - event log messages should remain valid UTF-8 / ASCII text and preserve the
    full upload URL and count.
- actual result:
  - one event message in boot `3`, sequence `35`, contains embedded binary
    garbage in the middle of `https://www.voipguru.org/rebooter`.
- evidence:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\power-capture-2026-05-15-live\events-192.168.1.30-20260515-154926.json`
- likely cause if known:
  - unknown; possible string-buffer corruption or serialization misuse while
    recording high-frequency central-upload events.
- recommended fix:
  - reproduce with event logging left enabled under sustained power uploads
  - inspect event-log string ownership / serialization boundaries for central
    upload messages
- status: mitigated in `0.1.29-dev-central-safe`; routine successful power-upload
  events are no longer persisted every batch, and event-log persistence is now
  deferred / coalesced instead of rewriting the log file on every add
  - follow-up verification still needed under longer steady-state soak on
    central-enabled devices

### 12. Low-load telemetry reports nonzero watts with zero current because current is clamped below 50 mA

- date: 2026-05-15
- title: low-current CSE7766 loads keep `i_ma = 0` even when `p_w` is nonzero
- severity: medium
- area/component: power telemetry / CSE7766 low-load handling
- environment/context:
  - live field devices `http://192.168.1.30`, `http://192.168.1.225`
  - `0.1.25-dev-central-safe`
- reproduction steps:
  1. Enable real power telemetry on a lightly loaded device.
  2. Observe `/api/status`.
- expected result:
  - low-load devices should either report a small nonzero current or clearly
    surface that current is only estimated/unknown.
- actual result:
  - `.30` reported about `4.9-5.0W` at about `119V` while `power_current_ma`
    stayed `0`.
  - `.225` later reported about `2.2W` at about `118.8V` while
    `power_current_ma` stayed `0`.
- evidence:
  - live `/api/status` captures and `power-capture-2026-05-15-live\summary.json`
- likely cause if known:
  - `src/power_monitor.cpp` intentionally suppresses measured current when the
    estimated current is below `MIN_MEASURED_CURRENT_A = 0.05f`, which zeroes
    current for light standby loads.
- recommended fix:
  - decide whether to expose estimated low-load current separately, lower the
    clamp, or keep the clamp but make the "power valid / current not trusted"
    semantics explicit in hub-side analytics
- status: additive estimated-current semantics implemented in `0.1.27-dev-central-safe`; live loaded-standby verification confirmed on `.225` under `0.1.28-dev-central-safe`

### 13. OTA stress harness can report upload failure even when the device actually updates successfully

- date: 2026-05-15
- title: local OTA harness sometimes records `upload_accepted=false` when the target has already rebooted into the new firmware
- severity: low
- area/component: QA tooling / OTA stress harness
- environment/context:
  - live field devices `http://192.168.1.30`, `http://192.168.1.67`
  - local script `scripts/qa-ota-stress.ps1`
- reproduction steps:
  1. Use the local OTA stress harness against a recovery-line device.
  2. Observe the HTTP client side of the multipart upload during the reboot boundary.
  3. Compare the harness result to the actual post-boot firmware version.
- expected result:
  - the harness should distinguish a real upload failure from a connection that
    is cut short because the device has already accepted the update and is rebooting.
- actual result:
  - `.30` updated to `0.1.29-dev-central-safe` and stayed healthy, but the
    harness still recorded `upload_accepted=false` with a canceled task.
  - the first `.67` attempt also produced a transport-side failure shape while
    the device rebooted out of recovery, which made the result ambiguous.
- evidence:
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\ota-30-to-0.1.29-results.json`
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\ota-67-to-0.1.29-results.json`
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\ota-67-to-0.1.29-retry-results.json`
- likely cause if known:
  - the client can see a canceled / interrupted request at the same moment the
    device has already accepted the firmware and torn down the connection to reboot.
- recommended fix:
  - teach the harness to weigh the post-boot firmware version and reboot timing
    more heavily than the raw HTTP client exception in this edge case.
- status: mitigated in `scripts/qa-ota-stress.ps1`; the harness now records an
  effective acceptance result when the target clearly rebooted and came back

### 14. Central-enabled long soak still degrades into recovery mode under repeated transport failures

- date: 2026-05-16
- title: repeated central transport failures still drive later auto-recovery on `0.1.29`
- severity: high
- area/component: central client / long-run transport failure handling
- environment/context:
  - `0.1.29-dev-central-safe`
  - observed across `.48`, `.67`, `.69`, `.30`, `.225`
  - split-tested most directly on `http://192.168.1.225`
- reproduction steps:
  1. Run the central-enabled line through a long soak with the device enrolled.
  2. Allow repeated heartbeat / poll / firmware-check / power-upload work to continue.
  3. Observe device status and captured event snapshots over time.
- expected result:
  - the device should remain stable even if some central transport attempts fail.
- actual result:
  - `.225` stays healthy with `central.enabled=false` and `power.enabled=false`,
    but still returns to `recovery_mode` with `central.enabled=true` even when
    `power.enabled=false`.
  - the completed overnight soak later showed `.48`, `.67`, `.69`, and `.30`
    also ending in `recovery_mode=true`.
- evidence:
  - `C:\dev\rebooter-firmware\docs\overnight-capture-and-root-cause-readout-2026-05-16.md`
  - `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\power-capture-2026-05-15-live\summary.json`
  - captured event snapshots under `power-capture-2026-05-15-live\events-*.json`
- likely cause if known:
  - repeated central transport failures still create enough heap churn and/or
    fragmentation to drive later early-boot failures and auto-recovery, even
    after the earlier `0.1.29` event-log persistence mitigation.
- recommended fix:
  - reduce transport-failure amplification under sustained outage
  - widen failure backoff for firmware-check / heartbeat / poll / power transport
  - trim repeated failure logging on the hot path
  - rerun a small controlled soak after the next mitigation
- status: open; this is now the primary firmware blocker
