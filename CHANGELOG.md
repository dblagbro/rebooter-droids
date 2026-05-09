# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
