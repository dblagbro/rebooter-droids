# QA Notes

Operational notes, environment quirks, and known limitations encountered
while testing.

## Test environment

- Tests run against the live deployment at
  `https://www.voipguru.org/rebooter`. There is no separate staging
  environment as of v0.1.2.
- Bootstrap admin: `dblagbro@gmail.com` / value of
  `REBOOTER_BOOTSTRAP_ADMIN_PASSWORD` in `/home/dblagbro/docker/.env`.
- Test data is created with the prefix `qa-` (groups, sites) or
  `QA <thing>` (display names) so cleanup queries can find it.

## Known quirks

- nginx is bind-mounted on a single config file. Editor atomic-rename
  changes the inode and `nginx -s reload` won't see the new content
  until the container is restarted. Use `sudo docker restart nginx`
  after editing the conf.
- Postgres data directory uses a sub-folder (`PGDATA=…/cluster`) because
  the parent bind mount is non-empty (gitkeep). This is intentional.
- The APScheduler instance is single-worker, gated by Postgres advisory
  lock 4242117310. Only one Gunicorn worker runs `expire_commands`.

## Test-data cleanup

Currently manual. After a regression run, devices/groups/sites tagged
with `qa-` should be removed via:

```sql
DELETE FROM commands WHERE issued_by_user_id IS NOT NULL
  AND device_id IN (SELECT id FROM devices WHERE display_name LIKE 'QA %');
DELETE FROM device_events WHERE device_id IN (SELECT id FROM devices WHERE display_name LIKE 'QA %');
DELETE FROM deployment_assignments WHERE device_id IN (SELECT id FROM devices WHERE display_name LIKE 'QA %');
DELETE FROM device_credentials WHERE device_id IN (SELECT id FROM devices WHERE display_name LIKE 'QA %');
DELETE FROM enrollment_tokens WHERE consumed_by_device_id IN (SELECT id FROM devices WHERE display_name LIKE 'QA %');
DELETE FROM devices WHERE display_name LIKE 'QA %';
DELETE FROM groups WHERE name LIKE 'qa-%';
DELETE FROM sites WHERE name LIKE 'qa-%';
DELETE FROM firmware_releases WHERE filename LIKE 'rebooter-qa-%';
```

The suite attempts cleanup on success — manual fallback above.

## Run history

### 2026-05-08 — first deep-regression pass (v0.1.2 → v0.1.3)

- 77 tests written; 62 passed first run, 3 hardening probes failed.
- Three real bugs found and shipped in v0.1.3:
  - BUG-001: enrollment-token race (high)
  - BUG-002: firmware concurrent-upload 500 (high)
  - BUG-003: trailing-slash 404 (medium)
- Six hardening findings logged (BUG-005..011); see `bug-log.md` and
  `remediation-plan.md`.
- One operator-reported issue (group-create logout, BUG-004) could not
  be reproduced in clean Playwright session; left in `monitoring`.

### How to re-run

```bash
cd /mnt/s/code/rebooter-droids
python3 -m pytest tests/qa -v --timeout=120
# or just the hardening probes:
python3 -m pytest tests/qa/test_hardening_probes.py -v --timeout=120
```

The suite hits the live deployment by default. Override with
`REBOOTER_QA_BASE=https://.../rebooter` for staging.

> **--timeout=120 is required.** Default is 60 s and BUG-025 (rate
> limit test) needs ~62 s wall-clock.

---

### 2026-05-09 PM — deep regression after v0.4.0 → v0.4.1 → v0.4.2 ships

Triggered by operator question "why no devices show online?" plus a
demand for senior-SDET-style deep validation. Conducted in 5 phases.

#### Phase 0 — ground-truth on the operator's complaint

- DB query: `SELECT count(*) FROM devices` → **1**
- That one row has `is_qa_fixture = true` and last beat 75 minutes ago.
- **There are zero real devices in the system.** The "no devices
  show online" observation is operationally correct.
- Documented in BUG-029 (environmental, no code fix).

#### Phase 1 — read existing docs

- bug-log.md (200 lines, 20 prior bugs)
- test-plan.md (78 lines, surface inventory)
- qa-notes.md (71 lines)
- rca-2026-05-09-no-device-online.md (235 lines — definitive answer
  to operator's question; aligned with BUG-029)

#### Phase 2 — full pytest baseline

- `python3 -m pytest tests/qa/ --tb=line --timeout=120` →
  **34 failed, 207 passed, 1 skipped** in 192 s.
- Per-file isolation re-run: only **7 distinct real failures**.
- Delta (27 cascading failures) traced to **BUG-021** —
  session-scoped admin token poisoned by tests that call
  `POST /api/v1/auth/logout`.

#### Phase 3 — device-claim/heartbeat/failsafe API

- `tests/qa/test_device_api.py` — 11/11 pass.
- Direct curl claim → register → heartbeat → cleanup round-trip
  green. Server-side device intake is verified-healthy.

#### Phase 4 — Playwright UI walkthrough

- Headless Chromium, 1280×800. Logged in as bootstrap super-admin.
- Walked Status / Devices / Rules / History / Settings; every
  link landed cleanly.
- Walked all 6 settings sub-tabs (System / Network / Authentication
  / Sync / Notifications / Theme); all titles correct except
  Authentication (BUG-028, low).
- **No console errors. No request failures.**
- **Sign out is not in the persistent header — only in Profile**
  (BUG-022, high).
- **No super-admin role badge in the header** (BUG-023, medium).

#### Phase 5 — direct LAN probe of the four lab devices

- This central host: `192.168.18.0/24` (gw 192.168.18.1).
- Lab devices: `192.168.1.0/24`. Different subnet.
- Live probe results (2026-05-09 22:55 UTC):
  - .67 → TCP-80 timeout
  - .225 → TCP-80 timeout
  - .30 → TCP-80 timeout
  - .207 → TCP-80 connection refused (host up, no http)
- Documented in BUG-027 (environmental). Earlier-today
  firmware-team report ("all 4 HTTP OK") was per their probe
  inside the lab subnet.

### Rate-limiter state — 2026-05-09 evening verify

Independent verify of the limiter (NOT via pytest):
- 5 logins in <1 s → 401 401 401 429 429 (kicks in at attempt 4).
- 35 logins after a 65 s window reset → 30×401 + 5×429 (matches
  the configured `30 per minute`).
- **Rate limiter works as documented.** BUG-025 is purely a
  test-timeout-config mismatch.

### Test-isolation discipline (BUG-021 root cause)

Tests that mutate shared user state — anything that calls
`/api/v1/auth/logout`, `/api/v1/admin/users/<id>/revoke-all`, or
the password-reset consume path — MUST use a dedicated test user,
not the bootstrap admin. The session-scoped `admin_token` fixture
is shared across every test file.

When adding a new test that mutates user state, either:
1. Mint a fresh test user via the admin API at the top of the
   test, scope teardown to delete that user.
2. Or use a pre-provisioned `qa-test-admin@…` user that's NOT the
   one the rest of the suite logs in as.

### Operator-facing diagnostic blind spot (BUG-027)

The central server lives on a different subnet than the lab
devices. Any "is device X reachable?" feature added to the UI
will produce constant false negatives. Recommended pattern: the
device sends a "I tried to reach you from <my LAN IP>" beacon
which central records, rather than central probing devices.
