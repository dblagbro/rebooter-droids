# Remediation Plan — post v0.1.2 QA pass

Generated 2026-05-08 from `docs/bug-log.md`. Items grouped by severity
and subsystem so we can pull a clean tranche per release.

## Release blockers (fixed before next firmware-team integration)

All shipped in **v0.1.3**:

- BUG-001 — enrollment-token race
- BUG-002 — firmware concurrent-upload 500
- BUG-003 — trailing-slash 404

Retest: full `tests/qa` suite (77 tests, ~38 s). Already green on
v0.1.3.

## v0.2 sprint (security + RBAC)

These are the highest-value next batch and align with the user-invite /
RBAC roadmap:

| ID | Item | Estimate |
|---|---|---|
| BUG-005 | Logout server-side revocation (session JTI table + refresh-token revocation list) | M |
| BUG-006 | Rate limiting on login + heartbeat (Flask-Limiter, in-memory bucket) | S |
| BUG-007 | Unique constraint on `groups.name`, `sites.name`; friendly 409 on conflict | XS |
| BUG-008 | Reject 0-byte firmware uploads; warn on suspiciously small (<8 KB) | XS |
| BUG-010 | Reject unknown fields in `PATCH /admin/devices/{id}` (or echo `ignored_fields`) | XS |
| ENH | Audit-log table (`audit_events`) with admin/device write events | M |
| ENH | RBAC roles: `super_admin`, `admin`, `operator`, `viewer` | M |
| ENH | Email-invite flow lifted from DevinGPT | M |

## v0.3+ hardening

| ID | Item |
|---|---|
| BUG-009 | Favicon + `<link rel="icon">` |
| BUG-011 | Skip `updated_at` bump on no-op PATCH |
| ENH | Optional signed-URL firmware downloads (per spec) |
| ENH | Per-site scoping for non-super admins |
| ENH | Backup/DR procedure for `data/pg/cluster` |
| ENH | Add `request_id` response header + emit to logs (for incident correlation) |
| ENH | Replace `Base.metadata.create_all()` first-boot with proper Alembic migration #1 |
| ENH | Mobile-client CORS policy (currently none) |
| ENH | k6/Locust load profile + nightly soak run |

## Architectural fixes (not local patches)

- **BUG-005 + RBAC + audit log are linked**. The same `audit_events`
  table benefits all three. Build them as one feature, not three.
- **Hub-style "every file <1,200 lines" lint**. We're well under but
  let's set a CI gate before refactor pressure climbs.
- **Test data isolation**: today the regression suite runs against
  prod. v0.2 should add a docker-compose profile for an isolated
  staging DB and let CI run there.

## New test coverage to add (already proposed in test-plan.md)

- Real-device happy path (pending firmware-team integration; first
  enrollment token already minted: `et_CPbXBxHZegApes5LJq04tpq02J4bGavW`).
- Firmware deployment supersede ordering (current code does it; needs an
  end-to-end test).
- APScheduler `expire_commands` wall-clock test (issue command with
  `ttl_seconds=5`, sleep 35, assert status=`expired`).
- Login rate-limit test once BUG-006 is fixed.
- Logout token-revocation test once BUG-005 is fixed.

## Recommended retest scope per fix

| Fix | Retest |
|---|---|
| BUG-001/002/003 (v0.1.3) | full `tests/qa` |
| BUG-005 logout revocation | `test_logout_does_not_revoke_cookie_server_side` flipped to `assert_revoked`, plus refresh-token reuse test |
| BUG-006 rate limit | `test_no_rate_limit_on_login` flipped to expect 429 after threshold |
| BUG-007 uniqueness | new `test_duplicate_group_name_rejected` |
| BUG-008 zero-byte | flip `test_zero_byte_firmware_upload…` to expect 400 |
| RBAC | new `test_operator_cannot_upload_firmware`, `test_viewer_cannot_send_command` |
| Email invite | new `test_admin_can_invite_and_invitee_redeems` |

## Severity → action mapping (heuristic)

- **critical / high** → fixed in same session as found, never deferred.
- **medium** → batched into next minor release.
- **low / enhancement** → backlogged, polished alongside related work.

## What this run did NOT cover (acknowledged gaps)

- Load / soak testing (no k6 yet).
- Visual regression on the admin UI.
- DR / backup drill on the Postgres volume.
- XSS payload probing on free-text fields (`notes`, `description`,
  enrollment hint, release notes). Quick to add — open ticket.
- IPv6 connectivity to the device API.
- Behaviour during a Postgres outage (currently we have no graceful
  degradation; admin endpoints would 500).
