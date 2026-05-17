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
- **Status:** **open - observed live 2026-05-14**
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
- **Fix direction:** split the visible state model into at least:
  `online`, `central stale/transport failed`, and `offline`.

### BUG-053 - Desired-name drift persists on ordinary fleet devices outside restore-after-reflash

- **Severity:** medium (source-of-truth drift)
- **Area:** hub desired-config / rename propagation
- **Status:** **open - observed live 2026-05-14**
- **Detail:** Live comparison between `/api/v1/admin/devices` and
  local device APIs shows `.48` is correct after the manual
  `apply_config.device_name` push, but multiple other devices still
  diverge:
  - hub `Erica''s Subwoofer` vs device `Rebooter` on `192.168.1.30`
  - hub `Erica''s F.R Speaker` vs device `Rebooter` on
    `192.168.1.225`
  - hub `Erica''s R.R. Speaker` vs device `Erica''s ?.?. Speaker` on
    `192.168.1.207`
  This proves `v0.5.8` solved the restore-after-reflash name push,
  but not the broader "hub display_name is the desired device name"
  contract.
- **Fix direction:** desired-name propagation must run on ordinary
  rename / drift reconciliation, not only restore-after-reflash.
- **2026-05-15 update — fixed.** v0.5.12 (B24) shipped the rename
  push from both API and UI handlers (`devices_api.py:93` +
  `devices_ui.py:149`); v0.5.22 (B21) generalised this to a full
  `desired_config` blob with drift detection. Re-verified during
  the 2026-05-15 regression sweep (R7 — set desired_config on
  `.48`; drift_summary returned `state='drifted',
  mismatched=['device_name']` against live `last_reported_config`
  echoed from firmware `0.1.19-dev-central-safe`).

### BUG-052 — status update (2026-05-15)

- **Status:** **fixed in v0.5.12 (B23)** — `_derive_central_status`
  in `services/devices/_serialize.py` now returns one of
  `transport_stale` / `central_stale` / `upgrade_pending` /
  `attention` / `awaiting_first_heartbeat` / `central_ok` /
  `local_only` instead of collapsing the entire space into binary
  online/offline. Devices-list + Device-detail templates render
  distinct chips with hover tooltips carrying the reason. The
  ".225 looks like .69" scenario was the motivating case the fix
  closes. Re-verified during the 2026-05-15 regression sweep.
- **Doc-cleanup note:** The original entry above was still tagged
  "open" until this sweep, despite v0.5.12 having shipped the fix
  on 2026-05-14. Bug-log drift caught.

---

## BUG-054 — `custom` probe kind is canonical but has no runtime branch

- **Date:** 2026-05-15
- **Severity:** medium (latent operator footgun via JSON editor)
- **Area:** `app/services/watchdog_runtime/_probes.py::run_probe`
- **Status:** **fixed in v0.5.34** (verified 2026-05-17 regression sweep — see note at end of entry)
- **Environment:** live hub `0.5.33` against
  `https://www.voipguru.org/rebooter`.
- **Repro:**
  ```bash
  curl -X POST /api/v1/admin/rules -d '{
    "name":"qa-custom",
    "probe":{"kind":"custom","name":"qa-custom"},
    "target":{"kind":"tag","tag":"qa-noop"},
    "action":{"kind":"notify_only"},
    "failure_threshold":3,"recovery_threshold":2,
    "window_seconds":60,"cooldown_seconds":300}' → 201 Created

  curl -X POST /api/v1/admin/rules/<id>/probe-now
  → {"data":{"outcome":"failure",
             "details":{"reason":"unknown probe kind: custom"}}}
  ```
- **Expected:** Either a runtime handler exists for `custom` and
  it executes, OR `KNOWN_PROBE_KINDS` rejects the kind at create
  time. Anything else is a contract gap.
- **Actual:** Validation accepts the kind (it's in
  `KNOWN_PROBE_KINDS`). Runtime fails the probe with the
  misleading reason string "unknown probe kind: custom". The rule
  silently builds toward firing every probe interval.
- **Evidence:** R3b output in the 2026-05-15 sweep log:
  `custom: outcome=failure  reason=unknown probe kind: custom, via=probe_now`.
- **Cause:** `PROBE_KIND_CUSTOM = "custom"` has been in
  `KNOWN_PROBE_KINDS` since v0.4.0. `_run_probe` never had a
  branch for it. `_probe_to_phrase` *does* handle it
  (`f"custom probe \`{p.get('name','?')}\`"`) which makes the gap
  even more confusing — model + service-phrase render agree the
  kind is real; only the runtime says no.
- **Recommended fix:** **Option A (recommended)** — drop
  `PROBE_KIND_CUSTOM` from `KNOWN_PROBE_KINDS` + the
  `_probe_to_phrase` branch. The integration probes
  (roku/ha/weather/ical) and power probes
  (power_above/_below/_zero_while_on) are the actual extensibility
  surface today. **Option B** — implement `_probe_custom` for e.g.
  user-supplied lambda or shell-exec; out of scope for any
  immediate ship, and a security review surface.

---

## BUG-055 — `create_rule()` skips per-kind probe-field validation for non-internet kinds

- **Date:** 2026-05-15
- **Severity:** medium (operator UX footgun; rules silently never
  fire OR fire wrong-target if action is `cycle`/`hold_off`)
- **Area:** `app/services/watchdog.py::create_rule` + `update_rule`
- **Status:** **fixed** (verified 2026-05-17 regression sweep — see note at end of entry)
- **Environment:** live hub `0.5.33`.
- **Repro:**
  ```bash
  curl -X POST /api/v1/admin/rules -d '{
    "name":"x",
    "probe":{"kind":"power_above","device_id":"dev_x",
             "threshold_w":"oops","window_seconds":300},
    "target":{"kind":"tag","tag":"qa"},
    "action":{"kind":"notify_only"},
    "failure_threshold":3,"recovery_threshold":2,
    "window_seconds":60,"cooldown_seconds":300}' → 201 Created
  ```
- **Expected:** 400 `validation_failed` with a per-kind message
  (`threshold_w must be numeric`, `source_id is required for
  kind=roku_app_active`, etc.).
- **Actual:** 12 of 15 deliberately-broken probe configurations
  returned 201 Created. Rule lands in DB. Runtime later returns
  `failure: reason="missing threshold_w"` every probe interval
  (the message is itself misleading — the actual cause is
  "threshold_w is a string, not a number"). For `notify_only`
  actions this is invisible. **For `cycle`/`hold_off` actions
  targeting a real device**, the malformed rule eventually fires
  after `failure_threshold` ticks because the streak gate doesn't
  know it's stuck — **operator could accidentally power-cycle a
  device by mistyping a threshold**.
- **Evidence:** R9 sweep output:
  ```
    roku-missing-source_id: 201
    roku-empty-app_name: 201
    ha-missing-entity_id: 201
    weather-missing-source_id: 201
    weather-bogus-severity: 201
    ical-bad-url-not-validated-at-rule: 201
    power_above-missing-threshold: 201
    power_above-negative-threshold: 201
    power_above-string-threshold: 201
    power_above-tiny-window: 201
    power_above-huge-window: 201
    power-missing-device_id: 201
    unknown-kind: 400 ✓ (correctly rejected)
    malformed-kind-int: 400 ✓ (correctly rejected)
    missing-kind: 400 ✓ (correctly rejected)
  ```
- **Cause:** `create_rule` validates only:
  - `probe.kind in KNOWN_PROBE_KINDS`
  - `internet.targets[*]` shape (added v0.5.9 — the only per-kind
    pre-existing validator)
  It does NOT validate per-kind required-field presence, type, or
  range for any other kind. The integration probes (v0.5.17/.23)
  and power probes (v0.5.32) inherited this gap.
- **Recommended fix:** Per-kind validator dispatch helper in
  `services/watchdog.py`. Pattern is already proven by
  `services.external_sensors._validate_kind_config()` which does
  exactly this for source-kind extras. Fix scope (~1-2 h):
  - `roku_app_active`: `source_id` + `app_name` non-empty
  - `ha_state_is`: `source_id` + `entity_id` + `expected_state` non-empty
  - `weather_alert_active`: `source_id` non-empty; `min_severity`
    ∈ {Minor, Moderate, Severe, Extreme} when present
  - `ical_event_active`: `source_id` non-empty
  - `power_above`/`power_below`: `device_id` non-empty;
    `threshold_w` numeric ∈ [0, 10000]; `window_seconds` ∈
    [30, 86400]
  - `power_zero_while_on`: `device_id` non-empty;
    `near_zero_threshold_w` numeric ∈ [0, 100]
- **Workaround until fix lands:** The rules-create form fields
  (v0.5.28 / v0.5.32) have the right HTML5 `required` + `min` +
  `max` constraints. Only the JSON-editor and API paths bypass
  them. The runtime stale-sample + missing-field gates *do*
  prevent power_above/_below with bad input from acting on the
  wrong target — they just fail-loud at probe-time rather than
  fail-loud at save-time.

---

## 2026-05-15 regression sweep — coverage map

| Round | Surface | Verdict |
|---|---|---|
| R1 | Smoke — `/api/v1/version` → 0.5.33 | ✓ |
| R2 | Subpackage import contract (v0.5.15 + v0.5.18/.21) — 15 modules + 30 public symbols re-exported cleanly | ✓ |
| R3 | Rule creation across all 14 probe kinds via API | ✓ all 201 |
| R3b | `probe-now` dispatch across all 14 kinds | ✓ 13/14 dispatched correctly; **BUG-054** surfaced on `custom` |
| R4 | Power telemetry E2E: ingest → query → manual rollup → idempotent re-run → chart data; math verified (`(144 + 145.2 + 146.4 + 147.6 + 148.8) / 5 = 146.4`) | ✓ |
| R5 | Cost calc — set/clear rate, bad input (non-numeric / 999 / -1) all return 302 with flash; page renders persisted value | ✓ |
| R6 | CSV export — `text/csv; charset=utf-8`, `attachment; filename="rebooter-power-24h.csv"`, header row matches; unknown window falls back to 24h cleanly | ✓ |
| R7 | Drift detection E2E against live `last_reported_config` from firmware `0.1.19-dev-central-safe`; bad-JSON + unknown-key both return 302 with flash; clear flow works | ✓ |
| R9 | Negative validation — 15 deliberately-broken probe configs | ✗ **BUG-055** — 12/15 false-201 |
| R10 | Long-poll concurrency — 4 parallel `Prefer: wait=5` polls all returned in ~5.16 s; `Preference-Applied: wait=5` echoed; no serialisation | ✓ |
| R11 | Log review — zero errors / exceptions / 5xx / DB connection issues; APScheduler ticking on all 5 jobs | ✓ |
| R12 | Power probe stale-sample gate — 12-min-old sample with 600 s max-age correctly returns `failure: reason='stale_sample'`; stricter 60 s also fails | ✓ |
| R13 | Power probe success-vs-failure semantics — 4 cases (`power_above` threshold above/below avg, `power_below` threshold above/below avg) all match expected outcomes | ✓ |
| R14 | Schema verification — `device_power_rollups` has the `uq_device_power_rollups_device_day` UNIQUE constraint (concern: module-level `UniqueConstraint()` declaration usually doesn't bind; verified that it does here) | ✓ |

### Surfaces NOT validated this sweep (gap inventory)

- Playwright UI flows on the new `/app/power`,
  `/app/settings/integrations`, and drift-chip surfaces. The sweep
  validated rendering via `grep -c` on the HTML body, not user
  interaction or console-error monitoring.
- Long-poll under saturation — only 4-wide tested; the worker
  thread pool is 8 (default `REBOOTER_GUNICORN_THREADS`); behaviour
  at 8+ concurrent long-polls not exercised.
- Power rollup cron tick at 02:00 UTC — manually invoked, but the
  actual scheduled fire not yet observed in a log.
- SQLite test path for the v0.5.22 `desired_config` columns — the
  `test_v0514_*.py` BigInteger autoincrement quirk noted at ship
  time still blocks the SQLite path; production Postgres works.
- nginx layer — route + redirect + alias behaviour against the new
  `/app/power` + `/app/power/rate` + `/app/power/export.csv` paths.
- The three firmware-coord-gated phases (Phase 3 hub absorption of
  expanded heartbeat fields, Phase 4B recovery-aware drift
  actions, Phase 4C schema alignment) — code not yet written.
- SSH to `tmrwww02` — unblocked but never exercised this session.
- Auto-rebind path — covered by `test_v0420_*` but not re-run live
  this sweep (would require minting a real device + simulating
  token loss; covered in the v0.5.24 merge ship's verification).

---

# 2026-05-17 — Post-refactor regression validation sweep (v0.5.86)

Deep regression / release-hardening pass after the v0.5.67–v0.5.86
refactor + QA arc (rule-form extraction, external_sensors split,
watchdog_runtime split, admin blueprint split, services subpackages,
`tests/unit/` tree). Method: full `tests/` suite (598 tests) against a
fresh nginx-fronted Postgres replica; live API negative testing;
static bug-class audit; doc cross-check.

**Headline:** no product regression from the refactors. Full suite
593/598 pass — all 5 failures are test-quality/environmental, not
product defects (see test-plan.md "Known non-gated failures"). API
negative paths are uniformly clean (consistent envelope, correct
4xx, zero 500s). Six new issues recorded below; none are release
blockers.

## BUG-054 / BUG-055 — status correction (2026-05-17)

Both were still tagged `open` in this log but are **fixed in the
shipped code** — verified live this sweep:

- **BUG-054** — `POST /api/v1/admin/rules` with `probe.kind="custom"`
  now returns `400 validation_failed` ("probe.kind must be one of
  …"). Option A was taken: `custom` was dropped from
  `KNOWN_PROBE_KINDS` in **v0.5.34** (`watchdog.py:608` comment
  confirms). Status line corrected above.
- **BUG-055** — per-kind probe-field validation is now present:
  `power_above` with `threshold_w:"oops"` → `400` ("probe.threshold_w
  must be numeric (got str)"); `ping` with no `host` → `400`. Status
  line corrected above. Exact fix version not recorded in the log —
  a follow-up to backfill.

This is itself a process defect — see **BUG-061**.

## BUG-056 — `schedule_runtime._fire_power_cycle` swallows per-device enqueue failures silently

- **Date:** 2026-05-17
- **Severity:** medium (reliability + observability of the core
  scheduled-reboot feature)
- **Area:** `app/services/schedule_runtime.py:117-119`
- **Status:** **fixed in v0.5.87** — the per-device except now
  `log.exception`s and counts failures; `last_outcome` reports
  `enqueued:N failed:M`.
- **Detail:** `_fire_power_cycle` fans a scheduled `relay_cycle` out
  across the resolved target devices in a per-device loop. Each
  `enqueue_for_device(...)` call is wrapped in `except Exception:
  pass`. If enqueue raises for a device — `DeviceLockedError`
  (device `is_protected`), `LookupError` (device id no longer
  exists), or any DB error — that device is silently skipped: no
  log line, no event row, no error counter. The function returns
  `enqueued:N`; when `N < len(device_ids)` the shortfall is
  invisible to the operator.
- **Expected:** a scheduled reboot that fails to enqueue for a
  device should be logged (and ideally surfaced — the watchdog
  equivalent `watchdog_runtime/_actions.py:42-43` does
  `log.exception(...)`).
- **Actual:** silent. A schedule the operator believes is arming a
  protected device simply never does, with nothing in the logs.
- **Cause:** asymmetry — the watchdog action path logs its
  exceptions; the schedule fan-out path swallows them.
- **Recommended fix:** `log.exception("scheduled relay_cycle enqueue
  failed for device %s", did)` inside the except, and fold a failure
  count into the return string (`enqueued:N skipped:M`).

## BUG-057 — error.html navigation links escape the app under the `/rebooter` prefix

- **Date:** 2026-05-17
- **Severity:** low-medium (error page is a dead-end that ejects the
  user from the app)
- **Area:** `templates/error.html:12` and `:18`
- **Status:** **fixed in v0.5.87** — both links now use
  `url_for('admin_ui.index')`; verified resolving to `/rebooter/app/`
  on a fresh prefixed replica.
- **Detail:** The error page (rendered for 404/403/500) has a brand
  link `<a class="brand" href="/">` and a `<a href="/">Back to
  dashboard</a>`. The app is deployed under the `/rebooter` URL
  prefix; `/` is the **voipguru.org host root**, a different site.
  Clicking either link from a rebooter error page leaves the app
  entirely instead of returning to `/rebooter/app/`.
- **Expected:** links resolve within the app — `{{ request.script_root }}/app/`
  or a `url_for('admin_ui.index')`.
- **Evidence:** every other template + every Python `redirect()`
  uses `url_for` (prefix-safe); `error.html` is the lone exception.
  The app's own `root_redirect` at `/` *does* honour the prefix —
  but `error.html` hardcodes `/` so it never reaches that route.
- **Recommended fix:** swap both hrefs to `url_for('admin_ui.index')`
  (the error template renders inside an app request context, so
  `url_for` is available).

## BUG-058 — `KNOWN_PROBE_KINDS` (create-rule validation) diverges from the `run_probe` dispatch table

- **Date:** 2026-05-17
- **Severity:** medium (probe-kind surface inconsistency; runtime
  branches unreachable via the validated create path)
- **Area:** `app/services/watchdog.py::KNOWN_PROBE_KINDS` vs
  `app/services/watchdog_runtime/_probes.py::run_probe`
- **Status:** **open**
- **Detail:** `create_rule` validates `probe.kind` against
  `KNOWN_PROBE_KINDS` — **13 kinds** (`internet, ping, tcp, http,
  dns, gateway, roku_app_active, ha_state_is, weather_alert_active,
  ical_event_active, power_above, power_below, power_zero_while_on`).
  But `run_probe` dispatches **~25 kinds** — it additionally handles
  `ha_numeric_above`, `ha_numeric_below`, `solar_production_above`,
  `solar_production_below`, `snmp_interface_down`,
  `snmp_throughput_above`, `snmp_throughput_below`,
  `snmp_error_rate_above`, `media_session_active`,
  `webhook_field_equals`, `mqtt_topic_equals`, `epg_show_airing`,
  and `host_awake`. Those ~12 kinds have full runtime handlers in
  `_probes_integrations.py` / `_probes.py` but **cannot be created
  through the validated API path** — `POST /api/v1/admin/rules`
  rejects them with `400`.
- **Impact:** either ~12 runtime dispatch branches are dead code, or
  there is a missing create surface for kinds the runtime fully
  supports. (BUG-054 was the inverse — a kind accepted at create
  with no runtime branch — fixed by *removing* the kind; this is the
  same divergence in the other direction.) Compounding it, the
  `templates/rules/edit.html` probe-shape reference card documents
  only ~7 kinds (noted stale in `refactor-log.md`). Three sources of
  truth, all different.
- **Recommended fix:** establish one canonical probe-kind registry
  (kind → {validator, runtime dispatcher, form builder, doc blurb})
  and derive `KNOWN_PROBE_KINDS`, `run_probe`'s dispatch, and the
  reference card from it. Short-term: decide per-kind whether each of
  the 12 should be createable; add to `KNOWN_PROBE_KINDS` (with
  per-kind field validation) or remove the runtime branch.

## BUG-059 — Latent SQLite-incompatible code blocks `tests/unit/` coverage expansion

- **Date:** 2026-05-17
- **Severity:** medium (test-blocking; prod-safe on Postgres)
- **Area:** multiple — see list
- **Status:** **open**
- **Detail:** Production runs exclusively on Postgres, where all of
  the following are correct. But the active QA initiative builds
  `tests/unit/` against an **isolated-SQLite** fixture (`hub_db`),
  and each of these crashes on SQLite — so the listed services
  cannot get in-process unit coverage until they are fixed. This is
  the same defect class as the already-fixed enrollment
  (`as_aware`, v0.5.85) and `DevicePowerSample.id` (`with_variant`,
  v0.5.86) issues; the established fix patterns already exist in the
  codebase.
  - **(A) Naive vs tz-aware datetime** — DB-read datetimes compared
    against an aware `now` without `app.models._helpers.as_aware()`
    (Postgres `TIMESTAMPTZ` reads aware, SQLite reads naive →
    `TypeError`): `invitations.py:158`, `invitations.py:184`,
    `password_resets.py:102`, `inbox.py:154`, `inbox.py:168`,
    `inbox.py:187`, `inbox.py:201`, `external_sensors/_query.py:41`,
    `external_sensors/_pollers.py:122`.
  - **(B) `BigInteger` PK without `.with_variant(Integer,"sqlite")`**
    (does not autoincrement on SQLite): `events.py:15` (`DeviceEvent`),
    `unregistered.py:27` (`UnregisteredAuthAttempt`), `audit.py:56`
    (`AuditEventArchive`).
  - **(C) Unconditional Postgres `ON CONFLICT`** —
    `unregistered.py:54` uses `dialects.postgresql.insert(...)
    .on_conflict_do_update(...)` with no dialect branch (contrast
    `device_power.py:689-714`, which branches sqlite/postgres for the
    same upsert).
- **Expected:** services use `as_aware()`, the `with_variant` PK
  pattern, and dialect-branched upserts consistently, so the
  in-process test fixture can exercise them.
- **Recommended fix:** apply the three established patterns at the
  listed sites; then add `tests/unit/` coverage for the
  invitations, password-resets, inbox, external-sensors, events and
  unregistered services (currently HTTP-only).
- **Note:** `bootstrap.py` (`pg_advisory_lock`, `ADD COLUMN IF NOT
  EXISTS`, `information_schema`, `DELETE … USING`) is Postgres-only
  *by design* — SQLite is not a supported app runtime, only a test
  models backend. Not a bug; documented in qa-notes.md.

## BUG-060 — Logout token/session revocation failures are swallowed silently

- **Date:** 2026-05-17
- **Severity:** medium (security observability)
- **Area:** `app/blueprints/auth.py:79-80` and `:86-87`;
  `app/blueprints/admin/auth_ui.py:215-216`
- **Status:** **fixed in v0.5.87** — all three sites now
  `log.exception` the revocation failure (logout still succeeds for
  the user).
- **Detail:** The logout handlers wrap `revoke_all_tokens(user_id)`
  and `sessions_service.revoke_one(sid)` in `except Exception:
  pass`. If server-side revocation fails, the user's JWT / session
  remains valid, the user is told they are logged out, and nothing
  is logged — the operator has no signal that a logout did not fully
  take effect.
- **Expected:** a failed revocation is logged at error level (logout
  should still return success to the user, but the failure must be
  observable).
- **Recommended fix:** replace `pass` with `log.exception("token/
  session revocation failed during logout for user %s", user_id)`.

## BUG-061 — bug-log.md not maintained: fixed bugs left tagged `open`

- **Date:** 2026-05-17
- **Severity:** low (process / documentation integrity)
- **Area:** `docs/bug-log.md`
- **Status:** **fixed this sweep** (BUG-054, BUG-055 status lines
  corrected above)
- **Detail:** BUG-054 (fixed v0.5.34) and BUG-055 (fixed, version
  unrecorded) were both still tagged `open` in this log months
  after the code shipped the fix. BUG-052 had the same drift,
  caught and noted in its own "Doc-cleanup note" on 2026-05-15 — yet
  054/055 slipped through the same sweep. A bug log that lies about
  status is worse than no log: it sends the next engineer to
  re-investigate closed issues and undermines trust in the `open`
  entries that *are* real.
- **Recommended fix:** the release-cut process (`tools/cut-rebooter-
  release.sh`) should require, or at least prompt for, a bug-log
  status update when a commit message references a `BUG-NNN`. Also:
  bug numbers 039, 047, 049 are unused (gaps) — harmless but worth a
  one-line "reserved/skipped" note to stop future readers hunting
  for them.
