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
- Browser automation note: direct fill/type helpers in the in-app browser hit a
  clipboard capability limitation for password entry, so the deep retest used a
  clipboard-paste plus click workflow for the local auth field.
- Fresh reload after the OTA stress pass cleared the transient background-refresh
  failures that appeared while the device was intentionally rebooting.
- The browser log surface exposed historical fetch failures across tabs and should
  be treated as advisory only, not as a strict per-tab verdict.
