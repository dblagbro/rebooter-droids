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

### 2026-05-09 PM — verified clean run after v0.4.4/.5/.6

After the test-infra hardening + 2 regression fixes, the full QA
suite runs **240 passed / 2 skipped / 0 failed** in 125 s on the
live deployment.

Both skips are expected:
1. `test_login_rate_limit_kicks_in` — this host's IP is in
   `REBOOTER_RATE_LIMIT_EXEMPT_IPS`. Rate limiter still works for
   non-exempt clients; this test detects the absence of
   `X-RateLimit-Limit` headers and skips cleanly. To validate the
   limiter from a non-exempt source, run from a host outside
   192.168.18.0/24.
2. `test_device_detail_open_local_ui_link_when_ip_known` — needs
   a device with `local_ip` set in the fleet. Per BUG-029, the
   only device row right now is the QA fixture, and it doesn't
   have `local_ip` populated.

### Operator-facing diagnostic blind spot (BUG-027)

The central server lives on a different subnet than the lab
devices. Any "is device X reachable?" feature added to the UI
will produce constant false negatives. Recommended pattern: the
device sends a "I tried to reach you from <my LAN IP>" beacon
which central records, rather than central probing devices.

### 2026-05-14 EDT - live hub-vs-device recheck for "Rebooter - renamed test"

Targeted live sweep against the production hub plus the local device
HTTP surfaces for `192.168.1.48`, `.30`, `.225`, `.207`, and `.69`.
Used the same bootstrap admin credentials as the live QA suite.

Concrete improved findings:
- Hub API row for `.48` remains healthy on recheck:
  `display_name="Rebooter - renamed test"`,
  `heartbeat_state="online"`, firmware `0.1.17-dev-central`.
- `.48` local `/api/status` and `/api/config` now both return the
  renamed device name as well, so the earlier manual
  `apply_config.device_name` convergence still holds end-to-end.
- The earlier `.225` stale/offline mismatch did not reproduce on the
  recheck window around `2026-05-14T04:11Z`: hub showed `online`,
  local `/api/status` showed `central_state="idle"` with heartbeat age
  ~25 s.

Concrete remaining issue:
- Desired-name drift is still live on ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs device `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs device `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs device `Erica's ?.?. Speaker`

Reliability note:
- `.69` remains a clean true-offline reference case: hub `offline`,
  local `/`, `/api/status`, and `/api/config` all timed out from this
  host during the same sweep.
- `.48` had one slow first read on `/api/status` before settling into
  fast 200s on repeated requests. Not enough evidence yet to log a new
  bug, but worth watching if future soak passes show repeat stalls.

### 2026-05-14 EDT - renamed-test soak follow-up

Follow-up sweep run around `2026-05-14T04:20Z` through `04:24Z` against
the same production hub/device surfaces.

Concrete improved findings:
- `.48` still converges end-to-end on the renamed identity once the
  device responds: hub UI/API, local `/api/status`, local
  `/api/config`, and the device root UI all returned
  `Rebooter - renamed test`.
- The earlier `.225` stale/offline presentation mismatch still did not
  reproduce on this later window. Hub row stayed `online`; local
  `/api/status` stayed `central_state="idle"` with heartbeat age in
  single/double digits.

Concrete remaining issue:
- BUG-053 still reproduces exactly on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/UI `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/UI `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability note:
- `.48` was not clean on first contact in this pass. In a 5-probe loop,
  the first two cycles had local HTTP timeouts on one or more of
  `/api/status`, `/api/config`, or `/`, then the next three cycles
  recovered to stable `200` responses.
- Once `.48` recovered, both hub and device settled on firmware
  `0.1.16-dev-central` during the same window. The earlier one-off
  `0.1.17-dev-central` reading from the list endpoint did not hold on
  repeat fetches, so treat that as an unstable snapshot rather than a
  verified current firmware version.

### 2026-05-14 EDT - renamed-test soak second follow-up

Second follow-up sweep run around `2026-05-14T04:30Z` through `04:35Z`
using the live hub UI/API plus repeated local device API probes for
`.48`, `.207`, and `.225`.

Concrete improved findings:
- `.48` remains converged on the renamed identity across the authenticated
  hub UI, hub API, local `/api/status`, and local `/api/config`.
- The mixed `.48` firmware readings from the earlier follow-up resolved
  in this window: after the first stale `0.1.16-dev-central` hub/API
  snapshot, both hub and local `/api/status` converged on
  `0.1.17-dev-central` in the repeated loop.
- A one-off `.207` hub `offline` snapshot seen at `2026-05-14T04:30:43Z`
  did not hold. In the 5-cycle recheck loop from `04:33Z` to `04:35Z`,
  the hub row stayed `online` and local `/api/status` returned
  `central_state="idle"` with heartbeat age in the single/double digits
  on every successful read.

Concrete remaining issue:
- BUG-053 still reproduces on ordinary fleet devices when compared
  against the live hub UI/API and the local device API-backed UI path:
  - `.30`: hub `Erica's Subwoofer` vs local API `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `.48` reproduced the first-contact local HTTP failure again. In the
  5-cycle loop, cycle 1 timed out on local `/api/status`, while cycles
  2-5 recovered to clean `200` responses with `central_state="idle"`.
  That repeat is enough to promote the issue from "watch item" to a
  logged reliability bug (BUG-054).
- `.207` had one transient local `/api/status` connection reset during
  cycle 3, then recovered on the next pass. That is worth watching, but
  it did not hold long enough in this run to log as a separate bug.

### 2026-05-14 EDT - renamed-test soak stabilization recheck

Stabilization recheck run around `2026-05-14T04:40Z` through `04:42Z`
against the live hub devices page/API plus local device HTTP surfaces
for `.48`, `.30`, `.225`, `.207`, and `.69`.

Concrete improved findings:
- `.48` stayed fully converged on this pass: hub UI row, hub API,
  local `/api/status`, and local `/api/config` all reported
  `Rebooter - renamed test` on a clean 5-cycle loop.
- `BUG-054` did not reproduce in this short recheck window. All five
  `.48` loop cycles returned `200` from `/`, `/api/status`, and
  `/api/config` with firmware `0.1.17-dev-central`.
- `.30` and `.225` now match the hub on firmware version as well as
  reachability. Both hub UI/API and local `/api/status` report
  `0.1.17-dev-central`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability note:
- `.69` remains the true-offline control case: hub `offline`, while the
  local device UI/API still timed out from this host on `/`,
  `/api/status`, and `/api/config`.
- `.207` stayed locally reachable in a 5-cycle recheck after one
  unrelated truncated-read seen during ad-hoc HTML inspection, so do
  not log a new bug from that one-off read.

### 2026-05-14 EDT - renamed-test soak latency recheck

Latency-focused recheck run around `2026-05-14T04:52Z` using the live
hub devices page/API plus local device `/`, `/api/status`, and
`/api/config` probes for `.48`, `.30`, `.225`, `.207`, and `.69`.

Concrete improved findings:
- `.48` still matches end-to-end on identity and firmware:
  hub devices page/API, local `/api/status`, and local `/api/config`
  all reported `Rebooter - renamed test` on `0.1.17-dev-central`.
- `BUG-054` did not reproduce as a hard failure in this pass. A fresh
  5-cycle loop on `.48` returned `200` from `/`, `/api/status`, and
  `/api/config` every time.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability note:
- `.48` is improved but not truly clean yet. The same 5-cycle loop had
  one slow local root-page fetch (~1.1 s) and one much slower local
  `/api/status` response (~3.2 s) before returning to sub-second reads.
  Treat this as a narrowed form of `BUG-054` rather than a resolved
  condition.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck after the `16:32Z` sample: `.48` rebooted again and then truncated `/api/config`, while `.225` joined the fresh masked-reboot bucket

Fresh live recheck run around `2026-05-14T16:40:42Z` through
`16:42:45Z` against the authenticated hub devices page/API plus the
rendered `.48` detail page/API, local device `/`, `/api/status`, and
`/api/config` probes for `.48`, `.30`, `.225`, `.207`, and `.69`,
followed by an immediate 5-cycle local loop on `.48` and confirming
local `/api/status` rereads on `.48` and `.225`.

Concrete improved findings:
- The live hub Devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. For the renamed soak target,
  the rendered list page, rendered detail page, list API, and detail
  API all still showed `Rebooter - renamed test` at `192.168.1.48`,
  `online` / `heartbeat_state="online"` on `0.1.17-dev-central`; the
  detail API also surfaced the newest reboot heartbeat as
  `latest_heartbeat.last_event_type="boot"` with
  `received_at="2026-05-14T16:40:44Z"` and
  `uptime_seconds=244`.
- `.30` and `.207` showed no fresh reliability regression in this pass.
  `.30` stayed responsive on local `/`, `/api/status`, and
  `/api/config` (`0.276 s`, `0.016 s`, `0.062 s`) with
  `uptime_seconds=4882`, while `.207` stayed responsive as well
  (`0.184 s`, `0.023 s`, `0.071 s`) with
  `uptime_seconds=1619`. `.69` also stayed unchanged as the offline
  control: hub `offline`, while local `/`, `/api/status`, and
  `/api/config` all timed out from this host.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` strengthened again on the renamed soak target in two ways.
  Relative to the prior `2026-05-14T16:31:50Z` to `16:32:20Z` pass,
  which had already climbed to local `uptime_seconds=238`, the first
  fresh local `.48` `/api/status` read had already reset to
  `uptime_seconds=244` and the hub detail API agreed with a fresh boot
  heartbeat at `2026-05-14T16:40:44Z`, so the device rebooted again
  while the hub list row still looked like an ordinary `online` device.
  The immediate 5-cycle local continuity loop then climbed only from
  `uptime_seconds=326` to `331`; cycles 1-4 stayed clean, but cycle 5
  failed on local `/api/config` after `5.808 s` with a
  truncated-body `ChunkedEncodingError`
  (`IncompleteRead(949 bytes read, 119 more expected)`) while local `/`
  and `/api/status` still returned healthy `200` responses. A
  confirming reread a little later showed local `.48`
  `/api/status.uptime_seconds=384`, so count this as another concrete
  reboot/recovery sample plus a new local `/api/config`
  response-integrity repro, not just another latency watch.
- `.225` moved back into a fresh masked-reboot bucket in this run.
  The first local sweep still caught the device healthy and reachable at
  `uptime_seconds=1313`, but the same live hub/API pass briefly showed
  the row as `offline` with stale heartbeat `2026-05-14T16:37:32Z`
  before the next reread reconverged it to `online`. By the follow-up
  detail API fetch, the hub had already ingested a new boot heartbeat at
  `2026-05-14T16:42:37Z` with `latest_heartbeat.uptime_seconds=64`, and
  the confirming local `/api/status` reread showed
  `uptime_seconds=90`. Treat that as fresh reboot/recovery evidence on
  `.225` rather than just the standing desired-name drift.

### 2026-05-14 EDT - renamed-test recheck after the `16:01Z` masked reboot: `.48` reconverged cleanly across hub surfaces, with only root-page latency still visible

Fresh live recheck run around `2026-05-14T16:11:46Z` through
`16:12:45Z` against the authenticated hub devices page, hub detail
page, `/api/v1/admin/devices`, `/api/v1/admin/devices/<id>`, and the
renamed soak target's local `/`, `/api/status`, and `/api/config`,
followed by an immediate 5-cycle local continuity loop on `.48`.

Concrete improved findings:
- The rendered hub Devices page still matched `/api/v1/admin/devices`
  on the renamed soak target, and the rendered detail page still
  matched `/api/v1/admin/devices/<id>` as well. All four hub surfaces
  showed `Rebooter - renamed test` at `192.168.1.48`,
  `online` / `heartbeat_state="online"` on
  `0.1.17-dev-central`, with the list heartbeat at
  `2026-05-14T16:12:24Z` and the detail API already reconverged to
  `latest_heartbeat.health_state="healthy"` with
  `latest_heartbeat.uptime_seconds=724`.
- `.48` improved again relative to the prior `2026-05-14T16:01:20Z` to
  `16:03:02Z` masked-reboot sample. The immediate local 5-cycle loop
  returned clean `200` responses on every root/status/config probe,
  local `/api/status` and `/api/config` both kept matching
  `Rebooter - renamed test` / `0.1.17-dev-central`, and local
  `/api/status.uptime_seconds` climbed steadily from `688` to `697`
  with `health_state="healthy"` throughout. That is another concrete
  recovery window rather than a fresh reboot.

Reliability notes:
- `BUG-054` did not reproduce in masked-reboot or truncated-body form
  in this pass, but the narrower local-root latency watch is still
  concrete. Local `/api/status` stayed fast at about `0.021 s`-
  `0.027 s` and local `/api/config` stayed fast at about
  `0.063 s`-`0.081 s`, while local root-page reads stretched to
  `1.128 s` on cycle 4 and `3.547 s` on cycle 5. Keep BUG-054 open as
  a local UI responsiveness issue only in this pass, not as a fresh
  reboot or hub-vs-device identity drift.

### 2026-05-14 EDT - renamed-test recheck after the 15:51Z clean window: `.48` rebooted again, then reconverged quickly without fresh hub list drift

Fresh live recheck run around `2026-05-14T16:01:20Z` through
`16:03:02Z` against the authenticated hub Devices page, hub detail
page/API, `/api/v1/admin/devices`, `/api/v1/admin/devices/<id>`, and
the renamed soak target's local `/`, `/api/status`, and `/api/config`,
followed by an immediate 5-cycle local API continuity loop and two
short hub/detail follow-up rereads.

Concrete findings:
- The live hub Devices page still matched `/api/v1/admin/devices` on
  the renamed soak target. The rendered list page, list API, and detail
  page all kept showing `Rebooter - renamed test` at `192.168.1.48`,
  `online`, and `0.1.17-dev-central`; there was still no fresh hub
  list-page vs list-API drift in this pass.
- `.48` moved back out of the clean bucket and into a fresh
  reboot/recovery sample relative to the prior `2026-05-14T15:51:47Z`
  to `15:51:56Z` clean window. That earlier pass had already reached
  local `/api/status.uptime_seconds=1729`, but this pass's direct local
  root/status/config sweep returned clean `200` responses (`0.369 s`,
  `0.021 s`, `0.072 s`) at only `uptime_seconds=63`, with local
  `/api/status` already back to
  `device_name="Rebooter - renamed test"`,
  `firmware_version="0.1.17-dev-central"`, and
  `health_state="healthy"`.
- The hub detail API briefly exposed the same restart in a narrower
  form rather than drifting on identity. On the first `16:01:20Z`
  capture it showed `latest_heartbeat.received_at="2026-05-14T16:00:24Z"`
  with `health_state="unknown"`, `uptime_seconds=0`, and
  `wifi_connected=false`, while the local device API was already back
  to `health_state="healthy"` and `uptime_seconds=63`. The later hub
  rereads at `16:01:50Z` and `16:03:02Z` reconverged to
  `health_state="healthy"` with `uptime_seconds=64` and then `124`,
  while the hub list row stayed `online` throughout. Treat that as
  another concrete `BUG-054` masked-reboot/recovery sample rather than
  a fresh central UI drift.
- The immediate 5-cycle local continuity loop stayed responsive and
  kept the renamed identity on every cycle while local
  `/api/status.uptime_seconds` climbed from `63` to `73` and local
  `/api/config` stayed stable. There was no fresh timeout,
  connection-reset, or truncated-body repro in this pass.
- `BUG-054` still did not fully narrow to zero. Cycle 2 of the same
  loop stretched local `/api/status` to `4.029 s`, and cycle 4 still
  took `1.036 s`, so the pass added both a fresh reboot sample and a
  renewed local status-latency signal.

### 2026-05-14 EDT - renamed-test recheck stayed improved again with only a narrow local status-latency blip

Fresh live recheck run around `2026-05-14T15:51:47Z` through
`15:51:56Z` against the authenticated hub devices page/detail/API plus
local `.48` device `/`, `/api/status`, and `/api/config`, followed by
an immediate 5-cycle local continuity loop on the renamed soak target.

Concrete improved findings:
- The rendered hub Devices page still matched
  `/api/v1/admin/devices`, and the hub detail page/API stayed
  converged as well. All hub surfaces still showed
  `Rebooter - renamed test` at `192.168.1.48`,
  `online` / `heartbeat_state="online"`, firmware
  `0.1.17-dev-central`, with the list heartbeat at
  `2026-05-14T15:50:11Z` and the detail API still exposing
  `latest_heartbeat.health_state="healthy"` at
  `latest_heartbeat.uptime_seconds=1624`.
- The direct local root/status/config sweep also stayed clean. Local
  `/` returned `200` in `0.432 s`, local `/api/status` returned `200`
  in `0.022 s` with `device_name="Rebooter - renamed test"`,
  `firmware_version="0.1.17-dev-central"`,
  `health_state="healthy"`, and `uptime_seconds=1723`, and local
  `/api/config` returned `200` in `0.069 s` while still matching the
  renamed identity.
- The immediate 5-cycle local continuity loop stayed responsive
  throughout. Local `/api/status.uptime_seconds` climbed from `1723` to
  `1729`, local `/api/config` kept matching
  `device_name="Rebooter - renamed test"` on every cycle, and this pass
  added no fresh reboot, timeout, connection-reset, or truncated-body
  evidence.

Reliability notes:
- `BUG-054` still stays open, but only as a narrower latency watch in
  this pass. The prior cycle-4 `/api/config` stretch did not repeat;
  instead cycle 5 stretched local `/api/status` to `1.038 s` while
  still returning `200` with `health_state="healthy"` and increasing
  uptime. Treat this as another improved recovery-window sample rather
  than a fresh repro.

### 2026-05-14 EDT - renamed-test recheck after the short clean `.48` window: `.48` stayed improved with uptime continuity and only one moderate config-page slowdown

Fresh live recheck run around `2026-05-14T15:42:27Z` through
`15:42:35Z` against the authenticated hub Devices page plus
`/api/v1/admin/devices` and `/api/v1/admin/devices/<id>` for the
renamed soak target at `192.168.1.48`, followed by an immediate
5-cycle local `/`, `/api/status`, and `/api/config` continuity loop.

Concrete improved findings:
- The rendered hub Devices page still matched
  `/api/v1/admin/devices` on the renamed soak target. Both hub surfaces
  showed `Rebooter - renamed test` at `192.168.1.48`, `online` /
  `heartbeat_state="online"`, firmware `0.1.17-dev-central`, and fresh
  heartbeat `2026-05-14T15:42:11Z`; the hub detail API also still
  showed `latest_heartbeat.health_state="healthy"` with
  `uptime_seconds=1144`.
- `.48` stayed in a meaningfully stronger recovery window than the
  prior `2026-05-14T15:30:42Z` to `15:31:40Z` sample. The direct local
  root/status/config sweep returned clean `200` responses
  (`0.203 s`, `0.034 s`, `0.057 s`), local `/api/status` still
  reported `device_name="Rebooter - renamed test"`,
  `firmware_version="0.1.17-dev-central"`,
  `health_state="healthy"`, and `uptime_seconds=1162`, and local
  `/api/config` still matched the renamed identity.
- The immediate 5-cycle local continuity loop stayed clean as well.
  Local `/api/status.uptime_seconds` climbed steadily from `1163` to
  `1170`, every local root/status/config read stayed at `200`, and
  there was no fresh reboot, timeout, connection-reset, or
  truncated-body repro.

Reliability notes:
- `BUG-054` did not re-strengthen in this pass, but keep it open in the
  narrower latency-watch bucket. Cycle 4 stretched local
  `/api/config` to `1.173 s` even though the same cycle kept local `/`
  at `0.415 s`, local `/api/status` at `0.016 s`, and uptime
  continuity intact.

### 2026-05-14 EDT - renamed-test recheck after the `15:21Z` reboot sample: `.48` recovered into another short clean window, with only local root-page latency still visible

Fresh focused live recheck run from about `2026-05-14T15:30:42Z`
through `15:31:40Z` against the authenticated hub Devices page,
`/api/v1/admin/devices`, `/api/v1/admin/devices/<id>`, and the renamed
soak target's local `/`, `/api/status`, and `/api/config`, followed by
an immediate 5-cycle local continuity loop on `192.168.1.48`.

Concrete improved findings:
- The rendered hub Devices page still matched
  `/api/v1/admin/devices` on the renamed soak target. The list API
  showed `Rebooter - renamed test` at `192.168.1.48` with
  `heartbeat_state="online"`, `online=true`, firmware
  `0.1.17-dev-central`, and last heartbeat `2026-05-14T15:31:11Z`,
  and the rendered Devices page still contained the same device id,
  name, IP, and firmware.
- `.48` recovered cleanly relative to the prior `2026-05-14T15:19:59Z`
  to `15:21Z` masked-reboot sample. The hub detail API showed
  `Rebooter - renamed test` on `0.1.17-dev-central` with
  `latest_heartbeat.health_state="healthy"` and
  `latest_heartbeat.uptime_seconds=484`, while the first direct local
  root/status/config sweep returned clean `200` responses
  (`0.20 s`, `0.022 s`, `0.071 s`) and local `/api/status` already
  reported `device_name="Rebooter - renamed test"`,
  `firmware_version="0.1.17-dev-central"`,
  `health_state="healthy"`, and `uptime_seconds=458`.
- The immediate 5-cycle local continuity loop also stayed clean. Local
  `/api/status` kept the renamed identity and healthy state throughout
  while `uptime_seconds` climbed from `510` to `517`, and local
  `/api/config` kept matching `device_name="Rebooter - renamed test"`
  on every cycle.

Concrete remaining issue:
- `BUG-054` stays open, but only in latency form for this pass. The
  local device UI root page returned `200` on all five continuity-loop
  cycles, yet cycle 5 still stretched to `2.946 s` while
  `/api/status` and `/api/config` stayed fast and uptime continued
  upward. This run added no fresh reboot, timeout, connection-reset, or
  truncated-body evidence.

### 2026-05-14 EDT - renamed-test recheck after the longer clean window: `.48` rebooted again, `.30` regressed harder, `.207` stayed improved, and `.69` stayed stale

Fresh live recheck run around `2026-05-14T15:19:59Z` through about
`15:21Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces still showed
  `.48`, `.30`, `.225`, and `.207` `online` with fresh heartbeats,
  while `.69` remained `offline` with its stale
  `2026-05-13T22:06:13Z` heartbeat.
- `.207` stayed in a clean recovery window rather than moving back into
  the fresh-repro bucket. The hub row/detail still showed it `online`
  on `0.1.16-dev-central` with heartbeat `2026-05-14T15:19:59Z`,
  `health: healthy`, and `uptime_s=1206`, while local `/`,
  `/api/status`, and `/api/config` all returned clean `200` responses
  (`0.193 s`, `0.022 s`, `0.108 s`) with local
  `/api/status.uptime_seconds=1265`.
- `.225` also stayed in uptime continuity. The hub row/detail still
  showed `Erica's F.R Speaker` `online` on `0.1.17-dev-central` with
  heartbeat `2026-05-14T15:20:11Z` and `uptime_s=5166`, while local
  `/`, `/api/status`, and `/api/config` all returned `200` with local
  `/api/status.uptime_seconds=5212`. One slower `1.281 s`
  `/api/status` read did not coincide with a reset or state mismatch.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` re-strengthened again on the renamed soak target. The hub
  row/detail plus local `/api/status` and `/api/config` still
  converged on `Rebooter - renamed test` / `online` /
  `0.1.17-dev-central`, but the prior `2026-05-14T15:09:56Z` to
  `15:12:59Z` pass had already reached local
  `/api/status.uptime_seconds=1429` while this pass's fresh local
  root/status/config sweep returned only `179` with clean `200`
  responses (`0.441 s`, `0.061 s`, `0.092 s`). The hub detail API was
  already back to `health: healthy` with `uptime_s=128` at heartbeat
  `2026-05-14T15:20:05Z`, so this is another masked reboot/recovery
  sample behind an `online` hub row.
- `BUG-056` strengthened more clearly than in the prior pass and is the
  main new regression in this sweep. The hub devices row/API still
  showed `.30` `online` on `0.1.17-dev-central` with heartbeat
  `2026-05-14T15:20:02Z`, but the hub detail API had already fallen to
  `health: unknown` with `uptime_s=0` while the local root/status/config
  sweep still returned clean `200` responses (`0.184 s`, `0.042 s`,
  `0.085 s`) at only `uptime_seconds=58`. Relative to the prior
  `2026-05-14T15:09:56Z` to `15:12:59Z` pass where local
  `/api/status.uptime_seconds` had already climbed to `1903`, this is
  another fresh reboot/recovery event behind a still-healthy list row.
- `.69` remains the stable offline control and keeps `BUG-057`
  unchanged: the hub list/API still showed `offline` with stale
  `2026-05-13T22:06:13Z`, local `/`, `/api/status`, and `/api/config`
  still timed out after about `15 s`, but the hub detail page/API still
  showed stale `health: healthy` / `uptime_s: 69`.

### 2026-05-14 EDT - renamed-test recheck after the 14:40Z reboot window: `.48` held a clean loop, `.30` rebooted behind an online hub row, and `.69` stayed mismatched

Fresh live recheck run around `2026-05-14T14:51:03Z` through
`14:53Z` against the authenticated hub devices page/API plus hub
detail pages/APIs for `.48`, `.30`, `.225`, `.207`, and `.69`,
followed by a fresh 5-cycle local continuity loop on `192.168.1.48`.

Concrete improved findings:
- The rendered hub Devices page still matched `/api/v1/admin/devices`
  on every comparison target in this pass. The list page and list API
  both still showed `.48`, `.30`, `.225`, and `.207` `online`, while
  `.69` stayed `offline` at stale
  `2026-05-13T22:06:13Z`.
- `.48` moved back into a clean recovery bucket after the prior
  `2026-05-14T14:40:52Z` to `14:42:17Z` masked reboot. The rendered hub
  row, hub detail page, `/api/v1/admin/devices`,
  `/api/v1/admin/devices/<id>`, local `/api/status`, and local
  `/api/config` all converged on `Rebooter - renamed test` /
  `online` / `0.1.17-dev-central`, with the hub detail page/API showing
  heartbeat `2026-05-14T14:51:56Z`, `health: healthy`, and
  `uptime_s` / `uptime_seconds=186`.
- The first direct local `.48` root/status/config sweep also returned
  clean `200` responses (`0.218 s`, `0.014 s`, `0.062 s`) with local
  `/api/status` already at `uptime_seconds=251`. The immediate 5-cycle
  follow-up loop then stayed fully clean while local `/api/status`
  climbed from `297` to `302`, with root-page reads holding at
  `0.108 s` to `0.154 s`. This pass added no fresh timeout,
  connection-reset, or truncated-body repro for `BUG-054`.
- `.225` and `.207` also stayed operationally improved. Local
  `/api/status` returned `uptime_seconds=3536` on `.225` and `1732` on
  `.207`, both with clean local `/`, `/api/status`, and `/api/config`
  `200` responses. The standing desired-name drift on `.225` and `.207`
  remained unchanged, but neither device added a fresh reboot or stall
  sample in this pass.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-056` strengthened again on `.30` in another masked-reboot shape.
  The rendered hub row, hub detail page, `/api/v1/admin/devices`, and
  `/api/v1/admin/devices/<id>` all still showed `.30`
  `online` on `0.1.17-dev-central` with heartbeat
  `2026-05-14T14:52:25Z`, `health: healthy`, and
  `uptime_s` / `uptime_seconds=664`. But the prior `14:40:52Z`
  recheck had already reached local `/api/status.uptime_seconds=7840`,
  while this pass's fresh local root/status/config sweep now returned
  only `uptime_seconds=700` with clean `200` responses
  (`0.322 s`, `0.015 s`, `0.067 s`). Treat that as another fresh reboot
  behind a healthy-looking hub row rather than just the standing name
  drift.
- `BUG-057` remained fully concrete and unchanged. The hub devices page
  and `/api/v1/admin/devices` still showed `192.168.1.69`
  (`Erica''s R.L. Speaker`) `offline` with stale
  `last_heartbeat_at="2026-05-13T22:06:13Z"`, and local `/`,
  `/api/status`, and `/api/config` still timed out after about `15 s`.
  But the rendered hub detail page and `/api/v1/admin/devices/<id>`
  still showed `health: healthy` and `uptime_s` / `uptime_seconds=69`
  from that stale heartbeat sample.

### 2026-05-14 EDT - renamed-test recheck after the `14:31Z` recovery: `.48` rebooted again behind an online hub row, while `.207`, `.30`, and `.225` stayed improved

Fresh live recheck run around `2026-05-14T14:40:52Z` through
`14:42:17Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 5-cycle local loop
on `.48`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces showed `.48`
  `online` on `0.1.17-dev-central` with last heartbeat
  `2026-05-14T14:40:16Z`, `.30` `online` on `0.1.17-dev-central` with
  `2026-05-14T14:38:19Z`, `.225` `online` on `0.1.17-dev-central` with
  `2026-05-14T14:40:11Z`, `.207` `online` on `0.1.16-dev-central` with
  `2026-05-14T14:40:13Z`, and `.69` `offline` with its still-stale
  `2026-05-13T22:06:13Z` heartbeat.
- `.207` stayed in a concrete recovery window relative to the earlier
  `14:22Z` to `14:25Z` reboot sample. The hub detail page/API rendered
  `healthy` at `uptime_s` / `uptime_seconds=964`, while local
  `/api/status` returned `200` in `0.023 s` with
  `uptime_seconds=1006`; local `/` and `/api/config` also stayed clean.
- `.30` and `.225` also stayed improved operationally. Local root /
  status / config all returned clean `200` responses, with local
  `/api/status.uptime_seconds=7840` on `.30` and `2809` on `.225`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` strengthened again on the renamed soak target immediately
  after the prior `14:31:44Z` to `14:33:27Z` clean window. The rendered
  Devices row and `/api/v1/admin/devices` still showed `.48`
  `online` / `0.1.17-dev-central` with fresh heartbeat
  `2026-05-14T14:40:16Z`, while the first direct local reread already
  reported only `uptime_seconds=42` in `0.022 s` and local
  `/api/config` still matched `Rebooter - renamed test`. In the same
  first sweep, `/api/v1/admin/devices/<id>` had already fallen back to
  `latest_heartbeat.health_state="unknown"` with
  `latest_heartbeat.uptime_seconds=0` at that same `14:40:16Z`
  heartbeat, so this was another fresh reboot masked by an `online`
  list row rather than just stale local telemetry.
- The immediate 5-cycle local follow-up loop on `.48` from
  `14:42:02Z` to `14:42:07Z` recovered cleanly: root-page reads stayed
  at `0.143 s` to `0.312 s`, `/api/status` at `0.019 s` to `0.057 s`,
  `/api/config` at `0.070 s` to `0.133 s`, and local
  `/api/status.uptime_seconds` climbed from `111` to `117`. A focused
  hub detail-page reread at `14:42:17Z` then rendered `health: healthy`
  and `uptime_s: 124`, with the detail API also back to
  `latest_heartbeat.health_state="healthy"` and
  `uptime_seconds=64`. Treat this as another short reboot/recovery
  sample behind a healthy-looking central list row, but note that this
  pass did not reproduce the earlier root-timeout, connection-reset, or
  truncated-body shapes.
- `.69` kept `BUG-057` fully concrete and unchanged. The hub devices
  page and `/api/v1/admin/devices` still showed `offline` with stale
  `last_heartbeat_at="2026-05-13T22:06:13Z"`, local `/`, `/api/status`,
  and `/api/config` still timed out after about `15 s`, but the hub
  detail page still rendered `health: healthy` / `uptime_s: 69` from
  that stale heartbeat sample.

### 2026-05-14 EDT - renamed-test soak recheck after the `14:25Z` recovery: `.48` held clean continuity, `.207` improved, and `.69` stayed centrally inconsistent

Fresh live recheck run around `2026-05-14T14:31:44Z` through
`14:33:27Z` against the authenticated hub Devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 5-cycle local loop
on `192.168.1.48`.

Concrete improved findings:
- The live hub Devices page still matched `/api/v1/admin/devices` on
  `.48`, `.30`, `.225`, `.207`, and `.69`. The rendered list still
  exposed the same device links and local-IP rows as the admin list
  API, so this pass added no new hub list-vs-API drift.
- `.48` held a materially cleaner continuity window than the earlier
  `14:10Z`-`14:14Z` reboot sample and extended the later `14:22Z`-`14:25Z`
  recovery. Hub list/detail plus local `/api/status` and `/api/config`
  all still showed `Rebooter - renamed test` / `online` /
  `0.1.17-dev-central`. The first local root/status/config sweep
  returned clean `200` responses (`0.225 s`, `0.021 s`, `0.078 s`) with
  local `/api/status.uptime_seconds=1180`, and the immediate 5-cycle
  loop stayed fully clean while uptime climbed from `1275` to `1281`.
- The `.48` follow-up reread also stayed converged: the hub list still
  showed heartbeat `2026-05-14T14:33:10Z`, the hub detail API reported
  `latest_heartbeat.health_state="healthy"` with
  `uptime_seconds=1264`, and local `/api/status` had continued to
  `uptime_seconds=1282`. Cycle 5 did slow the local root page to
  `1.39 s`, but there was no timeout, reset, or truncated-body repro in
  this pass.
- `.207` improved relative to the prior `14:22Z`-`14:25Z` masked reboot
  window. The hub row/API still showed it `online` on
  `0.1.16-dev-central` with heartbeat `2026-05-14T14:33:13Z`, the hub
  detail API reported `healthy` at `uptime_seconds=544`, and the local
  device returned clean `200` responses on `/api/status` and
  `/api/config` with local `/api/status.uptime_seconds=560`. One slower
  `1.006 s` local `/api/status` sample did not coincide with any reset.
- `.30` and `.225` also remained operationally improved in this pass:
  hub UI/API still showed both `online` on `0.1.17-dev-central`, while
  local `/api/status` reported `uptime_seconds=7337` on `.30` and
  `2307` on `.225` with clean local root/status/config reads.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` stays open historically on `.48`, but this pass added only a
  better recovery sample. The current failure shape narrowed to a
  watch-level local-root latency spike (`1.39 s`) rather than a fresh
  reboot, timeout, connection reset, or truncated body.
- `BUG-057` remained fully concrete on `.69`. The hub Devices page and
  `/api/v1/admin/devices` still showed `offline` with stale
  `last_heartbeat_at="2026-05-13T22:06:13Z"`, and local `/`,
  `/api/status`, and `/api/config` still timed out after about `15 s`,
  but the hub detail API still exposed
  `latest_heartbeat.health_state="healthy"` with
  `uptime_seconds=69`. No improvement yet on the list-vs-detail
  inconsistency.

### 2026-05-14 EDT - renamed-test recheck after the `14:14Z` masked-reboot window: `.48` stayed clean through another local loop, `.207` rebooted again behind a fresh online hub row, and `.69` kept the stale-healthy detail view

Fresh live recheck run around `2026-05-14T14:22:53Z` through
`14:25:18Z` against the authenticated hub devices page/API, the hub
device detail pages/APIs for `.48` and `.69`, and local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by an immediate 5-cycle local loop on
`.48` and a focused reread on `.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. The rendered rows and the API
  both showed `.48`, `.30`, and `.225` `online` with fresh heartbeats,
  while `.69` stayed `offline`; `.207` briefly converged `offline` in
  the first pass and then returned to `online` in the follow-up reread,
  with the Devices page and list API staying aligned at each step.
- `.48` improved materially relative to the prior `14:10:33Z` to
  `14:14:57Z` masked-reboot window. The rendered Devices row, hub
  detail page, `/api/v1/admin/devices`, `/api/v1/admin/devices/<id>`,
  local `/api/status`, and local `/api/config` all reconverged on
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`. In the
  first sweep, local root/status/config all returned clean `200`
  responses (`0.233 s`, `0.023 s`, `0.071 s`) and local
  `/api/status.uptime_seconds` was already `648`; the immediate 5-cycle
  loop then stayed fully clean while local `/api/status` climbed from
  `648` to `703`, with root-page reads at `0.113 s`-`0.233 s`,
  `/api/status` at `0.019 s`-`0.023 s`, and `/api/config` at
  `0.066 s`-`0.085 s`.
- `.225` improved further out of the fresh-reboot bucket. The hub row
  and list API still showed `Erica's F.R Speaker` `online` on
  `0.1.17-dev-central`, while local root/status/config all returned
  clean `200` responses (`0.185 s`, `0.036 s`, `0.067 s`) and local
  `/api/status` had climbed to `uptime_seconds=1730`, then the follow-up
  hub detail reread showed `latest_heartbeat.uptime_seconds=1806`.
- `.30` also stayed in the improved bucket. The hub row/API still
  showed `Erica's Subwoofer` `online` on `0.1.17-dev-central`, local
  root/status/config all returned clean `200` responses
  (`0.159 s`, `0.020 s`, `0.072 s`), and local `/api/status` had
  climbed to `uptime_seconds=6760` with the later hub detail reread at
  `uptime_seconds=6844`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-055` strengthened again on `.207` in a two-step shape. In the
  first pass, the hub devices page/API had already converged on
  `offline` with stale `last_heartbeat_at="2026-05-14T14:18:15Z"`
  while the local device still half-answered: the root page returned
  `200` only after `5.006 s`, local `/api/status` failed after
  `9.571 s` with a connection reset, and local `/api/config` still
  returned `200` in `0.091 s` with `Erica's ?.?. Speaker`. A focused
  reread a couple of minutes later then showed the hub Devices page and
  `/api/v1/admin/devices` back to `online` on `0.1.16-dev-central`
  with heartbeat `2026-05-14T14:25:13Z`, while local `/api/status`
  returned `200` in `0.022 s` but reported only `uptime_seconds=64`.
  Treat that as another fresh masked reboot/recovery behind a healthy
  central row, with an earlier in-pass local stall/reset shape.
- `BUG-057` remains concrete and now clearly reaches the rendered hub
  detail page as well as the detail API. The hub devices page and
  `/api/v1/admin/devices` still showed `.69` `offline` with stale
  `last_heartbeat_at="2026-05-13T22:06:13Z"`, and the local device
  still timed out on `/`, `/api/status`, and `/api/config` after about
  `12 s`. But the hub detail page still rendered `health: healthy` and
  `uptime_s: 69`, matching `/api/v1/admin/devices/<id>` for that same
  stale heartbeat sample.

### 2026-05-14 EDT - renamed-test recheck after the `14:01Z` recovery: `.48` rebooted again, `.225` re-entered the reboot bucket, and `.69` split inside the hub itself

Fresh live recheck run around `2026-05-14T14:10:33Z` through
`14:14:57Z` against the authenticated hub devices page/API plus hub
device detail API and direct local `/`, `/api/status`, and
`/api/config` probes for `.48`, `.30`, `.225`, `.207`, and `.69`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. The rendered list and list API
  both showed `.48`, `.30`, `.225`, and `.207` `online` with fresh
  heartbeats while `.69` stayed `offline` with stale
  `2026-05-13T22:06:13Z` last-seen data.
- `.30` and `.207` both stayed out of the fresh-reboot bucket in this
  window. The hub row/API still matched on identity and firmware, and
  local `/api/status` reported `uptime_seconds=6024` on `.30` and
  `1597` on `.207`, which is uptime continuity rather than another
  masked restart.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` strengthened again on the renamed soak target in another
  reboot-through-healthy-row shape. The first pass at `14:10:33Z`
  still showed `.48` converged across the hub list/API, hub detail
  API, local `/api/status`, and local `/api/config` as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
  local `/api/status.uptime_seconds=579` and hub detail
  `latest_heartbeat.uptime_seconds=424`. But by `14:14:57Z`, the same
  hub list/API still showed `.48` `online` with a fresh
  `2026-05-14T14:14:10Z` heartbeat while hub detail had already fallen
  back to `latest_heartbeat.uptime_seconds=124` and local
  `/api/status` had also reset to only `uptime_seconds=142`. Relative
  to the earlier `14:10:33Z` local uptime of `579`, this is another
  fresh masked reboot within about four minutes. The local root page
  also showed the weaker latency shape before that reboot, stretching
  to about `3.06 s` at `14:10:33Z` before returning to `0.13 s` by the
  `14:14:57Z` reread.
- `.225` re-entered the masked-reboot bucket relative to the last clean
  memo window. In the earlier `2026-05-14T13:00:56Z` to `13:04:04Z`
  pass, the same device had already climbed to about
  `uptime_seconds=7260`; by `14:10:33Z`, the live hub list/API still
  showed `.225` `online` on `0.1.17-dev-central`, but local
  `/api/status` had dropped back to only `uptime_seconds=994`. A
  follow-up reread at `14:14:57Z` climbed to `1223`, so the device was
  stable during this pass, but the lower uptime is still concrete fresh
  reboot evidence behind a healthy-looking hub row.
- `BUG-057` reproduced as a new central inconsistency on the offline
  control device. In both passes, the live hub devices page and
  `/api/v1/admin/devices` showed `.69` (`Erica's R.L. Speaker`) as
  `offline` with stale `last_heartbeat_at="2026-05-13T22:06:13Z"`,
  while the local root page plus `/api/status` plus `/api/config` all
  timed out from this host. But the hub detail API for the same device
  still exposed `latest_heartbeat.health_state="healthy"`,
  `latest_heartbeat.uptime_seconds=69`, and
  `latest_heartbeat.last_event_type="boot"` at that same stale
  `received_at="2026-05-13T22:06:13Z"`. That makes the central detail
  surface materially healthier-looking than both the list row and the
  locally unreachable device.

### 2026-05-14 EDT - renamed-test recheck after the `13:51Z` clean window: `.48` masked-rebooted again while the hub list stayed healthy-looking

Fresh live recheck run around `2026-05-14T14:00:53Z` through
`14:01:03Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for the renamed
soak target at `192.168.1.48`, followed by an immediate 5-cycle local
continuity loop and a confirming hub/local reread.

Concrete improved findings:
- The rendered hub Devices page still matched `/api/v1/admin/devices`
  on the renamed soak target in this pass. Both surfaces showed
  `Rebooter - renamed test` `online` on `0.1.17-dev-central`, with the
  list API and list page both carrying the fresh
  `2026-05-14T14:00:53Z` heartbeat and the hub detail page still
  rendering the same device identity and local IP `192.168.1.48`.
- The local device recovered quickly once the reboot window passed. The
  immediate 5-cycle follow-up loop returned clean `200` responses on
  `/`, `/api/status`, and `/api/config`, with root-page reads at
  `0.101 s`-`0.256 s`, `/api/status` at `0.014 s`-`0.018 s`,
  `/api/config` at `0.062 s`-`0.073 s`, and local
  `/api/status.uptime_seconds` climbing from `13` to `18`. A
  confirming reread a few seconds later showed local root back at
  `200` in `0.10 s`, local `/api/status` back to
  `health_state="healthy"` at `uptime_seconds=38`, and local
  `/api/config` still matching `Rebooter - renamed test`.

Reliability notes:
- `BUG-054` strengthened again on the renamed soak target in another
  reboot-through-healthy-row shape. Relative to the prior clean
  `2026-05-14T13:51:23Z` to `13:51:27Z` window where local
  `/api/status` had already reached `uptime_seconds=941`, the first
  local reread in this pass found the device already back at
  `uptime_seconds=12` with `health_state="unknown"`, even though the
  hub list API and rendered Devices page still showed
  `Rebooter - renamed test` `online` on `0.1.17-dev-central` with the
  fresh `2026-05-14T14:00:53Z` heartbeat.
- The same pass also reproduced another first-hit local root failure.
  The first direct local `/` request timed out after the full `15 s`,
  while local `/api/status` still returned `200` in `0.127 s` and
  local `/api/config` returned `200` in `0.097 s`. That puts the
  immediate post-reboot behavior back in the mixed root-timeout plus
  low-uptime bucket rather than the earlier latency-only bucket.
- The confirming reread showed the central split explicitly: the hub
  list API still presented the device as `online` with heartbeat
  `2026-05-14T14:01:03Z`, while the hub detail API had already dropped
  to `latest_heartbeat.health_state="unknown"` and
  `latest_heartbeat.uptime_seconds=0`. The local device then recovered
  from `uptime_seconds=12` to `38`, so this pass captured another short
  masked reboot/recovery window rather than just a stale single probe.

### 2026-05-14 EDT - renamed-test recheck stayed converged after the `13:40Z` recovery window

Fresh live recheck run around `2026-05-14T13:51:23Z` through
`13:51:27Z` against the authenticated hub devices page/API plus the hub
device detail page/API and direct local `.48` device `/`,
`/api/status`, and `/api/config`, followed by an immediate 5-cycle
local continuity loop.

Concrete improved findings:
- The rendered Devices page still matched `/api/v1/admin/devices` on
  the renamed soak target. Both surfaces showed
  `Rebooter - renamed test` `online` on `0.1.17-dev-central` with
  heartbeat `2026-05-14T13:50:53Z`.
- The hub device detail page and `/api/v1/admin/devices/<id>` also
  remained aligned in this pass. The admin detail API still exposed the
  live status under `latest_heartbeat`, showing
  `health_state="healthy"` and `uptime_seconds=906`, while the detail
  page rendered the same device identity, firmware, local IP
  `192.168.1.48`, and heartbeat timestamp.
- The first direct local `.48` root/status/config sweep returned clean
  `200` responses (`0.773 s`, `0.026 s`, `0.074 s`), and local
  `/api/status` plus `/api/config` still matched the renamed identity
  on `0.1.17-dev-central`. Local `/api/status` reported
  `health_state="healthy"` with `uptime_seconds=939`.
- The immediate 5-cycle local follow-up loop then stayed fully clean.
  Root-page reads held at `0.103 s`-`0.233 s`, `/api/status` at
  `0.020 s`-`0.026 s`, and `/api/config` at `0.070 s`-`1.146 s`, while
  local `/api/status.uptime_seconds` climbed from `939` to `941`.

Reliability notes:
- This pass added no fresh `BUG-054` reboot, timeout, connection-reset,
  or truncated-body evidence. Treat it as another concrete short
  recovery/continuity sample for the renamed soak target.

### 2026-05-14 EDT - renamed-test recheck after the `13:34Z` stall window: `.48` recovered into short clean convergence again

Fresh live recheck run around `2026-05-14T13:40:34Z` through
`13:41:04Z` against the authenticated hub devices page, the hub device
detail page, `/api/v1/admin/devices`, `/api/v1/admin/devices/<id>`, and
local device `/`, `/api/status`, and `/api/config` probes on
`192.168.1.48`, followed by an immediate 5-cycle local continuity loop.

Concrete improved findings:
- The rendered hub Devices page still matched `/api/v1/admin/devices`
  on the renamed soak target. Both surfaces showed
  `Rebooter - renamed test` `online` on `0.1.17-dev-central` at local
  IP `192.168.1.48`, and the row remained linked to the same device id
  `dev_01KRHTH2DQSTH1PAXBJD9P2XFY`.
- The hub device detail page also reconverged with the admin detail API.
  During the same window the detail page showed
  `Last seen 2026-05-14T13:40:53Z`, `health: healthy`, and
  `uptime_s: 306`, while `/api/v1/admin/devices/<id>` reported the same
  identity/version plus `latest_heartbeat.health_state="healthy"` and
  `latest_heartbeat.uptime_seconds=306`.
- The local device surfaces matched that same recovery state. The first
  direct local root/status/config sweep returned clean `200` responses
  (`0.315 s`, `0.022 s`, `0.078 s`), local `/api/status` reported
  `device_name="Rebooter - renamed test"`,
  `firmware_version="0.1.17-dev-central"`,
  `health_state="healthy"`, and `uptime_seconds=290`, and local
  `/api/config` kept the same device name and central device id.
- The immediate 5-cycle local follow-up loop also stayed fully clean:
  root-page reads stayed at `0.128 s`-`0.190 s`, `/api/status` stayed at
  `0.021 s`-`0.257 s`, `/api/config` stayed at `0.035 s`-`0.142 s`, and
  local `/api/status.uptime_seconds` climbed from `312` to `318` without
  a timeout, reset, or truncated-body failure.

Reliability notes:
- `BUG-054` remains open historically because the earlier
  `13:31:41Z`-`13:34:29Z` pass still captured a fresh reboot plus
  root/status stall window, but this follow-up pass added only a clean
  short recovery sample. No new hub UI drift, masked reboot, connection
  reset, or local root corruption reproduced in this recheck window.

### 2026-05-14 EDT - renamed-test recheck after the `13:22Z` clean window: `.48` rebooted again, then slid into another local root/status stall while the hub list stayed online

Fresh live recheck run around `2026-05-14T13:31:41Z` through
`13:34:29Z` against the authenticated hub devices list/API, the hub
device detail page for `.48`, and local device `/`, `/api/status`, and
`/api/config` probes on `192.168.1.48`.

Concrete improved findings:
- The rendered hub Devices list still matched `/api/v1/admin/devices`
  on the renamed soak target. Both surfaces showed
  `Rebooter - renamed test` `online` on `0.1.17-dev-central` at
  `192.168.1.48`, first with last heartbeat `2026-05-14T13:31:41Z` and
  later with `2026-05-14T13:33:46Z`. There is still no fresh hub
  list-vs-API drift on `.48`.
- When local `/api/status` recovered after the latest restart, it
  reconverged cleanly on the expected identity and firmware. The
  confirming 3-cycle loop from `13:34:27Z` to `13:34:29Z` returned
  clean `200` responses in `0.03 s` to `0.07 s` and climbed from
  `uptime_seconds=47` to `49` with `health_state="healthy"`.

Reliability notes:
- `BUG-054` strengthened again immediately after the earlier
  `13:22:34Z` to `13:22:44Z` clean window. On the first recheck at
  `13:31:41Z`, the hub list/API still showed the renamed device
  `online` on `0.1.17-dev-central`, while the local root page returned
  `200` only after `2.31 s`, local `/api/status` returned `200` in
  `0.03 s`, and local `/api/config` returned `200` in `0.08 s`. But
  local `/api/status` had already fallen back to
  `uptime_seconds=132`, which is fresh reboot evidence relative to the
  prior clean loop where the same device had already reached
  `uptime_seconds=1314` by `2026-05-14T13:22:44Z`.
- The renamed device then slid back into the local-surface failure
  shape within the same pass. The immediate 5-cycle follow-up loop from
  roughly `13:31:41Z` to `13:31:49Z` stayed clean on the root page and
  local `/api/status`, but a later direct local root-page fetch timed
  out at the full `10 s`, the next local `/api/status` fetch also
  timed out at the full `10 s`, and local `/api/config` only limped
  through with `200` after `7.30 s`.
- The stall coincided with another fresh short-uptime window rather
  than a one-off slow page. A full `/api/status` payload fetched just
  after the stall showed `device_name="Rebooter - renamed test"`,
  `firmware_version="0.1.17-dev-central"`,
  `health_state="unknown"`, and `uptime_seconds=23`; the immediate
  confirming loop then climbed only from `47` to `49`.
- The hub device detail page eventually reflected the restart but still
  lagged behind the local recovery. On repeated refreshes after the
  later `2026-05-14T13:33:46Z` heartbeat, the detail page still showed
  `health: unknown` and `uptime_s: 0` while local `/api/status` had
  already recovered to `47`-`49`. Treat this as the same reboot /
  recovery episode rather than a new standalone UI drift bug.

### 2026-05-14 live recheck addendum - post-`13:04Z` recovery window held for `.48` and `.207`, with no fresh hub UI/API drift

Fresh live recheck run around `2026-05-14T13:10:45Z` through
`13:12:30Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by immediate 5-cycle local loops on
`.48` and `.207`.

Concrete improved findings:
- The live hub Devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces showed `.48`
  (`Rebooter - renamed test`) `online` on `0.1.17-dev-central` with
  heartbeat `2026-05-14T13:11:56Z`, `.30` (`Erica's Subwoofer`)
  `online` on `0.1.17-dev-central` with `2026-05-14T13:11:18Z`, `.225`
  (`Erica's F.R Speaker`) `online` on `0.1.17-dev-central` with
  `2026-05-14T13:11:54Z`, `.207` (`Erica's R.R. Speaker`) `online` on
  `0.1.16-dev-central` with `2026-05-14T13:11:53Z`, and `.69`
  `offline` with the same stale `2026-05-13T22:06:13Z` heartbeat.
- `.48` improved back out of the immediate-failure shape seen around
  `13:01Z`. The initial local root page plus `/api/status` plus
  `/api/config` all returned clean `200` responses (`0.35 s`, `0.02 s`,
  `0.08 s`), local status/config both exposed `Rebooter - renamed test`
  on `0.1.17-dev-central`, and local `/api/status` already reported
  `uptime_seconds=598`. The immediate 5-cycle follow-up loop from
  `13:12:15Z` to `13:12:21Z` then stayed clean and climbed from
  `uptime_seconds=687` to `693`; only one root-page sample stretched to
  `1.12 s`, well below the earlier timeout / connection-reset pattern.
- `.207` also improved out of the immediate fresh-repro bucket. The
  initial local root page plus `/api/status` plus `/api/config` all
  returned clean `200` responses (`0.23 s`, `0.03 s`, `0.07 s`), local
  `/api/status` already reported `uptime_seconds=301`, and the
  immediate 5-cycle follow-up loop from `13:12:23Z` to `13:12:30Z`
  stayed clean while local `/api/status` climbed from `399` to `404`.
  The first loop root-page sample at `2.48 s` did not coincide with a
  reset, truncated body, or local API failure.
- `.30` stayed improved with uptime continuity: hub UI/API still showed
  it `online` on `0.1.17-dev-central`, local root/status/config all
  returned clean `200` responses, and local `/api/status` reported
  `uptime_seconds=2434`.
- `.225` stayed improved with uptime continuity: hub UI/API still
  showed it `online` on `0.1.17-dev-central`, local root/status/config
  all returned clean `200` responses, and local `/api/status` reported
  `uptime_seconds=7679`.

Concrete remaining issues:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  `.30` hub `Erica's Subwoofer` vs local API/config `Rebooter`, `.225`
  hub `Erica's F.R Speaker` vs local API/config `Rebooter`, and `.207`
  hub `Erica's R.R. Speaker` vs local API/config
  `Erica's ?.?. Speaker`.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with fresh `.48` reboot evidence, another `.207` masked reboot, and only a non-repeating `.225` first-hit wobble

Fresh live recheck run around `2026-05-14T13:00:56Z` through
`13:04:04Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 5-cycle local loop
on `.48`, `.207`, and `.225`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces still showed `.48`
  (`Rebooter - renamed test`) `online` on `0.1.17-dev-central`,
  `.30` (`Erica's Subwoofer`) `online` on `0.1.17-dev-central`, `.225`
  (`Erica's F.R Speaker`) `online` on `0.1.17-dev-central`, `.207`
  (`Erica's R.R. Speaker`) `online` on `0.1.16-dev-central`, and `.69`
  `offline`.
- `.30` stayed improved in uptime continuity. The local root page plus
  `/api/status` plus `/api/config` all returned clean `200` responses
  (`0.15 s`, `0.02 s`, `0.07 s`), and local `/api/status` reported
  `device_name="Rebooter"` with `uptime_seconds=1846`, which is
  consistent with continued uptime growth from the prior `12:50Z`
  sample rather than a fresh `BUG-056` reboot.
- `.225` improved back out of the fresh-failure bucket after one bad
  first hit. The initial local root page read failed with a
  truncated-body `ChunkedEncodingError` after about `3.34 s`, but local
  `/api/status` plus `/api/config` still returned fast `200` responses
  and kept exposing only the standing desired-name drift as
  `device_name="Rebooter"`. The immediate 5-cycle follow-up loop then
  stayed fully clean with root-page reads around `0.12 s`-`0.24 s` and
  local `/api/status` climbing from `uptime_seconds=7255` to `7260`, so
  this pass only adds watch-level local-root wobble rather than a new
  reboot bucket.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` strengthened again on the renamed soak target in a
  reboot-then-recover shape. The live hub row/API still showed
  `.48` `online` on `0.1.17-dev-central` with heartbeat
  `2026-05-14T13:00:56Z`, but the first local root-page read stretched
  to about `4.03 s`, the first local `/api/status` call was forcibly
  reset after about `9.56 s`, and the next successful local
  `/api/status` sample already showed only `uptime_seconds=93`. The
  immediate 5-cycle follow-up loop then recovered cleanly and climbed
  from `uptime_seconds=162` to `167`, so this pass is fresh masked
  reboot evidence plus a transient local-root/API failure rather than
  just the weaker latency-only form.
- `BUG-055` also strengthened again behind a healthy-looking hub row.
  The live hub row/API still showed `.207` `online` on
  `0.1.16-dev-central` with heartbeat `2026-05-14T13:01:47Z`, while the
  first local `/api/status` sample reported only `uptime_seconds=82`.
  The immediate follow-up checks later in the pass still showed only
  `uptime_seconds=161`, and the confirming 5-cycle loop climbed from
  `236` to `241`. Relative to the prior `12:50:43Z` to `12:51:23Z`
  pass where `.207` had already reached `uptime_seconds=1368`, this is
  another fresh masked reboot even though the local root/status/config
  surfaces had recovered by the time of the loop.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck after the last clean window: `.48` stayed clean again, `.30` and `.207` continued upward, and no fresh hub/API drift surfaced

Fresh live recheck run around `2026-05-14T12:50:43Z` through
`12:51:23Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 5-cycle local loop
on `.48` and confirming local re-reads on `.30` and `.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces showed `.48`
  (`Rebooter - renamed test`) `online` on `0.1.17-dev-central` with
  heartbeat `2026-05-14T12:51:11Z`, `.30` (`Erica's Subwoofer`)
  `online` on `0.1.17-dev-central` with `2026-05-14T12:51:18Z`, `.225`
  (`Erica's F.R Speaker`) `online` on `0.1.17-dev-central` with
  `2026-05-14T12:51:54Z`, `.207` (`Erica's R.R. Speaker`) `online` on
  `0.1.16-dev-central` with `2026-05-14T12:51:41Z`, and `.69`
  (`Erica's R.L. Speaker`) still `offline` with its stale
  `2026-05-13T22:06:13Z` heartbeat.
- `.48` improved again in another clean short soak window. The initial
  local root page plus `/api/status` plus `/api/config` all returned
  clean `200` responses (`0.34 s`, `0.02 s`, `0.08 s`), and local
  `/api/status` plus `/api/config` both showed
  `Rebooter - renamed test` / `0.1.17-dev-central` with
  `uptime_seconds=1603`. The immediate 5-cycle follow-up loop from
  `12:51:16Z` to `12:51:21Z` stayed fully clean, with local root-page
  reads around `0.10 s`-`0.13 s`, `/api/status` around `0.02 s`-`0.03
  s`, `/api/config` around `0.07 s`-`0.09 s`, and local
  `/api/status` climbing steadily from `uptime_seconds=1636` to
  `1640`. This pass added no fresh `BUG-054` reboot, stall, or
  truncated-body sample.
- `.30` improved further out of the fresh-repro bucket. The initial
  local root page plus `/api/status` plus `/api/config` all returned
  clean `200` responses (`0.22 s`, `0.02 s`, `0.07 s`), local
  `/api/status` still exposed the standing desired-name drift as
  `Rebooter`, and local `uptime_seconds=1231`. The confirming re-read
  later in the pass kept the endpoint healthy at `uptime_seconds=1269`;
  one `1.20 s` root-page sample did not coincide with any status/config
  failure or reset. Treat this as uptime continuity rather than another
  fresh `BUG-056` masked reboot.
- `.207` also improved further out of the fresh-repro bucket. The
  initial local root page plus `/api/status` plus `/api/config` all
  returned clean `200` responses (`0.39 s`, `0.02 s`, `0.07 s`), local
  `/api/status` plus `/api/config` still exposed
  `Erica's ?.?. Speaker`, and local `/api/status` reported
  `uptime_seconds=1331`. The confirming re-read later in the pass kept
  the device healthy at `uptime_seconds=1368` without any stall or
  reset. Treat this as uptime continuity rather than another fresh
  `BUG-055` repro.
- `.225` stayed in the improved watch bucket. The hub row/API still
  showed `Erica's F.R Speaker` `online` on `0.1.17-dev-central`, while
  the local root page plus `/api/status` plus `/api/config` all
  returned clean `200` responses (`0.43 s`, `0.04 s`, `0.16 s`) and
  local `/api/status` reported `uptime_seconds=6476`. That keeps `.225`
  below fresh-reboot level in this pass even though the standing
  desired-name drift remains.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck after the `12:32Z` bad window: `.48` improved cleanly, `.30` and `.207` stayed low-uptime but did not freshly regress

Fresh live recheck run around `2026-05-14T12:40:51Z` through
`12:41:54Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 5-cycle local loop
on `.48`, `.30`, and `.207` from `12:41:30Z` to `12:41:38Z`.

Concrete improved findings:
- The live hub Devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces showed `.48`,
  `.30`, `.225`, and `.207` `online` with fresh heartbeats through
  `2026-05-14T12:41:54Z`, while `.69` stayed `offline` with its still
  stale `2026-05-13T22:06:13Z` heartbeat.
- `.48` improved materially relative to the prior `12:30Z` to `12:32Z`
  root-corruption window. The hub row/API still showed
  `Rebooter - renamed test` `online` on `0.1.17-dev-central`; local
  `/api/status` and `/api/config` still matched that identity; the
  initial local root/status/config sweep returned clean `200`
  responses (`0.47 s`, `0.03 s`, `0.06 s`) with local `/api/status`
  already at `uptime_seconds=1003`; and the immediate 5-cycle loop then
  stayed fully clean with local `/api/status` climbing from
  `uptime_seconds=1050` to `1058`. This pass did not add a fresh
  `BUG-054` timeout, reboot, or truncated-body repro.
- `.225` stayed in the improved watch bucket. The hub row/API still
  showed `Erica's F.R Speaker` `online` on `0.1.17-dev-central`, while
  the local root page plus `/api/status` plus `/api/config` all
  returned clean `200` responses (`0.24 s`, `0.02 s`, `0.09 s`) and
  local `/api/status` reported `uptime_seconds=5875`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `.30` did not add a fresh `BUG-056` sample in this pass. The hub
  still showed `Erica's Subwoofer` `online` on `0.1.17-dev-central`
  with last heartbeat `2026-05-14T12:41:19Z`, while local root,
  `/api/status`, and `/api/config` all returned clean `200`
  responses (`0.18 s`, `0.02 s`, `0.07 s`) and local `/api/status`
  reported `uptime_seconds=630` on the initial sweep. The immediate
  5-cycle loop then climbed from `678` to `686` with only one slower
  root-page sample at `0.81 s`. Relative to the prior `12:30:34Z` to
  `12:32:29Z` pass where the same device had already climbed from `131`
  to `134`, this is consistent with uptime continuity rather than a new
  masked reboot.
- `.207` also did not add a fresh `BUG-055` repro in this pass. The
  hub row/API still showed `online` on `0.1.16-dev-central` with last
  heartbeat `2026-05-14T12:41:41Z`, while local `/api/status` and
  `/api/config` still exposed `Erica's ?.?. Speaker`. The initial local
  root/status/config sweep returned clean `200` responses
  (`0.83 s`, `0.04 s`, `0.07 s`) with local `/api/status` at
  `uptime_seconds=731`, and the immediate 5-cycle loop climbed from
  `777` to `785` without another stall or truncated-body failure.
  Relative to the prior `12:30:34Z` to `12:32:29Z` pass where the same
  device had already climbed from `233` to `235`, this is also
  consistent with uptime continuity rather than a fresh reboot.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck after the `12:32Z` bad window: `.48` improved cleanly, `.30` and `.207` stayed low-uptime but did not freshly regress

Fresh live recheck run around `2026-05-14T12:40:51Z` through
`12:41:54Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 5-cycle local loop
on `.48`, `.30`, and `.207` from `12:41:30Z` to `12:41:38Z`.

Concrete improved findings:
- The live hub Devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces showed `.48`,
  `.30`, `.225`, and `.207` `online` with fresh heartbeats through
  `2026-05-14T12:41:54Z`, while `.69` stayed `offline` with its still
  stale `2026-05-13T22:06:13Z` heartbeat.
- `.48` improved materially relative to the prior `12:30Z` to `12:32Z`
  root-corruption window. The hub row/API still showed
  `Rebooter - renamed test` `online` on `0.1.17-dev-central`; local
  `/api/status` and `/api/config` still matched that identity; the
  initial local root/status/config sweep returned clean `200`
  responses (`0.47 s`, `0.03 s`, `0.06 s`) with local `/api/status`
  already at `uptime_seconds=1003`; and the immediate 5-cycle loop then
  stayed fully clean with local `/api/status` climbing from
  `uptime_seconds=1050` to `1058`. This pass did not add a fresh
  `BUG-054` timeout, reboot, or truncated-body repro.
- `.225` stayed in the improved watch bucket. The hub row/API still
  showed `Erica's F.R Speaker` `online` on `0.1.17-dev-central`, while
  the local root page plus `/api/status` plus `/api/config` all
  returned clean `200` responses (`0.24 s`, `0.02 s`, `0.09 s`) and
  local `/api/status` reported `uptime_seconds=5875`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `.30` did not add a fresh `BUG-056` sample in this pass. The hub
  still showed `Erica's Subwoofer` `online` on `0.1.17-dev-central`
  with last heartbeat `2026-05-14T12:41:19Z`, while local root,
  `/api/status`, and `/api/config` all returned clean `200`
  responses (`0.18 s`, `0.02 s`, `0.07 s`) and local `/api/status`
  reported `uptime_seconds=630` on the initial sweep. The immediate
  5-cycle loop then climbed from `678` to `686` with only one slower
  root-page sample at `0.81 s`. Relative to the prior `12:30:34Z` to
  `12:32:29Z` pass where the same device had already climbed from `131`
  to `134`, this is consistent with uptime continuity rather than a new
  masked reboot.
- `.207` also did not add a fresh `BUG-055` repro in this pass. The
  hub row/API still showed `online` on `0.1.16-dev-central` with last
  heartbeat `2026-05-14T12:41:41Z`, while local `/api/status` and
  `/api/config` still exposed `Erica's ?.?. Speaker`. The initial local
  root/status/config sweep returned clean `200` responses
  (`0.83 s`, `0.04 s`, `0.07 s`) with local `/api/status` at
  `uptime_seconds=731`, and the immediate 5-cycle loop climbed from
  `777` to `785` without another stall or truncated-body failure.
  Relative to the prior `12:30:34Z` to `12:32:29Z` pass where the same
  device had already climbed from `233` to `235`, this is also
  consistent with uptime continuity rather than a fresh reboot.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.


### 2026-05-14 EDT - renamed-test recheck after the `12:24Z` masked-reboot window: `.48` recovered into uptime continuity but still stalled/corrupted the local root page, `.30` rebooted again, and `.207` stayed in the masked-reboot bucket

Fresh live recheck run around `2026-05-14T12:30:34Z` through
`12:32:29Z` against the authenticated hub Devices page,
`/api/v1/admin/devices`, and local device `/`, `/api/status`, and
`/api/config` probes for `.48`, `.30`, `.225`, `.207`, and `.69`,
followed by an immediate 10-cycle local loop on `.48` and confirming
3-cycle loops on `.30` and `.207`.

Concrete improved findings:
- The rendered hub Devices page still matched
  `/api/v1/admin/devices` on every comparison target in this pass.
  Both hub surfaces still showed `.48`, `.30`, `.225`, and `.207`
  `online` with fresh heartbeats, while `.69` stayed `offline` with
  its still-stale `2026-05-13T22:06:13Z` heartbeat.
- `.48` improved materially relative to the prior `12:23:58Z` to
  `12:24:54Z` masked-reboot sample. The initial local sweep returned
  clean `200` responses (`0.22 s`, `0.02 s`, `0.09 s`), and local
  `/api/status` plus `/api/config` both showed
  `Rebooter - renamed test` / `0.1.17-dev-central`. The immediate
  10-cycle follow-up loop then kept local `/api/status` climbing from
  `uptime_seconds=485` to `503`, so this pass did not add a fresh
  reboot on the renamed soak target.
- `.225` stayed in the improved watch bucket again. The hub row/API
  still showed `Erica's F.R Speaker` `online` on
  `0.1.17-dev-central`, while the local root page plus `/api/status`
  plus `/api/config` all returned clean `200` responses
  (`0.22 s`, `0.02 s`, `0.08 s`) and local `/api/status` reported
  `uptime_seconds=5267`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` stays concrete on the renamed soak target, but in the
  narrowed local-UI integrity form rather than another fresh reboot in
  this pass. During the focused `.48` loop, cycle 5 stretched the local
  root page to about `3.90 s`, and cycle 9 hit another truncated-body
  `ChunkedEncodingError` (`IncompleteRead(13936 bytes read, 2087 more
  expected)`) after about `3.17 s`, while local `/api/status` and
  `/api/config` kept returning fast `200` responses and
  `uptime_seconds` still climbed from `494` to `503`. Treat that as
  renewed proof that the renamed soak target can still degrade its
  local root UI even after recovering from the earlier reboot window
  and while the hub continues to present it as healthy.
- `BUG-056` strengthened again on `.30`. In this pass the hub Devices
  page/API still showed `Erica's Subwoofer` `online` on
  `0.1.17-dev-central` with fresh heartbeats through
  `2026-05-14T12:31:18Z`, but the first local `/api/status` read
  already reported only `uptime_seconds=22` with `health_state="unknown"`.
  The confirming 3-cycle loop then climbed only from
  `uptime_seconds=131` to `134` while the local root page plus
  `/api/status` plus `/api/config` all kept returning clean `200`
  responses. Relative to the prior `12:23:58Z` to `12:24:54Z` pass
  where the same device had already reached `uptime_seconds=2050`,
  this is fresh masked-reboot evidence rather than just the standing
  desired-name drift.
- `BUG-055` also strengthened again even though the confirming loop
  stayed responsive. The hub Devices page/API still showed `.207`
  `online` on `0.1.16-dev-central` with fresh heartbeats through
  `2026-05-14T12:31:41Z`, while the initial local `/api/status`
  reported only `uptime_seconds=122`. The later 3-cycle loop stayed
  clean and climbed from `233` to `235`, but relative to the earlier
  `12:24:51Z` to `12:24:53Z` pass where `.207` had already climbed to
  `uptime_seconds=121`, the new `12:30Z` sample is still fresh reboot
  evidence behind another healthy-looking hub row.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck after the clean `12:12Z` window: `.48` fell straight back into a masked reboot with a root timeout, `.207` rebooted again behind an online row, and `.30` / `.225` stayed improved

Fresh live recheck run around `2026-05-14T12:23:58Z` through
`12:24:54Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 5-cycle local loop
on `.48` from `12:24:46Z` to `12:24:50Z` and a 3-cycle local loop on
`.207` from `12:24:51Z` to `12:24:53Z`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift: `.48`, `.30`, `.225`, and `.207` all
  rendered with the same state and heartbeat values as the admin API,
  while `.69` stayed `offline` on both surfaces.
- `.30` stayed out of the new masked-reboot bucket in this pass. The
  hub row/API still showed `Erica's Subwoofer` `online` on
  `0.1.17-dev-central`, and the local root page plus `/api/status` plus
  `/api/config` all returned clean `200` responses (`0.35 s`, `0.04 s`,
  `0.06 s`) with local `/api/status` reporting `uptime_seconds=2050`.
- `.225` also stayed in the improved watch bucket. The hub row/API
  still showed `Erica's F.R Speaker` `online` on `0.1.17-dev-central`,
  the local root page plus `/api/status` plus `/api/config` all
  returned clean `200` responses (`0.20 s`, `0.02 s`, `0.07 s`), and
  local `/api/status` reported `uptime_seconds=4887`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` re-strengthened immediately after the prior clean `12:12Z`
  window. The hub devices page/API still showed
  `Rebooter - renamed test` `online` on `0.1.17-dev-central`, with the
  pre-loop heartbeat at `2026-05-14T12:21:54Z` and the post-loop
  heartbeat at `2026-05-14T12:24:11Z`. But the initial local sweep hit
  a full root-page timeout, local `/api/status` took `5.71 s` and
  reported only `uptime_seconds=14` with `health_state="unknown"`, and
  local `/api/config` was the only fast surface (`0.06 s`). The
  immediate 5-cycle follow-up loop then recovered and climbed only from
  `uptime_seconds=45` to `50` while the hub continued to present the
  row as healthy. That is another masked reboot/recovery sample, now
  combined with a fresh local-root timeout.
- `BUG-055` also strengthened again in a cleaner masked-reboot shape.
  The hub devices page/API showed `.207` back `online` on
  `0.1.16-dev-central` with heartbeats from `2026-05-14T12:23:00Z` to
  `12:24:00Z`, while the local root page plus `/api/status` plus
  `/api/config` all returned `200` (`0.44 s`, `0.02 s`, `0.08 s`) and
  the confirming 3-cycle loop stayed responsive. But local
  `/api/status` reported only `uptime_seconds=83` on the initial sweep
  and climbed only to `121` by the end of the focused loop. Relative to
  the earlier `12:12Z` pass where the same device had already climbed to
  `uptime_seconds=1190`, this is fresh reboot evidence behind another
  healthy-looking central row.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck after the latest clean `.48` window: `.48` rebooted again, `.30` joined the masked-reboot bucket, `.207` rebooted again during the loop, and `.225` stayed improved

Fresh live recheck run around `2026-05-14T11:50:46Z` through
`11:52:22Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 5-cycle local loop
on `.48`, `.30`, `.225`, and `.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces showed `.48`,
  `.30`, `.225`, and `.207` `online` with fresh heartbeats, while `.69`
  stayed `offline` with its stale `2026-05-13T22:06:13Z` heartbeat.
- `.225` stayed in the improved watch bucket. The hub row/API still
  showed `Erica's F.R Speaker` `online` on `0.1.17-dev-central`, while
  the local root page plus `/api/status` plus `/api/config` all
  returned clean `200` responses (`0.42 s`, `0.03 s`, `0.04 s`) and
  local `/api/status` climbed from `uptime_seconds=2861` on the initial
  sweep to `2966` by the end of the focused loop. That keeps `.225`
  below fresh-reboot level in this pass even though the standing
  desired-name drift remains.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` strengthened again on the renamed soak target in another
  reboot-through-healthy-row shape. The initial local `.48` sweep still
  looked healthy, with root/status/config all returning `200`
  (`1.02 s`, `0.02 s`, `0.08 s`) and local `/api/status` reporting
  `Rebooter - renamed test` / `0.1.17-dev-central` at
  `uptime_seconds=593`, while the live hub still showed `.48` `online`
  with last heartbeat `2026-05-14T11:50:46Z`. But the immediate 5-cycle
  follow-up loop starting at `11:51:54Z` already found local
  `/api/status` reset to `uptime_seconds=74`, then climb only to `90`
  by cycle 5. The hub devices page/API still showed the device
  `online` with a fresh `2026-05-14T11:51:46Z` heartbeat after the
  loop, so the reboot window remained masked centrally even though the
  local device had restarted again.
- `.30` produced fresh masked-reboot evidence for the first time in
  this soak series. The hub devices page/API showed
  `Erica's Subwoofer` `online` on `0.1.17-dev-central` with last
  heartbeat `2026-05-14T11:51:11Z`, while the initial local sweep
  returned clean `200` responses and local `/api/status` reported only
  `uptime_seconds=23`. The immediate 5-cycle loop then climbed from
  `111` to `129`, with one slower local root-page read at about
  `3.50 s` on cycle 5. Treat that as concrete reboot/recovery evidence
  behind a healthy-looking hub row rather than just the standing
  desired-name drift.
- `BUG-055` strengthened again in both reboot and local-root-stall
  form. The initial local `.207` sweep returned clean `200` responses
  (`0.26 s`, `0.02 s`, `0.07 s`) while local `/api/status` reported
  only `uptime_seconds=143` despite the hub row/API already showing
  `.207` `online` on `0.1.16-dev-central` with last heartbeat
  `2026-05-14T11:50:12Z`. The immediate 5-cycle loop first climbed from
  `231` to `242`, then cycle 5 hit a `10.49 s` local root-page stall
  while `/api/status` dropped to `uptime_seconds=11` with
  `health_state="unknown"`. A post-loop hub refresh still showed `.207`
  `online` with last heartbeat `2026-05-14T11:52:22Z`, so the reboot
  and recovery stayed masked by the central row again.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test soak recheck after the `16:21Z` truncated-body repro: `.48` rebooted again, but the hub identity surfaces stayed converged and the immediate local loop recovered cleanly

Fresh live recheck run around `2026-05-14T16:31:50Z` through
`16:32:05Z` against the authenticated hub devices page/API plus the hub
detail page/API for the renamed soak target, followed by local device
`/`, `/api/status`, and `/api/config` probes for `.48` and an immediate
5-cycle local continuity loop.

Concrete improved findings:
- The hub list UI still matched `/api/v1/admin/devices` on the renamed
  soak target, and the hub detail page still matched the detail API on
  identity/fleet state. All four hub surfaces still showed
  `Rebooter - renamed test` at `192.168.1.48` on
  `0.1.17-dev-central`, with the list row still `online` /
  `heartbeat_state="online"` and last heartbeat
  `2026-05-14T16:31:29Z`.
- The immediate local post-reboot continuity loop stayed clean. Local
  `/`, `/api/status`, and `/api/config` all returned `200` on every
  cycle; `/api/status` stayed `healthy`; `/api/config` kept matching
  `device_name="Rebooter - renamed test"`; and local
  `/api/status.uptime_seconds` climbed steadily from `232` to `238`.

Reliability notes:
- `BUG-054` strengthened again as another fresh reboot/recovery sample
  since the prior `2026-05-14T16:20:54Z` to `16:21:08Z` pass. That
  earlier pass had already reached local `uptime_seconds=1248`, but the
  first local status read in this recheck had already reset to
  `uptime_seconds=207` and the hub detail API's
  `latest_heartbeat.last_event_type` had flipped to `"boot"` with
  `received_at="2026-05-14T16:31:29Z"` and
  `uptime_seconds=185`. The hub list row still presented the device
  simply as `online`, so this remains a reboot window that is easy to
  miss from the central list view even though the detail surfaces now
  carry the newer low-uptime heartbeat sample.
- The stronger truncated-body/root-response corruption shape from the
  prior `16:21Z` pass did not reproduce in this follow-up loop, but
  BUG-054 still retains a narrower local-root latency watch because
  cycle 5 stretched local `/` to `1.205 s` while `/api/status` and
  `/api/config` stayed fast.

### 2026-05-14 EDT - renamed-test recheck after the `16:11Z` recovery: hub stayed converged while the local root UI truncated again

Fresh live recheck run around `2026-05-14T16:20:54Z` through
`16:21:08Z` against the authenticated hub Devices page, hub detail
page, `/api/v1/admin/devices`, `/api/v1/admin/devices/<id>`, and the
renamed soak target's local `/`, `/api/status`, and `/api/config`,
followed by an immediate 5-cycle local continuity loop on
`192.168.1.48`.

Concrete improved findings:
- The rendered hub Devices page still matched `/api/v1/admin/devices`
  on the renamed soak target, and the rendered detail page still
  matched `/api/v1/admin/devices/<id>` as well. All four hub surfaces
  kept showing `Rebooter - renamed test` at `192.168.1.48`,
  `online` / `heartbeat_state="online"` on `0.1.17-dev-central`, with
  the list heartbeat at `2026-05-14T16:20:24Z` and the detail page/API
  still showing `health: healthy` / `uptime_s=1204`.
- The initial local status/config sweep also stayed converged and
  healthy. Local `/api/status` returned `200` in `0.023 s` with
  `device_name="Rebooter - renamed test"`,
  `firmware_version="0.1.17-dev-central"`,
  `health_state="healthy"`, `uptime_seconds=1237`, and
  `wifi_connected=true`; local `/api/config` returned `200` in
  `0.069 s` with `device_name="Rebooter - renamed test"`; and the
  initial local root page still returned `200` in `0.760 s`.

Reliability notes:
- `BUG-054` strengthened again on the renamed soak target, but this
  time in the narrower local-root corruption form rather than the
  masked-reboot form. During the immediate 5-cycle continuity loop,
  cycle 3 failed on local `/` after `4.186 s` with a truncated-body
  `ChunkedEncodingError`
  (`IncompleteRead(12211 bytes read, 3812 more expected)`), while
  local `/api/status` and `/api/config` both kept returning clean,
  fast `200` responses.
- The same loop still showed no fresh reboot on `.48`: local
  `/api/status.uptime_seconds` climbed steadily from `1237` to `1248`,
  `health_state` stayed `healthy`, and local `/api/config` kept
  matching `Rebooter - renamed test` throughout. Keep `BUG-054` open
  in two narrower shapes from this pass: the concrete root-body
  truncation on cycle 3 and a secondary latency watch because cycle 5
  stretched local `/` to `1.504 s` and local `/api/status` to
  `0.168 s`.

### 2026-05-14 EDT - renamed-test follow-up recheck after the `15:00Z` pass: `.48` held clean, `.207` stayed in recovery, and no fresh hub UI/API drift surfaced

Fresh live recheck run around `2026-05-14T15:09:56Z` through
`15:12:59Z` against the authenticated hub devices page/API and device
detail API plus direct local device `/`, `/api/status`, and
`/api/config` probes for `.48`, `.30`, `.225`, `.207`, and `.69`,
followed by an immediate 5-cycle local continuity loop on `.48` and
`.207`.

Concrete improved findings:
- The rendered Devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. The hub list/API both showed
  `.48` `online` on `0.1.17-dev-central` with last heartbeat
  `2026-05-14T15:12:56Z`, `.30` `online` on `0.1.17-dev-central` with
  `2026-05-14T15:12:25Z`, `.225` `online` on `0.1.17-dev-central` with
  `2026-05-14T15:12:11Z`, `.207` `online` on `0.1.16-dev-central` with
  `2026-05-14T15:12:59Z`, and `.69` `offline` with the still-stale
  `2026-05-13T22:06:13Z` heartbeat.
- `.48` stayed in a longer clean recovery window after the prior
  `15:00:54Z`-`15:02:15Z` sample. The first local root/status/config
  sweep returned clean `200` responses (`1.56 s`, `0.026 s`,
  `0.08 s`) with local `/api/status` and `/api/config` still exposing
  `Rebooter - renamed test` / `0.1.17-dev-central` at
  `uptime_seconds=1343`. The immediate 5-cycle loop then stayed fully
  clean while local `/api/status.uptime_seconds` climbed from `1423` to
  `1429`; only cycle 5 slowed the local root page to `1.163 s`, with no
  timeout, reset, or truncated-body repro.
- `.207` moved back into the improved bucket relative to the prior
  `15:00:54Z`-`15:02:15Z` masked-reboot sample. The hub row/detail still
  showed it `online` on `0.1.16-dev-central` with
  `latest_heartbeat.health_state="healthy"` and
  `uptime_seconds=726`, while the fresh local root/status/config sweep
  returned clean `200` responses (`0.19 s`, `0.029 s`, `0.091 s`) and
  local `/api/status.uptime_seconds=683`. The immediate 5-cycle loop
  then stayed fully responsive while local `/api/status.uptime_seconds`
  climbed from `762` to `767`. Cycle 1 did stretch the local root page
  to `3.475 s`, but that did not coincide with a reset or API failure.
- `.30` and `.225` also stayed operationally improved. Local
  `/api/status` reported `uptime_seconds=1792` on `.30` and `4630` on
  `.225` on the first sweep, then later re-reads reached `1903` and
  `4739`, respectively, alongside clean local root/status/config
  responses. `.225` did have another slower first local root-page read
  (`2.832 s`), but there was no fresh reboot evidence in this pass.

Concrete remaining issue:
- `BUG-053` still reproduced unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- No fresh `BUG-054`, `BUG-055`, or `BUG-056` repro surfaced in this
  pass. The only new reliability signal was mild local root-page
  latency on otherwise healthy devices: `.48` peaked at `1.56 s` on the
  initial root-page fetch and `1.163 s` in loop cycle 5, `.207` hit
  `3.475 s` on loop cycle 1 before settling below `0.27 s`, and `.225`
  took `2.832 s` on its first root-page read while `/api/status` and
  `/api/config` stayed fast.
- `.69` remains the stable offline control: hub list/API still showed
  `offline` with stale `2026-05-13T22:06:13Z`, local `/`,
  `/api/status`, and `/api/config` still timed out from this host after
  about `15 s`, while the hub detail API still exposed
  `latest_heartbeat.health_state="healthy"` and `uptime_seconds=69`
  from the same stale sample.

### 2026-05-14 EDT - renamed-test recheck after the `.30` reboot sample: `.48` held clean again, `.207` rebooted again behind a healthy hub row, and `.30`/`.225` stayed improved

Fresh live recheck run around `2026-05-14T15:00:54Z` through
`15:02:15Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 5-cycle local loop
on `.48` and `.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces showed `.48`,
  `.30`, `.225`, and `.207` `online` with fresh heartbeats, while `.69`
  stayed `offline` with its still-stale `2026-05-13T22:06:13Z`
  heartbeat.
- `.48` stayed in a clean recovery window again. The hub row/detail and
  local `/api/status` plus `/api/config` all still converged on
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`; the
  first local root/status/config sweep returned clean `200` responses
  (`0.248 s`, `0.021 s`, `0.075 s`) with local
  `/api/status.uptime_seconds=760`, and the immediate 5-cycle local
  follow-up loop then stayed fully clean while local `/api/status`
  climbed from `826` to `833`. Cycle 5 slowed the local root page to
  `1.112 s`, but there was no fresh timeout, reset, or truncated-body
  failure, so this pass added only another improved BUG-054 recovery
  sample.
- `.30` stayed operationally improved after the prior `14:51Z` reboot
  sample. The hub row/detail still showed `Erica's Subwoofer` `online`
  on `0.1.17-dev-central` with heartbeat `2026-05-14T15:01:25Z`,
  `health: healthy`, and `uptime_s` / `uptime_seconds=1204`, while the
  local root/status/config sweep returned clean `200` responses
  (`0.211 s`, `0.026 s`, `0.078 s`) with local
  `/api/status.uptime_seconds=1255`. That is uptime continuity rather
  than another fresh BUG-056 repro.
- `.225` also stayed improved. The hub row/detail still showed
  `Erica's F.R Speaker` `online` on `0.1.17-dev-central`, while the
  local root/status/config sweep returned clean `200` responses
  (`0.106 s`, `0.022 s`, `0.068 s`) with local
  `/api/status.uptime_seconds=4091`. Keep only the standing
  desired-name drift plus watch status in this pass.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-055` re-strengthened again on `.207` in another masked-reboot
  shape. The hub row/detail still showed it `online` on
  `0.1.16-dev-central` with fresh heartbeats through
  `2026-05-14T15:01:59Z`, `health: healthy`, and
  `uptime_s` / `uptime_seconds=126`, but the prior `14:51:03Z` to
  `14:53Z` pass had already reached local `/api/status.uptime_seconds=1732`
  while this pass's fresh local root/status/config sweep returned only
  `uptime_seconds=142` with clean `200` responses. The immediate
  5-cycle local loop then stayed responsive while local
  `/api/status.uptime_seconds` climbed only from `162` to `169`, so
  treat this as another fresh reboot/recovery event behind a
  healthy-looking hub row.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test soak recheck stayed clean through another short loop, with only residual root-page latency

Fresh live recheck run around `2026-05-14T13:22:34Z` through
`13:22:44Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for
`192.168.1.48`, following an earlier 10-cycle local loop in the same
window.

Concrete improved findings:
- The live hub Devices page still matched `/api/v1/admin/devices` for
  the renamed soak target. Both surfaces showed
  `Rebooter - renamed test` `online` on `0.1.17-dev-central`, with
  local IP `192.168.1.48` and last heartbeat `2026-05-14T13:21:56Z`.
- Local `/api/status` and `/api/config` still matched that same device
  identity. The initial local sweep returned clean `200` responses with
  the root page at `0.14 s`, `/api/status` at `0.02 s`, and
  `/api/config` at `0.07 s`, while local `/api/status` reported
  `health_state="healthy"` and `uptime_seconds=1307`.
- The immediate 5-cycle follow-up loop stayed fully clean. Local
  `/api/status` climbed from `uptime_seconds=1307` to `1314`, the root
  page stayed at `0.11 s` to `1.34 s`, `/api/status` stayed at
  `0.02 s` to `0.05 s`, and `/api/config` stayed at `0.07 s` to
  `0.18 s`. Combined with the earlier 10-cycle local loop that climbed
  from `1240` to `1253` without a timeout or truncated body, this is
  another concrete recovery window for the renamed soak target.

Reliability note:
- `BUG-054` stays open, but this pass only reproduced intermittent
  local root-page latency rather than a fresh reboot or response
  corruption. Earlier in the same local recheck window one root-page
  sample stretched to `4.25 s` and another to `1.40 s`, yet every
  accompanying `/api/status` and `/api/config` call still returned fast
  `200` responses and local uptime kept rising. No fresh reboot,
  connection-reset, or truncated-body repro surfaced in this pass.

### 2026-05-14 EDT - renamed-test recheck after the `12:32Z` bad window: `.48` improved cleanly, `.30` and `.207` stayed low-uptime but did not freshly regress

Fresh live recheck run around `2026-05-14T12:40:51Z` through
`12:41:54Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 5-cycle local loop
on `.48`, `.30`, and `.207` from `12:41:30Z` to `12:41:38Z`.

Concrete improved findings:
- The live hub Devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces showed `.48`,
  `.30`, `.225`, and `.207` `online` with fresh heartbeats through
  `2026-05-14T12:41:54Z`, while `.69` stayed `offline` with its still
  stale `2026-05-13T22:06:13Z` heartbeat.
- `.48` improved materially relative to the prior `12:30Z` to `12:32Z`
  root-corruption window. The hub row/API still showed
  `Rebooter - renamed test` `online` on `0.1.17-dev-central`; local
  `/api/status` and `/api/config` still matched that identity; the
  initial local root/status/config sweep returned clean `200`
  responses (`0.47 s`, `0.03 s`, `0.06 s`) with local `/api/status`
  already at `uptime_seconds=1003`; and the immediate 5-cycle loop then
  stayed fully clean with local `/api/status` climbing from
  `uptime_seconds=1050` to `1058`. This pass did not add a fresh
  `BUG-054` timeout, reboot, or truncated-body repro.
- `.225` stayed in the improved watch bucket. The hub row/API still
  showed `Erica's F.R Speaker` `online` on `0.1.17-dev-central`, while
  the local root page plus `/api/status` plus `/api/config` all
  returned clean `200` responses (`0.24 s`, `0.02 s`, `0.09 s`) and
  local `/api/status` reported `uptime_seconds=5875`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `.30` did not add a fresh `BUG-056` sample in this pass. The hub
  still showed `Erica's Subwoofer` `online` on `0.1.17-dev-central`
  with last heartbeat `2026-05-14T12:41:19Z`, while local root,
  `/api/status`, and `/api/config` all returned clean `200`
  responses (`0.18 s`, `0.02 s`, `0.07 s`) and local `/api/status`
  reported `uptime_seconds=630` on the initial sweep. The immediate
  5-cycle loop then climbed from `678` to `686` with only one slower
  root-page sample at `0.81 s`. Relative to the prior `12:30:34Z` to
  `12:32:29Z` pass where the same device had already climbed from `131`
  to `134`, this is consistent with uptime continuity rather than a new
  masked reboot.
- `.207` also did not add a fresh `BUG-055` repro in this pass. The
  hub row/API still showed `online` on `0.1.16-dev-central` with last
  heartbeat `2026-05-14T12:41:41Z`, while local `/api/status` and
  `/api/config` still exposed `Erica's ?.?. Speaker`. The initial local
  root/status/config sweep returned clean `200` responses
  (`0.83 s`, `0.04 s`, `0.07 s`) with local `/api/status` at
  `uptime_seconds=731`, and the immediate 5-cycle loop climbed from
  `777` to `785` without another stall or truncated-body failure.
  Relative to the prior `12:30:34Z` to `12:32:29Z` pass where the same
  device had already climbed from `233` to `235`, this is also
  consistent with uptime continuity rather than a fresh reboot.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test soak recheck after the masked-reboot window: `.48` stayed clean through a fresh 10-cycle loop while the standing fleet issues remained unchanged

Fresh live recheck run around `2026-05-14T12:12:34Z` through
`12:13:14Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 10-cycle local
loop on `.48`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces showed `.48`
  `online` on `0.1.17-dev-central` with last heartbeat
  `2026-05-14T12:11:46Z`, `.30` `online` on `0.1.17-dev-central` with
  `2026-05-14T12:11:11Z`, `.225` `online` on `0.1.17-dev-central` with
  `2026-05-14T12:11:54Z`, `.207` `online` on `0.1.16-dev-central` with
  `2026-05-14T12:11:21Z`, and `.69` `offline` with its still-stale
  `2026-05-13T22:06:13Z` heartbeat.
- `.48` improved materially versus the prior `12:00:37Z`-`12:01:52Z`
  clean-but-slower window. The initial local sweep returned clean
  `200` responses with the root page at about `0.29 s`, `/api/status`
  at about `0.02 s`, and `/api/config` at about `0.09 s`, while local
  `/api/status` already reported `Rebooter - renamed test` /
  `0.1.17-dev-central` at `uptime_seconds=1282`. The immediate 10-cycle
  follow-up loop from `12:12:51Z` to `12:13:02Z` then stayed fully
  clean with root-page reads about `0.10 s`-`0.19 s`, `/api/status`
  about `0.02 s`-`0.04 s`, `/api/config` about `0.03 s`-`0.10 s`, and
  local `/api/status` climbing steadily from `uptime_seconds=1330` to
  `1341`. A post-loop hub refresh at `12:13:14Z` still showed `.48`
  `online` on `0.1.17-dev-central` with last heartbeat
  `2026-05-14T12:12:46Z`.
- `.225` stayed in the improved watch bucket. The hub row/API still
  showed `Erica's F.R Speaker` `online` on `0.1.17-dev-central`, while
  the local root page plus `/api/status` plus `/api/config` all
  returned clean `200` responses (`0.41 s`, `0.02 s`, `0.10 s`) and
  local `/api/status` reported `uptime_seconds=4155`. That keeps `.225`
  above fresh-reboot level in this pass even though the standing
  desired-name drift remains.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` did not reproduce in this recheck. Treat this as another
  concrete improvement sample for the renamed soak target, not a full
  clear: the same device still produced masked reboots and local-root
  instability earlier on `2026-05-14`.
- `.30` did not add a fresh reliability regression in this pass beyond
  the standing desired-name drift. The local root page returned `200`
  in about `0.17 s`, `/api/status` returned `200` in about `0.03 s`
  with `uptime_seconds=1318`, and `/api/config` returned `200` in
  about `0.09 s`.
- `BUG-055` improved relative to the earlier `11:50:46Z`-`11:52:22Z`
  masked-reboot/stall sample. The hub row/API still showed `.207`
  `online` on `0.1.16-dev-central`, local root/status/config all
  returned clean `200` responses (`0.26 s`, `0.02 s`, `0.07 s`), and
  local `/api/status` reported `uptime_seconds=1190`, which is
  consistent with continued uptime since the prior bad window. The
  unresolved desired-name drift still remains.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck returned to uptime continuity, but the local root page still stalled

Fresh live recheck run around `2026-05-14T12:00:37Z` through
`12:01:52Z` against the authenticated hub devices page/API plus local
`.48` device `/`, `/api/status`, and `/api/config` probes, followed by
an immediate 10-cycle local loop on `192.168.1.48`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` for
  the renamed soak target. Both surfaces showed `.48` as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
  the admin API reporting last heartbeat `2026-05-14T12:00:46Z`.
- The initial local `.48` sweep returned clean `200` responses on the
  root page plus `/api/status` plus `/api/config`
  (`1.34 s`, `0.02 s`, `0.07 s`), and local `/api/status` +
  `/api/config` both still reported `Rebooter - renamed test` on
  `0.1.17-dev-central`.
- `BUG-054` did not reproduce as another masked reboot in this pass.
  The immediate 10-cycle local loop stayed fully clean, and local
  `/api/status` climbed from `uptime_seconds=654` to `671` while
  keeping `health_state="healthy"`.

Reliability notes:
- `BUG-054` still did not fully clear. Cycle 8 of the focused `.48`
  loop stretched the local root page to about `3.18 s`, and cycle 9
  still took about `1.11 s`, while local `/api/status` and
  `/api/config` stayed fast and uptime kept climbing. Treat this as a
  narrowed local-root latency repro rather than another fresh reboot
  sample.


### 2026-05-14 EDT - renamed-test recheck after the latest clean `.48` window: `.48` rebooted again, `.30` joined the masked-reboot bucket, `.207` rebooted again during the loop, and `.225` stayed improved

Fresh live recheck run around `2026-05-14T11:50:46Z` through
`11:52:22Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 5-cycle local loop
on `.48`, `.30`, `.225`, and `.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces showed `.48`,
  `.30`, `.225`, and `.207` `online` with fresh heartbeats, while `.69`
  stayed `offline` with its stale `2026-05-13T22:06:13Z` heartbeat.
- `.225` stayed in the improved watch bucket. The hub row/API still
  showed `Erica's F.R Speaker` `online` on `0.1.17-dev-central`, while
  the local root page plus `/api/status` plus `/api/config` all
  returned clean `200` responses (`0.42 s`, `0.03 s`, `0.04 s`) and
  local `/api/status` climbed from `uptime_seconds=2861` on the initial
  sweep to `2966` by the end of the focused loop. That keeps `.225`
  below fresh-reboot level in this pass even though the standing
  desired-name drift remains.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` strengthened again on the renamed soak target in another
  reboot-through-healthy-row shape. The initial local `.48` sweep still
  looked healthy, with root/status/config all returning `200`
  (`1.02 s`, `0.02 s`, `0.08 s`) and local `/api/status` reporting
  `Rebooter - renamed test` / `0.1.17-dev-central` at
  `uptime_seconds=593`, while the live hub still showed `.48` `online`
  with last heartbeat `2026-05-14T11:50:46Z`. But the immediate 5-cycle
  follow-up loop starting at `11:51:54Z` already found local
  `/api/status` reset to `uptime_seconds=74`, then climb only to `90`
  by cycle 5. The hub devices page/API still showed the device
  `online` with a fresh `2026-05-14T11:51:46Z` heartbeat after the
  loop, so the reboot window remained masked centrally even though the
  local device had restarted again.
- `.30` produced fresh masked-reboot evidence for the first time in
  this soak series. The hub devices page/API showed
  `Erica's Subwoofer` `online` on `0.1.17-dev-central` with last
  heartbeat `2026-05-14T11:51:11Z`, while the initial local sweep
  returned clean `200` responses and local `/api/status` reported only
  `uptime_seconds=23`. The immediate 5-cycle loop then climbed from
  `111` to `129`, with one slower local root-page read at about
  `3.50 s` on cycle 5. Treat that as concrete reboot/recovery evidence
  behind a healthy-looking hub row rather than just the standing
  desired-name drift.
- `BUG-055` strengthened again in both reboot and local-root-stall
  form. The initial local `.207` sweep returned clean `200` responses
  (`0.26 s`, `0.02 s`, `0.07 s`) while local `/api/status` reported
  only `uptime_seconds=143` despite the hub row/API already showing
  `.207` `online` on `0.1.16-dev-central` with last heartbeat
  `2026-05-14T11:50:12Z`. The immediate 5-cycle loop first climbed from
  `231` to `242`, then cycle 5 hit a `10.49 s` local root-page stall
  while `/api/status` dropped to `uptime_seconds=11` with
  `health_state="unknown"`. A post-loop hub refresh still showed `.207`
  `online` with last heartbeat `2026-05-14T11:52:22Z`, so the reboot
  and recovery stayed masked by the central row again.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with another fresh `.48` reboot behind a healthy hub row

Fresh live recheck run around `2026-05-14T11:42:29Z` through
`11:43:06Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 5-cycle local loop
on `.48`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces showed `.48`
  `online` on `0.1.17-dev-central` with last heartbeat
  `2026-05-14T11:41:41Z`, `.30` `online` on `0.1.17-dev-central` with
  `2026-05-14T11:42:02Z`, `.225` `online` on `0.1.17-dev-central` with
  `2026-05-14T11:41:54Z`, `.207` `online` on `0.1.16-dev-central` with
  `2026-05-14T11:42:06Z`, and `.69` `offline` with its still-stale
  `2026-05-13T22:06:13Z` heartbeat.
- `.207` improved materially relative to the earlier reboot-watch
  windows. The local root page plus `/api/status` plus `/api/config`
  all returned clean `200` responses (`0.13 s`, `0.02 s`, `0.08 s`),
  and local `/api/status` reported `uptime_seconds=1469`, which is
  consistent with uptime continuity since the prior `11:31Z` sample.
- `.225` also stayed improved. The local root page plus `/api/status`
  plus `/api/config` all returned clean `200` responses (`0.30 s`,
  `0.02 s`, `0.06 s`), and local `/api/status` reported
  `uptime_seconds=2381`, so this pass did not add a fresh reboot signal.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` strengthened again on the renamed soak target. The live hub
  devices page and `/api/v1/admin/devices` still showed `.48`
  `online` on `0.1.17-dev-central` with last heartbeat
  `2026-05-14T11:41:41Z`, and the local root page plus `/api/status`
  plus `/api/config` all returned clean `200` responses
  (`0.24 s`, `0.02 s`, `0.09 s`). But local `/api/status` reported only
  `uptime_seconds=114`, which is fresh reboot evidence relative to the
  prior `11:31:57Z` to `11:32:09Z` clean loop where `.48` had already
  climbed to `uptime_seconds=1121`-`1131`. The immediate 5-cycle local
  follow-up loop then stayed fully clean while local `/api/status`
  climbed only from `145` to `150`. Treat this as another concrete
  reboot-through-healthy-row sample even though the local surfaces had
  already recovered by the time of the focused loop.
- `.30` showed no fresh regression beyond the standing desired-name
  drift. The local root page returned `200` in about `0.17 s`,
  `/api/status` returned `200` in about `0.02 s` with
  `uptime_seconds=7475`, and `/api/config` returned `200` in about
  `0.08 s`.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with another clean `.48` soak window and improved `.207` uptime continuity

Fresh live recheck run around `2026-05-14T11:31:18Z` through
`11:32:30Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 5-cycle local loop
on `.48` from `2026-05-14T11:31:57Z` through `11:32:09Z`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. The rendered rows and the API
  both showed `.48` `online` on `0.1.17-dev-central` with last
  heartbeat `2026-05-14T11:32:22Z`, `.30` `online` on
  `0.1.17-dev-central` with `2026-05-14T11:32:02Z`, `.225` `online` on
  `0.1.17-dev-central` with `2026-05-14T11:31:54Z`, `.207` `online` on
  `0.1.16-dev-central` with `2026-05-14T11:32:06Z`, and `.69`
  `offline` with its still-stale `2026-05-13T22:06:13Z` heartbeat.
- `.48` improved again in a more meaningful soak shape than the prior
  short clean window. The initial local root/status/config sweep
  returned `200` (`0.39 s`, `0.04 s`, `0.24 s`) with local
  `/api/status` + `/api/config` still showing
  `Rebooter - renamed test` / `0.1.17-dev-central` and
  `uptime_seconds=1154`. The immediate 5-cycle local follow-up loop
  then stayed fully clean with root-page reads about `0.10 s`-`1.42 s`,
  `/api/status` about `0.02 s`, `/api/config` about `0.07 s`-`0.08 s`,
  and local `/api/status` climbing steadily from
  `uptime_seconds=1121` to `1131`.
- `.207` improved materially relative to the earlier fresh-reboot
  interpretation. The hub row/API still showed `online` on
  `0.1.16-dev-central`, while the local root page plus `/api/status`
  plus `/api/config` all returned clean `200` responses
  (`0.13 s`, `0.02 s`, `0.07 s`) and local `/api/status` reported
  `uptime_seconds=873`. That uptime is consistent with continued
  survival since the prior `11:20Z` pass's `uptime_seconds=194`, so
  there was no fresh reboot signal in this recheck.
- `.225` also stayed improved overall. The hub row/API still showed
  `Erica's F.R Speaker` `online` on `0.1.17-dev-central`, while local
  `/api/status` and `/api/config` still exposed the standing desired-
  name drift as `Rebooter`. The first local root-page read stretched to
  about `2.97 s`, but the confirming local sweep returned `200` in
  about `0.16 s`, local `/api/status` returned `200` in about `0.02 s`
  with `uptime_seconds=1785`, and local `/api/config` returned `200` in
  about `0.07 s`. That is a slower first hit, not fresh reboot
  evidence.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` did not reproduce again in this pass. Keep it open because
  the earlier reboot/recovery plus local-surface failures on the same
  date remain concrete, but this was another clean follow-up window on
  the renamed soak target rather than a new regression.
- `.30` still showed no bug-level change beyond the standing desired-
  name drift. The local root page stretched to about `2.37 s`, but
  `/api/status` and `/api/config` stayed fast (`0.04 s`, `0.08 s`) and
  local `/api/status` reported `uptime_seconds=6879`, so this is not a
  repeatable reliability regression yet.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck regressed again; `.48` rebooted inside the loop, `.225` rebooted again, and `.207` stayed improved

Fresh live recheck run around `2026-05-14T11:03Z` through `11:05Z`
against the authenticated hub Devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by an immediate 5-cycle local loop on
`.48`, `.225`, and `.207`, a confirming local `/api/status` read for
those three devices, and a final hub Devices page/API refresh.

Concrete improved findings:
- The live hub Devices page still matched `/api/v1/admin/devices` on
  every comparison target in both the initial and post-loop refreshes.
  There was still no fresh central-side UI/API drift.
- `.207` improved materially versus the earlier reboot-masking pass.
  The hub stayed `online` on `0.1.16-dev-central` while the local root
  page returned `200` at about `0.49 s` on the initial sweep and then
  about `0.79 s`, `0.11 s`, `0.12 s`, `0.16 s`, and `0.15 s` across
  the follow-up loop. Local `/api/status` held steady around
  `uptime_seconds=1371` and then `1449`-`1450` with no timeout or
  truncated-body repro in this window.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` strengthened again on the renamed soak target. The initial
  `.48` sweep first looked clean and converged as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with the
  root page at about `0.19 s`, `/api/status` at about `0.02 s`,
  `/api/config` at about `0.08 s`, and local `/api/status` reporting
  `uptime_seconds=1607`. But the immediate 5-cycle follow-up loop then
  hit full `10 s` read timeouts on `/`, `/api/status`, and `/api/config`
  in cycle 1, another full `10 s` root-page timeout in cycle 2, and a
  `ChunkedEncodingError` on the root page in cycle 4
  (`IncompleteRead(13283 bytes read, 2740 more expected)`). The local
  `/api/status` samples that did return had already fallen to
  `uptime_seconds=10`, `10`, `14`, and `14`, and the confirming read
  reached only `uptime_seconds=17` with `health_state="unknown"`. The
  post-loop hub Devices page/API refresh still showed `.48` `online` on
  `0.1.17-dev-central` with last heartbeat `2026-05-14T11:04:35Z`, so
  the hub again masked the reboot/recovery plus local-UI corruption
  window.
- `.225` also regressed again as a fresh reboot watch item. The initial
  local sweep still returned clean `200` responses, but local
  `/api/status` already showed only `uptime_seconds=37` while the hub
  Devices page/API still showed `.225` `online` on
  `0.1.17-dev-central`. The immediate 5-cycle loop then stayed clean
  and climbed only from `114` to `115`, with a confirming later
  `/api/status` read at `117`, so this device restarted again shortly
  before the pass even though the hub row never exposed a degraded
  state.
- `.30` showed no fresh regression beyond the standing name drift. The
  local root page returned `200` in about `0.26 s`, `/api/status`
  returned `200` in about `0.03 s` with `uptime_seconds=5131`, and
  `/api/config` returned `200` in about `0.07 s`.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with fresh `.48` and `.207` reboot windows, but no new hub UI/API drift

Fresh live recheck run around `2026-05-14T11:12:59Z` through `11:14:42Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by an immediate 5-cycle local loop on
`.48`, `.225`, and `.207` plus a post-loop hub refresh.

Concrete improved findings:
- The live hub Devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- The hub was more honest about the two active reliability events in
  this window than in the earlier masked-online passes. On the initial
  sweep, both the rendered Devices page and `/api/v1/admin/devices`
  showed `.48` `offline` with last heartbeat `2026-05-14T11:09:35Z`
  and `.207` `offline` with last heartbeat `2026-05-14T11:07:41Z`.
- `.225` improved materially relative to the prior reboot-watch runs.
  The hub Devices page/API stayed `online` on `0.1.17-dev-central`,
  local root/status/config all returned `200`, local `/api/status`
  reported `uptime_seconds=637`, and the immediate 5-cycle follow-up
  loop stayed clean.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` strengthened again on the renamed soak target, but in a
  slightly different shape than the prior masked-online windows. The
  initial `.48` sweep found the hub already showing `offline`, while
  local root still returned `200` only after about `4.73 s` and local
  `/api/status` plus `/api/config` both timed out at the full `10 s`.
  The immediate 5-cycle local loop then recovered fully with root-page
  reads about `0.11 s`-`0.19 s`, fast `200` status/config responses,
  and local `/api/status` reporting
  `Rebooter - renamed test` / `0.1.17-dev-central` with
  `uptime_seconds=63`-`64`. A post-loop hub refresh by
  `2026-05-14T11:14:42Z` had already moved `.48` back to `online` with
  last heartbeat `2026-05-14T11:14:22Z`. Treat that as fresh reboot /
  recovery evidence plus another transient local-surface stall.
- `BUG-055` also strengthened with a fresh reboot / recovery window on
  `.207`. The initial sweep found hub UI/API `offline`, local root
  timed out after about `10 s`, local `/api/status` reset the
  connection after about `9.76 s`, and local `/api/config` was the only
  surface that returned `200`, taking about `3.68 s` while still
  exposing `Erica's ?.?. Speaker`. The immediate 5-cycle loop then
  recovered cleanly with root-page reads about `0.10 s`-`0.15 s`,
  fast `200` status/config responses, and local `/api/status` holding
  around `uptime_seconds=49`-`50`. A post-loop hub refresh had already
  flipped `.207` back to `online` with last heartbeat
  `2026-05-14T11:13:45Z`.
- `.30` showed no fresh regression beyond the standing desired-name
  drift. Local root returned `200` in about `0.18 s`, `/api/status`
  returned `200` in about `0.02 s` with `uptime_seconds=5730`, and
  `/api/config` returned `200` in about `0.07 s`.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck addendum: no fresh hub drift, `.48` stayed up through the short loop, and `.225`/`.207` advanced uptime cleanly

Fresh live recheck run around `2026-05-14T10:49Z` through `10:52Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by an immediate 5-cycle local loop on
`.48`, `.225`, and `.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. The rendered rows and the API
  both showed `.48`, `.30`, `.225`, and `.207` `online`, while `.69`
  stayed `offline`.
- `.48` stayed converged across hub UI/API and local status/config as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`. The
  initial local sweep returned `200` with the root page at about
  `0.16 s`, `/api/status` at about `0.03 s`, and `/api/config` at
  about `0.08 s`. The immediate 5-cycle follow-up loop kept returning
  `200`; after one slower first root-page sample at about `2.20 s`,
  later root-page reads fell back to about `0.12 s`-`0.15 s` while
  `/api/status` stayed about `0.02 s` and `/api/config` about
  `0.07 s`-`0.10 s`.
- `.225` improved relative to the prior reboot-watch state. The hub
  devices page/API still showed `Erica's F.R Speaker` `online` on
  `0.1.17-dev-central`, local `/api/status` + `/api/config` still
  exposed `Rebooter`, and the initial local sweep returned `200` with
  the root page at about `0.27 s`, `/api/status` at about `0.03 s`,
  and `/api/config` at about `0.08 s`. The immediate 5-cycle follow-up
  loop then stayed clean with root-page reads about `0.11 s`-`0.26 s`,
  `/api/status` about `0.02 s`-`0.06 s`, and `/api/config` about
  `0.06 s`-`0.09 s`.
- `.207` also improved out of the earlier reboot/recovery window. The
  hub devices page/API still showed `Erica's R.R. Speaker` `online` on
  `0.1.16-dev-central` with the pending-upgrade affordance, while local
  `/api/status` + `/api/config` still exposed `Erica's ?.?. Speaker`.
  The initial local sweep returned `200` with the root page at about
  `0.30 s`, `/api/status` at about `0.04 s`, and `/api/config` at
  about `0.06 s`; the immediate 5-cycle loop then stayed clean with
  root-page reads about `0.10 s`-`0.20 s`, `/api/status` about
  `0.02 s`-`0.04 s`, and `/api/config` about `0.07 s`-`0.08 s`.
- `.30` showed no fresh regression beyond the standing desired-name
  drift. The local root page returned `200` in about `0.23 s`,
  `/api/status` returned `200` in about `0.02 s` with
  `uptime_seconds=4351`, and `/api/config` returned `200` in about
  `0.07 s`.
- `.69` remained the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

Reliability notes:
- `BUG-054` improved materially in this pass but does not clear yet.
  The renamed soak target no longer showed a fresh reboot or truncated
  response in this window: local `/api/status` reported
  `uptime_seconds=828` on the initial sweep and then climbed from `877`
  to `884` across the immediate loop. Keep the bug open because the
  first loop root-page read still stretched to about `2.20 s`, but the
  earlier timeout/truncated-body shapes did not reproduce.
- `.225` did not show a fresh reboot in this pass. Local
  `/api/status` reported `uptime_seconds=682` on the initial sweep and
  then climbed from `731` to `739` across the immediate loop, so the
  earlier reboot evidence improved into a short clean watch window.
- `BUG-055` also improved materially in this pass. Local
  `/api/status` reported `uptime_seconds=591` on the initial sweep and
  then climbed from `640` to `647` across the immediate loop, with no
  repeated timeout or root-page corruption. Keep the bug open because
  the unresolved hub-vs-local desired-name drift remained and earlier
  reboot/recovery masking is still concrete.
- `BUG-053` remained unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

### 2026-05-14 EDT - renamed-test recheck with fresh `.48` reboot/offline drop, fresh `.207` reboot, and sharper `.225` hub-vs-local mismatch

Fresh live recheck run around `2026-05-14T10:30Z` through `10:36Z`
against the authenticated hub Devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by an immediate 5-cycle local loop on `.48`
and `.207` and a final hub/API confirmation snapshot.

Concrete regressions / reliability issues:
- `BUG-054` strengthened materially on the renamed soak target. In the
  first `10:30Z` sweep, hub UI/API still showed `.48` as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central` while
  local `/api/status` already reported `uptime_seconds=16`, which is
  fresh reboot evidence. The immediate 5-cycle local follow-up loop
  then stayed clean with root-page reads about `0.11 s`-`0.25 s`,
  `/api/status` about `0.02 s`-`0.03 s`, and uptime climbing from
  `105` to `110`. But by the final confirming probe a few minutes later
  local `/`, `/api/status`, and `/api/config` were all back to full
  `10 s` read timeouts, and the rendered hub Devices page plus
  `/api/v1/admin/devices` had flipped the device to `offline` with last
  heartbeat `2026-05-14T10:33:02Z`. Treat this as a concrete
  reboot/recovery-then-drop sequence, not just a one-off slow surface.
- `BUG-055` also re-strengthened into a fresh reboot/recovery window on
  `.207`. In the first `10:30Z` sweep, hub UI/API still showed
  `Erica's R.R. Speaker` `online` on `0.1.16-dev-central`, but local
  `/`, `/api/status`, and `/api/config` all hit full `10 s` read
  timeouts. The immediate 5-cycle loop then recovered cleanly with root
  reads about `0.18 s`-`1.60 s`, `/api/status` about `0.02 s`-`0.04 s`,
  and uptime climbing from `17` to `24`, which is concrete evidence of
  a fresh reboot. A later confirming local `/api/status` read still
  showed only `uptime_seconds=81` and took about `1.39 s`, while the
  hub still presented the device as `online` with last heartbeat
  `2026-05-14T10:36:34Z`.
- `.225` escalated from the earlier watch bucket into a sharper
  hub-vs-local reachability mismatch. In the first `10:30Z` sweep, hub
  API still showed `Erica's F.R Speaker` `online` on
  `0.1.17-dev-central`, while local `/` took about `7.81 s` and both
  local `/api/status` and `/api/config` timed out after about `10 s`.
  By the final confirmation window the local device had recovered
  cleanly (`/` about `0.19 s`, `/api/status` about `0.04 s`,
  `/api/config` about `0.08 s`, `uptime_seconds=8095`), but the hub
  Devices page/API had already flipped the row to `offline` with last
  heartbeat `2026-05-14T10:31:58Z`. That is concrete central-vs-local
  state drift rather than a clean offline transition.

Concrete improved finding:
- `.30` stayed unchanged and healthy in this pass. Hub state remained
  `online` on `0.1.17-dev-central`, and the local device still answered
  quickly with the pre-existing `BUG-053` name drift only
  (`Erica's Subwoofer` in hub vs local `Rebooter`).

### 2026-05-14 EDT - renamed-test recheck with clean `.48` and `.207` follow-up plus unchanged name drift

Fresh live recheck run around `2026-05-14T10:20Z` through `10:21Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by immediate 5-cycle local loops on `.48`
and `.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` did not reproduce in this short confirmation window. The
  initial `.48` local sweep returned `200` with the root page at about
  `0.35 s`, `/api/status` about `0.02 s`, and `/api/config` about
  `0.07 s`; the immediate 5-cycle follow-up loop then stayed clean with
  root-page reads about `0.11 s`-`1.35 s`, `/api/status` about
  `0.02 s`-`0.05 s`, and `/api/config` about `0.06 s`-`0.07 s`.
- The recent `.48` reboot/recovery evidence also improved materially in
  this pass. Local `/api/status` reported `uptime_seconds=1263` in the
  initial sweep and then climbed monotonically from `1331` to `1336`
  across the immediate loop, so there was no fresh short-uptime signal.
- `.207` also improved relative to the earlier truncated-body and
  reboot-watch windows. The initial local sweep returned `200` with the
  root page at about `0.27 s`, `/api/status` about `0.05 s`,
  `/api/config` about `0.07 s`, and local `/api/status` reported
  `uptime_seconds=922`; the immediate 5-cycle follow-up loop then stayed
  clean with root-page reads about `0.13 s`-`1.59 s`, `/api/status`
  about `0.02 s`-`0.08 s`, `/api/config` about `0.07 s`-`0.10 s`, and
  uptime climbing from `997` to `1002`.
- `.30` and `.225` both stayed locally healthy in this pass. `.30`
  returned `200` with the root page at about `0.16 s`, `/api/status`
  about `0.02 s`, and `/api/config` about `0.08 s`; `.225` returned
  `200` with the root page at about `0.32 s`, `/api/status` about
  `0.03 s`, and `/api/config` about `0.07 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `.207` still showed the pending upgrade state in the hub row toward
  `0.1.17-dev-central` while the device itself remained on
  `0.1.16-dev-central`, but no fresh `.207` failure or reboot signal
  surfaced in this pass.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with fresh `.48` truncated root response without reboot and renewed `.207` reboot evidence

Fresh live recheck run around `2026-05-14T10:10Z` through `10:13Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by immediate 5-cycle local loops on `.48`,
`.207`, and `.225`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `.48` no longer looked like a just-recovered reboot target in this
  pass. Local `/api/status` reported `uptime_seconds=711` on the first
  sweep and then `798`-`807` across the focused loop.
- `.30` stayed locally healthy despite the ongoing name drift: the root
  page returned `200` in about `0.20 s`, `/api/status` in about
  `0.02 s` with `uptime_seconds=2013`, and `/api/config` in about
  `0.08 s`.
- `.225` stayed locally healthy overall. The first root-page read
  stretched to about `2.24 s`, but the immediate 5-cycle follow-up loop
  stayed at `200` with one slower first hit at about `1.22 s` and later
  root-page reads back around `0.12 s`-`0.22 s` while `/api/status` and
  `/api/config` stayed fast.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` strengthened in a more important shape than the prior
  reboot-window evidence. The live hub devices page and
  `/api/v1/admin/devices` still showed `.48` converged as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, and
  local `/api/status` uptime stayed monotonic from `798` to `807`
  through the focused loop, but cycle 4 of that same loop hit a
  truncated-body `ChunkedEncodingError` on the root page after about
  `4.42 s` (`14891 bytes read, 1132 more expected`) while local
  `/api/status` and `/api/config` still returned fast `200` responses.
  Treat this as concrete evidence that the renamed soak target still has
  a local UI response-integrity failure even outside an immediate
  reboot/recovery window.
- `BUG-055` did not reproduce as a truncated-body failure in this pass,
  but `.207` now shows fresh reboot evidence after the prior
  `2026-05-14T10:01Z`-`10:03Z` pass. The live hub devices page still
  showed `.207` `online` on `0.1.16-dev-central` with the pending
  upgrade affordance, while local `/api/status` reported
  `uptime_seconds=372` on the first sweep and then `467`-`473` across
  the immediate loop. That means `.207` rebooted sometime after the
  earlier pass even though the hub never exposed a degraded state.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with improved `.48` first-hit recovery and calmer `.225` / `.207`

Fresh live recheck run around `2026-05-14T08:31Z` through `08:33Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48`,
`.207`, and `.225`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` improved relative to the earlier timeout and slower
  `/api/config` windows. The initial `.48` local sweep returned `200`
  with the root page at about `2.05 s`, `/api/status` about `0.04 s`,
  and `/api/config` about `0.07 s`; the immediate 5-cycle follow-up
  loop then stayed clean with root-page reads about `0.10 s`-`0.64 s`,
  `/api/status` about `0.02 s`-`0.04 s`, and `/api/config` about
  `0.07 s`-`0.09 s`.
- `.225` improved materially relative to the prior `8.12 s` root-page
  / `9.55 s` `/api/status` watch window. The initial local sweep
  returned `200` with the root page at about `0.20 s`, `/api/status`
  about `0.02 s`, and `/api/config` about `0.07 s`; the immediate
  5-cycle follow-up loop then stayed clean with root-page reads about
  `0.10 s`-`0.19 s`, `/api/status` about `0.02 s`-`0.05 s`, and
  `/api/config` about `0.02 s`-`0.08 s`.
- `.207` also improved relative to the earlier truncated-body,
  root-timeout, and `1.38 s` `/api/status` wobble windows. The initial
  local sweep returned `200` with the root page at about `0.18 s`,
  `/api/status` about `0.02 s`, and `/api/config` about `0.09 s`; the
  immediate 5-cycle follow-up loop then stayed clean with root-page
  reads about `0.10 s`-`0.19 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.06 s`-`0.08 s`.
- `.30` stayed locally healthy despite the ongoing name drift. The root
  page returned `200` in about `0.17 s`, `/api/status` in about
  `0.02 s`, and `/api/config` in about `0.08 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` did not fully clear. The first `.48` local root-page read
  still stretched to about `2.05 s` before the immediate follow-up loop
  returned to sub-second behavior across `/`, `/api/status`, and
  `/api/config`. That is materially better than the earlier full `10 s`
  timeout windows, but it is still a repeatable first-hit recovery
  wobble.
- `.207` still showed the pending upgrade affordance in the hub row
  toward `0.1.17-dev-central` while the device itself remained healthy
  on `0.1.16-dev-central`; this matched the hub API and did not produce
  a fresh local failure in this pass.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with renewed `.48` full timeout, transient central recovery wobble, and improved `.207`

Fresh live recheck run around `2026-05-14T08:10Z` through `08:13Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48`,
`.207`, and `.225`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  the rendered recheck for all comparison targets in this pass, so
  there was still no fresh UI-vs-API drift inside the hub itself.
- `.48` re-converged across hub UI/API and local status/config once the
  recovery completed: `Rebooter - renamed test` / `online` /
  `0.1.17-dev-central`.
- `.207` improved relative to the prior truncated-body and
  `1.24 s`-`1.59 s` wobble window. The initial local sweep returned
  `200` with the root page at about `0.34 s`, `/api/status` about
  `0.03 s`, and `/api/config` about `0.09 s`; the immediate 5-cycle
  follow-up loop then hit one slower root-page read at about `1.46 s`
  on cycle 1 before cycles 2-5 returned to about `0.10 s`-`0.13 s`
  with the JSON endpoints staying fast.
- `.225` improved after an apparently short recovery window. By the
  focused 5-cycle follow-up loop, the root page had returned to about
  `0.10 s`-`0.18 s`, `/api/status` to about `0.02 s`, and
  `/api/config` to about `0.07 s`-`0.08 s`.
- `.30` stayed locally healthy despite the ongoing name drift. The root
  page returned `200` in about `0.14 s`, `/api/status` in about
  `0.02 s`, and `/api/config` in about `0.08 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` re-strengthened again in this pass. The first `.48` local
  sweep timed out for the full `10 s` window on `/`, `/api/status`, and
  `/api/config`, while the first hub admin API sample still held the row
  at `offline` with `last_heartbeat_at` `2026-05-14T08:05:17Z`. The
  immediate 5-cycle follow-up loop then recovered cleanly, with the
  root page at about `0.10 s`-`0.16 s`, `/api/status` about
  `0.02 s`-`0.04 s`, and `/api/config` about `0.07 s`-`0.09 s`; a
  second hub UI/API fetch around `08:12Z` had already converged back to
  `online`. Treat this as renewed evidence of first-contact local
  outage/recovery behavior on the renamed soak target rather than a
  cleared issue.
- `.225` also showed a short recovery wobble before settling. In the
  initial sweep, the hub admin API sample still held the device at
  `offline` with `last_heartbeat_at` `2026-05-14T08:01:06Z`, while the
  local root page took about `6.04 s`, local `/api/status` still
  returned `200` in about `0.02 s` with `central_state="idle"` and
  `central_heartbeat_age_seconds=3`, and local `/api/config` returned
  `200` in about `0.08 s`. The immediate 5-cycle follow-up loop then
  stayed clean and the later hub UI/API recheck had already converged
  back to `online`. Keep this below fresh-bug level for now, but it is
  stronger watch evidence than the earlier mild `.225` windows.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with cleaner `.48` and `.225`, plus weaker non-repeating `.207` wobble

Fresh live recheck run around `2026-05-14T08:00Z` through `08:01Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by focused 5-cycle follow-up loops on
`.48`, `.207`, and `.225`, plus an extra 8-cycle confirmation loop on
`.207` through about `08:04Z`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` improved again relative to the earlier timeout and
  `1.54 s` follow-up windows. The initial `.48` local sweep returned
  `200` with the root page at about `0.21 s`, `/api/status` about
  `0.02 s`, and `/api/config` about `0.08 s`; the immediate 5-cycle
  follow-up loop then stayed clean with root-page reads about
  `0.10 s`-`0.22 s`, `/api/status` about `0.02 s`-`0.04 s`, and
  `/api/config` about `0.07 s`-`0.09 s`.
- `.225` improved versus the prior repeated `/api/status` watch window.
  The initial local sweep returned `200` with the root page at about
  `0.31 s`, `/api/status` about `0.05 s`, and `/api/config` about
  `0.09 s`; the immediate 5-cycle follow-up loop then stayed clean with
  the root page about `0.10 s`-`0.15 s`, `/api/status` about
  `0.02 s`-`0.03 s`, and `/api/config` about `0.07 s`-`0.08 s`.
- `.30` stayed locally healthy despite the ongoing name drift. The root
  page returned `200` in about `0.11 s`, `/api/status` in about
  `0.03 s`, and `/api/config` in about `0.08 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-055` improved versus the prior truncated-body failure and
  `.207` did not re-strengthen in the extended confirmation loop, but
  it also did not fully disappear. The initial `.207` local sweep
  returned `200` with the root page at about `0.33 s`, `/api/status`
  about `0.04 s`, and `/api/config` about `0.05 s`; the immediate
  5-cycle follow-up loop then hit one slower root-page read at about
  `1.24 s` and one separate slower `/api/config` read at about
  `1.59 s` before later cycles returned to normal. An immediate 8-cycle
  confirmation loop then stayed clean with root-page reads about
  `0.11 s`-`0.21 s`, `/api/status` about `0.02 s`-`0.05 s`, and
  `/api/config` about `0.04 s`-`0.08 s`. Treat this as weaker,
  non-repeating local control-plane wobble rather than a fresh
  regression.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with clean post-06:40Z window

Fresh live recheck run around `2026-05-14T06:40Z` through `06:42Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48` and
`.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` did not reproduce in this window. The initial `.48` local
  sweep plus the immediate 5-cycle follow-up loop all returned `200`,
  with root-page reads about `0.10 s`-`0.37 s`, `/api/status` about
  `0.02 s`-`0.03 s`, and `/api/config` about `0.03 s`-`0.09 s`.
- `BUG-055` also did not reproduce in this pass. `.207` returned `200`
  in the initial local sweep and the follow-up 5-cycle loop kept
  root-page reads about `0.11 s`-`0.20 s`, `/api/status` about
  `0.02 s`-`0.04 s`, and `/api/config` about `0.06 s`-`0.09 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `.225` had one slower initial local root-page fetch at about
  `1.41 s` while `/api/status` and `/api/config` stayed fast. This is
  not enough to call a fresh regression on its own, but it is worth
  watching if future soak passes show the same first-hit pattern on a
  second device.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test longer-loop recheck

Longer recheck run around `2026-05-14T05:00Z` through `05:01Z` against
the live hub devices page/API plus local device `/`, `/api/status`, and
`/api/config` surfaces for `.48`, `.30`, `.225`, `.207`, and `.69`.

Concrete improved findings:
- `.48` remained fully converged on this pass: the live hub devices page
  and admin API both showed `Rebooter - renamed test`, `online`, and
  firmware `0.1.17-dev-central`, while local `/api/status` and
  `/api/config` returned the same renamed identity.
- `BUG-054` did not reproduce in a longer 10-cycle `.48` loop. Every
  cycle returned `200`, with local root-page reads between about
  `0.11 s` and `0.39 s`, local `/api/status` between `0.02 s` and
  `0.04 s`, and local `/api/config` between `0.07 s` and `0.09 s`.
- The live hub UI and hub API matched on all comparison targets in this
  pass, so there was no fresh UI-vs-API drift on the central side.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability note:
- `.207` still appears in the live hub UI/API as `online` on
  `0.1.16-dev-central`, and the hub devices page still offers the
  one-click upgrade path to `0.1.17-dev-central`. Local `/api/status`
  stayed healthy in this pass (`central_state="idle"`).
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test first-contact regression recheck

Fresh live recheck run around `2026-05-14T05:11Z` through `05:13Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`.

Concrete improved findings:
- The live hub devices page and hub admin API still matched on all
  comparison targets in this pass. There was no fresh central-side
  UI-vs-API drift.
- `.48` stayed converged on identity and firmware once it answered
  locally: hub UI/API, local `/api/status`, and local `/api/config`
  all returned `Rebooter - renamed test` on
  `0.1.17-dev-central`.
- `.48` recovered cleanly in the immediate follow-up 5-cycle loop after
  the first failed pass. Cycles 1-5 all returned `200`; local `/`
  stayed about `0.10 s`-`0.48 s`, `/api/status` about `0.02 s`-`0.03 s`,
  and `/api/config` about `0.07 s`-`0.08 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability note:
- `BUG-054` reproduced again at the start of this run in a stronger
  first-contact shape than the prior `05:00Z` loop. Around
  `2026-05-14T05:11Z`, `.48` timed out on local `/`, `/api/status`, and
  `/api/config` for the full 10 s timeout window while the hub UI/API
  still showed the device `online`. A recheck about two minutes later
  recovered to `200`s, but the first successful local root-page fetch
  was still slow at about `1.9 s`.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test post-timeout narrowing recheck

Fresh live recheck run around `2026-05-14T05:20Z` through `05:22Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by a 10-cycle local `.48` loop.

Concrete improved findings:
- The live hub devices page and hub admin API still matched on all
  comparison targets in this pass. There was no fresh central-side
  UI-vs-API drift.
- `.48` stayed converged on identity and firmware across the hub UI,
  hub API, local `/api/status`, and local `/api/config`:
  `Rebooter - renamed test` on `0.1.17-dev-central`.
- The stronger hard-timeout shape of `BUG-054` did not reproduce in
  this window. The immediate local sweep on `.48` returned `200` from
  `/`, `/api/status`, and `/api/config`, and the follow-up 10-cycle
  loop also stayed at `200` throughout.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability note:
- `BUG-054` is narrower again, but not closed. In the 10-cycle `.48`
  loop after the successful first pass, nine root-page reads stayed
  around `0.14 s`-`0.54 s`, while cycle 10 stretched to about
  `2.16 s`; `/api/status` and `/api/config` remained fast in the same
  loop. Treat this as intermittent local root-page latency rather than
  a cleared stability issue.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test cross-check with `.207` UI latency follow-up

Fresh live recheck run around `2026-05-14T05:30Z` through `05:32Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by a 5-cycle local loop on `.48` and
`.207`.

Concrete improved findings:
- The live hub devices page and hub admin API still matched on all
  comparison targets in this pass. There was no fresh central-side
  UI-vs-API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- The stronger hard-timeout shape of `BUG-054` did not reproduce in
  this window. In the 5-cycle `.48` follow-up loop, `/`,
  `/api/status`, and `/api/config` all returned `200`; local root-page
  reads ran about `0.14 s`-`0.94 s`, `/api/status` about
  `0.02 s`-`0.03 s`, and `/api/config` about `0.07 s`-`0.09 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `.207` now has a concrete local UI responsiveness issue separate from
  `BUG-054`. In the initial sweep, local `/` took about `6.29 s` while
  the live hub still showed the device `online` on
  `0.1.16-dev-central`; in the immediate 5-cycle follow-up loop, local
  `/` stretched again to about `2.93 s` on cycle 2 while
  `/api/status` stayed about `0.02 s`-`0.03 s` and `/api/config`
  stayed about `0.07 s`-`0.08 s` throughout. Logged as `BUG-055`.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck after `.207` stall report

Fresh live recheck run around `2026-05-14T05:40Z` through `05:42Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48` and
`.207`.

Concrete improved findings:
- The live hub devices page and hub admin API still matched on all
  comparison targets in this pass. There was no fresh central-side
  UI-vs-API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` did not reproduce in this window. The initial `.48` local
  sweep plus the follow-up 5-cycle loop both returned `200` from `/`,
  `/api/status`, and `/api/config`; local root-page reads stayed about
  `0.10 s`-`0.18 s`, `/api/status` about `0.02 s`, and `/api/config`
  about `0.07 s`-`0.09 s`.
- `BUG-055` also did not reproduce in this pass. `.207` returned
  `200` on the initial local root/UI/API sweep, and the follow-up
  5-cycle loop kept local root-page reads about `0.13 s`-`0.19 s`,
  `/api/status` about `0.02 s`-`0.04 s`, and `/api/config` about
  `0.07 s`-`0.09 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `.207` still shows the same control-plane/device-name split as the
  prior pass: hub UI/API keep it `online` on `0.1.16-dev-central` with
  the one-click upgrade path visible to `0.1.17-dev-central`, while
  local `/api/status` and `/api/config` still return the older
  `Erica's ?.?. Speaker` name.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with weaker `.207` root-page stall

Fresh live recheck run around `2026-05-14T05:50Z` through `05:52Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48` and
`.207`.

Concrete improved findings:
- The live hub devices page and hub admin API still matched on all
  comparison targets in this pass. There was still no fresh
  central-side UI-vs-API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` again did not reproduce in this window. The initial `.48`
  local sweep plus the follow-up 5-cycle loop all returned `200` from
  `/`, `/api/status`, and `/api/config`; local root-page reads stayed
  about `0.10 s`-`0.19 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.08 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-055` weakly reproduced again instead of staying fully absent.
  `.207` stayed `online` in the hub on `0.1.16-dev-central`, local
  `/api/status` and `/api/config` stayed fast at about
  `0.02 s`-`0.09 s`, but the first local root-page fetch in the
  follow-up loop stretched to about `1.40 s` before cycles 2-5 settled
  back to about `0.09 s`-`0.16 s`. This is milder than the earlier
  `6.29 s` and `2.93 s` stalls, so the finding improved, but the bug is
  still open.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with clean `.207` follow-up

Fresh live recheck run around `2026-05-14T06:00Z` through `06:01Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48` and
`.207`.

Concrete improved findings:
- The live hub devices page still matched the hub admin API on all
  comparison targets in this pass. There was still no fresh
  central-side UI-vs-API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` again did not reproduce in this window. The initial `.48`
  local sweep plus the follow-up 5-cycle loop all returned `200` from
  `/`, `/api/status`, and `/api/config`; local root-page reads ran
  about `0.10 s`-`0.16 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.06 s`-`0.09 s`.
- `BUG-055` improved materially versus the prior pass. `.207` had one
  slower first local root-page read at about `1.30 s` in the initial
  sweep, but the immediate 5-cycle follow-up loop stayed clean with
  root-page reads about `0.10 s`-`0.16 s`, `/api/status` about
  `0.02 s`-`0.04 s`, and `/api/config` about `0.06 s`-`0.08 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `.207` is improved but not closed. The clean follow-up loop means the
  earlier repeated stall did not persist in this pass, but the slower
  `1.30 s` first root-page read keeps the issue as intermittent local
  UI latency rather than fully cleared behavior.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with another clean post-06:10Z window

Fresh live recheck run around `2026-05-14T06:10Z` through `06:11Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48` and
`.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` again did not reproduce in this window. The initial `.48`
  local sweep plus the follow-up 5-cycle loop all returned `200`; local
  root-page reads stayed about `0.15 s`-`0.29 s`, `/api/status` about
  `0.02 s`-`0.09 s`, and `/api/config` about `0.03 s`-`0.09 s`.
- `BUG-055` also did not reproduce in this pass. `.207` returned `200`
  on the initial local sweep and the follow-up 5-cycle loop kept
  root-page reads about `0.10 s`-`0.18 s`, `/api/status` about
  `0.02 s`-`0.03 s`, and `/api/config` about `0.06 s`-`0.10 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- This pass improves confidence that both `BUG-054` and `BUG-055` are
  intermittent rather than continuously reproducible, but neither issue
  is closed because both reproduced multiple times earlier on the same
  date.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with renewed `.48` root-page latency

Fresh live recheck run around `2026-05-14T06:20Z` through `06:21Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48` and
`.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-055` did not reproduce in this pass. `.207` returned `200` in
  the initial local sweep and the follow-up 5-cycle loop kept
  root-page reads about `0.11 s`-`0.14 s`, `/api/status` about
  `0.02 s`-`0.04 s`, and `/api/config` about `0.06 s`-`0.09 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` weakly reproduced again instead of staying absent. `.48`
  stayed `online` in the hub on `0.1.17-dev-central`, local
  `/api/status` stayed about `0.02 s` and local `/api/config` about
  `0.06 s`-`0.08 s`, but the first local root-page read stretched to
  about `3.03 s` and cycle 1 of the immediate 5-cycle follow-up loop
  still took about `1.02 s` before cycles 2-5 settled back to about
  `0.09 s`-`0.13 s`. This is materially milder than the earlier full
  timeout windows, but it keeps the local root-page latency problem
  open.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with 10-second `.48` first-hit stall

Fresh live recheck run around `2026-05-14T06:31Z` through `06:33Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48` and
`.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-055` did not reproduce in this pass. `.207` returned `200` in
  the initial local sweep and the follow-up 5-cycle loop kept
  root-page reads about `0.10 s`-`0.23 s`, `/api/status` about
  `0.02 s`-`0.03 s` except one slower `0.34 s` read on cycle 5, and
  `/api/config` about `0.07 s`-`0.10 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` reproduced again in a stronger latency-only form. `.48`
  stayed `online` in the hub on `0.1.17-dev-central`, and local
  `/api/status` plus `/api/config` stayed fast at about `0.02 s` and
  `0.06 s`, but the first local root-page fetch took about `10.29 s`
  before returning `200`. The immediate 5-cycle follow-up loop then
  stayed clean, with root-page reads about `0.10 s`-`0.43 s`,
  `/api/status` about `0.02 s`-`0.03 s`, and `/api/config` about
  `0.03 s`-`0.09 s`. Treat this as renewed evidence that the local root
  UI path can nearly hang on first contact even when the device JSON
  endpoints and hub heartbeat view remain healthy.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with `.207` API-latency blip

Fresh live recheck run around `2026-05-14T06:50Z` through `06:51Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48` and
`.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` did not reproduce in this window. The initial `.48` local
  sweep plus the follow-up 5-cycle loop all returned `200`; local
  root-page reads stayed about `0.10 s`-`0.35 s`, `/api/status` about
  `0.02 s`-`0.04 s`, and `/api/config` about `0.07 s`-`0.08 s`.
- The `.225` watch item improved versus the prior pass. Its initial
  local root-page fetch was about `0.31 s` instead of the earlier
  `1.41 s`, while `/api/status` and `/api/config` stayed fast.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-055` shifted shape in this pass rather than disappearing
  cleanly. `.207` stayed `online` in the hub on `0.1.16-dev-central`,
  local root-page reads stayed about `0.10 s`-`0.31 s`, and local
  `/api/config` stayed about `0.07 s`-`0.09 s`, but cycle 1 of the
  follow-up loop had a slower `/api/status` read at about `4.85 s`
  before cycles 2-5 returned to about `0.03 s`-`0.07 s`. Treat this as
  renewed evidence of intermittent local-control-plane latency on `.207`
  even though the root page and hub heartbeat view stayed healthy.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with milder `.207` first-hit root delay

Fresh live recheck run around `2026-05-14T07:02Z` through `07:04Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48` and
`.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` again did not reproduce in this window. The initial `.48`
  local root-page read returned `200` in about `0.63 s`, and the
  immediate 5-cycle follow-up loop stayed clean with root-page reads
  about `0.10 s`-`0.17 s`, `/api/status` about `0.02 s`-`0.04 s`, and
  `/api/config` about `0.06 s`-`0.08 s`.
- The `.225` watch item stayed mild rather than worsening. Its initial
  local root-page read was about `0.53 s` while `/api/status` and
  `/api/config` stayed fast at about `0.03 s` and `0.07 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-055` improved relative to the prior `.207` `/api/status`
  slowdown, but it did not disappear fully. The hub devices page and
  `/api/v1/admin/devices` still showed `.207` `online` on
  `0.1.16-dev-central` with the devices-page upgrade affordance still
  advertising `0.1.17-dev-central`; the initial local sweep returned
  `200` with the root page at about `0.28 s`, `/api/status` about
  `0.02 s`, and `/api/config` about `0.07 s`, but cycle 1 of the
  follow-up loop had a slower root-page read at about `1.46 s` before
  cycles 2-5 settled back to about `0.10 s`-`0.16 s`. Treat this as a
  weaker first-hit local UI delay than the earlier multi-second stalls,
  but keep the bug open.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with clean `.207` loop and weaker `.48` first-hit delay

Fresh live recheck run around `2026-05-14T07:10Z` through `07:12Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48` and
`.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `.207` improved relative to the prior pass. The hub still showed
  `online` on `0.1.16-dev-central` with the upgrade affordance toward
  `0.1.17-dev-central`, but the initial local sweep returned `200` with
  the root page at about `0.32 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.07 s`, and the immediate 5-cycle follow-up
  loop stayed clean with root-page reads about `0.10 s`-`0.27 s`,
  `/api/status` about `0.02 s`, and `/api/config` about `0.07 s`-`0.09 s`.
- The `.225` watch item improved again. Its initial local root-page
  read was about `0.22 s`, while `/api/status` and `/api/config` stayed
  fast at about `0.02 s` and `0.07 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` stayed narrower than the earlier timeout windows but still
  did not disappear. The initial `.48` local sweep returned `200` with
  the root page at about `0.39 s`, `/api/status` about `0.04 s`, and
  `/api/config` about `0.06 s`; then cycle 1 of the immediate 5-cycle
  follow-up loop hit a slower root-page read at about `1.20 s` before
  cycles 2-5 settled back to about `0.13 s`-`0.20 s`. `/api/status`
  and `/api/config` stayed fast throughout. Treat this as another
  weaker first-hit local UI delay rather than a fresh hard failure, but
  keep the bug open.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with clean `.48` and `.207` follow-up loops

Fresh live recheck run around `2026-05-14T07:21Z` through `07:22Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48` and
`.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` did not reproduce in this window. The initial `.48` local
  sweep returned `200` with the root page at about `0.17 s`,
  `/api/status` about `0.02 s`, and `/api/config` about `0.07 s`; the
  immediate 5-cycle follow-up loop also stayed clean with root-page
  reads about `0.10 s`-`0.20 s`, `/api/status` about `0.02 s`-`0.03 s`,
  and `/api/config` about `0.07 s`-`0.08 s`.
- `BUG-055` also did not reproduce in this window. `.207` stayed
  `online` in the hub on `0.1.16-dev-central` with the devices-page
  upgrade affordance still advertising `0.1.17-dev-central`; the
  initial local sweep returned `200` with the root page at about
  `0.12 s`, `/api/status` about `0.02 s`, and `/api/config` about
  `0.07 s`, and the immediate 5-cycle follow-up loop stayed clean with
  root-page reads about `0.10 s`-`0.23 s`, `/api/status` about
  `0.02 s`-`0.03 s`, and `/api/config` about `0.06 s`-`0.09 s`.
- The earlier `.225` watch item did not strengthen in this pass. Its
  initial local root-page fetch was about `0.20 s`, while
  `/api/status` and `/api/config` stayed fast at about `0.02 s` and
  `0.07 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `.30` stayed locally healthy despite the ongoing name drift. The root
  page returned `200` in about `0.21 s`, `/api/status` in about
  `0.02 s`, and `/api/config` in about `0.08 s`.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with renewed full-timeout `.48` first-contact failure

Fresh live recheck run around `2026-05-14T07:33Z` through `07:35Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48` and
`.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` still converged on identity and firmware once it responded:
  hub UI/API plus local `/api/status` and `/api/config` all agreed on
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-055` did not strengthen in this window. `.207` stayed `online`
  in the hub on `0.1.16-dev-central` with the devices-page upgrade
  affordance still pointing to `0.1.17-dev-central`; its initial local
  sweep returned `200` with the root page at about `0.20 s`,
  `/api/status` about `0.02 s`, and `/api/config` about `0.08 s`, and
  the immediate 5-cycle follow-up loop stayed clean with root-page
  reads about `0.11 s`-`0.37 s`, `/api/status` about `0.02 s`-`0.04 s`,
  and `/api/config` about `0.07 s`-`0.09 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` re-strengthened in this pass. The live hub devices page and
  `/api/v1/admin/devices` still showed `.48` `online` on
  `0.1.17-dev-central`, but the first local sweep timed out for the
  full 10 s window on `/`, `/api/status`, and `/api/config`. An
  immediate 5-cycle follow-up loop then recovered cleanly, with local
  root-page reads about `0.10 s`-`0.14 s`, `/api/status` about
  `0.02 s`-`0.11 s`, and `/api/config` about `0.03 s`-`0.09 s`. Treat
  this as renewed evidence of a first-contact local HTTP failure on the
  renamed soak target rather than just the weaker root-page latency
  shape seen in the prior two windows.
- `.225` stayed only a mild watch item. Its initial local root-page
  read was about `1.12 s`, while `/api/status` and `/api/config` stayed
  fast at about `0.02 s` and `0.08 s`; that is slower than the prior
  `0.20 s`-`0.22 s` windows but still not enough on its own for a fresh
  bug.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with transient `.207` truncated root response and stronger `.225` watch evidence

Fresh live recheck run around `2026-05-14T07:40Z` through `07:42Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48`,
`.207`, and `.225`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `.207` improved after its first-hit local root-page failure. The
  initial `/api/status` and `/api/config` reads stayed fast at about
  `0.02 s` and `0.09 s`, and the immediate 5-cycle root-page follow-up
  loop then returned clean `200`s at about `0.12 s`-`0.23 s`.
- `.225` recovered cleanly after its slower first-hit root-page read.
  The immediate 5-cycle follow-up loop returned `200`s at about
  `0.11 s`-`0.31 s`, while `/api/status` and `/api/config` stayed fast
  at about `0.02 s` and `0.07 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` stayed in the weaker latency-only bucket rather than the
  earlier full-timeout shape. The initial `.48` local sweep returned
  `200` with the root page at about `0.37 s`, `/api/status` about
  `0.02 s`, and `/api/config` about `0.08 s`, but cycle 2 of the
  immediate 5-cycle follow-up loop still hit a slower root-page read at
  about `1.54 s` before cycles 3-5 returned to about `0.16 s`-`0.26 s`.
- `BUG-055` shifted shape and re-strengthened. While the hub still
  showed `.207` `online` on `0.1.16-dev-central`, the first local root
  request failed after about `4.22 s` with a truncated-body
  `ChunkedEncodingError`; `/api/status` and `/api/config` stayed fast,
  and the immediate 5-cycle root-page follow-up loop then recovered
  cleanly. Treat this as renewed evidence of intermittent local UI
  failure on `.207`, not just weaker first-hit latency.
- `.225` strengthened as a watch item. Its first local root-page read
  stretched to about `2.10 s` while `/api/status` and `/api/config`
  stayed fast at about `0.02 s` and `0.07 s`; because earlier same-day
  passes also saw slower first-hit root-page reads on `.225`, this is
  now repeated watch evidence rather than a one-off sample.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with improved `.48`, non-repeating `.207` wobble, and shifted `.225` watch signal

Fresh live recheck run around `2026-05-14T07:51Z` through `07:53Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by focused timing loops through about
`07:56Z` on `.48`, `.207`, and `.225`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` improved relative to the prior timeout and `1.54 s`
  follow-up windows. The initial `.48` local sweep returned `200` with
  the root page at about `0.83 s`, `/api/status` about `0.03 s`, and
  `/api/config` about `0.09 s`, then the focused 5-cycle loop stayed
  clean with root-page reads about `0.10 s`-`0.22 s`, `/api/status`
  about `0.02 s`-`0.03 s`, and `/api/config` about `0.07 s`-`0.09 s`.
- `.207` improved versus the prior truncated-body failure. The initial
  local sweep returned `200` with the root page at about `0.30 s`,
  `/api/status` about `0.02 s`, and `/api/config` about `0.07 s`; a
  later 3-cycle confirmation loop also stayed clean with root-page
  reads about `0.16 s`-`0.20 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.07 s`-`0.08 s`.
- `.30` stayed locally healthy despite the ongoing name drift. The root
  page returned `200` in about `0.21 s`, `/api/status` in about
  `0.02 s`, and `/api/config` in about `0.07 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-055` did not cleanly re-strengthen in this window, but it also
  did not fully disappear. During one focused timing loop on `.207`,
  cycle 1 hit a slower root-page read at about `2.53 s` while
  `/api/status` and `/api/config` stayed fast at about `0.02 s` and
  `0.07 s`; the immediate 3-cycle confirmation loop did not repeat the
  slowdown.
- `.225` shifted from the earlier root-page-only watch pattern to a
  repeated local API stall. The initial sweep was mostly healthy with
  the root page at about `0.30 s`, `/api/status` about `0.03 s`, and
  `/api/config` about `0.08 s`, but a focused 5-cycle loop later hit a
  slower `/api/status` read at about `1.35 s`, and the immediate
  3-cycle confirmation loop hit another slower `/api/status` read at
  about `2.64 s` while the root page stayed about `0.17 s`-`0.32 s`
  and `/api/config` stayed about `0.07 s`-`0.10 s`. A subsequent
  8-cycle `/api/status` loop then returned to about `0.02 s`-`0.13 s`.
  Keep this below fresh-bug level for now, but it is now a repeated
  watch signal on a different endpoint.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with improved `.48`, stronger `.225` recovery wobble, and weaker `.207` API blip

Fresh live recheck run around `2026-05-14T08:20Z` through `08:22Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48`,
`.207`, and `.225`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` improved relative to the earlier full-timeout and root-page
  latency windows. The initial `.48` local sweep returned `200` with
  the root page at about `0.39 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.09 s`; the immediate 5-cycle follow-up loop
  then stayed clean on the root page at about `0.15 s`-`0.17 s` and on
  `/api/status` at about `0.02 s`-`0.04 s`.
- `.207` improved relative to the earlier truncated-body and
  first-contact root-page failures. The initial local sweep returned
  `200` with the root page at about `0.35 s`, `/api/status` about
  `0.02 s`, and `/api/config` about `0.12 s`; the immediate 5-cycle
  follow-up loop kept the root page to about `0.12 s`-`0.26 s` and
  `/api/config` to about `0.07 s`-`0.11 s`.
- `.30` stayed locally healthy despite the ongoing name drift. The root
  page returned `200` in about `0.21 s`, `/api/status` in about
  `0.02 s`, and `/api/config` in about `0.07 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` did not fully clear. Cycle 5 of the immediate `.48`
  `/api/config` follow-up loop stretched to about `2.58 s` while the
  rest of the loop stayed much faster; that is materially better than
  the earlier 10-second timeout windows, but still not clean enough to
  close the bug.
- `.225` re-strengthened into the strongest watch window so far while
  the hub still held it `online` on `0.1.17-dev-central`. The initial
  local root-page request took about `8.12 s`, the first local
  `/api/status` probe then failed after about `9.55 s` with a
  connection reset, and local `/api/config` still returned `200` in
  about `0.08 s`. The immediate 5-cycle follow-up loop then recovered
  cleanly with root-page reads about `0.10 s`-`0.19 s`,
  `/api/status` about `0.03 s`-`0.04 s`, and `/api/config` about
  `0.07 s`-`0.12 s`. This is materially worse than the earlier
  watch-only `.225` windows, even though it still recovered quickly.
- `BUG-055` stayed in the weaker bucket only. Cycle 2 of the immediate
  `.207` `/api/status` follow-up loop stretched to about `1.38 s`
  before later cycles returned to about `0.02 s`-`0.03 s`; the root
  page and `/api/config` stayed much faster throughout.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with renewed `.207` root-page wobble and improved `.225`

Fresh live recheck run around `2026-05-14T08:40Z` through `08:41Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48`,
`.207`, and `.225`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` stayed in the weaker latency-only bucket. The initial `.48`
  local sweep returned `200` with the root page at about `0.31 s`,
  `/api/status` about `0.02 s`, and `/api/config` about `0.08 s`; the
  immediate 5-cycle follow-up loop then hit one slower root-page read
  at about `1.44 s` before cycles 2-5 returned to about `0.10 s`-
  `0.12 s` while the JSON endpoints stayed fast.
- `.225` improved materially relative to the prior `8.12 s` root-page /
  `9.55 s` `/api/status` watch window. The initial local sweep returned
  `200` with the root page at about `0.25 s`, `/api/status` about
  `0.02 s`, and `/api/config` about `0.02 s`, and the immediate
  5-cycle follow-up loop stayed clean.
- `.30` stayed locally healthy despite the ongoing name drift. The root
  page returned `200` in about `0.17 s`, `/api/status` in about
  `0.02 s`, and `/api/config` in about `0.07 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-055` re-strengthened relative to the prior clean `08:31Z`-
  `08:33Z` window. `.207` stayed `online` on `0.1.16-dev-central`, the
  initial local sweep returned `200` with the root page at about
  `0.37 s`, `/api/status` about `0.03 s`, and `/api/config` about
  `0.08 s`, but cycle 1 of the immediate 5-cycle follow-up loop hit a
  slower `3.77 s` root-page read before cycles 2-5 returned to about
  `0.10 s`-`0.11 s` while `/api/status` and `/api/config` stayed fast.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with collapsing `.48` and `.207` recovery wobble plus weaker `.225` relapse

Fresh live recheck run around `2026-05-14T08:50Z` through `08:56Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48`,
`.207`, and `.225` plus a final 3-cycle spot-check on `.48` and
`.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` still converged across hub UI/API and local status/config as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central` once the
  local surfaces recovered.
- `.207` also reconverged cleanly after its first-hit stall. The
  immediate retry sweep returned `200` with the root page at about
  `0.18 s`, `/api/status` about `0.03 s`, and `/api/config` about
  `0.08 s`; the 5-cycle follow-up loop and final 3-cycle spot-check
  then stayed clean.
- `.30` stayed locally healthy despite the ongoing name drift. The root
  page returned `200` in about `0.12 s`, `/api/status` in about
  `0.02 s`, and `/api/config` in about `0.07 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` briefly re-strengthened, then narrowed again in the same
  watch window. The first `.48` local sweep hit about `9.82 s` on `/`,
  a full `10.02 s` timeout on `/api/status`, and about `2.69 s` on
  `/api/config` while the hub still showed the device `online`; the
  immediate retry sweep then recovered to about `0.19 s` on `/`,
  `0.02 s` on `/api/status`, and `0.03 s` on `/api/config`. In the
  immediate 5-cycle follow-up loop, cycle 1 of `/api/status` still
  stretched to about `2.80 s`, but later cycles and the final 3-cycle
  spot-check stayed clean.
- `BUG-055` also re-strengthened transiently before collapsing. The
  first `.207` local sweep hit a full `10.28 s` timeout on `/` and a
  slower `/api/status` read at about `4.86 s` while `/api/config`
  still returned `200` in about `0.08 s`; the immediate retry sweep,
  the 5-cycle confirmation loop, and the final 3-cycle spot-check then
  all stayed clean. Treat this as renewed evidence of intermittent
  local-surface recovery wobble, but not a sustained failure shape.
- `.225` slipped back into the weaker watch-only bucket rather than
  repeating the earlier root-plus-status failure shape. One initial
  sweep returned the root page in about `0.64 s` with fast JSON
  endpoints, then the immediate retry sweep hit a slower `4.05 s` root
  read while `/api/status` and `/api/config` stayed fast and the
  immediate 5-cycle follow-up loop stayed clean.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with clean `.48` and `.207` loops plus milder `.225`

Fresh live recheck run around `2026-05-14T09:00Z` through `09:01Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48`,
`.207`, and `.225`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` improved materially relative to the prior timeout and
  latency window. The initial `.48` local sweep returned `200` with the
  root page at about `0.25 s`, `/api/status` about `0.04 s`, and
  `/api/config` about `0.07 s`; the immediate 5-cycle follow-up loop
  then stayed clean with root-page reads about `0.10 s`-`0.19 s`,
  `/api/status` about `0.02 s`-`0.04 s`, and `/api/config` about
  `0.07 s`-`0.08 s`.
- `.207` improved materially relative to the prior first-hit timeout
  and `4.86 s` `/api/status` recovery wobble. The initial local sweep
  returned `200` with the root page at about `0.20 s`, `/api/status`
  about `0.02 s`, and `/api/config` about `0.07 s`; the immediate
  5-cycle follow-up loop then stayed clean with root-page reads about
  `0.09 s`-`0.11 s`, `/api/status` about `0.02 s`-`0.04 s`, and
  `/api/config` about `0.07 s`-`0.08 s`.
- `.225` improved materially relative to the prior `4.05 s` watch-only
  root delay. The initial local sweep returned `200` with the root page
  at about `0.20 s`, `/api/status` about `0.03 s`, and `/api/config`
  about `0.09 s`; the immediate 5-cycle follow-up loop then stayed
  clean with root-page reads about `0.10 s`-`0.26 s`,
  `/api/status` about `0.02 s`-`0.03 s`, and `/api/config` about
  `0.07 s`-`0.08 s`.
- `.30` stayed locally healthy despite the ongoing name drift. The root
  page returned `200` in about `0.13 s`, `/api/status` in about
  `0.02 s`, and `/api/config` in about `0.09 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` did not reproduce in this short recheck window, but keep it
  open because the same device hit repeated 10-second timeout and
  multi-second recovery windows earlier in the morning.
- `BUG-055` also did not reproduce in this short recheck window. The
  hub still showed `.207` `online` on `0.1.16-dev-central` with the
  one-click upgrade affordance to `0.1.17-dev-central`, while the local
  root page and JSON endpoints stayed fast throughout the follow-up
  loop.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with clean `.48` / `.207`, mild `.225`, and new `.30` root-page wobble

Fresh live recheck run around `2026-05-14T09:13Z` through `09:14Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48`,
`.207`, and `.225`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` did not reproduce in this window. The initial `.48` local
  sweep returned `200` with the root page at about `0.20 s`,
  `/api/status` about `0.02 s`, and `/api/config` about `0.07 s`; the
  immediate 5-cycle follow-up loop then stayed clean with root-page
  reads about `0.10 s`-`0.22 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.02 s`-`0.09 s`.
- `.207` also stayed clean in this window. The live hub still showed it
  `online` on `0.1.16-dev-central`, while the initial local sweep
  returned `200` with the root page at about `0.11 s`, `/api/status`
  about `0.03 s`, and `/api/config` about `0.07 s`; the immediate
  5-cycle follow-up loop then stayed clean with root-page reads about
  `0.11 s`-`0.17 s` and fast JSON endpoints.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `.225` slipped back into a weaker watch-only bucket. The initial
  local sweep returned `200` with the root page at about `0.19 s`,
  `/api/status` about `0.02 s`, and `/api/config` about `0.07 s`, but
  cycle 1 of the immediate 5-cycle follow-up loop hit a slower
  `3.01 s` root-page read before cycles 2-5 returned to about
  `0.10 s`-`0.16 s` while `/api/status` and `/api/config` stayed fast.
- `.30` stayed reachable and the hub/API identity picture did not
  change, but the first local root-page read stretched to about
  `4.23 s` while local `/api/status` and `/api/config` still returned
  `200` in about `0.02 s` and `0.07 s`. This is new root-page latency
  evidence on `.30`, but it only appeared once in this pass, so keep it
  below fresh-bug level for now.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with improved `.30` / `.225` and recent `.207` reboot evidence

Fresh live recheck run around `2026-05-14T09:20Z` through `09:21Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48`,
`.207`, and `.225` plus a confirming `.207` `/api/status` fetch about a
minute later.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` did not re-strengthen in this window. The initial `.48`
  local sweep returned `200` with the root page at about `1.33 s`,
  `/api/status` about `0.02 s`, and `/api/config` about `0.08 s`; the
  immediate 5-cycle follow-up loop then stayed clean with root-page
  reads about `0.10 s`-`0.60 s` and fast JSON endpoints.
- `.225` improved materially relative to the prior `3.01 s` watch-only
  root delay. The initial local sweep returned `200` with the root page
  at about `0.34 s`, `/api/status` about `0.02 s`, and `/api/config`
  about `0.07 s`; the immediate 5-cycle follow-up loop then stayed
  clean with root-page reads about `0.10 s`-`0.21 s` and fast JSON
  endpoints.
- `.30` improved materially relative to the prior one-off `4.23 s`
  root-page wobble. The initial local sweep returned `200` with the
  root page at about `0.21 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.07 s`; the earlier delay did not repeat in
  this pass.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `.207` stayed responsive and converged on state/version across the
  hub plus local API/config in this pass, but local `/api/status`
  reported `uptime_seconds=24` at about `09:20Z` and `94` on a
  confirming fetch about a minute later. That is concrete evidence of a
  recent reboot/recovery window even though the device stayed `online`
  in the hub and the immediate 5-cycle follow-up loop remained fast.
  Keep `BUG-055` open.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with fresh `.48` config truncation and repeated `.207` reboot evidence

Fresh live recheck run around `2026-05-14T09:29Z` through `09:31Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48`,
`.207`, and `.225` plus a confirming `.207` `/api/status` fetch about a
minute later.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.30` stayed improved relative to the prior one-off `4.23 s`
  root-page wobble. The local sweep returned `200` with the root page
  at about `0.12 s`, `/api/status` about `0.04 s`, and `/api/config`
  about `0.06 s`.
- `.225` also stayed improved. The initial local sweep returned `200`
  with the root page at about `0.28 s`, `/api/status` about `0.02 s`,
  and `/api/config` about `0.07 s`, and the immediate 5-cycle
  follow-up loop stayed clean with root-page reads about
  `0.10 s`-`0.13 s` and fast JSON endpoints.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `.48` remained converged across hub UI/API and local status/config as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, but
  `BUG-054` shifted back out of the clean bucket. The initial local
  sweep stayed healthy at about `0.38 s` on `/`, `0.03 s` on
  `/api/status`, and `0.07 s` on `/api/config`, yet cycle 4 of the
  immediate 5-cycle follow-up loop hit a `3.03 s`
  `ChunkedEncodingError` on `/api/config` with an incomplete body while
  `/` and `/api/status` stayed fast before and after. Treat this as a
  concrete recovery-wobble regression, albeit weaker than the earlier
  full 10-second timeout windows.
- `.207` stayed responsive and converged on state/version across the
  hub plus local API/config in this pass, but the reboot evidence
  strengthened rather than clearing. Local `/api/status` reported
  `uptime_seconds=109` in the first sweep and `206` on the confirming
  fetch about a minute later while the hub still showed the device
  `online` on `0.1.16-dev-central`. Keep `BUG-055` open.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with improved `.48` / `.225`, renewed `.207` root stall, and fresh `.30` reboot evidence

Fresh live recheck run around `2026-05-14T09:39Z` through `09:42Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by fresh 5-cycle local loops on `.48`,
`.207`, and `.225` plus confirming `/api/status` reads on `.30` and
`.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `BUG-054` improved relative to the prior `3.03 s`
  truncated-`/api/config` failure. The initial `.48` local sweep did
  hit a slower `2.97 s` root-page read, but `/api/status` stayed about
  `0.02 s`, `/api/config` stayed about `0.08 s`, and the immediate
  5-cycle follow-up loop stayed clean with root-page reads about
  `0.11 s`-`0.17 s`, `/api/status` about `0.02 s`-`0.05 s`, and
  `/api/config` about `0.03 s`-`0.11 s`.
- `.225` stayed in the improved bucket. The initial local sweep
  returned `200` with the root page at about `0.43 s`,
  `/api/status` about `0.03 s`, and `/api/config` about `0.07 s`, and
  the immediate 5-cycle follow-up loop stayed clean with root-page
  reads about `0.11 s`-`0.15 s` and fast JSON endpoints.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-055` re-strengthened in a new first-hit root-page shape. The
  live hub devices page and `/api/v1/admin/devices` still showed `.207`
  `online` on `0.1.16-dev-central`, while local `/api/status` and
  `/api/config` still exposed `Erica's ?.?. Speaker`. The initial local
  sweep stayed healthy, but cycle 2 of the immediate 5-cycle follow-up
  loop hit a slower `4.23 s` root-page read before cycles 3-5 returned
  to about `0.10 s`-`0.22 s` while `/api/status` and `/api/config`
  stayed fast. The confirming `/api/status` read later reported
  `uptime_seconds=814`, so this was not a fresh reboot, but it is
  renewed local-surface recovery wobble.
- `.30` stayed converged on reachability/state/version in the hub and
  local APIs, but new reboot evidence surfaced below fresh-bug level.
  The initial local sweep returned `200` with the root page at about
  `0.14 s`, `/api/status` about `0.02 s`, and `/api/config` about
  `0.07 s`, yet local `/api/status` reported `uptime_seconds=155` and a
  confirming fetch shortly afterward reported `279`. That is concrete
  evidence of a recent reboot/recovery window even though the hub still
  showed the device `online`; keep watching before promoting it beyond a
  reliability watch item.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with stronger `.48` reboot/recovery evidence and no fresh hub drift

Fresh live recheck run around `2026-05-14T09:50Z` through `09:53Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by an immediate 5-cycle local loop on `.48`
plus confirming `/api/status` reads on `.48` and `.30`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` re-converged quickly after the bad first hit. The immediate
  5-cycle follow-up loop returned clean `200` responses with root-page
  reads about `0.10 s`-`0.19 s`, `/api/status` about `0.02 s`-`0.05 s`,
  and `/api/config` about `0.07 s`-`0.09 s`.
- `.30` improved relative to the earlier short-uptime watch window. Two
  confirming `/api/status` reads reported `uptime_seconds=914` and then
  `949`, so the prior reboot evidence did not repeat in this pass.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` re-strengthened beyond the prior latency-only bucket. The
  live hub devices page and `/api/v1/admin/devices` still showed `.48`
  converged as `Rebooter - renamed test` / `online` /
  `0.1.17-dev-central`, but the first local root-page probe timed out
  after about `10.26 s` while local `/api/status` then returned `200`
  in about `1.59 s` with `uptime_seconds=10` and local `/api/config`
  returned `200` in about `0.07 s`. The immediate follow-up loop then
  stayed clean, and a later confirming `/api/status` read reported
  `uptime_seconds=98`. Treat this as concrete evidence of a real
  reboot/recovery window on the renamed soak target rather than just a
  one-off slow endpoint.
- `.225` stayed stable in this pass: the initial local sweep returned
  `200` with the root page at about `0.19 s`, `/api/status` about
  `0.02 s`, and `/api/config` about `0.07 s`. No fresh regression
  surfaced there.
- `.207` stayed improved relative to the earlier `4.23 s` root-page
  wobble. The initial local sweep returned `200` with the root page at
  about `0.35 s`, `/api/status` about `0.02 s`, `/api/config` about
  `0.09 s`, and local `/api/status` reported `uptime_seconds=1362`.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck with no fresh hub drift, clean `.48` follow-up, and stronger `.207` root failure

Fresh live recheck run around `2026-05-14T10:01Z` through `10:03Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by an immediate 5-cycle local loop on `.48`
and `.207` plus confirming `/api/status` reads on both devices.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. There was still no fresh
  central-side UI/API drift.
- `.48` remained converged across hub UI/API and local status/config:
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `.48` stayed clean in the immediate follow-up window after the recent
  reboot signal. The first local sweep returned `200` with the root
  page at about `0.28 s`, `/api/status` about `0.03 s`, and
  `/api/config` about `0.08 s`; the immediate 5-cycle follow-up loop
  then stayed clean with root-page reads about `0.12 s`-`0.22 s`,
  `/api/status` about `0.02 s`, and `/api/config` about `0.07 s`.
- `.30` and `.225` both stayed locally healthy in this pass while
  `BUG-053` remained unchanged: `.30` returned `200` with the root page
  at about `0.10 s`, `/api/status` about `0.05 s`, and `/api/config`
  about `0.08 s`; `.225` returned `200` with the root page at about
  `0.33 s`, `/api/status` about `0.03 s`, and `/api/config` about
  `0.08 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` did not reproduce again in the focused follow-up loop, but
  the recent reboot/recovery evidence remained concrete rather than
  cleared. The first `.48` `/api/status` read in this pass reported
  `uptime_seconds=92`, the immediate 5-cycle loop climbed cleanly from
  `180` to `186`, and a confirming read later reported
  `uptime_seconds=192`. Treat this as continued evidence that `.48`
  rebooted shortly before the pass even though the hub kept presenting
  it simply as `online`.
- `BUG-055` strengthened in a worse root-page shape than the earlier
  latency-only samples. The live hub devices page and
  `/api/v1/admin/devices` still showed `.207` `online` on
  `0.1.16-dev-central`, while local `/api/status` and `/api/config`
  still exposed `Erica's ?.?. Speaker`. The first local root-page probe
  failed after about `3.25 s` with a truncated-body
  `ChunkedEncodingError` (`IncompleteRead(2680 bytes read, 13343 more
  expected)`), while local `/api/status` still returned `200` in about
  `0.02 s` with `uptime_seconds=304` and `/api/config` returned `200`
  in about `0.08 s`. The immediate 5-cycle follow-up loop then stayed
  clean with root-page reads about `0.11 s`-`0.25 s`, and a confirming
  `/api/status` read reported `uptime_seconds=400`, so this was not a
  fresh reboot but it is a stronger local-surface response-integrity
  failure than the prior `4.23 s` root delay.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck after the `.48` offline dip: `.48` recovered but still corrupts root responses, `.225` rebooted, and `.207` rebooted again

Fresh live recheck run around `2026-05-14T10:39Z` through `10:42Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by an immediate 5-cycle local loop on
`.48`, `.225`, and `.207` plus confirming local `/api/status` reads.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. The rendered rows and the API
  both showed `.48`, `.30`, `.225`, and `.207` `online` with fresh
  heartbeats, while `.69` stayed `offline` with its stale
  `2026-05-13T22:06:13Z` heartbeat.
- `.48` recovered from the earlier `10:30Z`-`10:36Z` drop back into a
  fully converged identity state: hub UI/API plus local
  `/api/status` and `/api/config` all again showed
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`.
- `.225` also recovered out of the earlier hub-vs-local drift shape.
  By this pass the hub devices page/API and local status/config all
  again agreed on `online` / `0.1.17-dev-central`, and the immediate
  5-cycle follow-up loop stayed clean with root-page reads about
  `0.10 s`-`0.24 s`, `/api/status` about `0.02 s`-`0.03 s`, and
  `/api/config` about `0.06 s`-`0.08 s`.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` remains concrete on the renamed soak target in both reboot
  and local-UI corruption form. The initial `.48` local sweep returned
  `200` with the root page about `0.17 s`, `/api/status` about
  `0.02 s`, and `/api/config` about `0.08 s`, but local `/api/status`
  already showed only `uptime_seconds=232`, which is fresh reboot
  evidence relative to the earlier offline window. The immediate
  5-cycle follow-up loop then climbed from `298` to `326`, yet cycle 3
  still hit a `3.14 s` truncated-body `ChunkedEncodingError`
  (`IncompleteRead(2027 bytes read, 13996 more expected)`) on the root
  page while `/api/status` and `/api/config` kept returning fast `200`
  responses. That confirms the renamed soak target is not just
  rebooting; it still intermittently corrupts the local UI surface even
  after it comes back and while the hub keeps presenting it as
  `online`.
- `.225` improved operationally, but it now has fresh reboot evidence.
  The first local `/api/status` read in this pass reported
  `uptime_seconds=90`, the focused loop climbed from `153` to `181`,
  and a confirming later `/api/status` read reported `217`, so the
  device restarted again after the earlier drift window even though the
  hub had already reconverged to a healthy-looking `online` row.
- `BUG-055` also stays open as a reboot/recovery masking failure. In
  this pass the hub devices page/API still showed `.207` `online` on
  `0.1.16-dev-central`, while the initial local root-page read took
  about `11.35 s` and local `/api/status` showed only
  `uptime_seconds=9`. The immediate 5-cycle follow-up loop recovered
  and climbed from `61` to `89`, with one more slower root-page sample
  at about `2.10 s`, and a later confirming `/api/status` read
  reported `128`. Treat that as fresh reboot evidence plus continued
  transient local-root instability rather than a fully cleared device.
- `.30` showed no fresh regression beyond the standing name drift. The
  local root page returned `200` in about `0.22 s`, `/api/config`
  returned `200` in about `0.07 s`, and `/api/status` returned `200`
  with `uptime_seconds=3759`; the one slower `/api/status` sample at
  about `2.69 s` did not repeat in this pass.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck after the `.48` recovery: `.48` stayed clean, `.225` stayed improved, and `.207` rebooted again behind an online hub row

Fresh live recheck run around `2026-05-14T11:20:45Z` through `11:22:10Z`
against the authenticated hub devices page/API plus local device `/`,
`/api/status`, and `/api/config` probes for `.48`, `.30`, `.225`,
`.207`, and `.69`, followed by an immediate 5-cycle local loop on
`.48`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces showed `.48`
  `online` on `0.1.17-dev-central` with last heartbeat
  `2026-05-14T11:22:22Z`, `.30` `online` on `0.1.17-dev-central` with
  `2026-05-14T11:22:02Z`, `.225` `online` on `0.1.17-dev-central` with
  `2026-05-14T11:21:54Z`, `.207` `online` on `0.1.16-dev-central` with
  `2026-05-14T11:22:06Z`, and `.69` `offline` with its still-stale
  `2026-05-13T22:06:13Z` heartbeat.
- `.48` improved materially versus the prior offline/recovery window.
  The initial local sweep returned `200` with the root page at about
  `0.23 s`, `/api/status` at about `0.02 s`, and `/api/config` at
  about `0.08 s`, while local `/api/status` and `/api/config` both
  showed `Rebooter - renamed test` / `0.1.17-dev-central`. The
  immediate 5-cycle follow-up loop then stayed fully clean with root
  page reads about `0.10 s`-`0.21 s`, `/api/status` about
  `0.02 s`-`0.03 s`, `/api/config` about `0.07 s`-`0.09 s`, and local
  `/api/status` climbing steadily from `uptime_seconds=525` to `534`.
- `.225` stayed in the improved bucket. The hub row/API still showed
  `Erica's F.R Speaker` `online` on `0.1.17-dev-central`, while the
  local root page returned `200` in about `0.33 s`, local
  `/api/status` returned `200` in about `0.02 s` with
  `uptime_seconds=1106`, and local `/api/config` returned `200` in
  about `0.07 s`. That is still only a watch-level desired-name drift,
  not a fresh reboot/recovery issue.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-055` strengthened again on `.207` even though the hub row had
  already returned to a healthy-looking `online` state. In this pass
  the hub devices page/API showed `.207` `online` on
  `0.1.16-dev-central` with last heartbeat `2026-05-14T11:22:06Z`, and
  the local root page plus `/api/status` plus `/api/config` all
  returned clean `200` responses (`0.32 s`, `0.03 s`, `0.08 s`), but
  local `/api/status` reported only `uptime_seconds=194`. That is fresh
  reboot evidence relative to the prior `11:12:59Z`-`11:14:42Z`
  recovery loop where `.207` had already climbed to
  `uptime_seconds=49`-`50`; without another reboot it should have been
  far higher than `194` by `11:21Z`.
- `.30` showed no fresh regression beyond the standing desired-name
  drift. The local root page returned `200` in about `0.11 s`,
  `/api/status` returned `200` in about `0.02 s` with
  `uptime_seconds=6199`, and `/api/config` returned `200` in about
  `0.07 s`.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.

### 2026-05-14 EDT - renamed-test recheck after the latest clean `.48` window: `.48` rebooted again, `.30` joined the masked-reboot bucket, `.207` rebooted again during the loop, and `.225` stayed improved

Fresh live recheck run around `2026-05-14T11:50:46Z` through
`11:52:22Z` against the authenticated hub devices page/API plus local
device `/`, `/api/status`, and `/api/config` probes for `.48`, `.30`,
`.225`, `.207`, and `.69`, followed by an immediate 5-cycle local loop
on `.48`, `.30`, `.225`, and `.207`.

Concrete improved findings:
- The live hub devices page still matched `/api/v1/admin/devices` on
  every comparison target in this pass. Both surfaces showed `.48`,
  `.30`, `.225`, and `.207` `online` with fresh heartbeats, while `.69`
  stayed `offline` with its stale `2026-05-13T22:06:13Z` heartbeat.
- `.225` stayed in the improved watch bucket. The hub row/API still
  showed `Erica's F.R Speaker` `online` on `0.1.17-dev-central`, while
  the local root page plus `/api/status` plus `/api/config` all
  returned clean `200` responses (`0.42 s`, `0.03 s`, `0.04 s`) and
  local `/api/status` climbed from `uptime_seconds=2861` on the initial
  sweep to `2966` by the end of the focused loop. That keeps `.225`
  below fresh-reboot level in this pass even though the standing
  desired-name drift remains.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-054` strengthened again on the renamed soak target in another
  reboot-through-healthy-row shape. The initial local `.48` sweep still
  looked healthy, with root/status/config all returning `200`
  (`1.02 s`, `0.02 s`, `0.08 s`) and local `/api/status` reporting
  `Rebooter - renamed test` / `0.1.17-dev-central` at
  `uptime_seconds=593`, while the live hub still showed `.48` `online`
  with last heartbeat `2026-05-14T11:50:46Z`. But the immediate 5-cycle
  follow-up loop starting at `11:51:54Z` already found local
  `/api/status` reset to `uptime_seconds=74`, then climb only to `90`
  by cycle 5. The hub devices page/API still showed the device
  `online` with a fresh `2026-05-14T11:51:46Z` heartbeat after the
  loop, so the reboot window remained masked centrally even though the
  local device had restarted again.
- `.30` produced fresh masked-reboot evidence for the first time in
  this soak series. The hub devices page/API showed
  `Erica's Subwoofer` `online` on `0.1.17-dev-central` with last
  heartbeat `2026-05-14T11:51:11Z`, while the initial local sweep
  returned clean `200` responses and local `/api/status` reported only
  `uptime_seconds=23`. The immediate 5-cycle loop then climbed from
  `111` to `129`, with one slower local root-page read at about
  `3.50 s` on cycle 5. Treat that as concrete reboot/recovery evidence
  behind a healthy-looking hub row rather than just the standing
  desired-name drift.
- `BUG-055` strengthened again in both reboot and local-root-stall
  form. The initial local `.207` sweep returned clean `200` responses
  (`0.26 s`, `0.02 s`, `0.07 s`) while local `/api/status` reported
  only `uptime_seconds=143` despite the hub row/API already showing
  `.207` `online` on `0.1.16-dev-central` with last heartbeat
  `2026-05-14T11:50:12Z`. The immediate 5-cycle loop first climbed from
  `231` to `242`, then cycle 5 hit a `10.49 s` local root-page stall
  while `/api/status` dropped to `uptime_seconds=11` with
  `health_state="unknown"`. A post-loop hub refresh still showed `.207`
  `online` with last heartbeat `2026-05-14T11:52:22Z`, so the reboot
  and recovery stayed masked by the central row again.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.
### 2026-05-14 EDT - renamed-test recheck with improved `.48`, fresh `.207` reboot evidence, and no new hub identity drift

Fresh live recheck run around `2026-05-14T16:54:00Z` through
`16:55:45Z` against the authenticated hub devices page/detail plus
`/api/v1/admin/devices` and `/api/v1/admin/devices/<id>` for the
renamed soak target, followed by local `/`, `/api/status`, and
`/api/config` probes for `.48`, `.30`, `.225`, `.207`, and `.69`, plus
immediate 5-cycle follow-up loops on `.48`, `.207`, and `.225`.

Concrete improved findings:
- There was still no fresh hub identity drift on the renamed soak
  target. The hub devices page, hub device detail page,
  `/api/v1/admin/devices`, and `/api/v1/admin/devices/<id>` all kept
  showing `Rebooter - renamed test` at `192.168.1.48`,
  `online` / `heartbeat_state="online"` on `0.1.17-dev-central`; the
  detail API additionally showed `latest_heartbeat.last_event_type`
  still as `boot` with `received_at="2026-05-14T16:55:44Z"` and
  `uptime_seconds=1144`.
- `.48` improved materially relative to the prior `16:40:42Z` to
  `16:42:45Z` masked-reboot plus truncated-`/api/config` run. The
  initial local sweep returned `200` on `/`, `/api/status`, and
  `/api/config` (`0.261 s`, `0.021 s`, `1.569 s`) with local
  `/api/status` already at `uptime_seconds=1097`, and the immediate
  5-cycle follow-up loop then climbed cleanly from
  `uptime_seconds=1152` to `1158` without any timeout, reset, or
  truncated-body failure. Treat the slower `2.209 s` root-page sample
  on cycle 1 and slower `1.725 s` `/api/config` sample on cycle 3 as
  watch-level latency only, not a fresh BUG-054 repro.
- `.225` also moved back into the improved bucket after the earlier
  transient offline/recovery event. The hub still showed it `online` on
  `0.1.17-dev-central` with heartbeat `2026-05-14T16:54:37Z`, the
  first local `/api/status` read reported `uptime_seconds=805`, and the
  immediate 5-cycle loop then climbed steadily from `882` to `887`
  while `/`, `/api/status`, and `/api/config` all stayed clean.

Concrete remaining issue:
- `BUG-053` still reproduces unchanged on the ordinary fleet devices:
  - `.30`: hub `Erica's Subwoofer` vs local API/config `Rebooter`
  - `.225`: hub `Erica's F.R Speaker` vs local API/config `Rebooter`
  - `.207`: hub `Erica's R.R. Speaker` vs local API/config
    `Erica's ?.?. Speaker`

Reliability notes:
- `BUG-055` strengthened again on `.207` in both masked-reboot and
  local-root-integrity form. The hub devices page/API still showed
  `.207` `online` on `0.1.16-dev-central` with heartbeat
  `2026-05-14T16:54:35Z`, but the first local `/api/status` read was
  only `uptime_seconds=208`, far below the prior run's `1619`, so a
  fresh reboot happened between passes. The immediate 5-cycle local
  loop then climbed only from `271` to `283`; cycle 1 of local `/`
  failed after `3.189 s` with `ChunkedEncodingError` /
  `IncompleteRead(7568 bytes read, 8455 more expected)`, and cycle 3 of
  local `/` stretched to `5.945 s` while `/api/status` and
  `/api/config` stayed healthy. Treat that as another concrete
  reboot/recovery-plus-local-UI-corruption sample behind a healthy hub
  row.
- `.30` showed no fresh regression beyond the standing desired-name
  drift. The local root page returned `200` in about `0.253 s`,
  `/api/status` returned `200` in about `0.024 s` with
  `uptime_seconds=5700`, and `/api/config` returned `200` in about
  `0.029 s`.
- `.69` remains the stable offline control: hub `offline`, while local
  `/`, `/api/status`, and `/api/config` all timed out from this host.


---

## 2026-05-15 — post-v0.5.33 regression-sweep observations

### Environment

- Live hub `https://www.voipguru.org/rebooter` on `0.5.33`
- Fleet: 7 active devices; 0 power samples; 0 external_sensor_sources
- Firmware team had shipped `0.1.19-dev-central-safe` with the
  heartbeat-contract expansion (the Phase 3 unblock)

### Notable observations

- **Drift detection works against real `last_reported_config`.**
  R7 set `desired_config = {"device_name": "Erica's Subwoofer"}`
  on device `.48` (currently named "Rebooter - renamed test").
  Drift summary correctly returned
  `state='drifted', mismatched=['device_name']`. First time the
  B21 drift codepath has seen real device-reported data. Hub-side
  absorption of the new firmware heartbeat is partially working
  "for free" — `reported_config` shows up; richer `central_state`
  / `recovery_mode` fields still need hub plumbing (Phase 3 proper).
- **Long-poll concurrency holds at 4-wide.** All 4 returned in
  ~5.16 s in parallel (cap was 5 s). `gthread` worker × 8 threads
  gives headroom for the 7-device fleet. Saturation point (8+)
  untested; theoretically the 9th queues.
- **`compute_daily_rollups()` is idempotent.** Manual re-run for
  the same day produced `rollups_written: 1` both times — no
  IntegrityError because the module-level `UniqueConstraint()`
  declaration DID bind to the table (confirmed via `\d`:
  `uq_device_power_rollups_device_day UNIQUE CONSTRAINT, btree
  (device_id, day_bucket)`). Briefly concerning at code-review;
  reality fine.
- **`avg_w` math verified end-to-end.** R4 ingested 5 samples at
  144.0/145.2/146.4/147.6/148.8 W; rollup returned `avg_w: 146.4`
  exactly. Numeric→float coercion + SQL `AVG()` correctly drops
  the Decimal-precision footgun.
- **CSV export headers correct.** `text/csv; charset=utf-8` +
  `attachment; filename="rebooter-power-24h.csv"`. 11-column
  canonical header. Body empty when no samples — expected.
- **Power probe stale-sample gate works.** R12 ingested a 12-min-old
  sample; probe with `max_sample_age_seconds=600` correctly returned
  `failure: reason='stale_sample', sample_age_seconds: 721`. Runtime
  defends correctly even with bad operator input — the reason
  BUG-055 is medium, not high.

### Operator-UX edge cases (not bugs per se)

- **`/app/power` empty state is large.** Renders rate-setter form
  + `0 devices reporting` + "No power samples yet" card —
  ~2/3 of viewport. Correct per Phase 1B's "no broken card" goal
  but the steady state today. Consider a collapsed-card variant.
- **Auto-rebind guardrail UX is silent.** When a device announces
  with a known MAC but the announcing IP doesn't match
  `local_ip`, auto-rebind silently doesn't fire. Operator sees
  nothing about why. Consider an attention item
  ("auto-rebind candidate but IP mismatch — investigate").
- **Rules-list event log shows raw `details` JSON keys for unknown
  probe kinds.** When `_probe_to_phrase` falls through to
  `unknown probe '{kind}'`, the event row carries the raw
  `details` dict — Jinja renders as Python repr. Tied to BUG-054.

### Environment quirks worth remembering

- The `data/pg/` directory is owned by the Postgres-user inside
  the container; `git status` emits a permission warning on it.
  Harmless but noisy.
- `docker exec rebooter-droids python -c "…"` runs in a fresh
  interpreter where `init_engine()` hasn't been called. Anything
  using `session_scope()` from that path must call
  `init_engine(load_settings())` first. Documented during R4.
- The `mac_address` validator accepts hex + `:` `-` `.` space.
  QA scripts tripped over `"AA:CC:R12:..."` because `R` isn't hex.
  Use `secrets.token_hex(3).upper()` for synthetic MACs.

### Parallel-session work pending merge

Working tree at sweep end has (not mine to commit):
- `docs/firmware-apply-config-schema-v01.md` modified (Phase 4C
  alignment work by firmware-coord session)
- 5 new `docs/notes/2026-05-14-*.md` — firmware contract +
  heartbeat-expansion + reported-config + button-verification
- `docs/notes/2026-05-14-rebooter-48-heartbeat-preview.json`
- 3 still-empty 0-byte stubs from earlier sessions

Same merge posture as v0.5.24: wait for parallel session to pause
cleanly, then merge as a versioned ship.
