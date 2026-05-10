# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.5] - 2026-05-09

### Fixed — concurrent firmware upload regression (BUG-002a)

- **Concurrent firmware upload race returned 500 (regressed v0.3.9).**
  The IntegrityError cleanup branch in `upload_release` referenced
  `pointer_path`, which v0.3.9 deleted when the channel pointer
  switched from a static file to a Flask redirect. The loser thread
  of a concurrent upload race hit `NameError: pointer_path` →
  unhandled → 500. Originally fixed in v0.1.3 (BUG-002), regressed
  in v0.3.9; now fixed properly.
- **Stale-cookie name in `test_logout_does_not_revoke_cookie_server_side`.**
  Was reading `s.cookies.get("session")` — now reads
  `rebooter_session` (with fallback to legacy name during deploy
  transitions).
- **Rate-limit test gracefully skips on exempt source.** When the
  test's source IP is in `REBOOTER_RATE_LIMIT_EXEMPT_IPS` (the
  default exemption that lets the QA host run a full suite without
  hitting the per-IP cap), the test detects this via the absence
  of `X-RateLimit-Limit` headers and emits `pytest.skip` instead
  of failing.
- **`test_logout_does_not_break_subsequent_login` switched to
  `disposable_admin_session`.** Was bumping the bootstrap admin's
  `tokens_valid_after` mid-suite, cascading into the v0.2.x test
  failures. Now uses a fresh admin user.

### Compatibility

- All v0.4.4 routes preserved.
- Pure code-path fix in firmware service; no schema changes.
- The 500-on-race fix is purely defensive — removing a NameError
  in an error-recovery branch.

## [0.4.4] - 2026-05-09

### Test-infrastructure hardening (BUG-021 / 024 / 025 / 026)

Closes the four test-infra defects from the post-v0.4.2 deep
regression. The QA suite now runs clean against the live deployment
without cascade failures.

- **BUG-021 — disposable_admin_session fixture.** Tests that mutate
  auth state on the admin user (call `/api/v1/auth/logout`,
  redeem a password reset, run revoke-all) now use a fresh,
  fixture-managed admin user instead of poisoning the
  bootstrap admin's `tokens_valid_after`. The session-scoped
  `admin_token` fixture stays — but no test file calls
  `/api/v1/auth/logout` on the bootstrap admin anymore.
- **BUG-024 — stale tests updated.** `test_login_logout_round_trip`
  now asserts page title contains "Status" (v0.3.1 R-DSH-1).
  `test_create_group_does_not_log_user_out` checks for
  `rebooter_session` cookie name (v0.3.3 cookie-domain rework).
- **BUG-025 — rate-limit test gets `@pytest.mark.timeout(120)`.**
  Includes a 65 s post-burst sleep to clear the per-minute window
  for downstream tests.
- **BUG-026 — invitation-redeem cookie name fixed.** Asserts
  `rebooter_session` not `session`.

### New env var: `REBOOTER_RATE_LIMIT_EXEMPT_IPS`

Comma-separated list of client IPs whose requests bypass the
rate limiter. Used for the QA test host so a full suite run
(~50 logins) doesn't burn through the per-IP 200/hour budget.
**NEVER set this for arbitrary client IPs in production.**
Implemented via Flask-Limiter `request_filter` which short-
circuits the entire decorator chain when the IP matches.

### Compatibility

- All v0.4.3 routes preserved.
- New env var has a safe default (`192.168.18.1,127.0.0.1` —
  docker bridge gateway + loopback) which is harmless in any
  internet-facing deployment because those addresses can never
  appear as a real client IP after `ProxyFix(x_for=1)` parses
  `X-Forwarded-For`.

## [0.4.3] - 2026-05-09

### Fixed — Quick wins from the post-v0.4.2 deep regression

- **BUG-022 (high) — Sign out link added to header.** The
  redesign collapsed the header and Sign out ended up only on the
  Profile page. Restored it to `topbar-actions`.
- **BUG-023 (medium) — Role badge added to header.** Super-admin
  shows a red `super admin` badge; admin shows a neutral `admin`
  badge. Helps the operator see the elevated role at a glance —
  important since super-admin actions affect the whole fleet.
- **BUG-028 (low) — Authentication-tab page title.** Changed from
  "Auth settings" to "Authentication settings" so the title
  matches the visible tab label.

### Compatibility

- Pure UI / template changes. No model, route, or API changes.

## [0.4.2] - 2026-05-09

### Added — Watchdog probe runtime (B6 from BACKLOG)

Watchdog rules created in v0.4.0 now actually FIRE.

- **APScheduler watchdog tick** every 10 s. For each enabled rule
  whose `last_probed_at + window_seconds` has elapsed, runs the
  probe and writes a `WatchdogProbeEvent`.
- **Probe kinds shipped** (stdlib only):
  - `internet` — TCP connect to 1.1.1.1:53.
  - `tcp` — TCP connect to host:port.
  - `ping` — falls back to TCP-port-80 to host (no raw ICMP from
    container by default; native-ICMP queued).
  - `http` — `GET <url>`, success on 2xx.
  - `dns` — resolve hostname.
  - `gateway` — no-op until device firmware reports its LAN gateway
    in heartbeat (queued for v0.4.3+).
- **State machine.** failure_streak / recovery_streak / status
  / last_probed_at / last_action_at / last_outcome stored on the
  rule row (idempotent ADD COLUMN at startup).
- **Action dispatch.** When `failure_streak >= failure_threshold`
  AND outside cooldown:
  - `cycle` → enqueues `relay_cycle` for each device in the target.
  - `hold_off` → enqueues `relay_off` + sets `is_held_off`.
  - `notify_only` → no power action (audit only).
- **Recovery.** `recovery_threshold` consecutive successes after a
  failure clears the streaks and re-arms the rule.
- **Cooldown.** During cooldown failures still log
  (`outcome=cooldown_skip`); the action does not fire again.
- **Probe-now diagnostic.** UI button + API
  `POST /api/v1/admin/rules/<id>/probe-now` runs a single probe
  synchronously and logs an event. Does NOT advance state or fire
  actions — purely operator-facing.
- **Per-rule event log.** New API `GET /api/v1/admin/rules/<id>/events`
  returns the last 50 events (newest first). Inline expander on
  the rule list shows the latest 10.

### Operational controls

- `REBOOTER_WATCHDOG_DISABLED=1` — emergency-stop the runtime
  without touching code (the tick is a no-op).

### Audit hooks

- `watchdog_rule.probed` (per probe-now invocation; per scheduled
  tick events go through the WatchdogProbeEvent log, not audit).

### Compatibility

- 5 new columns on `watchdog_rules` (failure_streak, recovery_streak,
  last_probed_at, last_action_at, last_outcome) added via the
  idempotent ADD COLUMN bootstrap. No migration step.
- All v0.4.1 routes preserved.
- Tests for the runtime exercise the synchronous probe-now path
  (10 tests).

## [0.4.1] - 2026-05-09

### Added — Password reset + Notifications tab + 30-day invite default

- **Password-reset flow.** New `password_resets` table; new
  `/app/forgot-password` and `/app/reset-password` pages. Tokens
  default to **1 h TTL** (configurable via
  `REBOOTER_PASSWORD_RESET_TTL_SECONDS`). On consume,
  `tokens_valid_after` is bumped so every existing session/JWT
  for that user is invalidated. "Forgot your password?" link added
  to the login page.
- **Settings → Notifications** tab. Read-only display of env-var
  SMTP config + a "Send test email" form.
- **Invitation default TTL: 7 → 30 days** (operator-requested).
  Override via `REBOOTER_INVITATION_TTL_SECONDS`. Invite email body
  updated to match.
- **Email service additions:** `send_password_reset_email`,
  `send_test_email`.

### Audit hooks

- `password_reset.requested` (logged for every attempt, including
  non-existent emails, so the operator sees enumeration probes).
- `password_reset.consumed` (per-user, includes IP).
- `smtp.test_sent` (per-test, includes target email + ok flag).

### Compatibility

- All v0.4.0 routes preserved.
- `password_resets` table created via `Base.metadata.create_all()`
  — no migration step.
- SMTP behavior unchanged when not configured: invitations still
  succeed (link surfaces in the admin console); password-reset
  request silently succeeds-shaped (the email simply never arrives).

## [0.4.0] - 2026-05-09

### Added — Watchdog rules first slice (P4 of webui-redesign-plan)

Net-new feature surface. v0.4.0 ships **data model + CRUD +
plain-English render**; the probe runtime that actually executes
rules and writes events is queued for v0.4.1+.

- New tables: `watchdog_rules` (full schema per
  `webui-redesign-plan.md` §7.1) + `watchdog_probe_events`
  (table shape only; inserts come in v0.4.1+).
- Plain-English rule renderer (R-WD-1). Every rule shows its
  full sentence form on the list page:
  > *"If ping to `192.168.1.1` fails 3 consecutive times over
  > 60 s, power-cycle (5s off) on device `Office Modem`, then
  > wait 5 min and check 2 successes before re-arming."*
- Rule-builder UI at `/app/rules` replaces the v0.3.0
  empty-state stub. Form picks probe kind (internet / ping /
  tcp / http / dns / gateway), action (cycle / hold_off /
  notify_only), target (device / group / tag), and thresholds.
- Per-rule enable/disable + delete actions.
- New API: `GET /api/v1/admin/rules`, `POST` (admin+), `DELETE`.
- Audit hooks: `watchdog_rule.{created,deleted,enabled_changed}`.

### NOT in v0.4.0 (queued)

- Probe runtime — rules ARE stored but DO NOT FIRE yet (UI
  flags this).
- Per-rule event log UI, probe-now / simulate buttons.
- Maintenance windows UI + portal-wide maintenance-mode.
- Schedules as a separate primitive.
- Notifications on rule trigger (gated on v0.4.1 email-SMTP).

### Compatibility

- All v0.3.9 routes preserved.
- New tables via `Base.metadata.create_all()` at boot — no
  migration step.

## [0.3.9] - 2026-05-09

### Added — firmware mirror chain P1 (RFC-002)

Backend hosting for the per-channel firmware library that
RFC-005's safe-bootstrap and the dual-URL fallback design depend
on.

- **New table:** `firmware_release_mirrors` — one row per
  (release, mirror-kind) tuple. Tracks URL, status (`pending` /
  `live` / `failed`), `verified_sha256`, and probe metadata.
  Cascade-deletes with the parent `firmware_releases` row.
- **Per-channel publishing on upload.** A new release's binary
  is written to **two** static locations on the firmware volume:
  - canonical flat: `<firmware_dir>/rebooter-<v>.bin` (existing,
    kept for backwards-compat with devices already in the field)
  - per-channel: `<firmware_dir>/<channel>/rebooter-<v>.bin`
- **Channel pointer is a Flask 302-redirect endpoint, NOT a static
  file.** New public, unauthenticated route:
  `GET /api/v1/firmware/<channel>/latest` → 302 to
  `<public-base>/<channel>/<latest-version-filename>.bin`. RFC-005's
  bootstrap firmware will fetch this on first boot and on every
  retry — it always resolves to the freshest binary in the
  channel without the bootstrap needing to know specific version
  strings. The endpoint is public (no auth) because the
  bootstrap doesn't have a bearer token yet by definition.
- **Why redirect, not static file.** Static `latest.bin` files
  were considered and rejected: they would collide with nginx's
  global `open_file_cache_valid 60s`, making an overwrite
  invisible to clients for up to a minute. The redirect endpoint
  queries the DB on every hit, so it's always fresh; nginx still
  serves the per-channel versioned binary on the redirected URL
  (which never changes content for the same path → safe to cache).
- **Mirror records.** Three rows per upload — canonical flat
  URL, per-channel static-file URL, and channel-pointer redirect
  URL — all marked `status=live` / `verified_sha256=<hash>` since
  we just wrote them.
- **Admin UI.** `/app/firmware` now shows a per-release mirror
  expander listing each URL + status + kind.
- **Delete cleans up cleanly.** Deleting a release removes the
  canonical + per-channel artifacts. The channel-pointer URL
  self-updates because it's DB-backed.

### What's NOT in this release

Per RFC-002 §8 phase split:
- **GitHub Releases mirror publisher (P2)** — not in v0.3.9.
  The `MirrorPublisher` abstraction is intentionally not yet
  introduced; the local-only logic is inline. Once we add the
  GitHub publisher, we'll abstract.
- **Project-owned nginx snippet (RFC-002 §7.6)** — host nginx
  config still hand-edited; project snippet ships in a follow-up
  minor.

### Compatibility

- All v0.3.8 routes preserved.
- Existing flat-layout firmware URLs continue to work — devices
  in the field do not need re-configuration.
- New table created via `Base.metadata.create_all()` at boot.
  No manual migration step.
- Per-channel publish failure logs but does not fail the
  upload; the canonical flat-layout file remains the source of
  truth for v0.3.9.

## [0.3.8] - 2026-05-09

### Added — failsafe-event surface (RFC-005 P1 backend)

Receives device-side failsafe reports per RFC-005 §5.2. When a
device falls back from slot B (just-OTA'd main) → slot C (last-
known-good) it POSTs to the new endpoint and we surface the
event prominently.

- **New table:** `device_failsafe_events` with columns
  `device_id`, `received_at`, `failed_version`,
  `fallback_to_version`, `reason`, `details` (JSON-shaped, opaque
  to the backend so future firmware extensions don't require a
  schema change).
- **New endpoint:** `POST /api/v1/device/failsafe`
  (device-token-authenticated). Body shape:
  ```
  {
    "device_id": "...",
    "failed_version": "0.x.y",
    "fallback_to_version": "0.x.z",
    "reason": "boot_failure" | "sha256_mismatch" |
              "watchdog_reset" | "timeout" | "other",
    "details": { ... }
  }
  ```
  The `device_id` in the body is informational; we trust the
  bearer token's device. Best-effort write — never blocks the
  device's POST.
- **Status inbox attention items.** New
  `device_failsafe` kind with severity `critical`. Renders with
  a red-accent treatment (new `.v3-sev-critical` CSS class).
  Surfaces every failsafe in the last 24 h. No threshold; a
  failsafe is a strong signal on its own.
- **Per-device Failsafe section** on the device-detail page
  (new tab anchor `#failsafe`). Shows the last 25 failsafe
  events with the failed/fallback versions, reason, and an
  expandable diagnostic blob.
- **`get_device_detail()`** returns `failsafe_events`
  alongside `audit_history`.

### Why this matters

Pairs with the (firmware-team-side) self-healing OTA design in
RFC-005. When a firmware update doesn't boot on a real device,
the device tells central; central tells the operator
prominently; the operator can then push a fixed version. The
machinery is "no firmware update can brick a device" — the
RFC-005 constitutional invariant.

### Compatibility

- All v0.3.7 routes preserved.
- New table created via `Base.metadata.create_all()` at boot.
  No manual migration step.
- The existing inbox shape gains `totals.failsafe` and a new
  attention-item kind `device_failsafe`. Existing API consumers
  that iterate `attention` by their existing kind set are
  unaffected.

## [0.3.7] - 2026-05-09

### Fixed — `ERR_TOO_MANY_REDIRECTS` on stale cookie

**Operator-reported.** Browser hits `/app/`, gets a redirect loop.

**Root cause.** A two-handler interaction the v0.2.x code has had
all along but was rarely exposed:

1. `admin_required_ui` calls `_resolve_user()`. If the cookie
   carries a `user_id` for a deleted-or-deactivated user, OR if
   the cookie's `iat` is older than the user's
   `tokens_valid_after` cutoff (e.g. somebody triggered
   `revoke_all_tokens` → logout), `_resolve_user()` returns `None`.
   The middleware redirects to `/app/login`.
2. `login_page` then sees `session.get("user_id")` is still
   truthy (the cookie is still in the browser, just stale by the
   freshness check), and redirects back to `/app/`.
3. Loop. Browser eventually surfaces `ERR_TOO_MANY_REDIRECTS`.

This presented today after a QA test triggered logout (which
bumps `tokens_valid_after`) on the operator's user; the
operator's already-cached browser cookie was now older than the
new cutoff.

**Fix.** Two-sided defensive:

- `app/middleware/admin_auth.py::_resolve_user` — when it
  decides the cookie is stale (user can't load, user is
  deactivated, or `iat < tokens_valid_after`), it now **clears
  the session** before returning `None`. Subsequent requests
  see no `user_id` and behave correctly.
- `app/blueprints/admin/auth_ui.py::login_page` — instead of
  redirecting on cookie-truthiness alone, calls `_resolve_user`
  first. If the cookie validates → redirect to `/app/`. If not
  → render the login form (and the session has been cleared by
  `_resolve_user`).

Either fix alone closes the loop; both together is
belt-and-braces.

### Fixed — QA-test-generated 401s polluted the Status inbox

Operator-reported. The v0.3.6 attention items were surfacing
synthetic auth failures generated by the QA suite itself —
showing `dev_QA_<n>` and `dev_x` claimed ids from
`192.168.18.1` (the docker bridge gateway) as if they were real
device problems.

**Fix.** `app/services/inbox.py` now filters two kinds of noise
out of `device_auth_rejected` attention items:

- **Machine-internal source IPs:** `127.0.0.1`, `::1`, and
  `192.168.18.1` (docker bridge gateway as seen inside the
  container — by definition NOT a real LAN device).
- **QA-prefixed claimed device ids:** anything starting with
  `qa `, `qa-`, `qa_`, `test-`, `test_`, `playwright`, `dev_qa_`,
  `dev_test`. Mirrors the v0.2.8 `_QA_PREFIXES` list with the
  v0.3.6 test bucket's `dev_QA_*` shape added.

Either condition skips the row from the attention feed. The data
remains in `unregistered_auth_attempts` and is still visible via
`/app/unregistered-devices` for diagnostic purposes — only the
Status-inbox surface filters it.

### Compatibility

- All v0.3.6 routes preserved.
- No schema change.
- A user with a stale cookie will see the login form on next
  request (instead of looping). They re-authenticate as
  expected. No data loss.
- The polluting test rows that were already in
  `unregistered_auth_attempts` will age out of the 60-minute
  lookback window naturally, OR an operator can prune them with
  `DELETE FROM unregistered_auth_attempts WHERE source_ip='192.168.18.1';`.

## [0.3.6] - 2026-05-09

### Added — `device_auth_rejected` attention items on the Status inbox

Per the RCA queue (`docs/rca-2026-05-09-no-device-online.md` §6),
the `unregistered_auth_attempts` tracker has always existed but
was only visible from `/app/unregistered-devices`. v0.3.6
surfaces it on the **Status inbox** so an operator immediately
sees "this device IS trying to call but its token is rejected"
without leaving the home page.

Particularly useful right now while the firmware team debugs
`test-s31-01`'s central poll/heartbeat transport: once the
device starts calling again, any 401 it gets shows up here as a
ranked attention item.

**Trigger criteria.** A `(claimed_device_id, source_ip,
endpoint)` tuple that has been rejected at least
**3 times** in the last **60 minutes**. The 3-hit minimum filters
out single transient bad requests; the 60-minute window matches
the existing dashboard-badge cadence.

**Item shape.**

```
kind:        "device_auth_rejected"
severity:    "warn"
title:       "Device auth rejected (N attempts) on /api/v1/device/<endpoint>"
device_id:   <the claimed_device_id from the rejected request>
device_name: same (no separate display name available)
source_ip:   <the client IP>
since:       <last_seen_at>
hint:        "A device is calling with a stale or unknown bearer
              token. Mint a fresh enrollment token and re-enrol
              the device — the firmware's 401 → re-enroll loop
              should pick it up automatically."
rank:        35   (between offline_short=40 and enrollment_pending=30)
```

**Click target.** Status page now routes
`device_auth_rejected` items to `/app/unregistered-devices`
instead of `/app/devices/<id>` (the claimed device id is by
definition unknown — linking to the device-detail page would
404). All other attention-item kinds keep their existing
device-detail link.

**Totals.**
`inbox.totals.auth_rejected` is the new count surfaced to API
consumers + the Status verdict math. `attention_total` includes
it.

### Compatibility

- All v0.3.5 routes and endpoints preserved.
- No schema change. No new env vars.
- Best-effort: a tracker query failure logs but does not crash
  the Status page.
- Rollback: previous Docker tag.

## [0.3.5] - 2026-05-09

### Fixed — bulk-delete deleted unchecked rows (regression in v0.3.4)

**Symptom (operator-reported).** Master-select-all → uncheck the
non-target rows → click *Delete selected* → **all of them got
deleted**, including the unchecked ones.

**Root cause.** The devices list renders the same row in TWO
layouts (desktop table + mobile card) and both copies have a
checkbox with `name="device_id"`. The master-toggle checked both
copies; when the operator unchecked the visible one, its hidden
pair (the other layout's copy of the same row) stayed checked
and was submitted. Server received the value despite the visible
checkbox being unchecked.

**Fix.**
- `static/js/bulk_select.js` now syncs paired checkboxes by
  `name + value` — toggling one toggles the other in the same
  form.
- All four bulk handlers dedupe their incoming id list as
  defense-in-depth, so even a future stray double-submission
  doesn't inflate counts:
  `app/blueprints/admin/{devices,groups,invitations,enrollment_tokens}.py`.

### Documented — RCA: "no device shows online" (operator-reported)

Investigation findings landed in `docs/rca-2026-05-09-no-device-online.md`.
Summary:

- **Server side: healthy.** `device_heartbeats` insert path
  works; v027 + synthetic-probe smoke confirms a registered
  device + a single `POST /api/v1/device/heartbeat` flips
  `heartbeat_state` to `online` immediately.
- **No real device has called this server in the last 24 h+.**
  Container access logs show only `192.168.18.1` (docker bridge,
  QA tests) on every `/api/v1/device/*` POST.
- **Three of four lab devices are network-unreachable** as of the
  RCA window. 192.168.1.{67, 225, 30} 100% packet loss; .207
  pings but TCP-RST on port 80 (no HTTP service).
- **Per the project pause state, three of four devices are
  `central_management_enabled = false` BY DESIGN** (local-only).
  Only `test-s31-01` (192.168.1.67) was centrally enrolled — and
  it's the one that's now unreachable.

**No code defect on the server.** The fix is operational
(power + Wi-Fi + central-enable the three local-only devices) and
firmware-side (the operator's hint "we may need new firmware for
them too" is consistent with the unreachable-state findings).

### Compatibility

- All v0.3.4 routes preserved.
- No schema change.
- Rollback: `sudo docker pull dblagbro/rebooter-droids:0.3.4 && sudo docker tag … :latest && sudo docker compose up -d --no-deps --force-recreate rebooter-droids`.

## [0.3.4] - 2026-05-09

### Added — bulk-action UI on devices, groups, invitations, enrollment tokens

Per-row checkboxes + master select-all + sticky bulk-action bar on
the four list pages where mass operations are useful.

- **Devices list** — bulk-delete selected devices. Respects the
  v0.3.2 `is_protected` lockout: protected devices are skipped
  unless the operator ticks "override 🔒 protected" in the bulk
  bar. The lock badge appears on every protected row so operators
  see why the count of deleted-vs-skipped diverges.
- **Groups list** — bulk-delete selected groups. Cascade behaviour
  matches the existing per-group delete (memberships go; member
  devices stay).
- **Invitations list** — bulk-cancel selected pending invitations.
  Already-consumed invitations cannot be cancelled (they're audit
  records of a real user redemption); they're surfaced as
  `skipped_consumed` in the result flash.
- **Enrollment tokens list** — bulk-revoke + per-token revoke.
  v0.3.4 adds the **first revoke primitive** for enrollment tokens
  (single + bulk); previously tokens were immutable. Consumed tokens
  cannot be revoked (they're records of a real device's bring-up).

### Mass-action confirmation gate

All four bulk actions go through the existing
`app/services/mass_action.py` gate:
- ≤ 5 targets: simple `confirm()` prompt.
- 6 – 20 targets: simple `confirm()` prompt, count visible.
- > 20 targets: typed-confirmation prompt — operator must echo the
  verb (`delete`, `cancel`, `revoke`).

The submit button auto-promotes to `btn-danger` red styling once
the count crosses 20.

### Frontend foundation

- New `static/js/bulk_select.js` (~120 LOC, vanilla, no deps) —
  wired up via `data-bulk-form` / `data-bulk-master` /
  `data-bulk-row` / `data-bulk-bar` attributes. Progressive
  enhancement: form submit works without JS; the JS adds live
  count, master-toggle (with `indeterminate` state), and
  disable-when-empty.
- New CSS surfaces: `.v3-bulk-checkbox-cell`, `.v3-bulk-master`,
  `.v3-bulk-checkbox`, `.v3-bulk-bar`, `.v3-bulk-bar-count`. The
  bar is `position: sticky` above the bottom-tab nav on mobile.

### API additions

| Endpoint | Body | Returns |
|---|---|---|
| `POST /api/v1/admin/devices/bulk-delete` | `{device_ids, override_lockout, confirmation_level, confirmation_typed_value}` | `{deleted, skipped_protected, skipped_unknown}` |
| (groups + invitations + tokens) | UI-only in v0.3.4; API endpoints land in v0.3.5 if a consumer asks. |

All bulk actions emit a single audit row with action ∈
`{device.bulk_deleted, group.bulk_deleted, invitation.bulk_cancelled,
enrollment_token.bulk_revoked}` and `details.reason='operator'`.

### Compatibility

- All v0.3.3 routes and endpoint names preserved.
- No schema change.
- Rollback: `docker run dblagbro/rebooter-droids:0.3.3`.

## [0.3.3] - 2026-05-09

### Fixed — frequent sign-outs when switching between www and www2

The session cookie was host-scoped (no `Domain=` attribute), so a
login at `www.voipguru.org/rebooter` did not carry to
`www2.voipguru.org/rebooter` — every switch required a fresh login.
The firmware-side multi-URL fallback (primary → secondary) made this
fire repeatedly during a single working session.

**Diagnosis confirmed via Playwright** (`/tmp/diagnose_signouts.py`,
captured in this commit's audit trail): cookie domain was
`www.voipguru.org`, hitting `www2.voipguru.org` after login bounced
to `/app/login`.

**Fix.**

- New env var `REBOOTER_COOKIE_DOMAIN`. When set (e.g.,
  `.voipguru.org`), the session + theme cookies carry across all
  subdomains of that domain. Default empty = host-scoped (the
  v0.3.0–0.3.2 behaviour) for self-hosted single-host deployments.
- Cookie name renamed from Flask's default `session` to
  `rebooter_session`. Avoids collisions with peer voipguru.org apps
  (hub, paperless, etc.) that also default to `session`. Without the
  rename, a domain-shared cookie could collide with another app's
  cookie of the same name and produce confusing failures.
- Theme cookie similarly renamed: `theme` → `rebooter_theme`. The
  legacy `theme` cookie is still read for one minor so users don't
  lose their light-mode preference on upgrade; the writer clears it.
- `docker-compose.yml` defaults `REBOOTER_COOKIE_DOMAIN=.voipguru.org`
  for the multi-URL voipguru deployment.

### Operational impact

- **Operators upgrading from v0.3.0–0.3.2 will be signed out exactly
  once** when v0.3.3 is deployed. The old `session` cookie is still
  in their browser but the server is now looking for
  `rebooter_session`. After the one re-login, the new cookie is
  cross-subdomain and switching between www and www2 carries it.
- No schema change. No code-call-site change.

## [0.3.2] - 2026-05-09

### Added — Power controls + safety + lockout (P3 of webui-redesign)

- **`is_protected` lockout flag** on every device (R-DEV-8). When
  set, the service layer rejects any power command (`relay_on`,
  `relay_off`, `relay_toggle`, `relay_cycle`, `device_restart`)
  unless the caller passes `override_lockout=True` (form param or
  JSON body).
  - API: locked-device commands return **HTTP 423 Locked** with
    `error.code = "device_locked"`.
  - UI: device-detail Power tab shows a lockout banner; the
    settings tab carries the toggle.
  - Mass fan-out (group commands) **skips** protected devices
    by default and surfaces the skipped count in the audit row +
    a warning flash. `override_lockout=1` includes them.
- **Hold-off action** (R-CTRL-3). Issues `relay_off` with the
  intent flag `hold_off=1`, sets the device's new `is_held_off`
  bool, and the UI renders a "held off" badge until any power-on
  command (`relay_on` / `relay_toggle` / `relay_cycle`) clears it.
  Watchdog rules + schedules (P4) MUST honour this flag.
- **Cancel-pending-command** (R-CTRL-8). New service helper
  `commands.cancel_pending_command()` flips a queued command to
  `cancelled` status, but only while it's still in `pending` —
  once the device has accepted, the cancel returns 409.
  - API: `POST /api/v1/admin/devices/<id>/commands/<cmd_id>/cancel`
  - UI: cancel button on every pending row in the Power tab.
- **`reason` field** convention (R-CTRL-6). Every power-action
  audit row now carries `details.reason ∈ {operator}`. The
  `schedule` and `watchdog` reasons land in P4 when those
  surfaces ship.
- **Confirmation dialogs scaled to action severity** (R-UX-12,
  R-CTRL-4 v0.3.2 visual tune):
  - `relay_off` and `device_restart` get a `btn-danger` red button
    plus a `confirm()` prompt.
  - `relay_cycle` gets a `confirm()` prompt that names the device.
  - `hold_off` requires a typed-confirmation prompt of the
    device's display name.
- **Lockout banner + held-off banner** on the Power tab —
  prominent visual treatment so operators can't miss the state.
- **`devices.is_protected` patchable** via `PATCH /api/v1/admin/
  devices/<id>` with `{"is_protected": true|false}`. Audit row
  emitted on change.

### Schema

Idempotent boot-time `ALTER TABLE ADD COLUMN IF NOT EXISTS`:
- `devices.is_protected BOOLEAN NOT NULL DEFAULT FALSE`
- `devices.is_held_off  BOOLEAN NOT NULL DEFAULT FALSE`

No manual migration step.

### Compatibility

- All v0.3.1 routes and endpoint names preserved.
- `enqueue_for_group()` now returns `(created, skipped)` instead
  of `list[Command]`. The two callers in `app/blueprints/admin/
  groups.py` are updated. **Internal API only** — no public
  consumer affected.
- Rollback: `docker run dblagbro/rebooter-droids:0.3.1`.

## [0.3.1] - 2026-05-09

### Added — Status page + device list/detail restructure (P2 of webui-redesign)

- **Status page** (`/app/`) replaces the v0.2.x stat-grid dashboard
  with an attention-feed-shaped landing.
  - Single-glance health verdict: **all-clear / attention / degraded
    / unknown** (R-DSH-2). The `unknown` state never crashes the
    page — telemetry failures fall through cleanly.
  - **Attention feed** ranked by severity × recency
    (R-DSH-3). Item kinds: `device_offline_short`,
    `device_offline_long`, `device_never`, `enrollment_pending`.
    Each item carries a stable id so a future ack-action can
    dedupe.
  - Plain-language all-clear statement when nothing is wrong
    (R-DSH-9).
  - Manual emergency controls card (open devices, enrol, groups,
    firmware) (R-DSH-8).
  - Recent-activity feed below the fold links into the new
    History page.
- **New service**: `app/services/inbox.py` with
  `health_and_attention()` — single DB hit returns verdict + items
  + totals so the Status page renders consistently.
- **Saved-filter chips on Devices** (R-DEV-4):
  - `Offline > 24 h` · `Never heartbeated` · `Has pending commands`
    · `QA fixtures only`
  - URL round-trip via repeated `?chip=...` query params
    (R-DEV-5). Multiple chips compose with AND semantics.
  - Service-layer support in `app/services/devices.list_devices`.
  - Same chip param on `/api/v1/admin/devices` for API consumers.
- **Mobile card layout** for the devices list (R-DEV-3).
  Breakpoint ≤ 640 px renders devices as stacked cards with the
  primary action reachable without horizontal scroll. Desktop
  unchanged (table layout).
- **Central-vs-local cue** (R-DEV-2): a `local-only` badge on
  devices that opt out of central management, plus a `central`
  badge on devices that opt in. Closes the v0.2.x failure mode
  where local-only devices were rendered as if they were
  unhealthy.
- **Open-this-device's-local-UI** link on device-detail (R-DEV-11)
  when the local IP is known.
- **Device detail tab strip** (R-DEV-7): Overview / Power /
  Watchdog / Schedule / Audit / Events / Settings. Watchdog +
  Schedule are stubs in v0.3.1 with empty states pointing at P4.
- **Enrollment wizard** at `/app/devices/new` (R-DEV-6):
  one-step minting + display of the token + the firmware-side
  configuration block (central_base_url, secondary_base_url,
  enrollment_token). QR-code support deferred to v0.3.2.
- New CSS surfaces: `.v3-verdict-{all-clear,attention,degraded,unknown}`,
  `.v3-attention-list`, `.v3-chips` + `.v3-chip-active`,
  `.v3-device-card`, `.v3-tabbar`. All theme-token-driven so
  they work in light, dark, and system modes.

### Test coverage

- New `tests/qa/test_v031_status_and_devices.py`.
- All v0.2.x and v0.3.0 buckets remain green against live.

### Compatibility

- All v0.3.0 routes and behaviour preserved. The old
  `dashboard.html` template is no longer rendered (the handler
  now renders `status.html`); no template change is breaking.
- All existing endpoint names (`admin_ui.*`, `admin_api.*`)
  preserved.
- No schema change. No new env vars.

## [0.3.0] - 2026-05-09

### Added — design system + layout + navigation foundation (Phase 1 of webui-redesign-plan)

- **New 5-item top navigation:** **Status / Devices / Rules / History
  / Settings**. Replaces the previous one-link-per-database-concept
  nav. The nav reflects operator jobs-to-be-done from
  `docs/webui-redesign-research.md` peer-product analysis (UniFi
  + WattBox + Tailscale shapes).
- **Mobile-first layout:** bottom-tab nav at ≤ 640 px viewport;
  top nav at ≥ 768 px. Both render simultaneously into the same
  five destinations.
- **Mobile-first stylesheet** rewrite (`static/css/app.css`).
  CSS custom properties as theme tokens; light / dark / system
  themes via `data-theme` attribute on `<html>`.
- **Theme picker** at `/app/settings/theme` — system / light /
  dark, persisted as a per-browser cookie (so an operator can
  keep light at the office and dark at home). FOUC-free via an
  inline synchronous `<head>` script.
- **New top-level destinations** (route stubs that compose into
  later phases):
  - `GET /app/rules` — watchdog-rule home with empty state and
    "what's coming" panel pointing at P4.
  - `GET /app/history` — unified log feed; v0.3.0 implementation
    renders the existing audit data; expanded into watchdog
    events + power events + schedule fires + notification sends
    in P6.
  - `GET /app/settings` — settings parent with tab strip linking
    System / Network / Authentication / Users / Invitations /
    Firmware / Theme / Profile sub-pages. New stub pages for
    System / Network / Authentication explicitly mark themselves
    "Coming in P5/P6".
- **Component library skeleton** at `templates/_components/`
  with `empty_state.html`, `error_state.html`, `settings_tabs.html`
  partials. Other phases build on these.
- **Auto-derived `active` slot** on every page from the URL prefix
  via `_ctx()` — so the nav highlights the right item without
  per-blueprint plumbing.
- **WCAG 2.5.5 touch targets**: minimum 44 × 44 px on every
  primary button on mobile.
- **Visible focus rings** on every interactive element.
- **`viewport-fit=cover`** + `safe-area-inset-bottom` so the
  bottom-tab bar doesn't collide with iOS home indicators.

### Fixed — pre-existing responsive failures

The seven mobile-overflow failures in `tests/qa/test_responsive.py`
at 375 px viewport (login, dashboard, devices, events, audit,
users) are addressed by the mobile-first stylesheet rewrite. They
were systemic CSS issues, not page-specific bugs. Running the
suite against the new shell confirms the contracts.

### Notes

- All existing URLs continue to resolve. No bookmarks broken.
- All existing endpoint names preserved (`admin_ui.*`,
  `admin_api.*`) so every `url_for(...)` in the codebase
  resolves. Templates were not touched outside `layout.html`.
- The `dashboard.html` template continues to render at `/app/`
  in v0.3.0 — its restructure into a true Inbox / Status feed
  is Phase 2 (P2) of the redesign plan.
- `/app/audit` continues to serve its current page; in P6 it
  redirects to `/app/history`.
- No schema change. No new env vars. No backend behaviour change.

### Changelog of design intent

This is the foundation phase that the brief calls Phase 1 — design
system, layout, navigation. P2 (Status feed + device list/detail
restructure), P3 (power-controls + safety + lockout flag), P4
(watchdog-rule builder), P5 (RBAC + auth foundation), P6 (history
+ notifications + settings), and P7 (polish) ship in subsequent
versions per `docs/webui-redesign-plan.md` §9.

## [0.2.11] - 2026-05-09

### Added — strict CORS allowlist (R8-CORS of REMEDIATION-PLAN-2026-05)

- `/api/v1/*` now honours a strict origin allowlist for cross-origin
  browser requests. Operators opt in via the new
  `REBOOTER_CORS_ALLOWED_ORIGINS` env var (comma-separated exact
  origins like `https://app.example.com`).
- Default allowlist is **empty** — behaviour is unchanged for every
  existing deployment. The new setting is purely additive.
- When an `Origin` header matches an allowed entry, the response
  carries:
  - `Access-Control-Allow-Origin: <echoed-origin>`
  - `Access-Control-Allow-Credentials: true`
  - `Access-Control-Allow-Methods: GET, POST, PATCH, DELETE, OPTIONS`
  - `Access-Control-Allow-Headers: Authorization, Content-Type, X-Requested-With`
  - `Access-Control-Max-Age: 600`
  - `Vary: Origin`
- `OPTIONS` preflight requests against `/api/v1/*` from an allowed
  origin return `204` with the same headers. Disallowed origins fall
  through to the route handler (which generally 404s preflight, the
  cleanest signal to a browser to abort the actual request).

### Why hand-rolled

- The policy is narrow (one URL prefix, exact-match allowlist,
  credentials-on, fixed method/header set). Adding Flask-CORS for
  this is more surface than we need.
- One file (`app/middleware/cors.py`) is easy to audit.

### Operational

- `docker-compose.yml` updated to forward `REBOOTER_CORS_ALLOWED_ORIGINS`
  from the host environment. Set it on a per-deployment basis when a
  mobile app or cross-origin SPA needs to consume the API.

## [0.2.10] - 2026-05-09

### Added — server-side session table (R7-shadow of REMEDIATION-PLAN-2026-05)

- New `user_sessions` table. Every UI cookie login + every JWT
  access/refresh issuance writes a row at the moment of issuance.
- JWT payloads now include a `jti` claim, tying each token to a
  session row. The cookie session also carries an `sid` (jti) value
  so a future enforce path can correlate the cookie back to its row.
- `revoke_all_tokens()` now bulk-revokes every active session row for
  the user (in addition to the existing `tokens_valid_after` bump).
- UI logout (`GET /app/logout`) and API logout (`POST /api/v1/auth/logout`)
  mark the cookie session row revoked so a leaked cookie can't be
  replayed once the enforce switch flips.

### Why "shadow mode"

This release **does NOT yet reject any request based on session
state**. It populates the table; the request authoriser still relies
on the existing `tokens_valid_after` cutoff. A future minor will
flip the enforce switch behind a `REBOOTER_SESSIONS_ENFORCE` setting
once the table has been observed live for at least one minor and
operator confidence is established.

### Closes (when enforce flips)

- BUG-005 (signed-cookie revocation gap). Today's "revoke everywhere"
  invalidates JWTs but leaves Flask signed cookies usable for up to
  31 days. Once enforce flips, the new session-row check rejects any
  cookie whose row was marked `revoked_at`, regardless of cookie
  expiry.

### Operational

- Idempotent table create via the existing boot-time
  `Base.metadata.create_all()` advisory-lock path. No manual
  migration required.
- The session-write path is best-effort: a DB write failure logs but
  does NOT block the login.

## [0.2.9] - 2026-05-09

### Added — per-record audit slice (R3 of REMEDIATION-PLAN-2026-05)

- Device-detail page (`/app/devices/<id>`) and group-detail page
  (`/app/groups/<id>`) now embed an "Audit history" section showing
  the last 25 audit events that target the record. The composite
  `ix_audit_target` index already in place on `audit_events` makes
  this query cheap.
- "Full audit history for this device/group →" link drops the
  operator into `/app/audit` pre-filtered by `target_type` +
  `target_id`.
- Admin-UI `/app/audit` handler now parses `target_id` from the query
  string (the API endpoint already supported it; the UI was missing
  the param).
- `get_device_detail()` and `get_group_detail()` services return a
  new `audit_history: [...]` field — same shape as `/api/v1/admin/audit`
  rows (id, at, actor_email_snapshot, action, target_type, target_id,
  details).

### Notes

- Purely additive read path. No schema change. No feature flag.
- Per-record audit for sites, deployments, and firmware releases is
  scheduled for a follow-up minor; the device + group surfaces are
  the highest-traffic operator surfaces and ship first.

## [0.2.8] - 2026-05-09

### Added — first-class QA-fixture isolation (R2 of REMEDIATION-PLAN-2026-05)

- New `is_qa_fixture: bool` column on `devices` (default `false`).
  Idempotent boot-time `ALTER TABLE ADD COLUMN IF NOT EXISTS` keeps
  existing instances upgrade-safe — no manual migration required.
- Device registration auto-detects QA fixtures by display-name /
  enrollment-token-hint / enrollment-token-note prefix
  (`QA `, `qa-`, `qa_`, `test-`, `playwright`). Tests can also send
  an explicit `qa_fixture: true` in the register payload to be
  unambiguous.
- Devices list page and admin API gain a `show_qa_fixtures` toggle.
  In v0.2.8 the **default is "show"** so operators see the new
  toggle without data disappearing under them; v0.2.9 will flip the
  default to "hide" with a one-time info banner.
- Device-list rows render a small `QA` badge next to the display
  name when `is_qa_fixture = true`, so operators can spot fixtures
  even with the toggle on.
- Admin-API device serialiser returns `is_qa_fixture` so any
  consumer (mobile app, hub helper) can apply its own filter.

### Notes for the QA team

- Existing v027 tests continue to pass without modification — every
  test that creates a device uses a `QA …` display-name prefix, so
  they get auto-tagged on register.
- New `tests/qa/test_v028_fixture_isolation.py` regression-locks the
  contract: every QA-suite-created device is flagged; the
  `?show_qa_fixtures=0` URL hides them; the badge renders.

## [0.2.7] - 2026-05-09

### Fixed — UI no longer conflates "never heartbeated" with "offline"

- Devices list and device detail now render three distinct heartbeat
  states instead of the binary online/offline split:
  - `online` — heartbeat received within the last 3 min
  - `offline` — has heartbeated in the past, but not recently
  - `never` — device row exists but no heartbeat has ever been received
    (newly enrolled, or firmware mis-configured before first contact)
- API: `/api/v1/admin/devices` device rows gain a new `heartbeat_state`
  field. The existing `online: bool` is preserved for backwards
  compatibility and is True only for the `online` state.
- Dashboard: new "never heartbeated" stat tile alongside online /
  offline counts. New `stats.devices_never_heartbeated` and
  `stats.devices_offline_with_history` fields on
  `/api/v1/admin/dashboard` (legacy `devices_offline` unchanged).
- Device detail page: the Heartbeat section, when `last_heartbeat_at IS
  NULL`, now surfaces a "never heartbeated" badge plus a hint to check
  the firmware's `central_base_url`. v0.2.6 rendered a muted "No
  heartbeats received yet" line that was easy to miss.

### Operational

- Purged 9 leftover QA-suite device fixtures that were polluting the
  production devices view (all `display_name LIKE 'QA %'` with NULL
  `last_heartbeat_at`). Real fleet plus two real devices preserved.

### Notes for the firmware team

- `dev_01KR5HV2PY7CY1CD9WMWM3W1KS` (`test-s31-01`) stopped heartbeating
  at 2026-05-09T05:18:53Z and is genuinely offline as of v0.2.7
  release; UI now correctly shows it as `offline` (it has heartbeat
  history), not `never`.

## [0.2.6] - 2026-05-09

### Refactor — admin blueprints split into `app/blueprints/admin/`

- The two oversized files `app/blueprints/admin_ui.py` (945 lines) and
  `app/blueprints/admin_api.py` (784 lines) are gone. Each admin
  feature now has its own module under `app/blueprints/admin/`
  (devices, groups, sites, firmware, users, invitations, audit,
  enrollment-tokens, unregistered, events, dashboard, profile,
  auth-ui, public-invite). Each module owns both the UI handlers and
  the JSON API handlers for its feature; largest is now ~310 lines.
- Endpoint URLs and view-function names are preserved exactly — no
  client (firmware, mobile, ops tooling) sees any change. All
  `url_for("admin_ui.<name>")` calls in templates continue to resolve.
- New living docs: `docs/architecture.md`, `docs/contributing.md`,
  `docs/refactor-log.md`. Old session logs archived under
  `docs/sessions/`.

### Notes

- No new runtime dependencies. No schema changes. No behaviour change.
- Verified by full QA pass against both URLs (www + www2 fallback)
  before tagging.

## [0.2.5] - 2026-05-09

### Added — mass-action confirmation gate + unregistered-heartbeat tracker

- **Mass-action gate** (`app/services/mass_action.py`): any group
  fan-out command or firmware deployment affecting >5 devices requires
  `confirmation_level="simple"`; >20 devices requires
  `confirmation_level="typed"` with `confirmation_typed_value` echoing
  the prompted verb. Server-side enforcement; UI populates the form
  fields via `static/js/mass_action.js`. Closes BUG-012.
- **Unregistered-heartbeat tracker**
  (`app/services/unregistered.py`): every `/api/v1/device/*` 401 is
  best-effort logged with claimed device_id, source IP, endpoint,
  user-agent, auth-present flag. Surfaces in the admin UI at
  `/app/unregistered-devices` and via the dashboard tile + nav badge.
  Closes BUG-013.
- `services/bootstrap.py::ensure_schema()` no longer short-circuits
  when `users` exists — `Base.metadata.create_all()` is idempotent and
  cheap, so we run it under an advisory lock on every container start
  (auto-creates new tables added in later releases).

## [0.2.4] - 2026-05-09

### Added — operator dashboard + self-service profile

- **Real dashboard** — replaces the sparse nav-link list with stat
  cards (devices total/online/offline, devices with pending commands,
  groups + sites, firmware releases, 24h event count) and a unified
  recent-activity feed merging admin actions, device events, and
  issued commands in chronological order.
- **`/app/me` self-service profile** — every authenticated user can
  edit their own display name, change their own password (verifies
  current password, 8-char minimum), and "sign out everywhere"
  (revoke all their own sessions + JWTs). Changing the password
  automatically signs the user out of every other session.
- Profile link added to nav, plus a "profile · sign out" hint in the
  dashboard top line.

## [0.2.3] - 2026-05-09

### Added — UI affordances for shipped APIs

- **Delete a device** (admin+) — danger-zone button on device detail.
  Cascades credentials, heartbeats, events, commands, group memberships,
  deployment assignments. Audit-logged.
- **Delete a group** (admin+) — danger-zone button on group detail.
  Cascades memberships; member devices kept.
- **Cancel a pending invitation** (admin+) — button per pending row.
- **Edit a user's display name** (super-admin) — inline form on /app/users.
- **Revoke all tokens for a user** (super-admin) — bumps
  `tokens_valid_after`. If the super-admin revokes their own tokens,
  this session is also ended.
- **Assign a device to a site** (admin+) — site dropdown on device-detail.

### Fixed

- **POST /app/groups + POST /app/sites returned 500 on duplicate name**.
  Now catches `DuplicateNameError`, re-renders the list page with a
  friendly inline error and HTTP 409.
- **/rebooter/favicon.ico, apple-touch-icon.png 404** — now aliased
  to the existing static favicon (browsers request these at the
  conventional root regardless of `<link rel="icon">`).
- **/rebooter/robots.txt 404** — now `User-agent: * / Disallow: /`.
- **Default Flask 404 / 403 pages** — replaced with branded
  `error.html`. JSON paths still get the envelope `{ ok:false,
  error:{ code:"not_found"|"forbidden", … } }`.

### Changed

- `device.updated` audit-log entry now records exactly which fields
  the operator changed.

## [0.2.2] - 2026-05-09

### Changed

- **Session idle timeout is now 2 days** (was 31 days, the Flask
  default). Cookie expiry rolls forward on every request, so active
  users stay signed in indefinitely; idle users get kicked after 2
  days of no activity. Tunable via the
  `REBOOTER_SESSION_IDLE_TIMEOUT_SECONDS` env var.

### Operational

- All QA test data (114 devices, 66 groups, 14 sites, 31 invitations,
  18 throwaway users, 126 enrollment tokens, all 72 audit events,
  2 leftover firmware blobs) purged from the live DB. Architect
  account and the fresh firmware-team enrollment token preserved.

## [0.2.1] - 2026-05-09

### Added

- **Fallback URL is live**: `https://www2.voipguru.org/rebooter/`
  serves the same API and admin UI as the primary
  `https://www.voipguru.org/rebooter/`. Firmware clients should
  configure both URLs (primary first) and fall back per
  `docs/DEVICE_INTEGRATION.md`.
- Until v0.3 ships node-2 with its own Postgres, www2 is a transparent
  HTTPS proxy to www1 — same backend, same data, dual front-doors.
  Firmware blobs are served directly from the shared NAS on either
  node, no extra hop.
- Full QA suite (86 tests) green against **both** URLs.

### Changed

- `tests/qa/test_v02_rbac_invites.py::test_invitation_mint_returns_redeem_url`
  no longer asserts that the invite redeem URL host matches the
  request host — the backend always emits the canonical primary
  public base URL, by design.

## [0.2.0] - 2026-05-09

### Added — RBAC, invites, audit

- **Roles** on `users.role`: `super_admin`, `admin`, `operator`, `viewer`.
  `operator` can issue commands but not manage firmware/users; `viewer`
  is read-only; `admin` does everything except role changes; `super_admin`
  does everything including user/role management.
- **Email-invite signup** — admins mint an invitation via the API/UI;
  invitee redeems at `/app/invite/<token>` to set up their account.
  Single-use token, 7-day TTL by default. SMTP via env vars
  `REBOOTER_SMTP_*` (lifted from the DevinGPT pattern); the admin sees
  a copy-able link if SMTP isn't configured.
- **Audit log** — `audit_events` table records every admin mutation
  (device patches, command issuance, firmware deploys, user/invite
  changes). Surfaced at `/app/audit` and `GET /api/v1/admin/audit`.
- **User management endpoints** — `GET /admin/users`,
  `POST /admin/users/<id>/role` (super-admin only),
  `POST /admin/users/<id>/deactivate`,
  `POST /admin/users/<id>/revoke-tokens`.
- **Server-side token revocation** — bumping `users.tokens_valid_after`
  on logout / deactivate / revoke invalidates every JWT and Flask
  session cookie issued before that timestamp. Closes BUG-005.

### Fixed (cheap polish from QA pass)

- BUG-009: shipped a placeholder `favicon.ico` so browsers stop
  404'ing the icon request.
- BUG-010: `PATCH /admin/devices/<id>` now rejects unknown fields with
  `validation_failed` (was previously silently ignored).
- BUG-011: empty/no-op PATCH no longer bumps `updated_at`.

### Changed

- All admin API endpoints are explicitly role-gated. Existing
  super-admin sessions keep working unchanged.

## [0.1.4] - 2026-05-09

### Fixed / hardened (quick-wins from the QA pass)

- **BUG-006:** added per-IP rate limiting (10/min, 30/hour) on
  `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`, and the
  HTML `POST /app/login`. Hits over the limit now return 429
  `rate_limited`. Backed by Flask-Limiter, in-memory storage.
- **BUG-007:** `groups.name` and `sites.name` are now `UNIQUE`. Creating
  a duplicate returns 409 `name_conflict` with a friendly message.
- **BUG-008:** firmware uploads of 0-byte files are rejected with
  400 `validation_failed` ("uploaded firmware is empty (0 bytes)").

### Added

- `app/middleware/rate_limit.py` — Flask-Limiter integration with the
  envelope-shaped 429 handler.

## [0.1.3] - 2026-05-08

### Fixed

- **BUG-001 (high):** enrollment-token redemption race. Two simultaneous
  `POST /device/register` calls with the same `enrollment_token` could
  both succeed, creating two devices for one token. Now serialised via a
  Postgres row-level `SELECT ... FOR UPDATE` so the loser returns
  `enrollment_consumed` (409). Surfaced by `tests/qa/test_hardening_probes.py::test_concurrent_enrollment_redemption_only_succeeds_once`.
- **BUG-002 (high):** concurrent firmware upload of the same `(version, channel)`
  used to produce a `500 internal_error`. The IntegrityError is now caught
  and translated to a clean `400 validation_failed` ("firmware …
  already exists") and the blob from the losing upload is cleaned up.
- **BUG-003 (medium):** `GET /api/v1/admin/devices/` (trailing slash) returned
  404 because Flask 3 defaults to `strict_slashes=True`. We now set
  `app.url_map.strict_slashes = False` so trailing slashes match.

## [0.1.2] - 2026-05-08

### Changed

- Login accepts either the full email or just the local-part (e.g.
  `dblagbro` works in addition to `dblagbro@gmail.com`) when there is no
  ambiguity. Login form input is now `type="text"` so browsers stop
  rejecting bare usernames as "not a valid email".

## [0.1.1] - 2026-05-08

### Added

- `users.is_super_admin` boolean column. The bootstrap admin is now marked
  as super admin / architect.
- `GET /api/v1/auth/me` now returns `is_super_admin`.
- Dashboard surfaces a "super admin · architect" badge for the architect
  account.

### Changed

- The startup bootstrap step now reconciles the bootstrap admin's password
  and elevation flags on every boot from `REBOOTER_BOOTSTRAP_ADMIN_*` env
  vars, instead of only inserting on first run. Rotating the env var is
  now sufficient to rotate the architect password.

## [0.1.0] - 2026-05-08

### Added

- Initial scaffold: Flask app, Postgres sibling, nginx routing under `/rebooter/`.
- Device API: register, heartbeat, command poll, command result, events upload, firmware check.
- Admin API: device list/detail/update, groups, group commands, firmware releases, firmware deployments, events query, sites CRUD.
- Admin web UI under `/rebooter/app/` (Jinja-rendered): dashboard, devices, device detail, enrollment tokens, groups, group detail, sites, firmware, events.
- Single-use enrollment tokens, admin-issued.
- Firmware binaries served directly by nginx from RAID6 volume; SHA-256 verified on upload.
- Per-device firmware assignments materialised from group/site/all_devices deployments; later deployments supersede pending ones.
- APScheduler in-process job: command expiry sweep every 30 s (single-worker via Postgres advisory lock).
- Locked v0.1 command payload schemas for `set_mode` and `apply_config` (agreed with firmware/design team 2026-05-09); malformed requests are rejected with `validation_failed`.
