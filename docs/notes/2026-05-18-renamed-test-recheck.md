# Renamed-test live recheck - 2026-05-18

Scope: live `https://www.voipguru.org/rebooter` hub vs the renamed-test
device local UI shell and local APIs on `http://192.168.1.48`.

Time of capture:
- Hub version endpoint: `v0.5.93`
- Hub/device snapshot: about `2026-05-18T19:00Z`

Method:
1. Log in through the same QA/admin path used by `tests/qa/conftest.py`.
2. Fetch live `/api/v1/admin/devices?status=active`,
   `/api/v1/admin/devices/<id>`, `/app/devices`, and
   `/app/devices/<id>` for the `.48` row.
3. Re-probe `http://192.168.1.48/api/status`, `/api/config`, `/`,
   and `/app.js`.

## Improved findings

1. `.48` still matches end-to-end after the longer soak window
   - hub devices list and detail page both still show
     `Rebooter - renamed test`
   - hub API row is `online` with `central_ok`
   - hub detail reports:
     - `registration_state="active"`
     - `reported_central_state="heartbeat"`
     - `reported_recovery_mode=false`
     - `last_reported_config.device_name="Rebooter - renamed test"`
   - local `/api/status` reports:
     - `device_name="Rebooter - renamed test"`
     - `firmware_version="0.1.37-dev-central-safe"`
     - `central_registered=true`
     - `recovery_mode=false`
     - `health_state="healthy"`
     - uptime about `74k` seconds at capture
   - local `/api/config` still reports
     `device_name="Rebooter - renamed test"`

2. No new UI/API drift was found on the renamed-test device
   - the local root still ships the generic static shell
     (`<title>` / `<h1>` start as `Rebooter`)
   - live `/app.js` still contains the current hydration path
     `state.status.device_name`
   - live `/app.js` still contains the auth-header attach logic for
     protected actions
   - taken together, the browser-visible page should still converge to
     `Rebooter - renamed test` after hydration, consistent with the
     local APIs

## No new regressions found in this pass

- No fresh rename drift was observed on `.48`.
- No recovery regression was observed on `.48`.
- No new mismatch was observed between the live hub UI, hub API, and
  reachable local APIs for the renamed-test device.
