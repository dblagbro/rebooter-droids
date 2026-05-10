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
