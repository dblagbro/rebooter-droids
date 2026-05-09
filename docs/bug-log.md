# Bug Log

Format: `BUG-NNN — title` · severity · area · status. Each entry has
context, repro, expected, actual, evidence, cause, recommended fix.

Severities: `critical · high · medium · low · enhancement`.
Statuses: `open · fixed-in-vX.Y.Z · wontfix · monitoring · suspected`.

---

## BUG-001 — Enrollment token race: double-redeem creates two devices

- **Severity:** high (security / fleet integrity)
- **Area:** `app/services/enrollment.py::consume_enrollment_token`
- **Status:** **fixed in v0.1.3** (verified: `tests/qa/test_hardening_probes.py::test_concurrent_enrollment_redemption_only_succeeds_once`)
- **Repro:** issue two `POST /device/register` calls in parallel with the
  same `enrollment_token`. Before the fix both returned 201 and produced
  two separate `dev_…` records consuming the same one-shot token.
- **Expected:** exactly one 201, the other 409 `enrollment_consumed`.
- **Actual (pre-fix):** `[201, 201]` — single token, two devices, both
  with valid `device_token`s.
- **Cause:** the SELECT-then-UPDATE on `enrollment_tokens` ran in two
  read-then-write transactions with no row-level lock. Both saw
  `consumed_at IS NULL`, both inserted devices, both updated the token.
- **Fix:** `with_for_update()` on the SELECT serialises the redemption
  via Postgres row lock; the second transaction blocks until the first
  commits, then reads `consumed_at != NULL` and raises
  `EnrollmentError("enrollment_consumed")`.

## BUG-002 — Concurrent firmware upload returns 500 instead of 400

- **Severity:** high (operator-facing 500)
- **Area:** `app/services/firmware.py::upload_release`
- **Status:** **fixed in v0.1.3**
- **Repro:** two simultaneous uploads of the same `(version, channel)`.
- **Expected:** one 201, one 400 `validation_failed` ("firmware … already
  exists").
- **Actual (pre-fix):** one 201, one 500 `internal_error` because the
  `(version, channel)` uniqueness violation raised `IntegrityError`
  which fell through to the generic exception handler.
- **Fix:** wrap the DB insert in `try/except IntegrityError`, translate
  to `ValueError` (which the blueprint already maps to 400), and clean
  up the firmware blob the loser already moved into `data/firmware/`.

## BUG-003 — Trailing slash on admin endpoints returns 404

- **Severity:** medium (client compatibility)
- **Area:** Flask 3 default `strict_slashes=True`
- **Status:** **fixed in v0.1.3**
- **Repro:** `GET /api/v1/admin/devices/` (with trailing slash).
- **Expected:** 200 (or a 308 redirect to the no-slash form).
- **Actual (pre-fix):** 404. Anything that follows REST conventions and
  appends `/` will fail.
- **Fix:** `app.url_map.strict_slashes = False`.

## BUG-004 — Reported "create group → logged out", not reproducible in clean state

- **Severity:** suspected high → re-classified as **monitoring**
- **Area:** session / browser-state interaction
- **Status:** **monitoring** (could not reproduce in fresh Chromium)
- **Repro reported by operator on 2026-05-08 against v0.1.2.** Clicking
  *Create* on `/rebooter/app/groups` apparently redirected to the login
  form.
- **Expected:** redirect back to `/app/groups` with the new group listed.
- **Actual (clean repro):** form submitted, group created, no logout —
  see `tests/qa/test_ui_flows.py::test_create_group_does_not_log_user_out`.
- **Cause hypotheses (unconfirmed):**
  1. Stale session cookie carried across the v0.1.0→v0.1.1 deploy when
     the `users` schema gained `is_super_admin`. (We mitigated by adding
     `getattr(u, "is_super_admin", False)` and reconciling on bootstrap.)
  2. Multi-tab interaction: signed out in another tab, then submitted
     the form here.
  3. Browser cached a `/app/login` page from an earlier 302.
- **Watch:** if a similar report arrives, capture the browser network
  log before any retry. Add a `request_id` header to admin responses so
  reports can be cross-checked against server logs.

## BUG-005 — Logout does not revoke the session cookie server-side

- **Severity:** medium (security)
- **Area:** `app/blueprints/admin_ui.py::logout`, `auth.py::logout`
- **Status:** **open** (documented in `tests/qa/test_hardening_probes.py::test_logout_does_not_revoke_cookie_server_side`)
- **Detail:** Flask's signed session cookies are valid until their
  `Expires` regardless of `session.clear()` server-side. Anyone who
  obtained the cookie value before logout can keep using it for up to
  31 days (the default `session.permanent` lifetime). Same applies to
  the JWT refresh token (14 days, no revocation list).
- **Recommended fix:** keep a server-side `session_jti` table (or a
  Redis set) of issued session tokens; clear on logout. Add a
  refresh-token revocation list keyed on user_id+jti.

## BUG-006 — No rate limiting on login

- **Severity:** medium (security)
- **Area:** `app/blueprints/auth.py`, `admin_ui.py::login_submit`
- **Status:** **open**
- **Detail:** `tests/qa/test_hardening_probes.py::test_no_rate_limit_on_login`
  fires 10 instant bad-password attempts and they all return 401 with no
  delay. With argon2 hashing this isn't trivial CPU but it leaves the
  door open for a slow brute-force against any user account.
- **Recommended fix:** add `Flask-Limiter` (in-memory backend is fine
  for single-worker; Postgres if we go multi-worker).

## BUG-007 — Group / site names have no uniqueness constraint

- **Severity:** low (operator UX)
- **Area:** `app/models/groups.py::Group`, `app/models/sites.py::Site`
- **Status:** **open**
- **Detail:** A group called "Branch Routers" can be created twice; the
  UI list will show both, distinguishable only by their ULID.
- **Recommended fix:** add a unique constraint on `name` (or, for sites,
  `(name, parent_org_id)` if we add multi-tenancy). Catch the
  `IntegrityError` in the service layer and return a friendly 409.

## BUG-008 — 0-byte firmware upload accepted

- **Severity:** low (operator footgun)
- **Area:** `app/services/firmware.py::upload_release`
- **Status:** **open**
- **Detail:** Uploading a zero-byte file yields a successful release with
  `size_bytes: 0` and the well-known empty-file SHA-256. A device that
  downloaded it would brick or reject mid-flash.
- **Recommended fix:** reject `size_bytes < 1024` (or whatever realistic
  minimum) with `validation_failed`. Optionally sniff a magic-byte
  prefix matching the known firmware build.

## BUG-009 — Favicon 404 on every page load

- **Severity:** enhancement
- **Area:** `app/__init__.py`, `static/`
- **Status:** **open**
- **Detail:** Browsers fetch `/rebooter/favicon.ico` and get 404,
  cluttering the console.
- **Recommended fix:** add a static favicon and a `<link rel="icon">`
  in `templates/layout.html`.

## BUG-010 — `PATCH /admin/devices/{id}` silently ignores unknown fields

- **Severity:** low (API contract clarity)
- **Area:** `app/services/devices.py::update_device`
- **Status:** **open / by-design?**
- **Detail:** A PATCH with `{"is_admin": true}` returns 200 and leaves
  the device unchanged. We log no warning and surface no signal to the
  caller that the field was ignored. Easy footgun if a client thinks it
  patched something it did not.
- **Recommended fix:** either (a) reject unknown fields with
  `validation_failed`, matching `apply_config` behaviour, or (b) log
  and echo "ignored_fields" in the response.

## BUG-011 — Empty `PATCH /admin/devices/{id}` updates `updated_at`

- **Severity:** enhancement
- **Area:** `app/services/devices.py::update_device`
- **Status:** **open**
- **Detail:** A PATCH with `{}` bumps `updated_at` even though nothing
  changed. Trivial; recommend skipping the bump when the patch is a
  no-op.
