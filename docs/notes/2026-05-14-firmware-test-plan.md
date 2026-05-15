# Test Plan

Date created: 2026-05-14

## Validation Scope

Current scope: deep regression / release hardening

## Primary Surfaces

1. Local web UI
2. Local JSON API
3. Local auth and protected actions
4. OTA upload flow
5. Config persistence and reload behavior
6. Central heartbeat / preview payload behavior
7. Recovery and status truth surfaces
8. Power telemetry configuration surfaces

## Core Methods

- Browser-driven interaction testing for local UI
- Direct HTTP validation for API success and error paths
- Negative and malformed-input API tests
- Persistence / reload / reboot checks on live bench device
- Build verification
- Log and response-shape inspection

## High-Risk Areas

- Auth coverage consistency across protected endpoints
- Config save / restore edge cases
- OTA progress and reboot handling
- Central status truth and heartbeat field drift
- Recovery-mode and destructive system actions

## Coverage Gaps To Revisit

- Physical-button behavior still requires bench interaction
- Safe-fallback destructive flows require controlled retest windows
- Power sample upload path needs explicit runtime validation against a receiving hub

## 2026-05-14 Executed Coverage

- Build verification:
  - `pio run -e sonoff_s31`
- Direct API checks:
  - `/api/status`
  - `/api/config`
  - `/api/events`
  - relay endpoints with and without auth
  - `/api/system/config-backup`
  - `/api/system/heartbeat-preview`
  - `/api/system/central-diagnostic`
  - malformed JSON to `/api/config/save`
- UI/browser checks:
  - root page load
  - rendered status fields
  - relay button interaction
  - config save interaction
  - console/network error capture
- State/persistence checks:
  - repeated relay toggles
  - config save and reread for device name persistence
- Remediation verification:
  - `scripts/qa-api-regression.ps1`
  - `scripts/qa-deep-api-retest.ps1`
  - `scripts/qa-ota-stress.ps1`
  - `scripts/qa-ui-auth-static-regression.mjs`
  - local proxy UI validation against the live bench device
  - actual device-served UI validation on `.48`:
    - unlock with correct local auth
    - relay off/on
    - config save with a real checkbox state change and revert
    - clear local auth
    - failed unlock with bad password remains locked

## Added Regression Guardrails

- `scripts/qa-api-regression.ps1`
  - public config redaction
  - protected endpoint auth expectations
  - event log chronology fields
  - central diagnostic defaults
  - 405 and favicon checks
- `scripts/qa-ui-auth-static-regression.mjs`
  - auth form presence
  - protected-control marking
  - session-scoped auth persistence
  - auth header attachment for fetch and OTA XHR

## Gaps Discovered During This Run

- Physical button behavior still requires hardware-assisted verification.
- OTA-to-recovery edge was not reproduced in a 3-cycle stress retest on
  `0.1.21-dev-central-safe`, but should still be watched during future OTA work.
- Power sample upload path still needs explicit runtime validation against a receiving hub.
- Browser-plugin console log capture is not a reliable per-tab pass/fail source by
  itself; use DOM/API evidence as the primary signal.
