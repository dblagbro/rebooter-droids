# Test plan

Status: **2026-05-19** — CI gate at ~850 tests run behind nginx
(P-QA gate-1 + gate-2 widening + the **gate-3 backlog cleared in
v0.5.98** + the `tests/unit/` tree, grown by the v0.5.99 watchdog
tick + state-machine slice and the v0.5.100 firmware-deployment
slice). Charter `docs/notes/2026-05-15-pause-state-and-resume-charter.md`
is fully closed.

This document is the canonical description of how rebooter-droids is
tested. It replaces the prior per-sweep verdict-logging version of this
file, and supersedes the historical `qa-notes.md` — which grew to
~105 K tokens of accreted session notes and is now archived at
`docs/sessions/qa-notes-archived-20260519.md` (history only — not a
plan).

## How the suite is shaped today

- **~850 collected test items**, in two trees: most of `tests/qa/`
  (the historical HTTP-integration files, ~64 named per release —
  `test_v0535_*`, `test_v0568_*`, …) plus a growing in-process
  `tests/unit/` tree (~115 tests across the rules-form builders,
  scheduling math, watchdog probe-dispatcher + state machine + tick,
  the announce state machine, the deployments service, and the
  per-service slices listed below).
- The QA tree is **HTTP integration tests** driven with `requests`
  against a *running* server — `conftest.py`'s `base_url` fixture
  defaults to the live deployment (`https://www.voipguru.org/rebooter`)
  and `admin_token` logs in with a real admin. The `tests/unit/`
  tree runs purely in-process against an isolated SQLite via the
  shared `hub_db` fixture — fast, no HTTP, no Docker.
- A subset are Playwright browser tests (`test_responsive.py`,
  `test_ui_flows.py`) — the `chromium_browser` fixture skips them
  cleanly when playwright or the chromium binary isn't available.

This shape was driven by the "poor QA" charter problem: per-release
snapshot tests against production cannot gate a change, and until
2026-05-15 **nothing ran them automatically** on push or PR. The CI
gate cleared that. The shape now adds an in-process tree so
service-layer logic gets fast, HTTP-free coverage too.

## Surfaces under test

| Surface | How |
|---|---|
| Device API `/api/v1/device/*` (register, heartbeat, commands, events, power-samples, firmware, failsafe) | `requests` with synthetic device tokens — **in the CI gate** |
| Admin JSON API `/api/v1/auth/*`, `/api/v1/admin/*` | `requests` + JSON-shape asserts |
| Admin web UI `/app/*` | Playwright (Chromium headless) + curl-with-grep |
| Firmware delivery `/rebooter/firmware/*.bin` | curl + sha256 verify |
| Postgres schema / bootstrap idempotency | `Base.metadata.create_all()` + `_PENDING_COLUMNS` on cold start — **exercised by the CI gate** (fresh DB each run) |
| APScheduler ticks (watchdog, schedules, sensors, rollups, sync) | wall-clock + log inspection |
| nginx routing / prefix-strip / firmware static | **in the CI gate** — the gate runs behind nginx (`ci/nginx.conf`) |

## The CI gate (GitHub Actions — `.github/workflows/ci.yml`)

On every push to `main` and every pull request, CI:

1. **builds the application image** (`docker build`) — catches import,
   syntax and dependency breakage across the whole app;
2. **boots the image against a throwaway Postgres** — proves the app
   starts and self-bootstraps its schema + admin from an empty DB;
3. **runs the `-m ci` test bucket** against that fresh instance.

### What `-m ci` covers

The `ci` marker tags tests **verified green against a fresh ephemeral
instance**. As of the gate-2 widening (v0.5.79), the **gate-3 backlog
cleared in v0.5.98**, the `tests/unit/` tree (with the watchdog tick +
state-machine slice from v0.5.99 and the deployments slice from
v0.5.100), and the nginx front (v0.5.84), that is **~850 tests**
— every test file confirmed to pass `pytest -m ci` twice against a
from-scratch instance (fresh Postgres + populated DB):

- **Registration / device surface** — `test_device_api.py`,
  `test_v0568_adoption_token_redelivery.py`, `test_v027_heartbeat_state.py`
  (the original gate-1 set: the v0.5.36 `site_id` NOT NULL regression
  that 500'd `/register` for ~32 versions can no longer merge silently).
- **Admin API + RBAC + auth** — invites, password reset, session
  revoke/shadow, role bindings, CORS, mass-action gate, per-record audit.
- **Watchdog rules + schedules** — CRUD, validation, the JSON editor,
  the structured edit form (`test_v0578`), probe validation, integration
  probe kinds. *These are the tests that caught the v0.5.77/.78
  regressions — now they gate.*
- **Status / devices / power / firmware UI** and the B11 sync
  applier/emission/natural-key in-process tests.
- **History feed** — chip-filter, multi-source picker, CSV/JSON export,
  free-text search (`test_v0427/0430/0432`). These seed their own
  `watchdog_rule.*` audit activity via a module-scoped autouse fixture
  rather than assuming live data.
- **Settings / wizard / RBAC surfaces** (gate-3 0P bucket) —
  runtime SMTP (`test_v0425`), the per-device upgrade button
  (`test_v0428`), the enrol wizard (`test_v0431`), the firmware settings
  tab (`test_v0433`), role-binding back-compat (`test_v0500`),
  pending-adoption count (`test_v0502`), the devices-list nested-form
  fix (`test_v0503` — seeds its own device), and the scanned-release
  URL guard (`test_v0511` — skips when no firmware artifacts exist).
- **Smoke / auth / routing / admin-API** (gate-3 partial-fail bucket) —
  `test_smoke`, `test_auth_negative`, `test_routing_and_nginx`,
  `test_admin_api`, plus RBAC invites (`test_v02`), input validation
  (`test_v0411`), the maintenance toggle (`test_v047`), and the P1
  shell / theme picker (`test_v030`). The genuinely nginx-layer tests
  in these files `pytest.skip` when the base URL isn't the
  `/rebooter`-prefixed deployment.
- **`tests/unit/`** — the in-process unit-test tree. Pure-function
  tests (the `_rules_forms` form→JSON builders, schedule recurrence
  math, the `device_power` bitfield/taxonomy helpers, the watchdog
  `run_probe` dispatch table — network seams monkeypatched, the
  `commands._validate_payload` schema checks, the canonical
  probe-kind registry contract — `KNOWN_PROBE_KINDS` ==
  `DISPATCHED_PROBE_KINDS` + per-kind `_validate_probe`) need no fixture;
  DB-backed service tests (`create_rule` validation, the
  `upsert_announcement` state machine, the `enrollment`
  mint/consume/revoke service, the `device_power` query/rollup
  surface, the `commands` enqueue/cancel/result/expiry queue, the
  `heartbeats` ingest path, the `unregistered` tracker, the `events`
  ingest/query service, the `invitations` mint/redeem service, the
  `password_resets` service, the `inbox` health/attention feed and the
  `external_sensors` registry + sample reads + poll-due check) take
  the `hub_db` isolated-SQLite fixture.
  Every test under `tests/unit/` is auto-tagged `ci` by its conftest —
  no HTTP, no Docker, runs in ~13 s.

Two structural fixes made the gate-2 widening possible (both default to
the production-safe value; the CI app boot opts out):
`REBOOTER_SESSION_COOKIE_SECURE=0` (the Secure session cookie is never
sent over the gate's `http://localhost`) and
`REBOOTER_RATE_LIMIT_EXEMPT_IPS='*'` (33 test modules each log in — that
trips the 30/min auth limiter).

### Running it locally

Mirror the CI job — Postgres + app + nginx, the app fronted under
`/rebooter`. `ci/nginx.conf` pins the upstream to `rd-ci-app`, so use
that container name. (CI publishes nginx on host :8080; pick any free
host port locally and match it in the two URLs below.)

```bash
NGX_PORT=18080   # any free host port

# 1. boot the stack
docker network create rd-ci
docker run -d --name rd-ci-pg --network rd-ci \
  -e POSTGRES_USER=rebooter -e POSTGRES_PASSWORD=cipw -e POSTGRES_DB=rebooter postgres:16
docker run -d --name rd-ci-app --network rd-ci -p 8090:8090 \
  -v rd-ci-firmware:/data/firmware \
  -e REBOOTER_DATABASE_URL=postgresql+psycopg://rebooter:cipw@rd-ci-pg:5432/rebooter \
  -e REBOOTER_SECRET_KEY=ci-not-a-secret \
  -e REBOOTER_BOOTSTRAP_ADMIN_EMAIL=ci-admin@example.com \
  -e REBOOTER_BOOTSTRAP_ADMIN_PASSWORD=ci-Adm1n-pw \
  -e REBOOTER_SESSION_COOKIE_SECURE=0 \
  -e REBOOTER_RATE_LIMIT_EXEMPT_IPS='*' \
  -e REBOOTER_FIRMWARE_PUBLIC_BASE=http://localhost:${NGX_PORT}/rebooter/firmware \
  dblagbro/rebooter-droids:latest
# nginx must start after the app (its upstream name resolves at boot)
sleep 8
docker run -d --name rd-ci-nginx --network rd-ci -p ${NGX_PORT}:80 \
  -v rd-ci-firmware:/data/firmware:ro \
  -v "$PWD/ci/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
  nginx:alpine

# 2. run the gate against nginx
REBOOTER_QA_BASE=http://localhost:${NGX_PORT}/rebooter \
REBOOTER_QA_EMAIL=ci-admin@example.com \
REBOOTER_QA_PASS=ci-Adm1n-pw \
  pytest -m ci -v

# 3. clean up — only the rd-ci-* containers + volume
docker rm -f rd-ci-app rd-ci-pg rd-ci-nginx
docker volume rm rd-ci-firmware && docker network rm rd-ci
```

## Known non-gated test failures — RESOLVED in v0.5.98

The 2026-05-17 regression sweep had classified 5 failures against a
fresh nginx-fronted Postgres replica; **all five were cleared in
v0.5.98 and v0.5.99** (the latter via the watchdog tick + state-machine
slice). For historical reference:

| Test | Class | Resolution |
|---|---|---|
| `test_hardening_probes::test_session_cookie_attributes` | test-quality | v0.5.98 — replaced hardcoded creds with `admin_creds`; the Secure-cookie assertion now skips cleanly when `REBOOTER_SESSION_COOKIE_SECURE=0` (HTTP localhost) but still asserts `HttpOnly` + `SameSite` every gate. |
| `test_hardening_probes::test_logout_does_not_revoke_cookie_server_side` | test-quality | v0.5.98 — same `admin_creds` swap. |
| `test_responsive::test_mobile_topbar_nav_links_reachable` | stale test | Pre-cleared (assertion was already updated to 6). v0.5.98 — file gated; the `chromium_browser` fixture skips browser tests cleanly when playwright / chromium isn't available. |
| `test_v033_cookie_domain::test_theme_cookie_legacy_name_still_read` | test-environment | v0.5.98 — `cookielib`'s `domain=netloc` machinery doesn't reach a bare-`localhost` host or include a port; send the cookie explicitly via the GET request's `cookies=` instead. |
| `test_v042_watchdog_runtime::test_probe_now_http_success` | environmental | v0.5.98 — the probe URL was the test-client view of `base_url`; the probe runs *inside* the app container. Switched to `http://localhost:8090/api/v1/version` — the app's own in-container listener, reachable from any deployment. |

Only `test_v0520_long_poll_commands` is now intentionally out of
gate (its long-poll holds tie up the runner 4–6 s per test by design).

## Known coverage gaps (the honest list)

1. **Gate-3 backlog — CLEARED in v0.5.98.** The four `0P`-all-fail
   files plus the two playwright-only files now all gate cleanly. The
   per-file fixes that landed:
   - `test_hardening_probes` — replaced the hardcoded production
     creds with the `admin_creds` fixture; `test_session_cookie_attributes`
     now asserts `HttpOnly` + `SameSite` unconditionally and skips the
     Secure check when the instance has `REBOOTER_SESSION_COOKIE_SECURE=0`
     (HTTP localhost); `test_login_rate_limit_kicks_in` already skipped
     cleanly when `REBOOTER_RATE_LIMIT_EXEMPT_IPS` covers the client.
   - `test_v033_cookie_domain::test_theme_cookie_legacy_name_still_read`
     — `cookielib`'s `domain=netloc` machinery doesn't ship a cookie
     back to `localhost` or with a port; sends the cookie via the
     GET request's `cookies=` instead.
   - `test_v042_watchdog_runtime::test_probe_now_http_success` — the
     probe URL was the *test client's* view of `base_url`; the probe
     runs inside the app container. Probes
     `http://localhost:8090/api/v1/version` — the app's own
     in-container listener, reachable from any deployment.
   - `test_v034_bulk_actions` — added a module-scoped autouse
     `_seed_bulk_rows` fixture (device + group + invitation + token)
     so every parametrised list page has a row to render the
     bulk-form scaffolding for.
   - `test_responsive` + `test_ui_flows` — browser-driven; the
     `chromium_browser` fixture skips uniformly when playwright /
     chromium isn't available. Gated as such.
   Only `test_v0520_long_poll_commands` stays out, by design (the
   tests deliberately hold requests 4–6 s each).
2. **In-process unit coverage — closed the named gaps in
   v0.5.99 + v0.5.100.** `tests/unit/` now covers the `_rules_forms`
   builders, schedule recurrence math, `create_rule` validation, the
   canonical probe-kind registry, the `upsert_announcement` state
   machine, the `enrollment`, `device_power`, `commands`,
   `heartbeats`, `unregistered`, `events`, `invitations`,
   `password_resets`, `inbox`, `external_sensors`, and **scenes**
   services, the **watchdog `run_probe` dispatcher**, the
   **watchdog state machine** (`_update_state_and_maybe_fire` +
   `_rule_is_due` + `_in_maintenance_window`), the **watchdog `tick()`
   orchestrator** (env-disabled, portal maintenance, due-ness, rule
   maintenance window, probe-error recovery, threshold-cross fire),
   and the **firmware-deployment service** end-to-end
   (`create_deployment` + `assignment_for_device` +
   `mark_assignment_delivered` + `reconcile_assignment_reported_version`
   + `list_deployments`). **BUG-059 is fixed** (v0.5.88) — the
   `as_aware` / `with_variant` / dialect-branch landmines that crashed
   the SQLite test backend are cleared. The originally-named gaps
   (watchdog tick + state loop; `deployments`) are now covered.
3. **End-to-end adoption test — DONE.** The
   announce → pending-adoption → adopt → token-mint → `/register` →
   first-heartbeat → "online" flow (~60 KB across `announcements.py`
   / `pending_adoption.py` / `enrollment.py` / `device_api.py`) is now
   driven as one flow by `tests/qa/test_v0589_adoption_e2e.py`, gated
   into `-m ci`. It also covers the seam failures — the spent
   enrollment token is rejected on replay, and the announcement
   closes out as `registered`. This was the charter's (P-REG)
   highest-value missing test; the v0.5.36→v0.5.68 siteless-token
   regression is the class of bug it guards.
4. **Playwright tests gate uniformly now.** Both `test_responsive.py`
   and `test_ui_flows.py` carry `pytestmark = pytest.mark.ci` as of
   v0.5.98; the `chromium_browser` fixture skips them cleanly when
   playwright or the chromium binary isn't available. Local CI
   replicas with chromium installed actually run the browser flows
   (~60 tests in `test_ui_flows.py`); GitHub Actions without a
   browser image skips them cleanly. The previously-stale
   `test_mobile_topbar_nav_links_reachable` (5-vs-6 nav links) was
   pre-cleared.
5. **`qa-notes.md` archived in v0.5.101** to
   `docs/sessions/qa-notes-archived-20260519.md`. ~105 K-token soak-
   test diary; preserved for history (referenced from BUG entries,
   the 2026-05-15 charter, and the firmware refactor log) but no
   longer in the top-level docs/.
6. **bug-log.md drifts** — was the BUG-061 finding; fixed in the same
   sweep. Stale-`open` tags should be flagged on every release-notes
   review.

## How to widen the gate further

The gate-3 path is now drained — the named-file backlog cleared in
v0.5.98 and the two `tests/unit/` slices it pointed at landed in
v0.5.99 and v0.5.100. Forward gate work is therefore *additive*
rather than backlog-clearing:

1. **Grow `tests/unit/`.** The fastest, highest-leverage CI work —
   no HTTP, no Docker, auto-gated. The originally-named gaps
   (watchdog tick + state loop; `deployments`) are now covered;
   the next candidates are whatever new service shows up in a
   diff. Adoption/enrollment edge cases, the `schedules` runtime
   tick (companion to the watchdog one) and the `inbox` attention
   pipeline are the obvious next targets.
2. **Optional: add `test_v0520_long_poll_commands` behind a
   `slow` mark.** Today it's excluded by design (4–6 s holds per
   test = ~15 s added to the gate). A dedicated `slow` job that
   runs on PR merge but not every push would let it gate without
   bloating the fast path.
3. **Optional: a browser-installed CI lane.** The playwright
   files already gate cleanly via skip; a parallel `ci-browser`
   job that installs chromium would actually exercise them.

## 2026-06-13 update — coverage gaps surfaced by deep-QA workflow

The multi-agent QA pass found 4 HIGH bugs in power-topology code
(0.6.38/0.6.39) that should have been caught at PR time. The mutation
layer (`app/services/devices/_mutations.py`) had zero unit tests for
power_source_device_id behaviour. The new
`tests/unit/test_device_topology_guards.py` closes the gap with 6
tests (self-parent, simple cycle, 3-hop cycle, missing parent, clear,
valid chain). All run in `-m unit` against the in-process `hub_db`
fixture — no nginx + Postgres needed.

### Coverage gaps remaining (open work)

| Surface | What's missing | BUG ref |
|---|---|---|
| Device delete cascade | No test that `device.deleted` audit entry records orphaned children. FK is `ON DELETE SET NULL` but the audit row doesn't capture which children lost their power source. | BUG-067 |
| Audit-row content | Tests assert audit rows are WRITTEN but not their content. Topology old/new not visible to forensics. | BUG-066 |
| Picker site-scoping | No test that picker excludes devices in sites outside current operator's scope. | BUG-068 |
| Confirm dialog truncation | No test with N=30 children driving the relay-toggle confirm message. Browser limit could chop the warning. | BUG-069 |
| Display-name validation | No test for newlines/control chars in display_name. | BUG-070 (handler-side fix shipped 0.6.40; service-side validation still open) |
| Detail page N-children | No test for rendering 30 child devices in the "Powers:" line on detail. | BUG-071 |

### `responsive` marker bucket — outside the CI gate

`pytest -m ci` skips the `responsive` bucket. BUG-065 (nav-link count
regression) slept for ~3 days in the `responsive` test that lives
there because nothing ran it. Options:

  1. Promote `responsive` tests into `-m ci` — they're Playwright-
     driven and add ~30-60s of wall-clock to the gate. Acceptable.
  2. Add a separate GitHub Actions job for `-m responsive` that runs
     on the same image.

Recommend option 1 for the next PR-time fix.

### -x (halt-on-first-failure) hides downstream issues

`tests/qa/test_responsive.py::test_mobile_topbar_nav_links_reachable`
has two assertions (line 104 desktop, line 108 mobile). The `-x` flag
halts on line 104 and never runs 108. If the mobile nav has ALSO
regressed, we won't know until line 104 is fixed.

Recommend splitting that test into two — `test_topnav_link_count`
and `test_bottomnav_link_count` — so one failure doesn't mask the
other.
