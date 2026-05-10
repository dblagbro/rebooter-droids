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
