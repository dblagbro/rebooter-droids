# Test plan

Status: **2026-05-15** — first CI gate live (P-QA charter,
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
instance** — currently the registration / device-facing surface:

| File | Covers |
|---|---|
| `test_v0568_adoption_token_redelivery.py` | End-to-end adoption: announce → adopt → register → heartbeat, plus the lost-response and stranded-pickup recovery paths. |
| `test_device_api.py` | Full device-API round-trip + negative paths (register/heartbeat/commands/events/power-samples auth + validation). |
| `test_v027_heartbeat_state.py` | `online` / `offline` / `never` heartbeat-state derivation. |

**21 tests.** This is deliberately the registration-critical surface:
it is the exact class of bug that shipped in v0.5.36 (the `site_id`
NOT NULL regression that 500'd `/register` for ~32 versions, uncaught)
and was found in v0.5.68 only by *writing* the end-to-end adoption
test. That regression can no longer merge silently.

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

1. **~56 of the ~59 test files are not in the CI gate.** They assume
   the live deployment's accumulated data or pre-date current markup,
   so they fail against a fresh instance — not because the app is
   broken, but because the tests are brittle (HTML-string assertions,
   data-state assumptions).
2. **No in-process unit tests.** Service-layer logic
   (`announcements`, `enrollment`, `watchdog`, `device_power`, …) has
   no fast unit coverage — every test pays a full HTTP round-trip.
3. **Playwright `responsive` tests are not in CI** — they need a
   browser image; deferred until the gate is otherwise stable.
4. **`qa-notes.md` is unmaintained** (~105 K tokens). Not deleted yet
   (it has historical value) but it is not a plan.

## How to widen the gate

The path, in priority order:

1. **Triage the existing files.** For each non-`ci` file: run it
   against a fresh ephemeral instance. If it passes, add
   `pytestmark = pytest.mark.ci`. If it fails only on a stale
   HTML-string assertion, fix the assertion or drop the test. If it
   genuinely needs live-deployment data, leave it unmarked and note
   why.
2. **Add a `tests/unit/` tree** of in-process tests (Flask test client
   + a transactional test DB) for the service layer — fast, no HTTP,
   no Docker. This is where adoption/enrollment edge cases belong.
3. **Add the Playwright `responsive` tests to CI** behind a browser
   step once (1) and (2) are stable.

Each step is independently shippable; do them in order.
