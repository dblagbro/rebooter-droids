# Test Plan

**Status:** v0.5.33 baseline — refreshed 2026-05-15 after the
2026-05-14/15 push that shipped v0.5.15 → v0.5.33 (19 versions:
subpackage refactor, B17 integrations, B16 full power-monitoring
track, drift visibility, cost calc + CSV, Phase 1D power probes,
firmware-team alignment-plan coordination, docs cleanup).

Suite lives in `tests/qa/` — **55 test files** as of this refresh
(up from 31 in the prior plan; ~+24 files across v0.5.x).

**2026-05-15 regression-sweep verdict** (deep post-refactor pass):
14 of 14 covered rounds returned the expected behaviour or
surfaced a recorded bug. Two new bugs filed (BUG-054 pre-existing
latent; BUG-055 introduced by v0.5.17/.23/.32 inheriting the
v0.5.9-only per-kind validator pattern). Full sweep map in
`docs/bug-log.md` § "2026-05-15 regression sweep — coverage map".

### Surface inventory — updated post-v0.5.x

| # | Surface | Validation method | Coverage status |
|---|---|---|---|
| 1 | Admin web UI `/rebooter/app/*` (incl. new `/app/power`, `/app/settings/integrations`, drift chips) | Playwright (Chromium headless) + curl-with-grep | partial — new pages render-verified, not interaction-verified |
| 2 | Admin JSON API `/api/v1/auth/*`, `/api/v1/admin/*` | direct requests + JSON shape | **good** — v0.5.x added per-feature test files for B17, B19, B20, B21, B23, B24 |
| 3 | Device API `/api/v1/device/*` | direct requests with synthetic device tokens | partial — heartbeat-reported_config path tested manually 2026-05-15 (R7), not in CI suite |
| 4 | Firmware delivery | curl + sha256 verify | tested |
| 5 | Postgres schema | `psql \d` + Base.metadata.create_all() | **good** — new tables (`external_sensor_sources`, `external_sensor_samples`, `device_power_samples`, `device_power_rollups`) verified 2026-05-15 |
| 6 | APScheduler — 5 jobs now (`expire_commands` 30s, `watchdog_tick` 10s, `schedule_tick` 30s, `external_sensors_tick` 30s, `power_rollups_daily` 02:00 UTC cron) | wall-clock + log inspection | partial — 4 of 5 ticks observed firing; the 02:00 UTC cron not yet observed |
| 7 | nginx routing | curl with explicit hosts | tested |
| 8 | Compose orchestration | `docker compose ps`, healthchecks | tested |
| 9 | Long-poll `/device/commands` (RFC 7240) | parallel curl + concurrency | tested 4-wide 2026-05-15 (R10); **NOT tested at saturation** (8+ concurrent → thread-pool exhaustion behaviour unknown) |
| 10 | Power telemetry ingest → query → rollup → chart | live API E2E | tested 2026-05-15 (R4) — math verified end-to-end |
| 11 | Cost calc + CSV export | live API edge cases | tested 2026-05-15 (R5/R6) — bad input + unknown window + clear flow |
| 12 | Drift detection (B21 + Phase 4A) | live API against real `last_reported_config` | tested 2026-05-15 (R7) — drift surfaced with real firmware-reported config from `0.1.19-dev-central-safe` |
| 13 | Probe-kind contract (14 kinds canonical) | API + dispatch path | tested 2026-05-15 (R3 + R3b) — **BUG-054 surfaced** on `custom` kind |
| 14 | Per-kind probe-field validation | negative tests | tested 2026-05-15 (R9) — **BUG-055 surfaced** — 12/15 false-201 |
| 15 | Subpackage re-export contract (v0.5.15 + v0.5.18/.21) | docker exec python -c imports | tested 2026-05-15 (R2) — 15 modules + 30 symbols clean |

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

## Coverage gaps (refreshed 2026-05-15)

**Pre-existing gaps still open:**
- No load / soak tests (would benefit from k6 or Locust)
- No automated visual regression (manual eyeball only)
- No DR drill (no backup procedure documented for the Postgres volume)
- No chaos test (kill `rebooter-droids-pg` mid-request)
- No XSS / SSRF probing (basic input handling only)
- **No watchdog-runtime end-to-end probe-tick coverage** — manual
  `probe-now` path is well-tested; no test waits for three ticks
  + asserts `action_fired` event row. Recommend a
  `@pytest.mark.slow` wall-clock test.
- **No multi-hub sync coverage** — RFC-004 still pre-implementation
- **No firmware OTA end-to-end coverage** — URL shapes tested;
  device-side fetch+apply not exercised in CI
- **No invitation email pipeline E2E** — API surface tested; SMTP →
  user mailbox path not exercised

**New gaps surfaced by the v0.5.x push (HIGH PRIORITY to close):**
- **No automated test for per-kind probe-field validation
  (BUG-055)** — the negative cases R9 exposed should be
  parameterised into a `test_v053x_probe_validation.py` file once
  the fix lands.
- **No automated test for power telemetry E2E** — R4 was hand-run
  during the 2026-05-15 sweep. Recommend a slow-marked test that
  registers a synthetic device, POSTs samples, calls the rollup
  job, and asserts the chart-data shape.
- **No automated test for drift detection** — R7 was hand-run.
  Recommend a test that sets `desired_config`, checks
  `desired_config_drift_summary` is populated correctly, then
  clears.
- **No automated test for the 7 new integration / power probe
  kinds at the runtime tick level** — `test_v0525_*` covers
  validation acceptance only; `_run_probe` dispatch was
  hand-tested 2026-05-15 (R3b).
- **No automated test for cost calc + CSV export** — R5/R6 were
  hand-run.
- **SQLite-broken `test_v0514_*.py`** — passes on Postgres,
  fails on SQLite because `BigInteger` PKs don't ROWID-alias.
  Either fix with `.with_variant(Integer, 'sqlite')` (precedent in
  `DeviceHeartbeat`) or mark `@pytest.mark.postgres_only` and
  document.
- **Subpackage import contract** — manual `docker exec python -c`
  smoke covers it. No CI test would fail-loud if a future refactor
  dropped one of the 30 re-exports.

**Surfaces NOT validated this sweep** (per the bug-log § coverage map):
- Playwright on `/app/power`, `/app/settings/integrations`, drift chips
- Long-poll under saturation (>7 concurrent)
- Cron tick at 02:00 UTC observed firing in production
- nginx layer with the new `/app/power` paths
- Phase 3 / 4B / 4C — code not yet written
- SSH to `tmrwww02` — unblocked but never exercised

## Coverage added 2026-05-09 PM

- v0.4.0 — `test_v040_watchdog_rules.py` (10 tests)
- v0.4.1 — `test_v041_password_reset.py` (10 tests)
- v0.4.2 — `test_v042_watchdog_runtime.py` (6 tests)

## Coverage added v0.5.x ship line (2026-05-09 → 2026-05-14)

- v0.5.0+ — RBAC role bindings + invite/audit-history slices
- v0.5.09 — `test_v0509_internet_multitarget.py` — multi-target
  internet watchdog probe semantics
- v0.5.11 — `test_v0511_scan_download_url.py` — scanned-release
  per-channel URL hot-fix (B22)
- v0.5.13 — `test_v0513_firmware_content_changed_scan.py` — B19
  re-hash on existing entries
- v0.5.14 — `test_v0514_deployment_completion_and_status_truth.py`
  — 3 unit tests (Postgres-backed; **SQLite path broken**, see
  gaps above)
- v0.5.14 — `test_v0514_inline_toggle.py` — devices-list inline
  toggle (B18) + payload contract
- v0.5.20 — `test_v0520_long_poll_commands.py` — RFC 7240
  `Prefer: wait=N` happy path + timeout + early-return; marked
  `@pytest.mark.slow`
- v0.5.25 — `test_v0525_integration_probe_kinds_canonical.py` —
  parametrised rule-creation for all 4 integration probe kinds
- (also retroactively in `test_admin_api.py` + `test_device_api.py`:
  rename + power-samples API contract)

## Coverage that SHOULD be added (per 2026-05-15 sweep)

In priority order:

1. **`test_v053x_probe_validation.py`** — parametrised negative
   tests covering all 14 probe kinds. Catches BUG-055
   regression. ~20 cases.
2. **`test_v053x_power_telemetry_e2e.py`** — slow-marked.
   Register synthetic device → POST samples → call
   `compute_daily_rollups()` → assert chart-data shape +
   `device_power_rollups` row matches expected aggregation.
3. **`test_v053x_drift_detection.py`** — set `desired_config`,
   verify `desired_config_drift_summary` shape, verify status-
   page attention item appears, clear, verify cleanup.
4. **`test_v053x_integration_probe_runtime.py`** —
   parametrised across the 7 new (4 integration + 3 power)
   kinds: create rule → probe-now → assert sensible outcome +
   details shape (stale_sample when no source / no_samples when
   no device samples).
5. **`test_v053x_subpackage_imports.py`** — smoke test that
   every public symbol re-exported from `services/devices/` and
   `services/watchdog_runtime/` resolves. Catches refactor regressions.
6. **`test_v053x_cost_and_csv.py`** — rate set/clear/bad input
   + CSV export header + content-type + bad-window fallback.

## How to run

```
cd /mnt/s/code/rebooter-droids
python3 -m pytest tests/qa -x -v
```

Targets the live deployment at <https://www.voipguru.org/rebooter>. Cleans
up its own test artefacts (devices, groups, firmware releases prefixed
`qa-`) on success; on failure leaves them for inspection.
