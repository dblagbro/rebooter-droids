# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
