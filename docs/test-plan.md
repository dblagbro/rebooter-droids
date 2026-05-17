# Test plan

Status: **2026-05-17** — CI gate at ~535 tests / 67 files, run behind
nginx (P-QA gate-2 widening + gate-3 fixes + a growing `tests/unit/`
tree; charter `docs/notes/2026-05-15-pause-state-and-resume-charter.md`).

This document is the canonical description of how rebooter-droids is
tested. It replaces the prior per-sweep verdict-logging version of this
file, and supersedes `qa-notes.md` — which has grown to ~105 K tokens
of accreted session notes and is no longer a usable plan (treat it as
history only).

## How the suite is shaped today

- **443 collected test items**, all under `tests/qa/`, in ~59 files
  named per release (`test_v0535_*`, `test_v0568_*`, …).
- They are **HTTP integration tests** driven with `requests` against a
  *running* server — `conftest.py`'s `base_url` fixture defaults to
  the live deployment (`https://www.voipguru.org/rebooter`) and
  `admin_token` logs in with a real admin. There are **no in-process
  unit tests** and no Flask-test-client coverage.
- A subset are Playwright browser tests (the `responsive` marker).

This shape is why "poor QA" was a charter problem: per-release
snapshot tests against production cannot gate a change, and until
2026-05-15 **nothing ran them automatically** on push or PR.

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
instance**. As of the gate-2 widening (v0.5.79), the gate-3 fixes, the
`tests/unit/` tree and the nginx front (v0.5.84), that is **~433 tests
across 62 files** — every test file confirmed to pass `pytest -m ci`
twice against a from-scratch instance (fresh Postgres + populated DB):

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
  `commands._validate_payload` schema checks) need no fixture;
  DB-backed service tests (`create_rule` validation, the
  `upsert_announcement` state machine, the `enrollment`
  mint/consume/revoke service, the `device_power` query/rollup
  surface, the `commands` enqueue/cancel/result/expiry queue, the
  `heartbeats` ingest path) take the `hub_db` isolated-SQLite fixture.
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

## Known non-gated test failures (2026-05-17 regression sweep)

The full `tests/` suite (598 collected) was run against a fresh
nginx-fronted Postgres replica: **593 passed, 5 failed, 5 skipped**.
All 5 failures were classified — **none is a product regression**:

| Test | Class | Why it fails outside production |
|---|---|---|
| `test_hardening_probes::test_session_cookie_attributes` | test-quality | Hardcodes prod creds `dblagbro`/`Super*120120`; login fails on any other instance. Does **not** honour `REBOOTER_QA_EMAIL/PASS`. |
| `test_hardening_probes::test_logout_does_not_revoke_cookie_server_side` | test-quality | Same hardcoded-creds issue. |
| `test_responsive::test_mobile_topbar_nav_links_reachable` | stale test | Asserts 5 top-nav / 5 bottom-nav links; the nav legitimately has **6** (`layout.html` — Status/Devices/Rules/History/**Power**/Settings — the Power link shipped with B16). Update the assertion to 6. |
| `test_v033_cookie_domain::test_theme_cookie_legacy_name_still_read` | test-environment | Sets a cookie with `domain=urlsplit(base_url).netloc`; against a `host:port` base URL the port is included in the domain → the cookie is never sent. The code (`settings.py:437`) **does** still read the legacy `theme` cookie — verified. Passes against a port-less prod URL. |
| `test_v042_watchdog_runtime::test_probe_now_http_success` | environmental | Documented below — probe target unreachable from inside the container. |

**Correction to the prior accounting:** the gate-3 note below previously
stated `test_hardening_probes` is ungated only because of the
`RATE_LIMIT_EXEMPT_IPS=*` / `SESSION_COOKIE_SECURE=0` gate config. That
is incomplete — the *primary* blocker is hardcoded production
credentials. Any fix must (a) honour `REBOOTER_QA_EMAIL/PASS` and only
then (b) add the per-test skips for the config-disabled assertions.

## Known coverage gaps (the honest list)

1. **~4 of the ~64 test files are still not in the CI gate** (gate-3
   backlog). The `0P`-all-fail, partial-fail, in-process, timing-e2e
   and nginx-layer buckets are now fixed and gated; what's left needs
   more than an assertion tweak. The fix checklist that cleared the
   gated files: (a) the per-file `_login()` must honour
   `REBOOTER_QA_EMAIL/PASS`, not hardcoded creds; (b) seed any data the
   test assumes with a module-scoped autouse fixture; (c) widen HTML
   regexes — the responsive reflow added `data-label` attributes, so
   `<td><code>` must become `<td[^>]*><code>`; (d) in-process tests use
   an isolated SQLite DB + `init_engine` + a bare Flask app context
   (see `test_v0514` / `test_v0536`); (e) the watchdog + schedule
   runtimes take an injectable `now` so timing e2e is deterministic
   in-process (see `test_v0414` / `test_v0417`); (f) the gate runs
   behind nginx (`ci/nginx.conf`), so the prefix-proxy and firmware
   static-serving tests run for real. The remainder:
   - *Hardcoded creds + CI-environment-incompatible* —
     `test_hardening_probes` hardcodes production credentials (so it
     fails on every non-prod instance — fix: honour
     `REBOOTER_QA_EMAIL/PASS`), AND has a rate-limit test (the gate
     sets `RATE_LIMIT_EXEMPT_IPS=*`) and a cookie-`Secure` test (the
     gate sets `SESSION_COOKIE_SECURE=0`) that assert behaviour the
     gate's own config disables (fix: per-test skips keyed on those
     settings). Both must be fixed before it can gate.
   - *Base-URL assumes no port* — `test_v033_cookie_domain
     ::test_theme_cookie_legacy_name_still_read` sets a cookie scoped
     to `urlsplit(base_url).netloc`, which includes `:port` for a
     `host:port` base URL → cookie never sent. Fix: strip the port
     (`.split(":")[0]`) when deriving the cookie domain.
   - *Server-side probe target* — `test_v042_watchdog_runtime`'s one
     failure (`test_probe_now_http_success`) probes `base_url` from
     inside the app container, which can't reach the host-mapped port.
     Needs a probe target reachable from the app, or an in-process
     rewrite of the probe-now path.
   - *Order-dependent* — `test_v034_bulk_actions` asserts `/app/groups`
     shows bulk-form scaffolding, which only renders with a group
     present; gate it once it seeds its own group.
2. **In-process unit coverage is young.** `tests/unit/` exists and
   covers the `_rules_forms` builders, schedule recurrence math,
   `create_rule` validation, the `upsert_announcement` state machine,
   the `enrollment` mint/consume/revoke service, the `device_power`
   query/rollup surface, the watchdog `run_probe` dispatcher, the
   `commands` queue and the `heartbeats` ingest path — but other
   service-layer logic (`events` ingest, the watchdog
   tick/state-transition loop, `deployments`, …) still has only HTTP
   coverage. Growing `tests/unit/` is ongoing. **Blocker:** the
   `invitations`, `password_resets`, `inbox`, `external_sensors`,
   `events` and `unregistered` services cannot get `hub_db`
   (SQLite) coverage until **BUG-059** is fixed — they carry
   naive/aware datetime comparisons, non-variant `BigInteger` PKs,
   and an unconditional Postgres `ON CONFLICT` that crash on the
   SQLite test backend.
3. **No single end-to-end adoption test.** The
   announce → pending-adoption → adopt → token-mint → `/register` →
   first-heartbeat → "online" flow spans ~60 KB across
   `announcements.py` / `pending_adoption.py` / `enrollment.py` /
   `device_api.py` and has **no test driving it as one flow**
   (`architecture.md` / charter P-REG). This is the highest-value
   missing test — the v0.5.36→v0.5.68 regression (siteless-token
   adoption 500'd for 32 versions) is exactly what such a test
   would have caught.
4. **Playwright `responsive` tests are not in CI** — they need a
   browser image (the gate's `pip install -e ".[dev]"` has no
   playwright, so they skip cleanly); deferred. One is also stale
   (`test_mobile_topbar_nav_links_reachable`, asserts 5 nav links;
   there are 6).
5. **`qa-notes.md` is unmaintained** (~105 K tokens). Not deleted yet
   (it has historical value) but it is not a plan.
6. **bug-log.md drifts** — fixed bugs left tagged `open` (BUG-054,
   BUG-055; previously BUG-052). See BUG-061.

## How to widen the gate further (gate-3)

The path, in priority order:

1. **Fix the brittle files.** For each `0P` file: run it against a
   fresh ephemeral instance, then either seed the data it assumes or
   replace the stale HTML-string assertion, and add `pytestmark =
   pytest.mark.ci`. The gate-2 widening already triaged every file;
   this is the per-file repair work.
2. **Grow `tests/unit/`.** The tree is started (v0.5.82) — keep adding
   service-layer coverage there: it's fast, no HTTP, no Docker, and
   auto-gated. Adoption/enrollment edge cases, the watchdog probe
   dispatch and the announce state machine all belong here.
3. **Add the Playwright `responsive` tests to CI** behind a browser
   step once the above are stable.

Each step is independently shippable; do them in order.
