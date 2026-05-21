# Hub Tier-2 feature set — design & implementation plan

> **Status:** DESIGN PASS — 2026-05-20. Author: D. Blagbrough.
> Scope: six Tier-2 features for the Rebooter Hub (Python/Flask/Postgres/
> SQLAlchemy, ~v0.5.102). READ-ONLY design pass — no source modified.
> This document proposes; it does not commit anyone to a release number.

## Context the design is grounded in

The hub is a **public, internet-facing multi-tenant SaaS** (paid service;
also self-hostable open source; K8s-scalable goal). It already ships:

- RBAC (`role_bindings`, `rbac.enforce_mode` shadow/enforce), OAuth
  third-party login (`google_oauth.py`), SMTP invites, request-login
  (`signup_requests`), audit logging on every mutation.
- A watchdog rule engine: `watchdog_rules` table (probe / target / action /
  escalation / maintenance-window JSON columns), a probe runtime under
  `watchdog_runtime/`, plain-English `render_rule_sentence()`, and a
  structured rule form plus a JSON-editor escape hatch (`rules.py`).
- Per-device `desired_config` (JSON) + `desired_mode`
  (`smart_plug` / `internet_watchdog` / `device_watchdog`) with drift
  detection (`device_config.py`), pushed as an `apply_config` command.
- DB-backed runtime settings with env-var fallback (`runtime_settings.py`,
  `SMTP_KEYS` / `NETWORK_KEYS` / `SYSTEM_KEYS` / `RBAC_KEYS`).
- A tabbed Settings area (`settings.py`, `_components/settings_tabs.html`).
- Inbound integration webhooks (`integrations_webhook.py`) — note these are
  *inbound* (external → hub); this Tier-2 set adds *outbound*.
- A tile/feed dashboard (`dashboard.html`), a 5+1 top-nav / bottom-tab
  layout (`layout.html`) already responsive at 768px.

### Key constraints discovered while reading the code

1. **`stub.html` is dead code.** No route renders it; the Settings tab
   strip no longer links Backup / API-tokens / Webhooks. Those three
   sub-pages do not exist as routes today — they must be built from
   scratch, not "un-stubbed".
2. **Multi-tenancy seam is `site_id`, not a true `tenant`/`org` table.**
   `Device.site_id`, `WatchdogRule.site_id`, `EnrollmentToken.site_id` all
   FK to `sites`. There is no per-tenant isolation column beyond site +
   RBAC `role_bindings`. New models below must carry `site_id` (nullable,
   FK `sites.id ON DELETE SET NULL`) for scope parity, and every list
   query must run through `rbac_filter.py` like existing ones. **Open
   question for the PO (Q1)** on whether a hard `tenant` boundary is
   imminent — if so, new tables should carry `tenant_id` from day one.
3. **`apply_config` schema is firmware-owned and only `device_name` is
   "validated end-to-end".** The 3-mode picker (feature 1) and the
   friendly device-config form (feature 2) must generate config that
   stays inside `ALLOWED_DESIRED_CONFIG_KEYS` and the per-key support
   tiers in `docs/firmware-apply-config-schema-v01.md`. Anything beyond
   `device_name` is "accepted" but not round-trip-verified — the UI must
   say so, and auto-push stays behind `desired_config.enabled`.
4. **No CSRF token framework** is visible; `/app/*` POSTs rely on session
   cookies + `SameSite=Lax`. New forms follow the same pattern; new
   *machine* endpoints (API tokens) must be exempt like `/api/v1/*`.
5. **Outbound HTTP already exists in two risky spots** — `settings.py`
   calls `requests.get("http://localhost:8090/...")` and the sync
   replicator calls peer hubs. Feature 6's SSRF guard must be a shared
   helper, and ideally those existing call sites move behind it too
   (out of scope to fix here, but noted).

---

## Feature 1 — First-run setup wizard + plain-language 3-mode picker

### Goal
A `/app/setup` flow that walks a non-technical user through the three
device operating modes in plain language and generates the correct
`desired_config` + watchdog rule(s) behind the scenes. No `/app/setup`
exists today.

### Two distinct things, deliberately bundled
- **First-run wizard** — shown once when the hub has no devices / no
  rules / portal not yet branded. Brand the portal, confirm SMTP, point
  the operator at enrollment.
- **3-mode picker** — a *reusable* per-device flow ("Set up this device")
  reachable from the device-detail page and from the wizard's last step.
  It is the more valuable, reusable half; build it first.

### The three modes in plain language
| Picker label | `desired_mode` | What it generates |
|---|---|---|
| "Just a smart switch" — turn power on/off, on a schedule, by hand | `smart_plug` | `relay_restore_behavior`, optional schedule. **No** watchdog rule. |
| "Keep my internet alive" — restart the modem/router when the internet drops | `internet_watchdog` | `desired_config.internet` block (targets, thresholds, cycle timing) **and** a hub-side `internet`/`ping` watchdog rule targeting the device with a `cycle` action. |
| "Restart one device when it locks up" — watch a single device and power-cycle it | `device_watchdog` | `desired_config.device` block **and** a hub-side `ping`/`tcp`/`http` watchdog rule with a `cycle` action. |

The picker collects, in plain language: a friendly device name; for
internet mode — "how do you want us to check?" (default = ping
`1.1.1.1` + `8.8.8.8`) and "how long offline before we restart?"
(maps to `failure_threshold_seconds` / `failure_threshold` × `window`);
for device mode — "what's the address of the thing to watch?" (host /
URL) and the same offline-tolerance question. Advanced timers
(`power_off_seconds`, `post_reboot_holdoff_seconds`, `cooldown_seconds`,
`max_cycles_per_hour`) get sensible defaults and live behind a
"Fine-tune" disclosure.

### Approach
- A small **wizard state machine** rendered server-side, one step per
  GET, POST advances. State carried in a signed session dict
  (`session["setup_wizard"]`) — no new table needed; the wizard is
  ephemeral and idempotent. The final step is the only one that writes.
- The mode picker reuses two services that already exist:
  `device_config.set_desired_config()` for the config blob and
  `watchdog.create_rule()` for the rule. The picker is a **translator**:
  plain-language answers → `desired_config` dict + a `create_rule(...)`
  call. No new persistence logic.
- Generated rules get `description="Created by the setup wizard"` and a
  predictable `name` so re-running the picker can detect+replace its own
  prior rule rather than duplicating (look up by created-by + a marker in
  `probe`/`description`).

### Routes
- `GET  /app/setup` — wizard entry; redirects to first incomplete step.
- `GET/POST /app/setup/step/<step>` — `welcome` → `branding` → `smtp` →
  `first-device` → `done`. `branding`/`smtp` reuse `runtime_settings`.
- `GET  /app/devices/<id>/configure` — the reusable mode picker (also the
  wizard's `first-device` step embeds it).
- `POST /app/devices/<id>/configure` — applies mode; on success calls
  `set_desired_config` + `create_rule`, audits
  `device.setup_mode_applied`, redirects to device detail.
- A "first-run" check in `dashboard.py` (or a `before_request` on
  `admin_ui_bp`): if `count(devices)==0 and count(watchdog_rules)==0` and
  `system.setup_completed` runtime-setting is unset, surface a banner /
  redirect to `/app/setup`.

### Templates
- `templates/setup/wizard.html` (step shell, progress dots).
- `templates/setup/_step_welcome.html`, `_step_branding.html`,
  `_step_smtp.html`, `_step_done.html`.
- `templates/setup/mode_picker.html` — three big radio cards with plain
  copy, a help paragraph each, the per-mode question block, and a
  collapsed "Fine-tune" `<details>`.

### Models
- **None new.** Add one runtime-setting key `system.setup_completed`
  (boolean) to `SYSTEM_KEYS` so the first-run banner can be dismissed
  permanently. Optionally `system.setup_completed_at`.

### Implementation steps
1. Add `system.setup_completed` to `SYSTEM_KEYS`; expose on the System tab.
2. Build a `setup_wizard.py` service: pure functions
   `apply_smart_plug(device_id, answers)`, `apply_internet_watchdog(...)`,
   `apply_device_watchdog(...)` — each returns
   `{desired_config, rule_payload|None}` and is independently unit-testable.
3. Build the mode-picker template + `GET/POST /app/devices/<id>/configure`
   in a new `app/blueprints/admin/setup.py`; wire it into
   `admin/__init__.py` side-effect imports.
4. Add a "Set up this device" button on `device_detail.html` (top of the
   Settings card) that links to `/configure`.
5. Build the wizard shell + steps; final step embeds the mode picker for
   the first enrolled device (or links to enrollment if none).
6. Add the first-run banner/redirect; gate on `system.setup_completed`.
7. Unit-test the three `apply_*` functions for correct `desired_config`
   shape and rule payloads; smoke-test the wizard happy path.

### Risks / notes
- Only `device_name` is firmware-validated end-to-end. The picker's
  internet/device blocks are "accepted" — the picker copy must say
  "we'll send these settings to the device; older firmware may ignore
  some of them," and auto-push stays gated by `desired_config.enabled`.
- The hub-side watchdog rule works regardless of firmware support — it
  is the dependable half of internet/device mode. Lead with that.

---

## Feature 2 — Friendly device-config form

### Goal
Replace the raw-JSON `<textarea name="desired_config_json">` on the
device-detail "Desired config" card with a real field-based form.
Raw JSON stays available behind an "Advanced" disclosure.

### Approach
- The allowed shape is already enumerated: `ALLOWED_DESIRED_CONFIG_KEYS`
  in `device_config.py` plus the sub-key tables in
  `docs/firmware-apply-config-schema-v01.md`. The form is a direct
  rendering of that schema — flat fields for the scalar top-level keys
  (`device_name`, `relay_restore_behavior` as a `<select>`,
  `monitor_interval_seconds`, `boot_warmup_seconds`,
  `manual_button_enabled` as a checkbox) and grouped field-sets for the
  object keys (`internet`, `device`, `power`, `notifications`).
- **Reuse the structured-form pattern from `rules.py`/`_rules_forms.py`:**
  add `app/blueprints/admin/_device_config_forms.py` with a
  `build_desired_config_from_form(form) -> dict` builder that raises a
  `DeviceConfigFormError` with an operator-facing message. The route
  stays a thin HTTP translator (architecture.md module-boundary rule).
- Per-field help text and a per-key **support badge** ("verified" vs
  "accepted — firmware may ignore") sourced from a small static map
  mirroring the schema doc's support tiers.
- The "Advanced (raw JSON)" `<details>` keeps the existing textarea and
  the existing `device_desired_config_save_submit` endpoint untouched as
  the escape hatch — exactly how `rules/edit.html` keeps its JSON editor.
- Round-trip safety: if the stored `desired_config` contains a key or
  nested shape the structured form cannot represent without loss, fall
  back to JSON-only for that device (same `form_supported` gate pattern
  `rules.py` uses).

### Routes
- Reuse `POST /app/devices/<id>/desired-config` — add an optional
  `editor=form|json` discriminator. `form` runs the new builder; `json`
  is today's path. Service layer (`set_desired_config`) is unchanged.
- No new routes strictly required.

### Templates
- Refactor the "Desired config" `<section id="desired-config">` block of
  `device_detail.html` into a new include
  `templates/devices/_desired_config_form.html` — structured field-sets
  + the `<details>` advanced JSON block. Keeps `device_detail.html`
  readable and lets the wizard's mode-picker reuse the same partial.

### Models
- **None.** `desired_config` is already a JSON column.

### Implementation steps
1. Write `_device_config_forms.py`:
   `build_desired_config_from_form()` + `DeviceConfigFormError`, plus a
   `desired_config_to_form_values()` inverse for pre-population and the
   `is_form_representable(cfg)` round-trip gate.
2. Build `_desired_config_form.html` partial (field-sets, help text,
   support badges, advanced JSON `<details>`).
3. Update `device_detail.html` to `{% include %}` the partial.
4. Update `device_desired_config_save_submit` to branch on `editor=`:
   `form` → builder, `json` → existing parse path. Same audit event,
   same flash messages.
5. Validate against `ALLOWED_DESIRED_CONFIG_KEYS` (the service already
   does — keep it as the backstop) and reject unknown sub-keys per the
   schema doc.
6. Unit-test the builder both directions and the round-trip gate.

### Risks / notes
- `notifications.webhook_auth_token` is write-only/secret — render it as
  a password field, never echo it back; treat blank-on-save as
  "unchanged" (same trick as the SMTP password field).
- Keep the structured form strictly a subset of what the JSON editor can
  do; never let a structured save silently drop a key the JSON had.

---

## Feature 3 — Backup / restore config UI

### Goal
Export the hub's operator-managed configuration to a portable file, and
import it back — for migration, disaster recovery, and self-host setup.

### What is in scope (operator-managed config — NOT operational data)
- `runtime_settings` rows (System / Network / SMTP / RBAC) — **secrets
  redacted by default** (see below).
- Watchdog rules (`watchdog_rules` — minus runtime counters).
- Schedules (`schedules`).
- Scenes (`scenes`).
- Sites and groups (names + membership, not device rows).
- Per-device `desired_config` / `desired_mode` keyed by a stable natural
  key (MAC address) so a restore can re-attach to re-enrolled devices.
- External-sensor sources (`external_sensors`) — secrets redacted.

### Explicitly OUT of scope (operational / identity data)
Heartbeats, power samples, audit log, commands, device credentials,
enrollment tokens, users/passwords, OAuth identities, sync cursors.
Backup is **config portability**, not a database dump. Restoring users
or device credentials across hubs is a security hazard and a support
nightmare; say so in the UI.

### File format
- A single JSON document, versioned:
  `{"format": "rebooter-hub-config", "format_version": 1,
    "exported_at": ..., "source_hub_id": ..., "sections": {...}}`.
- Each section is a list of natural-key-addressable records.
- Secrets (SMTP password, HMAC keys, webhook tokens, source secrets) are
  **redacted to a sentinel** (`"__redacted__"`) unless the operator
  ticks "include secrets (encrypted)". When included, the file is
  AES-GCM-encrypted with an operator-supplied passphrase (scrypt-derived
  key); the rest of the doc stays plaintext JSON so it is diff-able.

### Restore semantics
- **Dry-run first, always.** Import parses the file, validates
  `format_version`, and renders a per-section plan: create / update
  (last-writer-wins or skip) / skip-unknown / conflict. Nothing is
  written until the operator confirms.
- Natural-key reconciliation reuses the pattern the multi-hub sync
  applier already established (`sync_replicator` / `apply_outbox_event`).
- `desired_config` re-attaches by MAC; devices not present on the target
  hub are listed as "skipped — device not enrolled here."
- Mass-import is a high-blast-radius action — route it through
  `mass_action.validate()` (typed confirmation) like bulk-delete.

### Routes
- `GET  /app/settings/backup` — the Backup sub-page (export + import).
- `POST /app/settings/backup/export` — streams the JSON download;
  honours an `include_secrets` + passphrase pair. Audited
  `config.exported`.
- `POST /app/settings/backup/import/preview` — multipart upload → parse →
  dry-run plan rendered back into the page. No write.
- `POST /app/settings/backup/import/apply` — applies a previously
  previewed plan (plan token stashed in session); typed confirmation.
  Audited `config.imported` with per-section counts.

### Templates
- `templates/settings/backup.html` — replaces the never-wired stub
  concept: an Export card and an Import card (upload → preview table →
  confirm).

### Models / services
- New service `app/services/config_backup.py`:
  `export_config(include_secrets, passphrase) -> dict|bytes`,
  `parse_and_plan(file_bytes, passphrase) -> ImportPlan`,
  `apply_plan(plan) -> ImportResult`.
- **No new model.** Optionally a lightweight `config_backup_log` table if
  the PO wants a history of exports/imports — but the audit log already
  covers it; default to no new table (Q3).

### Implementation steps
1. Build `config_backup.py` — start with export of `runtime_settings` +
   watchdog rules + schedules + scenes; redaction map for secret keys.
2. Add the AES-GCM encryptor for the include-secrets path (scrypt KDF).
3. Build the import parser + `parse_and_plan` dry-run; reuse sync's
   natural-key reconciliation helpers where they generalise.
4. Build `settings/backup.html` and the three routes in a new
   `app/blueprints/admin/backup.py`; wire into `admin/__init__.py`.
5. Add `backup` to the Settings tab strip (`settings_tabs.html`).
6. Gate export-with-secrets and all imports to `super_admin` via
   `role_required_ui`; audit every action.
7. Round-trip test: export from a seeded DB, wipe, import, assert parity.

### Risks / notes
- Importing `runtime_settings.network.*` onto a different hostname will
  break the hub — the dry-run plan must **flag network keys loudly** and
  default them to skip.
- Encrypted-with-secrets files are sensitive; the UI must warn and the
  passphrase must never be logged or audited.

---

## Feature 4 — Finish the stubbed Settings sub-pages (Backup, API-tokens, Webhooks)

Backup is Feature 3 above. This section covers **API-tokens** and
**Webhooks** as Settings sub-pages, plus re-adding all three to the tab
strip. (The Webhooks *engine* is Feature 6; this is its Settings UI.)

### 4a. API tokens
Today, programmatic API access uses a user's bearer token
(`g.current_user.get_bearer_token()` — see `settings.py` sync calls).
That couples automation to a human account. A first-class **personal /
service API token** is the Tier-2 deliverable.

- **Model:** new `api_tokens` table — `id`, `name`, `token_hash`
  (Argon2/SHA-256, never store plaintext), `token_prefix` (first ~8 chars
  shown in the list for identification), `created_by_user_id`, `site_id`
  (nullable, scope parity), `scopes` (JSON — e.g. `["read"]` /
  `["read","write"]`), `last_used_at`, `expires_at` (nullable),
  `revoked` (bool), `created_at`. Mirrors `DeviceCredential` exactly —
  reuse that hashing pattern.
- **Auth:** extend `middleware/admin_auth.py` (or a new
  `token_auth.py`) so `Authorization: Bearer rbt_<token>` resolves to an
  `api_tokens` row, sets a synthetic principal carrying the token's
  scopes, and enforces scope on write endpoints. The plaintext is shown
  **once** at creation and never again.
- **Routes:** `GET /app/settings/api-tokens` (list — prefix, name,
  scopes, last-used, expiry), `POST .../create` (returns the one-time
  plaintext), `POST .../<id>/revoke`. All `admin`+; audited.
- **Template:** `templates/settings/api_tokens.html`.

### 4b. Webhooks
The Settings → Webhooks page is the **management UI for outbound
webhook endpoints + notification channels** defined in Feature 6.

- **Routes:** `GET /app/settings/webhooks` (list channels + recent
  delivery attempts), `POST .../create`, `POST .../<id>/test` (fires a
  synthetic event through the SSRF-guarded sender), `POST .../<id>/toggle`,
  `POST .../<id>/delete`.
- **Template:** `templates/settings/webhooks.html`.
- Models + the delivery engine are Feature 6.

### Tab strip
Add Backup, API tokens, Webhooks to both the `<nav>` and `<select>` in
`_components/settings_tabs.html`, between "Notifications" and
"Integrations". Each page passes its `settings_tab` value. Delete the
dead `templates/stub.html` once all three are real.

### Implementation steps
1. Add `api_tokens` model; register in `models/__init__.py`; `ensure_columns`
   handles ADD COLUMN if extending later.
2. Token auth resolver in `admin_auth.py` + scope enforcement.
3. `services/api_tokens.py` — mint / list / revoke / verify (constant-time).
4. `blueprints/admin/api_tokens.py` — routes + `api_tokens.html`.
5. `blueprints/admin/webhooks.py` — routes + `webhooks.html` (consumes
   Feature 6 services).
6. Extend `settings_tabs.html`; remove `stub.html`.
7. Tests: token mint→use→revoke; scope-denied write; expired token.

### Risks / notes
- API tokens are bearer credentials on a public SaaS — enforce a length
  cap, default expiry, rate-limit creation, and audit every mint/revoke.
- Scope must be enforced server-side on every write route, not just
  hidden in the UI.

---

## Feature 5 — Mobile-first dashboard pass

### Goal
Make `dashboard.html` genuinely mobile-first. It is currently a
`.stat-grid` of tiles + a `.placeholder` activity feed + a "Jump to"
list — functional but desktop-shaped.

### Approach (CSS + template; minimal Python)
- **Priority-ordered single column on phone.** On a narrow viewport the
  most actionable things come first: (1) anything needing attention —
  offline devices, never-heartbeated, unregistered-auth loops, firmware
  failsafes — as a compact "Needs attention" card; (2) the stat tiles as
  a 2-up grid (not 1-up — they are glanceable); (3) recent activity;
  (4) "Jump to" collapsed into a `<details>`.
- **A real "Needs attention" summary.** `dashboard.py`'s stats service
  already computes `devices_offline`, `devices_never_heartbeated`,
  `unregistered_active`. Add a derived `attention_items` list (count +
  deep link + severity) so the card renders zero-state cleanly ("All
  good — nothing needs attention").
- **Touch targets.** Tiles and feed rows become ≥44px tap targets;
  whole-tile links (already `<a class="stat">`) keep that.
- **CSS only for layout.** Drive everything off `app.css` media queries
  at the existing 640px / 768px breakpoints the codebase already uses
  (`settings_tabs.html`, `device_detail.html` mobile-card pattern).
  No JS framework, consistent with the CSP-tight, no-`unsafe-inline`
  posture (`theme_flash.js` was extracted precisely for that).
- **Sticky bottom-nav already exists** (`layout.html .bottomnav`) — make
  sure the dashboard's last card has bottom padding so it is not
  occluded behind it.

### Routes / Models
- **No new routes, no new models.** Dashboard stays one page.

### Templates / static
- Rework `dashboard.html` section order + add the "Needs attention" card.
- Extend `static/css/app.css`: mobile-first base, progressively enhanced
  at `min-width:640px` / `768px`. Audit `.stat-grid`, `.activity-feed`,
  `.topline` for narrow-viewport overflow.

### Services
- Extend `services/dashboard.py` stats with `attention_items` (pure
  derivation from numbers it already gathers — cheap, no new query if
  the counts are already loaded).

### Implementation steps
1. Add `attention_items` to the dashboard stats payload.
2. Restructure `dashboard.html`: attention card → 2-up stat grid →
   activity → collapsible "Jump to".
3. Rewrite the relevant `app.css` blocks mobile-first; verify at
   320/375/414px and at the 640/768 breakpoints.
4. Manual pass on iOS Safari + Android Chrome (notch-safe — the layout
   already sets `viewport-fit=cover`).
5. Accessibility check: tap-target size, `aria-current`, contrast.

### Risks / notes
- Keep it CSS-first; the project deliberately avoids inline JS for CSP.
- This touches shared `app.css` — regression-check Devices, Rules,
  Settings, device-detail at mobile widths in the same pass.

---

## Feature 6 — Hub-side notifications / outbound webhooks

### Goal
An outbound-webhook engine + notification channels (Pushover,
Slack/Discord-style incoming-webhook) so the hub can tell an operator
when something happens — a watchdog rule fired, a device went offline,
a firmware failsafe occurred.

> **This is hub-side outbound and the hub is public SaaS — SSRF
> protection is mandatory.** Device-side webhooks (firmware
> `notifications.webhook_url`) are separate, firmware-owned, and may
> legitimately target private LAN IPs; this engine must NOT.

### Concepts
- **Notification channel** — a configured destination. Kinds:
  `webhook_generic` (operator-supplied URL + method + headers),
  `slack` / `discord` (incoming-webhook URL, kind-specific JSON shape),
  `pushover` (app token + user key). Each channel has an `enabled` flag
  and a `site_id` scope.
- **Event subscription** — which hub events feed which channels.
  Event types: `watchdog.rule_fired`, `watchdog.rule_escalated`,
  `device.went_offline`, `device.failsafe`, `device.recovered`,
  `firmware.deployment_completed`. A subscription is
  `(event_type, channel_id, optional site filter)`.
- **Delivery** — every send is recorded as a delivery attempt with
  status, HTTP code, response snippet, and retry count, so the Webhooks
  Settings page (Feature 4b) can show "last delivery: 200 OK / failed".

### SSRF protection (the load-bearing part)
A shared `app/services/ssrf_guard.py`:
1. Parse the target URL; require scheme `https` (allow `http` only if an
   explicit `outbound.allow_http` runtime-setting is on — default off).
2. Resolve the hostname to **all** A/AAAA records.
3. Reject if **any** resolved IP is private / loopback / link-local /
   multicast / reserved / ULA / CGNAT (`100.64.0.0/10`) — use
   `ipaddress.ip_address(...).is_private / is_loopback / is_link_local /
   is_reserved / is_multicast` plus an explicit CGNAT check.
4. Reject literal-IP hosts that resolve into those ranges, IPv4-mapped
   IPv6, and `0.0.0.0`.
5. **Pin the connection to the validated IP** — resolve once, validate,
   then connect to that exact IP with the original `Host` header, to
   close the DNS-rebinding (TOCTOU) gap. Implement via a custom
   `requests` transport adapter or `urllib3` `HTTPConnection` override.
6. Disable redirects by default (a 30x can bounce to an internal host);
   if redirects are allowed, re-run the full check on every hop.
7. Apply a hard timeout, a response-size cap, and a per-channel
   rate-limit. Block the metadata endpoints explicitly
   (`169.254.169.254` is already link-local, but assert it).
8. Slack/Discord/Pushover URLs are validated the same way — no
   allow-listing a vendor host by name (the hostname can still be
   spoofed via a CNAME), the IP-range check is the real gate.

This guard is also where the existing `settings.py` `localhost:8090`
call and the sync replicator *should* eventually be routed (those are
trusted internal calls, so they would use an explicit
`allow_internal=True` bypass — noted, not in scope here).

### Delivery engine
- Outbound sends are **queued and processed by APScheduler**
  (`app/jobs/scheduler.py` already runs the watchdog/rollup jobs) — never
  block a request thread on an external HTTP call.
- A `webhook_deliveries` table is the queue: rows in `pending` state,
  picked up by a job, moved to `sent` / `failed`, retried with
  exponential backoff up to N attempts, then `dead`.
- Each outbound request is HMAC-signed with a per-channel secret
  (`X-Rebooter-Signature`) so receivers can verify authenticity — same
  spirit as the existing inbound `X-Webhook-Secret` and sync HMAC.

### Models (new)
- `notification_channels` — `id`, `name`, `kind`, `config` (JSON:
  URL/token/headers; secrets stored encrypted or write-only),
  `enabled`, `site_id`, `created_by_user_id`, `created_at`, `updated_at`.
- `notification_subscriptions` — `id`, `event_type`, `channel_id` (FK),
  `site_id`, `enabled`, `created_at`.
- `webhook_deliveries` — `id` (BigInteger), `channel_id` (FK),
  `event_type`, `payload` (JSON), `status`
  (`pending`/`sent`/`failed`/`dead`), `attempts`, `http_status`,
  `response_snippet`, `next_attempt_at`, `created_at`, `updated_at`.
  Index `(status, next_attempt_at)` for the worker.

### Services
- `app/services/notifications.py` — channel CRUD, subscription CRUD,
  `emit(event_type, payload, site_id)` — the single hook the rest of the
  hub calls. `emit()` resolves matching subscriptions and inserts
  `webhook_deliveries` rows; it does **not** send inline.
- `app/services/webhook_delivery.py` — the SSRF-guarded sender + the
  per-channel payload formatters (generic / slack / discord / pushover)
  + the retry/backoff worker entrypoint.
- `app/services/ssrf_guard.py` — as above.

### Call sites that emit events
- `watchdog_runtime/_actions.py` — emit `watchdog.rule_fired` /
  `watchdog.rule_escalated` after an action fires.
- The heartbeat path / a staleness job — `device.went_offline` /
  `device.recovered`.
- `services/failsafe.py` — `device.failsafe`.
- `services/deployments.py` — `firmware.deployment_completed`.
All of these call the single `notifications.emit()` — best-effort,
never raises into the caller (same discipline as
`device_config.record_reported_config`).

### Routes
- All channel/subscription/test/delivery-log UI lives on
  `GET /app/settings/webhooks` (Feature 4b) — `webhooks.py` blueprint.
- The watchdog `escalation` action `{kind: "webhook", url: ...}` already
  exists in the rule schema (`watchdog.py` docstring) — that escalation
  path **must be re-pointed through the SSRF guard**; today it is a raw
  field. This is a security fix folded into Feature 6.

### Implementation steps
1. Build `ssrf_guard.py` first, with the IP-pinning transport adapter;
   unit-test it hard (private ranges, CGNAT, IPv4-mapped IPv6,
   redirect-to-internal, DNS-rebind simulation).
2. Add the three models; register in `models/__init__.py`.
3. `notifications.py` — channel/subscription CRUD + `emit()`.
4. `webhook_delivery.py` — formatters + SSRF-guarded sender + backoff
   worker; register the worker job in `jobs/scheduler.py`.
5. Wire `emit()` calls into the watchdog/heartbeat/failsafe/deployment
   call sites.
6. Build the Webhooks Settings page (Feature 4b) on top of these.
7. Re-point the watchdog `escalation` `webhook` kind through the guard.
8. Tests: SSRF guard suite; emit→queue→deliver happy path; retry/backoff;
   per-kind payload shape.

### Risks / notes
- SSRF is the whole ballgame here — the guard must be written and tested
  *before* any sender code can call out. Treat IP-pinning as mandatory,
  not optional; a resolve-then-`requests.get(url)` is the classic
  rebinding bug.
- Secrets in `notification_channels.config` (Slack URL is itself a
  secret, Pushover tokens, HMAC secrets) — store write-only / encrypted,
  never echo, redact in the Feature-3 backup export.
- Keep `emit()` strictly best-effort and async-queued so a slow or
  malicious receiver can never degrade heartbeat ingestion.

---

## Cross-cutting concerns

- **Multi-tenancy / scope.** Every new model (`api_tokens`,
  `notification_channels`, `notification_subscriptions`,
  `webhook_deliveries`) carries `site_id` (nullable FK
  `sites.id ON DELETE SET NULL`). Every new list query runs through
  `rbac_filter.py`. If a hard `tenant` boundary is coming (Q1), these
  should carry `tenant_id` instead/also from day one.
- **Schema migrations.** Follow the existing pattern — `ensure_schema()`
  + `_ensure_columns()` ADD COLUMN IF NOT EXISTS at startup, plus an
  Alembic revision under `migrations/` for parity.
- **Audit.** Every mutation new in this set audits via
  `audit_service.record()` with `target_type` / `target_id` — matching
  every existing handler.
- **CSP.** No new inline JS; extract any needed script to `static/js/`
  like `theme_flash.js` / `settings_tab_select.js`.
- **Secrets.** SMTP-password masking pattern (`********` = unchanged,
  blank = clear) is the template for every new secret field (API tokens
  shown once, channel secrets, backup passphrase).
- **Suggested sequencing.** 2 → 1 → 5 → 6 → 4 → 3. Feature 2 is
  low-risk and de-risks Feature 1's mode picker (shared partial).
  Feature 6's SSRF guard + models must precede Feature 4b. Feature 3
  (backup) lands last so it can already serialise the Feature-6 channels.

---

## Questions for the product owner

1. **Tenancy model.** Is a hard per-tenant boundary (`tenant`/`org`
   table) on the near roadmap, or does `site_id` + RBAC remain the
   isolation model? New tables' scope columns depend on the answer.
2. **3-mode picker vs. firmware reality.** Only `device_name` is
   "validated end-to-end" with the firmware; internet/device config
   blocks are "accepted" but unverified. Is it acceptable to ship the
   picker with copy that says "older firmware may ignore some settings,"
   relying on the dependable *hub-side* watchdog rule as the real
   guarantee — or should the picker be gated until the firmware team
   promotes those keys?
3. **Backup scope & history.** Confirm the in/out-of-scope split
   (config yes; users, credentials, heartbeats, audit no). Should there
   be a persisted `config_backup_log`, or is the audit log sufficient?
4. **API-token scopes.** What scope granularity is wanted for v1 —
   just `read` / `read+write`, or finer (per-resource: devices, rules,
   firmware)? And a mandatory default expiry, or operator-chosen?
5. **Notification event catalogue.** Is the proposed event set
   (`watchdog.rule_fired`, `device.went_offline`, `device.recovered`,
   `device.failsafe`, `firmware.deployment_completed`,
   `watchdog.rule_escalated`) the right v1 set, or are
   per-device-power-threshold or quiet-hours/digest features wanted now?
6. **Outbound HTTP policy.** Should the SSRF guard refuse plain `http://`
   outright on the public SaaS deployment (recommended), with an opt-in
   runtime flag only for self-hosters?
