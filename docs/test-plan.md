# Test plan

Status: **2026-05-17** — CI gate at ~256 tests / 44 files (P-QA gate-2
widening + gate-3 history & brittle-file fixes; charter
`docs/notes/2026-05-15-pause-state-and-resume-charter.md`).

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
| nginx routing / prefix-strip | curl with explicit hosts |

## The CI gate (GitHub Actions — `.github/workflows/ci.yml`)

On every push to `main` and every pull request, CI:

1. **builds the application image** (`docker build`) — catches import,
   syntax and dependency breakage across the whole app;
2. **boots the image against a throwaway Postgres** — proves the app
   starts and self-bootstraps its schema + admin from an empty DB;
3. **runs the `-m ci` test bucket** against that fresh instance.

### What `-m ci` covers

The `ci` marker tags tests **verified green against a fresh ephemeral
instance**. As of the gate-2 widening (v0.5.79) plus the gate-3 history
and brittle-file fixes, that is **~256 tests across 44 files** — every
test file confirmed to pass `pytest -m ci` twice against a from-scratch
instance (fresh Postgres + populated DB):

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
- **Settings / wizard / RBAC surfaces** (gate-3 brittle bucket) —
  runtime SMTP (`test_v0425`), the per-device upgrade button
  (`test_v0428`), the enrol wizard (`test_v0431`), the firmware settings
  tab (`test_v0433`), role-binding back-compat (`test_v0500`),
  pending-adoption count (`test_v0502`), the devices-list nested-form
  fix (`test_v0503` — seeds its own device), and the scanned-release
  URL guard (`test_v0511` — skips when no firmware artifacts exist).

Two structural fixes made the gate-2 widening possible (both default to
the production-safe value; the CI app boot opts out):
`REBOOTER_SESSION_COOKIE_SECURE=0` (the Secure session cookie is never
sent over the gate's `http://localhost`) and
`REBOOTER_RATE_LIMIT_EXEMPT_IPS='*'` (33 test modules each log in — that
trips the 30/min auth limiter).

### Running it locally

```bash
# 1. boot a throwaway instance
docker network create rd-ci
docker run -d --name rd-ci-pg --network rd-ci \
  -e POSTGRES_USER=rebooter -e POSTGRES_PASSWORD=cipw -e POSTGRES_DB=rebooter postgres:16
docker run -d --name rd-ci-app --network rd-ci -p 18090:8090 \
  -e REBOOTER_DATABASE_URL=postgresql+psycopg://rebooter:cipw@rd-ci-pg:5432/rebooter \
  -e REBOOTER_SECRET_KEY=ci-not-a-secret \
  -e REBOOTER_BOOTSTRAP_ADMIN_EMAIL=ci-admin@example.com \
  -e REBOOTER_BOOTSTRAP_ADMIN_PASSWORD=ci-Adm1n-pw \
  -e REBOOTER_SESSION_COOKIE_SECURE=0 \
  -e REBOOTER_RATE_LIMIT_EXEMPT_IPS='*' \
  dblagbro/rebooter-droids:latest

# 2. run the gate
REBOOTER_QA_BASE=http://localhost:18090 \
REBOOTER_QA_EMAIL=ci-admin@example.com \
REBOOTER_QA_PASS=ci-Adm1n-pw \
  pytest -m ci -v

# 3. clean up — only the rd-ci-* containers
docker rm -f rd-ci-app rd-ci-pg && docker network rm rd-ci
```

## Known coverage gaps (the honest list)

1. **~19 of the ~64 test files are still not in the CI gate** (gate-3
   backlog). They fall into three buckets, each needing real work
   before they can gate:
   - *Brittle — one failing test in an otherwise-green file* — the
     partial-fail files (`test_admin_api`, `test_auth_negative`,
     `test_hardening_probes`, `test_routing_and_nginx`, `test_smoke`,
     `test_v02_rbac_invites`, `test_v030_redesign_p1_shell`,
     `test_v039_firmware_mirrors`, `test_v0411_input_validation`,
     `test_v0420_announce_adopt`, `test_v047_…`). Each is one stale
     assertion away from gateable. The whole `0P`-all-fail bucket —
     the history files (`test_v0427/0430/0432`) and the settings/RBAC
     files (`test_v0425/0428/0431/0433/0500/0502/0503/0511`) — is now
     fixed and gated. The fix checklist that cleared them: (a) the
     per-file `_login()` must honour `REBOOTER_QA_EMAIL/PASS`, not
     hardcoded creds; (b) seed any data the test assumes with a
     module-scoped autouse fixture (a fresh instance has none);
     (c) widen HTML regexes — the responsive reflow added `data-label`
     attributes, so `<td><code>` must become `<td[^>]*><code>`.
   - *In-process collection errors* — `test_v0514_deployment_…` and
     `test_v0536_site_not_null_…` import `app.*` and need a DB engine
     fixture; they error at collection on a bare runner.
   - *Timing / wall-clock e2e* — `test_v0417_schedule_runtime_e2e`,
     `test_v0414_watchdog_runtime_e2e`, `test_v042_watchdog_runtime`:
     race the 30 s APScheduler tick, so they flake. They do not belong
     in a deterministic gate without a time-injection seam.
   - *Order-dependent* — `test_v034_bulk_actions` asserts `/app/groups`
     shows bulk-form scaffolding, which only renders with a group
     present; gate it once it seeds its own group.
2. **No in-process unit tests.** Service-layer logic
   (`announcements`, `enrollment`, `watchdog`, `device_power`, …) has
   no fast unit coverage — every test pays a full HTTP round-trip.
3. **Playwright `responsive` tests are not in CI** — they need a
   browser image (the gate's `pip install -e ".[dev]"` has no
   playwright, so they skip cleanly); deferred.
4. **`qa-notes.md` is unmaintained** (~105 K tokens). Not deleted yet
   (it has historical value) but it is not a plan.

## How to widen the gate further (gate-3)

The path, in priority order:

1. **Fix the brittle files.** For each `0P` file: run it against a
   fresh ephemeral instance, then either seed the data it assumes or
   replace the stale HTML-string assertion, and add `pytestmark =
   pytest.mark.ci`. The gate-2 widening already triaged every file;
   this is the per-file repair work.
2. **Add a `tests/unit/` tree** of in-process tests (Flask test client
   + a transactional test DB) for the service layer — fast, no HTTP,
   no Docker. This is where adoption/enrollment edge cases belong.
3. **Add a time-injection seam** so the watchdog/schedule runtime e2e
   tests are deterministic, then gate them.
4. **Add the Playwright `responsive` tests to CI** behind a browser
   step once the above are stable.

Each step is independently shippable; do them in order.
