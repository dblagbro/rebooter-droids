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
