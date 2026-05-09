# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
