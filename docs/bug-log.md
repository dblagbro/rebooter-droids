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
- **Area:** `app/blueprints/admin_ui.py::logout`, `auth.py::logout`,
  `app/middleware/admin_auth.py`
- **Status:** **fixed in v0.4.10** (verified: new
  `tests/qa/test_v0410_session_revoke_enforced.py` — revoked
  cookies and JWTs both denied at next request)
- **Detail:** v0.2.10 shipped the *write side* (sessions table +
  revoke_one + revoke_all_for_user) in shadow mode. The middleware
  ignored those rows until v0.4.10 flipped enforce-on. Auth path
  now consults `sessions.revoked_at` on every authenticated
  request; revoked rows fall through as unauthenticated. Legacy
  cookies / tokens without a `sid`/`jti` claim continue to
  authenticate (graceful fallback for any session minted before
  v0.2.10).

## BUG-006 — No rate limiting on login

- **Severity:** medium (security)
- **Area:** `app/blueprints/auth.py`, `admin_ui.py::login_submit`
- **Status:** **fixed in v0.1.4** (verified: in-window 30×401 + 5×429 from a
  non-exempt source; `tests/qa/test_hardening_probes.py::test_login_rate_limit_kicks_in`)
- **Detail:** Flask-Limiter shipped at v0.1.4 with `30 per minute; 200
  per hour` on `/api/v1/auth/login` and `/api/v1/auth/refresh`. v0.4.4
  added `REBOOTER_RATE_LIMIT_EXEMPT_IPS` for the QA host so a full
  suite run (~50 logins) doesn't burn the per-IP budget; the exempt
  list is empty by default in any internet-facing deployment.

## BUG-007 — Group / site names have no uniqueness constraint

- **Severity:** low (operator UX)
- **Area:** `app/models/groups.py::Group`, `app/models/sites.py::Site`
- **Status:** **fixed (already shipped, status corrected v0.4.10)**
  — DB-level `groups_name_key` + `sites_name_key` UNIQUE
  constraints; service layer catches IntegrityError and returns
  HTTP 409 `name_conflict` with a friendly message. Verified
  live 2026-05-09.

## BUG-008 — 0-byte firmware upload accepted

- **Severity:** low (operator footgun)
- **Area:** `app/services/firmware.py::upload_release`
- **Status:** **fixed (already shipped, status corrected v0.4.10)**
  — `if size == 0: raise ValueError("uploaded firmware is empty
  (0 bytes)")` already gates this path. Returns 400
  `validation_failed`.

## BUG-009 — Favicon 404 on every page load

- **Severity:** enhancement
- **Area:** `app/__init__.py`, `static/`, `templates/layout.html`
- **Status:** **fixed (already shipped, status corrected v0.4.10)**
  — `static/favicon.ico` shipped + `<link rel="icon"
  href="{{ url_for('static', filename='favicon.ico') }}">` in
  layout. Both `/rebooter/favicon.ico` and `/rebooter/static/favicon.ico`
  return 200.

## BUG-010 — `PATCH /admin/devices/{id}` silently ignores unknown fields

- **Severity:** low (API contract clarity)
- **Area:** `app/services/devices.py::update_device`
- **Status:** **fixed (already shipped, status corrected v0.4.10)**
  — PATCH now returns 400 `validation_failed` enumerating both
  the unsupported field and the allowed set, e.g.: *"unsupported
  PATCH fields: ['is_admin']. Allowed: ['central_management_enabled',
  'display_name', 'is_protected', 'notes', 'site_id']"*.

## BUG-011 — Empty `PATCH /admin/devices/{id}` updates `updated_at`

- **Severity:** enhancement
- **Area:** `app/services/devices.py::update_device`
- **Status:** **fixed (already shipped, status corrected v0.4.10)**
  — `update_device` only bumps `updated_at` when the diff
  actually changes a field. Empty PATCH and "set X to its
  current value" are both no-ops on `updated_at`.

## BUG-012 — No mass-action confirmation gate (fixed in v0.2.5)

- **Severity:** medium (operator safety)
- **Area:** `app/blueprints/admin_ui.py`, `app/blueprints/admin_api.py`,
  `app/services/mass_action.py` (new)
- **Status:** **fixed in v0.2.5**
- **Detail:** A super-admin who clicked "Fan out relay_cycle" on a group
  with a hundred members had no client-side or server-side prompt; one
  click could relay-cycle the whole estate. Equally, a single
  `POST /api/v1/admin/firmware/deployments` with `target_type=all_devices`
  could OTA the fleet with no confirmation.
- **Fix:** server-side gate in `app/services/mass_action.py`. Thresholds:
  `target_count <= 5` → no confirmation; `5 < N <= 20` → caller must
  supply `confirmation_level=simple`; `N > 20` → caller must supply
  `confirmation_level=typed` AND `confirmation_typed_value` matching
  the verb (case-sensitive). UI sets the form fields via a small JS
  helper (`static/js/mass_action.js`) — server is source of truth.
- **Audit log:** new event types `group.mass_command_issued` and
  `firmware.mass_deployment_issued` capture `target_count`,
  `fan_out_count`, and the confirmation level used.

## BUG-013 — Unregistered firmware heartbeats invisible (fixed in v0.2.5)

- **Severity:** medium (visibility / firmware bring-up)
- **Area:** `app/middleware/device_auth.py`,
  `app/services/unregistered.py` (new),
  `app/models/unregistered.py` (new)
- **Status:** **fixed in v0.2.5**
- **Detail:** First-real-device incident, 2026-05-09: firmware shipped
  with a hard-coded `device_id` (`dev_01KR5EXMVJ7028D5PSAKEV6KWB`) that
  had never gone through `POST /api/v1/device/register`. The device
  spent hours hitting `/api/v1/device/heartbeat` from 47.230.251.21
  with `User-Agent: ESP8266HTTPClient`, every call returning 401.
  The 401s were buried in nginx logs and there was no UI surface for
  ops to spot the loop.
- **Fix:** new `unregistered_auth_attempts` table aggregates one row
  per `(claimed_device_id, source_ip, endpoint)` with hit count and
  last-seen timestamps. The `device_auth_required` middleware records
  every 401 (best-effort, never raises). Admin UI surface at
  `/app/unregistered-devices` plus a dashboard tile and nav badge with
  60-min count. API at `GET /api/v1/admin/unregistered-devices`.
  Rolling cap of 5000 rows; oldest 10% pruned on overflow.

---

## Findings from 2026-05-09 PM deep regression (post v0.4.2)

Triggered by operator question "why no devices show online?" plus a
demand for a senior-SDET-style deep regression sweep. Documented in
order found.

### BUG-021 — Test suite is non-isolated; one logout poisons the session-scoped admin token

- **Severity:** high (test infrastructure / CI flakiness)
- **Area:** `tests/qa/conftest.py::admin_token` (scope="session"),
  `tests/qa/test_v037_stale_cookie_no_loop.py`,
  `tests/qa/test_hardening_probes.py`,
  `tests/qa/test_v0210_session_shadow.py`
- **Status:** **fixed in v0.4.4 + v0.4.6**
  (verified clean: 240 passed, 2 skipped, 0 failed full-suite run
  on 2026-05-09 PM)
- **Fix:** new `disposable_admin_session` conftest fixture mints a
  fresh admin user via the invitation flow; the three tests that
  call `/api/v1/auth/logout` against an admin (`test_v037_stale_cookie_no_loop`'s
  two cases + `test_v0210_session_shadow::test_logout_does_not_break_subsequent_login`)
  now use this fixture so the bootstrap admin's `tokens_valid_after`
  is never bumped mid-suite. v0.4.6 fixed the fixture's
  user-creation path (was using a non-existent `POST /api/v1/admin/users`
  endpoint; now mints + redeems an invitation).
- **Repro:** `python3 -m pytest tests/qa/` — 34 tests fail. Re-run
  the same files in isolation: only 7 fail.
- **Cause:** `admin_token` fixture is `scope="session"` and shared
  across every test that needs admin auth. Several tests
  (`test_v037_stale_cookie_no_loop`, `test_logout_does_not_revoke...`,
  invitation-redeem flow) call `POST /api/v1/auth/logout` for the
  bootstrap admin. The logout handler calls `revoke_all_tokens(user_id)`
  which bumps `users.tokens_valid_after`. Every previously-issued JWT
  with `iat < tokens_valid_after` then 401s for the rest of the run.
  Tests that come afterwards (alphabetically — `test_v02_rbac_invites`,
  `test_v028_fixture_isolation`, `test_v029_per_record_audit`,
  half of `test_ui_flows`) cascade-fail with `KeyError: 'data'`
  because they read `r.json()["data"]` from a 401-bodied error envelope.
- **Evidence:** isolated-file run: 1 real failure
  (`test_invitation_redeem_creates_user_and_signs_in` — different
  cookie name, BUG-026). Full-suite run: 34 failures with same root.
- **Recommended fix:** drop `admin_token` to `scope="function"` (and
  rename uses) OR allocate a separate test user (`qa-admin@…`) whose
  tokens_valid_after bumps don't touch the bootstrap admin's. The
  latter is cleaner because test_v037 *needs* a logout flow to assert
  on, but that user shouldn't be the same one the rest of the suite
  authenticates as.

### BUG-022 — No "Sign out" link in the persistent header

- **Severity:** high (operator UX / security hygiene)
- **Area:** `templates/layout.html` (header `topbar-actions`)
- **Status:** **fixed in v0.4.3**
  (verified: post-deploy Playwright walkthrough finds Sign out
  link in the header on every authenticated page)
- **Repro:**
  1. Log in as super-admin.
  2. Look at the top header on any page (`/app/`, `/app/devices`,
     anywhere).
  3. There is no "Sign out" or "Log out" link visible.
- **Expected:** the most-frequent terminal action (sign out) is
  reachable in one click from every page. Industry-standard UX
  pattern.
- **Actual:** the header shows `Rebooter` brand, the 5-tab nav, the
  version chip, and `@me` (a barely-discoverable text link to the
  Profile page). The operator must click `@me`, find the Sign out
  link inside Profile, and click that.
- **Evidence:**
  ```
  $ grep -rn "Sign out\|/logout" templates/
  templates/me.html: …  (only)
  ```
  Confirmed via Playwright walkthrough: header HTML contains no
  logout reference.
- **Cause:** v0.3.0 redesign collapsed the header to 5 nav items +
  version + `@me`. The original header had a user-menu
  dropdown with Sign out; the redesign removed it and the link
  was relegated to Profile.
- **Recommended fix:** add `<a href="{{ url_for('admin_ui.logout') }}"
  class="signout">Sign out</a>` to `topbar-actions` in
  `templates/layout.html`. Keep the Profile link too. Mirror in
  `bottomnav` for mobile (or keep mobile via Settings → Profile,
  acceptable since mobile pattern is well-understood).

### BUG-023 — No super-admin role badge in the persistent header

- **Severity:** medium (operator awareness / blast-radius hint)
- **Area:** `templates/layout.html`
- **Status:** **fixed in v0.4.3**
  (verified: red `super admin` badge renders in `topbar-actions`
  on every authenticated page for super-admin users; neutral
  `admin` badge for plain admins)
- **Repro:** log in as super-admin. The header gives no visual
  indication of the elevated role. A super-admin can mass-action
  the entire fleet; they should see the role indicator constantly.
- **Expected:** a small `<span class="badge red">super admin</span>`
  (or similar) in the top-right area, visible on every page.
- **Actual:** no role indicator anywhere in the header; only on the
  Profile page.
- **Recommended fix:** in `topbar-actions`, render
  `{% if current_user.role == 'super_admin' %}<span class="badge red">super admin</span>{% endif %}`.

### BUG-024 — Smoke / regression tests stale after v0.3.x redesign

- **Severity:** medium (false alarms during release validation)
- **Area:** `tests/qa/test_ui_flows.py`,
  `tests/qa/test_v02_rbac_invites.py`,
  `tests/qa/test_hardening_probes.py`
- **Status:** **fixed in v0.4.4 + v0.4.5**
  (verified: stale assertions updated for v0.3+ shapes —
  Status page title, `rebooter_session` cookie name)
- **Detail:** Several tests assume v0.2.x UI shapes that v0.3.x
  intentionally replaced:
  - `test_login_logout_round_trip` asserts page title contains
    `"Dashboard"` — title is now `"Status - Rebooter-Droids"`
    (v0.3.1 R-DSH-1 status-page replacement).
  - `test_dashboard_shows_super_admin_badge` looks for "super admin"
    in body — **will pass once BUG-023 lands**, currently fails
    because the badge was never re-added.
  - `test_back_button_after_logout_does_not_resurrect_session` waits
    for a `Sign out` link — same root as BUG-022.
  - `test_invitation_redeem_creates_user_and_signs_in` checks for
    cookie name `'session'` — actual name is `'rebooter_session'`
    (v0.3.3 cookie-domain rework).
- **Recommended fix:**
  1. Update the four named tests to match the v0.3+ UI shapes.
  2. After BUG-022 and BUG-023 land, re-validate the badge + sign-out
     assertions.
  3. Add a release-checklist item: "after a UI redesign, sweep
     `tests/qa/test_ui_flows.py` and the v0.2.x-named tests for
     stale assertions."

### BUG-025 — `test_login_rate_limit_kicks_in` needs ≥80s timeout

- **Severity:** low (test config)
- **Area:** `tests/qa/test_hardening_probes.py::test_login_rate_limit_kicks_in`,
  pyproject.toml pytest timeout config
- **Status:** **fixed in v0.4.4**
  (`@pytest.mark.timeout(120)` + post-burst 65s sleep added.
  Test additionally skips cleanly when the source IP is
  exempt — see BUG-006 fix.)
- **Detail:** The test fires 35 login attempts, sleeps 60 s for the
  rate-limit window to clear, then probes once more. Total wall
  time is ~62 s, exceeding the default `--timeout=60`. Rate limit
  itself works correctly (verified independently — 30×401 + 5×429
  in a fresh window).
- **Recommended fix:** mark the test
  `@pytest.mark.timeout(120)` or move the sleep-and-recheck step
  into a separate test that runs only on a `--longrun` flag.

### BUG-026 — Invitation-redeem test asserts wrong cookie name

- **Severity:** low (stale test)
- **Area:** `tests/qa/test_v02_rbac_invites.py:65`,
  `tests/qa/test_ui_flows.py`,
  `tests/qa/test_hardening_probes.py`
- **Status:** **fixed in v0.4.4 + v0.4.5**
  (assertion updated to `rebooter_session` cookie name throughout)
- **Detail:** Asserts `'session' in cookies`; actual cookie is
  `'rebooter_session'` (v0.3.3 cookie-domain rework).
- **Recommended fix:** change the assertion to
  `'rebooter_session' in s.cookies`.

### BUG-027 — Central host cannot reach lab device subnet directly

- **Severity:** medium (operability / diagnostic blind spot)
- **Area:** infrastructure / network topology
- **Status:** **environmental** (no code change can fix; document)
- **Detail:** The container running rebooter-droids sits on
  192.168.18.0/24 (default gw 192.168.18.1). The lab devices are
  on 192.168.1.0/24. From this host, RIGHT NOW (2026-05-09 23:01 UTC):
  - 192.168.1.67 — TCP-80 timeout
  - 192.168.1.225 — TCP-80 timeout
  - 192.168.1.207 — TCP-80 connection refused (host up, no http)
  - 192.168.1.30 — TCP-80 timeout
- **Operator-relevant consequence:** any "is the device reachable?"
  diagnostic added to the central UI will be blind to the actual
  device-side state. The firmware team's "all 4 HTTP OK" report
  from earlier today was per their own probe inside the device
  subnet; from central's network position those devices are not
  reachable.
- **Recommended:**
  1. **Don't** ship a "ping device from central" feature — it would
     produce false negatives constantly.
  2. **Do** ship a "device → central reachability beacon" — let the
     device push a tiny "I tried to reach you from <my LAN IP> at
     <UTC>" event regardless of registration state, recorded in the
     unregistered-attempts table. Then central UI can show "we last
     heard from this device-claimed-id N seconds ago, even though
     it has no valid token."
  3. **Document** this network split in `docs/architecture.md` so a
     future operator doesn't ask the same question.

### BUG-028 — Settings → Authentication tab title doesn't reflect tab

- **Severity:** low (UX polish)
- **Area:** `templates/settings/auth.html`
- **Status:** **fixed in v0.4.3**
  (page title is now "Authentication settings - Rebooter-Droids")
- **Detail:** Other settings tabs (System, Network, Sync,
  Notifications, Theme) render their tab name in the page `<title>`
  ("System settings - Rebooter-Droids", etc.). The Authentication
  tab renders just "Authentication - Rebooter-Droids" (no
  "settings" qualifier). Trivial.
- **Recommended fix:** update the `{% block title %}` line in
  `templates/settings/auth.html` to "Authentication settings - Rebooter-Droids".

### BUG-002a — Concurrent firmware upload returns 500 (regressed v0.3.9, fixed v0.4.5)

- **Severity:** high (operator-facing 500)
- **Area:** `app/services/firmware.py::upload_release`
- **Status:** **fixed in v0.4.5** (regression — was originally
  fixed in v0.1.3 as BUG-002)
- **Detail:** v0.3.9 (RFC-002 P1 firmware mirror chain) replaced
  the static channel-pointer file with a Flask 302-redirect
  endpoint. The IntegrityError-recovery cleanup branch in
  `upload_release` still referenced the now-removed
  `pointer_path` variable. Loser thread of a concurrent upload
  race hit `NameError: pointer_path is not defined` → unhandled
  → 500 instead of the expected 400 "already exists".
- **Caught by:** existing
  `tests/qa/test_hardening_probes.py::test_concurrent_firmware_upload_same_version`
  passing in 4 of 5 runs (race-dependent).
- **Fix:** removed `pointer_path` from the cleanup tuple. Now
  iterates only `(final_path, per_channel_path)` — the only
  on-disk artifacts in v0.3.9+.

### BUG-030 — Forgot-password 500 on SMTP failure (fixed v0.4.6)

- **Severity:** high (operator-facing 500)
- **Area:** `app/blueprints/admin/auth_ui.py::forgot_password_submit`
- **Status:** **fixed in v0.4.6**
- **Detail:** v0.4.1 introduced the password-reset flow. The
  POST handler called `send_password_reset_email` synchronously
  WITHOUT a try/except. When SMTP fails (currently the live
  deployment hits `SMTPServerDisconnected` from
  smtpauth.earthlink.net because the configured
  `REBOOTER_SMTP_PASSWORD` is the bootstrap admin's password
  rather than a valid EarthLink SMTP credential), the exception
  bubbles up to Flask → 500. Defeats the entire non-disclosing
  design of the flow — a 500 leaks "this email exists, but
  email delivery failed" to anyone probing.
- **Fix:** wrap `send_password_reset_email` in try/except. The
  password-reset token is still minted in the DB; the audit-log
  entry now records `smtp_ok=false` + `smtp_error=<exception
  name>`; the user sees the same non-disclosing confirmation
  page either way. Operator can recover the URL from audit
  history if needed.
- **Operational follow-up:** `REBOOTER_SMTP_PASSWORD` needs to
  be set to a real EarthLink SMTP credential (not the bootstrap
  admin's password) for password-reset and invite emails to
  actually deliver. Audit log will say `smtp_ok=true` once that
  happens.

### BUG-050 — Device register 500 on overlong fields (fixed v0.4.18)

- **Severity:** medium (operator-facing 500)
- **Area:** `app/services/enrollment.py::consume_enrollment_token`
- **Status:** **fixed in v0.4.18**
- **Detail:** Caller-supplied registration fields had no length
  validation. `display_name` >120 chars,
  `mac_address` >40, `hardware_model` >80, `firmware_version`
  >40, `local_ip` >64, `serial_number` >80, `hardware_revision`
  >40 — all hit Postgres `StringDataRightTruncation` on INSERT
  and 500'd. A misbehaving firmware sending a long version
  string would brick its own enrolment.
- **Fix:** column-width-aware validation in
  `consume_enrollment_token`. Returns 400 `validation_failed`
  with the field name + max length.

### BUG-051 — Device register accepts garbage MAC address (fixed v0.4.18)

- **Severity:** low (data integrity)
- **Area:** `app/services/enrollment.py::consume_enrollment_token`
- **Status:** **fixed in v0.4.18**
- **Detail:** `mac_address` had no format validation.
  `<script>alert(1)</script>` was persisted verbatim. Jinja
  autoescape covers the rendering surface so XSS was not
  possible, but operators saw nonsense values in the MAC
  column and there was no signal to the firmware that it was
  sending bad data.
- **Fix:** hex-only regex `[0-9A-Fa-f:.\-\s]+` covers all
  common MAC formats (colon, hyphen, dot-separated, vendor
  variants). Anything else → 400.

### BUG-044 — No API DELETE endpoint for enrollment tokens (fixed v0.4.17)

- **Severity:** low (API consistency)
- **Area:** `app/blueprints/admin/enrollment_tokens.py`
- **Status:** **fixed in v0.4.17**
- **Detail:** Pre-v0.4.17 the only revoke path was the UI form
  POST `/app/enrollment-tokens/<id>/revoke`. API consumers
  could not programmatically revoke tokens (DELETE returned 404
  Not Found). Caught during the 2026-05-09 PM cleanup when
  trying to revoke 3 leftover test tokens from this very
  iteration loop.
- **Fix:** new `DELETE /api/v1/admin/enrollment-tokens/<id>`
  matching the UI semantics (hard-deletes only when not yet
  consumed; consumed tokens are kept for audit).

### BUG-048 — HTTP watchdog probe treats 3xx as failure (fixed v0.4.17)

- **Severity:** medium (false-positive watchdog alerts)
- **Area:** `app/services/watchdog_runtime.py::_probe_http`
- **Status:** **fixed in v0.4.17**
- **Detail:** Pre-fix the probe checked `200 <= status < 300`.
  Health-check URLs that legitimately redirect (HTTPS upgrades
  via 301, app-root → /app/ patterns via 302, CDN routing) all
  returned a 3xx that the probe scored as failure. Operator
  using `https://example.com/` as a probe URL would see
  watchdog rules fire spuriously every cycle.
- **Fix:** follow up to 3 redirects (with loop-detection on
  the URL set) and treat a final 2xx as success. Bare 3xx with
  no Location header still treated as failure.

### BUG-046 — Bootstrap admin password reverts on container restart (fixed v0.4.16)

- **Severity:** high (operator footgun + auth surprise)
- **Area:** `app/services/bootstrap.py::ensure_bootstrap_admin`
- **Status:** **fixed in v0.4.16**
- **Detail:** `ensure_bootstrap_admin` had a "always reconcile
  to env var" branch that force-overwrote the bootstrap admin's
  password on every container startup. So:
  1. Operator does password reset, sets new password "MyNew123".
  2. Container restarts (image update, host reboot, etc.).
  3. Bootstrap reads `REBOOTER_BOOTSTRAP_ADMIN_PASSWORD` env var
     and overwrites the user's password back to it.
  4. "MyNew123" stops working — no audit, no signal.
  5. Operator does another reset → loop.
  Caught from a real operator support call (2026-05-09 PM):
  10 reset attempts in succession because the new password kept
  reverting after each container restart triggered by the
  rapid-fire QA test cycles.
- **Fix:** default behavior is now "only set password on initial
  create". Privileges still reconciled every startup so the
  operator can never lock themselves out of admin. Legacy
  force-reset behavior available behind opt-in env var
  `REBOOTER_BOOTSTRAP_ADMIN_FORCE_PASSWORD_ON_STARTUP=1` for the
  "I forgot my password" recovery path.

### BUG-045 — Forgot-password page lied when SMTP failed (fixed v0.4.15)

- **Severity:** medium (UX / operator confusion)
- **Area:** `app/blueprints/admin/auth_ui.py::forgot_password_submit`,
  `templates/forgot_password.html`
- **Status:** **fixed in v0.4.15**
- **Detail:** v0.4.6 caught the SMTP exception (BUG-030) so the
  request didn't 500, but the response page kept the cheerful
  "we've emailed you a link" message regardless of whether
  delivery actually happened. Users (specifically the operator
  trying to reset their own password ~10 times in a row on
  2026-05-09 PM) sat clicking expired links because each new
  reset generated an email-or-not but the UI gave no signal.
  Audit log showed `smtp_ok: false` retroactively for the
  failed sends.
- **Fix:** when SMTP returns an error AND the email IS
  registered (token was minted), the response page renders a
  red warning panel naming the SMTP error class
  (`SMTPConnectError`, `SMTPRecipientsRefused`, etc.) and
  pointing the user at their admin.

### BUG-042 — Watchdog rule serializer missing v0.4.2 runtime state (fixed v0.4.14)

- **Severity:** medium (UI silent-render + API consumer breakage)
- **Area:** `app/services/watchdog.py::serialize_rule`
- **Status:** **fixed in v0.4.14**
- **Detail:** v0.4.0 shipped `serialize_rule`; v0.4.2 added
  five new columns (`failure_streak`, `recovery_streak`,
  `last_probed_at`, `last_action_at`, `last_outcome`) but the
  serializer was never updated. UI templates referenced these
  fields and rendered as empty strings (silent UX regression);
  API consumers reading `rule["last_outcome"]` got `KeyError`.
- **Fix:** `serialize_rule` now exposes all five runtime-state
  fields with safe defaults via `getattr(..., None)`.
- **Caught by:** v0.4.14 wall-clock e2e test
  (`test_v0414_watchdog_runtime_e2e.py`) which asserts the
  fields are present in the response after a real tick.

### BUG-043 — Enrollment-token mint ignores caller-supplied TTL (fixed v0.4.14)

- **Severity:** low (operator UX)
- **Area:** `app/services/enrollment.py::mint_enrollment_token`,
  `app/blueprints/admin/enrollment_tokens.py::create_enrollment_token`
- **Status:** **fixed in v0.4.14**
- **Detail:** The API accepted a `ttl_seconds` field in the body
  but the service silently ignored it, always using
  `settings.enrollment_token_ttl_seconds` (env-var default 24 h).
  Operators wanting a 30-day token for a firmware-team handoff
  had to redeploy the whole container with a bumped env var.
- **Fix:** `mint_enrollment_token` accepts optional
  `ttl_seconds`, capped at 30 days. API/UI handlers thread the
  body field through.

### BUG-038 — Watchdog rule target accepts kind without identifier (fixed v0.4.13)

- **Severity:** medium (silent runtime no-op)
- **Area:** `app/services/watchdog.py::create_rule`
- **Status:** **fixed in v0.4.13**
- **Detail:** `target={"kind":"device"}` (no `id` key) was
  accepted on rule create. The runtime's
  `_resolve_target_devices` returned `[]` for that shape, so
  the rule appeared to fire but did nothing. Operators could
  spend minutes debugging "why isn't this rule cycling my
  device?".
- **Fix:** service requires `target.id` for device/group, and
  `target.tag` for tag. Empty strings rejected too.

### BUG-040 — Weekly schedule duplicate weekdays render as "Sat, Sat, Sat" (fixed v0.4.13)

- **Severity:** low (UX polish)
- **Area:** `app/services/schedules.py::create`
- **Status:** **fixed in v0.4.13**
- **Detail:** `weekdays=[5,5,5,5]` was stored verbatim and the
  sentence renderer emitted "every week on Sat, Sat, Sat, Sat".
- **Fix:** dedupe + sort weekdays before persist.

### BUG-041 — Weekly schedule accepts out-of-range weekdays (fixed v0.4.13)

- **Severity:** medium (rule never fires, no operator signal)
- **Area:** `app/services/schedules.py::create`,
  `compute_next_run_at`
- **Status:** **fixed in v0.4.13**
- **Detail:** `weekdays=[99]` was accepted. `compute_next_run_at`
  walked forward 7 days looking for a match and returned None.
  Schedule sat in the table forever, never firing, with no
  operator-visible signal.
- **Fix:** range-check `0 <= d <= 6` in the service layer →
  400 `validation_failed`.

### BUG-035 — Watchdog rule numeric thresholds unbounded (fixed v0.4.12)

- **Severity:** medium (operator footgun + state-machine break)
- **Area:** `app/services/watchdog.py::create_rule`
- **Status:** **fixed in v0.4.12**
- **Detail:** Pre-fix, an operator could create a rule with
  `failure_threshold=-1` and the runtime would fire its action
  on the very first probe (the `failure_streak < failure_threshold`
  gate is always False). Equally, `window_seconds=99_999_999`
  passed silently — sentence renderer rendered "27777 h 46 min"
  with a straight face.
- **Fix:** `1..100` for thresholds, `5..86400` for windows,
  `0..86400` for cooldown. Out-of-range values → 400
  `validation_failed`.

### BUG-036 — Watchdog/schedule name >120 chars returns 500 (fixed v0.4.12)

- **Severity:** medium (operator-facing 500)
- **Area:** `app/services/watchdog.py::create_rule`,
  `app/services/schedules.py::create`
- **Status:** **fixed in v0.4.12**
- **Detail:** Both `watchdog_rules.name` and `schedules.name`
  are `VARCHAR(120)`. A 121-char name hit
  `psycopg.errors.StringDataRightTruncation` on INSERT →
  unhandled → 500.
- **Fix:** service-layer length check returns 400
  `validation_failed` with message `"name must be 120 characters
  or fewer"`.

### BUG-037 — Maintenance `reason` field unbounded (fixed v0.4.12)

- **Severity:** low (UX / DoS-by-large-value)
- **Area:** `app/services/runtime_flags.py::set_maintenance_mode`
- **Status:** **fixed in v0.4.12**
- **Detail:** `reason` is stored inside a JSON value, so Postgres
  doesn't column-truncate. An operator could paste a 5KB blob
  and watch the Status banner render the whole thing. Equally a
  silent footgun if a schedule writes a multi-line reason.
- **Fix:** truncate to 200 chars (197 + `"..."`) before persist.

### BUG-033 — No standard security headers on responses (fixed v0.4.11)

- **Severity:** medium (security hardening)
- **Area:** `app/__init__.py`
- **Status:** **fixed in v0.4.11**
- **Detail:** Pre-v0.4.11 every response shipped without
  `X-Frame-Options`, `X-Content-Type-Options`,
  `Strict-Transport-Security`, `Referrer-Policy`, or CSP.
  Allowed clickjacking via `<iframe>` embed, MIME-sniff attacks,
  weakened transport security on subdomains.
- **Fix:** `@app.after_request` hook attaches all five with
  conservative defaults. CSP is `default-src 'self'` plus inline
  scripts/styles (the v0.3+ Jinja templates embed inline
  `<script>` blocks; will tighten when those are extracted).

### BUG-034 — Schedule with malformed `at_time_utc` returns 500 (fixed v0.4.11)

- **Severity:** medium (operator-facing 500)
- **Area:** `app/services/schedules.py::create`
- **Status:** **fixed in v0.4.11**
- **Detail:** Posting `{at_time_utc: "not-a-time"}` (10 chars)
  to `POST /api/v1/admin/schedules` failed the Postgres insert
  with `DataError: value too long for type character varying(5)`
  → unhandled → 500. Discovered during the v0.4.10 bug-iteration
  sweep.
- **Fix:** validate the `HH:MM` shape regex + range in the
  service before insert. Returns 400 `validation_failed` with
  message `"at_time_utc must be HH:MM (00:00 to 23:59)"`.

### BUG-031 — Watchdog rule JSON editor loses input on validation failure (fixed v0.4.10)

- **Severity:** low (operator UX)
- **Area:** `app/blueprints/admin/rules.py::rules_create_json_submit`
- **Status:** **fixed in v0.4.10**
- **Repro:** Paste a 200-line JSON rule body into
  `/app/rules → Advanced → Rule JSON`, introduce a typo, hit
  Submit. Pre-v0.4.10 the redirect-after-flash pattern threw
  away the textarea contents — operator had to re-paste the
  entire body.
- **Fix:** validation failures now re-render the rules page with
  the JSON pre-filled in the textarea and the error message
  rendered inline (no redirect).

### BUG-032 — Schedule-vs-operator maintenance race (fixed v0.4.10)

- **Severity:** medium (operator-visible state surprise)
- **Area:** `app/services/schedule_runtime.py::_reconcile_maintenance_flag`
- **Status:** **fixed in v0.4.10**
- **Repro:** Configure a daily maintenance schedule (e.g. 02:00
  UTC, duration 1 h). During the schedule's window, an operator
  manually toggles maintenance OFF on the Status page. Pre-v0.4.10
  the schedule_tick reconciler observed `in_window=True,
  current=False` and re-flipped ON ~30 s later.
- **Fix:** `set_maintenance_mode` now stamps `operator_override_at`
  on any non-schedule write. The reconciler skips flipping ON
  when an operator override timestamp is at or after the active
  window's start. The override naturally lapses when the next
  scheduled window begins.

### BUG-029 — Operator complaint "no devices online" is environmental

- **Severity:** environmental (no code change)
- **Area:** firmware + LAN ops
- **Status:** **answered** (see RCA + this doc)
- **Detail:** As of 2026-05-09 22:50 UTC, the only row in `devices`
  is `dev_01KR7AN84ECMZDC0CA6A0D94HD` — `is_qa_fixture=true`,
  display_name "QA Device 23744", last_heartbeat_at 21:35:23 UTC
  (~75 minutes stale). **There are zero real devices in the system.**
  Per the RCA + firmware-team correspondence: the central server is
  healthy; the four lab devices are either local-only by design (3 of
  4) or have firmware-side poll-transport failure (1 of 4 on
  192.168.1.67). Per BUG-027, central can't even probe their LAN
  state from its current network position.
- **Action items emitted:**
  - The cross-team note
    `docs/notes/2026-05-09-to-firmware-team-get-devices-online.md`
    enumerates what we need from the firmware team to unblock device
    bring-up.
  - **B5** in `docs/BACKLOG.md` tracks this as the unblocker.

### BUG-052 - Devices page collapses transport-stale and truly-offline into the same "offline" state

- **Severity:** medium (operator diagnosis confusion)
- **Area:** devices list / heartbeat-state presentation
- **Status:** **open - intermittent live reliability issue**
- **Detail:** On the live hub snapshot taken about `2026-05-14T03:04Z`,
  device `192.168.1.225` (`Erica''s F.R Speaker`) was shown as
  **offline** in `/app/devices`, but its local API still returned
  `200` with:
  - `wifi_connected: true`
  - `central_enabled: true`
  - `central_registered: true`
  - `central_state: "firmware_check_transport_failed"`
  - `central_heartbeat_age_seconds: 509`
  The operator-facing UI currently makes this look identical to a
  truly dead or unreachable device such as `192.168.1.69`, which was
  timing out on its local API from the same workstation.
- **Recheck:** By about `2026-05-14T04:11Z`, the same live comparison
  no longer reproduced on `.225`: hub API showed `heartbeat_state:
  "online"` and local `/api/status` showed `central_state: "idle"`
  with `central_heartbeat_age_seconds: 25`. A truly unreachable unit
  (`192.168.1.69`) still timed out locally and remained `offline` in
  the hub. So the issue is now narrowed to an intermittent stale-state
  presentation / recovery gap rather than a currently stuck bad row.
- **Fix direction:** split the visible state model into at least:
  `online`, `central stale/transport failed`, and `offline`.

### BUG-053 - Desired-name drift persists on ordinary fleet devices outside restore-after-reflash

- **Severity:** medium (source-of-truth drift)
- **Area:** hub desired-config / rename propagation
- **Status:** **open - observed live 2026-05-14**
- **Detail:** Live comparison between `/api/v1/admin/devices` and
  local device APIs shows the renamed-test unit on `192.168.1.48`
  remains converged on recheck (`display_name`, `/api/status`, and
  `/api/config` all report `Rebooter - renamed test`), but multiple
  ordinary fleet devices still diverge:
  - hub `Erica''s Subwoofer` vs device `Rebooter` on `192.168.1.30`
  - hub `Erica''s F.R Speaker` vs device `Rebooter` on
    `192.168.1.225`
  - hub `Erica''s R.R. Speaker` vs device `Erica''s ?.?. Speaker` on
    `192.168.1.207`
  This proves the `.48` recovery/rename path remains healthy, but the
  broader "hub display_name is the desired device name" contract is
  still incomplete outside that path.
- **Fix direction:** desired-name propagation must run on ordinary
  rename / drift reconciliation, not only restore-after-reflash.

### BUG-054 - Renamed-test device intermittently times out on first local HTTP probe before recovering

- **Severity:** medium (live reliability issue)
- **Area:** device local web/API responsiveness
- **Status:** **open - observed live 2026-05-14**
- **Detail:** The renamed soak target on `192.168.1.48` remains healthy
  once it responds, but repeated live sweeps showed a first-contact
  local HTTP failure pattern before recovery:
  - around `2026-05-14T04:20Z` to `04:24Z`, the first two cycles of a
    5-probe loop timed out on one or more of `/`, `/api/status`, or
    `/api/config`, then the next three cycles recovered to stable
    `200`s
  - around `2026-05-14T04:33Z` to `04:35Z`, cycle 1 timed out on local
    `/api/status`, then cycles 2-5 returned `200` with
    `central_state: "idle"` and the renamed device identity intact
  - around `2026-05-14T04:52Z`, the hard timeout did not reproduce in a
    fresh 5-cycle loop, but the device still showed local HTTP latency
    spikes: one root-page fetch took about `1.1 s` and one
    `/api/status` read took about `3.2 s` before later cycles returned
    to normal
  During the same windows, the hub continued to show the device as
  `online`, so this is not a name-propagation failure; it is a local
  reachability / responsiveness flap on the device HTTP surface.
- **Latest recheck:** around `2026-05-14T05:11Z`, the issue reproduced
  again in a stronger first-contact form after the clean `05:00Z`
  window: local `/`, `/api/status`, and `/api/config` all timed out for
  the full 10 s timeout window on `192.168.1.48` while the live hub
  still showed the device `online`. An immediate follow-up recheck
  around `05:13Z` recovered to clean `200`s across a 5-cycle loop, but
  the first successful local root-page fetch after recovery was still
  slow at about `1.9 s`. This keeps the bug clearly open as an
  intermittent first-contact local HTTP failure/latency problem rather
  than a resolved one.
- **Later recheck:** around `2026-05-14T05:20Z` to `05:22Z`, the hard
  timeout shape did not reproduce. The initial `.48` local sweep plus a
  10-cycle follow-up loop both returned `200` from `/`, `/api/status`,
  and `/api/config`, while the live hub still showed the device
  `online` on `0.1.17-dev-central`. The bug remains open because the
  local root page still had one slower read at about `2.16 s` in that
  loop even though `/api/status` and `/api/config` stayed fast.
- **Latest recheck:** around `2026-05-14T05:40Z` to `05:42Z`, the issue
  narrowed further. The initial `.48` local sweep plus a fresh 5-cycle
  follow-up loop all returned `200`, with local root-page reads about
  `0.10 s`-`0.18 s`, `/api/status` about `0.02 s`, and `/api/config`
  about `0.07 s`-`0.09 s`. Keep the bug open because the same device
  showed repeated first-contact timeouts earlier in the morning, but
  this pass adds another clean window.
- **Latest recheck:** around `2026-05-14T06:00Z` to `06:01Z`, the issue
  again did not reproduce. The initial `.48` local sweep plus a fresh
  5-cycle follow-up loop all returned `200`, with root-page reads about
  `0.10 s`-`0.16 s`, `/api/status` about `0.02 s`, and `/api/config`
  about `0.06 s`-`0.09 s`. Keep the bug open because the earlier
  first-contact timeout windows and later latency spikes still happened
  on the same date, but this adds another clean post-recovery window.
- **Latest recheck:** around `2026-05-14T06:10Z` to `06:11Z`, the issue
  again did not reproduce. The initial `.48` local sweep plus a fresh
  5-cycle follow-up loop all returned `200`, with root-page reads about
  `0.15 s`-`0.29 s`, `/api/status` about `0.02 s`-`0.09 s`, and
  `/api/config` about `0.03 s`-`0.09 s`. Keep the bug open because the
  stronger timeout and latency shapes reproduced earlier on the same
  date, but this adds a second consecutive clean short-window recheck.
- **Latest recheck:** around `2026-05-14T06:20Z` to `06:21Z`, the issue
  reappeared in a weaker latency-only form. The live hub devices page
  and `/api/v1/admin/devices` still showed `.48` `online` on
  `0.1.17-dev-central`, local `/api/status` stayed about `0.02 s`, and
  local `/api/config` stayed about `0.06 s`-`0.08 s`, but the first
  local root-page read stretched to about `3.03 s`. An immediate
  5-cycle follow-up loop then stayed at `200`, with cycle 1 still
  slower at about `1.02 s` before cycles 2-5 settled back to about
  `0.09 s`-`0.13 s`. Treat this as renewed evidence that the local root
  UI path is still intermittently slow even when the JSON endpoints and
  hub heartbeat view remain healthy.
- **Latest recheck:** around `2026-05-14T06:31Z` to `06:33Z`, the issue
  strengthened again. The live hub devices page and
  `/api/v1/admin/devices` still showed `.48` `online` on
  `0.1.17-dev-central`, local `/api/status` stayed about `0.02 s`, and
  local `/api/config` stayed about `0.06 s`, but the first local root
  page read took about `10.29 s` before still returning `200`. An
  immediate 5-cycle follow-up loop then stayed clean, with root-page
  reads about `0.10 s`-`0.43 s`, `/api/status` about `0.02 s`-`0.03 s`,
  and `/api/config` about `0.03 s`-`0.09 s`. Treat this as the local
  root UI nearly hanging on first contact rather than a cleared issue,
  even though the JSON endpoints and hub heartbeat view remained
  healthy throughout.
- **Latest recheck:** around `2026-05-14T07:10Z` to `07:12Z`, the issue
  stayed in the weaker latency-only bucket rather than reproducing the
  earlier hard timeout shape. The live hub devices page and
  `/api/v1/admin/devices` still showed `.48` `online` on
  `0.1.17-dev-central`; the initial local sweep returned `200` with the
  root page at about `0.39 s`, `/api/status` about `0.04 s`, and
  `/api/config` about `0.06 s`; then cycle 1 of the immediate 5-cycle
  follow-up loop hit a slower root-page read at about `1.20 s` while
  `/api/status` stayed about `0.02 s`-`0.06 s` and `/api/config` stayed
  about `0.03 s`-`0.10 s`. Cycles 2-5 returned to about `0.13 s`-`0.20 s`
  on the root page. Treat this as another weaker first-hit local UI
  delay rather than a cleared issue, but still as improved relative to
  the earlier `10 s` timeout window and `10.29 s` near-hang.
- **Latest recheck:** around `2026-05-14T07:33Z` to `07:35Z`, the
  stronger first-contact timeout shape reproduced again after the clean
  `07:21Z` to `07:22Z` window. The live hub devices page and
  `/api/v1/admin/devices` still showed `.48` `online` on
  `0.1.17-dev-central`, but the first local sweep timed out for the
  full 10 s window on `/`, `/api/status`, and `/api/config`. An
  immediate 5-cycle follow-up loop then recovered cleanly at `200`,
  with local root-page reads about `0.10 s`-`0.14 s`, `/api/status`
  about `0.02 s`-`0.11 s`, and `/api/config` about `0.03 s`-`0.09 s`.
  Treat this as renewed evidence that the renamed soak target still has
  a first-contact local HTTP failure mode, not just the weaker
  root-page latency-only variant.
- **Latest recheck:** around `2026-05-14T07:40Z` to `07:42Z`, the
  stronger timeout shape did not repeat, but the weaker latency-only
  shape remained. The live hub devices page and `/api/v1/admin/devices`
  still showed `.48` `online` on `0.1.17-dev-central`; the initial
  local sweep returned `200` with the root page at about `0.37 s`,
  `/api/status` about `0.02 s`, and `/api/config` about `0.08 s`; then
  cycle 2 of the immediate 5-cycle follow-up loop hit a slower
  root-page read at about `1.54 s` before cycles 3-5 returned to about
  `0.16 s`-`0.26 s`. Treat this as another weaker first-hit local UI
  latency reproduction rather than a cleared issue.
- **Latest recheck:** around `2026-05-14T08:20Z` to `08:22Z`, the
  stronger timeout shape still did not reproduce and the issue
  narrowed further, but it did not clear. The live hub devices page and
  `/api/v1/admin/devices` still showed `.48` `online` on
  `0.1.17-dev-central`; the initial local sweep returned `200` with the
  root page at about `0.39 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.09 s`; the immediate 5-cycle follow-up loop
  then stayed clean on the root page at about `0.15 s`-`0.17 s` and on
  `/api/status` at about `0.02 s`-`0.04 s`, but cycle 5 of
  `/api/config` stretched to about `2.58 s`. Treat this as concrete
  improvement relative to the earlier full 10-second failures, but keep
  the bug open because the renamed-test device still shows intermittent
  first-contact local HTTP instability on at least one endpoint.
- **Fix direction:** inspect the device-side local web/API server for
  startup, reboot, or socket-handling gaps that can drop the first
  request after a recovery or heartbeat transition.

### BUG-055 - `.207` local root UI intermittently stalls while device APIs stay fast

- **Severity:** medium (live reliability issue)
- **Area:** device local web UI responsiveness
- **Status:** **open - observed live 2026-05-14**
- **Detail:** Live comparison against the hub and local device surfaces
  for `192.168.1.207` shows the issue is not a full device outage.
  Around `2026-05-14T05:30Z`, the live hub devices page and
  `/api/v1/admin/devices` both showed `Erica''s R.R. Speaker` as
  `online` on `0.1.16-dev-central`, while local `/api/status` returned
  `200` in about `0.03 s` with `central_state: "idle"` and local
  `/api/config` returned `200` in about `0.08 s`. But the local root
  UI request to `http://192.168.1.207/` took about `6.29 s` in the same
  sweep.
- **Repeat evidence:** In the immediate 5-cycle follow-up loop around
  `2026-05-14T05:31Z` to `05:32Z`, the same device root page stretched
  again to about `2.93 s` on cycle 2, while the other root-page reads
  stayed about `0.12 s`-`0.19 s` and the JSON endpoints remained fast
  throughout. That makes this a repeated local UI stall rather than a
  one-off truncated read.
- **Latest recheck:** around `2026-05-14T05:40Z` to `05:42Z`, the stall
  did not reproduce. The initial `.207` local sweep plus a fresh
  5-cycle loop all returned `200`, with root-page reads about
  `0.13 s`-`0.19 s`, `/api/status` about `0.02 s`-`0.04 s`, and
  `/api/config` about `0.07 s`-`0.09 s`. Treat the bug as narrowed but
  still open until a longer soak stops reproducing the earlier
  multi-second root-page stalls.
- **Next recheck:** around `2026-05-14T05:50Z` to `05:52Z`, the issue
  reappeared in a weaker form. The live hub devices page and
  `/api/v1/admin/devices` still showed `.207` `online` on
  `0.1.16-dev-central`, local `/api/status` stayed about `0.02 s`, and
  local `/api/config` stayed about `0.06 s`-`0.09 s`, but the first
  local root-page read in the follow-up loop stretched to about
  `1.40 s` before cycles 2-5 settled back to about `0.09 s`-`0.16 s`.
  Treat this as improved relative to the earlier `6.29 s` and `2.93 s`
  stalls, but still as evidence that the root-page render path is not
  consistently healthy.
- **Latest recheck:** around `2026-05-14T06:00Z` to `06:01Z`, the issue
  improved again. The initial `.207` local sweep had one slower first
  root-page read at about `1.30 s`, while `/api/status` stayed about
  `0.03 s` and `/api/config` about `0.03 s`; the immediate 5-cycle
  follow-up loop then stayed clean with root-page reads about
  `0.10 s`-`0.16 s`, `/api/status` about `0.02 s`-`0.04 s`, and
  `/api/config` about `0.06 s`-`0.08 s`. Treat this as stronger
  improvement than the prior weak reproduction, but keep the bug open
  until the root-page path stops showing intermittent first-read
  latency.
- **Latest recheck:** around `2026-05-14T06:10Z` to `06:11Z`, the stall
  did not reproduce again. The initial `.207` local sweep returned `200`
  with the root page at about `0.21 s`, `/api/status` about `0.03 s`,
  and `/api/config` about `0.07 s`; the immediate 5-cycle follow-up
  loop then stayed clean with root-page reads about `0.10 s`-`0.18 s`,
  `/api/status` about `0.02 s`-`0.03 s`, and `/api/config` about
  `0.06 s`-`0.10 s`. Treat this as another improved window, but keep
  the bug open until a longer soak stops showing intermittent root-page
  latency.
- **Latest recheck:** around `2026-05-14T06:20Z` to `06:21Z`, the stall
  again did not reproduce. The initial `.207` local sweep returned `200`
  with the root page at about `0.43 s`, `/api/status` about `0.02 s`,
  and `/api/config` about `0.07 s`; the immediate 5-cycle follow-up
  loop then stayed clean with root-page reads about `0.11 s`-`0.14 s`,
  `/api/status` about `0.02 s`-`0.04 s`, and `/api/config` about
  `0.06 s`-`0.09 s`. Treat this as another improved window, but keep
  the bug open until a longer soak stops showing intermittent root-page
  latency.
- **Latest recheck:** around `2026-05-14T06:31Z` to `06:33Z`, the stall
  again did not reproduce. The initial `.207` local sweep returned `200`
  with the root page at about `0.46 s`, `/api/status` about `0.02 s`,
  and `/api/config` about `0.07 s`; the immediate 5-cycle follow-up
  loop then stayed clean with root-page reads about `0.10 s`-`0.23 s`,
  `/api/status` about `0.02 s`-`0.03 s` except one slower `0.34 s`
  read on cycle 5, and `/api/config` about `0.07 s`-`0.10 s`. Treat
  this as another improved window rather than a fresh reproduction of
  the earlier multi-second root-page stall.
- **Latest recheck:** around `2026-05-14T06:40Z` to `06:42Z`, the stall
  again did not reproduce. The initial `.207` local sweep returned `200`
  with the root page at about `0.85 s`, `/api/status` about `0.04 s`,
  and `/api/config` about `0.07 s`; the immediate 5-cycle follow-up
  loop then stayed clean with root-page reads about `0.11 s`-`0.20 s`,
  `/api/status` about `0.02 s`-`0.04 s`, and `/api/config` about
  `0.06 s`-`0.09 s`. Treat this as another improved window rather than
  a fresh reproduction of the earlier multi-second root-page stall.
- **Latest recheck:** around `2026-05-14T06:50Z` to `06:51Z`, the root
  stall still did not reproduce, but the local control-plane latency
  did show up on a different endpoint. The live hub devices page and
  `/api/v1/admin/devices` still showed `.207` `online` on
  `0.1.16-dev-central`; the initial local sweep returned `200` with the
  root page at about `0.31 s`, `/api/status` about `0.03 s`, and
  `/api/config` about `0.09 s`; then cycle 1 of the immediate 5-cycle
  follow-up loop hit a slower `/api/status` read at about `4.85 s`
  while the root page stayed about `0.24 s` and `/api/config` about
  `0.08 s`. Cycles 2-5 returned to about `0.10 s`-`0.16 s` on the root
  page, `0.03 s`-`0.07 s` on `/api/status`, and `0.07 s`-`0.08 s` on
  `/api/config`. Treat this as renewed evidence that `.207` still has
  intermittent local-control-plane latency even when the root page and
  hub heartbeat view stay healthy.
- **Fix direction:** inspect the device-side local HTTP/control-plane
  handlers on `.207` for intermittent blocking or socket starvation.
  Earlier windows hit the root-page render path; the latest window hit
  `/api/status` while the hub and neighboring local endpoints stayed
  healthy.
- **Latest recheck:** around `2026-05-14T07:02Z` to `07:04Z`, the
  `.207` path improved again, but a weaker first-hit root-page delay
  remained. The live hub devices page and `/api/v1/admin/devices` still
  showed `.207` `online` on `0.1.16-dev-central`; the devices row also
  continued to advertise the pending upgrade affordance toward
  `0.1.17-dev-central`. The initial local sweep returned `200` with the
  root page at about `0.28 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.07 s`; then cycle 1 of the immediate 5-cycle
  follow-up loop hit a slower root-page read at about `1.46 s` while
  `/api/status` stayed about `0.02 s`-`0.04 s` and `/api/config` stayed
  about `0.07 s`-`0.08 s`. Cycles 2-5 returned to about `0.10 s`-`0.16 s`
  on the root page. Treat this as improved relative to the earlier
  `6.29 s`, `2.93 s`, and `4.85 s` shapes, but keep the bug open until
  the local UI path stops showing intermittent first-contact latency.
- **Latest recheck:** around `2026-05-14T07:10Z` to `07:12Z`, the
  `.207` path improved further and did not reproduce the prior stall.
  The live hub devices page and `/api/v1/admin/devices` still showed
  `.207` `online` on `0.1.16-dev-central`, and the devices row still
  advertised the pending upgrade affordance toward `0.1.17-dev-central`.
  The initial local sweep returned `200` with the root page at about
  `0.32 s`, `/api/status` about `0.02 s`, and `/api/config` about
  `0.07 s`; the immediate 5-cycle follow-up loop then stayed clean with
  root-page reads about `0.10 s`-`0.27 s`, `/api/status` about
  `0.02 s`, and `/api/config` about `0.07 s`-`0.09 s`. Treat this as a
  concrete improved window relative to the earlier `6.29 s`, `2.93 s`,
  `4.85 s`, and `1.46 s` shapes, but keep the bug open until a longer
  soak stops showing intermittent first-contact latency.
- **Latest recheck:** around `2026-05-14T07:40Z` to `07:42Z`, the
  issue shifted shape and re-strengthened. The live hub devices page
  and `/api/v1/admin/devices` still showed `.207` `online` on
  `0.1.16-dev-central`, local `/api/status` returned `200` in about
  `0.02 s`, and local `/api/config` returned `200` in about `0.09 s`,
  but the first local root-page request failed after about `4.22 s`
  with a truncated-body `ChunkedEncodingError`. An immediate 5-cycle
  follow-up loop then recovered cleanly at `200`, with root-page reads
  about `0.12 s`-`0.23 s`. Treat this as renewed evidence that the
  local UI path can fail transiently even while the device API and hub
  heartbeat view remain healthy.
- **Latest recheck:** around `2026-05-14T08:00Z` to `08:04Z`, the
  stronger failure shape did not reproduce and the issue narrowed again
  to weaker, non-repeating latency. The live hub devices page and
  `/api/v1/admin/devices` still showed `.207` `online` on
  `0.1.16-dev-central` with the pending upgrade affordance toward
  `0.1.17-dev-central`. The initial local sweep returned `200` with the
  root page at about `0.33 s`, `/api/status` about `0.04 s`, and
  `/api/config` about `0.05 s`; the immediate 5-cycle follow-up loop
  then hit one slower root-page read at about `1.24 s` and one
  separate slower `/api/config` read at about `1.59 s` before later
  cycles returned to normal. An immediate 8-cycle confirmation loop
  then stayed clean with root-page reads about `0.11 s`-`0.21 s`,
  `/api/status` about `0.02 s`-`0.05 s`, and `/api/config` about
  `0.04 s`-`0.08 s`. Keep the bug open, but treat this as improved
  relative to the earlier `6.29 s`, `2.93 s`, `4.85 s`, and truncated
  response shapes.
- **Latest recheck:** around `2026-05-14T08:10Z` to `08:13Z`, the
  issue improved again and stayed in the weaker first-hit-latency
  bucket. The live hub devices page and `/api/v1/admin/devices` still
  showed `.207` `online` on `0.1.16-dev-central` with the pending
  upgrade affordance toward `0.1.17-dev-central`; the initial local
  sweep returned `200` with the root page at about `0.34 s`,
  `/api/status` about `0.03 s`, and `/api/config` about `0.09 s`; the
  immediate 5-cycle follow-up loop then hit one slower root-page read
  at about `1.46 s` on cycle 1 before cycles 2-5 returned to about
  `0.10 s`-`0.13 s` while the JSON endpoints stayed fast. Keep the bug
  open, but this is materially improved relative to the earlier
  truncated-body and multi-second stall shapes.
- **Latest recheck:** around `2026-05-14T08:20Z` to `08:22Z`, the
  issue stayed in the weaker non-repeating bucket. The live hub devices
  page and `/api/v1/admin/devices` still showed `.207` `online` on
  `0.1.16-dev-central` with the pending upgrade affordance toward
  `0.1.17-dev-central`; the initial local sweep returned `200` with the
  root page at about `0.35 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.12 s`; the immediate 5-cycle follow-up loop
  then hit one slower `/api/status` read at about `1.38 s` on cycle 2
  before later cycles returned to about `0.02 s`-`0.03 s`, while the
  root page stayed about `0.12 s`-`0.26 s` and `/api/config` stayed
  about `0.07 s`-`0.11 s`. Keep the bug open, but treat this as
  improved relative to the earlier truncated-body and multi-second
  root-path failures.

### WATCH - `.225` showed one mild first-hit local root-page slowdown

- **Severity:** low (watch only, not a bug yet)
- **Area:** device local web UI responsiveness
- **Status:** **watch - observed once live 2026-05-14**
- **Detail:** Around `2026-05-14T06:40Z` to `06:42Z`, the live hub
  devices page and `/api/v1/admin/devices` still showed
  `192.168.1.225` (`Erica''s F.R Speaker`) as `online` on
  `0.1.17-dev-central`, while local `/api/status` returned `200` in
  about `0.03 s` and local `/api/config` returned `200` in about
  `0.06 s`. In the same sweep, the initial local root UI request to
  `http://192.168.1.225/` took about `1.41 s`.
- **Interpretation:** This is not enough to call a fresh regression by
  itself because there is only one slower first-hit sample and no
  repeated stall. Keep it as a watch item only unless future soak passes
  repeat the same first-contact slowdown pattern.
- **Latest recheck:** around `2026-05-14T07:10Z` to `07:12Z`, the watch
  item improved rather than strengthening. The live hub devices page
  and `/api/v1/admin/devices` still showed `192.168.1.225`
  (`Erica''s F.R Speaker`) as `online` on `0.1.17-dev-central`, while
  local `/api/status` returned `200` in about `0.02 s`, local
  `/api/config` returned `200` in about `0.07 s`, and the initial local
  root UI request returned `200` in about `0.22 s`. Keep this as a
  watch item only.
- **Latest recheck:** around `2026-05-14T07:40Z` to `07:42Z`, the watch
  item strengthened as repeated evidence rather than clearing. The live
  hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.225` (`Erica''s F.R Speaker`) as `online` on
  `0.1.17-dev-central`, while local `/api/status` returned `200` in
  about `0.02 s` and local `/api/config` returned `200` in about
  `0.07 s`; in the same sweep, the first local root UI request took
  about `2.10 s`. An immediate 5-cycle follow-up loop then returned to
  about `0.11 s`-`0.31 s` on the root page. Keep this below bug level
  for now, but it is no longer just a single slow sample.
- **Latest recheck:** around `2026-05-14T07:51Z` to `07:56Z`, the
  watch signal shifted rather than clearing. The live hub devices page
  and `/api/v1/admin/devices` still showed `192.168.1.225`
  (`Erica''s F.R Speaker`) as `online` on `0.1.17-dev-central`; the
  initial local sweep returned `200` with the root page at about
  `0.30 s`, `/api/status` about `0.03 s`, and `/api/config` about
  `0.08 s`; then a focused 5-cycle loop hit a slower `/api/status`
  read at about `1.35 s`, and the immediate 3-cycle confirmation loop
  hit another `/api/status` read at about `2.64 s` while the root page
  stayed about `0.17 s`-`0.32 s` and `/api/config` stayed about
  `0.07 s`-`0.10 s`. A later 8-cycle `/api/status` loop then returned
  to about `0.02 s`-`0.13 s`. Keep this as a watch item, but note that
  the intermittent latency is no longer limited to the first local root
  page request.
- **Latest recheck:** around `2026-05-14T08:00Z` to `08:01Z`, the
  watch item improved rather than strengthening. The live hub devices
  page and `/api/v1/admin/devices` still showed `192.168.1.225`
  (`Erica''s F.R Speaker`) as `online` on `0.1.17-dev-central`; the
  initial local sweep returned `200` with the root page at about
  `0.31 s`, `/api/status` about `0.05 s`, and `/api/config` about
  `0.09 s`, and the immediate 5-cycle follow-up loop then stayed clean
  with the root page about `0.10 s`-`0.15 s`, `/api/status` about
  `0.02 s`-`0.03 s`, and `/api/config` about `0.07 s`-`0.08 s`. Keep
  this below bug level until a later soak pass repeats the slower
  `/api/status` behavior.
- **Latest recheck:** around `2026-05-14T08:10Z` to `08:13Z`, the
  watch item strengthened into a short recovery wobble but still did
  not hold long enough to call a fresh bug. The first hub admin API
  sample still held `192.168.1.225` (`Erica''s F.R Speaker`) at
  `offline` with `last_heartbeat_at` `2026-05-14T08:01:06Z`, while the
  local root page took about `6.04 s` and local `/api/status` still
  returned `200` in about `0.02 s` with `central_state="idle"` and
  `central_heartbeat_age_seconds=3`; local `/api/config` returned
  `200` in about `0.08 s`. An immediate 5-cycle follow-up loop then
  stayed clean with root-page reads about `0.10 s`-`0.18 s`,
  `/api/status` about `0.02 s`, and `/api/config` about
  `0.07 s`-`0.08 s`, and the later hub UI/API recheck had already
  converged back to `online`. Keep this as a watch item, but it is now
  stronger evidence of transient local recovery and delayed central
  convergence than the earlier mild `.225` windows.
- **Latest recheck:** around `2026-05-14T08:20Z` to `08:22Z`, the
  watch item strengthened again into the strongest local wobble seen so
  far even though the rendered hub Devices page and
  `/api/v1/admin/devices` both still held `192.168.1.225`
  (`Erica''s F.R Speaker`) at `online` on `0.1.17-dev-central`. The
  initial local root UI request took about `8.12 s`, the first local
  `/api/status` probe then failed after about `9.55 s` with a
  connection reset, and local `/api/config` still returned `200` in
  about `0.08 s`. An immediate 5-cycle follow-up loop then recovered
  cleanly with root-page reads about `0.10 s`-`0.19 s`,
  `/api/status` about `0.03 s`-`0.04 s`, and `/api/config` about
  `0.07 s`-`0.12 s`. Keep this below fresh-bug level for one more pass
  only; if the same root-plus-status failure shape repeats, promote it
  out of watch-only status.

### 2026-05-14 live recheck addendum - `.207` re-strengthened, `.225` improved, `.48` stayed narrowed

- **BUG-054 latest recheck:** around `2026-05-14T08:40Z` to `08:41Z`,
  the renamed soak target on `192.168.1.48` stayed converged across the
  rendered Devices page, `/api/v1/admin/devices`, and local
  `/api/status` + `/api/config` as `Rebooter - renamed test` /
  `online` / `0.1.17-dev-central`. The initial local root-page request
  returned in about `0.31 s`, but cycle 1 of the immediate 5-cycle
  confirmation loop still stretched to about `1.44 s` before cycles
  2-5 returned to about `0.10 s`-`0.12 s`. Keep the bug open, but it
  remains materially narrower than the earlier 10-second timeout shape.
- **BUG-055 latest recheck:** around `2026-05-14T08:40Z` to `08:41Z`,
  the live hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central` with the pending-upgrade affordance toward
  `0.1.17-dev-central`; the initial local sweep returned `200` with the
  root page at about `0.37 s`, `/api/status` about `0.03 s`, and
  `/api/config` about `0.08 s`, but cycle 1 of the immediate 5-cycle
  follow-up loop hit a slower `3.77 s` root-page read before cycles
  2-5 returned to about `0.10 s`-`0.11 s` while the JSON endpoints
  stayed fast. Treat this as renewed evidence of intermittent local UI
  latency on `.207`, stronger than the prior `1.38 s` watch window but
  still weaker than the earlier truncated-body and multi-second failure
  shapes.
- **WATCH `.225` latest recheck:** around `2026-05-14T08:40Z` to
  `08:41Z`, the rendered hub Devices page and `/api/v1/admin/devices`
  both still showed `192.168.1.225` (`Erica''s F.R Speaker`) `online`
  on `0.1.17-dev-central`, while the initial local sweep returned
  `200` with the root page at about `0.25 s`, `/api/status` about
  `0.02 s`, and `/api/config` about `0.02 s`. The immediate 5-cycle
  follow-up loop then stayed clean. This materially improves on the
  earlier `8.12 s` root-page / `9.55 s` `/api/status` wobble and keeps
  `.225` below fresh-bug level for now.

### 2026-05-14 live recheck addendum - `.48` and `.207` briefly re-strengthened, `.225` fell back to watch-only

- **BUG-054 latest recheck:** around `2026-05-14T08:50Z` to `08:56Z`,
  the renamed soak target on `192.168.1.48` still matched the rendered
  Devices page and `/api/v1/admin/devices` as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, but the
  first local sweep briefly returned to a stronger failure shape: the
  root page took about `9.82 s`, `/api/status` timed out after the full
  `10.02 s` read window, and `/api/config` still returned `200` in
  about `2.69 s`. An immediate retry sweep then recovered to about
  `0.19 s` on `/`, `0.02 s` on `/api/status`, and `0.03 s` on
  `/api/config`; the immediate 5-cycle follow-up loop hit one slower
  `/api/status` read at about `2.80 s`, and a final 3-cycle spot-check
  then stayed clean. Keep the bug open: this was not a sustained outage,
  but it is stronger evidence than the prior `1.44 s` latency-only
  bucket.
- **BUG-055 latest recheck:** in the same `08:50Z` to `08:56Z` window,
  the live hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central` with the pending-upgrade affordance toward
  `0.1.17-dev-central`, but the first local sweep hit a full
  `10.28 s` timeout on `/` and a slower `/api/status` read at about
  `4.86 s` while `/api/config` still returned `200` in about `0.08 s`.
  An immediate retry sweep then recovered to about `0.18 s` on `/`,
  `0.03 s` on `/api/status`, and `0.08 s` on `/api/config`, and both
  the immediate 5-cycle confirmation loop and final 3-cycle spot-check
  stayed clean. Treat this as renewed evidence of intermittent
  local-surface recovery wobble, stronger than the prior `3.77 s`
  root-page-only sample but weaker than a persistent outage.
- **WATCH `.225` latest recheck:** in the same watch window, the
  rendered hub Devices page and `/api/v1/admin/devices` both still
  showed `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`. One initial sweep returned the root page in
  about `0.64 s` with fast JSON endpoints; an immediate retry sweep
  then hit a slower `4.05 s` root-page read while `/api/status` still
  returned `200` in about `0.02 s` with `central_state="idle"` and
  heartbeat age in single digits, and `/api/config` stayed fast. The
  immediate 5-cycle follow-up loop then stayed clean. This is weaker
  than the earlier `8.12 s` root-page plus `9.55 s` `/api/status`
  failure shape, so keep `.225` below fresh-bug level for now.

### 2026-05-14 live recheck addendum - clean `.48` and `.207` windows, `.225` stayed mild

- **BUG-054 latest recheck:** around `2026-05-14T09:00Z` to `09:01Z`,
  the renamed soak target on `192.168.1.48` stayed converged across the
  rendered Devices page, `/api/v1/admin/devices`, and local
  `/api/status` + `/api/config` as `Rebooter - renamed test` /
  `online` / `0.1.17-dev-central`. The initial local sweep returned
  `200` with the root page at about `0.25 s`, `/api/status` about
  `0.04 s`, and `/api/config` about `0.07 s`; the immediate 5-cycle
  follow-up loop then stayed clean with root-page reads about
  `0.10 s`-`0.19 s`, `/api/status` about `0.02 s`-`0.04 s`, and
  `/api/config` about `0.07 s`-`0.08 s`. Keep the bug open because the
  same device reproduced multiple 10-second timeout and recovery
  windows earlier on the same date, but this adds another clean short
  window.
- **BUG-055 latest recheck:** in the same `09:00Z` to `09:01Z` window,
  the live hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central` with the one-click upgrade affordance toward
  `0.1.17-dev-central`, while the initial local sweep returned `200`
  with the root page at about `0.20 s`, `/api/status` about `0.02 s`,
  and `/api/config` about `0.07 s`. The immediate 5-cycle follow-up
  loop then stayed clean with root-page reads about `0.09 s`-`0.11 s`,
  `/api/status` about `0.02 s`-`0.04 s`, and `/api/config` about
  `0.07 s`-`0.08 s`. Treat this as a materially improved clean window,
  but not enough to close the bug after the earlier timeout and
  multi-second recovery wobble.
- **WATCH `.225` latest recheck:** in the same watch window, the
  rendered hub Devices page and `/api/v1/admin/devices` both still
  showed `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`, while the initial local sweep returned `200`
  with the root page at about `0.20 s`, `/api/status` about `0.03 s`,
  and `/api/config` about `0.09 s`. The immediate 5-cycle follow-up
  loop then stayed clean with root-page reads about `0.10 s`-`0.26 s`
  and fast JSON endpoints. This improves materially on the prior
  `4.05 s` root-page sample and keeps `.225` below fresh-bug level.

### 2026-05-14 live recheck addendum - clean `.48` / `.207`, weaker `.225`, and one-off `.30` root delay

- **BUG-054 latest recheck:** around `2026-05-14T09:13Z` to `09:14Z`,
  the renamed soak target on `192.168.1.48` stayed converged across the
  rendered Devices page, `/api/v1/admin/devices`, and local
  `/api/status` + `/api/config` as `Rebooter - renamed test` /
  `online` / `0.1.17-dev-central`. The initial local sweep returned
  `200` with the root page at about `0.20 s`, `/api/status` about
  `0.02 s`, and `/api/config` about `0.07 s`; the immediate 5-cycle
  follow-up loop then stayed clean with root-page reads about
  `0.10 s`-`0.22 s` and fast JSON endpoints. Keep the bug open because
  the earlier 10-second timeout / recovery windows on the same date
  still outweigh this short clean sample, but this adds another
  improved window.
- **BUG-055 latest recheck:** in the same `09:13Z` to `09:14Z` window,
  the live hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central` while the local device API/config still exposed
  the unresolved desired-name drift (`Erica''s ?.?. Speaker`). The
  initial local sweep returned `200` with the root page at about
  `0.11 s`, `/api/status` about `0.03 s`, and `/api/config` about
  `0.07 s`, and the immediate 5-cycle follow-up loop then stayed clean
  with root-page reads about `0.11 s`-`0.17 s` and fast JSON
  endpoints. Treat this as another improved short window, but not
  enough to close the bug after the earlier timeout and multi-second
  wobble windows.
- **WATCH `.225` latest recheck:** in the same watch window, the
  rendered hub Devices page and `/api/v1/admin/devices` both still
  showed `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`. The initial local sweep stayed healthy, but
  cycle 1 of the immediate 5-cycle follow-up loop hit a slower
  `3.01 s` root-page read before cycles 2-5 returned to about
  `0.10 s`-`0.16 s` while `/api/status` and `/api/config` stayed fast.
  This is weaker than the earlier root-plus-status wobble, so keep it
  below fresh-bug level for now.
- **WATCH `.30` latest recheck:** in the same watch window, the live
  hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`, while the local device API/config still exposed
  the known `BUG-053` desired-name drift as `Rebooter`. The first local
  root-page read took about `4.23 s`, but local `/api/status` still
  returned `200` in about `0.02 s` and `/api/config` still returned
  `200` in about `0.07 s`. This is new one-off root-page latency
  evidence on `.30`, but it did not repeat in this pass, so keep it
  below fresh-bug level unless a later soak reproduces it.

### 2026-05-14 live recheck addendum - `.30` / `.225` cleared, `.48` stayed mild, `.207` likely rebooted recently

- **BUG-054 latest recheck:** around `2026-05-14T09:20Z` to `09:21Z`,
  the renamed soak target on `192.168.1.48` stayed converged across the
  rendered Devices page, `/api/v1/admin/devices`, and local
  `/api/status` + `/api/config` as `Rebooter - renamed test` /
  `online` / `0.1.17-dev-central`. The initial local sweep returned
  `200` with the root page at about `1.33 s`, `/api/status` about
  `0.02 s`, and `/api/config` about `0.08 s`; the immediate 5-cycle
  follow-up loop then stayed clean with root-page reads about
  `0.10 s`-`0.60 s` and fast JSON endpoints. Keep the bug open, but
  this stayed in the weaker latency-only bucket rather than
  re-strengthening into a timeout/recovery window.
- **BUG-055 latest recheck:** in the same `09:20Z` to `09:21Z` window,
  the live hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central` while the local device API/config still exposed
  the unresolved desired-name drift (`Erica''s ?.?. Speaker`). The
  initial local sweep returned `200` with the root page at about
  `0.27 s`, `/api/status` about `0.02 s`, and `/api/config` about
  `0.07 s`, and the immediate 5-cycle follow-up loop then stayed clean
  with root-page reads about `0.10 s`-`0.14 s` and fast JSON
  endpoints. However, local `/api/status` reported `uptime_seconds=24`
  in the first sweep and `94` on a confirming fetch about a minute
  later, which is concrete evidence of a recent reboot/recovery window.
  Keep the bug open.
- **WATCH `.225` latest recheck:** in the same watch window, the
  rendered hub Devices page and `/api/v1/admin/devices` both still
  showed `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`. The initial local sweep returned `200` with the
  root page at about `0.34 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.07 s`; the immediate 5-cycle follow-up loop
  then stayed clean with root-page reads about `0.10 s`-`0.21 s` and
  fast JSON endpoints. This materially improves on the prior `3.01 s`
  root-page sample and keeps `.225` below fresh-bug level.
- **WATCH `.30` latest recheck:** in the same watch window, the live
  hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`, while the local device API/config still exposed
  the known `BUG-053` desired-name drift as `Rebooter`. The initial
  local sweep returned `200` with the root page at about `0.21 s`,
  `/api/status` about `0.02 s`, and `/api/config` about `0.07 s`. The
  earlier `4.23 s` root-page wobble did not repeat in this pass, so
  keep `.30` below fresh-bug level unless a later soak reproduces it.

### 2026-05-14 live recheck addendum - `.48` config truncation returned, `.207` reboot evidence repeated

- **BUG-054 latest recheck:** around `2026-05-14T09:29Z` to `09:30Z`,
  the renamed soak target on `192.168.1.48` still stayed converged
  across the rendered Devices page, `/api/v1/admin/devices`, and local
  `/api/status` + `/api/config` identity fields as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`. The
  initial local sweep returned `200` with the root page at about
  `0.38 s`, `/api/status` about `0.03 s`, and `/api/config` about
  `0.07 s`, but cycle 4 of the immediate 5-cycle follow-up loop hit a
  `3.03 s` truncated-body `ChunkedEncodingError` on `/api/config`
  (`IncompleteRead(949 bytes read, 119 more expected)`) while `/` and
  `/api/status` stayed fast before and after. This is weaker than the
  earlier full-timeout windows, but it is still a concrete local
  recovery wobble that the hub view did not reflect.
- **BUG-055 latest recheck:** in the same `09:29Z` to `09:31Z` window,
  the live hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central` while the local device API/config still exposed
  the unresolved desired-name drift (`Erica''s ?.?. Speaker`). The
  initial local sweep and immediate 5-cycle follow-up loop both stayed
  fast, but local `/api/status` reported `uptime_seconds=109` in the
  first sweep and `206` on a confirming fetch about a minute later.
  That repeats and strengthens the earlier evidence of a recent
  reboot/recovery window even though the hub continued to show the
  device as healthy.
- **WATCH `.225` latest recheck:** in the same watch window, the
  rendered hub Devices page and `/api/v1/admin/devices` both still
  showed `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`. The initial local sweep returned `200` with the
  root page at about `0.28 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.07 s`, and the immediate 5-cycle follow-up
  loop stayed clean. Keep `.225` below fresh-bug level.
- **WATCH `.30` latest recheck:** in the same watch window, the live
  hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`, while the local device API/config still exposed
  the known `BUG-053` desired-name drift as `Rebooter`. The initial
  local sweep returned `200` with the root page at about `0.12 s`,
  `/api/status` about `0.04 s`, and `/api/config` about `0.06 s`. The
  earlier one-off root-page wobble stayed cleared in this pass.

### 2026-05-14 live recheck addendum - `.48` stayed mild, `.207` root stall returned, `.30` likely rebooted recently

- **BUG-054 latest recheck:** around `2026-05-14T09:39Z` to `09:40Z`,
  the renamed soak target on `192.168.1.48` still stayed converged
  across the rendered Devices page, `/api/v1/admin/devices`, and local
  `/api/status` + `/api/config` identity fields as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`. The
  initial local sweep hit a slower `2.97 s` root-page read, but
  `/api/status` still returned `200` in about `0.02 s` and
  `/api/config` in about `0.08 s`, and the immediate 5-cycle follow-up
  loop then stayed clean with root-page reads about `0.11 s`-`0.17 s`
  plus fast JSON endpoints. This improves on the prior truncated-body
  `/api/config` failure and keeps the bug in the weaker latency-only
  bucket for this pass.
- **BUG-055 latest recheck:** in the same `09:39Z` to `09:42Z` window,
  the live hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central` while the local device API/config still exposed
  the unresolved desired-name drift (`Erica''s ?.?. Speaker`). The
  initial local sweep stayed healthy, and local `/api/status` later
  confirmed `uptime_seconds=814`, so there was no fresh reboot in this
  pass. However, cycle 2 of the immediate 5-cycle follow-up loop hit a
  slower `4.23 s` root-page read before cycles 3-5 returned to about
  `0.10 s`-`0.22 s` while `/api/status` and `/api/config` stayed fast.
  Treat this as renewed local-surface recovery wobble that keeps the
  bug open.
- **WATCH `.225` latest recheck:** in the same watch window, the
  rendered hub Devices page and `/api/v1/admin/devices` both still
  showed `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`. The initial local sweep returned `200` with the
  root page at about `0.43 s`, `/api/status` about `0.03 s`, and
  `/api/config` about `0.07 s`, and the immediate 5-cycle follow-up
  loop stayed clean. Keep `.225` below fresh-bug level.
- **WATCH `.30` latest recheck:** in the same watch window, the live
  hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`, while the local device API/config still exposed
  the known `BUG-053` desired-name drift as `Rebooter`. The initial
  local sweep stayed fast, but local `/api/status` reported
  `uptime_seconds=155` and a confirming fetch shortly afterward
  reported `279`, which is concrete evidence of a recent
  reboot/recovery window that the hub view did not surface. Keep it as
  a reliability watch item unless a later soak repeats a visible stall.

### 2026-05-14 live recheck addendum - `.48` showed a real reboot/recovery window

- **BUG-054 latest recheck:** around `2026-05-14T09:50Z` to `09:53Z`,
  the renamed soak target on `192.168.1.48` still stayed converged
  across the rendered Devices page, `/api/v1/admin/devices`, and local
  `/api/status` + `/api/config` identity fields as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, but the
  first local root-page probe timed out after about `10.26 s`. A
  follow-on local `/api/status` read then returned `200` in about
  `1.59 s` with `uptime_seconds=10`, while local `/api/config`
  returned `200` in about `0.07 s`. The immediate 5-cycle follow-up
  loop then stayed clean with root-page reads about `0.10 s`-`0.19 s`,
  `/api/status` about `0.02 s`-`0.05 s`, and `/api/config` about
  `0.07 s`-`0.09 s`, and a later confirming `/api/status` read
  reported `uptime_seconds=98`. This is materially stronger than the
  prior latency-only and truncated-body shapes: it is concrete evidence
  that `.48` went through a real reboot/recovery window that the hub
  continued to mask as simply `online`.
- **BUG-055 latest recheck:** in the same `09:50Z` to `09:53Z` window,
  the live hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central` while the local device API/config still exposed
  the unresolved desired-name drift (`Erica''s ?.?. Speaker`). The
  initial local sweep returned `200` with the root page at about
  `0.35 s`, `/api/status` about `0.02 s`, and `/api/config` about
  `0.09 s`, and local `/api/status` reported `uptime_seconds=1362`.
  That materially improves on the earlier root-page stall and fresh
  reboot evidence, but keep the bug open because the naming drift and
  earlier wobble shapes still stand.
- **WATCH `.225` latest recheck:** in the same watch window, the
  rendered hub Devices page and `/api/v1/admin/devices` both still
  showed `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`. The initial local sweep returned `200` with the
  root page at about `0.19 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.07 s`. No fresh regression surfaced there.
- **WATCH `.30` latest recheck:** in the same watch window, the live
  hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`, while the local device API/config still exposed
  the known `BUG-053` desired-name drift as `Rebooter`. Two confirming
  `/api/status` reads reported `uptime_seconds=914` and then `949`, so
  the earlier short-uptime reboot evidence did not repeat in this pass.

### 2026-05-14 live recheck addendum - `.48` stayed converged but `.207` escalated into a truncated root-body failure

- **BUG-054 latest recheck:** around `2026-05-14T10:01Z` to `10:03Z`,
  the renamed soak target on `192.168.1.48` still stayed converged
  across the rendered Devices page, `/api/v1/admin/devices`, and local
  `/api/status` + `/api/config` identity fields as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`. The
  first local sweep returned `200` with the root page at about
  `0.28 s`, `/api/status` about `0.03 s`, and `/api/config` about
  `0.08 s`, and the immediate 5-cycle follow-up loop stayed clean with
  root-page reads about `0.12 s`-`0.22 s`, `/api/status` about
  `0.02 s`, and `/api/config` about `0.07 s`. However, the recent
  reboot evidence did not clear: local `/api/status` reported
  `uptime_seconds=92` in the first sweep, `180`-`186` across the
  immediate loop, and `192` on a later confirming read. Keep BUG-054
  open because the hub still masked that reboot/recovery window as
  simply `online`.
- **BUG-055 latest recheck:** in the same `10:01Z` to `10:03Z` window,
  the live hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central` while the local device API/config still exposed
  the unresolved desired-name drift (`Erica''s ?.?. Speaker`). The
  first local root-page probe then failed after about `3.25 s` with a
  truncated-body `ChunkedEncodingError`
  (`IncompleteRead(2680 bytes read, 13343 more expected)`) while local
  `/api/status` still returned `200` in about `0.02 s` with
  `uptime_seconds=304` and local `/api/config` returned `200` in about
  `0.08 s`. The immediate 5-cycle follow-up loop and a later
  `/api/status` confirm then stayed clean, with uptime climbing to
  `400`, so this was not a fresh reboot. Still, it is stronger than the
  earlier `4.23 s` root delay and should now be treated as a concrete
  transient local UI response-integrity failure.
- **WATCH `.225` latest recheck:** in the same watch window, the
  rendered hub Devices page and `/api/v1/admin/devices` both still
  showed `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`. The initial local sweep returned `200` with the
  root page at about `0.33 s`, `/api/status` about `0.03 s`, and
  `/api/config` about `0.08 s`. No fresh regression surfaced there.
- **WATCH `.30` latest recheck:** in the same watch window, the live
  hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`, while the local device API/config still exposed
  the known `BUG-053` desired-name drift as `Rebooter`. The initial
  local sweep returned `200` with the root page at about `0.10 s`,
  `/api/status` about `0.05 s`, and `/api/config` about `0.08 s`, so
  the earlier watch-only reboot evidence did not re-strengthen here.

### 2026-05-14 live recheck addendum - `.48` and `.207` both improved in a clean confirmation window

- **BUG-054 latest recheck:** around `2026-05-14T10:20Z` to `10:21Z`,
  the renamed soak target on `192.168.1.48` still stayed converged
  across the rendered Devices page, `/api/v1/admin/devices`, and local
  `/api/status` + `/api/config` identity fields as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`. The
  initial local sweep returned `200` with the root page at about
  `0.35 s`, `/api/status` about `0.02 s`, and `/api/config` about
  `0.07 s`, and the immediate 5-cycle follow-up loop then stayed clean
  with root-page reads about `0.11 s`-`1.35 s`, `/api/status` about
  `0.02 s`-`0.05 s`, and `/api/config` about `0.06 s`-`0.07 s`. The
  recent reboot signal also improved: local `/api/status` reported
  `uptime_seconds=1263` in the initial sweep and then climbed from
  `1331` to `1336` across the loop. Treat this as a clean follow-up
  sample that keeps BUG-054 open, but only as a non-reproducing watch
  item in this pass.
- **BUG-055 latest recheck:** in the same `10:20Z` to `10:21Z` window,
  the live hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central` while the local device API/config still exposed
  the unresolved desired-name drift (`Erica''s ?.?. Speaker`). The
  initial local sweep returned `200` with the root page at about
  `0.27 s`, `/api/status` about `0.05 s`, and `/api/config` about
  `0.07 s`, and the immediate 5-cycle follow-up loop stayed clean with
  root-page reads about `0.13 s`-`1.59 s`, `/api/status` about
  `0.02 s`-`0.08 s`, and `/api/config` about `0.07 s`-`0.10 s`. Local
  `/api/status` uptime also climbed monotonically from `922` in the
  initial sweep to `997`-`1002` in the loop, so neither the earlier
  truncated-body failure nor the fresh reboot evidence repeated here.
  Keep BUG-055 open, but this pass improved it back into the watch-only
  bucket.
- **WATCH `.225` latest recheck:** in the same watch window, the
  rendered hub Devices page and `/api/v1/admin/devices` both still
  showed `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`. The initial local sweep returned `200` with the
  root page at about `0.32 s`, `/api/status` about `0.03 s`, and
  `/api/config` about `0.07 s`. No fresh regression surfaced there.
- **WATCH `.30` latest recheck:** in the same watch window, the live
  hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`, while the local device API/config still exposed
  the known `BUG-053` desired-name drift as `Rebooter`. The initial
  local sweep returned `200` with the root page at about `0.16 s`,
  `/api/status` about `0.02 s`, and `/api/config` about `0.08 s`, so
  there was no fresh reboot or latency signal in this pass.

### 2026-05-14 live recheck addendum - renamed soak target regressed again, `.207` rebooted again, and `.225` now drifts both directions

- **BUG-054 latest recheck:** around `2026-05-14T10:30Z` to `10:36Z`,
  the renamed soak target on `192.168.1.48` first appeared converged in
  the rendered Devices page and `/api/v1/admin/devices` as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, but
  local `/api/status` already reported `uptime_seconds=16`, which is a
  fresh reboot. The immediate 5-cycle follow-up loop then looked clean
  again with root-page reads about `0.11 s`-`0.25 s`,
  `/api/status` about `0.02 s`-`0.03 s`, `/api/config` about
  `0.03 s`-`0.08 s`, and uptime climbing from `105` to `110`. A few
  minutes later, however, local `/`, `/api/status`, and `/api/config`
  all fell back to full `10 s` read timeouts, and the hub Devices
  page/API had flipped the row to `offline` with last heartbeat
  `2026-05-14T10:33:02Z`. This materially strengthens the bug from
  "intermittent response-integrity failure" to a fresh
  reboot/recovery-then-drop sequence.
- **BUG-055 latest recheck:** in the same `10:30Z` to `10:36Z` window,
  the live hub Devices page and `/api/v1/admin/devices` still showed
  `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central`, but the first local `/`, `/api/status`, and
  `/api/config` probes all timed out for the full `10 s` window. The
  immediate 5-cycle follow-up loop then recovered cleanly with uptime
  climbing from `17` to `24`, which is fresh reboot evidence rather
  than just another latency wobble. A later confirming local
  `/api/status` read still showed only `uptime_seconds=81` and took
  about `1.39 s`, while the hub still presented the device as `online`
  with last heartbeat `2026-05-14T10:36:34Z`. Keep BUG-055 open as a
  stronger reboot/recovery masking failure.
- **WATCH `.225` latest recheck:** this device no longer looks like a
  soft watch-only case. In the initial `10:30Z` sweep, the rendered hub
  Devices page and `/api/v1/admin/devices` still showed
  `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`, while local `/` stretched to about `7.81 s` and
  local `/api/status` plus `/api/config` both timed out after about
  `10 s`. By the final confirmation window the local device had
  recovered cleanly (`/` about `0.19 s`, `/api/status` about `0.04 s`,
  `/api/config` about `0.08 s`, `uptime_seconds=8095`), but the hub
  Devices page/API had already flipped it to `offline` with last
  heartbeat `2026-05-14T10:31:58Z`. Treat this as concrete
  hub-vs-local state drift rather than a clean offline transition.

### 2026-05-14 live recheck addendum - `.48` recovered from offline but still corrupts root responses, `.225` rebooted after reconverging, and `.207` rebooted again

- **BUG-054 latest recheck:** around `2026-05-14T10:39Z` to `10:42Z`,
  the renamed soak target on `192.168.1.48` had recovered from the
  earlier offline window and the rendered Devices page,
  `/api/v1/admin/devices`, local `/api/status`, and local `/api/config`
  all again converged as `Rebooter - renamed test` / `online` /
  `0.1.17-dev-central`. The initial local sweep returned `200` with the
  root page about `0.17 s`, `/api/status` about `0.02 s`, and
  `/api/config` about `0.08 s`, but local `/api/status` already showed
  only `uptime_seconds=232`, which is fresh reboot evidence. The
  immediate 5-cycle follow-up loop then climbed from `298` to `326`,
  yet cycle 3 still failed after about `3.14 s` with a truncated-body
  `ChunkedEncodingError`
  (`IncompleteRead(2027 bytes read, 13996 more expected)`) on the root
  page while `/api/status` and `/api/config` kept returning fast `200`
  responses. Keep BUG-054 open as confirmed reboot/recovery plus
  transient local-UI response corruption while the hub continues to
  show the device as healthy.
- **BUG-055 latest recheck:** in the same `10:39Z` to `10:42Z` window,
  the live hub Devices page and `/api/v1/admin/devices` still showed
  `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central`, while local `/api/status` and `/api/config`
  still exposed the unresolved desired-name drift
  (`Erica''s ?.?. Speaker`). The first local root-page read took about
  `11.35 s`, and local `/api/status` reported only `uptime_seconds=9`.
  The immediate 5-cycle follow-up loop then recovered and climbed from
  `61` to `89`, with one slower root-page sample at about `2.10 s`, and
  a later confirming `/api/status` read reported `128`. Keep BUG-055
  open as fresh reboot/recovery masking with continued transient local
  root instability.
- **WATCH `.225` latest recheck:** this device improved out of the
  prior hub-vs-local drift in the `10:39Z` to `10:42Z` window. The
  rendered hub Devices page and `/api/v1/admin/devices` had already
  returned to `online` on `0.1.17-dev-central`, and the immediate
  5-cycle local follow-up loop stayed clean with root-page reads about
  `0.10 s`-`0.24 s`, `/api/status` about `0.02 s`-`0.03 s`, and
  `/api/config` about `0.06 s`-`0.08 s`. But local `/api/status` first
  reported `uptime_seconds=90`, then climbed from `153` to `181`, with
  a later confirm at `217`, so it has fresh reboot evidence even though
  the hub row already looks healthy again. Keep `.225` above the old
  watch-only bucket.
- **WATCH `.30` latest recheck:** in the same watch window, the live
  hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`, while the local device API/config still exposed
  the known `BUG-053` desired-name drift as `Rebooter`. The local root
  page returned `200` in about `0.22 s`, `/api/config` returned `200`
  in about `0.07 s`, and `/api/status` returned `200` with
  `uptime_seconds=3759`; one slower `/api/status` sample at about
  `2.69 s` did not repeat, so there was no fresh bug-level regression
  beyond the standing name drift.

### 2026-05-14 live recheck addendum - `.48`, `.225`, and `.207` all improved in the short follow-up window

- **BUG-054 latest recheck:** around `2026-05-14T10:49Z` to `10:52Z`,
  the renamed soak target on `192.168.1.48` stayed converged across the
  rendered Devices page, `/api/v1/admin/devices`, and local
  `/api/status` + `/api/config` as `Rebooter - renamed test` /
  `online` / `0.1.17-dev-central`. The initial local sweep returned
  `200` with the root page at about `0.16 s`, `/api/status` about
  `0.03 s`, and `/api/config` about `0.08 s`, with local
  `/api/status` reporting `uptime_seconds=828`. The immediate 5-cycle
  follow-up loop then climbed from `877` to `884`; cycle 1 of the root
  page stretched to about `2.20 s`, but every sample still returned
  `200` and the earlier truncated-body / timeout shapes did not
  reproduce. Keep the bug open, but this pass improved it materially.
- **WATCH `.225` latest recheck:** in the same `10:49Z` to `10:52Z`
  window, the rendered hub Devices page and `/api/v1/admin/devices`
  still showed `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`, while local `/api/status` + `/api/config` still
  exposed the standing desired-name drift as `Rebooter`. The initial
  local sweep returned `200` with the root page at about `0.27 s`,
  `/api/status` about `0.03 s`, and `/api/config` about `0.08 s`.
  Local `/api/status` reported `uptime_seconds=682`, then the immediate
  5-cycle loop climbed from `731` to `739` with clean root/API
  responses throughout. Treat this as an improved short watch window
  rather than a fresh reboot signal.
- **BUG-055 latest recheck:** in the same `10:49Z` to `10:52Z` window,
  the live hub Devices page and `/api/v1/admin/devices` still showed
  `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central`, while local `/api/status` + `/api/config` still
  exposed `Erica''s ?.?. Speaker`. The initial local sweep returned
  `200` with the root page at about `0.30 s`, `/api/status` about
  `0.04 s`, and `/api/config` about `0.06 s`, with local
  `/api/status` reporting `uptime_seconds=591`. The immediate 5-cycle
  follow-up loop then climbed from `640` to `647` with root-page reads
  about `0.10 s`-`0.20 s` and no repeated timeout or response-integrity
  failure. Keep the bug open because the earlier reboot/recovery
  masking remains concrete, but this pass improved it materially.

### 2026-05-14 live recheck addendum - `.48` regressed again inside the loop, `.225` rebooted again, and `.207` improved

- **BUG-054 latest recheck:** around `2026-05-14T11:03Z` to `11:05Z`,
  the renamed soak target on `192.168.1.48` again first appeared
  converged across the rendered Devices page, `/api/v1/admin/devices`,
  and local `/api/status` + `/api/config` as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`. The
  initial local sweep returned `200` with the root page about `0.19 s`,
  `/api/status` about `0.02 s`, and `/api/config` about `0.08 s`, with
  local `/api/status` reporting `uptime_seconds=1607`. But the
  immediate 5-cycle follow-up loop then hit full `10 s` read timeouts
  on `/`, `/api/status`, and `/api/config` in cycle 1, another full
  `10 s` root-page timeout in cycle 2, and a root-page
  `ChunkedEncodingError` in cycle 4
  (`IncompleteRead(13283 bytes read, 2740 more expected)`). The
  returning `/api/status` samples had already dropped to
  `uptime_seconds=10`, `10`, `14`, and `14`, and a later confirming
  read reached only `uptime_seconds=17` with `health_state="unknown"`.
  A post-loop hub Devices page/API refresh still showed the device
  `online` on `0.1.17-dev-central` with last heartbeat
  `2026-05-14T11:04:35Z`. Keep BUG-054 open as repeated proof that the
  renamed soak target is still rebooting and corrupting or stalling its
  local UI while the hub continues to present it as healthy.
- **WATCH `.225` latest recheck:** in the same `11:03Z` to `11:05Z`
  window, the rendered hub Devices page and `/api/v1/admin/devices`
  still showed `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`, while local `/api/status` + `/api/config` still
  exposed the standing desired-name drift as `Rebooter`. The initial
  local sweep returned clean `200` responses, but local `/api/status`
  already showed only `uptime_seconds=37`. The immediate 5-cycle loop
  then stayed clean and climbed from `114` to `115`, with a later
  confirming `/api/status` read at `117`. Keep `.225` above the old
  watch-only bucket because it restarted again shortly before the pass
  while the hub row never exposed a degraded state.
- **BUG-055 latest recheck:** in the same `11:03Z` to `11:05Z` window,
  the live hub Devices page and `/api/v1/admin/devices` still showed
  `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central`, while local `/api/status` and `/api/config`
  still exposed `Erica''s ?.?. Speaker`. However, the initial local
  root-page read returned `200` in about `0.49 s`, the immediate
  5-cycle follow-up loop stayed at `200` with root-page reads about
  `0.79 s`, `0.11 s`, `0.12 s`, `0.16 s`, and `0.15 s`, and local
  `/api/status` held steady from `uptime_seconds=1371` to `1450`
  without a timeout or truncated-body repro. Keep BUG-055 open because
  the masking and desired-name drift still stand, but this pass
  improved it materially.

### 2026-05-14 live recheck addendum - `.48` and `.207` both rebooted through fresh offline windows while `.225` stayed clean

- **BUG-054 latest recheck:** around `2026-05-14T11:12:59Z` to
  `11:14:42Z`, the renamed soak target on `192.168.1.48` no longer
  looked falsely healthy in the hub at the start of the pass: the live
  Devices page and `/api/v1/admin/devices` both already showed it
  `offline` on `0.1.17-dev-central` with last heartbeat
  `2026-05-14T11:09:35Z`. But the local device was still in a degraded
  recovery window rather than a clean hard-down state. The root page
  returned `200` only after about `4.73 s`, while local `/api/status`
  and `/api/config` both timed out at the full `10 s`. The immediate
  5-cycle local follow-up loop then recovered cleanly with root-page
  reads about `0.11 s`-`0.19 s`, fast `200` status/config responses,
  and local `/api/status` reporting `Rebooter - renamed test` /
  `0.1.17-dev-central` at only `uptime_seconds=63`-`64`. A post-loop
  hub refresh had already moved the device back to `online` with last
  heartbeat `2026-05-14T11:14:22Z`. Keep BUG-054 open as fresh reboot /
  recovery evidence plus another transient local-surface stall.
- **BUG-055 latest recheck:** in the same `11:12:59Z` to `11:14:42Z`
  window, the live hub Devices page and `/api/v1/admin/devices` also
  showed `192.168.1.207` (`Erica''s R.R. Speaker`) `offline` on
  `0.1.16-dev-central` with last heartbeat `2026-05-14T11:07:41Z`. The
  local device was again in a partial recovery shape: the root page
  timed out after about `10 s`, local `/api/status` reset the
  connection after about `9.76 s`, and only local `/api/config`
  returned `200`, taking about `3.68 s` while still exposing the
  desired-name drift as `Erica''s ?.?. Speaker`. The immediate 5-cycle
  follow-up loop then recovered fully with root-page reads about
  `0.10 s`-`0.15 s`, fast `200` status/config responses, and local
  `/api/status` holding around `uptime_seconds=49`-`50`. A post-loop
  hub refresh had already flipped `.207` back to `online` with last
  heartbeat `2026-05-14T11:13:45Z`. Keep BUG-055 open as fresh reboot /
  recovery evidence even though the hub now exposed the outage instead
  of masking it as `online`.
- **WATCH `.225` latest recheck:** in the same window, the rendered hub
  Devices page and `/api/v1/admin/devices` still showed
  `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`, while local `/api/status` + `/api/config` still
  exposed the standing desired-name drift as `Rebooter`. Unlike the
  prior reboot-watch samples, the initial local sweep returned clean
  `200` responses, local `/api/status` reported `uptime_seconds=637`,
  and the immediate 5-cycle loop stayed clean. Treat this as a concrete
  short-window improvement, but not a closure of the name-drift issue.
- **BUG-054 latest recheck:** around `2026-05-14T11:20:45Z` to
  `11:22:10Z`, the renamed soak target on `192.168.1.48` improved
  materially relative to the prior offline/recovery window. The live
  Devices page and `/api/v1/admin/devices` again matched, both showing
  the device `online` on `0.1.17-dev-central` with last heartbeat
  `2026-05-14T11:22:22Z`. The initial local sweep returned clean `200`
  responses with the root page at about `0.23 s`, `/api/status` at
  about `0.02 s`, and `/api/config` at about `0.08 s`, while local
  `/api/status` + `/api/config` both showed `Rebooter - renamed test`.
  The immediate 5-cycle follow-up loop then stayed fully clean with
  root-page reads about `0.10 s`-`0.21 s`, `/api/status` about
  `0.02 s`-`0.03 s`, `/api/config` about `0.07 s`-`0.09 s`, and local
  `/api/status` climbing from `uptime_seconds=525` to `534`. Keep
  BUG-054 open because the earlier reboot/recovery failures remain
  concrete, but this pass is an actual short-window improvement rather
  than another immediate repro.
- **WATCH `.225` latest recheck:** in the same `11:20:45Z` to
  `11:22:10Z` window, the live hub Devices page and
  `/api/v1/admin/devices` still showed `192.168.1.225`
  (`Erica''s F.R Speaker`) `online` on `0.1.17-dev-central`, while
  local `/api/status` + `/api/config` still exposed the standing
  desired-name drift as `Rebooter`. The local root page returned `200`
  in about `0.33 s`, local `/api/status` returned `200` in about
  `0.02 s` with `uptime_seconds=1106`, and local `/api/config`
  returned `200` in about `0.07 s`. Keep this in the improved watch
  bucket rather than the reboot-watch bucket.
- **BUG-055 latest recheck:** in the same `11:20:45Z` to
  `11:22:10Z` window, the live hub Devices page and
  `/api/v1/admin/devices` showed `192.168.1.207`
  (`Erica''s R.R. Speaker`) back `online` on `0.1.16-dev-central` with
  last heartbeat `2026-05-14T11:22:06Z`, and the local root page plus
  `/api/status` plus `/api/config` all returned clean `200` responses
  (`0.32 s`, `0.03 s`, `0.08 s`). However, local `/api/status` still
  exposed `Erica''s ?.?. Speaker` and reported only
  `uptime_seconds=194`, which is fresh reboot evidence relative to the
  prior `11:12:59Z`-`11:14:42Z` recovery loop where `.207` had already
  climbed to `uptime_seconds=49`-`50`. Keep BUG-055 open as another
  reboot-through-healthy-row sample even though the local surfaces were
  responsive again in this pass.

### 2026-05-14 live recheck addendum - `.48` stayed clean again, `.207` no longer showed a fresh reboot, and `.225` remained watch-only

- **BUG-054 latest recheck:** around `2026-05-14T11:31:18Z` to
  `11:32:30Z`, the renamed soak target on `192.168.1.48` stayed
  converged across the rendered Devices page, `/api/v1/admin/devices`,
  and local `/api/status` + `/api/config` as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`. The
  hub row/API showed last heartbeat `2026-05-14T11:32:22Z`. The
  initial local sweep returned clean `200` responses with the root page
  at about `0.39 s`, `/api/status` at about `0.04 s`, and
  `/api/config` at about `0.24 s`, with local `/api/status` reporting
  `uptime_seconds=1154`. The immediate 5-cycle local follow-up loop
  from `11:31:57Z` to `11:32:09Z` then stayed fully clean, with root-
  page reads about `0.10 s`-`1.42 s`, fast `200` status/config
  responses, and local `/api/status` climbing from
  `uptime_seconds=1121` to `1131`. Keep BUG-054 open because the
  earlier reboot/recovery and local-surface failures from the same date
  remain concrete, but this pass was another clean soak window rather
  than a fresh repro.
- **WATCH `.225` latest recheck:** in the same `11:31:18Z` to
  `11:32:30Z` window, the live hub Devices page and
  `/api/v1/admin/devices` still showed `192.168.1.225`
  (`Erica''s F.R Speaker`) `online` on `0.1.17-dev-central` with last
  heartbeat `2026-05-14T11:31:54Z`, while local `/api/status` +
  `/api/config` still exposed the standing desired-name drift as
  `Rebooter`. The first local root-page read stretched to about
  `2.97 s`, but the confirming local sweep returned `200` in about
  `0.16 s`; local `/api/status` returned `200` in about `0.02 s` with
  `uptime_seconds=1785`, and local `/api/config` returned `200` in
  about `0.07 s`. Keep this as a slower-first-hit watch item only,
  not fresh reboot evidence.
- **BUG-055 latest recheck:** in the same `11:31:18Z` to
  `11:32:30Z` window, the live hub Devices page and
  `/api/v1/admin/devices` still showed `192.168.1.207`
  (`Erica''s R.R. Speaker`) `online` on `0.1.16-dev-central` with last
  heartbeat `2026-05-14T11:32:06Z`, while local `/api/status` +
  `/api/config` still exposed the standing desired-name drift as
  `Erica''s ?.?. Speaker`. The local root page plus `/api/status` plus
  `/api/config` all returned clean `200` responses (`0.13 s`, `0.02 s`,
  `0.07 s`), and local `/api/status` reported `uptime_seconds=873`.
  That uptime is consistent with continued survival since the prior
  `11:20:45Z` to `11:22:10Z` pass where the same endpoint reported
  `uptime_seconds=194`, so this recheck did not add a fresh reboot or
  response-corruption repro. Keep BUG-055 open because the earlier
  reboot/recovery windows and the desired-name drift remain concrete,
  but this pass improved it materially.

### 2026-05-14 live recheck addendum - `.48` rebooted again behind an `online` hub row while `.225` and `.207` stayed improved

- **BUG-054 latest recheck:** around `2026-05-14T11:42:29Z` to
  `11:43:06Z`, the renamed soak target on `192.168.1.48` still looked
  healthy in both the rendered Devices page and
  `/api/v1/admin/devices`: `Rebooter - renamed test` / `online` /
  `0.1.17-dev-central`, with last heartbeat `2026-05-14T11:41:41Z`.
  The local root page plus `/api/status` plus `/api/config` also all
  returned clean `200` responses (`0.24 s`, `0.02 s`, `0.09 s`), but
  local `/api/status` reported only `uptime_seconds=114`. That is fresh
  reboot evidence relative to the prior `11:31:57Z` to `11:32:09Z`
  clean loop where the same device had already climbed to
  `uptime_seconds=1121`-`1131`. The immediate 5-cycle local follow-up
  loop then stayed fully clean and climbed only from `145` to `150`.
  Keep BUG-054 open as another concrete reboot-through-healthy-row
  sample even though the local surfaces had already recovered by the
  time of the focused loop.
- **WATCH `.225` latest recheck:** in the same `11:42:29Z` to
  `11:43:06Z` window, the live hub Devices page and
  `/api/v1/admin/devices` still showed `192.168.1.225`
  (`Erica''s F.R Speaker`) `online` on `0.1.17-dev-central`, while
  local `/api/status` + `/api/config` still exposed the standing
  desired-name drift as `Rebooter`. The local root page plus
  `/api/status` plus `/api/config` all returned clean `200` responses
  (`0.30 s`, `0.02 s`, `0.06 s`), and local `/api/status` reported
  `uptime_seconds=2381`, so this pass did not add a fresh reboot
  signal.
- **BUG-055 latest recheck:** in the same `11:42:29Z` to
  `11:43:06Z` window, the live hub Devices page and
  `/api/v1/admin/devices` still showed `192.168.1.207`
  (`Erica''s R.R. Speaker`) `online` on `0.1.16-dev-central`, while
  local `/api/status` + `/api/config` still exposed `Erica''s ?.?. Speaker`.
  The local root page plus `/api/status` plus `/api/config` all
  returned clean `200` responses (`0.13 s`, `0.02 s`, `0.08 s`), and
  local `/api/status` reported `uptime_seconds=1469`. That uptime is
  consistent with continued survival since the prior `11:31:18Z` to
  `11:32:30Z` pass where the same endpoint reported `uptime_seconds=873`,
  so this recheck did not add a fresh reboot or response-integrity
  repro.

### BUG-056 - `.30` rebooted behind a healthy-looking `online` hub row

- **Severity:** medium (live reliability issue)
- **Area:** device reboot/recovery visibility
- **Status:** **open - observed live 2026-05-14**
- **Detail:** Around `2026-05-14T11:50:46Z` to `11:52:22Z`, the live
  hub Devices page and `/api/v1/admin/devices` both showed
  `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`, with fresh heartbeats through
  `2026-05-14T11:52:11Z`. The local root page plus `/api/status` plus
  `/api/config` all returned `200`, but local `/api/status` initially
  reported only `uptime_seconds=23`, then the immediate 5-cycle
  follow-up loop climbed only from `111` to `129`. That is impossible
  without a fresh reboot shortly before the sweep, yet the central hub
  never exposed a degraded state.
- **Latest recheck:** in the same window, the local root page stayed
  mostly responsive but still showed one slower `3.50 s` sample on
  cycle 5 while `/api/status` and `/api/config` remained fast. Keep
  this open as a masked reboot/recovery issue distinct from the older
  desired-name drift on the same device.
- **Fix direction:** inspect whether the hub should surface a
  recent-reboot / unstable state when device uptime resets behind an
  otherwise fresh heartbeat, and inspect the device for the cause of
  the unexpected restart.

### 2026-05-14 live recheck addendum - `.48` rebooted again, `.30` joined the masked-reboot bucket, `.207` rebooted again during the loop, and `.225` stayed improved

- **BUG-054 latest recheck:** around `2026-05-14T11:50:46Z` to
  `11:52:22Z`, the renamed soak target on `192.168.1.48` still looked
  healthy in both the rendered Devices page and
  `/api/v1/admin/devices`: `Rebooter - renamed test` / `online` /
  `0.1.17-dev-central`, with heartbeats through
  `2026-05-14T11:51:46Z`. The initial local root page plus
  `/api/status` plus `/api/config` all returned `200`
  (`1.02 s`, `0.02 s`, `0.08 s`), and local `/api/status` reported
  `uptime_seconds=593`. But the immediate 5-cycle follow-up loop
  starting at `11:51:54Z` already found local `/api/status` reset to
  `uptime_seconds=74`, then climb only to `90` by cycle 5. Keep
  BUG-054 open as another concrete reboot-through-healthy-row sample
  even though the local root/API surfaces stayed responsive during the
  focused loop.
- **BUG-056 latest recheck:** in the same `11:50:46Z` to `11:52:22Z`
  window, the live hub Devices page and `/api/v1/admin/devices` still
  showed `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central` with fresh heartbeats through
  `2026-05-14T11:52:11Z`. The local root page plus `/api/status` plus
  `/api/config` all returned `200`, but local `/api/status` initially
  reported only `uptime_seconds=23` and then climbed only from `111` to
  `129` in the immediate 5-cycle loop. Keep BUG-056 open as fresh
  proof that `.30` can also reboot behind a healthy-looking central row.
- **WATCH `.225` latest recheck:** in the same window, the live hub
  Devices page and `/api/v1/admin/devices` still showed
  `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`, while local `/api/status` + `/api/config` still
  exposed the standing desired-name drift as `Rebooter`. The local root
  page plus `/api/status` plus `/api/config` all returned clean `200`
  responses (`0.42 s`, `0.03 s`, `0.04 s`), and local `/api/status`
  climbed from `uptime_seconds=2861` to `2966`, so this pass kept `.225`
  in the improved watch bucket rather than adding fresh reboot evidence.
- **BUG-055 latest recheck:** in the same `11:50:46Z` to `11:52:22Z`
  window, the live hub Devices page and `/api/v1/admin/devices` still
  showed `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central` with heartbeats through
  `2026-05-14T11:52:22Z`, while local `/api/status` + `/api/config`
  still exposed `Erica''s ?.?. Speaker`. The initial local sweep
  returned clean `200` responses and local `/api/status` reported only
  `uptime_seconds=143`; the immediate 5-cycle loop first climbed from
  `231` to `242`, then cycle 5 hit a `10.49 s` local root-page stall
  while local `/api/status` dropped to `uptime_seconds=11` with
  `health_state="unknown"`. Keep BUG-055 open as renewed proof that
  `.207` still reboots and degrades locally while the hub keeps
  presenting it as healthy.

### 2026-05-14 live recheck addendum - `.48` returned to uptime continuity but still showed local root-page latency

- **BUG-054 latest recheck:** around `2026-05-14T12:00:37Z` to
  `12:01:52Z`, the renamed soak target on `192.168.1.48` again stayed
  converged across the rendered Devices page, `/api/v1/admin/devices`,
  and local `/api/status` + `/api/config` as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
  the admin API reporting last heartbeat `2026-05-14T12:00:46Z`. The
  initial local root page plus `/api/status` plus `/api/config` all
  returned `200` (`1.34 s`, `0.02 s`, `0.07 s`), and local
  `/api/status` reported `uptime_seconds=654`. The immediate 10-cycle
  follow-up loop then stayed fully clean and climbed to
  `uptime_seconds=671`, so this pass did not add a fresh reboot sample.
  Keep BUG-054 open, though: cycle 8 still stretched the local root
  page to `3.18 s`, and cycle 9 still took `1.11 s`, while
  `/api/status` and `/api/config` remained fast. That keeps the issue
  in the narrowed local-root-latency bucket rather than closing it.

### 2026-05-14 live recheck addendum - `.48` fell back into a masked reboot with a root timeout, `.207` rebooted again behind an online row, and `.30` / `.225` stayed improved

- **BUG-054 latest recheck:** around `2026-05-14T12:23:58Z` to
  `12:24:54Z`, the renamed soak target on `192.168.1.48` still looked
  healthy in both the rendered Devices page and
  `/api/v1/admin/devices`: `Rebooter - renamed test` / `online` /
  `0.1.17-dev-central`, with heartbeats moving from
  `2026-05-14T12:21:54Z` before the local loop to `2026-05-14T12:24:11Z`
  after it. But the initial local sweep hit a full root-page timeout,
  local `/api/status` took `5.71 s` and reported only
  `uptime_seconds=14` with `health_state="unknown"`, and only local
  `/api/config` stayed fast (`0.06 s`). The immediate 5-cycle follow-up
  loop from `12:24:46Z` to `12:24:50Z` then recovered and climbed only
  from `uptime_seconds=45` to `50`. Keep BUG-054 open as another
  reboot-through-healthy-row sample, now strengthened by a fresh local
  root timeout at first contact.
- **BUG-056 latest recheck:** in the same `12:23:58Z` to `12:24:54Z`
  window, the live hub Devices page and `/api/v1/admin/devices` still
  showed `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`. The local root page plus `/api/status` plus
  `/api/config` all returned clean `200` responses (`0.35 s`, `0.04 s`,
  `0.06 s`), and local `/api/status` reported `uptime_seconds=2050`, so
  this pass did not add a fresh reboot signal beyond the already-open
  issue.
- **WATCH `.225` latest recheck:** in the same window, the live hub
  Devices page and `/api/v1/admin/devices` still showed
  `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`, while local `/api/status` + `/api/config` still
  exposed the standing desired-name drift as `Rebooter`. The local root
  page plus `/api/status` plus `/api/config` all returned clean `200`
  responses (`0.20 s`, `0.02 s`, `0.07 s`), and local `/api/status`
  reported `uptime_seconds=4887`, so this pass kept `.225` in the
  improved watch bucket.
- **BUG-055 latest recheck:** in the same `12:23:58Z` to `12:24:54Z`
  window, the live hub Devices page and `/api/v1/admin/devices` still
  showed `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central`, with heartbeats from `2026-05-14T12:23:00Z` to
  `12:24:00Z`, while local `/api/status` + `/api/config` still exposed
  `Erica''s ?.?. Speaker`. The local root page plus `/api/status` plus
  `/api/config` all returned clean `200` responses (`0.44 s`, `0.02 s`,
  `0.08 s`), and the confirming 3-cycle loop stayed responsive, but
  local `/api/status` reported only `uptime_seconds=83` on the initial
  sweep and climbed only to `121` by the end of the loop. Relative to
  the earlier `12:12:34Z` to `12:13:14Z` pass where the same endpoint
  had already reached `uptime_seconds=1190`, keep BUG-055 open as fresh
  reboot evidence that stayed masked by the central row.

### 2026-05-14 live recheck addendum - `.48` recovered from the last masked reboot but still corrupted the local root page, `.30` rebooted again, and `.207` stayed low-uptime behind healthy hub rows

- **BUG-054 latest recheck:** around `2026-05-14T12:30:34Z` to
  `12:32:29Z`, the renamed soak target on `192.168.1.48` again stayed
  converged across the rendered Devices page, `/api/v1/admin/devices`,
  and local `/api/status` + `/api/config` as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
  the hub row showing last heartbeat `2026-05-14T12:31:11Z`. The
  initial local root page plus `/api/status` plus `/api/config` all
  returned `200` (`0.22 s`, `0.02 s`, `0.09 s`), and local
  `/api/status` reported `uptime_seconds=395`. The immediate 10-cycle
  follow-up loop then kept local `/api/status` climbing from
  `uptime_seconds=485` to `503`, so this pass did not add a fresh
  reboot sample. Keep BUG-054 open, though: cycle 5 still stretched the
  local root page to about `3.90 s`, and cycle 9 hit another
  truncated-body `ChunkedEncodingError`
  (`IncompleteRead(13936 bytes read, 2087 more expected)`) while local
  `/api/status` and `/api/config` remained fast. That keeps the bug
  concrete in local-root integrity form even during an otherwise
  recovered uptime window.
- **BUG-056 latest recheck:** in the same `12:30:34Z` to `12:32:29Z`
  window, the live hub Devices page and `/api/v1/admin/devices` still
  showed `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`, with heartbeats through
  `2026-05-14T12:31:18Z`. The local root page plus `/api/status` plus
  `/api/config` all returned clean `200` responses (`0.35 s`, `0.02 s`,
  `0.07 s`), but the first local `/api/status` read already reported
  only `uptime_seconds=22` with `health_state="unknown"`, and the
  confirming 3-cycle loop climbed only from `131` to `134`. Relative to
  the prior `12:23:58Z` to `12:24:54Z` pass where the same device had
  already reached `uptime_seconds=2050`, keep BUG-056 open as renewed
  masked-reboot evidence behind a healthy-looking central row.
- **WATCH `.225` latest recheck:** in the same window, the live hub
  Devices page and `/api/v1/admin/devices` still showed
  `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`, while local `/api/status` + `/api/config` still
  exposed the standing desired-name drift as `Rebooter`. The local root
  page plus `/api/status` plus `/api/config` all returned clean `200`
  responses (`0.22 s`, `0.02 s`, `0.08 s`), and local `/api/status`
  reported `uptime_seconds=5267`, so this pass kept `.225` in the
  improved watch bucket.
- **BUG-055 latest recheck:** in the same `12:30:34Z` to `12:32:29Z`
  window, the live hub Devices page and `/api/v1/admin/devices` still
  showed `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central`, with heartbeats through
  `2026-05-14T12:31:41Z`, while local `/api/status` + `/api/config`
  still exposed `Erica''s ?.?. Speaker`. The initial local sweep
  returned clean `200` responses (`0.34 s`, `0.03 s`, `0.08 s`) but
  local `/api/status` reported only `uptime_seconds=122`; the later
  confirming 3-cycle loop stayed responsive and climbed from `233` to
  `235`. Relative to the earlier `12:24:51Z` to `12:24:53Z` pass where
  `.207` had already climbed to `uptime_seconds=121`, keep BUG-055 open
  as fresh reboot evidence that again stayed masked by the central row.

### 2026-05-14 live recheck addendum - `.48` returned to a clean recovery window, while `.30` and `.207` showed uptime continuity instead of a fresh repro

- **BUG-054 latest recheck:** around `2026-05-14T12:40:51Z` to
  `12:41:54Z`, the renamed soak target on `192.168.1.48` again stayed
  converged across the rendered Devices page, `/api/v1/admin/devices`,
  and local `/api/status` + `/api/config` as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
  the hub row showing heartbeats through `2026-05-14T12:41:11Z`. The
  initial local root page plus `/api/status` plus `/api/config` all
  returned `200` (`0.47 s`, `0.03 s`, `0.06 s`), and local
  `/api/status` reported `uptime_seconds=1003`. The immediate 5-cycle
  follow-up loop from `12:41:30Z` to `12:41:38Z` then stayed fully
  clean and climbed from `uptime_seconds=1050` to `1058`, so this pass
  did not add a fresh reboot, stall, or truncated-body sample. Keep
  BUG-054 open historically, but treat this window as another improved
  confirmation pass.
- **BUG-056 latest recheck:** in the same `12:40:51Z` to `12:41:54Z`
  window, the live hub Devices page and `/api/v1/admin/devices` still
  showed `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`, with heartbeats through
  `2026-05-14T12:41:19Z`. The local root page plus `/api/status` plus
  `/api/config` all returned clean `200` responses (`0.18 s`, `0.02 s`,
  `0.07 s`), and local `/api/status` reported `uptime_seconds=630`; the
  immediate 5-cycle loop then climbed from `678` to `686`. Relative to
  the prior `12:30:34Z` to `12:32:29Z` pass where the same device had
  already climbed from `131` to `134`, this is consistent with uptime
  continuity rather than a fresh masked reboot. Keep BUG-056 open, but
  this pass improved it back into the watch-only bucket.
- **WATCH `.225` latest recheck:** in the same window, the live hub
  Devices page and `/api/v1/admin/devices` still showed
  `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`, while local `/api/status` + `/api/config` still
  exposed the standing desired-name drift as `Rebooter`. The local root
  page plus `/api/status` plus `/api/config` all returned clean `200`
  responses (`0.24 s`, `0.02 s`, `0.09 s`), and local `/api/status`
  reported `uptime_seconds=5875`, so this pass kept `.225` in the
  improved watch bucket.
- **BUG-055 latest recheck:** in the same `12:40:51Z` to `12:41:54Z`
  window, the live hub Devices page and `/api/v1/admin/devices` still
  showed `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central`, with heartbeats through
  `2026-05-14T12:41:41Z`, while local `/api/status` + `/api/config`
  still exposed `Erica''s ?.?. Speaker`. The initial local root page
  plus `/api/status` plus `/api/config` all returned clean `200`
  responses (`0.83 s`, `0.04 s`, `0.07 s`), and local `/api/status`
  reported `uptime_seconds=731`; the immediate 5-cycle loop then
  climbed from `777` to `785` without another stall or truncated-body
  failure. Relative to the prior `12:30:34Z` to `12:32:29Z` pass where
  the same device had already climbed from `233` to `235`, this is
  consistent with uptime continuity rather than a fresh reboot. Keep
  BUG-055 open, but this pass improved it back out of the fresh-repro
  bucket.

### 2026-05-14 live recheck addendum - `.48` stayed clean through another short soak window, while `.30` and `.207` continued upward without a fresh masked reboot

- **BUG-054 latest recheck:** around `2026-05-14T12:50:43Z` to
  `12:51:23Z`, the renamed soak target on `192.168.1.48` again stayed
  converged across the rendered Devices page, `/api/v1/admin/devices`,
  and local `/api/status` + `/api/config` as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
  the hub row showing heartbeat `2026-05-14T12:51:11Z`. The initial
  local root page plus `/api/status` plus `/api/config` all returned
  `200` (`0.34 s`, `0.02 s`, `0.08 s`), and local `/api/status`
  reported `uptime_seconds=1603`. The immediate 5-cycle follow-up loop
  from `12:51:16Z` to `12:51:21Z` then stayed fully clean and climbed
  from `uptime_seconds=1636` to `1640`, with root-page reads around
  `0.10 s`-`0.13 s`. This pass did not add a fresh reboot, stall, or
  truncated-body sample; treat it as another concrete improvement
  window while keeping BUG-054 open historically.
- **BUG-056 latest recheck:** in the same `12:50:43Z` to `12:51:23Z`
  window, the live hub Devices page and `/api/v1/admin/devices` still
  showed `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`, with heartbeat `2026-05-14T12:51:18Z`, while
  local `/api/status` + `/api/config` still exposed the standing
  desired-name drift as `Rebooter`. The initial local root page plus
  `/api/status` plus `/api/config` all returned clean `200` responses
  (`0.22 s`, `0.02 s`, `0.07 s`) and local `/api/status` reported
  `uptime_seconds=1231`; the confirming re-read later in the pass still
  returned clean `200` responses and `uptime_seconds=1269`. One
  `1.20 s` root-page sample did not coincide with any status/config
  failure or reset, so this pass is consistent with uptime continuity
  rather than another fresh masked reboot. Keep BUG-056 open, but in
  the improved watch bucket.
- **WATCH `.225` latest recheck:** in the same window, the live hub
  Devices page and `/api/v1/admin/devices` still showed
  `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`, while local `/api/status` + `/api/config` still
  exposed the standing desired-name drift as `Rebooter`. The local root
  page plus `/api/status` plus `/api/config` all returned clean `200`
  responses (`0.43 s`, `0.04 s`, `0.16 s`), and local `/api/status`
  reported `uptime_seconds=6476`, so this pass kept `.225` in the
  improved watch bucket.
- **BUG-055 latest recheck:** in the same `12:50:43Z` to `12:51:23Z`
  window, the live hub Devices page and `/api/v1/admin/devices` still
  showed `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central`, with heartbeat `2026-05-14T12:51:41Z`, while
  local `/api/status` + `/api/config` still exposed
  `Erica''s ?.?. Speaker`. The initial local root page plus
  `/api/status` plus `/api/config` all returned clean `200` responses
  (`0.39 s`, `0.02 s`, `0.07 s`), and local `/api/status` reported
  `uptime_seconds=1331`; the confirming re-read later in the pass again
  returned clean `200` responses and `uptime_seconds=1368`. That is
  consistent with uptime continuity rather than another fresh reboot or
  stall, so keep BUG-055 open historically but out of the fresh-repro
  bucket in this pass.

### 2026-05-14 live recheck addendum - `.48` rebooted again with a transient local API reset, `.207` rebooted again behind an online row, and `.225` only added watch-level root wobble

- **BUG-054 latest recheck:** around `2026-05-14T13:00:56Z` to
  `13:04:04Z`, the renamed soak target on `192.168.1.48` still stayed
  converged across the rendered Devices page, `/api/v1/admin/devices`,
  and local `/api/status` + `/api/config` as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
  the hub row showing heartbeat `2026-05-14T13:00:56Z`. But the first
  local root-page read stretched to `4.03 s`, the first local
  `/api/status` call was connection-reset after `9.56 s`, and the next
  successful local `/api/status` sample already showed only
  `uptime_seconds=93`. The immediate 5-cycle follow-up loop then
  recovered cleanly and climbed from `uptime_seconds=162` to `167`.
  Relative to the prior `12:50:43Z` to `12:51:23Z` pass where the same
  device had already reached `uptime_seconds=1640`, this is fresh
  masked reboot evidence plus another transient local UI/API failure.
- **BUG-056 latest recheck:** in the same `13:00:56Z` to `13:04:04Z`
  window, the live hub Devices page and `/api/v1/admin/devices` still
  showed `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`, while local `/api/status` + `/api/config`
  still exposed the standing desired-name drift as `Rebooter`. The
  local root page plus `/api/status` plus `/api/config` all returned
  clean `200` responses (`0.15 s`, `0.02 s`, `0.07 s`), and local
  `/api/status` reported `uptime_seconds=1846`, so this pass stayed in
  uptime-continuity territory rather than another fresh masked reboot.
- **WATCH `.225` latest recheck:** in the same window, the live hub
  Devices page and `/api/v1/admin/devices` still showed
  `192.168.1.225` (`Erica''s F.R Speaker`) `online` on
  `0.1.17-dev-central`, while local `/api/status` + `/api/config` still
  exposed the standing desired-name drift as `Rebooter`. The first
  local root-page read hit a `3.34 s` truncated-body
  `ChunkedEncodingError`, but local `/api/status` reported
  `uptime_seconds=7094`, and the immediate 5-cycle follow-up loop then
  kept root/status/config fully clean while local `/api/status` climbed
  from `7255` to `7260`. Keep this below bug level unless a later soak
  reproduces the same root failure.
- **BUG-055 latest recheck:** in the same `13:00:56Z` to `13:04:04Z`
  window, the live hub Devices page and `/api/v1/admin/devices` still
  showed `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central`, with heartbeat `2026-05-14T13:01:47Z`, while
  local `/api/status` + `/api/config` still exposed
  `Erica''s ?.?. Speaker`. The first local `/api/status` sample
  reported only `uptime_seconds=82`, a later confirming sample still
  reported only `161`, and the immediate 5-cycle follow-up loop then
  climbed from `236` to `241`. Relative to the prior `12:50:43Z` to
  `12:51:23Z` pass where `.207` had already reached
  `uptime_seconds=1368`, this is another fresh masked reboot even
  though the local surfaces had recovered by the time of the loop.
- **BUG-054 latest recheck:** around `2026-05-14T13:10:45Z` to
  `13:12:21Z`, the renamed soak target on `192.168.1.48` again stayed
  converged across the rendered Devices page, `/api/v1/admin/devices`,
  and local `/api/status` + `/api/config` as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
  the hub row showing heartbeat `2026-05-14T13:11:56Z`. The initial
  local root page plus `/api/status` plus `/api/config` all returned
  clean `200` responses (`0.35 s`, `0.02 s`, `0.08 s`), local
  `/api/status` already reported `uptime_seconds=598`, and the
  immediate 5-cycle follow-up loop then stayed clean while local
  `/api/status` climbed from `687` to `693`. Only one root-page sample
  stretched to `1.12 s`, which is materially better than the prior
  `4.03 s` + connection-reset failure window. Keep BUG-054 open, but
  this pass improved it back out of the immediate fresh-repro bucket.
- **BUG-055 latest recheck:** around `2026-05-14T13:10:45Z` to
  `13:12:30Z`, the live hub Devices page and `/api/v1/admin/devices`
  still showed `192.168.1.207` (`Erica''s R.R. Speaker`) `online` on
  `0.1.16-dev-central`, with heartbeat `2026-05-14T13:11:53Z`, while
  local `/api/status` + `/api/config` still exposed
  `Erica''s ?.?. Speaker`. The initial local root page plus
  `/api/status` plus `/api/config` all returned clean `200` responses
  (`0.23 s`, `0.03 s`, `0.07 s`), local `/api/status` already reported
  `uptime_seconds=301`, and the immediate 5-cycle follow-up loop then
  stayed clean while local `/api/status` climbed from `399` to `404`.
  The first loop root-page sample at `2.48 s` did not coincide with a
  reset, truncated body, or local API failure. Keep BUG-055 open
  historically, but this pass improved it back out of the immediate
  fresh-repro bucket.

### 2026-05-14 live recheck addendum - `.48` stayed converged and added another clean uptime-continuity window, with only root-page latency left in scope

- **BUG-054 latest recheck:** around `2026-05-14T13:22:34Z` to
  `13:22:44Z`, the renamed soak target on `192.168.1.48` still stayed
  converged across the rendered Devices page, `/api/v1/admin/devices`,
  and local `/api/status` + `/api/config` as
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
  the hub row and API both showing last heartbeat
  `2026-05-14T13:21:56Z`. The initial local root page plus
  `/api/status` plus `/api/config` all returned clean `200` responses
  (`0.14 s`, `0.02 s`, `0.07 s`), local `/api/status` reported
  `health_state="healthy"` and `uptime_seconds=1307`, and the
  immediate 5-cycle follow-up loop then stayed fully clean while local
  `/api/status` climbed from `1307` to `1314`. The only remaining
  signal was intermittent local root-page latency: the 5-cycle loop
  peaked at `1.34 s`, and an earlier 10-cycle local loop in the same
  recheck window saw one slower first-hit root sample at `4.25 s` and
  a later `1.40 s` read while `/api/status` and `/api/config` stayed
  fast and local uptime climbed from `1240` to `1253`. Keep BUG-054
  open, but this pass added no fresh reboot, connection-reset, or
  truncated-body repro.
- **BUG-054 latest recheck:** around `2026-05-14T13:31:41Z` to
  `13:34:29Z`, the renamed soak target on `192.168.1.48` again stayed
  aligned on identity/version between the rendered Devices list and
  `/api/v1/admin/devices`: both still showed
  `Rebooter - renamed test` `online` on `0.1.17-dev-central`, first
  with heartbeat `2026-05-14T13:31:41Z` and later
  `2026-05-14T13:33:46Z`. But the local device had already restarted
  again by the first sweep: root/status/config returned `200` at
  `2.31 s`, `0.03 s`, and `0.08 s`, yet local `/api/status` reported
  only `uptime_seconds=132` relative to the prior `13:22:44Z` clean
  loop where the same device had already reached `1314`.
- In the same pass, the local surfaces then degraded again in the
  stronger root-plus-status-stall shape. A later direct local `/` fetch
  timed out after the full `10 s`, the next local `/api/status` fetch
  also timed out at `10 s`, and local `/api/config` returned `200`
  only after `7.30 s`.
- A full local `/api/status` payload immediately after that stall
  showed `device_name="Rebooter - renamed test"`,
  `firmware_version="0.1.17-dev-central"`,
  `health_state="unknown"`, and `uptime_seconds=23`, then the
  confirming 3-cycle recovery loop climbed only from `47` to `49`.
  The hub device detail page partially reflected that same restart with
  heartbeat `2026-05-14T13:33:46Z`, `health: unknown`, and
  `uptime_s: 0`, but the hub list/API still presented the device as
  `online`. Keep BUG-054 open as fresh repeated masked reboot evidence
  plus another local root/status stall window.

### 2026-05-14 live recheck addendum - `.48` recovered cleanly again after the `13:34Z` stall window

- **BUG-054 latest recheck:** around `2026-05-14T13:40:34Z` to
  `13:41:04Z`, the renamed soak target on `192.168.1.48` recovered into
  another short fully converged window across the rendered Devices page,
  the hub device detail page, `/api/v1/admin/devices`,
  `/api/v1/admin/devices/<id>`, and local `/api/status` +
  `/api/config` as `Rebooter - renamed test` / `online` /
  `0.1.17-dev-central`.
- In the same pass, the hub detail page showed last seen
  `2026-05-14T13:40:53Z`, `health: healthy`, and `uptime_s: 306`, while
  the admin detail API reported the same heartbeat timestamp with
  `latest_heartbeat.health_state="healthy"` and
  `latest_heartbeat.uptime_seconds=306`.
- The first direct local root/status/config sweep also returned clean
  `200` responses (`0.315 s`, `0.022 s`, `0.078 s`), with local
  `/api/status` reporting `health_state="healthy"` and
  `uptime_seconds=290`.
- The immediate 5-cycle local follow-up loop then stayed clean while
  local `/api/status` climbed from `312` to `318`; root-page reads held
  at `0.128 s`-`0.190 s`, `/api/status` at `0.021 s`-`0.257 s`, and
  `/api/config` at `0.035 s`-`0.142 s`. This pass added no fresh
  reboot, timeout, connection-reset, or truncated-body repro, so keep
  BUG-054 open historically but treat this window as another concrete
  recovery sample.
- **BUG-054 latest recheck:** around `2026-05-14T14:00:53Z` to
  `14:01:03Z`, the renamed soak target on `192.168.1.48` again stayed
  aligned on identity/version between the rendered Devices page and
  `/api/v1/admin/devices`: both still showed
  `Rebooter - renamed test` `online` on `0.1.17-dev-central`, with
  fresh heartbeats at `2026-05-14T14:00:53Z` and `2026-05-14T14:01:03Z`.
- But the local device had already restarted again relative to the
  prior clean `13:51:23Z` to `13:51:27Z` window where local
  `/api/status` had already reached `uptime_seconds=941`. In this pass,
  the first local `/api/status` reread returned `200` in `0.127 s` but
  reported `health_state="unknown"` and only `uptime_seconds=12`,
  while local `/api/config` still returned `200` in `0.097 s` and still
  exposed `Rebooter - renamed test`.
- The same pass also reproduced another first-hit local root failure:
  the first direct local `/` request timed out at the full `15 s`,
  while the immediate 5-cycle recovery loop then returned clean `200`
  responses on `/`, `/api/status`, and `/api/config` with local
  `/api/status.uptime_seconds` climbing only from `13` to `18`.
- A confirming reread a few seconds later showed the central split
  explicitly. The hub list API still kept the device in the `online`
  bucket with heartbeat `2026-05-14T14:01:03Z`, but the hub detail API
  had already dropped to `latest_heartbeat.health_state="unknown"` and
  `latest_heartbeat.uptime_seconds=0`. The local device then recovered
  to `health_state="healthy"` at `uptime_seconds=38`, which confirms
  this was another short masked reboot/recovery window rather than a
  stale one-off sample.
- **BUG-054 latest recheck:** around `2026-05-14T14:10:33Z` to
  `14:14:57Z`, the renamed soak target on `192.168.1.48` reproduced
  the stronger masked-reboot shape again after the earlier `14:01Z`
  recovery. The first pass still showed the hub devices page,
  `/api/v1/admin/devices`, and `/api/v1/admin/devices/<id>` converged
  on `Rebooter - renamed test` `online` on `0.1.17-dev-central`, while
  local `/api/status` reported `uptime_seconds=579` and the hub detail
  API still reported `latest_heartbeat.uptime_seconds=424`. But by the
  follow-up reread four minutes later, the hub list/API still showed
  the device `online` with fresh heartbeat `2026-05-14T14:14:10Z`
  while hub detail had already fallen back to
  `latest_heartbeat.uptime_seconds=124` and local `/api/status` had
  reset to `uptime_seconds=142`. The earlier `14:10:33Z` local root
  page also stretched to about `3.06 s` before the `14:14:57Z` reread
  recovered to about `0.13 s`. Keep the bug open as another confirmed
  reboot-through-healthy-row event plus a weaker root-latency repro.

### BUG-057 - Offline device detail API can still present a stale heartbeat as healthy

- **Severity:** medium (operator diagnosis confusion)
- **Area:** hub device-detail heartbeat presentation
- **Status:** **open - observed live 2026-05-14**
- **Detail:** On repeated live checks around `2026-05-14T14:10Z` and
  `14:14Z`, the hub devices page and `/api/v1/admin/devices` both
  showed `192.168.1.69` (`Erica''s R.L. Speaker`) as `offline` with
  stale `last_heartbeat_at="2026-05-13T22:06:13Z"`, and the local
  device timed out from this host on `/`, `/api/status`, and
  `/api/config`. But `/api/v1/admin/devices/<id>` for the same device
  still exposed `latest_heartbeat.health_state="healthy"`,
  `latest_heartbeat.uptime_seconds=69`, and
  `latest_heartbeat.last_event_type="boot"` at that same stale
  `received_at="2026-05-13T22:06:13Z"`.
- **Impact:** An operator who drills into the device detail can see a
  materially healthier state than the list row and the locally
  unreachable device justify. The list says `offline`; the detail API
  still says the latest heartbeat was `healthy` without signaling that
  the sample is stale beyond the same timestamp.
- **Fix direction:** when a heartbeat ages past the offline threshold,
  the detail surface should either derive a stale/offline presentation
  from that age or explicitly annotate the last heartbeat as historical
  rather than still semantically healthy.

- **BUG-054 latest recheck:** around `2026-05-14T14:22:53Z` to
  `14:25:18Z`, the renamed soak target on `192.168.1.48` recovered into
  another clean convergence window. The rendered Devices row, hub
  detail page, `/api/v1/admin/devices`, `/api/v1/admin/devices/<id>`,
  and local `/api/status` + `/api/config` all showed
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
  the hub detail page/API aligned on heartbeat `2026-05-14T14:23:10Z`,
  `health: healthy`, and `uptime_s` / `uptime_seconds=664`.
- The first direct local root/status/config sweep also returned clean
  `200` responses (`0.233 s`, `0.023 s`, `0.071 s`), with local
  `/api/status` already at `uptime_seconds=648`. The immediate 5-cycle
  local follow-up loop then stayed fully clean while local
  `/api/status` climbed from `648` to `703`, and a later focused
  reread still showed the hub list/API presenting `.48` `online` with
  heartbeat `2026-05-14T14:25:10Z` while local `/api/status` had
  continued up to `uptime_seconds=804`. Keep BUG-054 open
  historically, but treat this pass as another concrete recovery
  sample.

- **BUG-055 latest recheck:** around `2026-05-14T14:22:53Z` to
  `14:25:13Z`, `.207` moved through another masked reboot/recovery
  cycle. In the first sweep, the hub devices page and
  `/api/v1/admin/devices` had already converged on `offline` with
  stale `last_heartbeat_at="2026-05-14T14:18:15Z"`, but the local
  device still half-answered: the root page returned `200` only after
  `5.006 s`, local `/api/status` failed after `9.571 s` with a
  connection reset, and local `/api/config` still returned `200` in
  `0.091 s` with `device_name="Erica's ?.?. Speaker"`.
- A focused reread a couple of minutes later showed the hub devices
  page and `/api/v1/admin/devices` back to `online` on
  `0.1.16-dev-central` with heartbeat `2026-05-14T14:25:13Z`, while the
  local root page, `/api/status`, and `/api/config` all returned clean
  `200` responses again. But local `/api/status` reported only
  `uptime_seconds=64`, and `/api/v1/admin/devices/<id>` showed
  `latest_heartbeat.health_state="healthy"` with
  `latest_heartbeat.uptime_seconds=64`. Keep BUG-055 open as fresh
  reboot/recovery evidence plus another local-surface stall/reset
  sample in the same pass.

- **BUG-057 latest recheck:** around `2026-05-14T14:24Z` to `14:25Z`,
  the hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.69` (`Erica''s R.L. Speaker`) as `offline` with stale
  `last_heartbeat_at="2026-05-13T22:06:13Z"`, and the local device
  still timed out on `/`, `/api/status`, and `/api/config` after about
  `12 s`. The rendered hub detail page now clearly reproduced the same
  optimism as the detail API, still showing `health: healthy` and
  `uptime_s: 69` from that stale heartbeat sample. Keep BUG-057 open as
  a list-vs-detail presentation bug, not just a detail-API bug.

- **BUG-054 latest recheck:** around `2026-05-14T14:31:44Z` to
  `14:33:27Z`, the renamed soak target on `192.168.1.48` stayed in a
  stronger recovery window. The rendered Devices row, the hub detail
  page, `/api/v1/admin/devices`, `/api/v1/admin/devices/<id>`, and
  local `/api/status` + `/api/config` all still showed
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
  the hub detail API reporting `latest_heartbeat.health_state="healthy"`
  and `uptime_seconds=1264` at `2026-05-14T14:33:10Z`.
- The first direct local root/status/config sweep returned clean `200`
  responses (`0.225 s`, `0.021 s`, `0.078 s`) with local
  `/api/status.uptime_seconds=1180`, and the immediate 5-cycle local
  follow-up loop also stayed clean while local `/api/status` climbed
  from `1275` to `1281`. Cycle 5 did slow the local root page to
  `1.39 s`, but this pass added no fresh reboot, timeout,
  connection-reset, or truncated-body repro. Keep BUG-054 open
  historically, but treat this recheck as another concrete improvement
  sample.

- **BUG-055 latest recheck:** around `2026-05-14T14:31:44Z` to
  `14:33:27Z`, `.207` improved relative to the prior masked reboot at
  `14:22Z`-`14:25Z`. The hub devices page and `/api/v1/admin/devices`
  still showed it `online` on `0.1.16-dev-central` with heartbeat
  `2026-05-14T14:33:13Z`, the hub detail API reported
  `latest_heartbeat.health_state="healthy"` with
  `uptime_seconds=544`, and the local device returned clean `200`
  responses on `/api/status` and `/api/config` with local
  `/api/status.uptime_seconds=560`.
- One local `/api/status` read stretched to `1.006 s`, but there was no
  fresh low-uptime reset, root-page stall, or connection-reset failure
  in this pass. Keep BUG-055 open historically, but count this window
  as a short operational recovery rather than another new repro.

- **BUG-057 latest recheck:** around `2026-05-14T14:31:44Z` to
  `14:33:27Z`, the hub devices page and `/api/v1/admin/devices` still
  showed `192.168.1.69` (`Erica''s R.L. Speaker`) as `offline` with
  stale `last_heartbeat_at="2026-05-13T22:06:13Z"`, while local `/`,
  `/api/status`, and `/api/config` still timed out after about `15 s`.
- But `/api/v1/admin/devices/<id>` still exposed
  `latest_heartbeat.health_state="healthy"` and
  `latest_heartbeat.uptime_seconds=69` at that same stale timestamp.
  The central detail optimism remains concrete and unchanged.

- **BUG-054 latest recheck:** around `2026-05-14T14:40:52Z` to
  `14:42:17Z`, the renamed soak target on `192.168.1.48` regressed
  again immediately after the prior `14:31:44Z` to `14:33:27Z`
  recovery window. The rendered Devices row and
  `/api/v1/admin/devices` still showed
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central` with
  last heartbeat `2026-05-14T14:40:16Z`, and the first direct local
  root/status/config sweep still returned clean `200` responses
  (`0.220 s`, `0.022 s`, `0.077 s`).
- But that same first local `/api/status` sample already reported only
  `uptime_seconds=42`, while `/api/v1/admin/devices/<id>` had already
  dropped to `latest_heartbeat.health_state="unknown"` with
  `latest_heartbeat.uptime_seconds=0` at the same `14:40:16Z`
  heartbeat. Relative to the prior clean window where local
  `/api/status` had already climbed to `1281`, this is another fresh
  masked reboot behind a still-healthy list row.
- An immediate 5-cycle local follow-up loop from `14:42:02Z` to
  `14:42:07Z` then recovered cleanly while local `/api/status` climbed
  from `111` to `117`, and a focused hub detail-page reread at
  `14:42:17Z` rendered `health: healthy` with `uptime_s: 124`. Keep
  BUG-054 open as another reboot/recovery sample, but note that this
  pass did not reproduce the earlier root-timeout, connection-reset, or
  truncated-body failure shapes.

- **BUG-055 latest recheck:** around `2026-05-14T14:40:52Z`, `.207`
  stayed in a short operational recovery window rather than producing a
  fresh repro. The hub devices page and `/api/v1/admin/devices` still
  showed it `online` on `0.1.16-dev-central` with heartbeat
  `2026-05-14T14:40:13Z`, the rendered hub detail page showed
  `health: healthy` with `uptime_s: 964`, and local `/api/status`
  returned `200` in `0.023 s` with `uptime_seconds=1006`.
- Local `/` and `/api/config` also returned clean `200` responses, so
  this pass added no fresh reboot, stall, or reset evidence. Keep
  BUG-055 open historically because of the earlier `14:22Z` to `14:25Z`
  reboot/recovery sample, but count this window as improved.

- **BUG-057 latest recheck:** around `2026-05-14T14:40:52Z` to
  `14:42:17Z`, the hub devices page and `/api/v1/admin/devices` still
  showed `192.168.1.69` (`Erica''s R.L. Speaker`) as `offline` with
  stale `last_heartbeat_at="2026-05-13T22:06:13Z"`, while local `/`,
  `/api/status`, and `/api/config` still timed out after about `15 s`.
- The rendered hub detail page still showed `health: healthy` and
  `uptime_s: 69` from that stale heartbeat sample. The list-vs-detail
  optimism remains concrete and unchanged.

- **BUG-054 latest recheck:** around `2026-05-14T14:51:03Z` to
  `14:53Z`, the renamed soak target on `192.168.1.48` stayed in a clean
  recovery window rather than reproducing another fresh reboot. The
  rendered Devices row, hub detail page, `/api/v1/admin/devices`,
  `/api/v1/admin/devices/<id>`, and local `/api/status` + `/api/config`
  all converged on `Rebooter - renamed test` / `online` /
  `0.1.17-dev-central`, with the hub detail page/API aligned on
  heartbeat `2026-05-14T14:51:56Z`, `health: healthy`, and
  `uptime_s` / `uptime_seconds=186`.
- The first direct local root/status/config sweep returned clean `200`
  responses (`0.218 s`, `0.014 s`, `0.062 s`) with local
  `/api/status.uptime_seconds=251`, and an immediate 5-cycle local
  follow-up loop then stayed fully clean while local `/api/status`
  climbed from `297` to `302`. Keep BUG-054 open historically because
  the earlier masked-reboot samples remain concrete, but this pass
  added only a fresh recovery sample.

- **BUG-056 latest recheck:** around `2026-05-14T14:51:03Z` to `14:53Z`,
  `.30` moved back into the masked-reboot bucket. The rendered hub
  devices row, hub detail page, `/api/v1/admin/devices`, and
  `/api/v1/admin/devices/<id>` all still showed
  `192.168.1.30` (`Erica''s Subwoofer`) `online` on
  `0.1.17-dev-central`, with heartbeat `2026-05-14T14:52:25Z`,
  `health: healthy`, and `uptime_s` / `uptime_seconds=664`.
- But the prior `2026-05-14T14:40:52Z` recheck had already reached
  local `/api/status.uptime_seconds=7840`, while this pass's direct
  local root/status/config sweep returned clean `200` responses
  (`0.322 s`, `0.015 s`, `0.067 s`) at only `uptime_seconds=700`.
  Treat that as another fresh reboot behind a healthy-looking hub row,
  not merely the standing `.30` desired-name drift.

- **BUG-055 latest recheck:** around `2026-05-14T14:51:03Z` to `14:53Z`,
  `.207` stayed in a continued recovery window. The hub devices page,
  hub detail page, `/api/v1/admin/devices`, and
  `/api/v1/admin/devices/<id>` still showed it `online` on
  `0.1.16-dev-central` with heartbeat `2026-05-14T14:51:13Z`,
  `health: healthy`, and `uptime_s` / `uptime_seconds=1624`.
- Local `/`, `/api/status`, and `/api/config` also returned clean `200`
  responses (`0.151 s`, `0.015 s`, `0.064 s`), with local
  `/api/status.uptime_seconds=1732`. This pass added no fresh reboot,
  root-page stall, or connection-reset evidence; keep BUG-055 open
  historically only.

- **BUG-057 latest recheck:** around `2026-05-14T14:51:03Z` to `14:53Z`,
  the hub devices page and `/api/v1/admin/devices` still showed
  `192.168.1.69` (`Erica''s R.L. Speaker`) as `offline` with stale
  `last_heartbeat_at="2026-05-13T22:06:13Z"`, while local `/`,
  `/api/status`, and `/api/config` still timed out after about `15 s`.
- The rendered hub detail page and `/api/v1/admin/devices/<id>` still
  showed `health: healthy` and `uptime_s` / `uptime_seconds=69` from
  that stale heartbeat sample. The detail optimism remains concrete and
  unchanged.

- **BUG-054 latest recheck:** around `2026-05-14T15:00:54Z` to
  `15:02:15Z`, the renamed soak target on `192.168.1.48` stayed in a
  clean recovery window. The hub row/detail and local
  `/api/status` + `/api/config` all still converged on
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, the
  first direct local root/status/config sweep returned clean `200`
  responses (`0.248 s`, `0.021 s`, `0.075 s`) with local
  `/api/status.uptime_seconds=760`, and an immediate 5-cycle local loop
  then stayed fully clean while local `/api/status` climbed from `826`
  to `833`.
- Cycle 5 slowed the local root page to `1.112 s`, but there was no
  fresh timeout, reset, or truncated-body failure. Keep BUG-054 open
  historically because of the earlier masked-reboot samples, but count
  this pass only as another recovery window.

- **BUG-055 latest recheck:** around `2026-05-14T15:00:54Z` to
  `15:02:15Z`, `.207` moved back into the masked-reboot bucket. The hub
  devices page, hub detail page, `/api/v1/admin/devices`, and
  `/api/v1/admin/devices/<id>` still showed it `online` on
  `0.1.16-dev-central` with fresh heartbeats through
  `2026-05-14T15:01:59Z`, `health: healthy`, and
  `uptime_s` / `uptime_seconds=126`.
- But the prior `2026-05-14T14:51:03Z` to `14:53Z` recheck had already
  reached local `/api/status.uptime_seconds=1732`, while this pass's
  direct local root/status/config sweep returned clean `200` responses
  (`0.307 s`, `0.024 s`, `0.076 s`) at only `uptime_seconds=142`. The
  immediate 5-cycle local follow-up loop then stayed responsive while
  local `/api/status.uptime_seconds` climbed only from `162` to `169`.
  Treat that as another fresh reboot/recovery event behind a
  healthy-looking hub row.

- **BUG-054 latest recheck:** around `2026-05-14T15:19:59Z` to about
  `15:21Z`, the renamed soak target on `192.168.1.48` moved back into
  the masked-reboot bucket after the prior longer clean window. The hub
  row/detail and local `/api/status` + `/api/config` still converged on
  `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, the
  hub detail API showed heartbeat `2026-05-14T15:20:05Z`,
  `health: healthy`, and `uptime_s=128`, and the direct local
  root/status/config sweep returned clean `200` responses
  (`0.441 s`, `0.061 s`, `0.092 s`).
- But the prior `2026-05-14T15:09:56Z` to `15:12:59Z` recheck had
  already reached local `/api/status.uptime_seconds=1429`, while this
  pass's first local `/api/status` sample returned only
  `uptime_seconds=179`. Treat that as another fresh reboot/recovery
  event behind an `online` hub row even though the detail surface had
  already reconverged to `healthy`.

- **BUG-056 latest recheck:** around `2026-05-14T15:19:59Z` to about
  `15:21Z`, `.30` strengthened from a plain masked reboot into a split
  hub-row vs hub-detail shape. The rendered hub devices row and
  `/api/v1/admin/devices` still showed `192.168.1.30`
  (`Erica''s Subwoofer`) `online` on `0.1.17-dev-central` with
  heartbeat `2026-05-14T15:20:02Z`, while the local root/status/config
  sweep still returned clean `200` responses (`0.184 s`, `0.042 s`,
  `0.085 s`) and local `/api/status` reported only `uptime_seconds=58`.
- At the same time, `/api/v1/admin/devices/<id>` had already dropped to
  `latest_heartbeat.health_state="unknown"` with
  `latest_heartbeat.uptime_seconds=0`. Relative to the prior
  `2026-05-14T15:09:56Z` to `15:12:59Z` recheck where local
  `/api/status.uptime_seconds` had already climbed to `1903`, this is
  another fresh reboot/recovery sample behind a still-healthy list row.

- **BUG-055 latest recheck:** around `2026-05-14T15:19:59Z` to about
  `15:21Z`, `.207` stayed in a clean recovery window rather than
  reproducing again. The hub devices page, hub detail API,
  `/api/v1/admin/devices`, and local `/`, `/api/status`, and
  `/api/config` all remained responsive, with the hub detail API at
  heartbeat `2026-05-14T15:19:59Z`, `health: healthy`, and
  `uptime_s=1206`, while local `/api/status` returned `200` in
  `0.022 s` with `uptime_seconds=1265`.
- This pass added no fresh reboot, root-page stall, connection-reset,
  or truncated-body evidence; keep BUG-055 open historically, but count
  this window as improved.

- **BUG-057 latest recheck:** around `2026-05-14T15:19:59Z` to about
  `15:21Z`, the hub devices page and `/api/v1/admin/devices` still
  showed `192.168.1.69` (`Erica''s R.L. Speaker`) as `offline` with
  stale `last_heartbeat_at="2026-05-13T22:06:13Z"`, while local `/`,
  `/api/status`, and `/api/config` still timed out after about `15 s`.
- The hub detail API still exposed `health: healthy` and `uptime_s=69`
  from that stale heartbeat sample. The offline-vs-detail optimism
  remains concrete and unchanged.

- **BUG-054 latest recheck:** around `2026-05-14T15:30:42Z` to
  `15:31:40Z`, the renamed soak target on `192.168.1.48` moved back
  out of the fresh-repro bucket and into a clean short recovery window.
  The rendered hub Devices page still matched
  `/api/v1/admin/devices`, with the list API showing
  `Rebooter - renamed test`, `heartbeat_state="online"`,
  `online=true`, firmware `0.1.17-dev-central`, and heartbeat
  `2026-05-14T15:31:11Z`; the hub detail API still showed
  `latest_heartbeat.health_state="healthy"` with
  `latest_heartbeat.uptime_seconds=484`.
- The direct local root/status/config sweep also returned clean `200`
  responses (`0.20 s`, `0.022 s`, `0.071 s`) with local
  `/api/status` reporting `device_name="Rebooter - renamed test"`,
  `firmware_version="0.1.17-dev-central"`,
  `health_state="healthy"`, and `uptime_seconds=458`. The immediate
  5-cycle local continuity loop then stayed fully responsive at the API
  layer while local `/api/status.uptime_seconds` climbed from `510` to
  `517`, and local `/api/config` kept matching the renamed identity on
  every cycle.
- Keep BUG-054 open because cycle 5 of the same loop still stretched
  the local root page to `2.946 s`, but this pass added no fresh
  reboot, timeout, connection-reset, or truncated-body evidence. Count
  it as another improved recovery sample rather than a new repro.

- **BUG-054 latest recheck:** around `2026-05-14T15:42:27Z` to
  `15:42:35Z`, the renamed soak target on `192.168.1.48` stayed in an
  improved continuity window rather than slipping back into the
  masked-reboot bucket. The rendered hub Devices page still matched
  `/api/v1/admin/devices`, both still showed `Rebooter - renamed test`
  `online` on `0.1.17-dev-central` with heartbeat
  `2026-05-14T15:42:11Z`, and the hub detail API still showed
  `latest_heartbeat.health_state="healthy"` with
  `latest_heartbeat.uptime_seconds=1144`.
- The direct local root/status/config sweep also returned clean `200`
  responses (`0.203 s`, `0.034 s`, `0.057 s`) with local
  `/api/status` still reporting `device_name="Rebooter - renamed test"`
  / `firmware_version="0.1.17-dev-central"` / `health_state="healthy"`
  at `uptime_seconds=1162`. The immediate 5-cycle local continuity loop
  then stayed clean while local `/api/status.uptime_seconds` climbed
  from `1163` to `1170`, and local `/api/config` kept matching the
  renamed identity on every cycle.
- This pass added no fresh reboot, timeout, connection-reset, or
  truncated-body evidence. Keep BUG-054 open only because cycle 4
  stretched local `/api/config` to `1.173 s`; count this pass as a
  concrete improvement window rather than a fresh repro.

- **BUG-054 latest recheck:** around `2026-05-14T15:51:47Z` to
  `15:51:56Z`, the renamed soak target on `192.168.1.48` stayed in the
  improved continuity bucket again rather than falling back into the
  masked-reboot shape. The rendered hub Devices page still matched
  `/api/v1/admin/devices`, the hub detail page/API stayed converged as
  well, and all hub surfaces still showed `Rebooter - renamed test`
  `online` on `0.1.17-dev-central` with the list heartbeat at
  `2026-05-14T15:50:11Z` and detail
  `latest_heartbeat.health_state="healthy"` at
  `latest_heartbeat.uptime_seconds=1624`.
- The direct local root/status/config sweep also returned clean `200`
  responses (`0.432 s`, `0.022 s`, `0.069 s`) with local
  `/api/status` reporting `device_name="Rebooter - renamed test"`,
  `firmware_version="0.1.17-dev-central"`,
  `health_state="healthy"`, and `uptime_seconds=1723`. The immediate
  5-cycle local continuity loop then stayed clean while local
  `/api/status.uptime_seconds` climbed from `1723` to `1729`, and local
  `/api/config` kept matching the renamed identity on every cycle.
- This pass added no fresh reboot, timeout, connection-reset, or
  truncated-body evidence. Keep BUG-054 open only because cycle 5
  stretched local `/api/status` to `1.038 s` while the device still
  returned `200` with `health_state="healthy"` and increasing uptime;
  count this as another improved recovery sample rather than a fresh
  repro.

- **BUG-054 latest recheck:** around `2026-05-14T16:01:20Z` to
  `16:03:02Z`, the renamed soak target on `192.168.1.48` fell back out
  of the clean bucket again after the prior `15:51:47Z` to `15:51:56Z`
  improved window. The rendered hub Devices page still matched
  `/api/v1/admin/devices`, and the rendered list/detail pages plus the
  list API all kept showing `Rebooter - renamed test` `online` on
  `0.1.17-dev-central` at `192.168.1.48`, so there was still no fresh
  hub list/UI drift.
- The fresh local root/status/config sweep still returned clean `200`
  responses (`0.369 s`, `0.021 s`, `0.072 s`), but local
  `/api/status` had reset to `uptime_seconds=63` even though the prior
  clean loop had already reached `uptime_seconds=1729`. That is another
  concrete reboot/recovery event on `.48`.
- The hub detail API briefly reflected the restart in a narrower form:
  at the first `16:01:20Z` capture it showed
  `latest_heartbeat.received_at="2026-05-14T16:00:24Z"` with
  `health_state="unknown"`, `uptime_seconds=0`, and
  `wifi_connected=false`, while the local device API was already back
  to `health_state="healthy"` and `uptime_seconds=63`. Follow-up
  rereads at `16:01:50Z` and `16:03:02Z` reconverged the detail API to
  `health_state="healthy"` with `uptime_seconds=64` and then `124`
  while the hub list row stayed `online` throughout.
- The immediate 5-cycle local continuity loop stayed responsive while
  local `/api/status.uptime_seconds` climbed from `63` to `73`, and
  `/api/config` kept matching the renamed identity on every cycle.
  Keep BUG-054 open as another masked-reboot/recovery sample plus a
  renewed latency signal, because cycle 2 stretched local
  `/api/status` to `4.029 s` and cycle 4 still took `1.036 s`, even
  though there was no fresh timeout, reset, or truncated-body failure.

- **BUG-054 latest recheck:** around `2026-05-14T16:11:46Z` to
  `16:12:45Z`, the renamed soak target on `192.168.1.48` moved back
  out of the masked-reboot bucket and into another clean recovery
  window. The rendered hub Devices page still matched
  `/api/v1/admin/devices`, the rendered detail page still matched
  `/api/v1/admin/devices/<id>`, and all hub surfaces again showed
  `Rebooter - renamed test` `online` on `0.1.17-dev-central`, with the
  list heartbeat at `2026-05-14T16:12:24Z` and the detail API already
  reconverged to `latest_heartbeat.health_state="healthy"` with
  `latest_heartbeat.uptime_seconds=724`.
- The immediate local 5-cycle root/status/config loop also returned
  clean `200` responses on every cycle, with local `/api/status`
  reporting `device_name="Rebooter - renamed test"`,
  `firmware_version="0.1.17-dev-central"`,
  `health_state="healthy"`, and steadily increasing
  `uptime_seconds=688` through `697`, while local `/api/config` kept
  matching the renamed identity throughout. This pass added no fresh
  reboot, timeout, connection-reset, or truncated-body evidence.
- Keep BUG-054 open only in the narrower local-root latency form,
  because cycle 4 stretched the root page to `1.128 s` and cycle 5
  stretched it to `3.547 s` while local `/api/status` and
  `/api/config` both stayed fast and healthy. Count this as a concrete
  improvement window rather than a fresh repro.

- **BUG-054 latest recheck:** around `2026-05-14T16:20:54Z` to
  `16:21:08Z`, the renamed soak target on `192.168.1.48` kept the hub
  side converged while the local root UI slipped back into the stronger
  truncated-body shape. The rendered hub Devices page still matched
  `/api/v1/admin/devices`, the rendered detail page still matched
  `/api/v1/admin/devices/<id>`, and all hub surfaces kept showing
  `Rebooter - renamed test` `online` on `0.1.17-dev-central`, with the
  list heartbeat at `2026-05-14T16:20:24Z` and the detail page/API
  still showing `health: healthy` / `uptime_s=1204`.
- The initial local root/status/config sweep also still looked
  converged, with clean `200` responses (`0.760 s`, `0.023 s`,
  `0.069 s`) and local `/api/status` reporting
  `device_name="Rebooter - renamed test"`,
  `firmware_version="0.1.17-dev-central"`,
  `health_state="healthy"`, `wifi_connected=true`, and
  `uptime_seconds=1237`.
- But cycle 3 of the immediate 5-cycle local continuity loop then
  failed on local `/` after `4.186 s` with a truncated-body
  `ChunkedEncodingError`
  (`IncompleteRead(12211 bytes read, 3812 more expected)`), while
  local `/api/status` and `/api/config` still returned clean, fast
  `200` responses and local `/api/status.uptime_seconds` continued
  climbing from `1237` to `1248`. Treat this as renewed concrete proof
  that `.48` still intermittently corrupts the local root UI even when
  the hub list/detail pages and the local APIs all look healthy, not as
  another masked reboot. Keep BUG-054 open in the stronger local-root
  integrity bucket, with a smaller residual latency watch because cycle
  5 still stretched local `/` to `1.504 s`.

- **BUG-054 latest recheck:** around `2026-05-14T16:31:50Z` to about
  `16:32:20Z`, the renamed soak target on `192.168.1.48` shifted back
  out of the truncated-body bucket and into another reboot/recovery
  sample. The rendered hub Devices page still matched
  `/api/v1/admin/devices`, and the rendered detail page still matched
  `/api/v1/admin/devices/<id>` on the device identity: all four hub
  surfaces still showed `Rebooter - renamed test` at `192.168.1.48` on
  `0.1.17-dev-central`, with the list row `online` and list heartbeat
  `2026-05-14T16:31:29Z`.
- Local status showed this was still a fresh restart relative to the
  prior `16:20:54Z` to `16:21:08Z` pass, which had already reached
  `uptime_seconds=1248`: the first local `/api/status` read in this
  pass had already reset to `uptime_seconds=207`, while the hub detail
  API's `latest_heartbeat` showed `received_at="2026-05-14T16:31:29Z"`,
  `health_state="healthy"`, `uptime_seconds=185`,
  `wifi_connected=true`, and `last_event_type="boot"`. That means the
  central detail surfaces did ingest the reboot heartbeat, but the hub
  list row still presented the device simply as `online`, so the reboot
  remains easy to miss from the main fleet view.
- The immediate 5-cycle local continuity loop then returned clean `200`
  responses on `/`, `/api/status`, and `/api/config` while local
  `/api/status.uptime_seconds` climbed from `232` to `238` and
  `/api/config` kept matching the renamed identity. Keep BUG-054 open
  as another concrete reboot/recovery sample plus a narrower residual
  local-root latency watch, because cycle 5 still stretched local `/`
  to `1.205 s` even though there was no fresh timeout, reset, or
  truncated-body failure.

- **BUG-054 latest recheck:** around `2026-05-14T16:40:42Z` to
  `16:42:45Z`, the renamed soak target on `192.168.1.48` stayed
  identity-converged on the hub side but reproduced both a fresh reboot
  and a new local `/api/config` integrity failure. The rendered hub
  Devices page still matched `/api/v1/admin/devices`, the rendered
  detail page still matched `/api/v1/admin/devices/<id>`, and all four
  hub surfaces still showed `Rebooter - renamed test` at
  `192.168.1.48`, `online` / `heartbeat_state="online"` on
  `0.1.17-dev-central`.
- Relative to the prior `16:31:50Z` to `16:32:20Z` pass, which had
  already reached local `uptime_seconds=238`, the first fresh local
  `/api/status` read had already reset to `uptime_seconds=244`. The hub
  detail API agreed that a reboot had just happened: its
  `latest_heartbeat` showed `received_at="2026-05-14T16:40:44Z"`,
  `last_event_type="boot"`, `health_state="healthy"`,
  `uptime_seconds=244`, and `wifi_connected=true`. That means the hub
  detail surfaces again recorded the reboot, but the main Devices row
  still reduced it to a normal-looking `online` entry.
- The immediate 5-cycle local continuity loop then kept local `/` and
  `/api/status` healthy while local `/api/status.uptime_seconds` climbed
  from `326` to `331`, but cycle 5 failed on local `/api/config` after
  `5.808 s` with a truncated-body `ChunkedEncodingError`
  (`IncompleteRead(949 bytes read, 119 more expected)`). A confirming
  later reread still showed local `/api/status` healthy at
  `uptime_seconds=384`. Keep BUG-054 open as another concrete masked
  reboot/recovery sample plus a stronger local response-integrity issue,
  now reproduced on `/api/config` instead of only the root page.
