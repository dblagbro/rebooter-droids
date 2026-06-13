# QA notes

Living document. Notes during a QA pass go here — environment quirks,
flaky behaviour, risks that don't qualify as bugs but matter for the
next round.

## 2026-06-12/13 deep regression session

### Environment

- Hub: `https://www.voipguru.org/rebooter` (prod, container
  `rebooter-droids` on tmrwww01).
- Fleet at QA-start: 3 devices on firmware 0.2.33 (185, 188, 190).
  `.185` had 21 Exceptions in the 5h preceding QA — the cascade was
  active when QA started, fixed mid-QA via 0.2.33.
- No staging — all validation runs against prod. Read-only by default;
  every write is opt-in and audit-trailed.

### Auth

- Session cookie minted out-of-band via `docker exec rebooter-droids
  python3 -c "...SecureCookieSessionInterface..."` and stashed at
  `~/ui-audit/session-cookie.txt`. NEVER paste this into a script.
- The agent test token `rbt_zqxqDUgE17o8qtfN59igJKJidbrwim46zZ34mz5lgis`
  has `read` scope — useful for negative tests against write endpoints.

### Power topology context (#210)

- `.188` is plugged into `.185`'s relay (confirmed 2026-06-12). Every
  `.185` reboot causes a `.188` Power-On reset. This affects every
  reboot-count interpretation: `.188`'s "Power On" count is **inflated**
  by exactly `.185`'s reboot count.
- During QA, `.190` is independently powered.

### Recent ship velocity

- Firmware: 0.2.18 → 0.2.33 (16 releases) in ~7 days. Three CRITICAL
  fixes (#208/#209/#205). Each fix was caught by the diag-syslog
  harness + CrashRecorder, NOT by static review — the bugs only
  surfaced under heap pressure that the test suite doesn't simulate.
- Hub: 0.6.24 → 0.6.39 (16 releases). Includes the 10-PR brutal-review
  redesign + power topology.

### Known flaky areas (avoid retesting until fixed)

- Firmware OTA mid-cascade: `.190` returned "Connection refused" on
  OTA attempt during the 0.2.32 → 0.2.33 window. Retry succeeded
  after the device finished its in-flight reboot. The hub-side OTA
  has no retry logic; manual operator retry is the workaround.
- Single-worker advisory-lock gate: `_claim_scheduler_lock()` uses
  `pg_try_advisory_lock` with a per-session check-out. A second call
  in the same process gets a different connection from the pool, so
  the second call fails. The diag-syslog collector startup is folded
  into the scheduler-start block specifically to work around this.

### Test infrastructure quirks

- `tests/qa/conftest.py::shell_session` boots a session per test
  module via `/api/v1/auth/login` with bootstrap admin creds. Tests
  that revoke the session must `pytest.fixture(scope='function',
  autouse=True)` clean up the cookie.
- The `responsive` marker for mobile viewport tests requires
  Playwright; older Selenium tests are deprecated.
- `tools/ui-audit.py` does its own session-mint flow — see
  `~/ui-audit/session-cookie.txt`. Run after every UI-touching ship.

### Fleet timing

- Diag-syslog packets land in `/data/diag/<mac>.jsonl` per device.
  Files are NOT in the git tree (gitignored as of 0.6.39). Each can
  grow up to 5 MB before rotation; rotated file is `.jsonl.1`.
- Fleet under chronic heap pressure: `.185` and `.188` sit at
  mfb≈12-13K with frag 30-40% steady-state. Proactive restart at
  13K + 15s debounce (as of 0.2.33) is expected to fire frequently
  on these devices. This is by design — better a clean planned
  restart than a WiFi-SDK NULL deref.

### 2026-06-13 QA workflow results

Multi-agent QA workflow (`rebooter-droids-deep-qa-validation`)
exercised 7 surface probes + pytest CI + ui-audit harness. Findings:

- 4 HIGH: BUG-062 (cycles), BUG-063 (FK 500), BUG-064 (RBAC tamper),
  BUG-065 (CI red — nav-link count)
- 5 MEDIUM, 2 LOW, plus 1 doc drift

All 4 HIGH **fixed in v0.6.40 + tests landed**:
  - `tests/unit/test_device_topology_guards.py` — 6/6 green
  - `tests/qa/test_responsive.py` updated to accept the trimmed nav

5 MEDIUM open and tracked in BUG-066…BUG-069 + BUG-071. Don't block
release; schedule for next bundle.

### Workflow infrastructure observations

- The deep workflow surfaced bugs in code I'd shipped within the last
  6 hours. The pattern is becoming routine: ship → spawn QA workflow
  → triage 4-10 findings → fix.
- `tools/UI-AUDIT.md:46` documents cookie path as
  `tools/ui-audit-cookie.txt` but `tools/ui-audit.py` reads
  `tools/session-cookie.txt`. Minor doc-code drift; will fix on next
  touch of either file.
- pytest CI uses `-x` (halt on first failure) — that's fine for fast
  feedback but means BUG-065's failing line 104 masked whether line
  108 also fails. Recommend splitting that single test into two so
  one failure doesn't hide the other.

### Process improvements identified

- QA workflow should run as a routine post-deploy step. Currently I
  invoke it ad-hoc when scope feels worth it; should be reflex on any
  PR that touches `app/blueprints/` or `app/services/`.
- The `responsive` marker bucket is invisible to the CI gate. Either
  promote it into `-m ci` (acceptable runtime addition) or add a
  separate workflow job for the responsive bucket.
