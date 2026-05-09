# Test Plan

**Status:** v0.1.2 baseline — first formal test plan. Suite lives in
`tests/qa/`.

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
- No rate-limiting test — system has no rate limiting (logged as a
  hardening finding)

## How to run

```
cd /mnt/s/code/rebooter-droids
python3 -m pytest tests/qa -x -v
```

Targets the live deployment at <https://www.voipguru.org/rebooter>. Cleans
up its own test artefacts (devices, groups, firmware releases prefixed
`qa-`) on success; on failure leaves them for inspection.
