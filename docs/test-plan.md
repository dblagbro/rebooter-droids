# Test Plan

**Status:** v0.4.2 baseline — refreshed 2026-05-09 PM after the
session that shipped v0.4.0 → v0.4.2 (watchdog rules + password
reset + watchdog runtime). Suite lives in `tests/qa/` (31 files,
242 tests).

**Health snapshot (2026-05-09 PM):**
- Isolated-file run of every test file: 7 real failures
  (BUG-024 stale tests + BUG-025 timeout config + BUG-026 cookie
  name + BUG-022 + BUG-023). 235 pass.
- Full-suite run: 34 failures — 27 of those are cascade victims
  of BUG-021 (non-isolated session-scoped admin token).
- New test files added this session: `test_v040_watchdog_rules.py`,
  `test_v041_password_reset.py`, `test_v042_watchdog_runtime.py`.
  All 26 new tests pass in isolation AND in the v0.4.x bundle.

## Surface inventory

| # | Surface | Validation method | Notes |
|---|---|---|---|
| 1 | Admin web UI (`/rebooter/app/*`) | Playwright (Chromium headless) | session-cookie behaviour, form submits, navigation |
| 2 | Admin JSON API (`/api/v1/auth/*`, `/api/v1/admin/*`) | direct requests + JSON shape asserts | bearer + cookie auth |
| 3 | Device API (`/api/v1/device/*`) | direct requests with synthetic device tokens | register → heartbeat → poll → result → events → firmware-check loop |
| 4 | Firmware delivery (`/rebooter/firmware/*.bin`) | curl + sha256 verify | served by nginx, no auth |
| 5 | Postgres schema | `psql \d` snapshots, advisory-lock check | bootstrap idempotency |
| 6 | APScheduler `expire_commands` | wall-clock test (issue cmd with low TTL, wait, observe) | single-worker via pg advisory lock |
| 7 | nginx routing (prefix, redirect, alias) | curl with explicit host headers | strict_slashes, trailing-slash, prefix-stripping |
| 8 | Compose orchestration | `docker compose ps`, healthchecks | non-destructive |

## Test classes

### Smoke (run on every deploy)

- `GET /api/v1/version` → 200 with current version
- Admin login (form + JSON) succeeds with username **and** full email
- Dashboard loads, no console errors
- Device-API `register → heartbeat → poll → result` round-trip

### Standard regression (every non-trivial change)

- Auth: login, logout, refresh, /me, JWT vs cookie, expired access, bad refresh
- Devices: list (filters: site/group/search/status), detail, edit metadata,
  send each command type, fan-out to group, supersede semantics
- Groups: create, add member, remove member, send command, delete
- Sites: create, list, delete (with attached device — what happens?)
- Firmware: upload (good sha + bad sha + missing file + duplicate version),
  list, delete, download via nginx with sha verify, deploy to
  device/group/site/all_devices, supersede on second deploy
- Events: ingest 1, ingest 200 (max), ingest 201 (over-cap), filters
- Enrollment tokens: mint, redeem (good, bad, expired, consumed), list

### Deep regression / release hardening (this run)

- Negative tests for every endpoint — wrong methods, malformed JSON,
  missing fields, extra fields, oversize payloads, type mismatches
- Permission tests — unauth API, expired JWT, JWT signed with wrong
  secret, device token used at admin endpoint, admin token used at device
  endpoint
- State-transition tests — group with members → command fan-out, then
  remove a member mid-flight
- Race / concurrency — two admins creating same-named group, two
  concurrent firmware uploads of the same version, two devices sharing
  the same enrollment token attempted simultaneously
- Persistence / reload — sessions survive across container restart,
  bootstrap idempotency on cold-start with existing data
- Cross-feature — apply_config validation enforces locked schema
- Browser specifics — console errors, mixed content, cookie SameSite,
  back-button after logout, multi-tab session interaction

## Coverage gaps (acknowledged)

- No load / soak tests (would benefit from k6 or Locust)
- No automated visual regression (manual eyeball only)
- No DR drill (no backup procedure documented for the Postgres volume)
- No chaos test (kill `rebooter-droids-pg` mid-request)
- No XSS / SSRF probing (basic input handling only)
- ~~No rate-limiting test~~ — shipped post-v0.2.x; lives in
  `test_hardening_probes.py::test_login_rate_limit_kicks_in` (note:
  BUG-025 — needs ≥80 s timeout to complete the post-window verify).
- **No watchdog-runtime end-to-end probe-tick coverage** — v0.4.2
  ships the runtime but tests only exercise the synchronous
  probe-now path. A full end-to-end test (create rule, wait for
  three ticks against a known-failing probe, assert action_fired
  event) would need a 30 s+ wall-clock test — recommend marking
  `@pytest.mark.slow`.
- **No multi-hub sync coverage** — entire RFC-004 surface is
  pre-implementation; no test scaffolding exists yet.
- **No firmware OTA end-to-end coverage** — RFC-002 P1 mirror
  chain is shipped (v0.3.9 + v0.4.0 tests cover URL shapes) but
  no test covers a real device pulling the OTA + applying it.
- **No invitation-redemption end-to-end with email delivery** —
  the redeem flow is tested at the API level, but the email
  pipeline (SMTP → user mailbox) is not exercised end-to-end.

## Coverage added 2026-05-09 PM (this session)

- v0.4.0 — `test_v040_watchdog_rules.py` (10 tests): rule CRUD,
  validation, sentence render, UI render, enable/disable toggle.
- v0.4.1 — `test_v041_password_reset.py` (10 tests): forgot/reset
  GET+POST, non-disclosure, weak-password rejection, mismatched
  password rejection, Notifications tab render, settings-strip
  inclusion.
- v0.4.2 — `test_v042_watchdog_runtime.py` (6 tests): probe-now
  HTTP success/failure, TCP success/failure, no-state-advance
  invariant, unknown-rule 404, button presence on UI.

## How to run

```
cd /mnt/s/code/rebooter-droids
python3 -m pytest tests/qa -x -v
```

Targets the live deployment at <https://www.voipguru.org/rebooter>. Cleans
up its own test artefacts (devices, groups, firmware releases prefixed
`qa-`) on success; on failure leaves them for inspection.
