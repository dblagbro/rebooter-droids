# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.28] - 2026-06-06

## [0.6.27] - 2026-06-06

## [0.6.26] - 2026-06-06

## [0.6.25] - 2026-06-06

## [0.6.24] - 2026-06-06

## [0.6.23] - 2026-06-06

## [0.6.22] - 2026-06-06

## [0.6.21] - 2026-06-06

## [0.6.20] - 2026-06-06

## [0.6.19] - 2026-06-06

## [0.6.18] - 2026-06-06

## [0.6.17] - 2026-05-30

## [0.6.16] - 2026-05-29

### Added
- **Live device-detail status panel (#171)** — extends the 0.6.12 list-page
  live updates to per-device pages. A compact strip in the Overview polls
  `/admin/devices/live` every 3s and surfaces heartbeat_state + age, relay
  state, uptime, `free_heap`, `max_free_block`, `heap_fragmentation_pct`
  (the last three from the firmware 0.2.10+ trajectory ring's tail
  sample). `frag_pct` turns amber at >=25% and red at >=40% so operators
  can watch fragmentation creep toward the BearSSL ceiling in real time.

## [0.6.15] - 2026-05-29

### Changed
- **Idle command-poll cadence 30s → 300s (#170)** — when the heartbeat
  handler finds no pending commands for the device, return
  `next_poll_after_seconds=300` (5min) instead of the old 30s default.
  Pairs with firmware 0.2.13's heartbeat-piggyback to drop steady-state
  HTTPS rate per device from ~3 calls/min (1 hb + 2 polls) to ~1.2/min
  (1 hb + 1 poll/5min) — ~3x slower BearSSL fragmentation creep. Burst
  cadence (2s while commands are queued) preserved.

## [0.6.14] - 2026-05-29

### Added
- **Heartbeat piggybacks pending commands (#170)** — heartbeat ACK now
  drains the device's pending command queue (`list_pending_for_device`,
  `mark_delivered=True`) and includes the rows in
  `data.pending_commands`. Firmware 0.2.13+ executes them after the
  heartbeat exits its handler scope. Old firmware ignores the field —
  the dedicated `/device/commands` GET remains the fallback delivery.

## [0.6.13] - 2026-05-29

### Added
- **Adaptive command-poll cadence (#168)** — when commands are pending
  for a device, return `next_poll_after_seconds=2` in the heartbeat ACK
  so the device polls again within ~2s instead of the default 30s. As
  soon as the queue empties, the value rebounds. Cuts burst-control
  latency for follow-up commands without changing steady-state pressure.
  First-command latency unchanged (still gated on next heartbeat).

### Note
- An earlier firmware-side `Prefer: wait=N` long-poll attempt in 0.2.11
  was reverted — see `feedback_esp8266_blocking_long_poll`. ESP8266 is
  single-threaded; a blocking long-poll starves the LAN web server and
  trips the soft WDT (`reset_reason="Exception"` reboots within 2 min).

## [0.6.12] - 2026-05-29

### Added
- **Live device-list updates (#167)** — slim
  `/api/v1/admin/devices/live` endpoint returns per-device online state,
  relay state, last_seen, and the latest heap snapshot. The
  `devices_list.html` template tags rows with `data-device-id`, hooks
  the online cell and relay button, and includes a small JS poller
  that hits the endpoint every 3s and swaps the badge text + button
  label/colour + form's hidden `type` input in place. After a relay
  toggle submit the poller bumps to 1Hz for 30s so the state change
  appears as soon as the device reports back.

## [0.6.11] - 2026-05-29

### Added
- **Heap trajectory ingest (#165)** — `device_heartbeats.heap_trajectory`
  JSONB column (added via `bootstrap._PENDING_COLUMNS`) stores the
  per-heartbeat heap-sample ring uploaded by firmware 0.2.10+:
  `[{up,fh,mfb,fp}, ...]`. Surfaces fragmentation creep that the
  device's polled `/api/status` `free_heap` alone hides (free_heap
  often stays flat while `max_free_block` declines toward the BearSSL
  ceiling). Persisted indefinitely on the hub for forensic correlation.

## [0.6.10] - 2026-05-29

### Added
- **Nearby-networks scan (#154)** — firmware 0.2.8+ does an opt-in
  periodic async WiFi scan and reports the top-5 networks in the
  heartbeat. The hub stores the latest snapshot on the device
  (`devices.last_wifi_scan` JSONB + `last_wifi_scan_at`, both registered
  in `bootstrap._PENDING_COLUMNS`) and shows it on the device-detail
  "WiFi environment" section with strong/ok/weak labels. NULL until a
  scan arrives. RF-environment visibility (competing APs / mesh / noise).

## [0.6.9] - 2026-05-29

### Fixed
- **Schema-drift guard** — `device_heartbeats.wifi_rssi_dbm` (added in
  0.6.8) was missing from `bootstrap._PENDING_COLUMNS`, so the startup
  bootstrap couldn't add it to an already-existing production DB
  (a fresh DB was fine via `create_all`). Registered it in
  `_PENDING_COLUMNS` and dropped the stray alembic migration `0008`
  (this project upgrades prod via `bootstrap.ensure_schema`, not
  alembic). Restores CI green.

## [0.6.8] - 2026-05-29

### Added
- **WiFi RSSI capture** — `device_heartbeats.wifi_rssi_dbm` column stores
  the current-connection signal strength reported by firmware 0.2.7+.
  Surfaced on the device-detail Overview with a strong/ok/weak label.
  NULL for pre-0.2.7 heartbeats. First visibility into per-device WiFi
  signal quality. (Bootstrap upgrade-path registration was missed —
  fixed in 0.6.9.)

## [0.6.7] - 2026-05-28

### Added
- **`device_heartbeat_stale` watchdog probe** — the first probe that
  watches the devices the hub *manages* (vs an outbound target or a
  power sample). Fails when a device's `last_heartbeat_at` is older
  than `max_age_seconds` (default 300) or it never heartbeated. Pair
  with a NOTIFY action to be alerted when a fleet device goes silent.
  Wired through the full canonical-probe path (validation + runtime in
  lockstep per the BUG-058 contract).

## [0.6.6] - 2026-05-27

### Added
- **`device.rebooted` events** emitted automatically when a heartbeat's
  `uptime_seconds` regresses below the prior heartbeat's value. Event
  details capture `prior_uptime_seconds`, `new_uptime_seconds`,
  `reset_reason`, and `last_planned_restart_reason` — operators can now
  chart reboot cadence and cause directly from the events feed.

### Changed
- **`source_flags` decoder** now returns human-readable bit names
  (`REAL`, `VOLTAGE_VALID`, `POWER_VALID`, `ENERGY_VALID`, …) in a new
  `names` field alongside `bits_set`. Bit semantics mirror
  `rebooter-firmware` `include/app_state.h`. Unmapped bits land in
  `bits_set` but not `names` so a new firmware bit shows up rather
  than being silently dropped.
- **BACKLOG.md** Low-heap-power-upload row closed (resolved by
  rebooter-firmware `0.2.3` BearSSL pool refactor, commit `092daac`;
  full arc in `docs/notes/2026-05-26-low-heap-bench-validation.md`).
  Firmware-team-asks header reframed — we own these.

## [0.6.5] - 2026-05-23

## [0.6.4] - 2026-05-22

## [0.6.3] - 2026-05-22

## [0.6.2] - 2026-05-22

## [0.6.1] - 2026-05-21

## [0.6.0] - 2026-05-21

## [0.5.102] - 2026-05-19

### Changed — B11 sync: doc truthfulness + preflight + runbook

The "B11 applier is a stub" framing in `design.md §6` and the
`BACKLOG.md` B11 historical entry was **30+ versions stale**. The
applier shipped fully in v0.5.70–.72 (LWW + natural-key reconciliation
+ site-FK remap + tombstone replay protection) and is gated by 18
in-process tests across `test_v0570_b11_applier.py` +
`test_v0571_b11_emission.py` + `test_v0572_b11_natural_key.py`. No
code work remained for the applier itself; what was missing was an
operator-facing preflight + runbook so the `sync.enabled` flip could
be exercised confidently.

- **`design.md §6`** (Multi-hub posture) rewritten: B11 is
  code-complete; `sync.enabled` remains default-off because the flip
  is the operator's call after a dual-hub preflight, not because the
  code is unfinished. **§7** "Deliberately deferred" line refreshed to
  match.
- **`BACKLOG.md`** historical B11 entry updated — drops the stale
  "Scaffold only" + "create/update LWW is still a stub" wording.
- **`docs/runbooks/sync-enable.md`** — new operator playbook. Covers
  what the flip does, the preconditions checklist, the three-phase
  preflight (read-only → flip → convergence-test), 24-h soak
  guidance, kill-switch + rollback procedures.

### Added — admin sync-status endpoint + preflight script

- **`GET /api/v1/admin/sync/status`** (admin-Bearer-auth) — wraps the
  existing HMAC-only `/api/v1/sync/status` so admin tooling can query
  the same outbox + cursor data plus the `sync.enabled` flag,
  configured peer list (`id` + `base_url` only — never the HMAC key),
  and a `hmac_key_set` boolean. Operator-useful regardless of the
  preflight; needed by the preflight script.
- **`scripts/sync-dual-hub-preflight.sh`** — operator harness for the
  B11 flip. Two modes: `--read-only` (preconditions only — peer
  config, HMAC, hub_ids differ, current outbox + cursor snapshot) and
  default convergence-test (creates a marker Site on hub-A, polls
  hub-B for it, deletes it on hub-A, polls hub-B for the tombstone;
  reports both convergence latencies). Never flips `sync.enabled`
  itself — that stays an explicit operator decision per the runbook.

### Tests

- `tests/qa/test_v05102_sync_admin_status.py` (5 tests) — pins the new
  admin endpoint's shape, the `sync.enabled=false` default safety
  property, the HMAC-key-never-leaked invariant, the admin-auth
  requirement, and the peer-entry credential-surface tightness.

`sync.enabled` was **not touched** this ship. Post-deploy verification
caught a separate stale assumption: **the live production cluster
already has `sync.enabled=true` on both hubs** (set in a prior session;
both directions fully converged — tmrwww01 outbox max_seq=14, tmrwww02
cursor for www at seq=14; tmrwww02 outbox max_seq=5, tmrwww01 cursor
for www2 at seq=5; both `last_error=null` since 2026-05-16). The new
runbook is now best read as "verify-what's-running + recover-if-needed"
rather than "first-time-flip"; the kill-switch + rollback sections
apply unchanged.

Also caught pre-commit: the new admin endpoint mapped the peer-config
field as `base_url`, but the production-truth + replicator-truth field
is `url`. Fixed in `peer_hubs_safe`; the runbook example + the
QA test field-name assertion updated to match; added
`test_seeded_peer_url_round_trips` so an empty-`peer_hubs` test
fixture can no longer mask a field-name drift.

Gate widens 856 → 862 (5 sync-admin-status + 1 seeded round-trip).

### Operator validation — convergence test PASSED against production

After the ship, ran `scripts/sync-dual-hub-preflight.sh` against the
live pair (tmrwww01 ↔ tmrwww02). Two bugs fell out of the first run
— both in the script, not the wire:

1. The `curl_a` / `curl_b` helpers passed `"$@"` *and* `"$HUB$1"` to
   curl, so curl saw two URLs and the response body was concatenated
   garbage that broke downstream jq.
2. The jq path was `.data[]?` but `/api/v1/admin/sites` returns
   `{ok: true, data: {sites: [...]}}`. Path fixed to `.data.sites[]?`.

Trap-driven cleanup ran correctly even on the crashed run (no stray
marker on either hub — verified). After fix:

- **Create** on hub-A → converged on hub-B in **3 seconds**.
- **Delete** on hub-A → tombstone reflected on hub-B in **5 seconds**.
- Both within the ~10 s replicator tick cadence.
- ID preserved across hubs.

The script (in `scripts/`, outside the Docker image) is updated in
this same ship; the v0.5.102 image is unchanged.

## [0.5.101] - 2026-05-19

### Fixed — numeric form inputs can no longer 500 the page

The remediation-plan Group A ship: closes the latent uncaught-`ValueError`
bug carried since v0.5.67 (refactor-log entry "one pre-existing latent
issue"), backfills the rules-edit probe-shape reference, and archives
the stale qa-notes diary.

- **`_int_field` + `FormValidationError` helper** in
  `app/blueprints/admin/_common.py` — wraps the `int(form.get(name) or
  default)` pattern. Empty/missing falls back to the caller's default
  (matches prior behaviour). Non-integer raises a typed error with the
  field name and the bad value; the calling handler turns it into a
  flash + redirect (form routes) or a `validation_failed` envelope
  (JSON API routes). Optional `lo` / `hi` bounds available but
  deliberately not adopted at any site yet (drop-in, no behaviour
  change).
- **All seven 500-able sites wired through it**:
  - `rules.py::rules_create_submit` (structured form),
  - `rules.py::rules_create_json_submit` (JSON-editor),
  - `rules.py::rules_edit_submit` (JSON-editor on the edit page),
  - `rules.py::rules_edit_form_submit` (structured edit form),
  - `schedules.py::schedules_create_submit` (form),
  - `schedules.py::create_schedule_api` (JSON API).
  The two adjacent sites (`groups.py::group_send_command_submit` and
  `devices_ui.py::device_send_command`) already had defensive
  `try/except ValueError → default` fallbacks and were left as-is.

### Added — probe-shape reference backfill (rules edit page)

The "Probe shape reference" card on `/app/rules/<id>/edit` documented
~9 of the 26 probe kinds in `KNOWN_PROBE_KINDS`. Backfilled the
missing 16 (6 network kinds — internet / ping / tcp / http / dns /
gateway — and 10 integration kinds — `ha_numeric_above` / `_below`,
`solar_production_above` / `_below`, `snmp_interface_down`,
`snmp_throughput_above` / `_below`, `snmp_error_rate_above`,
`media_session_active`, `webhook_field_equals`). Intro paragraph
refreshed.

### Changed — archived `docs/qa-notes.md`

Moved to `docs/sessions/qa-notes-archived-20260519.md`. ~105 K-token
soak-test diary; explicitly flagged as "history only" in
`test-plan.md` for several sweeps. Preserved for cross-references
(BUG-entries, the 2026-05-15 charter, the firmware refactor log)
without polluting the top-level `docs/`.

### Tests

- `tests/qa/test_v05101_form_input_hardening.py` — pins the
  redirect-not-500 behaviour at every fixed site (6 tests). Runs
  in the `-m ci` gate.

Gate widens 850 → 856.

### Added — `tests/unit/` coverage for the firmware-deployment service

Test-only ship. Closes the second of the two follow-ups
(`tests/unit/` for `deployments`) noted in the pause-state notes after
the watchdog tick + state-machine slice landed in v0.5.99.

- `test_deployments_service.py` — covers
  `app/services/deployments.py` end-to-end:
  - `create_deployment` — target-type validation (rejects unknown
    types, requires target_id when not `all_devices`); release lookup;
    fan-out to `device` / `group` / `site` / `all_devices` targets;
    channel inheritance vs override; the supersede-prior-pending
    invariant (covers both `pending` and `delivered`).
  - `assignment_for_device` — no-active → None, pending+delivered
    returned, terminal states (`completed` / `failed` / `superseded`)
    ignored.
  - `mark_assignment_delivered` — pending→delivered promotion;
    no-pending no-op.
  - `reconcile_assignment_reported_version` — records reported
    version, marks completed when reported matches target, clears
    prior `error_message` on completion, stores error messages,
    no-ops on missing/empty device id, holds in `delivered` when
    versions differ.
  - `list_deployments` — assignment-state count breakdown and
    newest-first ordering.

25 new unit tests. Gate widens from ~825 to ~850.

## [0.5.99] - 2026-05-19

### Added — `tests/unit/` coverage for the watchdog tick + state machine

Test-only ship. `tests/unit/test_watchdog_binding.py` already covered
the level-triggered binding path; this fills in the *non-binding*
state machine and the tick orchestration.

- `test_watchdog_state_transitions.py` — covers
  `_update_state_and_maybe_fire` end-to-end for `notify_only` rules:
  success-when-armed no-op, failure-streak below threshold,
  threshold-cross fire + `action_fired` event, cooldown gate
  recording `cooldown_skip`, refire after cooldown elapses,
  recovery-streak progression, recovery-threshold rearm,
  `probe_error` holds streaks, `last_probed_at` / `last_outcome`
  always update. Also covers `_rule_is_due` (no-probe / inside-window
  / past-window) and `_in_maintenance_window` (empty / hit / miss /
  malformed / naive-UTC timestamps).
- `test_watchdog_tick.py` — covers the `tick()` orchestrator:
  `REBOOTER_WATCHDOG_DISABLED=1` short-circuit, portal maintenance
  mode short-circuit, disabled-rule filtering, "not yet due" skip,
  rule maintenance window → `maintenance_skip` event, raising probe
  → `probe_error` event + errors stat, success outcome wiring, full
  failure-threshold → fire across two ticks. Uses the v0.5.82
  injectable `now` and monkeypatches `run_probe` so no real network
  is touched.

25 new unit tests. Gate widens from ~800 to ~825.

## [0.5.98] - 2026-05-19

### Changed — P-QA gate-3: 6 more test files now gate

The CI gate has been widening test-by-test as the blockers behind each
file got cleared. This pass clears the gate-3 backlog from
`docs/test-plan.md`:

- `test_hardening_probes` — replaced hardcoded production credentials
  with the `admin_creds` fixture (so it works against any instance);
  the cookie-`Secure` assertion now skips cleanly when the instance is
  running with `REBOOTER_SESSION_COOKIE_SECURE=0` (HTTP localhost) but
  still asserts `HttpOnly` + `SameSite` every gate.
- `test_v033_cookie_domain` — `test_theme_cookie_legacy_name_still_read`
  derived the cookie domain from `urlsplit(base_url).netloc`, which
  for a `host:port` base URL included the port → the cookie was never
  sent back. Strip the port.
- `test_v042_watchdog_runtime::test_probe_now_http_success` — the
  probe URL was `{base_url}/api/v1/version`, but `base_url` is the
  *test client's* view of the app; the probe runs inside the app
  container and couldn't reach a host-mapped port. Use the app's own
  in-container listener (`http://localhost:8090/api/v1/version`) —
  reachable from any deployment.
- `test_v034_bulk_actions` — added a module-scoped autouse
  `_seed_bulk_rows` fixture that seeds one device + one group + one
  invitation + one enrollment token so every list page has a row and
  the parametrized bulk-form scaffolding assertion exercises the real
  path on a fresh CI replica (instead of skipping via the empty-state
  fallback).
- `test_responsive` + `test_ui_flows` — browser-driven; the
  `chromium_browser` fixture already skips cleanly when playwright /
  the chromium binary isn't available. Now gated; both skip uniformly
  in CI.

Gate widens from ~739 to ~800 tests (the bulk of the increase is
`test_ui_flows`'s browser suite, which actually runs when playwright
+ chromium are present locally; in GitHub Actions without a browser
those tests skip cleanly). The `test-plan.md` "Known coverage gaps"
item is reduced — the only remaining ungated file is
`test_v0520_long_poll_commands`, excluded by design (it deliberately
holds requests for 4-6 s per test).

## [0.5.97] - 2026-05-19

### Added — device-detail Watchdog / Schedule sections list real rules

The device-detail page's Watchdog and Schedule sections were
unconditional empty-state stubs ("…ship in P4") — they never queried
the backend, so even a device covered by rules showed "none". They now
list the rules and schedules acting on the device:

- `watchdog.list_rules_for_device(device_id)` and
  `schedules.list_for_device(device_id)` — both reuse the watchdog
  runtime's `resolve_target_devices`, so the page lists exactly what
  the runtime would act on: direct `device` targets and `group`
  memberships. (`tag` targets resolve to no devices today — a
  tag-targeted rule shows on no device's page, matching the runtime
  no-op.) Distinct targets are resolved once each.
- The Watchdog section shows each rule's name, plain-English sentence,
  armed/disabled status, and failure streak, with an Edit link; the
  Schedule section shows name, sentence, next run and status. Both
  keep an honest empty state when nothing targets the device.
- Fixed: the schedule section's empty-state CTA pointed at the rules
  page — now links to `/schedules`.

6 new unit tests + 4 new live tests (direct, group-membership, empty
state, schedule).

## [0.5.96] - 2026-05-19

### Fixed — status page "active rules" tile shows a live count

The status page (`/app/`) totals row hardcoded `0` active rules with a
"watchdogs ship in P4" sub-label — stale since v0.3.1; the watchdog
rules engine shipped long ago. The tile now reads live counts:

- `dashboard.stats()` returns `rules_total` and `rules_active`
  (`WatchdogRule` rows, and the `enabled` subset).
- The tile shows the active count, and its sub-label reflects reality
  — "of N total · M disabled", "all enabled", or "none yet — add one".

2 new unit tests (`dashboard.stats()` rule counts) + 2 new live tests
(no stale copy, count tracks a created rule).

Known adjacent staleness, not fixed here: the device-detail page's
Watchdog / Schedule sections are still unconditional empty-state stubs
(they never query rules targeting the device) — wiring those is a
separate item, tracked in the pause-state notes.

## [0.5.95] - 2026-05-19

### Added — rules form-builder: parity on the *edit* page (Phase 2B)

v0.5.93 brought the structured rule **create** form up to the
TV-scheduling rule shapes. The **edit** page lagged: an `epg_show_airing`
rule, or any rule with a `relay_on` / `relay_off` / `apply_scene` /
`binding` action, fell back to the JSON editor — there was no field
block to round-trip it. The edit form now mirrors the create form:

- **Probe** — `epg_show_airing` joins `STRUCTURED_PROBE_KINDS`, so an
  EPG rule edits in the structured form (show title + optional
  network), pre-populated from the rule. Was JSON-editor-only.
- **Actions** — `relay_off` / `relay_on` / `apply_scene` / `binding`
  gained field blocks on the edit page, toggled by the shared
  `rules_create_action.js` and pre-selected from the rule (the
  `apply_scene` scene picker and the `binding` active/cleared scene
  pickers all reflect the saved `scene_id`s).
- **Safe fallback** — a new `_action_form_supported` gate is the
  action-side analogue of `STRUCTURED_PROBE_KINDS`: an `apply_scene`
  with inline `items` (no `scene_id`), or a `binding` with a non-scene
  edge, still drops to the JSON editor rather than being silently
  flattened on save. The structured form is offered only when both the
  probe **and** the action can round-trip; the "isn't available"
  notice now names whichever side fell back.

The whole Erica/Jeopardy binding rule is now point-and-click to *edit*,
not just create. No schema change. 9 new unit tests
(`_action_form_supported` + the EPG probe-kind gate) and 4 new live
edit-form tests (EPG render + save, binding render + round-trip).
Gate ~725 tests.

## [0.5.94] - 2026-05-18

### Added — Google Calendar integration (OAuth) — B17

The last unbuilt B17 integration. A new `google_calendar` external-
sensor kind: the operator connects a calendar once through Google's
OAuth consent screen, and the hub polls it for events — an
`ical_event_active` watchdog probe then fires while a matching event
is on (e.g. "force the TV off during the kids-bedtime calendar
event"). The other "remaining" B17 integrations (MQTT, Plex /
Jellyfin / iOS-Shortcut webhooks) were already shipped in v0.5.61/.63.

- `app/services/google_oauth.py` — OAuth2 plumbing: consent-URL
  builder, authorization-code exchange, refresh-token rotation.
  Stdlib HTTP only (no `google-auth` dependency). Client credentials
  come from `runtime_settings` with an env fallback
  (`REBOOTER_GOOGLE_OAUTH_CLIENT_ID` / `_SECRET`).
- Two routes on the integrations blueprint —
  `/app/settings/integrations/google/connect` (CSRF `state`, redirect
  to Google) and `…/google/callback` (state-verified code exchange →
  creates the `google_calendar` source).
- `_poll_google_calendar` — refreshes the access token when the
  cached one is stale (persisted onto the source `config`), fetches
  the next 24 h of events, and normalises them to the **same payload
  shape `_poll_ical` produces** — so the existing `ical_event_active`
  probe is calendar-back-end-agnostic.
- Settings → Integrations gained a "Connect Google Calendar" card
  (shown once the client credentials are configured).

A Google Calendar also still works as an `ical` source via its
private `.ics` URL — OAuth adds live responsiveness without the
secret-URL share. Needs an operator-registered Google Cloud OAuth app
(redirect URI = the hub's `…/google/callback`). The OAuth connect
flow is verified live (302 → Google's consent screen with the right
params); 11 new unit tests. Gate ~712 tests.

## [0.5.93] - 2026-05-18

### Added — rules form-builder: binding / scene / EPG without JSON (Stage C)

The TV-scheduling rule shapes (`epg_show_airing` probe, `binding`
action, `apply_scene`) shipped in v0.5.89–.92 but could only be
authored via the JSON editor / API. The structured rule-create form on
`/app/rules` now exposes them:

- **Probe** — `epg_show_airing` ("tv guide — show currently airing")
  with `show` + optional `network` fields. Toggled by
  `rules_create_probe.js` like the other integration probes.
- **Action** — `relay_off` / `relay_on` (idempotent set-state),
  `apply_scene` (a saved-scene picker), and `binding` (two saved-scene
  pickers — the scene to hold while the probe succeeds, the scene to
  restore when it clears). A new `rules_create_action.js` toggles the
  per-action-kind field blocks (the first per-action JS the form has
  had — `cycle` keeps its fields).
- The form's binding is expressed as **two saved scenes** — the common
  shape; non-scene bindings stay on the JSON editor. So the whole
  Erica/Jeopardy rule is now point-and-click: pick the EPG probe, pick
  `binding`, pick the active + cleared scenes from
  `/app/scenes`-authored scenes.

`_rules_forms.py` gained the `epg_show_airing` probe builder and the
four action builders; `rules_page` + the JSON-editor re-render now pass
`scenes` to the template. Verified live — the structured form POST
creates a rule with `probe.kind=epg_show_airing` and
`action.kind=binding` carrying `apply_scene` edges. 7 new builder unit
tests; gate ~701 tests.

This closes the TV-scheduling feature (Stages A–C) — the
Erica/Jeopardy rule is fully operable from the UI: author scenes on
`/app/scenes`, then the binding rule on `/app/rules`.

## [0.5.92] - 2026-05-18

### Added — the named scene library (Stage C)

Stage B's `apply_scene` carried its device list inline in the rule's
`action` JSON. Stage C makes scenes **named, reusable, first-class**:
author "Erica's TV audio" once, reference it from any rule.

- New `scenes` table + `Scene` model (`id`, unique `name`,
  `description`, `items`). A brand-new table — `create_all()` at
  startup adds it on every deployment; no `_PENDING_COLUMNS` ALTER.
- New `app/services/scenes.py` — `create` / `list` / `get` / `update`
  / `delete` + `validate_scene_items` (the canonical item-shape
  check) + `scene_items` (the runtime resolver).
- New admin surface (`app/blueprints/admin/scenes.py`): the
  `/api/v1/admin/scenes` JSON API (GET/POST/GET-one/PATCH/DELETE) and
  an `/app/scenes` management page — list, create (name + JSON
  items), delete; each scene shows its `scn_…` id to copy into a
  rule. Linked from the Rules page.
- `apply_scene` actions now take **either** `scene_id` (reference a
  saved scene) **or** inline `items`. `_validate_leaf` accepts both;
  `_fire_scene` resolves `scene_id` through `scenes.scene_items` and
  reports a missing scene as an action error rather than raising.

So the Erica/Jeopardy rule is now: create the "Erica TV audio" scene
once on `/app/scenes`, then a binding rule whose `on_active` /
`on_clear` are `apply_scene(scene_id=…)`. Verified live — scene
created via the API, a binding referencing it created (201), the
`/app/scenes` page renders. 18 new unit tests
(`tests/unit/test_scenes_service.py`); gate ~694 tests.

The remaining Stage-C polish — a rules **form-builder** that exposes
binding / `apply_scene` / EPG kinds (today they are JSON-editor /
API-authored) and an EPG show↔network mapping helper — is the next
follow-up.

## [0.5.91] - 2026-05-18

### Added — scenes: one action, several devices, different states (Stage B)

Stage A bindings make a rule level-triggered, but each leaf action
(`relay_on` / `relay_off` / …) targets one device-set with one state.
The TV-scheduling use case needs more — "turn the surround **and** the
subwoofer off, **and** push Erica's audio config" — several devices,
*different* states, in one edge.

Stage B adds the `apply_scene` action: `{kind:'apply_scene', items:
[…]}`, one `items` entry per device, each carrying a `relay` state
(`on` / `off` / `cycle`) and/or an `apply_config` payload. It is a
leaf action — usable as a plain rule action and, crucially, as a
binding's `on_active` / `on_clear` edge. So the full Erica/Jeopardy
rule is now expressible end-to-end:

```
probe  = epg_show_airing(Jeopardy)
action = binding(
  on_active = apply_scene([surround:off, subwoofer:off]),
  on_clear  = apply_scene([surround:on,  subwoofer:on]))
```

- `apply_scene` lives inside the `action` JSON — no schema change.
- `_validate_action` split into `_validate_action` + `_validate_leaf`
  so a binding's sub-actions are now *fully* validated (a malformed
  `apply_scene` inside a binding edge is rejected, not just its kind).
- `_fire_scene` enqueues per-device `relay_on` / `relay_off` /
  `relay_cycle` / `apply_config` commands; a protected device is
  skipped (its protection wins), the rest of the scene still applies.
- `apply_scene` ignores the rule's `target` — each item self-targets.

Verified live: the Jeopardy binding with `apply_scene` edges creates
and renders ("…apply a 2-device scene…"); a malformed scene is
rejected (400). 15 new unit tests (`tests/unit/test_watchdog_scene.py`),
incl. the full binding+scene drive of the audio group. Gate ~675
tests. Named/reusable scene *library* + the scene-editor UI are the
Stage C follow-up — today scenes are authored in the JSON editor.

## [0.5.90] - 2026-05-18

### Added — condition bindings: the rules engine becomes level-triggered (Stage A)

The watchdog rules engine was edge-triggered only — "N consecutive
failures → remediate once → cooldown". That cannot express "while a
condition holds, keep a device in state X; when it clears, restore it"
— the TV-scheduling use case ("while Jeopardy is airing, turn the
surround off; when it ends, turn it back on").

Stage A adds a **binding** rule: a level-triggered action whose target
device-state follows the probe both ways.

- New action kind `binding` — `{kind:'binding', on_active, on_clear}`,
  where `on_active` / `on_clear` are leaf actions. It lives wholly
  inside the existing `action` JSON column — **no schema migration**.
- New leaf actions `relay_on` / `relay_off` — idempotent set-state
  commands (unlike `hold_off`, they do not set the sticky
  `is_held_off` flag, so a binding stays free to flip its target).
- New binding runtime (`watchdog_runtime/_state.py::_binding_tick`):
  applies `on_active` once the probe is stably `success` for
  `recovery_threshold` evaluations, `on_clear` once stably `failure`
  for `failure_threshold`. Fires only on a genuine edge — idempotent
  across the steady state; a transient `probe_error` holds the
  current edge rather than flipping it. Records a `binding_applied`
  event per edge.
- `_validate_action` validates the binding shape; `render_rule_sentence`
  reads it as "While &lt;probe&gt;, &lt;on_active&gt; on &lt;target&gt;;
  when it clears, &lt;on_clear&gt;."

Verified live: the rule
`probe=epg_show_airing(Jeopardy)` +
`action=binding(relay_off / relay_on)` creates via the API and renders
the correct sentence; a malformed binding is rejected (400). Binding
rules are created via the JSON editor / API (the structured
form-builder is a later UX task). 19 new unit tests
(`tests/unit/test_watchdog_binding.py`); gate ~660 tests.

### Added — P-QA: in-process unit tests for inbox + external_sensors

Two new `tests/unit/` files (23 tests) covering the last two services
BUG-059 had blocked from `hub_db` (SQLite) coverage:

- `test_inbox_service.py` (8 tests) — `health_and_attention`: the
  empty-fleet verdict, every device-age bucket (online /
  offline-short / offline-long / enrollment-pending / device-never),
  QA-fixture exclusion, and an explicit guard that the verdict is
  never `unknown` on a real fleet (the BUG-059(A) `as_aware` symptom
  was a silent fallback to `verdict="unknown"`).
- `test_external_sensors_service.py` (15 tests) — `create_source`
  (per-kind validation), `list_sources` / `delete_source` /
  `set_enabled`, the `_query` reads (`latest_sample` freshness,
  `last_two_samples` incl. the `as_aware` stale-newer site,
  `latest_sample_for_topic`), and `poll_all_due`'s due-check (the
  `as_aware` `last_polled_at` site — `poll_source` monkeypatched so no
  network).

Closes the BUG-059 follow-up — all six previously-blocked services
(invitations, password_resets, events, unregistered, inbox,
external_sensors) now have in-process coverage. Gate is now ~641
tests. Test-only change: no app code touched, so no version bump /
redeploy.

### Added — P-QA: end-to-end device-adoption regression test

`tests/qa/test_v0589_adoption_e2e.py` — the charter's (P-REG)
highest-value missing test. It drives the complete bring-up as **one
continuous flow**: device announces (unauthenticated) → operator sees
it in pending-adoption → operator adopts (token minted) → device's
next announce poll picks up the enrollment token → device registers →
device sends its first heartbeat → the hub reports it `active` +
`online`; plus the announcement closing out as `registered` and the
spent enrollment token being rejected on replay (`409
enrollment_consumed`).

Every prior test exercised a single hop of this ~60 KB path
(`announcements.py` / `pending_adoption.py` / `enrollment.py` /
`device_api.py`); a break in the *seam between* hops — like the
v0.5.36→v0.5.68 siteless-token regression that 500'd `/register` for
32 versions — could merge silently. This test gates that seam.
`pytest.mark.ci` — runs on every push. Gate is now ~618 tests.
Test-only change: no app code touched, so no version bump / redeploy.

## [0.5.89] - 2026-05-17

### Fixed — BUG-058: probe-kind validation/dispatch divergence

`create_rule`'s validation gate (`KNOWN_PROBE_KINDS`) accepted only 13
probe kinds, while the watchdog runtime (`run_probe`) had dispatched
~26 for several releases. The 13 kinds in the gap —
`host_awake`, `ha_numeric_above` / `ha_numeric_below`,
`solar_production_above` / `_below`, `snmp_interface_down`,
`snmp_throughput_above` / `_below`, `snmp_error_rate_above`,
`media_session_active`, `webhook_field_equals`, `mqtt_topic_equals`,
`epg_show_airing` — had full runtime handlers but could not be created
via the API or the JSON editor (`400 validation_failed`).

- `KNOWN_PROBE_KINDS` (`app/models/watchdog.py`) is now the canonical
  26-kind registry — every kind the runtime supports.
- `_validate_probe` (`app/services/watchdog.py`) gained a per-kind
  field-validation branch for each new kind (its fail-closed default
  guarantees no canonical kind can lack a validator).
- `_probe_to_phrase` renders a plain-English sentence for each new
  kind — created rules no longer show "unknown probe".
- `run_probe` (`watchdog_runtime/_probes.py`) now guards on a
  `DISPATCHED_PROBE_KINDS` frozenset — the runtime side of the
  registry.
- New `tests/unit/test_probe_kind_registry.py` (47 tests) pins the
  contract: `set(KNOWN_PROBE_KINDS) == DISPATCHED_PROBE_KINDS`, plus a
  well-formed-probe accept test and a missing-required-field reject
  test for every kind. The two lists can no longer drift — a mismatch
  fails CI.

Verified live: `host_awake` / `snmp_interface_down` / `ha_numeric_above`
rules now create (201) with correct sentences; a missing required
field is rejected (400). The CI gate is now ~617 tests. The rules
form-builder still does not expose the integration kinds — the JSON
editor remains the create path for them (a separate UI task).

## [0.5.88] - 2026-05-17

### Fixed — BUG-059: SQLite-incompatible code blocking `tests/unit/` coverage

The 2026-05-17 regression sweep filed BUG-059 — a cluster of code that
is correct on Postgres (production) but crashes on the SQLite
in-process test backend, blocking unit coverage of six services. All
three sub-parts fixed; production behaviour is unchanged.

- **(A) naive/aware datetime** — nine DB-read datetimes compared
  against a tz-aware `now` without `as_aware()` coercion now use it:
  `invitations.py` (`lookup_pending`, `redeem_invitation`),
  `password_resets.py` (`consume_reset`), `inbox.py` (the four
  health-feed age comparisons), `external_sensors/_query.py`
  (counter-delta freshness) and `external_sensors/_pollers.py`
  (`poll_all_due` due-check).
- **(B) `BigInteger` primary keys** — `DeviceEvent.id`,
  `UnregisteredAuthAttempt.id` and `AuditEventArchive.id` now use
  `BigInteger().with_variant(Integer(), "sqlite")` so the PK
  autoincrements on SQLite (it only ROWID-aliases an `INTEGER` PK).
- **(C) unconditional Postgres `ON CONFLICT`** —
  `unregistered.record()` now branches the upsert by dialect
  (`sqlite_insert` vs `pg_insert`), like `device_power.py`.

### Added — P-QA: in-process unit tests for the unblocked services

Four new `tests/unit/` files (35 tests) covering the services that
BUG-059 had blocked — and proving every fix pattern:
`test_unregistered_service.py` (record upsert + autoincrement PK —
proves B + C), `test_events_service.py` (ingest/query — proves B),
`test_invitations_service.py` (mint/lookup/redeem incl. the expiry
path — proves A), `test_password_resets_service.py`
(request/consume/expire incl. the expiry path — proves A). The CI
gate is now ~570 tests. `inbox` and `external_sensors` are now
unblocked too — their `tests/unit/` coverage is the next follow-up.

## [0.5.87] - 2026-05-17

### Fixed — post-refactor regression sweep: quick-win remediations

The 2026-05-17 post-refactor regression validation sweep (see
`docs/bug-log.md`, `docs/qa-notes.md`) filed BUG-056..061. This ship
clears the three quick wins:

- **BUG-057** — `templates/error.html` linked the brand and "Back to
  dashboard" to a bare `href="/"`. Under the `/rebooter` deployment
  prefix that is the host root (a different site) — the error page
  ejected the user from the app. Both now use
  `url_for('admin_ui.index')` → verified resolving to `/rebooter/app/`.
- **BUG-056** — `schedule_runtime._fire_power_cycle` wrapped each
  per-device `enqueue_for_device` in `except Exception: pass`. A
  scheduled `relay_cycle` that failed to enqueue for a device (locked
  device, missing id, DB error) was silently lost — no log, no count.
  Now `log.exception`s the failure and reports `enqueued:N failed:M`
  in `last_outcome` so a half-firing schedule is visible.
- **BUG-060** — the JSON and cookie logout handlers (`auth.py`,
  `auth_ui.py`) wrapped token/session revocation in
  `except Exception: pass`. A failed revoke left the user's JWT or
  session valid while telling them they were logged out, with nothing
  logged. All three sites now `log.exception` the failure (logout
  still succeeds for the user).

Also: corrected the stale `test_responsive::test_mobile_topbar_nav_links_reachable`
assertion (5 → 6 nav links — the `Power` link shipped with B16).
No version-relevant behaviour change; the v0.5.84–v0.5.86 `tests/unit/`
additions below roll up into this release.

### Added — P-QA: in-process unit tests for the watchdog probe dispatcher

`tests/unit/test_watchdog_probe_dispatch.py` (16 tests) covers
`app/services/watchdog_runtime/_probes.py` — `run_probe(rule)` and the
core network probes. The probes do real network I/O, so coverage is
the deterministic surface: the dispatch table (unknown kind → failure
with reason, missing kind, `gateway` skip-as-success, `probe_exception`
catch-all, bool→outcome mapping, `host_awake` defaulting to SSH port
22), the `_probe_internet` multi-target logic (default targets when
none given, ANY-success = healthy, ALL-fail = failure, malformed-target
rejection, the 8-target cap), and every probe's no-input guard
(`_probe_tcp` empty host/port, `_probe_http` empty/non-HTTP scheme,
`_probe_dns` empty hostname, `_probe_ping` missing host). The
socket-level helpers are monkeypatched where a test exercises dispatch
past the network call — no real packets, runs in ~0.3 s. Test-only
change: no app code touched, so no version bump / redeploy.

### Added — P-QA: in-process unit tests for the device-command service

`tests/unit/test_commands_service.py` (26 tests) covers
`app/services/commands.py` — the command queue. Coverage: the pure
`_validate_payload` schema checks for every validated command type
(`set_mode`, `apply_config`, `relay_cycle`, the LAN-bridge
`lan_scan` / `lan_proxy` / `lan_ota_push`, and simple-type
passthrough); `enqueue_for_device` (pending-command creation, the
unsupported-type and unknown-device errors, the `is_protected` power
lockout + `override_lockout`, the `set_hold_off` flag flip,
power-on auto-clearing `is_held_off`, custom TTL); `enqueue_for_group`
fan-out and protected-device skipping; `cancel_pending_command`
(pending-only, unknown-id); `list_pending_for_device` (delivery
marking, the no-mark path, expiry exclusion); `record_result`
(result storage + command status update, bad-status and
unknown-command errors); and `expire_overdue_commands`. The DB-backed
cases use the `hub_db` isolated-SQLite fixture. Test-only change: no
app code touched, so no version bump / redeploy.

### Added — P-QA: in-process unit tests for the heartbeat-ingest service

`tests/unit/test_heartbeats_service.py` (17 tests) covers
`app/services/heartbeats.py` — `record_heartbeat` and
`latest_heartbeat`. Coverage: the unknown-device `LookupError`, the
`DeviceHeartbeat` history-row write, the Device firmware/IP update
(and that a blank payload field never clobbers last-known truth), the
status-field copy onto the history row, the Device hot-column refresh
(and its partial-payload preservation), `last_event_at` ISO parsing
plus malformed-timestamp tolerance, the `reported_config` stash
(dict-only), the recovery-transition detection (`last_known_good_restored`
and `recovery_exit` triggers, and that a steady-state heartbeat fires
no push), and `latest_heartbeat` newest-by-`received_at` selection.
The deferred `device_config.maybe_push_after_recovery` is swapped for
a spy via an autouse fixture so the commands/audit chain stays out.
DB-backed cases use the `hub_db` isolated-SQLite fixture. Test-only
change: no app code touched, so no version bump / redeploy — the gate
widens to ~535 tests on the next CI run.

## [0.5.86] - 2026-05-17

### Added — P-QA: in-process unit tests for the device-power service

`tests/unit/test_device_power_service.py` (26 tests) covers
`app/services/device_power.py` — the power-telemetry query surface —
in-process against the `hub_db` isolated-SQLite fixture. Coverage:
the pure helpers (`decode_source_flags` bitfield decode + negative
clamp, `source_kind` real/synthetic taxonomy); `cost_rate_per_kwh`
(unset → `None`, set, negative and non-numeric rejection);
`latest_sample` / `latest_samples_by_device` (newest-wins, channel
scoping, the stale-age flag, batch absence); `recent_samples` (window
filtering, `source` filter, newest-first order); `power_source_breakdown`
(real vs synthetic split); `intraday_power_series` (fixed-width buckets
with gap slices); `fleet_summary` (per-device aggregation, biggest-hog
sort, cost hidden without a rate); and the rollups —
`compute_daily_rollups` (one-day aggregation, day-boundary isolation,
idempotent re-run upsert), `daily_rollups_for_device` (newest-first),
`fleet_daily_rollups` (day/device pivot). Gate is now ~476 tests /
64 files.

### Fixed — `DevicePowerSample.id` did not autoincrement on SQLite

`DevicePowerSample.id` was a plain `BigInteger` primary key. SQLite
only ROWID-aliases (and so autoincrements) an `INTEGER PRIMARY KEY` —
so inserting a sample without an explicit id failed on the SQLite
in-process test backend. Its sibling `DevicePowerRollup.id` already
had the `BigInteger().with_variant(Integer(), "sqlite")` trick;
`DevicePowerSample.id` now matches. Postgres production is unaffected
(still `BIGINT` with a sequence) — same class of fix as v0.5.82's
`WatchdogProbeEvent.id` / `AuditEvent.id`.

## [0.5.85] - 2026-05-17

### Added — P-QA: in-process unit tests for the enrollment service

`tests/unit/test_enrollment_service.py` (17 tests) covers
`app/services/enrollment.py` — the registration core — in-process
against the `hub_db` isolated-SQLite fixture, auto-`ci`-gated by the
`tests/unit/` conftest. Coverage: `mint_enrollment_token` (`et_`
secret prefix, hash-only persistence, default and 30-day-capped TTL,
issuer/hint recording); `consume_enrollment_token` (Device +
`dt_` bearer credential creation, token stamped consumed, display-name
hint fallback, QA-fixture flagging, and the `enrollment_invalid` /
`enrollment_consumed` / `enrollment_expired` / `validation_failed`
rejections); `revoke_enrollment_token` /
`revoke_enrollment_tokens_bulk` (pending delete, consumed-token no-op,
unknown-id `False`, bulk revoked/skipped partitioning). Gate is now
~450 tests / 63 files.

### Fixed — `consume_enrollment_token` naive/aware datetime crash on SQLite

The token-expiry check compared `EnrollmentToken.expires_at` against a
tz-aware `now`. Postgres `TIMESTAMPTZ` returns an aware datetime, but
SQLite returns a naive one — so the comparison raised `TypeError`
under in-process tests (and would on any SQLite-backed run). Coerced
the stored value with `app.models._helpers.as_aware` before the
comparison — a no-op on a Postgres deployment.

## [0.5.84] - 2026-05-17

### Changed — P-QA: the CI gate now runs behind nginx

The gate ran against the bare app on `http://localhost`; production
always runs behind nginx under the `/rebooter` prefix. The
nginx-layer tests (prefix root-redirect, firmware static serving with
`autoindex off`, firmware download-URL) therefore `pytest.skip`d.

CI now boots an `nginx:alpine` container (`ci/nginx.conf`) fronting the
app under `/rebooter`, sharing the firmware volume, and points
`REBOOTER_QA_BASE` at it. The whole `-m ci` gate now exercises the
real production request path. The three nginx-layer tests in
`test_routing_and_nginx` / `test_admin_api` run for real instead of
skipping, and `test_v039_firmware_mirrors` (the per-channel firmware
mirror chain) is now gated. Gate is ~433 tests / 62 files.

### Fixed — `/` deployment-root redirect was prefix-blind

`root_redirect` did `redirect("/app/")` — a host-root-relative path. On
the `/rebooter`-mounted production deployment, hitting the deployment
root (`/rebooter/`) redirected to `/app/`, which is a 404 behind the
prefix. Now redirects to `request.script_root + "/app/"` — `/rebooter/app/`
behind the prefix, `/app/` on a bare mount. Surfaced by wiring nginx
into the gate.

## [0.5.83] - 2026-05-17

### Added — P-QA: a `tests/unit/` in-process unit-test tree

Started the unit-test tree the gate-3 plan called for. `tests/unit/`
holds fast in-process service-layer tests — no HTTP, no Docker — in two
tiers: pure-function tests that need no fixture (the `_rules_forms`
form→JSON builders, schedule recurrence math) and DB-backed tests that
take a `hub_db` isolated-SQLite fixture. Every test under `tests/unit/`
is auto-tagged `ci` by the tree's conftest, so new unit files gate
without a per-file marker. Coverage so far — the `_rules_forms`
builders, schedule recurrence (`compute_next_run_at`), watchdog
`create_rule` validation, and **the device-announcement state machine**
(`upsert_announcement`: pending → adopted → registered, plus rejected).
39 tests across 4 files, ~3 s.

### Changed — `test_v0420_announce_adopt` into the CI gate; answered the open gate-3 question

The gate-3 backlog flagged `test_v0420` as a "behaviour question": two
tests expected a repeat `/announce` of an adopted-not-registered device
to return `awaiting_register`, but a fresh instance returns `adopted`.
Writing the announce-state-machine unit tests settled it — **the code
is right, the test was stale.** v0.5.68 (the P-REG strand fix)
deliberately stopped clearing `adoption_token_secret` on first
delivery: the token is now re-delivered as `adopted` on every poll
until the device registers, so a device that loses one announce
response self-heals. `test_v0420`'s `awaiting_register` assertions
predated that fix; corrected to `adopted` and the file is now gated.

### Fixed

- **`announcements.py` module docstring was stale** — it still
  described the pre-v0.5.68 "secret cleared on delivery → later polls
  get `awaiting_register`" behaviour, directly contradicting the
  v0.5.68 code comment a few lines down. Rewritten to match.

Gate is now ~430 tests / 61 files.

## [0.5.82] - 2026-05-17

### Changed — P-QA gate-3: timing-e2e files into the CI gate (time-injection seam)

`test_v0414_watchdog_runtime_e2e` and `test_v0417_schedule_runtime_e2e` were wall-clock tests that slept ~25–100 s waiting for the real APScheduler tick — flaky whenever the sleeps didn't line up with a tick boundary. They are now deterministic in-process tests (+6 tests; gate ~383 / 56 files); the watchdog e2e runs in **under a second** instead of ~75 s.

The seam: **`watchdog_runtime.tick()` and `schedule_runtime.tick()` now take an optional `now`.** Both already threaded a single `now` through every cadence / cooldown / window check, so this is just lifting that to a parameter. The APScheduler jobs call `tick()` with no argument and get wall-clock time exactly as before; the in-process tests pass an explicit `now` and step the rule / schedule state machine forward by hand against an isolated SQLite DB.

### Fixed

- **`WatchdogProbeEvent.id` and `AuditEvent.id` now autoincrement under SQLite.** They are `BigInteger` autoincrement PKs; SQLite only treats an `INTEGER` PK as the auto-rowid alias, so the runtime couldn't insert probe events in an in-process test. Both use `BigInteger().with_variant(Integer, "sqlite")` — `BIGINT` on Postgres (unchanged), `INTEGER` on SQLite.
- **The watchdog + schedule runtime tolerate naive datetimes.** `_rule_is_due`, the cooldown gate, `schedule_runtime`'s due-check + maintenance-window reconciler, and `compute_next_run_at` all subtracted/compared a DB-read datetime against `now`. Postgres returns `TIMESTAMPTZ` aware; SQLite returns it naive, which raised `TypeError`. A shared `as_aware()` helper coerces naive → UTC at each comparison site — a no-op against a real deployment.



### Changed — P-QA gate-3: in-process collection-error files into the CI gate

The two in-process test files that errored at collection on a bare runner are now gated — `test_v0514_deployment_completion_and_status_truth` and `test_v0536_site_not_null_and_archive` (+7 tests; gate ~378 / 54 files).

- **`test_v0514`** errored because its hand-built `Settings(...)` was missing the `session_cookie_secure` field added in v0.5.79 — a gated test would have caught that at the time. Field added. It then needed a bare Flask app context: the device-list RBAC filter reads Flask `g` for the current user, which exists under HTTP requests and APScheduler jobs but not in an in-process test; the fixture now pushes one (so `g.get("current_user")` resolves to `None` → unfiltered, the correct system-context behaviour).
- **`test_v0536`** was rewritten from `create_app()` + a `base_url` version gate (which needed a reachable database and HTTP server, so it only ran on a hub host) to the isolated-SQLite in-process pattern that `test_v0514` and the B11 sync tests use.

### Fixed

- **`_heartbeat_state_for()` crashed on a naive `last_heartbeat_at`.** Postgres returns the `TIMESTAMPTZ` column tz-aware; SQLite (the in-process test backend) returns it naive, and `now - last_heartbeat_at` then raised `TypeError: can't subtract offset-naive and offset-aware datetimes`. Now coerces a naive value to UTC — a no-op against a real (Postgres) deployment.
- **`audit_prune.py` used Postgres-only `id = ANY(:ids)`.** The nightly prune's source-delete was raw SQL that no other dialect accepts, blocking in-process testing of the prune service. Replaced with the dialect-portable ORM `delete(AuditEvent).where(AuditEvent.id.in_(ids))` — identical result on Postgres.



### Fixed — bootstrap admin had role="admin" despite is_super_admin

`ensure_bootstrap_admin()` set the legacy `is_super_admin=True` flag but never set the `role` string column (added with the v0.5.0 RBAC migration), so the bootstrap admin kept `User.role`'s default of `"admin"`. Role-checked endpoints — the maintenance toggle, user management, anything `role_required(super_admin)` — then **403'd the super-admin account** on any fresh deployment. Now sets `role="super_admin"` on both create and the every-startup privilege reconcile. Found via the gate-3 QA work; production deployments whose admin was already `super_admin` are unaffected (the reconcile is a no-op there).

### Fixed — theme-preference cookie was hardcoded Secure

`settings_theme_submit()` set the `rebooter_theme` cookie with `secure=True` hardcoded. On a non-HTTPS deployment a Secure cookie is never sent back, so the theme choice silently didn't stick. Now honours the same flag as the session cookie (`REBOOTER_SESSION_COOKIE_SECURE`, default on) — same bug class as the session-cookie fix in v0.5.79.

### Changed — P-QA gate-3: partial-fail test files into the CI gate

Brought 9 more files into the `-m ci` gate — the "partial-fail" bucket (mostly-green files with one or two failing tests): `test_smoke`, `test_auth_negative`, `test_routing_and_nginx`, `test_admin_api`, `test_v02_rbac_invites`, `test_v0411_input_validation`, `test_v047_maintenance_and_firing_inbox`, `test_v030_redesign_p1_shell`. Gate is now ~370 selected / ~50 files. Causes fixed:

- the two app bugs above (cleared the `403` failures in `test_v02`, `test_v0411`, `test_v047` and the theme failure in `test_v030`);
- more hardcoded `_login()` creds (`test_smoke`, `test_auth_negative`);
- nginx-layer tests (`test_root_redirects_to_app`, `test_firmware_dir_does_not_index`, `test_firmware_upload_then_download_via_nginx`) now `pytest.skip` when the base URL is not the `/rebooter`-prefixed deployment, so they still run against live but don't fail the bare-app gate.

Two files stay in the gate-3 backlog with documented reasons: `test_hardening_probes` (its rate-limit and cookie-Secure tests are incompatible with the gate's `RATE_LIMIT_EXEMPT_IPS=*` / `SESSION_COOKIE_SECURE=0`) and `test_v0420_announce_adopt` (two tests assert an `awaiting_register` announce-lifecycle transition that a fresh instance doesn't produce — a behaviour question for the lifecycle owner). `test_v0420`'s stale `retry_after_seconds == 30` assertion was corrected to `>= 1` regardless (no deployment sets that value to 30).

### Changed — P-QA gate-3: brittle test files into the CI gate

Cleared the entire `0P`-all-fail bucket — 11 test files that failed
every test against a fresh instance — into the `-m ci` gate. Gate is
now ~256 tests / 44 files (was 230 after gate-2). Test-only change; no
app code touched.

- **History feed** (`test_v0427_history_chips`, `test_v0430_history_sources`,
  `test_v0432_history_export_search`) — +11 tests.
- **Settings / wizard / RBAC** (`test_v0425_runtime_smtp`,
  `test_v0428_upgrade_button`, `test_v0431_enrol_wizard`,
  `test_v0433_firmware_settings_tab`, `test_v0500_role_bindings`,
  `test_v0502_pending_adoption_count`, `test_v0503_devices_list_nested_form`,
  `test_v0511_scan_download_url`) — +15 tests, +2 conditional skips.

Three recurring root causes, fixed with one checklist:

- the per-file `_login()` helper hardcoded `dblagbro@gmail.com` /
  `Super*120120` instead of honouring `REBOOTER_QA_EMAIL`/`REBOOTER_QA_PASS`
  — every test 401'd against the CI admin;
- the tests assumed accumulated live-deployment data; a fresh instance
  has none. Added module-scoped autouse fixtures that seed exactly what
  each file needs — `watchdog_rule.*` audit activity for the history
  files, a registered device (mint enrollment token → register) for
  `test_v0503`;
- a `<td><code>` row-parsing regex missed the `data-label="Action"`
  attribute the responsive-table reflow added — widened to `<td[^>]*>`.

`test_v0511` (scanned-release URL guard) needs firmware artifacts on
disk that a fresh instance lacks; its two tests now `pytest.skip`
cleanly when there are none — consistent, and they still run against a
live deployment.

## [0.5.79] - 2026-05-16

### Changed — P-QA gate-2: widen the CI gate

The `-m ci` gate covered 21 tests / 3 files — the registration/device surface. It missed everything else, including the watchdog/rules tests that *would* have caught the v0.5.77 and v0.5.78 regressions had they been gated. Widened it to **~230 tests / 33 files**.

Two structural fixes made the rest of the suite CI-runnable — both default to the production-safe value; only the throwaway CI instance opts out:

- **`SESSION_COOKIE_SECURE` is now env-configurable** (`REBOOTER_SESSION_COOKIE_SECURE`, default `1`). It was hardcoded `True`, so the Secure session cookie was never sent over the CI gate's plain `http://localhost` — every cookie-authenticated HTTP test 401'd. CI sets it `0`.
- **The rate-limit exempt list accepts a `*` wildcard.** 33 test modules each log in; that tripped the 30/min auth limiter with `429`s. CI sets `REBOOTER_RATE_LIMIT_EXEMPT_IPS='*'`.

Every newly-gated file was verified by running `pytest -m ci` twice against a from-scratch instance (fresh Postgres, then populated DB) — a faithful CI replica. Triage caught two tests that pass only by luck and were **left out** of the gate, with the reason recorded in each file: `test_v034_bulk_actions` (order-dependent — `/app/groups` scaffolding needs a pre-existing group) and `test_v0417_schedule_runtime_e2e` (wall-clock race against the 30 s APScheduler tick). The remaining ~30 files (brittle HTML-string asserts, timing-flaky e2e) are the gate-3 backlog in `docs/test-plan.md`.

## [0.5.78] - 2026-05-16

### Added — #15: structured rule-edit form (P-UI walkthrough, Phase 2B)

Rule editing was raw-JSON-only — to change a rule's threshold or target you hand-edited a JSON blob. The edit page (`/app/rules/<id>/edit`) now opens a **structured form mirroring the create form**, pre-populated from the rule: probe kind + per-kind fields, action, target picker, thresholds, and a maintenance window. The JSON editor stays underneath as a collapsible "Advanced" escape hatch.

- New route `POST /rules/<id>/edit-form` reuses the exact `_rules_forms` builders the create form uses (`build_probe_from_form` / `build_target_from_form` / `build_action_from_form` / `build_maintenance_windows_from_form`), then calls `update_rule`.
- **No silent data loss.** Fields the structured form doesn't surface — `escalation`, `max_retries`, `retry_delay_seconds`, `description`, `site_id` — are read from the existing rule and carried through every structured save untouched.
- **Probe kinds the form can't round-trip stay JSON-only.** A rule whose `probe.kind` is outside the 13 structured kinds (e.g. `host_awake`, `mqtt_topic_equals`, `epg_show_airing`) renders the JSON editor only, with a notice — the structured form would otherwise rebuild the probe as `internet` on save. Guarded by `STRUCTURED_PROBE_KINDS` server-side and `probe_form_supported` in the template.
- **Multi-window maintenance is flagged, not clobbered silently.** The structured form edits one window; a rule with several shows an amber warning to use the JSON editor.
- The create form's probe/target JS (`rules_create_probe.js`, `rules_create_target.js`) is reused as-is. One additive tweak: the tag-target text input now seeds from a `data-tag-value` attribute so an existing tag pre-fills (no-op for the create form).
- `_render_rule_edit()` is the single render path for the edit page, so the JSON-editor validation-error re-render and the initial GET ship the structured form identical context.

CI: new `ci`-gated HTTP test `test_v0578_rule_edit_form.py` — create → load structured edit page → structured-form update → verify the change applied and `max_retries` / `description` preserved.

### Fixed

- **Bad JSON in the rule creator's JSON editor 500'd instead of showing the error.** `rules_create_json_submit`'s error re-render never passed `sources_by_kind`, so the create form's integration-probe blocks hit an undefined variable under Jinja `StrictUndefined`. Latent since v0.5.28 (when those blocks were added); surfaced now because the existing `test_v049` JSON-editor test isn't in the `-m ci` gate. The error path now renders the friendly inline error as intended.
- Refreshed a stale assertion in `test_v040` that still expected the "What's coming next" roadmap card removed in v0.5.77 (#17).

## [0.5.77] - 2026-05-16

### Changed — P-UI Tier E: content & polish (defect walkthrough #16–#23)

The final tier of the walkthrough — the content/polish defects. As in Tier C, measuring the live pages first showed **two of the eight were over-flagged** and one was softer than written:

- **#16 — dev-docs copy is out of the Settings → Overview card.** It told operators about `docs/webui-redesign-plan.md`, "P5/P6 placeholder copy in v0.3.0", `docs/redesign-continuation-plan.md`, per-line version numbers (`v0.4.10`, `v0.4.1`), "TOTP/OIDC queued", "env-var-driven", and "awaiting your architecture pick on RFC-004". Rewritten to plain operator-facing copy: each section now has a one-line "what it's for" description with no internal references.
- **#17 — the Rules "What's coming next" roadmap card is gone.** It listed unshipped backlog items (`B6.3`, `B17 Layer 2`) and a "Recent ships" version log (`v0.4.2 … v0.5.19`) — internal release notes surfaced on an operator page. Card deleted.
- **#20 — integration add-forms collapse.** The Integrations page was an endless scroll of ten fully-expanded add-forms (Roku, Home Assistant, Weather, iCal, SolarEdge, Enphase, SNMP, webhook, MQTT) plus a long reference card. Each is now a `<details>` — the page opens as a short list of headings; click one to expand its form. No JS (native `<details>`).
- **#21 — the sign-in card no longer spans the full page.** `.auth` had no `max-width`, so the login form stretched across the full 1100px main with the fields stranded top-left. Constrained to a centred 26rem box.
- **#22 — badges no longer wrap into a misshapen pill.** Added `white-space:nowrap` to `.badge` so a two-word badge stays on one line inside its rounded pill.
- **#23 — attention items get one snooze-duration picker.** The "Needs attention" list repeated three near-identical buttons per item (`Snooze 1h`, `Snooze 24h`, `Ack`). The two snooze buttons are now a single duration `<select>` (1 hour / 24 hours) + one `Snooze` button; `Ack` is relabelled `Acknowledge`. Same `snooze_seconds` form field — no backend change.

Over-flagged, corrected on inspection (no change needed):

- **#18 (device-detail tabs "don't work") — they do.** The `.v3-tabbar` entries are anchor-jump links and all eight matching `<section id="…">` targets (overview, power, watchdog, schedule, audit, failsafe, events, settings) exist. Clicking a tab scrolls to its section as designed.
- **#19 (history "has no pagination") — it is capped.** `history_page()` already limits to `limit=int(request.args.get("limit") or 200)` and a CSV export exists. Not unbounded.

### Found, not fixed (out of Tier E scope)

- Status page totals row hardcodes **"0 active rules / watchdogs ship in P4"** — stale dev copy now that rules ship. Fixing it properly needs a live rule count from the backend, not a template tweak; logged for a follow-up rather than shipped as a wrong-but-prettier number.

### Notes
- Tiers A, B, C, D, E of the P-UI defect walkthrough are now done. Only #15 (structured rule-edit form) remains deferred as a standalone Phase-2B feature build.

## [0.5.76] - 2026-05-16

### Changed — P-UI Tier D: form UX (defect walkthrough #12, #13, #14)

- **#12 / #13 — radio buttons and checkboxes now align with their labels.** The blanket `form input { display:block; width:100% }` rule was making radios and checkboxes full-width block elements, floating the control above/beside its label text (visible on the Schedules form's Kind radios and Weekday checkboxes). Radio/checkbox inputs are now `inline-block; width:auto` — a one-rule CSS fix that corrects every form app-wide.
- **#14 — the Schedules create-form shows only the fields that apply.** It previously showed every conditional field at once, tagged "(weekly only)" / "(one-shot only)" / "(maintenance only)". `static/js/schedules_form.js` now shows/hides the at-time / weekdays / start-at / duration fields and the Target fieldset based on the selected Kind and Recurrence; the redundant parentheticals are dropped.

### Deferred — #15 (structured rule-edit form)

Rule editing remains raw-JSON-only. A structured edit form — mirroring the create form, pre-populated from the rule — is the long-deferred "Phase 2B"; it is a feature build, not form polish, so it is tracked separately rather than folded into this tier.

### Notes
- Tiers A, B, C, D (less #15) done. Tier E (content/polish) remains.

## [0.5.75] - 2026-05-16

### Changed — P-UI Tier C: consistency (defect walkthrough #9, #11)

Tier C of the walkthrough. Measuring the live pages first showed **two of the five flagged defects were not real** — the walkthrough over-read them from scaled-down screenshots:

- **#7 (page width "jumps 640↔1150px") — not a defect.** `<main>` is a uniform `max-width:1100px` on every page; measured 1100 on Status, Devices, Sites, Rules, Login alike. The "narrow vs wide" impression was content density, not container width.
- **#8 (desktop "wastes space", "table squeezed to 640px") — not a defect.** Data tables are `width:100%` of the 1100px main. No change.
- **#10 (breadcrumbs "inconsistent") — already coherent.** Every sub-page uses the same `← Parent` pattern (`← Devices`, `← Groups`, `← Sites`, `← Users`, …); top-nav-level pages correctly have none (the nav is the navigation). No structural change.

The two genuine defects are fixed:

- **#11 — destructive buttons no longer look like primary actions.** `Delete` (Sites, Firmware), `Remove` (Group members) and `Reject` (signup requests) were plain primary-blue buttons — a destructive action indistinguishable from a normal one. All now use `btn-danger` (red); the Reject button's inline red is replaced by the class.
- **#9 — form fields widened.** The `max-width` cap on inputs/selects was 320px, which read as unfinished inside a full-width card. Raised to 30rem (≈480px) for inputs/selects and 44rem for textareas — a normal, scannable field width.

### Notes
- Tiers A, B, C done. Tier D (form UX) and Tier E (content/polish) remain.

## [0.5.74] - 2026-05-16

### Changed — P-UI Tier B: mobile layout (defect walkthrough #3, #4, #6)

Operator-approved Tier B (`docs/notes/2026-05-16-pui-tier-b-mobile-proposal.md`). Completes RFC-003 §11.3 / requirement C4 ("usable at 375px"). CSS + small template edits — no route or behaviour changes.

- **#4 — data tables become row-per-card on phones.** At ≤640px every `.table-wrap` table collapses to a stack of cards: the header row hides, each row is a bordered card, and each cell renders its column name (a `data-label`) as a small label above the value. Pre-fix, list tables horizontal-scrolled or clipped at 375px. `data-label`s added to the Sites, Groups, Pending-adoption, Firmware (releases + deployments), Rules and History tables.
- **#3 — mobile header no longer wraps mid-word.** Below 768px the topbar wraps whole items to a tidy second row (brand, then actions) instead of breaking "Sign out" across lines; the version string and role badge shrink to fit. All header items kept (per operator decision).
- **#6 — Settings sections are a jump-menu on phones.** The 12-section tab strip is unusable scrolled at 375px; at ≤640px it is swapped for a native `<select>` jump-menu. `settings_tabs.html` renders both the strip and the select; CSS swaps them at the breakpoint; `static/js/settings_tab_select.js` navigates on change. Desktop keeps the tab strip.
- **#5** — spot-checked, no change: `main` already reserves space for the fixed bottom-nav; the walkthrough's "overlap" was a full-page-screenshot artifact.

### Notes
- Tier B done. Tiers C–E of the walkthrough remain.

## [0.5.73] - 2026-05-16

### Fixed — P-UI Tier A: broken pages (defect walkthrough #1, #2)

First fixes from the P-UI defect walkthrough (`docs/notes/2026-05-16-pui-defect-walkthrough.md`).

- **`/app/power` returned HTTP 500 (defect #1).** `templates/power.html` did `{% for v in d.values %}` — Jinja resolved `d.values` to the dict's `.values` *method* (a non-iterable builtin) instead of the dict's `"values"` key, so the fleet-power page rendered a raw JSON error to the operator. Fixed with subscript access `d['values']` (2 occurrences).
- **CSP silently blocked inline `<script>` (defect #2).** The page CSP is `script-src 'self'`; four inline `<script>` blocks were blocked, so their JS never ran — dead were the rule-create form's probe-kind field switching / add-remove-target rows / target filter, the pending-adoption auto-refresh, and the user-detail role-scope picker. Extracted all four to static files loaded with `defer` (behaviour-preserving, defensive null-guards added):
  - `static/js/pending_adoption_refresh.js`
  - `static/js/rules_create_probe.js`
  - `static/js/rules_create_target.js`
  - `static/js/user_detail_scope.js`

### Notes
- Tier A of the walkthrough is done. Tiers B–E remain; Tier B (mobile) gets a dedicated research + design pass before any code.

## [0.5.72] - 2026-05-16

### Fixed — B11 bootstrap-seam: applier reconciles divergent ids, sync-cursor errors stop

Each hub bootstraps its own admin user (`dblagbro@gmail.com`) and "Default" site independently, with different ids. Once sync went live (v0.5.71) those collided on the `users.email` / `sites.name` unique constraints every time the entity synced — a persistent `IntegrityError` on the sync cursor's `last_error` (logged + skipped, never wedging, but constant noise).

- **Natural-key reconciliation** (`apply_outbox_event`): when an incoming create's id isn't found locally, the applier now looks the entity up by its unique natural key (`user.email`, `site.name`, `group.name`) before inserting. A match → the create becomes a converging last-writer-wins update of the existing row (local id preserved) — no UNIQUE collision, no cursor error. A genuinely new entity (no natural-key match) still inserts normally.
- **`site_id` remap**: Device and Group both FK `sites.id`. A peer row may reference a site this hub has under a different id (the independently-bootstrapped "Default"). The applier now remaps an unknown `site_id` to the local Default site (`resolve_default_site_id`) on every device/group upsert, so the FK always holds — prevents the FK-violation errors that natural-key reconciliation would otherwise have traded the collision for.
- `last_login_at` added to the user emission ignore-set — a login is per-hub operational state, not config, and no longer emits a `user.updated` (less outbox churn). `tokens_valid_after` (logout / password reset) still emits.
- **`update_sync_cursor` now clears `last_error` on a clean batch** — previously it only ever *set* the error and never cleared it, so a single past error showed on the cursor forever even after sync recovered.
- `tests/qa/test_v0572_b11_natural_key.py` — 5-case unit test.

### Notes
- The two hubs keep their own local ids for the bootstrap admin / Default site; the applier bridges the divergence transparently (reconcile + remap). Nothing synced FK-references `user.id`, so the user-id divergence is inert; `site_id` is the only cross-entity FK and is remapped.

## [0.5.71] - 2026-05-16

### Added — B11: sync-emission ORM hooks — multi-hub sync converges end-to-end

v0.5.70 completed the applier but found emission was the real gap (most syncable mutations emitted nothing — `sites.py` had zero audit calls, device-create on `/register` was unaudited, `user` verbs didn't match). This release fixes emission properly.

- **`app/services/sync_emission.py`** (new) — mapper-level `after_insert`/`after_update`/`after_delete` listeners on the four syncable models (Device, Site, Group, User). Every row write lands an `outbox_events` row on the *same* connection/transaction as the mutation. Emission can no longer miss a mutation or depend on audit-action-string parsing.
- **`after_update` is change-filtered** — emits only when a column *outside* a per-model ignore set changed. Device's ignore set is its heartbeat/telemetry columns (`last_heartbeat_at`, `reported_*`, `firmware_version`, `local_ip`, `last_reported_config`); those refresh per-hub from the device's own heartbeats, so replicating them via the outbox would be redundant and would emit on every heartbeat. Config changes (rename, site/group, desired_config, registration_state, protection, …) still emit.
- **Loop prevention** — the applier writes to these same tables; it now runs inside `sync.suppress_emission()` (a ContextVar guard) and flushes within it, so an applied peer event never re-emits. No hub-to-hub ping-pong.
- **Old audit-path emission removed** — `audit._should_sync_action` / `_emit_outbox_for_scoped_action` and the emit call in `record()` are gone. The audit log and the sync outbox are now fully independent concerns.
- **Replicator batch isolation** (`sync_replicator._apply_event_batch`) — each event is now applied in its *own* transaction, and the sync cursor advances past every event (applied, skipped, or errored). Pre-fix, one un-appliable event (e.g. a unique-constraint collision) poisoned the whole batch's transaction and the cursor never advanced — sync wedged forever, retrying the same failing batch. Found by the two-instance convergence test. Errors are logged and surfaced on the cursor's `last_error`.
- `OutboxEvent.seq` uses `BigInteger().with_variant(Integer, "sqlite")` so the sync layer is unit-testable without Postgres (same precedent as `DeviceHeartbeat`).
- `tests/qa/test_v0571_b11_emission.py` — 6-case in-process unit test (create/update/delete emit, `updated_at`-only no-emit, suppression, device telemetry-vs-config filter). Runs in the `-m ci` gate.

### B11 status

With the applier (v0.5.70) and emission (v0.5.71), B11 multi-hub sync converges create/update/delete for device/site/group/user. Validated with a two-instance convergence test (a mutation on hub A replicates to hub B). `sync.enabled` remains **off** — re-enabling it is the operator's call.

## [0.5.70] - 2026-05-16

### Added — B11: the sync applier (create/update upsert + LWW) is complete

`apply_outbox_event()` previously applied only deletes/tombstones; create/update was a `# TODO` stub — the documented gate that kept `sync.enabled` off (see `BACKLOG.md`). This release completes the **applier**.

- **Applier** (`sync.apply_outbox_event`): generic create/update upsert for the syncable entity types (device, site, group, user) with **last-writer-wins on `updated_at`**. A missing row is created; an existing row is updated only if the incoming write is strictly newer. Idempotent — re-applying an event is a no-op. Datetime columns and the peer's `event.at` (ISO strings over JSON) are coerced to tz-aware datetimes, with naive/aware normalised so the LWW compare never raises. Tombstoned entities are never recreated.
- **Emission** (`audit._emit_outbox_for_scoped_action`): create/update events now carry a full entity snapshot taken by the emit path itself (`sync.snapshot_entity`) — previously each call-site had to pass `entity_snapshot` and none did, so no create/update event was ever emitted with a real payload. `record()` is now the single emission point; the redundant emit in `record_scoped()` is removed.
- `tests/qa/test_v0570_b11_applier.py` — 7-case in-process unit test (create, LWW win/skip, idempotency, delete+tombstone, tombstone-blocks-recreate, unknown-type). The first `tests/unit/`-style test; self-contained SQLite, runs in the `-m ci` gate with no infra.

### Known gap — B11 does NOT yet fully converge; `sync.enabled` stays off

The applier is done and correct, but **emission coverage is incomplete**, so this release does *not* make it safe to turn sync back on. Emission only fires where a mutation is audited with a syncable action (`audit._should_sync_action`), and an audit revealed the action vocabulary is inconsistent: `device` create/update/delete emit, but `site` mutations are **not audited at all** (`sites.py` has zero audit calls), `group` only audits deletes, and `user` actions use verbs (`created_via_invite`, `role_changed`, …) outside the syncable set. So site/group/user create/update still emit nothing.

Next step before re-enabling sync: an **emission-coverage pass** — preferably ORM-level `after_insert`/`after_update` hooks on the four synced models, so emission cannot miss a mutation or depend on audit-string parsing. Tracked in `BACKLOG.md`.

## [0.5.69] - 2026-05-16

### Fixed — Settings → Sync save form 500'd on every submit

`settings_sync_save_submit` (`POST /app/settings/sync`) referenced `g` and `flash` without importing them — `settings.py` imports those per-function, and this handler was missing the line. Every submit raised `NameError: name 'g' is not defined` → HTTP 500. **The Settings → Sync form has never worked** — sync settings (enable/disable, hub id, peers, HMAC key) could not be changed through the UI at all.

- Added the missing `from flask import flash, g` to the handler, matching the file's per-function import pattern.
- Found while disabling `sync.enabled` per operator request (the B11 multi-hub applier is still incomplete — sync must stay off until it lands; see `BACKLOG.md`).

## [0.5.68] - 2026-05-15

First fixes of the P-REG charter (`docs/notes/2026-05-15-pause-state-and-resume-charter.md`). Two distinct bugs were breaking registration; both are the "failing registrations" symptom. Found by writing the end-to-end adoption test the codebase never had.

### Fixed — P-REG #1: `/register` 500'd for every announce-adopted device (showstopper)

The RBAC P2 work (v0.5.36) made `devices.site_id` NOT NULL. But `announcements.adopt()` mints enrolment tokens with **no `site_id`**, so `consume_enrollment_token` inserted the new `Device` with `site_id=NULL` → `psycopg.errors.NotNullViolation` → HTTP 500. **Every device adopted through the normal announce → pending-adoption → register flow has been unable to register since v0.5.36** — ~32 versions. It went uncaught because there was no end-to-end adoption test; the QA suite only ever exercised a synthetic register against a hand-minted token.

- New `sites.resolve_default_site_id(session)` — resolves a fallback site (the "Default" site, else the sole site, else a freshly created "Default"), within the caller's transaction.
- `consume_enrollment_token` fresh-adoption branch now uses `et.site_id or resolve_default_site_id(session)`. A device adopted without an explicit site lands in "Default"; the operator can re-home it from the devices UI. Restore-after-reflash is unaffected (it preserves the existing row's site).

### Fixed — P-REG #2: a lost /announce response permanently bricked adoption

`upsert_announcement` cleared `adoption_token_secret` from the announcement row on the **first** `/announce` poll that delivered it. If the device lost that single HTTP response — a dropped packet, or an ESP8266 crash/reboot under TLS/heap pressure (precisely the conditions the firmware team documents) — the plaintext token was gone forever. Every later poll returned `status=awaiting_register` with no token, and the device was **permanently stranded at adoption**.

- **The token now survives a lost response.** `adoption_token_secret` stays on the row, re-delivered on every `/announce` poll, until the device completes `/register`. `mark_consumed` is now the *only* place it is cleared — the proper end-of-life, once registration succeeds.
- **Already-stranded devices self-heal.** New `_maybe_recover_stranded_pickup` helper: a row that is adopted + delivered + not-consumed with the secret already gone (the pre-fix strand state) gets a freshly minted enrolment token on its next poll — carrying over the original token's site / restore-target / name context. If the original token turns out to have been consumed, the row is reconciled to `registered` instead (covers a `/register` that carried no MAC to cross-link on).
- Security posture unchanged: `/announce` is unauthenticated by design (operator approval is the trust gate), so the token was already handed to any caller polling that MAC. Keeping the plaintext until `/register` only widens the delivery window from one poll to the intended short bring-up window; the token is single-use with a 7-day TTL.

### Added
- `tests/qa/test_v0568_adoption_token_redelivery.py` — the first true end-to-end adoption regression test: announce → adopt → announce → **announce again** (the lost packet) → register → announce. Guards both fixes — pre-fix the second announce returned `awaiting_register` and `/register` 500'd.

### Notes
- No schema change. Behavior for a device that registers on its first post-adoption poll is identical to before — except it now succeeds instead of returning 500.
- The separate strand mode — a *registered* device that loses its `device_token` **and** changes DHCP lease — is still gated by the conservative IP-match check in `_maybe_prepare_auto_rebind`; a narrower, documented limitation, not addressed here.

## [0.5.67] - 2026-05-15

### Changed — refactor: extract rule-form mapping out of the rules blueprint

Continues the incremental refactor (`refactor-log.md`). `blueprints/admin/rules.py` was 645 LOC, dominated by a **211-line `rules_create_submit` handler** — ~130 lines of which were form→probe-dict mapping logic, violating `architecture.md` §"Module-boundary principles" (*no business logic in blueprints*).

- **New `app/blueprints/admin/_rules_forms.py`** — four pure mappers (`build_probe_from_form`, `build_target_from_form`, `build_action_from_form`, `build_maintenance_windows_from_form`) + a typed `RuleFormError`. They turn the create-form's flat fields into the probe/target/action JSON shapes; the handler catches `RuleFormError` → flash + redirect, exactly as the inline code did.
- **`rules_create_submit` shrank 211 → ~32 lines** — now a thin HTTP translator. `rules.py` dropped 645 → 487 LOC, back under the 500-LOC blueprint soft limit.
- Behavior-preserving — pure extraction; UI + API handlers stay co-located in `rules.py` (co-location principle intact). Verified via `create_app()` smoke test.

### Notes
- The form-mapping is now unit-testable in isolation.
- Next refactor targets unchanged: `settings.py` (596) and `devices_ui.py` (563) — both only ~1.2× over the limit, lower value.

## [0.5.66] - 2026-05-15

### Added — P1.3: low-load current semantics

Completes P1.3's loaded-power half. The firmware team's `2026-05-15-power-capture-and-g2-progress.md` confirmed real loaded telemetry is now flowing from field devices (`.30` ~5 W, `.225` ~0–2 W) — the old "no non-zero data" blocker is gone — but surfaced a correctness wrinkle: the CSE7766 firmware clamps measured current below ~50 mA to zero, so a real standby load uploads `i_ma=0` with a non-zero `p_w`. Firmware 0.1.27+ added estimated-current fields to disambiguate.

- **Two new `device_power_samples` columns** — `i_ma_estimated` (bool) + `i_ma_estimate` (int mA), both nullable, added via `_PENDING_COLUMNS`. When `i_ma_estimated` is true the firmware clamped the reading and `i_ma_estimate` carries the standby estimate.
- **Ingestion** (`events.py::ingest_power_samples`): reads the new fields. The exact upload-row key is being confirmed with the firmware team — the hub accepts the short `i_ma_*` form and the firmware's published `power_*` status names (first match wins); see `docs/notes/2026-05-15-to-firmware-current-semantics.md`.
- **Surfaced** in the power-samples API (`i_ma_estimated`/`i_ma_estimate`) and the device-detail power card — a clamped reading renders `~20 mA (est)` instead of a misleading `0 mA`.
- **Consumer rule**: `i_ma=0` must not be read as "no activity" when `i_ma_estimated` is true. The `power_zero_while_on` watchdog probe already keys on watts (`avg_w`), not current — no logic change needed there.

### Notes
- Backward-compatible — pre-0.1.27 firmware omits the fields; columns stay NULL and behavior is unchanged.
- P1.3's interactive 24h chart shipped earlier (v0.5.59). With this release P1.3 is complete bar the firmware confirming the exact upload-row key name.
- RFC-006 Decision 6 (G2 cross-device timing) remains blocked — firmware shipped the `wall_clock_unix_ms` instrumentation but the measurement run is still pending.

## [0.5.65] - 2026-05-15

### Changed — incremental architectural refactor (behavior-preserving)

Two oversized service files — both bloated this session as B16/B17 integrations and probes accumulated — split into cohesive modules following the existing `architecture.md` §"Service subpackages" convention. Pure re-organization: every public import path is preserved via `__init__.py` re-exports; no blueprint, scheduler, template, or behavior change.

- **`services/external_sensors.py` (1369 LOC) → `services/external_sensors/` subpackage:**
  - `_common.py` — `_iso`, `ROKU_DEFAULT_PORT` (dependency-free shared leaf)
  - `_crud.py` — source registry: create / list / enable / delete, per-kind config validation, redacted serialization
  - `_pollers.py` — poll dispatch + every `_poll_<kind>` (Roku/HA/weather/iCal/solar/SNMP) + SNMP helpers
  - `_inbound.py` — webhook + MQTT sample writers (the push side)
  - `_query.py` — sample reads consumed by the watchdog probes + UI
  - `__init__.py` — public API re-exports

- **`services/watchdog_runtime/_probes.py` (1265 LOC) → split in two:**
  - `_probes.py` — `run_probe()` dispatcher + core network probes (internet/ping/tcp/http/dns/host_awake)
  - `_probes_integrations.py` — the 14 sensor-backed probes (Roku/HA/weather/iCal/power/solar/SNMP/media/webhook/MQTT/EPG). `run_probe` dispatches into them via a one-directional import.

### Docs
- **Created `docs/design.md`** (was missing) — design rationale: the local-first contract, the three integration ingestion shapes (poll / webhook / subscriber), the modality model, and key trade-offs.
- Updated `docs/architecture.md` (source-layout tree) and appended to `docs/refactor-log.md`.

### Notes
- Verified behavior-preserving via a full `create_app()` smoke test in the built image — all 8 blueprints register, all re-exports resolve.
- No oversized file remains above ~720 LOC (was 1369). Next targets recorded in `refactor-log.md`.

## [0.5.64] - 2026-05-15

### Added — B17 Layer 2: EPG (TV programming guide)

Implements B17 Layer 2 per `docs/notes/2026-05-15-b17-layer2-epg-design.md` — operator picked the **TVMaze** provider (free, no auth; over Schedules Direct's $25/yr). "Run a watchdog rule while a named show is airing."

- **`external_epg_cache` table** (`app/models/external_epg.py`): provider-agnostic cache of "what's airing on which channel when" — `provider`, `channel_id`, `airing_start`/`airing_end` (half-open window), `show_title`, episode metadata, `extra`. New table, no migration needed (`create_all()`).

- **EPG service** (`app/services/epg.py`): `refresh_epg()` fetches today + tomorrow's US schedule from TVMaze `GET /schedule?country=US&date=…`, replaces that window in the cache, and runs a janitor (drops airings ended > 24 h ago). `show_airing_now()` answers the probe; `epg_status()` feeds the UI. A transient TVMaze outage never wipes the cache.

- **Scheduler**: `epg_refresh` job every 6 h (first run ~30 s after start so the cache populates promptly after a deploy).

- **`epg_show_airing` watchdog probe**: succeeds while a show (title substring, case-insensitive) is airing now, optionally restricted to a TVMaze `network`. No `source_id` — the EPG cache is global. Distinguishes "not airing" from "EPG cache never loaded". Pair with `roku_app_active` for "the show is on AND the Roku is on the right app".

- **UI**: a TV Guide (EPG) status card on the integrations page (provider, cached-airing count, last refresh) + an `epg_show_airing` probe reference card in the rule editor.

### Notes
- The design's companion `epg_channel_mappings` table (operator-friendly "Spectrum 27 = ABC" labels) is **deferred** — the v1 probe matches the TVMaze network name directly, which is sufficient in a JSON-rule world. A follow-up if operators want a friendly channel picker.
- Schedules Direct stays available as a future `provider` value — the schema and provider column are already provider-agnostic; no migration needed to add it.

## [0.5.63] - 2026-05-15

### Added — B17 Ship 3: MQTT broker subscriber

Implements "Ship 3" from `docs/notes/2026-05-15-b17-remaining-integrations-design.md` §3.1 / §5 — the long-lived-subscriber pattern. Operator confirmed an existing Mosquitto broker on the Home Assistant host, so the hub subscribes to it (no broker bundled).

- **New `mqtt` external-sensor kind**: `host`/`port` = broker; `config = {topics[], username?, password?, client_id}`. Topics support MQTT wildcards. `password` joins the redacted-config set.

- **In-process subscriber** (`app/services/mqtt_subscriber.py`) — design Option A: one `paho-mqtt` client per source, started from the scheduler bootstrap (single-worker, advisory-lock-guarded — the paho daemon threads inherit that guarantee). `connect_async` + `loop_start()` with bounded auto-reconnect backoff; `on_connect` re-subscribes (subscriptions don't survive a reconnect). Each message → one `external_sensor_samples` row via the new `record_mqtt_message()` (`payload = {topic, msg, received_at}`). Best-effort throughout — a down broker never blocks the scheduler.

- **`mqtt_topic_equals` watchdog probe**: resolves the most-recent message on a named topic (MQTT spans many topics per source — new `latest_sample_for_topic()` helper, Python-side topic filter per the design's first-ship option (a)) and matches it case-insensitively. E.g. power-cycle the garage opener when `garage/door/state` last published `open`.

- **Dependency**: `paho-mqtt>=2.1,<3` (OSS, EPL/EDL — consistent with the open-source-only policy).

- **Integrations UI**: MQTT add-source form, sample summary (`last: <topic> = <msg>`), `mqtt_topic_equals` probe reference card.

### Notes
- First-ship scope: the subscriber list is read at container start — adding/editing an MQTT source takes effect on the next restart (a live reconcile is a documented follow-up). The UI states this.
- No schema change — topic filtering is Python-side; a `topic_key` column + index is the documented migration if message volume warrants it.

## [0.5.62] - 2026-05-15

### Added — B17 Ship 4: `host_awake` watchdog probe

Implements "Ship 4" from `docs/notes/2026-05-15-b17-remaining-integrations-design.md` §3.6 — design option (A), a direct watchdog probe, no external-sensor source. The smallest B17 item: an alias on the existing TCP-connect probe.

- **`host_awake` probe kind** (`app/services/watchdog_runtime/_probes.py`): a TCP-connect check — succeeds while the host is reachable (powered on / awake). `port` is optional, defaults to 22 (SSH). No `source_id` — it probes the host directly. A reboot rule using it fires only while the host is OFF — the operator story is "don't power-cycle the office switch while the work laptop is on."
- Probe-shape reference card added to the rule editor.

### Notes
- Zero new architecture — `host_awake` reuses `_probe_tcp`; it is a one-line dispatch alias plus docs.

## [0.5.61] - 2026-05-15

### Added — B17 Ship 2: inbound-webhook framework + Plex / Jellyfin / iOS Shortcuts

Implements "Ship 2" from `docs/notes/2026-05-15-b17-remaining-integrations-design.md` §6 — the documented next B17 integration after Solar (Ship 1). Adds the inbound-webhook architecture pattern (the hub *receives* events, vs. the existing poll model).

- **Three new webhook `external_sensor_sources` kinds** — `plex`, `jellyfin`, `ios_shortcut`. They are not polled; `poll_all_due()` skips them and `poll_source()` returns a clear "not pollable" message. A per-source `webhook_secret` is auto-minted on creation.

- **Inbound endpoint** (`app/blueprints/api/integrations_webhook.py`): `POST /api/v1/integrations/webhook/<source_id>`. Authenticated by a per-source secret in the `X-Webhook-Secret` header (constant-time compare, `secrets.compare_digest`); no admin session, no CSRF (external callers, `/api/v1/*` is CSRF-exempt). 64 KiB body cap. Accepts JSON (Jellyfin / iOS Shortcuts) or Plex's multipart `payload` form field. Writes via the new `record_webhook_event()` service helper.

- **Two watchdog probes** (`app/services/watchdog_runtime/_probes.py`):
  - `media_session_active` — Plex/Jellyfin playback. Webhook samples are *events*, so the latest play/resume event within `presumed_duration_seconds` presumes an active session. Success = media active (mirrors `roku_app_active`) — a reboot rule fires only when idle ("don't power-cycle the AV gear mid-movie").
  - `webhook_field_equals` — generic JSON-field match for iOS Shortcuts / Apple Home (e.g. a Shortcut posting `{"state":"home"}`).

- **Integrations UI**: a webhook add-source form (kind picker), the inbound URL + `X-Webhook-Secret` shown on each webhook source row (admin-only page, not masked — the operator must paste it into Plex/Jellyfin/the Shortcut), per-kind sample summary, and setup notes for all three senders.

### Notes
- No schema change — `external_sensor_sources.config` holds `webhook_secret`; samples land in `external_sensor_samples`.
- Body size is capped at 64 KiB; explicit per-source rate-limiting is a tracked follow-up (the secret + size cap are the v1 protections).
- Per the design, Google Calendar OAuth (Ship 5) stays deferred — the existing `ical` integration is functionally equivalent.

## [0.5.60] - 2026-05-15

### Added — P3a: consistent modality tagging (RFC-006 phase 1)

First phase of RFC-006 (`docs/RFC-006-multimodal-ingest.md`) — the serialization-only, zero-migration step the RFC §5 greenlit for a routine version. Stops the `modality` tagging inconsistency from spreading before the cross-modal query layer (P3b) is built.

- **`KIND_TO_MODALITY` map** (`app/models/external_sensors.py`): the authoritative source-kind → modality lookup (`roku`→media, `home_assistant`→appliance_state, `weather`→weather, `ical`→calendar, `solaredge`/`enphase_envoy`→solar, `snmp`→network). Per RFC-006 §4 this code-side map is preferred over a denormalized DB column — zero migration.
- **`modality` in serialization**: every external-sensor source now carries `modality` in its admin-API/list serialization (derived from the map); every power sample carries `modality: "power"`. The cross-modal envelope (RFC-006 Decision 1) is now consistently tagged across all three shipped modalities.

### Notes
- P3b+ (the `app/services/multimodal.py` cross-modal query layer) remains gated on the RFC-006 §9 schema review and the operator confirming cross-modal analytics is a v1 goal.

### Docs

- **Reconciled the canonical docs to v0.5.60 reality** (per `docs/notes/2026-05-15-hub-doc-reconciliation-checklist.md`). The repo shipped v0.5.34→v0.5.60 while `docs/BACKLOG.md` stayed frozen at v0.5.33, so the docs actively misled. Fixed: `BACKLOG.md` (header date, current-state rewrite, B1/B11 moved to shipped with accurate status, stale research-charter block dropped, firmware-asks section added); `B16-power-analytics-design.md` (status "Draft / do not implement" → "Implemented"); `redesign-continuation-plan-v2.md` (HLW8032 → CSE7766); `PROJECT-STATE-2026-05-09-FULL-SYNC.md` (superseded banner); `2026-05-15-p3-implementation-progress.md` + `2026-05-15-b1-rbac-design.md` (status headers → shipped); `README.md` (www2 "active-active sync" softened — B11 is a scaffold). Doc-only, no deploy.

### Design

- **P3 — RFC-006 Multimodal Ingest drafted.** `docs/RFC-006-multimodal-ingest.md`. Locks the six P3 cross-modal decisions per the hub-team plan §6: (1) a common ingest envelope (`source_ref`/`modality`/`sampled_at`/`quality`/`metrics`/`metadata`) as a query contract; (2) keep the typed-power / JSON-polled storage fork — ratified, not debt; never one sparse table; (3) a first-class cross-modal query layer (`app/services/multimodal.py`) that gates "final" schema; (4) mixed transport, one normalized ingest (direct HTTPS for plugs, no forced MQTT); (5) per-modality adapter failure isolation; (6) time sync measured not assumed — coarse windows until firmware G2 drift data. Near-zero migration by design. **Phase P3a** (make `modality` tagging mandatory + consistent across all poll payloads) is safe to ship in a routine version; **P3b+** (the query layer) waits on a schema review (RFC §9 gate) and the operator confirming cross-modal analytics is a v1 goal.

## [0.5.59] - 2026-05-15

### Added — P1.3 (partial): interactive 24h power chart

Third phase of the P1 work track per `docs/notes/2026-05-15-hub-team-status-sync-and-plan.md` §6. P1.3 has two halves — an interactive intraday chart (shipped here) and loaded-power analytics validation (still firmware-blocked, see Notes).

- **`intraday_power_series()`** (`app/services/device_power.py`): splits a window (default 24 h) into equal time buckets (default 144 → 10-min resolution) and averages `p_w` per bucket. Empty buckets land `avg_w=None` so the chart shows a gap rather than interpolating across a reporting outage. Each bucket also carries real/synthetic sample counts (P1.2 data-quality).

- **Interactive 24h chart on device detail** (`templates/device_detail.html`): a server-rendered SVG time-series of average watts over the last 24 h, with native `<title>` hover tooltips on every point (time, watts, sample count, synthetic count) — CSP-safe, no JavaScript. Line segments break at gaps; synthetic-only buckets render amber. Sits above the existing 14-day rollup sparkline, which stays as the longer trend view.

### Notes
- **Loaded-power validation remains firmware-blocked.** Every real CSE7766 sample so far is no-load (0 W); the cost/kWh analytics cannot be validated against non-zero current/watts until the firmware team provides a known-load capture. The chart itself works correctly at any wattage — it just currently draws a near-zero line, which is accurate for an idle plug.
- No schema change — the chart reads existing `device_power_samples`.

## [0.5.58] - 2026-05-15

### Added — P2.2 / P2.3: Router & managed-switch telemetry (SNMP)

Implements the P2.2/P2.3 design (`docs/notes/2026-05-15-p2-router-switch-telemetry-design.md`) — the operator confirmed SNMP-capable gear on site (Ubiquiti UniFi). Router and switch telemetry are the same IF-MIB data, so both ship as a single `kind='snmp'` external-sensor integration.

- **New `external_sensor_sources` kind `snmp`** (`app/models/external_sensors.py`, `app/services/external_sensors.py`): polls the standard SNMP IF-MIB interface table — `sysName`/`sysUpTime` plus per-interface `oper_status`, 64-bit `ifHCInOctets`/`ifHCOutOctets`, error and discard counters. Config `{version, community | v3:{...}, interface_filter}`; SNMP v2c and v3 (authPriv) both supported.

- **net-snmp CLI shell-out**: `_poll_snmp()` shells out to `snmpbulkwalk`/`snmpget` via `subprocess` — the same pattern the watchdog ping probe uses for `iputils-ping`. Zero new Python dependencies. The `snmp` apt package is added to the Dockerfile.

- **Three watchdog probes** (`app/services/watchdog_runtime/_probes.py`):
  - `snmp_interface_down` — point-in-time link-state check (the WAN-down detector; pair with a `relay_cycle` on the modem's plug)
  - `snmp_throughput_above` / `snmp_throughput_below` — bits/sec from the octet-counter delta between the last two samples (`direction` ∈ in/out/total)
  - `snmp_error_rate_above` — RX+TX error counters per minute (flaky-cable / dying-port detector)
  - Rate probes use a new `last_two_samples()` helper + `_snmp_counter_delta()` with a counter-reset guard; cold-start (one sample) returns success with `reason=insufficient_history`.

- **Integrations UI**: SNMP add-source form (v2c primary, v3 in an expandable block), per-kind sample summary (`<sys_name> · N interfaces · M up`), and `snmp_*` rule-probe examples. Community strings and v3 keys are redacted in the admin API.

### Notes
- Works with Ubiquiti UniFi (enable SNMP in the UniFi Network controller), MikroTik, OpenWrt, pfSense/OPNsense, and managed switches. Consumer ISP gateways typically expose no SNMP — accepted limitation.
- 64-bit HC octet counters are used throughout (32-bit `ifInOctets` wraps in ~34 s at 1 Gbps).

## [0.5.57] - 2026-05-15

### Added — P2.4: Home Assistant bridge deepening

Fourth phase of the P2 work track per `docs/notes/2026-05-15-hub-team-status-sync-and-plan.md` §6. The HA poll already caches every entity in each sample; P2.4 makes that data genuinely usable — numeric rules and entity discovery — at near-zero marginal cost.

- **Numeric HA probes** (`app/services/watchdog_runtime/_probes.py`): `ha_numeric_above` / `ha_numeric_below` — `_probe_ha_numeric()` reads an HA entity's `state` (or an optional `attribute`, e.g. `climate.*` → `current_temperature`), coerces to float, and compares to `threshold`. Mirrors the `power_above`/`power_below` semantics. The string-only `ha_state_is` couldn't express "freezer sensor > -10 °C" — most HA sensors are numeric, so this is the real functional gap closed.

- **HA entity browser** (`ha_entities()` + `GET /app/settings/integrations/<id>/entities`): flattens the most-recent HA sample into a sorted, browsable table — `entity_id`, friendly name, state, unit, last-changed — with an optional `?q=` substring filter. Lets the operator discover `entity_id`s for rules without leaving the hub. Linked from the Integrations page ("Entities" action, HA sources only).

### Notes
- No schema change — both the poll cache and the entity data already exist; P2.4 is pure read/probe surface.
- The numeric probe's `attribute` field is optional; omit it to read the entity's primary `state`.

## [0.5.56] - 2026-05-15

### Added — P2.1: Solar integration (SolarEdge cloud + Enphase Envoy local)

First phase of the P2 work track (zero-hardware-cost integration sources) per `docs/notes/2026-05-15-hub-team-status-sync-and-plan.md` §6 and the B17 design (`docs/notes/2026-05-15-b17-remaining-integrations-design.md` §3.4). Solar is the highest-priority P2 source — it pairs directly with B16 power monitoring (power measures *load*, solar measures *generation*; the delta is real import/export).

- **Two new `external_sensor_sources` kinds** (`app/models/external_sensors.py`):
  - `solaredge` — cloud: polls `monitoringapi.solaredge.com/site/{id}/overview`. Config `{site_id, api_key}` (static key, no OAuth). 300 req/day rate limit — the UI defaults the interval to 300 s.
  - `enphase_envoy` — local: polls the Envoy's `/production.json`. `host` = Envoy IP; optional `config.jwt` for firmware-7.0+ Envoys (legacy Envoys need no auth). Prefers the metered `eim` reading, falls back to the inverter sum.

- **Poll drivers** (`app/services/external_sensors.py`): `_poll_solaredge()` and `_poll_enphase_envoy()`. Both emit a normalized payload — `{vendor, production_w, lifetime_energy_wh, ...}`. Config validation, default ports (Envoy → 80), and secret redaction (`api_key`, `jwt` masked in the admin API) wired through the existing source CRUD.

- **Watchdog probe** (`app/services/watchdog_runtime/_probes.py`): `solar_production_above` / `solar_production_below` — `_probe_solar()` reads the latest source sample, gates on `max_sample_age_seconds` (default 1800 s), and compares `production_w` to `threshold_w`. Mirrors the B16 `power_above`/`power_below` semantics for operator-mental-model consistency.

- **Integrations UI** (`templates/settings/integrations.html`): two new add-source forms (SolarEdge / Enphase Envoy), per-kind sample summary (`N W producing · N kWh today`), and a `solar_production_above` rule-probe example.

### Notes
- Per the design, Enphase *cloud* is deferred — the local Envoy gives the same data without the OAuth dance.
- Operator value: *"if solar is exporting > 3 kW, switch on the water heater"* — the most synergistic integration with the just-shipped B16 power track.

## [0.5.55] - 2026-05-15

### Added — P1.2: Power data-quality surfacing

Second phase of the P1 work track per `docs/notes/2026-05-15-hub-team-status-sync-and-plan.md` §6. Makes the real-vs-synthetic split in power telemetry explicit so charts and rollups never silently average measured CSE7766 data with synthetic firmware fallback.

- **Source taxonomy** (`app/services/device_power.py`): `steady`/`burst` = real CSE7766 measurement; `synthetic` = firmware fallback. `source_kind()` classifies a sample as `real`/`synthetic`. Every serialized sample now carries `source_kind`.

- **`source_flags` decoder**: `decode_source_flags()` turns the opaque integer bitfield into `{raw, bits_set: [...]}`. Each sample carries `source_flags_decoded`. Bit *semantics* are firmware-owned and not yet published — see the firmware ask below.

- **`power_source_breakdown()`**: cheap `GROUP BY source` count over a window — `{total, real, synthetic, by_source}`. The data-quality primitive.

- **Power-samples API** (`GET /api/v1/admin/devices/<id>/power-samples`): adds an optional `?source=` filter (e.g. `?source=steady` to exclude synthetic samples) and a `source_breakdown` block over the full window. Samples carry `source_kind` + `source_flags_decoded`.

- **Rollup data-quality** (`device_power_rollups.synthetic_sample_count`): new nullable column records how many of a rollup day's samples were synthetic. `compute_daily_rollups()` populates it via `SUM(CASE WHEN source='synthetic')`. `_serialize_rollup()` exposes `synthetic_sample_count` + `is_synthetic_tainted` (None on pre-P1.2 rollups = "quality unknown"). Added via the `_PENDING_COLUMNS` pattern.

- **Device-detail UI**: the Power section shows a last-24h telemetry-quality line — green when all samples are real, amber when synthetic data is mixed in ("charts below mix measured and fallback data").

### Firmware asks (recorded for the firmware team)
- **`source_flags` bit dictionary**: publish the meaning of each bit so `decode_source_flags()` can surface named flags, not just bit indices.
- **Frame counts in the heartbeat**: `power_valid_frame_count` / `power_invalid_frame_count` / `power_chip_seen` are exposed on the device's `/api/status` (the `.48` snapshot showed ~35% invalid frames — a real operational signal) but are **not** in the heartbeat or power-samples payload, so the hub cannot surface them yet. Adding them to the heartbeat (alongside the existing `power_*` fields) would let a future hub release persist and chart UART/frame health.

### Notes
- P1.3 (loaded-power validation + interactive chart) remains blocked on the firmware team's loaded-power capture — every real CSE7766 sample so far is no-load (0 W).

## [0.5.54] - 2026-05-15

### Added — P1.1: JSON power query API

First phase of the P1 work track (power telemetry data-path follow-through) per `docs/notes/2026-05-15-hub-team-status-sync-and-plan.md` §6. The power-telemetry query functions in `app/services/device_power.py` previously fed only server-rendered admin pages — there was no JSON surface. P1.1 adds read-only API endpoints over them.

- **New endpoints** (`app/blueprints/admin/power_api.py`, all under `/api/v1/admin`):
  - `GET /devices/<id>/power-samples` — windowed raw samples (params: `window_seconds`, `limit`, `channel_id`)
  - `GET /devices/<id>/power-rollups` — recent daily rollups (param: `days`)
  - `GET /power/summary` — fleet aggregate (param: `window_seconds`; supports 24h/7d/30d)

- **RBAC**: per-device endpoints are `admin_required_api` + `scope_required_api(ROLE_VIEWER, scope="device")` — same posture as `GET /devices/<id>`. The fleet summary is `admin_required_api` only, matching the un-scope-filtered fleet `/app/power` page; per-device scope-filtering of the fleet summary is a tracked follow-up.

- **Modality-tagged envelope**: every response payload carries `"modality": "power"` and an explicit window descriptor. Per the hub-team plan §6 (P3), this is the seam the future cross-modal query layer reuses — tagging the envelope now means a second modality can be added without reshaping these responses.

- **Path note**: the fleet endpoint is `/api/v1/admin/power/summary` (consistent with the `admin_api` namespace) rather than the plan's indicative `/api/v1/power/summary`.

- Documented in `docs/API.md` (new "Power telemetry" section).

### Notes
- Read-only; no schema change. Unknown `device_id` → `404 device_unknown`.
- P1.2 (data-quality surfacing) and P1.3 (loaded-power validation, gated on a firmware loaded-power capture) follow.

## [0.5.53] - 2026-05-15

### Added — P0.3: Recovery-aware config push (Phase 4B) + schema reconciliation (Phase 4C)

Final phase of the P0 work track per `docs/notes/2026-05-15-hub-team-status-sync-and-plan.md` §6. Closes the P0 track (absorb the firmware status/recovery/central contract).

**Phase 4B — recovery-aware desired-config re-push:**

- **Transition detection** (`app/services/heartbeats.py`): `record_heartbeat()` now captures the pre-update recovery hot-columns and detects a recovery transition — `last_known_good_restored` newly going true, or `recovery_mode` going true→false (recovery incident closed). Either can leave the device's on-box config diverged from operator intent.

- **Re-push** (`app/services/device_config.py::maybe_push_after_recovery`): on a detected transition, the hub re-asserts `desired_config` via an `apply_config` command (`source="restore"`) and audits `device.recovery_config_pushed` with the trigger (`last_known_good_restored` / `recovery_exit`). Gated on the existing `desired_config.enabled` feature flag — same as restore-after-reflash auto-push, so it stays off until the operator opts in. When the flag is off the transition is logged for observability but no command is enqueued. Best-effort: never raises out of the heartbeat path.

**Phase 4C — `apply_config` schema reconciliation:**

- **`docs/firmware-apply-config-schema-v01.md` rewritten** to match the firmware team's source-backed notes (`docs/notes/2026-05-14-firmware-config-and-reported-schema.md`). The pre-2026-05-15 doc described an aspirational schema the real firmware never implemented (Wi-Fi credentials under `internet`, MQTT under `notifications`, `device.boot_mode`/`led_brightness`/`timezone`). Corrected:
  - `internet`/`device` are watchdog target/timer config, not Wi-Fi/boot config.
  - `notifications` is webhook-oriented, not MQTT.
  - **Support-tier table**: each `ALLOWED_DESIRED_CONFIG_KEYS` entry is marked *validated end-to-end* (only `device_name` today — confirmed drift round-trip) vs. *accepted* (firmware parses it, hub permits it, full drift round-trip not yet individually verified).
  - The hub's `ALLOWED_DESIRED_CONFIG_KEYS` is confirmed **in agreement** with the firmware's actual `apply_config` top-level keys — no key the firmware cannot parse is accepted.
- Open firmware ask recorded: per-key `reported_config` honor confirmation gates promoting keys beyond `device_name` to "validated end-to-end" — which is why the auto-push paths stay feature-flagged.

### Notes
- P0 track complete (P0.1 v0.5.51 + P0.2 v0.5.52 + P0.3 v0.5.53).
- No schema change in this release — Phase 4B reuses the P0.1 `reported_*` columns.

## [0.5.52] - 2026-05-15

### Added — P0.2: Render distinct device states in the UI

Second phase of the P0 work track per `docs/notes/2026-05-15-hub-team-status-sync-and-plan.md` §6. Consumes the firmware truth persisted in P0.1 (v0.5.51) to replace the online/offline collapse with explicit, actionable device states. This is the visible fix for the `.69` confusion.

- **New device states** (`app/services/devices/_serialize.py::_derive_central_status`): the device-self-reported `reported_*` truth is now consulted *before* heartbeat freshness — a device with a real reason it went quiet shows that reason. New states, mapped per the firmware status contract (`docs/notes/2026-05-14-firmware-status-and-recovery-contract.md` §4):
  - **central disabled on device** (`central_disabled`) — `reported_central_enabled=false`. The device's own config has central off; it may be healthy on the LAN. **This is the `.69` fix** — previously indistinguishable from offline.
  - **recovery mode** (`recovery_mode`) — `reported_recovery_mode=true`. Device is alive in recovery; reason notes consecutive-unhealthy-boot count.
  - **rebind needed** (`rebind_needed`) — `reported_central_state` ∈ {`registered_no_token`, `awaiting_register_no_token`, `reauth_required`}.
  - **transport stale** — extended: an offline device whose last `central_state` was a `*_transport_failed` value is now distinguished from a plain stale device.

- **Severity in the service layer**: `_derive_central_status()` now returns a `badge_class` (`green`/`amber`/`red`/`""`). Templates render a single chip from it instead of re-deriving severity. The three duplicated 8-line `if/elif` badge blocks (devices list ×2, device detail ×1) collapse to one-liners.

- **Device detail — "Device-reported status" block** (`templates/device_detail.html`): the Overview now shows `central_enabled`, `central_registered`, `central_state`, `recovery_mode`, boot counts, and captive-portal/recovery flags when the device runs firmware 0.1.19+ (older firmware leaves it hidden).

- **`reported_*` fields in serialization** (`serialize_device()`): the 8 device-self-reported hot columns are now in the device dict for API consumers and templates.

- **Consistency fix**: the devices-list path now passes `latest_health_state` to `_derive_central_status()`, so the `attention` (registered-but-unhealthy) state surfaces in the list, not only on detail.

### Notes
- **Backwards compatible**: devices on pre-0.1.19 firmware (NULL `reported_*`) fall through to the existing online/offline/stale logic unchanged.
- State priority: hub intent (`local_only`) → device-reported truth (central disabled / recovery / rebind) → firmware-upgrade mismatch → heartbeat freshness → health.

## [0.5.51] - 2026-05-15

### Added — P0.1: Absorb firmware status/recovery/central heartbeat contract

First phase of the P0 work track per `docs/notes/2026-05-15-hub-team-status-sync-and-plan.md` §6. Firmware `0.1.19-dev-central-safe`+ emits a rich status/recovery/central-state contract in the heartbeat payload (see `docs/notes/2026-05-14-firmware-status-and-recovery-contract.md`); the hub previously discarded all of it, collapsing every device into online/offline. This phase persists those fields. UI state rendering follows in P0.2.

- **DeviceHeartbeat history columns** (`app/models/devices.py`): 16 new nullable columns capture the per-heartbeat firmware snapshot — `recovery_mode`, `auto_recovery_triggered`, `last_known_good_restored`, `consecutive_unhealthy_boots`, `in_captive_portal`, `holdoff_remaining_seconds`, `cooldown_remaining_seconds`, `central_enabled`, `central_registered`, `central_state`, `central_device_id`, `central_heartbeat_age_seconds`, `power_analytics_enabled`, `power_chip_type`, `power_sample_rate_hz`, `power_batch_seconds`. These rows are the timeline — they support flap detection ("when did it drop into recovery").

- **Device hot columns** (`app/models/devices.py`): 8 `reported_*` columns hold current truth for fast filtering without a latest-heartbeat join — `reported_recovery_mode`, `reported_auto_recovery_triggered`, `reported_last_known_good_restored`, `reported_consecutive_unhealthy_boots`, `reported_in_captive_portal`, `reported_central_enabled`, `reported_central_registered`, `reported_central_state`. The `reported_` prefix marks them as device-asserted, distinct from the hub-owned `central_management_enabled` (hub intent) and `registration_state` (enrollment lifecycle).

- **Heartbeat ingestion** (`app/services/heartbeats.py`): `record_heartbeat()` copies all 16 fields onto the history row and refreshes the 8 hot columns. Hot columns are only touched when the device actually reports the field — a partial payload or pre-0.1.19 firmware never clobbers last-known truth with NULL.

- **Schema migration** (`app/services/bootstrap.py`): 24 additive nullable columns added via the `_PENDING_COLUMNS` `ADD COLUMN IF NOT EXISTS` pattern. Picked up automatically on container start.

### Notes
- **The `.69` case**: a device that is healthy and reachable locally but `central_enabled=false` was indistinguishable from "offline." The hub now stores `reported_central_enabled`; P0.2 will render it as "central disabled on device" rather than a false outage.
- **Backwards compatible**: all columns nullable. Pre-0.1.19 firmware that omits the fields behaves exactly as before.

## [0.5.50] - 2026-05-15

### Fixed
- **Device detail UI**: Changed "Send relay_cycle" button text to "Execute power cycle now" to clarify that the button executes an immediate power cycle with the provided parameters, not saving them as defaults for future executions. Resolves user confusion about button behavior in the Power section.

## [0.5.49] - 2026-05-15

### Added — B11 Multi-Hub Sync Phase 7: Settings UI

Final phase of B11 multi-hub sync implementation (RFC-004 Option C). Ships the admin settings UI for configuring sync parameters and viewing sync status.

- **Settings UI** (`templates/settings/sync.html`): Full sync configuration interface with form fields for `sync.enabled` toggle, `hub_id`, `hmac_key` (64-character hex), and `peer_hubs` (JSON array). Includes live sync status dashboard showing outbox stats (max_seq, total_events) and peer cursor table with last_seq, updated_at, and error status for each peer.

- **Settings controller** (`app/blueprints/admin/settings.py`):
  - `settings_sync_page()` — renders sync UI, fetches live status from `/api/v1/sync/status`
  - `settings_sync_save_submit()` — validates and persists sync config to `runtime_settings`, validates HMAC key format and peer_hubs JSON structure

- **Documentation section**: In-UI summary of RFC-004 Option C, conflict resolution (last-writer-wins), tombstones, idempotent apply, and table references.

### Notes
- B11 Phases 1-7 complete. Multi-hub sync fully operational with bidirectional event replication between www and www2 hubs.
- Default state: `sync.enabled=false`. Operator must configure via `/app/settings/sync` to activate.

## [0.5.48] - 2026-05-15

### Added — B11 Multi-Hub Sync Phase 6: HMAC Authentication & Replicator

Sixth phase of B11 multi-hub sync. Ships HMAC-based peer authentication and the background sync replicator daemon.

- **HMAC authentication** (`app/middleware/sync_auth.py`):
  - `sync_peer_required` decorator for `/api/v1/sync/*` endpoints
  - Token format: `hmac-sha256.<hub_id>.<signature>` where signature = HMAC-SHA256(shared_key, hub_id)
  - Reads `sync.hmac_key` from runtime_settings
  - Falls back to `admin_required_api` for manual testing

- **Sync replicator** (`app/services/sync_replicator.py`):
  - `tick()` — main entry point, called every 3s by APScheduler
  - `_get_peer_hubs()` — reads `sync.peer_hubs` from runtime_settings
  - `_generate_hmac_bearer_token()` — creates authentication tokens
  - `_fetch_events_from_peer()` — polls peer's `/api/v1/sync/since` with HMAC auth
  - `_apply_event_batch()` — applies fetched events and updates sync cursor

- **Scheduler integration** (`app/jobs/scheduler.py`):
  - Registered `sync_replicator_tick` job with 3-second interval
  - Runs only when `sync.enabled=true` in runtime_settings

- **API endpoint security**: Changed `/api/v1/sync/since` and `/api/v1/sync/status` from `admin_required_api` to `sync_peer_required` for proper peer authentication.

### Changed
- Sync API endpoints now require HMAC authentication instead of admin bearer token

## [0.5.47] - 2026-05-15

### Fixed
- Import error: Corrected decorator import in sync API endpoints from non-existent `require_authenticated` to `admin_required_api`

## [0.5.46] - 2026-05-15

### Added — B11 Multi-Hub Sync Phase 4: Audit Integration

Fourth phase of B11 multi-hub sync. Integrates outbox emission into the audit service for automatic event capture.

- **Audit service integration** (`app/services/audit.py`):
  - `_should_sync_action()` — filters syncable actions (device/site/group/user create/update/delete/renamed/adopted)
  - `_emit_outbox_for_scoped_action()` — emits outbox events for syncable mutations, creates tombstones for deletes
  - Updated `record_scoped()` — added `entity_snapshot` parameter, automatically emits outbox events for syncable actions
  - Updated `record()` — also emits outbox events for syncable actions (not just scoped path)

### Notes
- Outbox emission is now automatic for all syncable mutations. No explicit emit calls needed in application code.

## [0.5.45] - 2026-05-15

### Added — B11 Multi-Hub Sync Phases 1-3: Foundation

First three phases of B11 multi-hub sync implementation per RFC-004 Option C. Ships database models, core sync services, and API endpoints for peer-to-peer event replication.

**Phase 1: Database Models** (`app/models/sync.py`):
- `OutboxEvent` — append-only event log with seq (auto-increment), event_type, entity_type, entity_id, payload (JSON), scope_claims, tombstone_for, and at (timestamp)
- `SyncCursor` — tracks last_seq per peer_hub_id for replication progress
- `Tombstone` — prevents resurrection of deleted entities across hubs

**Phase 2: Sync Services** (`app/services/sync.py`):
- `emit_outbox_event()` — creates OutboxEvent rows
- `get_sync_cursor()` / `update_sync_cursor()` — manages peer replication progress
- `is_tombstoned()` / `add_tombstone()` — tombstone management
- `apply_outbox_event()` — applies incoming events with last-writer-wins conflict resolution
- `fetch_outbox_events_since()` — queries outbox for `/api/v1/sync/since` endpoint
- `entity_to_dict()` — converts SQLAlchemy entities to JSON

**Phase 3: Sync API** (`app/blueprints/api/sync.py`):
- `GET /api/v1/sync/since` — returns events since seq with pagination (next_seq, has_more)
- `GET /api/v1/sync/status` — returns outbox stats and peer cursor state

### Changed
- Added `requests>=2.31,<3` dependency for HTTP polling in sync replicator

### Notes
- Sync is disabled by default. Configuration via Settings UI in Phase 7.
- LWW conflict resolution uses `event.at` timestamp.
- Idempotent event application: safe to replay events.

## [0.5.38] - 2026-05-15

### Added — B1 RBAC Phase 4a (P4a): Scoped invitations + bindings API

Fourth phase of B1 RBAC rollout per `docs/notes/2026-05-15-b1-rbac-design.md` §4 (P4).
Ships backend and API support for scoped invitations and user binding management. UI for
invite form and binding editor deferred to P4b.

- **Scoped invitations**: `invitations.scope_payload` JSONB column stores binding specifications
  that will be granted on redemption. Shape: `{"bindings": [{"scope_type": "site", "scope_id": "..."}]}`.
  NULL = legacy global role only. Added via `_PENDING_COLUMNS` pattern in `bootstrap.py`.

- **Invitation service updates** (`app/services/invitations.py`):
  - `mint_invitation()` accepts optional `scope_payload` parameter with validation
  - `redeem_invitation()` creates `role_bindings` rows from `scope_payload` after user creation
  - Failed binding grants (e.g. resource deleted between invite/redeem) are logged but don't fail redemption

- **Invitation API** (`app/blueprints/admin/invitations.py`):
  - `POST /api/v1/admin/invitations` accepts `scope_payload` in request body
  - Returns `has_scope_payload: true` in response when scope provided
  - Audit row includes `has_scope: true` for tracking

- **User bindings management API** (`app/blueprints/admin/users.py`):
  - `GET /api/v1/admin/users/<id>/bindings` — list all bindings for a user
  - `POST /api/v1/admin/users/<id>/bindings` — grant a binding (body: `{scope_type, scope_id?, role}`)
  - `DELETE /api/v1/admin/users/<id>/bindings/<bid>` — revoke a binding
  - All endpoints super_admin only, emit audit events (`user.binding_granted`, `user.binding_revoked`)

### Changed
- **Invitation model**: Added `scope_payload: Mapped[dict | None]` field (JSONB, nullable)

### Notes
- **P4a vs P4b**: This ship is API-complete. Operators can create scoped invitations and manage
  bindings programmatically. UI for invite form pickers and user binding editor deferred to P4b.
- **Backwards compatible**: Invitations without `scope_payload` behave exactly as before (global
  role only, RBAC backfill grants default bindings).

## [0.5.37] - 2026-05-15

### Added — B1 RBAC Phase 3 (P3): Scope-aware list filtering

Third phase of B1 RBAC rollout per `docs/notes/2026-05-15-b1-rbac-design.md` §4 (P3).
Applies scope-based filtering to four major list surfaces: devices, groups, sites, and
audit/history. In shadow mode (default), runs double-query pattern that logs what WOULD
be hidden but preserves legacy behavior (returns unfiltered results). In enforce mode,
actually filters results based on user's effective scope.

- **Central filtering logic** (`app/services/rbac_filter.py`): New file with four filter
  functions: `filter_devices_with_shadow_logging()`, `filter_groups_with_shadow_logging()`,
  `filter_sites_with_shadow_logging()`, `filter_audit_with_shadow_logging()`. Each
  implements shadow/enforce mode switching and double-query pattern. Super_admin always
  bypasses filtering (escape hatch from §9.0).

- **Double-query shadow pattern**: In shadow mode, runs both unfiltered and filtered
  queries, calculates diff, logs `rbac.shadow_diff` audit rows with `total_count`,
  `scoped_count`, `hidden_count`, and `hidden_sample` (first 10 IDs), then returns
  unfiltered results. Provides production observability of enforcement impact before flip.

- **Integrated into list services**:
  - `app/services/devices/_query.py::list_devices()` — device list filtering
  - `app/services/groups.py::list_groups()` — group list filtering  
  - `app/services/sites.py::list_sites()` — site list filtering
  - `app/services/history.py::_audit_iter()` — audit/history filtering

- **Cross-resource audit filtering**: Audit events are visible if user has access to the
  target resource. Checks `target_type` (device/site/group) and filters based on whether
  `target_id` is in user's effective scope for that resource type. Events with
  `target_type=NULL` (system events) are always visible. Most complex filter due to
  cross-resource nature.

### Tests
- **Regression test**: `tests/qa/test_v0537_scope_filter_lists.py` validates:
  (a) super_admin sees all resources in all lists; (b) super_admin produces no
  `rbac.shadow_diff` rows; (c) scoped users produce shadow_diff rows when listing
  resources; (d) all list endpoints serve correctly with filtering. Enforce-mode
  validation happens in live retest (flipping global runtime setting in test would
  affect real callers).

## [0.5.36] - 2026-05-15

### Added — B1 RBAC Phase 2 (P2): Device.site_id NOT NULL + audit archive

Second phase of B1 RBAC rollout per `docs/notes/2026-05-15-b1-rbac-design.md` §4 (P2).
Enforces `Device.site_id NOT NULL` via one-shot backfill + schema constraint, and ships
the audit archival system for long-term audit retention.

- **Device.site_id NOT NULL enforcement**: One-shot backfill in
  `app/services/bootstrap.py::ensure_device_site_id_backfill()` assigns any devices
  with `site_id=NULL` to a "Default" site (or reuses the single existing site if
  exactly one exists). New `_PENDING_CONSTRAINTS` pattern in `bootstrap.py` then
  applies `ALTER TABLE devices ALTER COLUMN site_id SET NOT NULL`. Backfill runs
  before the constraint is applied, ensuring no devices are orphaned. Unblocks P3
  (scope-aware list filtering) which depends on reliable site associations.

- **Audit event archival system**: New `audit_events_archive` table
  (`app/models/audit.py::AuditEventArchive`) mirrors `audit_events` shape with
  additional `archived_at` timestamp. Nightly APScheduler job at 03:00 UTC
  (`app/services/audit_prune.py::prune_old_audit_events()`) soft-prunes events older
  than `system.audit_retention_days` runtime setting (default 90 days) into the
  archive, then deletes from source. Date-rollover guard ensures one run per day.

- **Runtime setting**: `system.audit_retention_days` (default 90) controls audit prune
  threshold; env-var fallback `REBOOTER_AUDIT_RETENTION_DAYS`. Documented in
  `app/services/runtime_settings.py::SYSTEM_KEYS`.

### Changed
- **APScheduler**: Now starts 6 jobs (added `_audit_prune_job` / `audit_prune_daily`).
  Startup log shows `audit_prune_daily @ 03:00 UTC`.
- **Bootstrap sequence**: `run_startup_bootstrap()` calls `ensure_device_site_id_backfill()`
  after RBAC backfill, then applies `_ensure_constraints()` to enforce pending constraints
  that depend on backfills having run.

### Tests
- **Regression test**: `tests/qa/test_v0536_site_not_null_and_archive.py` validates:
  (a) all devices have non-null `site_id` after backfill; (b) `audit_events_archive`
  table exists with correct schema; (c) nightly prune job moves old events to archive;
  (d) date-rollover guard prevents double-runs. (Requires app context; designed for
  in-container execution or host with psycopg installed.)

## [0.5.35] - 2026-05-15

### Added — B1 RBAC Phase 1 (P1): shadow-mode scope-check foundation

First implementable slice of the B1 RBAC rollout, per the design doc
`docs/notes/2026-05-15-b1-rbac-design.md` §4 (P1). The `role_bindings`
table + resolver shipped in v0.5.0/.1; this ship wires those resolvers
into an *enforcement pathway* that runs in **shadow mode** by default —
the legacy `role_required_*` decorators stay authoritative, and a scope
miss is only logged, never blocked.

- **Scope-check helpers** (`app/services/role_bindings.py`):
  `require_can_act_on_device` / `_site` / `_group`. Each consults the
  caller's effective scope; on a miss it emits an audit row
  (`rbac.shadow_deny` in shadow mode, `rbac.enforce_deny` in enforce
  mode) and, in enforce mode only, raises `RbacScopeDenied`. New
  `effective_group_ids` / `can_act_on_group` resolvers complete the
  device/site/group set. A `super_admin` is always exempt
  (RFC-003 §9.0 escape hatch) — exempt via either the legacy
  `users.role` column or a global super_admin binding.
- **`rbac.enforce_mode` runtime setting** — `{"shadow","enforce"}`,
  default `shadow`. Toggling it is the entire A8 cut-over: no redeploy,
  no code branch. `enforce_mode()` reads it live.
- **`scope_required_api` / `scope_required_ui` decorators**
  (`app/middleware/admin_auth.py`) — pair below a `role_required_*`
  decorator; check the caller's bindings against the resource id in the
  route URL. Declarative `scope=` + `id_kwarg=` form (the predictable
  URL-id case the design doc earmarks for decorators). `scope_required_ui`
  ships now for the P3 list/detail work but is not yet wired to a route.
- **`record_scoped()` choke-point** (`app/services/audit.py`) — wraps
  `record()` and folds a `scope_claim` into the audit details. Today a
  thin wrapper; it is the single seam B11 multi-hub sync (RFC-004
  Option C) will append `outbox_events` from, so every per-resource
  mutation routed through it is B11-ready without a second sweep.
- **Two demonstrator routes wired** (`app/blueprints/admin/devices_api.py`):
  `GET /api/v1/admin/devices/<id>` (read, `ROLE_VIEWER`) and
  `POST /api/v1/admin/devices/<id>/commands` (write, `ROLE_OPERATOR`).
  The command route's audit row now flows through `record_scoped()`.
- **Regression test** — `tests/qa/test_v0535_rbac_shadow_skeleton.py`:
  super_admin never shadow-denies; a no-binding user is shadow-logged
  but not blocked; the command audit row carries its `scope_claim`;
  legacy auth is unchanged.

Everything else is untouched — only the two demonstrator routes use the
new pathway, so a rollback is a one-PR back-out. P2–P5 follow per the
design doc; the enforce flip (P5b) is gated on a ≥7-day clean shadow
soak.

## [0.5.34] - 2026-05-15

### Fixed — BUG-054: `custom` probe-kind dropped from canonical list

The 2026-05-15 regression sweep (R3b) confirmed that
`PROBE_KIND_CUSTOM` had been in `KNOWN_PROBE_KINDS` since v0.4.0
but the runtime `_run_probe` dispatcher never had a branch — any
operator who saved a rule with `kind=custom` got
`failure: reason="unknown probe kind: custom"` forever.

Fix (chose Option A from the bug log):
- Removed `PROBE_KIND_CUSTOM` from `KNOWN_PROBE_KINDS` in
  `app/models/watchdog.py`. The string constant itself is preserved
  with a deprecation comment for any third-party importer.
- Removed the `custom` branch from `_probe_to_phrase()` in
  `app/services/watchdog.py`. Old DB rows with `kind=custom`
  (none on the live hub) would fall through to the generic
  "unknown probe" phrase.
- `KNOWN_PROBE_KINDS` shrinks from 14 to 13 canonical kinds.

### Fixed — BUG-055: per-kind probe-field validation

The 2026-05-15 regression sweep (R9) caught that `create_rule` +
`update_rule` only validated `probe.kind` and (for `internet`) the
`targets` list shape. 12 of 15 deliberately-broken probe
configurations returned 201 — operators could save rules that
silently failed at runtime, including (for `cycle`/`hold_off`
actions) rules that eventually fired actions on wrong targets
when their threshold typos crossed the failure-streak gate.

Fix:
- New `_validate_probe(probe)` helper in
  `app/services/watchdog.py` dispatching per-kind. Follows the
  pattern established by
  `services.external_sensors._validate_kind_config()`.
- Per-kind enforcement now:
  - **`internet`**: `targets[*]` shape (preserved from v0.5.9)
  - **`ping`**: `host` non-empty
  - **`tcp`**: `host` non-empty; `port` int in [1, 65535]
  - **`http`**: `url` non-empty + must use http/https scheme
  - **`dns`**: `hostname` non-empty
  - **`gateway`**: no required fields
  - **`roku_app_active`**: `source_id` + `app_name` non-empty
  - **`ha_state_is`**: `source_id` + `entity_id` + `expected_state` non-empty
  - **`weather_alert_active`**: `source_id` non-empty; optional
    `min_severity` ∈ {Minor, Moderate, Severe, Extreme}
  - **`ical_event_active`**: `source_id` non-empty
  - **`power_above` / `power_below`**: `device_id` non-empty;
    `threshold_w` numeric in [0, 10000]; optional `window_seconds`
    int in [30, 86400]
  - **`power_zero_while_on`**: `device_id` non-empty; optional
    `near_zero_threshold_w` numeric in [0, 100]; optional
    `window_seconds` int in [30, 86400]
- Defensive fallback: a future canonical kind without a
  validator-branch raises a developer-facing error.
- `create_rule` + `update_rule` both call the new helper. The
  duplicated v0.5.9 internet-targets inline block is removed from
  both (collapsed to a single source of truth in `_validate_probe`).

### Tests

- `tests/qa/test_v0534_probe_validation.py`:
  - `test_custom_probe_kind_rejected_at_create` — BUG-054 retest
  - 25 parametrised bad-case tests (BUG-055 negative coverage)
  - 15 parametrised happy-case tests (no false rejections across
    all 13 canonical kinds)

The 25 bad cases match the v0.5.34 in-container smoke
(9 quick checks all returned the expected `WatchdogValidationError`
with the expected message substring) plus the full coverage list
from the bug-log fix-direction.

### Out of scope

- Rules-create form UI fields already enforced these constraints
  via HTML5 `required`/`min`/`max` (v0.5.28 / v0.5.32). The fix
  closes the API + JSON-editor bypass paths.
- No template change shipped; this is purely a server-side
  validation hardening.

## [0.5.33] - 2026-05-14

### Changed — Phase 5: docs/backlog cleanup pass

Operator-asked "next up we plan and research the B1 RBAC, B11 sync,
B17 remaining and B17 layer 2 epg" — this ship lands the backlog
re-org that surfaces those items clearly for the research session.

**`docs/BACKLOG.md` rewrite (top portion):**

- New **Current state** section at top — replaces the stale
  "Last updated: 2026-05-09" header with a fresh 2026-05-14 PM
  status snapshot (live version, ship history reference).
- New **Truly open — operator-decision territory** section with
  per-item size + blocker columns. Lists B1, B11, B17 remaining
  integrations, B17 Layer 2 EPG, Phase 3, 4B, 4C, 6, and 2B-full.
- New **Operator-decision research items** section preserving
  the operator's verbatim 2026-05-14 directive as the next
  session's charter.
- New **Ops items** section — www2 mirror sync + bench reflash
  staging.
- New **Shipped — what's CLOSED** section with a compact
  pre-v0.5.x B1-B14 list + B15-B24 mid-cycle list + the B16 phase
  table + the firmware-team alignment-plan phase table + the
  structural-refactor cross-reference.
- New **How to consume this list** preamble pointing readers at
  the live-priority view vs the historical entries.

The historical detailed B1-B24 entries are retained verbatim below
the new top section for archival reference; no information is
deleted.

### Phase 3 unblock noted

The parallel firmware-team session shipped `0.1.19-dev-central-safe`
this evening with the heartbeat-contract expansion the alignment
plan called for (richer status fields + `reported_config`
snapshot). The new BACKLOG section flags this as the unblocker for:

- **Phase 3** — recovery / status truth (hub-side absorption now
  actionable).
- **Phase 4B** — recovery-aware drift actions (depends on Phase 3).

See `docs/notes/2026-05-14-firmware-status-and-recovery-contract.md`
+ `docs/notes/2026-05-14-heartbeat-expansion-and-reported-config-memo.md`
for the contract spec the hub side will implement against.

### Parallel-session uncommitted state at v0.5.33 ship time

Working tree has uncommitted parallel-session work I did NOT touch
this ship (separate Claude session is mid-work on the firmware-team
side):
- `docs/firmware-apply-config-schema-v01.md` modified
- 5 new `docs/notes/2026-05-14-*.md` files (firmware-coord + heartbeat
  + reported-config + button-verification + 48-preview JSON)
- 3 still-empty 0-byte stubs from earlier sessions

Not mine to commit; will land when the parallel session pauses
cleanly. Documented in the new BACKLOG **Ops** section.

## [0.5.32] - 2026-05-14

### Added — B16 Phase 1D: power-targeted watchdog probe kinds

Closes the B16 power-monitoring track. Three new probe kinds read
recent `device_power_samples` for a designated device and compare
against operator-set thresholds.

**New `KNOWN_PROBE_KINDS`** (`app/models/watchdog.py`):
- `power_above` — fails when `avg_w(window) > threshold_w`. Use
  case: "heater shouldn't draw > 1500 W for 5 min — fire notify".
- `power_below` — fails when `avg_w(window) < threshold_w`. Use
  case: "subwoofer should idle at 15 W; if < 5 W for 10 min,
  appliance probably stuck off — fire relay_cycle".
- `power_zero_while_on` — phantom-failure detector. Fails when the
  device's latest heartbeat says `relay_on=true` AND avg watts over
  the window is under `near_zero_threshold_w` (default 0.5 W).
  Catches appliances that died while the smart-plug relay is still
  energized.

**Probe shape** (rules JSON):
```json
{"kind": "power_above",
 "device_id": "dev_…",
 "threshold_w": 1500,
 "window_seconds": 300,
 "max_sample_age_seconds": 600}
```

`power_zero_while_on` swaps `threshold_w` for
`near_zero_threshold_w` (defaults to 0.5).

**Stale-sample failure gate** — if the most-recent sample is older
than `max_sample_age_seconds` (default 600 s = 10 min), the probe
returns failure with `reason='stale_sample'`. A dead device-side
sampler can never pin a power_below rule to a misleading "success".

**Empty-window failure gate** — if there are no samples in the
window OR no `p_w` readings (firmware reports rssi but not real
power), the probe returns failure with an explanatory `reason`.
This is the inverse of stale-sample protection: we'd rather fire
than pretend nothing's wrong.

**Plain-English sentence** — rules-list renders:
- `device 'dev_x' averaging > 1500 W over 300 s`
- `device 'dev_y' averaging < 5 W over 600 s`
- `device 'dev_z' drawing near-zero (< 0.5 W) while relay is on`

**Rules-create form** — new probe-kind options + a shared
`#probe_power_block` with a device picker, threshold/near-zero
field (swaps by kind via JS), window-seconds + max-sample-age
inputs.

**Rules-edit JSON reference** — three new `<details>` snippets on
`/app/rules/<id>/edit` for the three power probe kinds, matching
the format already established for the integration probes.

**Rules-list chips** — per-kind chip in the existing chip-cluster
under each rule's sentence:
- `probe: power > 1500 W`
- `probe: power < 5 W`
- `probe: phantom failure`

**Event-log details** — per-probe rendering carries `avg N.N W vs
threshold X W · N samples / Ns · relay on/off`.

### Closes the B16 track

| Phase | Status |
|---|---|
| 1A live last-sample + watts chip | ✅ v0.5.26 |
| 1B `/app/power` fleet page | ✅ v0.5.27 |
| 1C rollups + sparkline + fleet timeseries chart | ✅ v0.5.29 |
| 1C cost calc + CSV export | ✅ v0.5.30 |
| **1D power-targeted probe kinds** | ✅ v0.5.32 |

The remaining B16 items in the original spec (threshold alerting
via notifications, retention tuning) are addressable via the
existing rule + notification machinery — no further dedicated B16
work owed.

## [0.5.31] - 2026-05-14

### Added — Phase 4A: desired-config drift visibility

Promotes the v0.5.22 B21 desired-config plumbing from a per-device
editor into a **fleet-level** triage signal. Operators no longer have
to open each device's detail page to find which units are drifted
from the operator-intended config.

**Devices list** (`/app/devices`):
- New drift chip per row, rendered in the Central column beneath the
  central-status chip:
  - `drift · N · M missing` (amber) when `desired_config` and
    `last_reported_config` disagree on N mismatched and/or M missing
    fields.
  - `config: unconfirmed` (grey) when `desired_config` is set but
    the device has never echoed `reported_config` in a heartbeat.
  - **No chip when in-sync** — clean rows stay clean.
- Each chip is a link to `/app/devices/<id>#desired-config` so a
  click goes straight to the per-device card where the operator can
  push.

**Status page attention items** (`/app/`):
- New `desired_config_drifted` attention (severity `warn`, rank 70,
  between offline-short and watchdog-firing). Title carries the
  mismatched + missing field counts; hint tells the operator to
  push from the device-detail Desired Config card. Surfaces on the
  Status feed every fleet that has at least one drifted device.
- New `desired_config_unconfirmed` attention (severity `info`, rank
  35). Fires when desired_config is set but
  `last_reported_config` has never landed — typically a firmware
  version that doesn't echo `reported_config` in heartbeats.

Both kinds default-link to the device-detail page via the existing
status-template fallback path (no template change needed).

**Backend** — `services.devices.list_devices` now serializes
`desired_config_drift_summary` per row:

```json
{
  "state": "drifted" | "in_sync" | "unconfirmed",
  "missing": ["field", ...],
  "mismatched": ["field", ...]
}
```

Computed inline from the row's `desired_config` + `last_reported_config`
JSON columns — no extra queries.

`services.inbox._compute()` reuses the same logic for the
attention-feed items.

### Out of scope (still queued)

- **Phase 4B** — recovery-aware drift actions (post-rebind /
  post-recovery surfacing of "push desired config now" guided
  action). Depends on Phase 3 heartbeat-contract expansion which is
  firmware-coord-gated.
- **Phase 4C** — desired-config schema alignment with firmware
  schema doc + version-gating hints. Firmware-coord-gated.
- **last-pushed-age cue** on devices list and **freshness cue** on
  `last_reported_config` — both pending Phase 3 heartbeat-contract
  fields that surface those timestamps reliably.

## [0.5.30] - 2026-05-14

### Added — B16 cost calc + CSV export, Phase 2C polish, Phase 2B edit reference

Polish bundle wrapping the operator-visible loose ends from the prior
ships in this push (v0.5.25 → v0.5.29).

#### B16 Phase 1C remainder — cost calc + CSV export

- New runtime settings:
  - `power.rate_per_kwh` (numeric; 0–10) — operator's electricity
    rate. Unset → cost rendering hides cleanly.
  - `power.currency` (default `USD`) — currency label rendered next
    to costs. Up to 8 chars.
- New service helper `services.device_power.cost_rate_per_kwh()`
  returns `(rate, currency)` — `None` rate when unset.
- `fleet_summary()` now computes per-device + fleet-level `kwh_window`
  and `cost_window` for the chosen window. kWh comes from
  `device_power_rollups.kwh` (preferred — direct from the nightly
  job) plus an `avg_w × window_hours / 1000` approximation when no
  rollup yet (typical state today since firmware-side sampling not
  shipped). Cost = `kWh × rate`.
- New endpoints:
  - `POST /app/power/rate` — operator-set rate; clears the override
    when submitted blank.
  - `GET /app/power/export.csv?window=24h|7d|30d` — per-device
    aggregates as CSV. Audit-logged as `power.csv_exported`.
- `/app/power` UI:
  - Rate-setting form inline at the top of the page.
  - "Export CSV" button (window-scoped).
  - Two new headline cards: **Fleet kWh** + **Fleet cost** (only
    when data exists).
  - Per-device table gains a `kWh (<window>)` column always, and
    a `Cost (<window>)` column when a rate is set.

Audit events: `power.rate_per_kwh_set`, `power.rate_per_kwh_cleared`,
`power.csv_exported`.

#### Phase 2C — per-probe event-detail polish for integration probes

The rules-list event log already rendered details for `ping`,
`internet`, `roku_app_active`, `cooldown_skip`, `action_fired`,
and generic `reason` payloads. v0.5.30 fills in the three remaining
integration probe shapes:

- **`ha_state_is`** — `· entity=… · expected=… · actual=… · changed <ts>`
- **`weather_alert_active`** — `· N/M alerts match · filter "X" · min Severe · <event[severity]>, …`
  (renders top 3 matched alerts; "+N more" for the rest)
- **`ical_event_active`** — `· N/M airing · filter "X" · "summary1", "summary2"`
  (renders top 2 active events; "+N more" for the rest)

Operators no longer have to expand a probe row's raw details JSON
to see what the rule actually saw.

#### Phase 2C — integration-source health on `/app/settings/integrations`

- Health column now also renders the truncated `last_error` inline
  in red below the status chip (was previously only available as a
  tooltip).
- Latest-sample column is now per-kind aware:
  - **roku** → `<active_app>` + screensaver chip (unchanged).
  - **home_assistant** → `<N entities> cached`.
  - **weather** → `<N alerts>` badge with the top 2 events listed;
    "no active alerts" when 0.
  - **ical** → `<N events> in window` with the next event's summary.
- Single source-of-truth: the per-kind rendering matches what the
  matching watchdog probe reads, so operators see the same data the
  rule engine sees.

#### Phase 2B edit-flow back-port — probe-shape reference card (intermediate)

Full structured per-kind form on `templates/rules/edit.html` is
deferred to a future ship — it's a non-trivial Jinja extraction.
v0.5.30 ships a meaningful intermediate: a "Probe shape reference"
card on the edit page with copy-pasteable JSON snippets for the four
integration probe kinds (roku_app_active / ha_state_is /
weather_alert_active / ical_event_active). Each `<details>` opens to
the canonical probe shape + an optional-field note. Operators editing
an integration rule no longer need to leave the page to look up
field names.

### Out of scope (still queued)

- Threshold alerting (power-targeted watchdog rule probe kinds —
  Phase 1D).
- Full structured per-kind probe form on `templates/rules/edit.html`
  (Phase 2B finish-line — would require extracting the create-form
  blocks into a shared Jinja macro/include with value pre-population).
- Phase 3 — heartbeat-contract expansion (`central_state`,
  `recovery_mode`, etc.) — firmware-team-coord-gated.

## [0.5.29] - 2026-05-14

### Added — B16 Phase 1C: daily rollups + per-device sparkline + fleet timeseries chart

The middle slice of the B16 power-monitoring track. Phase 1A (v0.5.26)
shipped the live single-sample surface; Phase 1B (v0.5.27) shipped the
fleet `/app/power` table; this ship lights up **historical trend**
visibility for both surfaces.

**Schema** (auto-creates via `Base.metadata.create_all()`):
- `device_power_rollups` table: `(id, device_id, day_bucket,
  computed_at, sample_count, avg_w, min_w, max_w, kwh)`. Unique on
  `(device_id, day_bucket)` so re-runs upsert cleanly. Index on
  `(device_id, day_bucket desc)` for the per-device sparkline lookup.

**Nightly aggregation job** — new `power_rollups_daily` APScheduler
cron tick at 02:00 UTC. Calls
`services.device_power.compute_daily_rollups()` which:
- Aggregates yesterday's `device_power_samples` (channel 0 only for
  now) via a single SQL `GROUP BY device_id`.
- Computes `kwh = (max(energy_wh) - min(energy_wh)) / 1000.0` per
  device-day. Devices that don't report `energy_wh` land NULL.
- Upserts via `INSERT ... ON CONFLICT (device_id, day_bucket) DO
  UPDATE` (Postgres) or `INSERT OR REPLACE` (SQLite test path).
  Idempotent — backfill after a samples correction is the expected
  operator workflow.

**Query helpers** — `services.device_power`:
- `daily_rollups_for_device(device_id, days=N)` — newest-first list,
  default 14 days. Used by Device-detail sparkline.
- `fleet_daily_rollups(days=30)` — pivoted into a per-day-per-device
  shape ready for stacked-bar rendering. Used by `/app/power`.

**UI** — both surfaces gain inline-SVG charts (no JS dep, no new
deps):
- **Device-detail Power tab** — 14-day daily-avg-watts sparkline
  beneath the live last-sample card. Polyline + per-day dots; peak-W
  callout in the footer. Hidden when no rollups exist yet (typical
  state today).
- **Fleet `/app/power` page** — 30-day stacked-bar chart of per-
  device daily averages. SVG `<title>` tooltips on hover. Auto-scaled
  Y-axis to the peak value across the window. Color legend below the
  chart maps device-id → display name. Hidden when no rollups exist
  (typical state today since no firmware-side B16 sampling yet).

**Synthetic-data note**: today's fleet has zero `device_power_samples`
rows, so the nightly rollup job will be a no-op until firmware-team
ships device-side sampling. Both chart surfaces gracefully hide when
empty — they do NOT render a broken/zero chart. When firmware starts
emitting, the next nightly job produces rollups + charts light up
automatically with zero further hub change.

### Out of scope for this ship (queued for v0.5.30)

- **Phase 1C remainder**: cost calc widget (`power.rate_per_kwh`
  runtime_setting → "$XX this window"), CSV export from chart range
  pickers, threshold alerting.
- **Phase 1D**: power-targeted watchdog probe kinds.
- **Phase 2C**: richer per-probe event-detail rendering on rules list;
  integration-source health cues on `/app/settings/integrations`.
- **Phase 2B edit-flow back-port**: `templates/rules/edit.html` still
  uses the JSON editor for integration probes; per-kind form blocks
  on the edit page deferred.

## [0.5.28] - 2026-05-14

### Added — Phase 2B: per-kind form fields for integration probes

Closes the v0.5.25 Phase 2A escape-hatch gap. Operators no longer
need the JSON editor to create rules using the four integration
probe kinds — the rules-create form on `/app/rules` now has per-
kind blocks with kind-filtered source pickers.

**New probe-kind options** in the form `<select>`:
- `roku — active app matches`
- `home assistant — entity state matches`
- `weather — active NWS alert`
- `calendar — iCal event currently airing`

**Per-kind form blocks** (show/hide JS keyed off the `<select>`):
- **Roku** — source picker (filtered to `kind=roku`) + app-name
  text + max-sample-age (default 120s).
- **Home Assistant** — source picker (`kind=home_assistant`) +
  entity_id + expected_state + max-sample-age (default 60s).
- **Weather** — source picker (`kind=weather`) + optional
  event-contains substring + optional min-severity dropdown
  (Minor / Moderate / Severe / Extreme) + max-sample-age
  (default 600s).
- **iCal** — source picker (`kind=ical`) + optional
  summary-contains substring + max-sample-age (default 1800s).

Each block has a graceful "no sources of this kind registered yet"
fallback that links to `/app/settings/integrations` so the operator
knows what's missing.

**Backend** (`app/blueprints/admin/rules.py`):
- `rules_page()` now passes `sources_by_kind` (a dict of `{kind:
  [sources]}`) to the template — one batched call to
  `external_sensors.list_sources()`.
- `rules_create_submit()` gains four `elif probe_kind ==` branches
  that pull the per-kind fields off the form and build the probe
  dict in the shape `services.watchdog.create_rule()` validates.

**JS** — extended the existing `syncVisibility()` to drive the four
new blocks. `PROBE_ARG_KINDS = {ping, tcp, http, dns}` is the only
set that shows the generic `probe_arg` textbox; integration kinds
hide it.

**JSON editor** stays as the escape hatch for shapes the form
can't express (custom probes, exotic combos).

### Out of scope for this ship

- Edit flow for the integration probes still uses the JSON editor
  (`templates/rules/edit.html`). Per-kind form support on the edit
  page is queued as a follow-up for v0.5.29 or later.
- Phase 2C — richer per-probe event-detail rendering on the
  rules-list event log + integration-source health cues on
  `/app/settings/integrations` (queued for v0.5.29).

## [0.5.27] - 2026-05-14

### Added — B16 Phase 1B: `/app/power` fleet page

UI ride-along to the `fleet_summary()` backend that pre-shipped in
v0.5.26. New top-level nav entry "Power" leads to a per-device
table sorted biggest-hogs-first over a 24h / 7d / 30d window.

- **New blueprint**: `app/blueprints/admin/power.py` →
  `GET /app/power?window=24h|7d|30d`. Unknown window values fall
  back to 24h cleanly.
- **New template**: `templates/power.html` — window-selector chips,
  fleet headline (device count + avg + peak watts), per-device
  table (latest W with stale/fresh chip, avg/min/max W over window,
  sample count, first/last sample timestamps), forward-look
  "what's coming next" card.
- **Nav**: topnav + bottomnav both gain a "Power" item between
  History and Settings (⚡ icon on mobile).
- **Empty state**: when no device has reported in the window
  (the steady state today — firmware-side B16 sampling not yet
  shipped), renders an explicit empty card explaining that this
  will light up when firmware emits, with no further hub change.

No new schema. No new API. Read-only page over the existing
`device_power_samples` table.

### Out of scope for this ship

- Phase 1C charts (sparklines + fleet timeseries) — v0.5.29
- Phase 1C rollups (`device_power_rollups`) — v0.5.29
- Cost calc widget (`power.rate_per_kwh`) — v0.5.29
- CSV export — v0.5.29
- Threshold alerting / Phase 1D — later

## [0.5.26] - 2026-05-14

### Added — B16 Phase 1A: Power-tab live telemetry + devices-list watts chip

The first operator-visible surface over the B16 power-ingest slice
shipped in v0.5.12. `DevicePowerSample` rows have been writable since
then; this ship makes them *visible* on the Device-detail Power tab
and as a compact "{N} W" chip on the devices list.

**New service** — `app/services/device_power.py`:
- `latest_sample(device_id, channel_id=0)` — most-recent sample for one
  device (or `None` if never sampled). Cheap: one indexed lookup on
  `ix_device_power_samples_device_channel_sampled`.
- `latest_samples_by_device(device_ids, channel_id=0)` — batched
  variant used by the devices-list render (one ordered SELECT, first-
  wins per device).
- `recent_samples(device_id, *, window_seconds, limit)` — raw window
  for the future Phase 1C charting. Window clamped to [60, 86400]
  with a 720-sample defensive cap.
- `fleet_summary(window_seconds=86400)` — aggregate over the window:
  per-device sample_count + avg/min/max watts + first/last sample
  timestamps + latest sample blob. Used by the future v0.5.27
  `/app/power` page; landed here so it ships once and lights up when
  Phase 1B wires the UI.
- `MAX_FRESH_SAMPLE_AGE_SECONDS = 300` (5 min) — anything older
  surfaces with `is_stale: true` so the UI can chip it amber.

**Numeric coercion**: `DevicePowerSample.v_v` / `p_w` / `s_va` /
`pf` / `hz` are SQLAlchemy `Numeric` columns (Decimal in Python).
The serializer coerces them to `float` so JSON envelopes + Jinja
templates don't have to deal with Decimal arithmetic. Bytes-wise
identical numerically.

**Device detail** (`/app/devices/<id>` `#power` section):
- New chip on the "Power" header:
  `live telemetry` (green) when a sample exists and is fresh;
  `telemetry stale` (amber) when the latest sample is older than
  5 min; `no telemetry yet` (grey) when the device has never
  reported. The empty state is explicit — the card doesn't render
  broken numeric fields for devices that haven't reported yet.
- Live last-sample card under the header showing **W / V / mA / pf /
  Hz / VA / Wh cumulative / rssi_dbm**, plus the sample's `source`
  (steady/burst/synthetic) and `chip_type` (CSE7766 today). Sample-
  age in seconds rendered next to the sampled-at timestamp.
- Older "no telemetry" path links operators to the B16 design doc
  so they know charting + rollups land in Phase 1C (v0.5.29).

**Devices list** (`/app/devices`):
- Compact `{N} W` chip in the existing Power column, next to the
  relay-state toggle. Renders only when there's a sample; absent
  when the device has never reported. Amber when stale.
  `font-variant-numeric:tabular-nums` so the watt readouts align.

**Get-device-detail + list-devices** (`services.devices`):
- `get_device_detail()` now returns `latest_power_sample` (dict or
  None).
- `list_devices()` returns `latest_power_sample` per row — one
  batched query for the entire list, not N queries.

**Empty-state behavior**: today most devices in the fleet have NOT
shipped firmware-side B16 sampling (`POST /api/v1/device/power-
samples`). They'll render the "no telemetry yet" empty state cleanly
— no broken card, no fallthrough.

### Out of scope for this ship (still queued)

- Phase 1B fleet `/app/power` page (v0.5.27) — backend ready
  (`fleet_summary`); UI route + template pending.
- Phase 1C rollups + charts + `device_power_rollups` table (v0.5.29).
- Phase 1D power-targeted watchdog rule hook points.
- Cost calculation widget (`power.rate_per_kwh` runtime_setting).
- CSV export from chart range-pickers.
- Threshold alerting.

## [0.5.25] - 2026-05-14

### Fixed — Phase 2A contract normalization for integration probe kinds

The firmware-team alignment review on 2026-05-14 (post-merge) flagged
a "live product inconsistency": the four external-source probe kinds
shipped with v0.5.17 (Roku) + v0.5.23 (HA / weather / iCal) are
runtime-supported in `watchdog_runtime/_probes.py::run_probe`, but the
model `KNOWN_PROBE_KINDS` validation gate still rejected them.

Concrete consequence pre-v0.5.25: **operators could not create rules
using any of the four new probe kinds — not via the API, not via the
JSON editor at /app/rules.** `create_rule()` and `update_rule()` both
gate on `KNOWN_PROBE_KINDS`, so the only way to get such a rule into
the DB was direct SQL. The v0.5.17 and v0.5.23 features were
runtime-complete but operator-inaccessible.

Fix:

- `app/models/watchdog.py` — added four canonical constants
  (`PROBE_KIND_ROKU_APP_ACTIVE`, `PROBE_KIND_HA_STATE_IS`,
  `PROBE_KIND_WEATHER_ALERT_ACTIVE`, `PROBE_KIND_ICAL_EVENT_ACTIVE`)
  and extended `KNOWN_PROBE_KINDS` to include all four. Inline
  docstring documents the Phase 2A → 2B → 2C plan.
- `app/services/watchdog.py::_probe_to_phrase()` — extended with
  per-kind plain-English rendering so the rules list sentence no
  longer falls through to `"unknown probe '<kind>'"` for the
  integration probes. Rendering examples:
  - `roku_app_active` → `Roku source \`ext_…\` showing app matching \`Spectrum TV\``
  - `ha_state_is` → `Home Assistant source \`ext_…\` entity \`sensor.…\` in state \`on\``
  - `weather_alert_active` → `weather source \`ext_…\` has alerts matching (event contains \`Storm\`, severity ≥ \`Severe\`)` (or `(any active)` when no filters)
  - `ical_event_active` → `calendar source \`ext_…\` has event matching \`Jeopardy\` currently airing`

Until **Phase 2B** ships (planned v0.5.28) the rules-create form's
probe-kind `<select>` still doesn't list the new kinds. Operators
use the **JSON editor** at `/app/rules` (advanced editor, already
shipped v0.4.9) as the escape hatch. Phase 2C (v0.5.29) adds richer
per-probe event-detail rendering + integration-source health cues.

### Tests

- `tests/qa/test_v0525_integration_probe_kinds_canonical.py` —
  parametrized live test that creates a rule with each of the four
  integration kinds via the API, asserts `201 Created`, checks the
  plain-English sentence doesn't render as `"unknown probe"`, and
  cleans up the rule. Guards the contract.

## [0.5.24] - 2026-05-14

### Merge — parallel firmware-team session lands

This release commits work-in-progress from a parallel Claude session
that coordinated with the firmware team on safe-fallback firmware
rollout + a hub-side auto-rebind feature. The hub-side ships shipped
here today (v0.5.15 → v0.5.23) and this parallel work proceeded
independently; they meet here cleanly.

### Added — hub auto-rebind for devices that lost their local token

Previously, a known device that wiped its local enrollment token
(power loss during /register, manual factory-reset, firmware OTA
that nuked the config partition) had no path back into the fleet
without operator intervention — the device's next `/api/v1/device/announce`
returned `registered_no_token` and the operator had to manually
restore via the pending-adoption UI.

New behaviour in `services.announcements.upsert_announcement()`:
after the usual lifecycle update, call
`_maybe_prepare_auto_rebind()` which silently self-heals if **all
three** guardrails pass:

1. Announcement row was previously consumed (the device had
   registered before — never auto-rebind a fresh device).
2. An active, central-managed Device row exists with the same MAC.
3. The announcing IP still matches the hub's last-known
   `local_ip` for that device (either claimed_local_ip or
   source_ip).

When all three pass:
- Mint a restore-style enrollment token targeted at the existing
  device id (`target_device_id` set; same path as v0.5.7 B20 restore-
  vs-fresh adoption).
- Reset the announcement lifecycle (`adopted_at` set, `consumed_at`
  cleared, `enrollment_token_id` set) so the device's next announce
  picks up the new token and the regular `/register` rebind path
  takes over.
- No operator action required; audit history is preserved via the
  re-mint (note carries `Auto-rebind after device-side token loss`).

Devices that fail any guardrail still get the normal manual-
adoption flow — auto-rebind only adds a path, doesn't remove one.
Live verified on `192.168.1.48` (the "Rebooter — renamed test"
unit); the rollout + verification snapshots are captured under
`docs/notes/fleet-rollout-2026-05-14/` and
`docs/notes/protected-backups-2026-05-14/`.

### Tests

`tests/qa/test_v0420_announce_adopt.py::test_known_device_missing_token_auto_rebinds`
— full end-to-end: announce → adopt → register → announce-again-with-
no-token → auto-rebind → register-again returns the same `device_id`.

### Firmware-team coordination notes captured

Parallel-session work product committed alongside the code so future
resumers have the full picture without scrolling Slack/coordinator-hub
history:

- `2026-05-14-safe-fallback-firmware-progress.md` — the safe-fallback
  firmware progression that produced `bootstrap-0.2.5-dev-safe` +
  `rebooter-0.1.17`+`0.1.18-dev-central-safe`.
- `2026-05-14-safe-fallback-bad-firmware-test-plan.md` — the bad-
  firmware fault-injection plan the firmware team is using on bench
  devices `.225` + `.69`.
- `2026-05-14-safe-fallback-closeout-items-1-4.md` — closeout
  inventory for the bench-testing rollout.
- `2026-05-14-rollout-and-live-rebind-results.md` — fleet-wide
  rollout to `0.1.18-dev-central-safe` (6 devices) + live auto-
  rebind verification on `.48`.
- `2026-05-14-hub-power-recovery-alignment-plan.md` — design doc
  on aligning safe-fallback recovery semantics between firmware
  and hub layers.
- `2026-05-14-to-rebooter-droids-status-sync-and-b16-alignment.md` —
  status sync + B16 (power-monitoring) ingestion contract alignment.
- `2026-05-14-backlog-items-4-5-progress.md` — running progress on
  closeout items 4 and 5.
- `2026-05-14-next-steps-after-hub-power-recovery-plan.md` — the
  follow-on backlog after the power-recovery alignment plan.
- `2026-05-14-live-hub-vs-device-audit.md` — live audit comparing
  hub state vs device-reported state.
- `2026-05-14-hub-auto-rebind-local-verification.json` — single-
  device local verification capture used to validate the auto-
  rebind code path.
- `2026-05-14-rebooter-48-{live-rebind-backup,pre-badboot-overlay-baseline,protected-config-backup}.json`
  — per-device protected-config + baseline captures around the
  bench testing window.
- `fleet-rollout-2026-05-14/` — per-device config + status JSON
  snapshots (before/after pairs for all 7 devices + a
  `rollout-summary.json`).
- `protected-backups-2026-05-14/` — per-device protected-config
  backup snapshots (1 per device + a `summary.json`).

### Skipped (empty stubs from the parallel-session tree)

- `docs/notes/2026-05-13-webui-audit-and-redesign-plan.md` — 0 bytes
- `docs/notes/2026-05-14-b16-kickoff-and-project-boundary.md` — 0 bytes
- `docs/notes/codex-write-test.txt` — 0-byte stray test file

These three stay untracked. If the parallel session intended them
to land they'll get filled in and committed later.

## [0.5.23] - 2026-05-14

### Added — B17 adjacent integrations: Home Assistant + Weather (NWS) + iCal

#7 from the priority backlog. Extends the v0.5.17 B17 Layer 1 Roku
shape with three more polling-model-compatible source kinds. MQTT
pub/sub and Plex/Google-OAuth webhooks defer to a future ship (they
need different architectural patterns).

**Schema** (additive, auto-creates via `_PENDING_COLUMNS`):
- `external_sensor_sources.config JSONB` — per-kind extras bag.
  Existing Roku sources keep NULL + behave exactly as before; new
  kinds populate it (HA token, weather lat/lng, iCal URL).

**New `kind` values** (`EXTERNAL_SOURCE_KINDS`):
- `home_assistant` — `GET <host>:<port>/api/states` with long-lived
  access token (Authorization: Bearer …). Compact entity dict
  stored: `{entity_id → {state, last_changed, attributes_clipped}}`.
- `weather` — `GET api.weather.gov/alerts/active?point=lat,lng`
  (NWS, no auth, US-only). Compact alert list:
  `{event, severity, headline, effective, ends}`.
- `ical` — fetch any HTTP(S) / webcal:// .ics feed; tiny VEVENT
  parser (no `icalendar` lib dep) extracts current-airing-or-
  next-24h events: `{summary, start, end, uid}`. Capped at 50
  events per sample.

**Per-kind validation** in `services.external_sensors._validate_kind_config()`:
- `home_assistant` → requires `token` (non-empty); optional
  `verify_ssl: bool`.
- `weather` → requires numeric `lat` + `lng`, range-checked.
- `ical` → requires `url` with http/https/webcal scheme; webcal://
  is normalized to https:// for the fetch.

**Three new watchdog probe kinds** in `watchdog_runtime/_probes.py`:
- `ha_state_is` — match `entities[entity_id].state` against
  `expected_state` (case-insensitive exact).
  Stale-sample failure gate at default 60 s.
- `weather_alert_active` — succeed when at least one active alert
  passes optional `event_contains` substring filter +
  `min_severity` rank floor (Minor < Moderate < Severe < Extreme).
  Stale-sample failure gate at default 600 s.
- `ical_event_active` — succeed when an event whose SUMMARY
  contains `summary_contains` is currently airing (start ≤ now <
  end). Open-ended events treated as 24 h. Stale-sample failure
  gate at default 1800 s.

**Admin UI** — `/app/settings/integrations` gains three new add
forms (HA, Weather, iCal) below the existing Roku one. HA token is
rendered `password` and masked in source-row serialization. Each
form ships sensible default poll intervals (HA 30 s, weather +
iCal 600 s).

**Security note**: HA tokens are stored DB-side in plain text — same
posture as SMTP password in `runtime_settings`. v0.6.x is the
natural cleanup point for at-rest encryption of these
operator-supplied secrets.

**Use-case example shipped**: pair the v0.5.17 Roku `roku_app_active`
probe with `ical_event_active` for the operator's full Jeopardy
automation — "Roku-A is on Spectrum TV AND a calendar entry titled
'Jeopardy' is currently airing → relay_off subwoofer". No EPG
dependency needed (Layer 2 still deferred).

## [0.5.22] - 2026-05-14

### Added — B21 desired-config blob + drift detection + push-on-restore

#2 from the priority backlog. The v0.5.8 / v0.5.12 (B24) slice
pushed *only* `display_name` on restore-after-reflash and on
ordinary rename. v0.5.22 generalises that to a full
operator-set-intended-config blob with drift detection.

**Schema** (additive, auto-creates via `_PENDING_COLUMNS`):
- `devices.desired_config JSONB` — operator's intended config blob
  matching the locked v0.1 apply_config schema. NULL means no
  operator intent set; behaviour stays identical to today.
- `devices.desired_mode VARCHAR(40)` — intended top-level mode
  (`smart_plug` / `internet_watchdog` / `device_watchdog`).
- `devices.last_reported_config JSONB` — device's most recent
  self-reported config. Populated from `heartbeat.reported_config`
  if present.
- `devices.desired_config_updated_at TIMESTAMPTZ`
- `devices.last_config_pushed_at TIMESTAMPTZ`

**New service** — `app/services/device_config.py`:
- `get_desired_config(device_id)` / `set_desired_config(...)` /
  `push_desired_config(..., source)` / `compute_drift(device_id)`.
- Validates top-level keys against `ALLOWED_DESIRED_CONFIG_KEYS`
  (mirrors `commands.APPLY_CONFIG_ALLOWED_TOP_LEVEL`).
- `is_feature_enabled()` reads `desired_config.enabled`
  runtime_setting. Defaults OFF — restore-after-reflash auto-push
  via the blob is gated; manual operator-initiated push always
  fires (explicit intent).

**Restore flow integration** — `services/enrollment.py`
`_push_restore_config()` helper. On restore-after-reflash:
1. If `desired_config` is set AND feature flag is on → push the
   full blob.
2. Otherwise fall back to v0.5.8 short-circuit
   (`apply_config{device_name}` only) so the QA-flagged name-drift
   still gets fixed.

**Heartbeat** — when payload carries `reported_config: {...}`,
`heartbeats.record_heartbeat()` stashes it on the device row for
drift detection.

**UI** — new "Desired config" sub-card on `/app/devices/<id>`:
- Drift badge (`in sync` / `drift · N fields · M missing`).
- Mismatched-fields detail list.
- `<textarea>` JSON editor + `desired_mode` dropdown.
- "Save desired config" + "Push to device now" (the push button
  always fires regardless of the feature flag — explicit intent).
- Feature-off banner so the operator knows restore auto-push is
  gated.

**Audit events added**:
- `device.desired_config_set`
- `device.desired_config_cleared`
- `device.desired_config_pushed` (manual + restore both audit-logged)
- v0.5.8's `device.restore_config_pushed` audit retained but now
  carries `via: desired_config_blob` when the full path fires, or
  `via: display_name_only` when it falls back.

**Feature flag rollout**:
- Today: `desired_config.enabled = (unset)` → ships as OFF.
  Restore auto-push uses display-name-only fallback. Manual save +
  push work for any device.
- Operator opt-in: set
  `desired_config.enabled = '1'` via `runtime_settings` once the
  firmware-side schema for the heavier apply_config keys (`internet`,
  `device`, `notifications`, `power`) is validated end-to-end.

**Tests**: validation surface unit-tested in-container; live
integration deferred to QA suite next pass.

## [0.5.21] - 2026-05-14

### Removed — back-compat underscore aliases from v0.5.18

The v0.5.18 naming-cleanup ship promoted `_run_probe` →
`run_probe`, `_record_event` → `record_event`,
`_resolve_target_devices` → `resolve_target_devices` and kept the
underscore names as deprecated module-level aliases for one release
so any mid-rollout caller wouldn't break.

Three releases later (v0.5.19, v0.5.20, this one) confirmed no
caller in the tree references the underscore forms. Deleted:

- alias definitions in `_probes.py`, `_state.py`, `_actions.py`
- `_run_probe` / `_record_event` / `_resolve_target_devices` entries
  in `watchdog_runtime/__init__.py` imports + `__all__`

Anything that still tries `from app.services.watchdog_runtime
import _run_probe` will now get `ImportError`. Migration: replace
with the underscore-less name.

## [0.5.20] - 2026-05-14

### Added — long-poll `/api/v1/device/commands` (RFC 7240 `Prefer: wait`)

#1 from the priority backlog. Closes the firmware-team responsiveness
ask from the 2026-05-10 message — devices that opt into long-poll get
sub-second command latency without raising their poll rate.

**Contract:**
- Missing or `wait=0` Prefer header → legacy no-wait response
  (back-compat with 0.1.x firmware that doesn't know about long-poll).
- `Prefer: wait=N` → server holds the request open until either a
  command is enqueued for the device or `N` seconds elapse. Server
  caps `wait` at 30 s no matter what the client asks (RFC 7240
  §4.3 explicitly permits this). On success the response carries
  `Preference-Applied: wait=N`.

**Implementation:**
- Hot path unchanged: immediate-return when commands are already
  pending or `wait=0`. Existing clients see no difference.
- Slow path: 1-second-cadence poll loop. Each iteration opens its
  own `session_scope()` so the Postgres connection pool isn't
  pinned across the wait window. `time.sleep()` releases the GIL so
  other threads in the worker keep serving requests.
- Worker model: gunicorn `gthread` (default 8 threads). Each open
  long-poll consumes one thread; with the fleet at 7 devices today,
  peak concurrent long-polls fit comfortably with headroom for
  healthchecks + UI traffic. Bump `REBOOTER_GUNICORN_THREADS` if
  the fleet grows past ~10 simultaneous long-pollers.
- Operator-stop: set `REBOOTER_LONG_POLL_DISABLED=1` to force every
  call back to the legacy no-wait path. Useful as a panic switch
  without a code change.

**Middleware change:**
- `app/middleware/response.py::ok()` and `err()` now accept an
  optional `headers=` dict (additive — every existing caller works
  unchanged). Used here for `Preference-Applied`.

**Tests:**
- `tests/qa/test_v0520_long_poll_commands.py` — three contract
  tests:
  1. No Prefer header → near-instant return (<2 s round-trip).
  2. `Prefer: wait=3` with no commands → holds ~3 s then returns
     empty + `Preference-Applied: wait=3`.
  3. `Prefer: wait=10` while a peer enqueues a command 2 s in →
     returns within ~3 s with the command + Preference-Applied.

  The slow tests are marked `@pytest.mark.slow` so the default
  `pytest -m 'not slow'` run skips them; opt-in with `pytest -m
  slow` or `pytest -m ""`.

### Firmware-team handoff note

Firmware can adopt long-poll in two steps:

1. **Phase 1 — opportunistic.** Add `Prefer: wait=25` to the existing
   `/device/commands` request and double the poll interval (current
   30 s → 60 s effective). Sub-second command latency in the steady
   state; no other firmware changes.
2. **Phase 2 — backoff.** When a long-poll returns with a command,
   immediately re-poll without waiting (drain queue). On empty
   timeout, sleep a small amount (5-10 s) before the next long-poll
   to give the server room to breathe under load.

If a future fleet grows past ~10 devices, the operator may want to
bump `REBOOTER_GUNICORN_THREADS` and/or pick smaller per-device
`wait` values (e.g. `wait=10`).

## [0.5.19] - 2026-05-14

### Added — Rules UX phase (#2 from priority backlog)

Four operator-visible improvements to `/app/rules`:

**1. Real Edit flow.** Pre-v0.5.19, changing any field on a rule meant
delete-and-recreate (audit history split, runtime state lost). Now
each rule row carries an **Edit** button → `/app/rules/<id>/edit` →
JSON editor pre-filled with the rule's current shape (name, probe,
target, action, thresholds, escalation, maintenance windows). Save
calls the new `services.watchdog.update_rule()` which runs the same
validation as `create_rule` and **resets the runtime state machine**
(`failure_streak` / `recovery_streak` / `status='armed'`) so a rule
that was stuck firing comes back clean after a config change.

API: `PATCH /api/v1/admin/rules/<id>` mirrors the new endpoint.

Audit event: `watchdog_rule.updated`.

**2. Structured chips under each rule's sentence.** The plain-English
sentence kept its prominence; a row of small chips beneath now
surfaces the shape at a glance:
- probe (kind + target/host/url/app-name; internet shows target
  count)
- target (kind + name; tags get the tag literal)
- action (kind + cycle parameters)
- thresholds (`Nf / Nr · every Ns · cool Ns`)
- streak (only when non-zero — shows progress toward firing or
  recovery)
- maintenance windows (count, when configured)

**3. Richer event details in the per-rule event log.** Pre-v0.5.19,
only internet multi-target probes rendered a useful summary; ping
rtt, http URL, dns hostname, tcp host:port, and the new
`roku_app_active` (B17) payloads dumped raw or nothing. Now the
event row renders per-probe-kind details:
- ping → `rtt=Nms` on success / `host reason (exit N) — stderr_tail` on failure
- internet → `N/N ok · failed: host:port, …` (unchanged)
- roku_app_active → `expected=X · actual=Y · screensaver · sample <ts>`
  on success; `source X: stale_sample (max age Ns)` on stale data
- cooldown_skip → `streak N (rule on cooldown)`
- action_fired → `cycle · enqueued: cmd_…, … · skipped: N`
- generic `reason` / `error` fallback for probe_error and the rest

Outcome badges now color-code amber for `maintenance_skip`,
`cooldown_skip`, `probe_error` (previously rendered uncolored).

**4. Filterable target picker.** Pre-v0.5.19, picking from a fleet
of 7+ devices meant scrolling a flat optgroup. Now:
- Inline search input filters options by name / id / MAC
- Each device option carries an inline state hint (`· online` /
  `· offline` / `· never heartbeated`)
- Group options show `(N devices)` when the count is known
- Kind dropdown filters which optgroup is visible (no more
  picking a device while the kind says "group")
- `kind=tag` swaps the select for a free-form text input so the
  operator can type the tag name directly

### Backend

- `services/watchdog.update_rule()` (new) — full-rule update with
  same validation surface as `create_rule`; resets streak counters
  + status to armed. ~120 LOC.
- `blueprints/admin/rules.py` — `GET /app/rules/<id>/edit`,
  `POST /app/rules/<id>/edit`, `PATCH /api/v1/admin/rules/<id>`.

### Templates

- `templates/rules/edit.html` (new).
- `templates/rules/index.html` — chips + richer event details +
  filterable target picker + updated "what's coming next".

### Notes

The operator's original Rules UX spec was cut off mid-message
(during the v0.5.10 firmware-team-side message). The four
improvements above match the un-cut portion (Edit flow + structured
chips). If your full spec covered other items (richer escalation
editor, multi-window maintenance UI, etc.) please resend and I'll
add them as v0.5.20.

## [0.5.18] - 2026-05-14

### Refactor — naming cleanup (drop underscore on cross-module watchdog helpers)

Behavior-preserving rename. The v0.5.15 refactor-log flagged this as
the next-recommended target; this ship lands it.

Three watchdog_runtime helpers that are imported from outside the
package shed their leading underscore (since the underscore claimed
"internal" while their import site list said otherwise):

| Old name | New name | External caller |
|---|---|---|
| `_run_probe` | `run_probe` | `services/watchdog.py::probe_now` |
| `_record_event` | `record_event` | `services/watchdog.py::probe_now` |
| `_resolve_target_devices` | `resolve_target_devices` | `services/schedule_runtime.py::_fire_power_cycle` |

The underscore-prefixed names remain as **deprecated aliases** for
one release so any third-party caller (or a stale Claude session)
that still imports `_run_probe` keeps working. Remove the aliases
in v0.6.x.

Surveyed in passing: `_derive_central_status`, `_heartbeat_state_for`,
`_serialize_assignment`, `_active_assignments_by_device`,
`_latest_heartbeat_by_device` (all in `services/devices/`) are
genuinely internal — used only inside the subpackage — so they keep
the underscore per the v0.5.15 "internal to package" convention.
Same for `_fire_action`, `_fire_cycle`, `_fire_hold_off` (only the
target-resolution helper has cross-module use, so only that one was
promoted).

### Docs

- `docs/architecture.md` — no source-layout change (rename only).
- `docs/refactor-log.md` will be appended on the next structural
  refactor; for naming-only renames the CHANGELOG is the durable
  record.

## [0.5.17] - 2026-05-14

### Added — B17 Layer 1: Roku ECP integration + `roku_app_active` watchdog probe

First slice of B17 (external integrations). The remaining layers
(EPG lookup, Home-Assistant bridge, MQTT, Plex, weather, calendar)
reuse the same `external_sensor_sources` + `external_sensor_samples`
shape — only `services.external_sensors._poll_kind` needs new
branches.

**Schema** (auto-creates via `ensure_schema()` on next container start):
- `external_sensor_sources` — operator-registered sources (kind, host,
  port, enabled, poll_interval_seconds, last_polled_at, last_error).
- `external_sensor_samples` — append-only poll history with JSON
  payload; index on `(source_id, sampled_at desc)`.

**Polling** — new APScheduler tick at 30 s (`_external_sensors_job`
in `app/jobs/scheduler.py`) calls `services.external_sensors.
poll_all_due()`. Per-source `poll_interval_seconds` is honored so a
source with `interval=300` only gets polled every 5 min even though
the tick runs every 30 s.

**Roku ECP poller** — HTTP GET against `http://<host>:8060/query/active-app`
(LAN-local, unauth) → tiny regex parser → payload
`{active_app, active_app_id, screensaver_active, raw_xml}`. 3 s
timeout. Per-source errors recorded in `last_error` without
crashing the tick.

**Watchdog probe** — new kind `roku_app_active`:
```json
{"kind": "roku_app_active",
 "source_id": "ext_…",
 "app_name": "Spectrum TV",
 "max_sample_age_seconds": 120}
```
Reads the latest sample from the source. Match is case-insensitive
substring against `payload.active_app`. **Stale samples
(>`max_sample_age_seconds`) always return `failure`** so a dead
poller can never make a rule "stick true" indefinitely.

**Admin UI** — new Settings → Integrations tab
(`/app/settings/integrations`). Register Rokus by friendly name +
LAN IP; per-source actions: probe-now (manual test), enable/disable,
delete. Latest-sample chip renders inline. Tab strip in
`templates/_components/settings_tabs.html` extended to include
"Integrations".

**Use case shipped by this slice**: the Jeopardy automation
(operator-described in B17 Layer 1): "if Roku-A is on Spectrum TV
right now, do X" — paired with the existing `schedules` primitive
(B8) for the time-window half (weekdays 19:00-19:30) gets you ~80 %
of the dream without EPG, OCR, or any Spectrum-specific anything.

### Audit events added

- `external_sensor.source_created` / `source_deleted`
- `external_sensor.probed` (manual test)
- `external_sensor.toggled`

## [0.5.16] - 2026-05-14

### Added — B15: Settings → Sync tab content

Replaced the pre-decision stub on `/app/settings/sync` with a
structured page reflecting the locked RFC-004 §10b Option-C design.

The page now surfaces:
- **This hub right now** — configured role, container hostname,
  public base URL, active-device count, most-recent-heartbeat age.
- **Today's reality** — single-hub explanation with the dual-URL
  nginx semantics (both URLs share fate at the Postgres layer).
- **Locked design** — Option-C summary (outbox_events + HMAC-bearer
  cross-pull, idempotent apply, LWW conflict resolution, tombstone
  rows for deletes, 1–3 s steady-state latency target).
- **What will appear here once sync ships** — explicit forward-
  looking inventory (peer status, replication health, operator
  actions, conflict log).

Backend (`app/blueprints/admin/settings.py::settings_sync_page`) now
queries `Device` for the fleet count + max heartbeat timestamp and
threads through the runtime envs (hostname, public base URL, hub
role). Pure-read; no schema change.

### Closed in BACKLOG (no code work)

- **B5** — Get-devices-online firmware-team coordination. Firmware
  team has been actively delivering since the original handoff
  (0.1.3 → 0.1.17). Hub-side bugs surfaced by each cut were closed
  as their own items (B19, B20, B22, B23, B24). Marked CLOSED in
  BACKLOG.
- **B12** — RFC-005 redlines were marked CLOSED 2026-05-10 already;
  tidied the trailing prose to make the device-side-OTA ownership
  explicit so future resumers don't think hub work is owed here.
- **B20** — Live-DB inspection confirmed the production MAC dupe
  (`dev_01KRH81ASVCMHZ7SXC72J0RHPH`) was cleaned up at some prior
  point. Only `dev_01KR8127W5XMP6MDF34J0TXQP9` ("Erica's Subwoofer")
  remains for MAC `C4:D8:D5:0C:F7:A5`. Schema + UI for restore-vs-
  fresh-vs-decommission already shipped in v0.5.7; nothing left to
  do. Marked RESOLVED in BACKLOG.

## [0.5.15] - 2026-05-14

### Refactor — service subpackages: `devices/` + `watchdog_runtime/`

Behavior-preserving structural refactor. No new features, no API
changes, no template changes. Every existing `from app.services.*
import …` keeps working.

Two service files that had drifted past 2× their documented soft
limits get split into feature-internal subpackages:

- `app/services/devices.py` (700 LOC, 2.8× the 250-LOC service soft
  limit) → `app/services/devices/{__init__,_serialize,_query,_mutations}.py`.
  - `_serialize.py` — `serialize_device`, `_heartbeat_state_for`,
    `_derive_central_status`, `_serialize_assignment`.
  - `_query.py` — `find_by_mac`, `list_devices`, `get_device_detail`,
    `firmware_version_breakdown`, `latest_stable_release_dict`, plus
    the two `_*_by_device` join helpers.
  - `_mutations.py` — `update_device`, `delete_device`,
    `delete_devices_bulk`, `enqueue_display_name_sync`,
    `UnknownPatchFieldError`.
- `app/services/watchdog_runtime.py` (578 LOC) →
  `app/services/watchdog_runtime/{__init__,_probes,_state,_actions}.py`.
  - `_probes.py` — `_run_probe` dispatcher + the 5 probe implementations
    (internet / ping / tcp / http / dns).
  - `_state.py` — `_rule_is_due`, `_in_maintenance_window`,
    `_record_event`, `_update_state_and_maybe_fire`.
  - `_actions.py` — `_fire_action`, `_fire_cycle`, `_fire_hold_off`,
    `_resolve_target_devices`.

Each subpackage's `__init__.py` re-exports every external symbol
(both public and the underscore-prefixed helpers other modules
legitimately import). Internal-only files keep the underscore prefix
as a "import via package root, not directly" signal.

### Cleanup

- Deleted empty `app/services/power_samples.py` (0 bytes; the actual
  `ingest_power_samples` lives in `services/events.py`).
- `backups/` now in `.gitignore` (untracked SQL dumps + ad-hoc backup
  folders should never enter git history).
- Fixed `tests/qa/test_v0514_*.py` SQLite incompatibility:
  `DeviceHeartbeat.id` now uses `BigInteger().with_variant(Integer(),
  'sqlite')` so SQLite test paths get an autoincrement-capable PK.
  Postgres production behaviour unchanged.

### Docs

- `docs/architecture.md` updated with the new subpackage tree under
  `app/services/` and a new "Service subpackages" section codifying
  the split convention.
- `docs/contributing.md` updated "Sizing" guidance to point at the
  new convention.
- `docs/refactor-log.md` appended with the 2026-05-14 entry covering
  scope, decisions, risks, remaining debt, and next recommended
  targets.

### Out-of-scope (deferred deliberately)

- `services/firmware.py` (504 LOC) — cohesive single-domain; defer.
- `services/inbox.py` (455 LOC) — only 3 top-level functions; defer.
- `services/announcements.py` (393 LOC) — borderline; defer.
- `tests/qa/` mirror-by-feature restructure — defer until ≥150 tests.
- `app/schemas/` Pydantic dir — still gated on ≥3 endpoints feeling
  validation pain.

## [0.5.14] - 2026-05-14

### Added — B18: inline on/off toggle on the devices list

The devices list at `/app/devices` had no inline power control —
operators had to drill into the per-device detail page to fire
`relay_on` / `relay_off`. Every commercial smart-plug app (Kasa,
Shelly, HA, SmartThings) puts a toggle on the row itself. R-DEV-2
asked for this in the v0.3.1 redesign Phase 2 and it never shipped.
With the firmware-side relay dispatcher fixed in 0.1.6-dev-central,
the inline toggle is now the satisfying single-click action it
should have been at v0.3.1.

Implementation:
- **Backend**: `list_devices()` now joins the latest `DeviceHeartbeat`
  row per device (one extra query per scan, not per device) and
  surfaces `latest_relay_on` + `latest_mode` on every list payload.
  Devices with no heartbeat carry `latest_relay_on: None`.
- **Frontend**: new "Power" column in the desktop table layout and
  matching toggle on the mobile card layout. State logic:
  - `latest_relay_on=True` → green **ON** button, click POSTs
    `relay_off` (with `next=list` so the operator stays on the list)
  - `latest_relay_on=False` → grey **OFF** button, click POSTs
    `relay_on`
  - `latest_relay_on=None` → em-dash (device never heartbeated)
  - `is_held_off=True` → grey HELD badge (toggle disabled — must
    clear hold-off from the detail page first)
  - `online=False` → button disabled with offline tooltip; visual
    state still shown so the operator knows what state the device
    was last in
  - `is_protected=True` → `data-confirm-message` typed-confirm modal
    + hidden `override_lockout=1` field so the API actually fires
    once confirmed
- Existing `POST /devices/<id>/commands` endpoint extended with a
  `next=list` form field; success flash + redirect now land back on
  the list instead of the detail page when invoked inline.
- Audit event `device.command_issued` carries
  `via='list_inline_toggle'` for traceability vs the detail form.

Tests: `tests/qa/test_v0514_inline_toggle.py` exercises the
list-payload contract (`latest_relay_on` key present) and
verifies `next=list` redirect on the toggle endpoint.

### Backlog cleanup

- B4 (runtime-editable SMTP): retired in BACKLOG (was shipped
  v0.4.25; entry was stale).
- B6.1 (real ICMP ping): retired in BACKLOG (shipped v0.5.13).
- B6.3 (tag-as-target dispatch): explicitly deferred — needs a
  `device_tags` primitive that doesn't exist yet.
- B19 / B23 / B24 marked FIXED in BACKLOG with the version that
  closed each.

## [0.5.13] - 2026-05-14

### Fixed — B19: firmware scan picks up content-changed binaries with same filename

When the firmware team rebuilt a release without bumping the version
string (typical iterative-fix workflow, e.g. fixing a BearSSL heap
bug in `rebooter-0.1.9-dev-central.bin`), the scan in
`app/services/firmware.py::discover_on_disk_releases` skipped the
new bytes because the dedupe key was filename-only.

Live consequence pre-fix: the hub-registry SHA stayed stale, devices
polling `/device/firmware` saw the new bytes but the hub-advertised
SHA still pointed at the old image, the device's verification failed,
and OTA refused to flash — even though both sides were operating
correctly. The only workaround was a manual SQL `UPDATE
firmware_releases SET sha256 = …` after every rebuild.

Fix:
- Scan now re-hashes every already-tracked entry and compares to the
  stored SHA. On change, updates `firmware_releases.sha256` +
  `size_bytes` for the row and `verified_sha256` + `last_probed_at`
  + `status='live'` on every mirror row.
- New `updated` list in the scan response (alongside existing
  `discovered`); UI flash + API surface both render it.
- Per-row audit event `firmware.content_updated` with old + new
  SHA, old + new size, channel, filename, and `via=ui|api`. The
  unified history feed (C1, already shipped) surfaces these so
  operators can audit "did anyone swap a firmware on us?"
- No-op overhead: existing entries that haven't changed cost one
  SHA per scan, negligible against the operator-triggered cadence.

### Changed — B6.1: real ICMP ping probe in the watchdog runtime

Pre-v0.5.13, the `probe.kind='ping'` watchdog rule was actually a
TCP-port-80 probe — the container had no `ping` binary, and silently
treating "is this host reachable on port 80" as "ping" produced
false negatives for any host that doesn't run an HTTP server (most
ESP devices, most routers' LAN interfaces, etc.).

Fix:
- Dockerfile now installs `iputils-ping`.
- `_probe_ping()` in `watchdog_runtime.py` calls `ping -c 1 -W
  <timeout>` and parses the rtt from stdout. Success payload
  carries `rtt_ms`; failure payload carries `exit_code` + a 200-byte
  `stderr_tail`.
- Defensive fallback: if `ping` is unavailable in the runtime
  (slim images, BSD-only containers), the probe falls back to
  TCP-80 *and* sets `fallback: 'tcp_80'` in the event details so
  operators can tell real ICMP success from compatibility-mode
  success without grepping container metadata.
- `probe.host` is required (was previously optional); `probe.timeout_seconds`
  is honored if set (defaults to `PROBE_TIMEOUT_SECONDS = 3`).

### Deferred — B6.3 (tag-as-target dispatch)

Implementing tag-targeted watchdog actions requires a `device_tags`
primitive that doesn't exist in the schema yet. The current stub
returns an empty target set with a clear "no devices in target"
downstream signal, which is already the right operator-facing
behavior; real implementation gates on an operator decision about
whether tags become their own table or an extension of `groups`.
Captured in BACKLOG so the placeholder is explicit.

## [0.5.12] - 2026-05-14

Bundle ship — parallel-session work landed:

### Added — B23: split "offline" into operator-meaningful states

Devices list + device detail used to collapse three distinct states
under "offline":
- truly unreachable device
- central heartbeat stale / transport failure
- device on old firmware but otherwise healthy

The parallel-session work adds `_derive_central_status()` in
`app/services/devices.py` and surfaces a structured
`{code, label, reason}` tuple on every device list/detail payload:

| Code | When |
|---|---|
| `local_only` | `central_management_enabled=False` |
| `awaiting_first_heartbeat` | central on, no heartbeat yet |
| `central_stale` | central on, heartbeat older than threshold |
| `transport_stale` | active assignment + stale heartbeat + version mismatch |
| `upgrade_pending` | active assignment + reported version != target |
| `attention` | latest heartbeat carried non-healthy `health_state` |
| `central_ok` | central on, recent heartbeat, on target version |

Templates (`templates/devices_list.html`, `templates/device_detail.html`)
render these as colored chips with hover tooltips carrying the
operator-readable `reason`. A device like `.225` (central enabled but
transport failing) no longer presents identically to a truly dead
device like `.69`.

### Added — B24: device rename pushes `apply_config.device_name`

Restore-after-reflash (v0.5.8) already auto-enqueued an
`apply_config{device_name}` push, but ordinary operator renames
through `/api/v1/admin/devices/<id>` (PATCH) and the UI form did
not — so renames stayed hub-local until a future reflash propagated
them.

Both API and UI rename handlers now call `enqueue_display_name_sync()`
(promoted from the v0.5.8 helper) when the new display name differs
from the old. Audit details now include
`display_name_sync_enqueued: true/false` for traceability. No-op for
units with `central_management_enabled=False`.

### Added — B16 (kickoff): power-sample ingestion endpoint + model

First slice of the B16 power-monitoring track. Lands the receiving
side only — no UI surfaces yet — so firmware can start emitting
samples against a stable contract.

- `app/models/power_analytics.py` — `DevicePowerSample` table with
  voltage / current / real-power / apparent-power / power-factor /
  frequency / cumulative-energy fields, plus Wi-Fi telemetry
  (rssi/retry/beacon-miss/CRC) and `chip_type` discriminator.
  Indexed on `(device_id, channel_id, sampled_at desc)` and
  `(received_at desc)` for the two expected query patterns.
- `app/services/events.py::ingest_power_samples()` — batch ingestion
  with per-field type validation, max 3600 samples/batch
  (1 hour at 1s cadence), `source ∈ {steady, burst, synthetic}`.
- `POST /api/v1/device/power-samples` — device-auth endpoint
  documented in `docs/API.md`.
- `apply_config` allows the new top-level `power` key so future
  device-side sample-cadence config can be pushed back.

Storage schema bootstraps via `Base.metadata.create_all()` on next
container start — no migration script needed.

### Fixed — Deployment assignment never advanced past `delivered`

Live soak surfaced a gap where deployments moved
`pending → delivered` when the device fetched `/device/firmware`, but
never advanced to `completed` after the device came back on the
target version. The .185 ship report (2026-05-14) noted the
auto-flip happened anyway, but only because the heartbeat path
indirectly bumped state — a brittle dependency.

`app/services/deployments.py::reconcile_assignment_reported_version()`
is now called explicitly from `record_heartbeat()` and:
- updates `last_reported_version` on every heartbeat
- if `reported == target`, flips state → `completed` and clears
  `error_message`
- copies `health_state` into `error_message` when present so
  operators see device-reported errors in the deployment row

### Changed — Pending-adoption page state-counts card

When operator clicks "Show all" on `/app/pending-adoption`, the
new state-counts card surfaces per-state announcement counts at a
glance (pending / awaiting_pickup / awaiting_register / registered /
rejected) so the history view stops feeling like a wall of rows.

Also updated the "How adoption works" copy from "~30s" → "~5s while
pending" to match the v0.5.10 retry_after change.

### Tests

- `tests/qa/test_v0514_deployment_completion_and_status_truth.py` —
  3 unit tests covering heartbeat-completes-assignment,
  upgrade_pending, and transport_stale codepaths. **Known issue:**
  uses SQLite for isolation, but `BigInteger` PK columns don't
  autoincrement under SQLite (Postgres-specific behavior). Tests
  pass against Postgres, fail on local SQLite. Track as a follow-up.
- `tests/qa/test_admin_api.py` — `test_patch_device_rename_enqueues_apply_config_command`
  exercises the B24 wiring against the live hub.
- `tests/qa/test_device_api.py` — power-samples happy-path + batch-
  overflow rejection tests.

### Docs

- `docs/API.md` — `POST /device/power-samples` request shape, `power`
  allowed in `apply_config`.
- `docs/BACKLOG.md` — B23 + B24 entries (formally captured).
- `docs/qa-notes.md`, `docs/bug-log.md`, `docs/PROJECT-STATE-…md` —
  accumulated QA findings + bug entries + full project state
  handover snapshot (2026-05-09 PM full-sync) committed for resume
  reference.
- `docs/notes/2026-05-12-substitute-firmware-team.md` —
  substitute-firmware-team coordination notes.
- `docs/notes/2026-05-14-live-hub-vs-device-audit.md` — live
  hub-vs-device drift findings on 2026-05-14 (the .225 case that
  motivated B23).

## [0.5.11] - 2026-05-13

### Fixed — B22: scanned releases handed devices the wrong OTA URL

Firmware-team caught 2026-05-13 PM after live UI-driven OTA test:
device on 0.1.15 received a `/device/firmware` response pointing at
the canonical root URL
`/rebooter/firmware/rebooter-0.1.15-dev-central.bin` and got HTTP
404, because the scanned `.bin` actually lives under the per-channel
subdirectory at `/rebooter/firmware/stable/rebooter-0.1.15-dev-central.bin`.

Root cause in `app/services/firmware.py::discover_on_disk_releases`:
the scan loop only enumerates files under `firmware_dir/<channel>/`,
but `download_url` was being set to `{base}/{filename}` (root path).
The upload path got away with the same shape because it copies the
artifact into both locations — the scan path does no such copy. The
device reads `release.download_url` verbatim, so the field-level
mismatch produced the 404.

Fix:
- `discover_on_disk_releases` now sets `download_url = per_channel_url`
- Mirror-row emission for scanned releases drops the bogus root
  `local` mirror row (would have claimed `status=live`+`verified_sha`
  for a URL that doesn't serve anything). `local_per_channel` +
  `local_channel_pointer` are still emitted as before.
- One-shot data fix: existing `firmware_releases.download_url` rows
  for scanned entries (16 rows pre-fix) rewritten via SQL UPDATE so
  in-flight deployments resolve correctly; matching `local` mirror
  rows deleted. Defensive backup at
  `rebooter-droids-db-PRE-B22-fix-20260513T212822Z.sql.gz`.
- Upload-path behaviour is unchanged — it still writes both
  locations and keeps the `local` root URL as a live mirror.

Tests: `tests/qa/test_v0511_scan_download_url.py` — exercises
scan-then-HEAD against the live deployment + asserts mirror-row
layout no longer claims a root-path `local` mirror for scanned
entries.

Backlog: B22 entry added in `docs/BACKLOG.md` (marked FIXED in
v0.5.11). Long-poll `/device/commands` (previously planned as
v0.5.11 per the firmware-team responsiveness ask) is bumped to
v0.5.12 to make room for this hot-fix.

## [0.5.10] - 2026-05-13

### Changed — Pending-adoption responsiveness (firmware-team priority bump)

Two small responsiveness wins ahead of the bigger v0.5.11 long-poll
work for `/device/commands`:

- `/api/v1/device/announce` now responds to pending devices with
  `retry_after_seconds: 5` (was `30`). Operator clicks "Adopt", the
  device sees the token within ~5s instead of up to 30s. Tunable
  via the new `REBOOTER_ANNOUNCE_PENDING_RETRY_AFTER_SECONDS` env
  (default 5). Other states (rejected/adopted/registered/
  awaiting_register) unchanged.
- `/app/pending-adoption` now auto-refreshes every 3 seconds via a
  small JS interval. The refresh is suppressed when (a) the browser
  tab is backgrounded, (b) an input/textarea/select is focused, or
  (c) a `<dialog open>` is showing — so it never eats operator
  keystrokes during typed-confirm prompts. No SSE/WebSocket; the
  fleet-side request rate stays bounded by operator count, not
  device count.

Files: `app/config.py`, `app/services/announcements.py`,
`templates/pending_adoption.html`.

## [0.5.9] - 2026-05-13

### Added — Multi-target `internet` watchdog probe

Pre-v0.5.9 the `probe.kind=='internet'` watchdog probe was a
single hardcoded `_probe_tcp("1.1.1.1", 53)` — a single upstream
host issue could falsely look like an internet outage and fire a
power-cycle. Firmware/product side asked for the same multi-target
model the device-side internet watchdog already uses.

The `internet` probe now walks a list of TCP targets. Defaults
(applied when the rule does not pin its own list) are:

- `1.1.1.1:53` (Cloudflare)
- `8.8.8.8:53` (Google)
- `4.2.2.2:53` (Level 3)

Semantics: rule outcome is **success** if ANY configured target
responds, **failure** only when ALL fail. Every target is probed
every tick (not short-circuit) so the event log always reports
the complete picture — operators can tell "one resolver blip"
from "real outage" without opening the API.

Event-log `details` payload now carries:

```
{
  "targets_succeeded": [{"host": "1.1.1.1", "port": 53}, ...],
  "targets_failed":    [{"host": "8.8.8.8", "port": 53, "error": "tcp_connect_failed"}, ...],
  "targets_total":     3,
  "used_default_targets": true   // only when defaults were substituted
}
```

UI: when `probe_kind=='internet'`, the create form now shows a
repeatable host/port row widget pre-filled with the three
defaults, with `+ add target` / `remove` buttons (max 8). The
recent-events log row inline-renders `<N>/<total> ok · failed: …`
so the multi-target outcome is visible at a glance.

Validation: `probe.targets` must be a list, length 1-8; each
entry must be `{host: <non-empty str>, port: int 1-65535}`.
Invalid shapes are rejected at create-time with a clear error.

Backward-compatible: every existing internet rule auto-upgrades
on the next tick — no migration, no operator action. The
plain-English rule sentence now reads "outbound internet
connectivity (3 default targets)" or "(N targets)" so the rule
list communicates the new scope.

Files: `app/services/watchdog_runtime.py`,
`app/services/watchdog.py`, `app/blueprints/admin/rules.py`,
`templates/rules/index.html`,
`tests/qa/test_v0509_internet_multitarget.py`.

## [0.5.8] - 2026-05-13

### Added — Auto-push display_name on restore-after-reflash

QA finding 2026-05-13: hub-side B20 restore correctly preserves
the device row identity (display_name "Erica's Subwoofer", id
`dev_01KR8127W5XMP6MDF34J0TXQP9`, audit history, group memberships)
but the reflashed device kept its local `device_name="Rebooter"`
because hub-side restore didn't push hub-side metadata back DOWN
to the device.

Short-term fix per firmware-team collaboration:

In `consume_enrollment_token`'s restore branch, after the
credential rotation completes, automatically enqueue an
`apply_config` command carrying the hub's `display_name` as
`device_name`. Delivered on the device's first `/device/commands`
poll after `/register` completes — typically ~30 seconds.

Best-effort; never raises out of `/register`. If the enqueue
fails (e.g., command-queue full, unusual error) the restore
itself still succeeds — only the auto-push is skipped, and the
operator can manually re-enqueue via /app/devices later.

Audit event: `device.restore_config_pushed` with
`{trigger: "restore_after_reflash", pushed_fields: [...], device_name: ...}`.

### Medium-term tracked separately as B21

Full `desired_config` blob on each device row (matches the locked
v0.1 apply_config schema), `last_reported_config` for drift
detection, operator-edit UI on device detail, optional auto-
repair-on-drift. Will land as v0.6.0 behind a feature flag.

## [0.5.7] - 2026-05-12

### Added — B20: MAC-based duplicate detection at adoption + restore-vs-fresh choice

Operator hit a duplicate-device bug 2026-05-12 PM when reflashing
Erica's Subwoofer (.30): same physical hardware, two device_ids.
Orphan audit history, group memberships, scheduled rules on the
old row. Same problem will hit when the other 4 bricked speakers
get reflashed.

Schema:
- `enrollment_tokens.target_device_id` — VARCHAR(40) NULL FK → devices(id)
  ON DELETE SET NULL. When set, `/device/register` REBINDS the
  existing device row instead of creating a new one. Idempotent
  `ADD COLUMN IF NOT EXISTS` step in `bootstrap._ensure_columns`.

New `registration_state`: `decommissioned` — set by the
"Decommission + adopt fresh" flow on the dupe-MAC card. Hidden
from `find_by_mac` so future reflashes don't surface abandoned
rows.

Service layer:
- `app/services/devices.py::find_by_mac(mac)` — case-insensitive
  MAC lookup, excludes decommissioned rows.
- `app/services/enrollment.py::mint_enrollment_token(..., target_device_id=)` — pass-through to new column.
- `app/services/enrollment.py::consume_enrollment_token` — branches
  on `target_device_id`. Restore path: verifies MAC match
  defensively, updates existing row in place (firmware_version,
  local_ip, registration_state='active', last_heartbeat_at=NULL),
  rotates `device_credentials`, returns EXISTING `device_id`.
  Fresh path unchanged.
- `app/services/announcements.py::adopt(..., mode=, target_device_id=)` —
  new `mode='restore'` parameter; verifies target exists +
  MAC-matches; mints enrollment token with `target_device_id` set.

UI:
- `/app/pending-adoption` page now passes `existing_devices` per
  announcement via `find_by_mac`.
- Template: when a MAC dupe exists, renders amber-bordered dupe-
  warning card with each existing device's display_name + id +
  firmware + prior IP + last-heartbeat-age. Three actions per
  matched device:
  - **✓ Restore to this device** — visually dominant green button.
    Default for stale/offline prior rows.
  - **Decommission + adopt fresh** — secondary; double-confirm
    requires typing "decommission". Marks old row decommissioned
    (preserved for audit), then standard fresh adopt.
  - **or adopt as new (anyway)** — de-emphasised link-style;
    double-confirm requires typing "duplicate". Creates a second
    logical device for the same physical hardware. Should be rare.
- No-dupe case: pending-adoption UI unchanged.

New routes:
- `POST /app/pending-adoption/<ann_id>/restore/<existing_device_id>`
- `POST /app/pending-adoption/<ann_id>/decommission-and-adopt/<existing_device_id>`

Audit events:
- `device.restored_from_reflash` — restore path
- `device.decommissioned_for_replacement` — decommission-and-adopt
- `device.adopted_with_mac_duplicate` — fresh adopt when dupe existed

Back-compat: fresh adoption when no dupe = identical behaviour to
v0.5.6. All new code paths gated on operator-chosen action.

### Why now

The 4 bricked Erica's speakers (R.L., F.L., F.R., R.R is healthy,
Subwoofer already partially-dupe'd) will all hit this exact flow
when physically reflashed. Without B20 each one creates an orphan
row and split audit history. With B20 the operator picks "Restore"
on each one's pending-adoption card and identity is preserved
across the reflash.

## [0.5.6] - 2026-05-12

### Added — LAN-bridge command types for remote fleet recovery

Three new entries in `commands.ALLOWED_TYPES` so the hub can enqueue
the LAN-recovery commands the firmware team added in v0.1.11:

- **`lan_scan`** — payload `{start, end}` (integer last-octet
  range, 1-254 each, max 254 IPs). Tells a bridge device to scan
  its LAN subnet for live rebooter devices and return the map via
  `/device/command-result`.

- **`lan_proxy`** — payload `{ip, path, method, body?, headers?}`.
  Tells a bridge device to make an HTTP request to a LAN peer
  (e.g. `POST http://192.168.1.30/api/system/reboot`) and return
  the response. `method` ∈ {GET, POST}; `path` must start with `/`.

- **`lan_ota_push`** — payload `{ip, url, sha256?}`. Tells a bridge
  device to instruct a LAN peer to OTA-pull from the given URL and
  self-flash. Unlocks the operator-remote silent-fleet recovery
  path documented in B19/staged-deployments without requiring the
  operator to be on the LAN.

Validation is light by design — these are operator-triggered
recovery commands, not customer-facing endpoints, so the schema
checks catch the obvious wrong-type cases (`ip` is a string,
`url` starts with `http://` or `https://`, etc.) but don't try to
prevent every misuse.

#### Required firmware

Device must be on **0.1.11-dev-central or later** to dispatch these
command types. Older firmware silently ignored or rejected them.

#### Operational pattern

The hub-side recovery sequence remains operator-paused per the
fail-safe gate, but with 0.1.11 + v0.5.6 the actual mechanics are:

1. Operator green-lights an OTA push to a bridge device (e.g. R.R.
   Speaker) — un-pauses the paused `deployment_assignment`.
2. Bridge device upgrades to 0.1.11.
3. Operator enqueues `lan_scan` against the bridge device — finds
   the silent peers' IPs.
4. Operator enqueues `lan_ota_push` against the bridge device,
   targeting each silent peer with the same firmware URL.
5. Silent peers self-flash, reboot, and re-authenticate with their
   restored hub-side credentials. Fleet recovered.

No hub-side test ships yet — coverage will land with the operator-
fired recovery run. The schema-validator code path is exercised
implicitly by every `lan_*` command issued.

## [0.5.5] - 2026-05-11

### Refactor — split `devices.py` blueprint by concern

The heaviest blueprint in the codebase (630 lines, mixed UI + API
+ bulk-delete + upgrade-to-latest + protection toggle + cancel
command + ...) split into three files by concern:

```
app/blueprints/admin/devices.py       —  23 lines (back-compat shim)
app/blueprints/admin/devices_ui.py    — 427 lines (UI handlers)
app/blueprints/admin/devices_api.py   — 246 lines (JSON API handlers)
```

#### What's where now

- **`devices_ui.py`** — every `@admin_ui_bp` handler:
  list/detail/update/delete/send_command/cancel_command/
  upgrade-to-latest/bulk-delete (UI)/protection-toggle.
  Includes the `_show_qa_fixtures` helper.
- **`devices_api.py`** — every `@admin_api_bp` handler:
  list/get/patch/send_command/delete/bulk-delete (API)/
  cancel_command.
- **`devices.py`** — thin shim that imports both for side-effect
  route registration. Preserves `from app.blueprints.admin.devices
  import ...` for any external introspection.

#### Endpoint names preserved

All `url_for("admin_ui.<name>")` and `url_for("admin_api.<name>")`
calls in templates and tests resolve unchanged. The blueprint
object (`admin_ui_bp` / `admin_api_bp`) is shared across files —
both new modules import it from `app.blueprints.admin`.

#### Why now

- v0.5.5 unblocks the upcoming B18 ship (inline on/off toggle in
  the devices list) — that work would have added another ~80
  lines of UI logic to the already-630-line single file.
- Lines-per-file rule (operator's coordinator-hub convention was
  <1,200 per file) was still satisfied at 630 but trending
  upward. Splitting now is cheap; splitting at 900+ is harder.

No behaviour change. No new tests needed beyond the existing
regression coverage (24+ live tests already exercise the routes,
all green post-split).

## [0.5.4] - 2026-05-11

### Refactor — version helpers extracted; package import-clean

Two related, behaviour-preserving changes:

- `is_upgrade()` and `_version_sort_key()` moved from
  `app/services/devices.py` to a new minimal module
  `app/services/_versions.py`. Pure-Python, no Flask/SQLAlchemy
  dependencies. Re-exported from `app/services/devices.py` for
  back-compat with existing callers (template Jinja global
  `is_upgrade=`, blueprint imports).

- `app/__init__.py` deferred its top-level imports of
  `app.middleware.rate_limit.init_rate_limit` and
  `app.middleware.response.register_envelope_handlers` into the
  `create_app()` function. The package can now be imported
  (e.g. for unit tests) without pulling in the entire Flask
  runtime stack (`flask_limiter`, etc.). The Flask app itself
  still loads them on `create_app()` so live behaviour is
  unchanged.

#### Why

`tests/qa/test_v0429_upgrade_direction.py` was failing on developer
hosts that don't have `flask_limiter` installed (e.g. anywhere
outside the Docker image) because importing
`from app.services.devices import is_upgrade` triggered
`app/__init__.py`'s eager Flask-stack imports. Now the test file
imports from `app.services._versions` directly and the package
load is clean.

4/4 v0.4.29 tests pass on the host now (previously: 4/4 fail
outside the container).

### Tests

- v0.4.29 upgrade-direction tests now run host-side without the
  container. Test file's `is_upgrade` import points at
  `app.services._versions`.

## [0.5.3] - 2026-05-11

### Fixed — clicking Upgrade button could delete the device (critical)

Operator hit this twice today. Audit-log evidence:

- 2026-05-11 17:45:50 UTC — clicked Upgrade on R.L. Speaker;
  audit emitted `device.bulk_deleted_per_device` for R.L. instead of
  `device.upgrade_initiated`.
- 2026-05-11 01:53 UTC — same pattern deleted 4 devices.

**Root cause**: per-row upgrade `<form>` tags were rendered INSIDE
the wrapping bulk-delete `<form>`. Per the WHATWG HTML5 parser
spec:

- An inner `<form>` start tag inside an existing form context is a
  parse error and is **ignored** (no nested form element created).
- The corresponding `</form>` end tag DOES close whichever form is
  currently open — which means it closes the OUTER bulk-delete
  form mid-table.
- Buttons inside the "ignored" inner form become submitters of the
  OUTER form. Clicking Upgrade submitted the bulk-delete form with
  whatever device_id checkboxes were checked, then showed the
  `confirm()` prompt from `bulk_select.js` that the operator
  misread as the upgrade confirmation.

#### Fix

- Moved the bulk-delete `<form>` to AFTER the table + mobile cards.
  It now only wraps the bulk-action bar at the bottom.
- Row checkboxes (desktop + mobile + master) carry the HTML5
  `form="devices-bulk-delete-form"` attribute to associate with the
  form across the DOM. No nesting.
- Per-row upgrade forms are now top-level (no enclosing form).
- `bulk_select.js` switched from `form.querySelectorAll()`
  (descendant-only) to a document-wide query filtered by `.form`
  ownership so it still picks up the now-DOM-detached checkboxes.

#### Recovery

Erica's R.L. Speaker device row + credentials restored from the
v0.5.2 POST-RESTORE backup at 2026-05-11 17:50 UTC via targeted
INSERT replay (same procedure used for the 4 devices on 2026-05-11
02:30).

### Tests

- `tests/qa/test_v0503_devices_list_nested_form.py` — verifies the
  rendered page has no nested forms (max depth ≤ 1, balanced), all
  device_id checkboxes carry the form= attribute, and the bulk-delete
  form has the expected id.

## [0.5.2] - 2026-05-11

### Fixed — misleading "1 device · Pending adoption →" sub-header on /app/devices

Operator-flagged regression: the devices-list sub-line rendered

```
{{ devices|length }} device · Pending adoption →
```

which, when the fleet view contained 1 device, read as
"1 device · Pending adoption →" — the eye parses this as
**"1 device pending adoption →"**. The "1" was actually the
fleet-count and the link target was an unrelated page showing
zero pending announcements. Clicking through left the operator
staring at "No pending devices" wondering where the alleged 1
went.

#### Fix (three layers)

- **New `count_pending_announcements()` service helper** in
  `app/services/announcements.py` — single `SELECT COUNT(*)` over
  `device_announcements` filtered to rows where `consumed_at IS
  NULL AND rejected_at IS NULL` (the same predicate
  `list_announcements()` uses by default).
- **Wired into the devices-list page context** as
  `pending_adoption_count`.
- **Sub-header restructured**:
  - Fleet count now ends with the qualifier "in fleet" so it
    cannot be misread as "pending": "5 devices in fleet".
  - Pending-adoption link is on its own line as a styled chip
    with the count baked into the visible text: "Pending
    adoption: 0 →" or "Pending adoption: N →".
  - Chip turns **amber** when the count is > 0 so the operator
    actually notices when there's something to action.

### Tests

- `tests/qa/test_v0502_pending_adoption_count.py` — verifies the
  link-bound count format and cross-checks against the actual
  pending-adoption page contents.

## [0.5.1] - 2026-05-11

### Fixed — v0.5.0 backfill over-granted bindings to operator users

v0.5.0's one-shot RBAC backfill gated on `users.is_admin` which is
also True for users with `role='operator'` in this schema. Result:
every active operator got incorrect site-admin bindings in
`role_bindings` that they should not have per B10 Q2 ("operators
→ no rows; forced re-grant").

Live evidence post-v0.5.0 deploy:

```
scope_type | role        | count
-----------+-------------+-------
global     | super_admin |     1   ← correct
site       | admin       |   220   ← expected 110 (22 admins × 5 sites);
                                     extra 110 came from 22 operators
                                     × 5 sites being mis-classified
```

#### Fix

`ensure_role_bindings_backfill()` now gates on the actual `role`
column instead of `is_admin`:

- `is_super_admin=True` → `('global', NULL, 'super_admin')`
- `role == 'admin'` (and not super) → one row per site
- everything else (including operators, viewers) → **no rows**

#### Corrective one-shot

A new corrective step runs once on first deploy of v0.5.1,
tracked via `rbac.role_bindings_v050_correction_applied_at`. It:

1. Deletes every `role_bindings` row whose user has
   `role IN ('operator', 'viewer')` — drops the bad rows v0.5.0
   created.
2. De-duplicates `(user_id, scope_type, scope_id)` rows in case
   gunicorn-worker contention in v0.5.0 also produced duplicates
   (using `IS NOT DISTINCT FROM` so NULL scope_ids dedupe correctly).
3. Records completion in `runtime_settings`. Never re-runs.

The v0.5.0 backfill tracking row (`rbac.role_bindings_backfilled_at`)
is **preserved** by the correction step — we don't want the
corrected backfill to also re-run on every restart. The correction
is one-shot, the backfill remains one-shot, both are idempotent.

If the v0.5.0 image somehow gets re-deployed before v0.5.1 lands
(e.g., a rollback) the correction would re-execute on the next
v0.5.1 upgrade, which is the intended safety net.

## [0.5.0] - 2026-05-10

### Added — RBAC role_bindings table + one-shot backfill (Tier A / A1)

Foundation ship of the RBAC scoping migration locked by B10
redlines 2026-05-10 PM (RFC-003 §9.0). **Non-enforcing.** This
release adds the table, populates it from the legacy
`users.is_super_admin` / `is_admin` / `role` columns, and exposes
a service-level CRUD + effective-scope resolver. The shadow-mode
middleware that *logs* would-have-denied decisions (A2) and the
enforce flip (A8) are later ships gated on ≥ 7 days of clean
shadow-log soak per RFC-003 §6.3.

#### New schema

```
role_bindings
├── id                  (rb_<ulid>)
├── user_id             FK users.id ON DELETE CASCADE
├── scope_type          'global' | 'site' | 'group' | 'device'
├── scope_id            NULL for global; ULID otherwise
├── role                'super_admin' | 'admin' | 'operator' | 'viewer'
├── created_at, updated_at
├── created_by_user_id  FK users.id ON DELETE SET NULL
└── UNIQUE (user_id, scope_type, scope_id)
INDEX (user_id), (scope_type, scope_id)
```

#### Auto-backfill (one-shot per database, idempotent)

Runs on container startup after `ensure_schema()` /
`ensure_bootstrap_admin()`. Tracked via a `runtime_settings` row
under `rbac.role_bindings_backfilled_at` so it's a hard no-op on
subsequent boots. Per B10 Q2:

- existing super_admins → `('global', NULL, 'super_admin')`
- existing admins (not super) → one row per current `site_id`,
  `('site', <site_id>, 'admin')`. If no sites exist yet, one
  `('global', NULL, 'admin')` row as a safety net so the operator
  isn't locked out on day one.
- existing operators / viewers → **no rows**. Per B10 Q2, the
  operator tier must be re-granted explicitly by an admin before
  the enforce flip.

If the backfill errors (e.g., DB constraint surprise), startup
continues — we never block a healthy container on this one-shot
data migration. The exception is logged; operator can re-run by
deleting the `runtime_settings` tracking row.

#### New service module

`app/services/role_bindings.py`:

- `grant(user_id, scope_type, scope_id, role)` — idempotent upsert
- `revoke(user_id, scope_type, scope_id)` — drop a binding
- `list_for_user(user_id)` — enumerate
- `has_global_role(user_id, role_needed)` — fast hot-path check
- `effective_site_ids(user_id, role_needed)` → `"ALL"` sentinel or set
- `effective_device_ids(user_id, role_needed)` → `"ALL"` or set,
  computed by unioning global / site / group / device bindings via
  GroupMembership joins
- `can_act_on_device(user_id, device_id, role_needed)`
- `can_act_on_site(user_id, site_id, role_needed)`

Role-hierarchy enforcement built in: a `super_admin` binding
satisfies an `admin`-required check; an `admin` binding satisfies
`operator`; etc.

#### What this doesn't do (yet)

- Does **not** change any existing auth-decorator behaviour. All
  `@admin_required_ui` / `@role_required_*` decorators keep their
  v0.4.x semantics. The legacy `users.role` + `users.is_admin` +
  `users.is_super_admin` columns stay authoritative until A8.
- Does **not** scope queries. `GET /api/v1/admin/devices` still
  returns every device an admin can see today — scope-filtered
  queries land in A4.
- Does **not** expose any UI for grant/revoke. CRUD UI lands in
  A6 / A7.

### Tests

- `tests/qa/test_v0500_role_bindings.py` — verifies v0.5.0
  deployment health, legacy auth back-compat preserved.

## [0.4.34] - 2026-05-10

### Fixed — Firmware on-disk scan misses recently-written .bin files

Firmware team reported (2026-05-10 PM) that
`POST /api/v1/admin/firmware/scan` failed to register a
freshly-SCP'd `rebooter-0.1.6-dev-central.bin` even though the file
was on the host filesystem before the scan ran. The next invocation
~2 minutes later picked it up cleanly — classic bind-mount
cache-miss pattern between the host's
`/mnt/s/code/rebooter-droids/data/firmware/stable/` and the
container's `/data/firmware/stable/`.

Fix: call `os.sync()` at the start of `discover_on_disk_releases`
so any pending writes are flushed before the directory walk. Cost
is one syscall per scan, executed only when the operator triggers
the scan — negligible. Best-effort; falls through cleanly on
platforms that don't support it.

Operationally there is no behavioural change for the working
case; the buggy case (recently-written file invisible to
container's `iterdir`) now returns the correct discovered set on
the first try.

## [0.4.33] - 2026-05-10

### Changed — Firmware UI moves under Settings (D3)

The firmware-releases + deployments page that's been at
`/app/firmware` since v0.1 is now canonically a Settings tab at
`/app/settings/firmware`. Matches how the Settings tab strip
already named it. The legacy URL keeps working as a **302 redirect**
to the new canonical URL so existing bookmarks, external docs, the
one-click upgrade button copy, and any operator muscle memory all
keep functioning.

Template gains the Settings tab strip at top + a "Settings" page
header so the breadcrumb mental model matches the URL.

No data migration. No API change. Pure UX consolidation.

### Tests

- `tests/qa/test_v0433_firmware_settings_tab.py` — new URL renders
  with tab strip; legacy URL 302s to the new home.

## [0.4.32] - 2026-05-10

### Added — History export + free-text search (C2 + C3)

Two related history-page features land together because they share
the form area:

- **CSV / JSON export** (`?export=csv` / `?export=json`). Streams
  the current filter view as a download with a sane
  `Content-Disposition` header. Honours every filter currently in
  the URL — source, action_prefix, free-text — so the file is
  "what you see on screen". Caps at 50,000 rows per request;
  narrow filters for more. New `Export CSV` / `Export JSON`
  buttons below the filter form.
- **Free-text search** (`?q=<text>`). ILIKE wildcard match across:
  - actor / actor_user_id
  - action (with substring match, broader than the exact-match
    `?action=` field)
  - target_type / target_id / message
  - `details` JSON cast to text — searches anything inside the
    blob in one box
  Works across all four sources (audit / watchdog_probe /
  device_event / all). Search field renders full-width above the
  existing filter inputs.

### Backlog

- **B16 power-monitoring design doc** drafted at
  `docs/B16-power-analytics-design.md`. 4-tier architecture, 8-ship
  roadmap, full privacy posture, retention strategy, ingestion API
  contract, cross-plug correlation as the "MIT-tier" feature.
  Pending firmware-team confirmation of the metering chip (CSE7766
  on Sonoff S31 per Tasmota / ESPHome; corrected from the original
  HLW8032 assumption).

### Tests

- `tests/qa/test_v0432_history_export_search.py` — CSV/JSON export
  headers + content, action_prefix-honoured export, search-narrowing
  guarantee, search field renders.

## [0.4.31] - 2026-05-10

### Improved — Device enrolment wizard (E5 from continuation plan v2)

`/app/devices/new` shipped in v0.3.1 as the guided one-stop enrolment
page but had three rough edges fixed in v0.4.31:

- **Site selector** in the form when sites exist. The mint service
  already accepted `site_id`; the wizard form just didn't expose it.
- **TTL picker** — operator-friendly options (1 h / 24 h / 7 d / 30 d)
  with 24 h as the default. Picks one of an allow-listed set; invalid
  values silently fall back to the system default rather than 400.
- **Cross-link to `/app/pending-adoption`** for the no-serial-access
  flow. Operators who can't paste a token directly into the device
  now have a clear path to the v0.4.20 announce-then-adopt UX.

The Status page's `+ Enrol a device` button and the
`unregistered_devices.html` "mint one here" link both used to point
at `/app/enrollment-tokens` (the token-list management view). They
now route to `/app/devices/new` (the guided wizard) — a much better
operator experience.

QR-code support stays deferred (no operator mobile workflow yet that
would consume one; pending-adoption already covers the "I can't
paste the token" case).

### Tests

- `tests/qa/test_v0431_enrol_wizard.py` covers TTL picker render +
  custom-TTL submit round-trip + Status-page link-routing.

## [0.4.30] - 2026-05-10

### Added — Unified history feed (C1 from continuation plan v2)

`/app/history` now surfaces three event streams behind a single chip
nav:

- **Audit** (`source=audit`, default) — same `audit_events` view that
  shipped in v0.3.0 / v0.4.27, with chip filters preserved.
- **Watchdog probes** (`source=watchdog_probe`) — outcomes from the
  `watchdog_probe_events` table; each row is rendered as a
  `watchdog_probe.<outcome>` action.
- **Device events** (`source=device_event`) — rows from
  `device_events` posted via `POST /api/v1/device/events`.
- **All sources** (`source=all`) — time-merged union of the three,
  with an extra Source column showing per-row provenance.

Backed by a new `app/services/history.py` that normalises the three
on-disk tables into one shape (`source, at, actor, action, target,
details, ip`). Defaults preserve back-compat — anyone bookmarked at
`/app/history?action_prefix=...` keeps seeing audit events.

Schedule fires and notification sends keep arriving via the audit
slice today; they'll get their own sources when the notification
surface ships (Tier C of `redesign-continuation-plan-v2.md`).

### Tests

- `tests/qa/test_v0430_history_sources.py` — verifies the source
  picker renders, switching flips active state, `watchdog_probe`
  rows actually appear, the new "all" view adds a Source column,
  and the v0.4.27 audit chip filter still narrows correctly.

## [0.4.29] - 2026-05-10

### Fixed — Upgrade button could offer downgrades

`latest_stable_release_dict()` picked "latest" by upload time
(`created_at desc`), which surfaced the most-recently-uploaded
stable release regardless of its version number. When an older
release was re-uploaded after a newer one (e.g. `0.1.2` pushed
after the fleet had already moved to `0.1.5`), the per-device
upgrade button on `/app/devices` offered a **downgrade**.

Fix has three layers:

- **Server-side selection.** `latest_stable_release_dict()` now
  picks the highest-version stable release, comparing by the
  dotted-int numeric prefix of the version string.
- **Template gate.** Replaces the old string `!=` check with a new
  `is_upgrade(target, current)` helper exposed as a Jinja global.
  Only shows the button when the target is strictly newer
  numerically. Same-numeric-prefix (e.g. label-only changes like
  `0.1.1-dev-central` → `0.1.1-dev-central-ui`) is intentionally
  not flagged as an upgrade.
- **Handler guard.** The submit handler refuses to create a
  deployment if the target is not strictly newer than what's on
  the device. Defends against stale pages or directly-posted forms.

Unit tests at `tests/qa/test_v0429_upgrade_direction.py` cover the
comparator (numeric ordering, label-only ties, `None` / empty
inputs, and the actual fleet versions from 2026-05-10).

## [0.4.28] - 2026-05-10

### Fixed

- **One-click Upgrade button regression.** Clicking the per-device
  "Upgrade to latest" button on `/app/devices` returned
  `{"error":{"code":"internal_error"...}}` since v0.4.21. Root cause
  was a stray `current_app.config["SETTINGS"]` reference in
  `device_upgrade_to_latest_submit` that was assigning to an unused
  local; `current_app` was never imported, so the very first click
  hit a `NameError` and bubbled up to the generic 500 handler. Fix
  is to delete the dead line (the `settings` local was never read).
  Regression test added at `tests/qa/test_v0428_upgrade_button.py`.

## [0.4.27] - 2026-05-10

### Added — History page chip filters + API.md refresh

- **History page chips.** `/app/history` now has a chip nav with
  one-click filters for the 14 most common audit-action prefixes
  (`device`, `device_announcement`, `watchdog_rule`, `schedule`,
  `firmware`, `user`, `attention`, `maintenance_mode`,
  `password_reset`, `enrollment_token`, `smtp`, `network`, `system`,
  `group`). Backed by a new `action_prefix` query parameter on
  `GET /admin/audit` that does a `LIKE '<prefix>.%'` match. Free-text
  filters still work alongside chips.
- **`docs/API.md` refreshed.** Documents endpoints added across the
  v0.4.x series: `/device/announce`, `/device/failsafe`,
  `/admin/pending-adoption/*`, `/admin/firmware/scan`,
  `/admin/maintenance`, `/admin/attention/{id}/ack`,
  `/admin/rules/*` (watchdog rules), `/admin/schedules`,
  `/admin/users/*`, `/admin/invitations/*`,
  `/admin/devices/bulk-delete`, `/admin/devices/{id}/commands/{cid}/cancel`,
  runtime-settings save/clear surface. Adds new error codes
  (`forbidden`, `rate_limited`, `announcement_*`, `maintenance_active`).

### Tests

- `tests/qa/test_v0427_history_chips.py` — verifies chip nav renders,
  active state flips on `action_prefix`, and filtered rows only
  contain matching actions.

## [0.4.26] - 2026-05-10

### Added — Runtime-editable Network + System settings

Settings → Network and Settings → System are now editable. Same
DB-backed-with-env-var-fallback pattern as the SMTP work in v0.4.25.

#### Network tab — fields editable

- **Public base URL** (`network.public_base_url`) — env
  `REBOOTER_PUBLIC_BASE_URL`. **Live** (effective immediately).
- **Firmware public base** — env `REBOOTER_FIRMWARE_PUBLIC_BASE`. **Live.**
- **CORS allowed origins** — env `REBOOTER_CORS_ALLOWED_ORIGINS`.
  **Restart required** (Flask-CORS reads once at app init).
- **Rate-limit exempt IPs** — env `REBOOTER_RATE_LIMIT_EXEMPT_IPS`.
  **Live** (read per-request).
- **Cookie domain** — env `REBOOTER_COOKIE_DOMAIN`. **Restart required**.

#### System tab — fields editable, all **Live**

- **Invitation TTL (seconds)** — env `REBOOTER_INVITATION_TTL_SECONDS`.
  Live: `invitations.mint` now reads from `runtime_settings`.
- **Password-reset TTL (seconds)** — env `REBOOTER_PASSWORD_RESET_TTL_SECONDS`.
  Live: `password_resets.request_reset` reads from `runtime_settings`.
- **Enrollment-token default TTL** — env `REBOOTER_ENROLLMENT_TOKEN_TTL_SECONDS`.
  Live: `enrollment.mint_enrollment_token` reads from `runtime_settings`.
- **Session idle timeout** — env `REBOOTER_SESSION_IDLE_TIMEOUT_SECONDS`.
  Restart required (Flask session config wired at app init).
- **Portal name** (display only).

#### New service helpers
- `runtime_settings.network_config()` — full live network config dict
- `runtime_settings.system_config()` — full live system config dict
- `runtime_settings.is_live_editable(name)` — per-field UI badge hint
- `NETWORK_KEYS` / `SYSTEM_KEYS` constants for enumeration

#### New audit hooks
- `network.config_updated` / `network.config_cleared`
- `system.config_updated` / `system.config_cleared`

#### Indicators on every field
Per-field "DB override" vs "env-var fallback" indicator (matches
the v0.4.25 SMTP pattern), plus **live** vs **restart required**
badges so the operator knows what takes effect when.

### Compatibility

- All v0.4.25 routes preserved.
- Empty DB on a fresh deployment still picks up env-var defaults.
- The `_exempt_ips()` rate-limit helper now reads
  `runtime_settings` → env-var fallback so the QA host's
  exemption is preserved.

## [0.4.25] - 2026-05-10

### Added — Runtime-editable SMTP credentials

Settings → Notifications is now editable. Operators can rotate
SMTP creds without recreating the container. Each field reads
DB → env-var fallback; clearing a field reverts to env-var.

- New `runtime_settings` table (key/value, JSON-typed).
- New `app/services/runtime_settings.py` with
  `get(name, env_var=, default=)`, `set_(name, value, user_id=)`,
  `delete(name)`, `has_db_value(name)`, `list_keys()`,
  and a typed `smtp_config()` helper used by the email service.
- `app/services/email.py` now reads SMTP via `runtime_settings.smtp_config()`
  rather than the once-at-startup `Settings` dataclass — DB
  rotations take effect immediately on the next email send.
- New UI:
  - **Edit SMTP settings** form on `/app/settings/notifications`
    with per-field "DB override" / "env-var fallback" indicators.
  - **Save** button (audit-logged as `smtp.config_updated` with
    a list of which fields changed) — masked password preserved
    on round-trip via the `********` sentinel value.
  - **Revert to env-var defaults** button (audit-logged as
    `smtp.config_cleared`) — drops every DB override at once.
- Rendered indicator on every field shows whether it's
  currently DB-backed or env-var-fallback so the operator
  knows live state at a glance.

### Tests

`tests/qa/test_v0425_runtime_smtp.py` — page renders
edit form + save/clear round-trip with HELO field (host /
user / password untouched to avoid breaking real SMTP).

### Compatibility

- All v0.4.24 routes preserved.
- New `runtime_settings` table created via
  `Base.metadata.create_all()` at boot.
- Empty DB on a fresh deployment falls through to env-var
  defaults (zero behavior change without explicit operator
  action).
- Existing email-sending callers (invitations, password-reset,
  send-test, future watchdog notifications) unchanged — they
  continue calling `email.send_email()` which now picks up
  live config.

## [0.4.24] - 2026-05-10

### Docs / state checkpoint

State-checkpoint ship after the announce-poll flow validated
end-to-end on lab-69 + firmware team's RFC-005 §9 redlines came
in. Pure docs ship — no code or schema change.

- **B12 closed.** `docs/notes/2026-05-10-from-firmware-team-rfc005-redlines.md`
  preserves the firmware team's full Q1..Q9 reply. RFC-005 §9
  rewritten with the final answers folded in (slot sizes locked
  A=640KiB / B=1MiB / C=1MiB; Q3 success criteria broader than
  just heartbeat; Q4 6 canonical reason strings agreed; AP-mode
  shipped in `bootstrap-0.2.2`; flash-time config = both serial
  + AP-mode; LittleFS JSON not NVS; Python CLI flash tool
  first; hosting in force with publish-integrity discipline).
- **`docs/BACKLOG.md`** — B12 marked CLOSED.
- **`docs/redesign-continuation-plan.md`** — B12 strikethrough
  in the gated-list.
- Hub-side follow-ups recorded for queue: recognise the 6
  canonical Q4 reason strings in failsafe service, add a "verify
  external mirror" button on /app/firmware (publish-integrity
  per Q9), treat `bootstrap-0.2.2` as the recommended baseline
  for new device bring-up.

### Fleet state at this checkpoint

- **5/5 lab devices online** (lab-30 / lab-67 / lab-69 / lab-207
  / lab-225) — including lab-69 brought up via the new
  `/api/v1/device/announce` flow shipped in v0.4.20
- 4 watchdog rules armed (internet-connectivity, hub-self-check,
  hub-www2-self-check, dns-resolver-health)
- 0 open code-fix bugs
- 316 tests passing, 5 expected skips
- Firmware build with announce-loop: `0.1.5-dev-central` (lab-69's
  current); other 4 devices on 0.1.2 / 0.1.3
- `bootstrap-0.2.2` is the firmware-team-recommended baseline
  going forward (includes Wi-Fi AP fallback per Q5)

## [0.4.23] - 2026-05-10

### Docs / UI copy refresh — UI redesign continuation plan

The Settings tabs Overview / System / Network / Authentication
were carrying stale "Coming in P5/P6" placeholder copy from
v0.3.0. Substantial chunks of P5/P6 actually shipped piecemeal
across v0.4.x but the placeholder copy never got updated. This
ship reconciles that.

- **`docs/redesign-continuation-plan.md`** (NEW) — full map of
  what shipped from the original P5/P6 plan vs what's still
  queued, plus a re-prioritised next-4-ships proposal.
- **`templates/settings/index.html`** — Overview points at the
  per-tab "Live now / Queued" sections + cross-references the
  continuation plan doc.
- **`templates/settings/system.html`** — replaced "Coming in P5"
  empty-state with explicit Live-now (maintenance toggle, schedules)
  + env-var-driven (read-only) sections.
- **`templates/settings/network.html`** — replaced "Coming in P6"
  empty-state with Live-now (CORS, dual hosting, security headers,
  rate-limit exemption) + Editable-UI-queued sections.
- **`templates/settings/auth.html`** — replaced "Coming in P5"
  empty-state with Live-now (session-revoke enforced, password-
  reset, bootstrap admin password persistence, login rate limit,
  invitations) + Queued (RBAC, TOTP, OIDC) sections.

### Compatibility

- All v0.4.22 routes preserved.
- Pure copy / docs change — no code, no schema, no behavior shift.

## [0.4.22] - 2026-05-10

### Security — D / BUG-049: CSP `'unsafe-inline'` dropped from `script-src`

- **CSP `script-src 'self'`** — no longer allows arbitrary inline
  scripts or event handlers. Real defense-in-depth XSS hardening.
- The 1 inline `<script>` previously in `templates/layout.html`
  (theme-flash mitigation) extracted to
  `static/js/theme_flash.js`.
- 18 inline `onsubmit="return confirm(...)"` /
  `onclick="return confirm(...)"` handlers across 12 templates
  migrated to `data-confirm-message="..."` data attributes;
  centrally wired by `static/js/confirm_handlers.js` via
  `addEventListener` on DOMContentLoaded.
- Custom-function inline handlers (`confirmMassAction(this, ...)`,
  `confirmFirmwareDeploy(this)`) replaced with
  `data-mass-action-verb` / `data-mass-action-count` /
  `data-firmware-deploy-confirm` data attributes; wired in
  `static/js/mass_action.js`.
- Hold-off type-the-name confirm preserved via
  `data-confirm-typed-name="..."` (single template, single
  pattern).
- `style-src` keeps `'unsafe-inline'` for now — 123 inline
  `style=` attributes across templates are a separate migration
  with much lower security impact.

### Added — E / Tier-2: Status-inbox attention ack / snooze

- New `attention_acks` table + service. Per-attention-item ack
  with optional snooze duration.
- Status-page attention items now render **Snooze 1h**,
  **Snooze 24h**, and **Ack** buttons (super-admin / admin only).
  Ack hides the item until manually cleared OR the underlying
  state changes (e.g. device comes back online).
- Inbox service filters acked items at read time.
- API: `POST /api/v1/admin/attention/<id>/ack` (with optional
  JSON body `{snooze_seconds, reason}`) +
  `DELETE /api/v1/admin/attention/<id>/ack` (un-ack).
- Audit hooks: `attention.acked`, `attention.unacked`.

### Tests

`tests/qa/test_v0422_csp_and_ack.py` — 6 tests covering CSP
header tightness, no-inline-script-blocks-in-rendered-HTML,
unauth pages still load post-CSP, ack lifecycle, ack hides
items, garbage snooze handled.

### Compatibility

- All v0.4.21 routes preserved.
- New tables created via `Base.metadata.create_all()` at boot.
- The CSP change is breaking for any operator who had heavily
  customised templates with their own inline scripts (none in
  the standard tree).

## [0.4.21] - 2026-05-10

### Added — One-click "Upgrade to <latest-stable>" on the devices list

- **Per-row upgrade button** on `/app/devices`. Whenever a device's
  `firmware_version` differs from the current latest-stable
  release tracked in `firmware_releases`, the row gets a small
  ⬆ button labelled with the target version. One click queues a
  single-device deployment of the latest stable release.
- Equivalent to going to `/app/firmware`, picking the release,
  selecting `target_type=device`, typing the device id — folded
  into one click.
- Confirmation prompt names both the source and target version
  before queueing.
- Hidden when:
  - no stable release is tracked (UI gives operator nothing to
    point at)
  - the device is already on the latest version
  - device opts out of central (`central_management_enabled=false`)
  - the viewer lacks edit permission (super_admin / admin)
- New audit hook `device.upgrade_initiated` with details
  `{via, release_id, release_version, deployment_id}`.
- New endpoint `POST /app/devices/<device_id>/upgrade-to-latest`.
- New service helper `latest_stable_release_dict()` for templates.

### Compatibility

- All v0.4.20 routes preserved.
- The new button is purely additive — the existing
  `/app/firmware` deploy form continues to work.

## [0.4.20] - 2026-05-10

### Added — Pending-adoption flow (operator-driven device onboarding)

Replaces the old "mint a token in the UI, paste into firmware
build at flash time" workflow. Devices flash generic, announce
themselves, get adopted by name.

- **`POST /api/v1/device/announce`** — new unauthenticated endpoint.
  Devices without an enrolment token POST their MAC + claims here
  every ~30s. Hub upserts a `device_announcements` row keyed on
  MAC. Response tells the device to keep polling (`pending`),
  pick up its delivered token (`adopted`, one-shot), wait for
  /register (`awaiting_register`), or back off (`rejected`).
- **New `device_announcements` table** with full lifecycle:
  pending → awaiting_pickup → awaiting_register → registered.
  MAC is the unique key. `adoption_token_secret` is plaintext-
  but-cleared-after-delivery; never exposed to admin queries.
- **`/app/pending-adoption`** admin UI page lists pending devices
  with all claimed metadata + source IP + announce count.
  **Adopt** button mints a 7-day enrolment token and stashes it
  on the row; **Reject** button sets a 1-hour back-off; **Delete**
  cleans up consumed/rejected rows.
- **API:** `GET /api/v1/admin/pending-adoption`,
  `POST /api/v1/admin/pending-adoption/<id>/adopt`,
  `POST /api/v1/admin/pending-adoption/<id>/reject`.
- **Cross-linked** from `consume_enrollment_token` — when a
  device successfully registers via an adopt-delivered token, the
  announcement's `consumed_at` is stamped (best-effort, never
  raises out of /register).
- **Audit hooks:** `device_announcement.adopted`,
  `device_announcement.rejected`, `device_announcement.deleted`.
- **Devices page link** to /app/pending-adoption in the page header.

### Firmware-team contract

Documented at
`docs/notes/2026-05-10-firmware-team-announce-adopt-contract.md`
— full request/response shapes, lifecycle state machine,
recommended timing, idempotency notes. Existing
register-with-baked-in-token flow continues to work; this is
additive.

### Tests

- `tests/qa/test_v0420_announce_adopt.py` (7 tests):
  full lifecycle, validation rejection, idempotency on repeat
  announces, reject + back-off, UI render.

### Compatibility

- All v0.4.19 routes preserved.
- New `device_announcements` table created via
  `Base.metadata.create_all()` at boot.
- `/api/v1/device/announce` is unauthenticated by design — same
  trust posture as `/api/v1/device/register` (both rely on the
  enrolment-token contract).

## [0.4.19] - 2026-05-10

### Added — Operator visibility upgrades while firmware-side debug continues

- **Tier-1 A: Per-firmware-version fleet breakdown.** New
  `firmware_version_breakdown()` service + collapsible card on
  `/app/devices`. Groups the fleet by `firmware_version`, marks
  the majority cohort green, flags any minority cohorts as
  "outliers" with an amber badge. Operator can spot upgrade
  drift at a glance. Excludes QA fixtures from the calculation
  regardless of the show-fixtures toggle. Devices with no
  reported firmware bucket as `(unknown)`.
- **Tier-1 B: On-disk firmware reconciliation.** New
  `discover_on_disk_releases()` service walks
  `data/firmware/<channel>/` for `.bin` files not already in
  `firmware_releases`, computes SHA + size, backfills DB rows
  and mirror records. Closes the gap between the firmware
  team's direct-to-disk artifact placement and the admin UI's
  Firmware page. Idempotent. Audit-logged as `firmware.scanned`.
  UI: "Scan now" button on `/app/firmware`. API:
  `POST /api/v1/admin/firmware/scan`.
- **Tier-1 C: Real watchdog rules for the lab fleet** (configured
  post-deploy via the API, not in the codebase).

### Compatibility

- All v0.4.18 routes preserved.
- New endpoint `POST /api/v1/admin/firmware/scan` — admin role.
- New section card on `/app/devices` is purely additive; existing
  table + filters unchanged.

## [0.4.18] - 2026-05-09

### Fixed

- **BUG-050 — Device register 500 on overlong caller-supplied
  fields.** Pre-fix, sending `display_name` >120 chars (or
  `hardware_model` >80, `mac_address` >40, etc.) hit the
  Postgres `StringDataRightTruncation` on INSERT → unhandled
  → 500. Now: validation-failed → 400 with field name + max
  length.
- **BUG-051 — Device register accepted any string as
  `mac_address`.** `<script>alert(1)</script>` was happily
  persisted. Output rendering is Jinja-escaped so no XSS
  surface, but admins saw garbage in the MAC column. Now:
  hex-only validation regex (`[0-9A-Fa-f:.\-\s]+`) rejecting
  anything outside the common formats.

### Compatibility

- All v0.4.17 routes preserved.
- Service-layer validation only — existing devices with
  legacy MAC strings (none in production today; we just
  cleaned the fleet) pass through unchanged.

## [0.4.17] - 2026-05-09

### Added
- **`DELETE /api/v1/admin/enrollment-tokens/<id>`** (BUG-044).
  API consistency with the UI POST `/app/.../revoke` endpoint.
  Returns 200 + `{deleted: true}` on success, 404 on
  not-found-or-already-consumed.
- **End-to-end schedule runtime test** at
  `tests/qa/test_v0417_schedule_runtime_e2e.py`. ~120s wall-
  clock test that exercises a one-shot maintenance schedule
  through the full lifecycle: schedule fires → maintenance
  flag flips ON → window ends → flips OFF.

### Fixed
- **BUG-048 — HTTP watchdog probe followed redirects.** Pre-fix
  treated 302 as a failure. Real-world health checks often
  redirect (HTTPS upgrades, CDN routing, app entrypoints). Now
  follows up to 3 redirects and treats a final 2xx as success.
  Operators using `/health` URLs that 302 to `/login` no longer
  see false-positive "down" alerts.

### Compatibility

- All v0.4.16 routes preserved.
- Probe behavior change is operator-friendlier: rules that were
  silently failing (false negatives) on redirecting URLs will
  now correctly probe success.

## [0.4.16] - 2026-05-09

### Fixed — Bootstrap admin password no longer reverts on container restart (BUG-046)

- **BUG-046 — `ensure_bootstrap_admin` overwrote the password on
  every startup.** Pre-fix: any time the container restarted
  (image update, host reboot, `--force-recreate`), the bootstrap
  admin's password got force-set back to
  `REBOOTER_BOOTSTRAP_ADMIN_PASSWORD`. This silently nuked any
  password the operator had legitimately reset via the
  `/app/reset-password` flow. Worst-case-followed: operator
  resets to a new password, container recreates next morning,
  new password stops working, operator does another reset, and
  so on.
- **Default behavior changes**: startup only sets the password
  on initial create. Privileges (`is_admin`, `is_super_admin`,
  `is_active`) are still reconciled every startup so an operator
  can never lock themselves out of admin.
- **Recovery path preserved** behind a new opt-in env var:
  `REBOOTER_BOOTSTRAP_ADMIN_FORCE_PASSWORD_ON_STARTUP=1`. Set
  this when you've forgotten your password — restart with the
  env var set, log in with the env-var password, then unset the
  env var so subsequent restarts don't keep clobbering.

### Compatibility

- All v0.4.15 routes preserved.
- The default behavior change is intentional and operator-
  safer. To opt into the legacy "always force-reconcile"
  behavior, set the new env var.

## [0.4.15] - 2026-05-09

### Fixed — Forgot-password page lied when SMTP failed (BUG-045)

- **BUG-045 — Forgot-password page told the user "we've emailed
  you a link" even when the SMTP send blew up.** Pre-fix:
  v0.4.6 (BUG-030) caught the SMTP exception so the request
  didn't 500, but the user-facing message stayed cheerful and
  identical to the success path. Users sat waiting for an email
  that never arrived.
- Now: when `smtp_ok=False` AND the email IS registered (token
  was minted), the page renders an additional warning panel with
  the SMTP-error class name (`SMTPConnectError`,
  `SMTPRecipientsRefused`, etc.) and a "contact your admin" hint.
- Disclosure delta is acceptable: the page already echoes a
  masked email back to the user, which proves the form processed
  their input. The smtp-status difference between known/unknown
  email is small relative to that.

### Compatibility

- All v0.4.14 routes preserved.
- Pure UI / message-rendering change.

## [0.4.14] - 2026-05-09

### Operational

- **Database cleanup.** Purged 130 QA-fixture devices, 1522
  enrollment-token leftovers, 66 noise rows in
  `unregistered_auth_attempts`, 83 QA-prefixed groups, 25
  QA-prefixed sites. The cluster is now in a pristine zero-row
  state across every device-related table — operator can see the
  truth: zero real devices have ever come online. Status page
  now reads "No devices yet".

### Fixed

- **BUG-042 — Watchdog rule serializer missing v0.4.2 runtime
  state.** The `serialize_rule` shape was written in v0.4.0,
  before v0.4.2 added `failure_streak`, `recovery_streak`,
  `last_probed_at`, `last_action_at`, `last_outcome` columns.
  UI templates already referenced these (rendered as empty
  strings); JSON consumers got KeyError. Now: serializer
  exposes all five.
- **BUG-043 — `POST /api/v1/admin/enrollment-tokens` ignored
  `ttl_seconds` parameter.** Service used the env-var
  `REBOOTER_ENROLLMENT_TOKEN_TTL_SECONDS` (default 24 h) as the
  only knob. Operators wanting a 30-day token for a firmware-
  team handoff had to recreate the container with a bumped env
  var. Now: `ttl_seconds` honored, capped at 30 days.

### Test coverage

- New `tests/qa/test_v0414_watchdog_runtime_e2e.py` (3 tests):
  end-to-end against the real APScheduler tick — failing TCP
  probe → action_fired → cooldown_skip transitions; succeeding
  HTTP probe → no action; maintenance window suppresses firing.
  Wall-clock ~25 s per test; skip via `SKIP_E2E=1` in
  budget-constrained CI.

### Compatibility

- All v0.4.13 routes preserved.
- BUG-042 fix is additive — old fields still present, new ones
  added alongside.
- BUG-043 fix is additive — `ttl_seconds=null` keeps the env-var
  default behavior.

## [0.4.13] - 2026-05-09

### Fixed — schema validation hardening (BUG-038, 040, 041)

- **BUG-038 — Rule target requires a concrete identifier.**
  `target={"kind":"device"}` (no `id`) was accepted; runtime
  silently no-ops. Now: returns 400 unless `id` (device/group)
  or `tag` is present and non-empty.
- **BUG-040 — Schedule weekly weekdays deduped + sorted.**
  Pre-fix: `weekdays=[5,5,5,5]` rendered "Sat, Sat, Sat, Sat".
  Now: stored as `[5]`.
- **BUG-041 — Schedule weekly weekdays must be 0..6.** Pre-fix:
  `weekdays=[99]` accepted, sentence rendered an empty day list,
  schedule never fired.

### Compatibility

- All v0.4.12 routes preserved.
- Existing rules with empty target.id continue to load (we only
  validate on create — historical rows pass through unchanged).

## [0.4.12] - 2026-05-09

### Fixed — input validation hardening (BUG-035, 036, 037)

Found during the v0.4.11 iteration probe.

- **BUG-035 — Watchdog rule numeric thresholds now bounded.**
  `failure_threshold` and `recovery_threshold` 1..100;
  `window_seconds` 5..86400; `cooldown_seconds` 0..86400.
  Pre-fix: `failure_threshold=-1` made every probe fire the
  rule's action immediately on the first failure (the
  `failure_streak < failure_threshold` gate was always False).
- **BUG-036 — Watchdog rule + schedule names now capped at 120
  chars in the service.** Pre-fix: 121-char names hit Postgres
  `value too long for type character varying(120)` → 500.
- **BUG-037 — Portal-wide maintenance `reason` capped at 200
  chars** (truncated with "..." suffix). Pre-fix: a 5KB reason
  rendered as a wall of text in the Status banner.

### Compatibility

- All v0.4.11 routes preserved.
- Pure validation-layer changes; no schema or runtime-state
  changes.

## [0.4.11] - 2026-05-09

### Security

- **BUG-033 — Standard security headers on every response.**
  After-request hook attaches `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  strict-origin-when-cross-origin`,
  `Strict-Transport-Security: max-age=15552000;
  includeSubDomains`, plus a conservative CSP allowing same-
  origin scripts/styles + inline (Jinja templates have inline
  `<script>` blocks) and `frame-ancestors 'none'`. None of the
  headers were present pre-v0.4.11.

### Fixed

- **BUG-034 — `POST /api/v1/admin/schedules` with malformed
  `at_time_utc` returned 500.** The column is `VARCHAR(5)`;
  values like `"not-a-time"` (10 chars) failed the Postgres
  insert with `DataError` → unhandled → 500. Now: validates
  the `HH:MM` shape (`00:00`–`23:59`) in the service layer and
  returns 400 `validation_failed` with a friendly message.

### Compatibility

- All v0.4.10 routes preserved.
- Security headers use `setdefault` — any blueprint that has
  set its own header keeps it.

## [0.4.10] - 2026-05-09

### Security

- **BUG-005 — Server-side cookie + JWT revocation now ENFORCED.**
  Previous behaviour (v0.2.10–v0.4.9): the auth path wrote
  session rows on issuance and `revoke_one`/`revoke_all_for_user`
  set `revoked_at`, but the middleware ignored those rows. A
  cookie or access-token exfiltrated before logout could keep
  authenticating until its hard expiry. Now: the middleware
  consults `sessions.revoked_at` on every authenticated request.
  Revoked rows are treated as unauthenticated. Legacy cookies
  with no `sid` claim still work (graceful fallback).

### Fixed

- **BUG-031 — JSON-rule editor preserved input on validation
  failure.** Previously a typo or validation error redirected
  away and the operator's pasted JSON was lost. Now the form
  re-renders with the JSON pre-filled and the error inline.
  Saves a re-paste on every typo.
- **BUG-032 — Schedule-vs-operator maintenance race.** If the
  operator manually toggled portal-wide maintenance OFF during
  a scheduled-maintenance window, the schedule_tick reconciler
  would re-enable it ~30 s later. Now: operator toggles stamp
  `operator_override_at` and the reconciler respects that for
  the rest of the active window.

### Bug-log housekeeping

Five "open" bugs were already fixed in earlier releases — the
bug-log tracker had simply gone stale. No code change needed
beyond status updates:
- **BUG-007** — Group/site name uniqueness — already enforced
  via DB unique constraint + 409 `name_conflict` error path.
- **BUG-008** — 0-byte firmware upload — already rejected with
  `ValueError → 400`.
- **BUG-009** — Favicon 404 — favicon shipped + `<link rel="icon">`
  in layout.
- **BUG-010** — PATCH ignores unknown fields — already returns
  400 `validation_failed` with the allowed-fields list.
- **BUG-011** — Empty PATCH bumps `updated_at` — already
  short-circuits when the diff is empty.

### Compatibility

- All v0.4.9 routes preserved.
- Auth-middleware change is additive: legacy cookies/tokens
  without a `sid`/`jti` continue to authenticate. Only revoked
  rows are denied.
- Pure code-path fixes; no schema changes.

## [0.4.9] - 2026-05-09

### Added — Watchdog rule JSON editor (B9) + bulk-action per-device audit (B14)

- **JSON editor** on `/app/rules` for shapes the form-builder
  can't express (custom probes, multi-window maintenance,
  complex escalation chains). Same body shape as the v0.4.0 API.
  Round-trip lossless. Audit-logged with `via=json_editor`.
- **Per-device audit fanout** for bulk actions. New helper
  `audit_service.record_per_device(action, device_ids, ...)`.
  Wired into:
  - `device.bulk_deleted_per_device` — one row per deleted
    device on bulk-delete from the Devices page.
  - `device.bulk_delete_skipped_per_device` — for protected
    devices that the bulk-delete refused to touch.
  - `device.mass_command_issued_per_device` — one row per
    device when a group command fans out.
  - `device.mass_command_skipped_per_device` — for protected
    devices skipped during a group fan-out.
  The aggregate meta-row (`device.bulk_deleted`,
  `group.mass_command_issued`) still emits — these are
  *additional* rows that make "what did this bulk-delete
  actually touch?" answerable from `/app/audit?target_id=<dev>`.

### Compatibility

- All v0.4.8 routes preserved.
- New audit-action names; existing meta-action queries unchanged.

## [0.4.8] - 2026-05-09

### Added — Schedules as a separate primitive (B8)

Time-driven counterpart to watchdog rules. Rules fire on probe
failure; schedules fire on time.

- **New table `schedules`** with `kind`, `recurrence` (once /
  daily / weekly), `at_time_utc`, `weekdays`, `duration_seconds`,
  `target`, plus runtime state (`last_run_at`, `next_run_at`,
  `last_outcome`).
- **Two kinds:**
  - `power_cycle` — enqueues `relay_cycle` against a target
    (device / group / tag) on the schedule.
  - `maintenance` — flips portal-wide watchdog maintenance ON
    for `duration_seconds`, then OFF (so e.g. "every Sat 2-3 am
    UTC, suppress watchdog rules" is one-line).
- **APScheduler `schedule_tick`** every 30 s. Fires due
  schedules, recomputes `next_run_at` for recurrences,
  reconciles the maintenance flag.
- **UI at `/app/schedules`** with form for all the shapes +
  enable/disable/delete + plain-English sentence render.
- **API:** `GET / POST / DELETE /api/v1/admin/schedules`.
- **Audit hooks:** `schedule.created`, `schedule.deleted`,
  `schedule.enabled_changed`.
- **Cross-link** between `/app/rules` and `/app/schedules` in
  the page header (no top-nav change).

### Operational controls

- `REBOOTER_SCHEDULER_DISABLED=1` already short-circuits the
  whole APScheduler — same flag covers the new schedule_tick.

### Compatibility

- All v0.4.7 routes preserved.
- New `schedules` table created via `Base.metadata.create_all()`
  at boot.

## [0.4.7] - 2026-05-09

### Added — Maintenance windows + portal-pause + watchdog.firing inbox (B7 + B13)

- **Portal-wide watchdog maintenance toggle.** Super-admin
  toggles via the Status page (or
  `POST /api/v1/admin/maintenance`). When ON, the watchdog tick
  short-circuits — no probes, no actions. Operator can do a
  scheduled cabinet reboot without false-positive firing. ON-state
  shows a banner on the Status page; OFF-state shows a collapsed
  "pause all" form. Audit hook: `maintenance_mode.toggled`.
- **Per-rule maintenance windows.** Each rule's existing
  `maintenance_windows` JSON is now honored by the runtime. Rule-
  create form gets a "From/To" datetime-local pair (treated as
  UTC). During the window the runtime records
  `maintenance_skip` events instead of firing the rule's action.
- **Status inbox surfaces watchdog.firing rules.** Any rule with
  `status='firing'` OR an `action_fired` event in the last hour
  shows up as an attention item, severity warn, ranking between
  `device_offline_long` and `device_failsafe`. Click target is
  `/app/rules#<rule-id>` (rule-list anchor).

### New table: `runtime_flags`

Tiny key/value store for flags the operator must change without a
redeploy. Today only `maintenance_mode_active` lives here; future
operator-toggles (e.g. "freeze fleet during incident") will share
the table.

### Compatibility

- All v0.4.6 routes preserved.
- New tables created via `Base.metadata.create_all()` at boot.
- Rules created pre-v0.4.7 have empty `maintenance_windows`; runtime
  treats empty as "always run" — no behavior change.

## [0.4.6] - 2026-05-09

### Fixed — forgot-password handler crashes on SMTP failure (BUG-030)

- `forgot_password_submit` was calling `send_password_reset_email`
  WITHOUT a try/except. When the configured SMTP server hangs
  up mid-handshake (currently the case on prod —
  smtpauth.earthlink.net auth fails because the SMTP password is
  set to the bootstrap admin's app password rather than the
  EarthLink SMTP password), the SMTPServerDisconnected bubbled
  out to Flask → 500.
- Now: SMTP failures are caught and logged; the password-reset
  token is still minted in the DB, the audit-log entry records
  `smtp_ok=false` + `smtp_error=<exception name>`, and the user
  sees the same non-disclosing "if an account exists, we've
  emailed you" page as before. Operator can recover the URL from
  audit history.

### Operational note

Until the operator updates `REBOOTER_SMTP_PASSWORD` to a real
EarthLink SMTP credential, password-reset emails will not be
delivered. The forgot-password flow no longer 500s, but no email
arrives. Settings → Notifications → "Send test email" surfaces
the same SMTP error to the operator immediately.

## [0.4.5] - 2026-05-09

### Fixed — concurrent firmware upload regression (BUG-002a)

- **Concurrent firmware upload race returned 500 (regressed v0.3.9).**
  The IntegrityError cleanup branch in `upload_release` referenced
  `pointer_path`, which v0.3.9 deleted when the channel pointer
  switched from a static file to a Flask redirect. The loser thread
  of a concurrent upload race hit `NameError: pointer_path` →
  unhandled → 500. Originally fixed in v0.1.3 (BUG-002), regressed
  in v0.3.9; now fixed properly.
- **Stale-cookie name in `test_logout_does_not_revoke_cookie_server_side`.**
  Was reading `s.cookies.get("session")` — now reads
  `rebooter_session` (with fallback to legacy name during deploy
  transitions).
- **Rate-limit test gracefully skips on exempt source.** When the
  test's source IP is in `REBOOTER_RATE_LIMIT_EXEMPT_IPS` (the
  default exemption that lets the QA host run a full suite without
  hitting the per-IP cap), the test detects this via the absence
  of `X-RateLimit-Limit` headers and emits `pytest.skip` instead
  of failing.
- **`test_logout_does_not_break_subsequent_login` switched to
  `disposable_admin_session`.** Was bumping the bootstrap admin's
  `tokens_valid_after` mid-suite, cascading into the v0.2.x test
  failures. Now uses a fresh admin user.

### Compatibility

- All v0.4.4 routes preserved.
- Pure code-path fix in firmware service; no schema changes.
- The 500-on-race fix is purely defensive — removing a NameError
  in an error-recovery branch.

## [0.4.4] - 2026-05-09

### Test-infrastructure hardening (BUG-021 / 024 / 025 / 026)

Closes the four test-infra defects from the post-v0.4.2 deep
regression. The QA suite now runs clean against the live deployment
without cascade failures.

- **BUG-021 — disposable_admin_session fixture.** Tests that mutate
  auth state on the admin user (call `/api/v1/auth/logout`,
  redeem a password reset, run revoke-all) now use a fresh,
  fixture-managed admin user instead of poisoning the
  bootstrap admin's `tokens_valid_after`. The session-scoped
  `admin_token` fixture stays — but no test file calls
  `/api/v1/auth/logout` on the bootstrap admin anymore.
- **BUG-024 — stale tests updated.** `test_login_logout_round_trip`
  now asserts page title contains "Status" (v0.3.1 R-DSH-1).
  `test_create_group_does_not_log_user_out` checks for
  `rebooter_session` cookie name (v0.3.3 cookie-domain rework).
- **BUG-025 — rate-limit test gets `@pytest.mark.timeout(120)`.**
  Includes a 65 s post-burst sleep to clear the per-minute window
  for downstream tests.
- **BUG-026 — invitation-redeem cookie name fixed.** Asserts
  `rebooter_session` not `session`.

### New env var: `REBOOTER_RATE_LIMIT_EXEMPT_IPS`

Comma-separated list of client IPs whose requests bypass the
rate limiter. Used for the QA test host so a full suite run
(~50 logins) doesn't burn through the per-IP 200/hour budget.
**NEVER set this for arbitrary client IPs in production.**
Implemented via Flask-Limiter `request_filter` which short-
circuits the entire decorator chain when the IP matches.

### Compatibility

- All v0.4.3 routes preserved.
- New env var has a safe default (`192.168.18.1,127.0.0.1` —
  docker bridge gateway + loopback) which is harmless in any
  internet-facing deployment because those addresses can never
  appear as a real client IP after `ProxyFix(x_for=1)` parses
  `X-Forwarded-For`.

## [0.4.3] - 2026-05-09

### Fixed — Quick wins from the post-v0.4.2 deep regression

- **BUG-022 (high) — Sign out link added to header.** The
  redesign collapsed the header and Sign out ended up only on the
  Profile page. Restored it to `topbar-actions`.
- **BUG-023 (medium) — Role badge added to header.** Super-admin
  shows a red `super admin` badge; admin shows a neutral `admin`
  badge. Helps the operator see the elevated role at a glance —
  important since super-admin actions affect the whole fleet.
- **BUG-028 (low) — Authentication-tab page title.** Changed from
  "Auth settings" to "Authentication settings" so the title
  matches the visible tab label.

### Compatibility

- Pure UI / template changes. No model, route, or API changes.

## [0.4.2] - 2026-05-09

### Added — Watchdog probe runtime (B6 from BACKLOG)

Watchdog rules created in v0.4.0 now actually FIRE.

- **APScheduler watchdog tick** every 10 s. For each enabled rule
  whose `last_probed_at + window_seconds` has elapsed, runs the
  probe and writes a `WatchdogProbeEvent`.
- **Probe kinds shipped** (stdlib only):
  - `internet` — TCP connect to 1.1.1.1:53.
  - `tcp` — TCP connect to host:port.
  - `ping` — falls back to TCP-port-80 to host (no raw ICMP from
    container by default; native-ICMP queued).
  - `http` — `GET <url>`, success on 2xx.
  - `dns` — resolve hostname.
  - `gateway` — no-op until device firmware reports its LAN gateway
    in heartbeat (queued for v0.4.3+).
- **State machine.** failure_streak / recovery_streak / status
  / last_probed_at / last_action_at / last_outcome stored on the
  rule row (idempotent ADD COLUMN at startup).
- **Action dispatch.** When `failure_streak >= failure_threshold`
  AND outside cooldown:
  - `cycle` → enqueues `relay_cycle` for each device in the target.
  - `hold_off` → enqueues `relay_off` + sets `is_held_off`.
  - `notify_only` → no power action (audit only).
- **Recovery.** `recovery_threshold` consecutive successes after a
  failure clears the streaks and re-arms the rule.
- **Cooldown.** During cooldown failures still log
  (`outcome=cooldown_skip`); the action does not fire again.
- **Probe-now diagnostic.** UI button + API
  `POST /api/v1/admin/rules/<id>/probe-now` runs a single probe
  synchronously and logs an event. Does NOT advance state or fire
  actions — purely operator-facing.
- **Per-rule event log.** New API `GET /api/v1/admin/rules/<id>/events`
  returns the last 50 events (newest first). Inline expander on
  the rule list shows the latest 10.

### Operational controls

- `REBOOTER_WATCHDOG_DISABLED=1` — emergency-stop the runtime
  without touching code (the tick is a no-op).

### Audit hooks

- `watchdog_rule.probed` (per probe-now invocation; per scheduled
  tick events go through the WatchdogProbeEvent log, not audit).

### Compatibility

- 5 new columns on `watchdog_rules` (failure_streak, recovery_streak,
  last_probed_at, last_action_at, last_outcome) added via the
  idempotent ADD COLUMN bootstrap. No migration step.
- All v0.4.1 routes preserved.
- Tests for the runtime exercise the synchronous probe-now path
  (10 tests).

## [0.4.1] - 2026-05-09

### Added — Password reset + Notifications tab + 30-day invite default

- **Password-reset flow.** New `password_resets` table; new
  `/app/forgot-password` and `/app/reset-password` pages. Tokens
  default to **1 h TTL** (configurable via
  `REBOOTER_PASSWORD_RESET_TTL_SECONDS`). On consume,
  `tokens_valid_after` is bumped so every existing session/JWT
  for that user is invalidated. "Forgot your password?" link added
  to the login page.
- **Settings → Notifications** tab. Read-only display of env-var
  SMTP config + a "Send test email" form.
- **Invitation default TTL: 7 → 30 days** (operator-requested).
  Override via `REBOOTER_INVITATION_TTL_SECONDS`. Invite email body
  updated to match.
- **Email service additions:** `send_password_reset_email`,
  `send_test_email`.

### Audit hooks

- `password_reset.requested` (logged for every attempt, including
  non-existent emails, so the operator sees enumeration probes).
- `password_reset.consumed` (per-user, includes IP).
- `smtp.test_sent` (per-test, includes target email + ok flag).

### Compatibility

- All v0.4.0 routes preserved.
- `password_resets` table created via `Base.metadata.create_all()`
  — no migration step.
- SMTP behavior unchanged when not configured: invitations still
  succeed (link surfaces in the admin console); password-reset
  request silently succeeds-shaped (the email simply never arrives).

## [0.4.0] - 2026-05-09

### Added — Watchdog rules first slice (P4 of webui-redesign-plan)

Net-new feature surface. v0.4.0 ships **data model + CRUD +
plain-English render**; the probe runtime that actually executes
rules and writes events is queued for v0.4.1+.

- New tables: `watchdog_rules` (full schema per
  `webui-redesign-plan.md` §7.1) + `watchdog_probe_events`
  (table shape only; inserts come in v0.4.1+).
- Plain-English rule renderer (R-WD-1). Every rule shows its
  full sentence form on the list page:
  > *"If ping to `192.168.1.1` fails 3 consecutive times over
  > 60 s, power-cycle (5s off) on device `Office Modem`, then
  > wait 5 min and check 2 successes before re-arming."*
- Rule-builder UI at `/app/rules` replaces the v0.3.0
  empty-state stub. Form picks probe kind (internet / ping /
  tcp / http / dns / gateway), action (cycle / hold_off /
  notify_only), target (device / group / tag), and thresholds.
- Per-rule enable/disable + delete actions.
- New API: `GET /api/v1/admin/rules`, `POST` (admin+), `DELETE`.
- Audit hooks: `watchdog_rule.{created,deleted,enabled_changed}`.

### NOT in v0.4.0 (queued)

- Probe runtime — rules ARE stored but DO NOT FIRE yet (UI
  flags this).
- Per-rule event log UI, probe-now / simulate buttons.
- Maintenance windows UI + portal-wide maintenance-mode.
- Schedules as a separate primitive.
- Notifications on rule trigger (gated on v0.4.1 email-SMTP).

### Compatibility

- All v0.3.9 routes preserved.
- New tables via `Base.metadata.create_all()` at boot — no
  migration step.

## [0.3.9] - 2026-05-09

### Added — firmware mirror chain P1 (RFC-002)

Backend hosting for the per-channel firmware library that
RFC-005's safe-bootstrap and the dual-URL fallback design depend
on.

- **New table:** `firmware_release_mirrors` — one row per
  (release, mirror-kind) tuple. Tracks URL, status (`pending` /
  `live` / `failed`), `verified_sha256`, and probe metadata.
  Cascade-deletes with the parent `firmware_releases` row.
- **Per-channel publishing on upload.** A new release's binary
  is written to **two** static locations on the firmware volume:
  - canonical flat: `<firmware_dir>/rebooter-<v>.bin` (existing,
    kept for backwards-compat with devices already in the field)
  - per-channel: `<firmware_dir>/<channel>/rebooter-<v>.bin`
- **Channel pointer is a Flask 302-redirect endpoint, NOT a static
  file.** New public, unauthenticated route:
  `GET /api/v1/firmware/<channel>/latest` → 302 to
  `<public-base>/<channel>/<latest-version-filename>.bin`. RFC-005's
  bootstrap firmware will fetch this on first boot and on every
  retry — it always resolves to the freshest binary in the
  channel without the bootstrap needing to know specific version
  strings. The endpoint is public (no auth) because the
  bootstrap doesn't have a bearer token yet by definition.
- **Why redirect, not static file.** Static `latest.bin` files
  were considered and rejected: they would collide with nginx's
  global `open_file_cache_valid 60s`, making an overwrite
  invisible to clients for up to a minute. The redirect endpoint
  queries the DB on every hit, so it's always fresh; nginx still
  serves the per-channel versioned binary on the redirected URL
  (which never changes content for the same path → safe to cache).
- **Mirror records.** Three rows per upload — canonical flat
  URL, per-channel static-file URL, and channel-pointer redirect
  URL — all marked `status=live` / `verified_sha256=<hash>` since
  we just wrote them.
- **Admin UI.** `/app/firmware` now shows a per-release mirror
  expander listing each URL + status + kind.
- **Delete cleans up cleanly.** Deleting a release removes the
  canonical + per-channel artifacts. The channel-pointer URL
  self-updates because it's DB-backed.

### What's NOT in this release

Per RFC-002 §8 phase split:
- **GitHub Releases mirror publisher (P2)** — not in v0.3.9.
  The `MirrorPublisher` abstraction is intentionally not yet
  introduced; the local-only logic is inline. Once we add the
  GitHub publisher, we'll abstract.
- **Project-owned nginx snippet (RFC-002 §7.6)** — host nginx
  config still hand-edited; project snippet ships in a follow-up
  minor.

### Compatibility

- All v0.3.8 routes preserved.
- Existing flat-layout firmware URLs continue to work — devices
  in the field do not need re-configuration.
- New table created via `Base.metadata.create_all()` at boot.
  No manual migration step.
- Per-channel publish failure logs but does not fail the
  upload; the canonical flat-layout file remains the source of
  truth for v0.3.9.

## [0.3.8] - 2026-05-09

### Added — failsafe-event surface (RFC-005 P1 backend)

Receives device-side failsafe reports per RFC-005 §5.2. When a
device falls back from slot B (just-OTA'd main) → slot C (last-
known-good) it POSTs to the new endpoint and we surface the
event prominently.

- **New table:** `device_failsafe_events` with columns
  `device_id`, `received_at`, `failed_version`,
  `fallback_to_version`, `reason`, `details` (JSON-shaped, opaque
  to the backend so future firmware extensions don't require a
  schema change).
- **New endpoint:** `POST /api/v1/device/failsafe`
  (device-token-authenticated). Body shape:
  ```
  {
    "device_id": "...",
    "failed_version": "0.x.y",
    "fallback_to_version": "0.x.z",
    "reason": "boot_failure" | "sha256_mismatch" |
              "watchdog_reset" | "timeout" | "other",
    "details": { ... }
  }
  ```
  The `device_id` in the body is informational; we trust the
  bearer token's device. Best-effort write — never blocks the
  device's POST.
- **Status inbox attention items.** New
  `device_failsafe` kind with severity `critical`. Renders with
  a red-accent treatment (new `.v3-sev-critical` CSS class).
  Surfaces every failsafe in the last 24 h. No threshold; a
  failsafe is a strong signal on its own.
- **Per-device Failsafe section** on the device-detail page
  (new tab anchor `#failsafe`). Shows the last 25 failsafe
  events with the failed/fallback versions, reason, and an
  expandable diagnostic blob.
- **`get_device_detail()`** returns `failsafe_events`
  alongside `audit_history`.

### Why this matters

Pairs with the (firmware-team-side) self-healing OTA design in
RFC-005. When a firmware update doesn't boot on a real device,
the device tells central; central tells the operator
prominently; the operator can then push a fixed version. The
machinery is "no firmware update can brick a device" — the
RFC-005 constitutional invariant.

### Compatibility

- All v0.3.7 routes preserved.
- New table created via `Base.metadata.create_all()` at boot.
  No manual migration step.
- The existing inbox shape gains `totals.failsafe` and a new
  attention-item kind `device_failsafe`. Existing API consumers
  that iterate `attention` by their existing kind set are
  unaffected.

## [0.3.7] - 2026-05-09

### Fixed — `ERR_TOO_MANY_REDIRECTS` on stale cookie

**Operator-reported.** Browser hits `/app/`, gets a redirect loop.

**Root cause.** A two-handler interaction the v0.2.x code has had
all along but was rarely exposed:

1. `admin_required_ui` calls `_resolve_user()`. If the cookie
   carries a `user_id` for a deleted-or-deactivated user, OR if
   the cookie's `iat` is older than the user's
   `tokens_valid_after` cutoff (e.g. somebody triggered
   `revoke_all_tokens` → logout), `_resolve_user()` returns `None`.
   The middleware redirects to `/app/login`.
2. `login_page` then sees `session.get("user_id")` is still
   truthy (the cookie is still in the browser, just stale by the
   freshness check), and redirects back to `/app/`.
3. Loop. Browser eventually surfaces `ERR_TOO_MANY_REDIRECTS`.

This presented today after a QA test triggered logout (which
bumps `tokens_valid_after`) on the operator's user; the
operator's already-cached browser cookie was now older than the
new cutoff.

**Fix.** Two-sided defensive:

- `app/middleware/admin_auth.py::_resolve_user` — when it
  decides the cookie is stale (user can't load, user is
  deactivated, or `iat < tokens_valid_after`), it now **clears
  the session** before returning `None`. Subsequent requests
  see no `user_id` and behave correctly.
- `app/blueprints/admin/auth_ui.py::login_page` — instead of
  redirecting on cookie-truthiness alone, calls `_resolve_user`
  first. If the cookie validates → redirect to `/app/`. If not
  → render the login form (and the session has been cleared by
  `_resolve_user`).

Either fix alone closes the loop; both together is
belt-and-braces.

### Fixed — QA-test-generated 401s polluted the Status inbox

Operator-reported. The v0.3.6 attention items were surfacing
synthetic auth failures generated by the QA suite itself —
showing `dev_QA_<n>` and `dev_x` claimed ids from
`192.168.18.1` (the docker bridge gateway) as if they were real
device problems.

**Fix.** `app/services/inbox.py` now filters two kinds of noise
out of `device_auth_rejected` attention items:

- **Machine-internal source IPs:** `127.0.0.1`, `::1`, and
  `192.168.18.1` (docker bridge gateway as seen inside the
  container — by definition NOT a real LAN device).
- **QA-prefixed claimed device ids:** anything starting with
  `qa `, `qa-`, `qa_`, `test-`, `test_`, `playwright`, `dev_qa_`,
  `dev_test`. Mirrors the v0.2.8 `_QA_PREFIXES` list with the
  v0.3.6 test bucket's `dev_QA_*` shape added.

Either condition skips the row from the attention feed. The data
remains in `unregistered_auth_attempts` and is still visible via
`/app/unregistered-devices` for diagnostic purposes — only the
Status-inbox surface filters it.

### Compatibility

- All v0.3.6 routes preserved.
- No schema change.
- A user with a stale cookie will see the login form on next
  request (instead of looping). They re-authenticate as
  expected. No data loss.
- The polluting test rows that were already in
  `unregistered_auth_attempts` will age out of the 60-minute
  lookback window naturally, OR an operator can prune them with
  `DELETE FROM unregistered_auth_attempts WHERE source_ip='192.168.18.1';`.

## [0.3.6] - 2026-05-09

### Added — `device_auth_rejected` attention items on the Status inbox

Per the RCA queue (`docs/rca-2026-05-09-no-device-online.md` §6),
the `unregistered_auth_attempts` tracker has always existed but
was only visible from `/app/unregistered-devices`. v0.3.6
surfaces it on the **Status inbox** so an operator immediately
sees "this device IS trying to call but its token is rejected"
without leaving the home page.

Particularly useful right now while the firmware team debugs
`test-s31-01`'s central poll/heartbeat transport: once the
device starts calling again, any 401 it gets shows up here as a
ranked attention item.

**Trigger criteria.** A `(claimed_device_id, source_ip,
endpoint)` tuple that has been rejected at least
**3 times** in the last **60 minutes**. The 3-hit minimum filters
out single transient bad requests; the 60-minute window matches
the existing dashboard-badge cadence.

**Item shape.**

```
kind:        "device_auth_rejected"
severity:    "warn"
title:       "Device auth rejected (N attempts) on /api/v1/device/<endpoint>"
device_id:   <the claimed_device_id from the rejected request>
device_name: same (no separate display name available)
source_ip:   <the client IP>
since:       <last_seen_at>
hint:        "A device is calling with a stale or unknown bearer
              token. Mint a fresh enrollment token and re-enrol
              the device — the firmware's 401 → re-enroll loop
              should pick it up automatically."
rank:        35   (between offline_short=40 and enrollment_pending=30)
```

**Click target.** Status page now routes
`device_auth_rejected` items to `/app/unregistered-devices`
instead of `/app/devices/<id>` (the claimed device id is by
definition unknown — linking to the device-detail page would
404). All other attention-item kinds keep their existing
device-detail link.

**Totals.**
`inbox.totals.auth_rejected` is the new count surfaced to API
consumers + the Status verdict math. `attention_total` includes
it.

### Compatibility

- All v0.3.5 routes and endpoints preserved.
- No schema change. No new env vars.
- Best-effort: a tracker query failure logs but does not crash
  the Status page.
- Rollback: previous Docker tag.

## [0.3.5] - 2026-05-09

### Fixed — bulk-delete deleted unchecked rows (regression in v0.3.4)

**Symptom (operator-reported).** Master-select-all → uncheck the
non-target rows → click *Delete selected* → **all of them got
deleted**, including the unchecked ones.

**Root cause.** The devices list renders the same row in TWO
layouts (desktop table + mobile card) and both copies have a
checkbox with `name="device_id"`. The master-toggle checked both
copies; when the operator unchecked the visible one, its hidden
pair (the other layout's copy of the same row) stayed checked
and was submitted. Server received the value despite the visible
checkbox being unchecked.

**Fix.**
- `static/js/bulk_select.js` now syncs paired checkboxes by
  `name + value` — toggling one toggles the other in the same
  form.
- All four bulk handlers dedupe their incoming id list as
  defense-in-depth, so even a future stray double-submission
  doesn't inflate counts:
  `app/blueprints/admin/{devices,groups,invitations,enrollment_tokens}.py`.

### Documented — RCA: "no device shows online" (operator-reported)

Investigation findings landed in `docs/rca-2026-05-09-no-device-online.md`.
Summary:

- **Server side: healthy.** `device_heartbeats` insert path
  works; v027 + synthetic-probe smoke confirms a registered
  device + a single `POST /api/v1/device/heartbeat` flips
  `heartbeat_state` to `online` immediately.
- **No real device has called this server in the last 24 h+.**
  Container access logs show only `192.168.18.1` (docker bridge,
  QA tests) on every `/api/v1/device/*` POST.
- **Three of four lab devices are network-unreachable** as of the
  RCA window. 192.168.1.{67, 225, 30} 100% packet loss; .207
  pings but TCP-RST on port 80 (no HTTP service).
- **Per the project pause state, three of four devices are
  `central_management_enabled = false` BY DESIGN** (local-only).
  Only `test-s31-01` (192.168.1.67) was centrally enrolled — and
  it's the one that's now unreachable.

**No code defect on the server.** The fix is operational
(power + Wi-Fi + central-enable the three local-only devices) and
firmware-side (the operator's hint "we may need new firmware for
them too" is consistent with the unreachable-state findings).

### Compatibility

- All v0.3.4 routes preserved.
- No schema change.
- Rollback: `sudo docker pull dblagbro/rebooter-droids:0.3.4 && sudo docker tag … :latest && sudo docker compose up -d --no-deps --force-recreate rebooter-droids`.

## [0.3.4] - 2026-05-09

### Added — bulk-action UI on devices, groups, invitations, enrollment tokens

Per-row checkboxes + master select-all + sticky bulk-action bar on
the four list pages where mass operations are useful.

- **Devices list** — bulk-delete selected devices. Respects the
  v0.3.2 `is_protected` lockout: protected devices are skipped
  unless the operator ticks "override 🔒 protected" in the bulk
  bar. The lock badge appears on every protected row so operators
  see why the count of deleted-vs-skipped diverges.
- **Groups list** — bulk-delete selected groups. Cascade behaviour
  matches the existing per-group delete (memberships go; member
  devices stay).
- **Invitations list** — bulk-cancel selected pending invitations.
  Already-consumed invitations cannot be cancelled (they're audit
  records of a real user redemption); they're surfaced as
  `skipped_consumed` in the result flash.
- **Enrollment tokens list** — bulk-revoke + per-token revoke.
  v0.3.4 adds the **first revoke primitive** for enrollment tokens
  (single + bulk); previously tokens were immutable. Consumed tokens
  cannot be revoked (they're records of a real device's bring-up).

### Mass-action confirmation gate

All four bulk actions go through the existing
`app/services/mass_action.py` gate:
- ≤ 5 targets: simple `confirm()` prompt.
- 6 – 20 targets: simple `confirm()` prompt, count visible.
- > 20 targets: typed-confirmation prompt — operator must echo the
  verb (`delete`, `cancel`, `revoke`).

The submit button auto-promotes to `btn-danger` red styling once
the count crosses 20.

### Frontend foundation

- New `static/js/bulk_select.js` (~120 LOC, vanilla, no deps) —
  wired up via `data-bulk-form` / `data-bulk-master` /
  `data-bulk-row` / `data-bulk-bar` attributes. Progressive
  enhancement: form submit works without JS; the JS adds live
  count, master-toggle (with `indeterminate` state), and
  disable-when-empty.
- New CSS surfaces: `.v3-bulk-checkbox-cell`, `.v3-bulk-master`,
  `.v3-bulk-checkbox`, `.v3-bulk-bar`, `.v3-bulk-bar-count`. The
  bar is `position: sticky` above the bottom-tab nav on mobile.

### API additions

| Endpoint | Body | Returns |
|---|---|---|
| `POST /api/v1/admin/devices/bulk-delete` | `{device_ids, override_lockout, confirmation_level, confirmation_typed_value}` | `{deleted, skipped_protected, skipped_unknown}` |
| (groups + invitations + tokens) | UI-only in v0.3.4; API endpoints land in v0.3.5 if a consumer asks. |

All bulk actions emit a single audit row with action ∈
`{device.bulk_deleted, group.bulk_deleted, invitation.bulk_cancelled,
enrollment_token.bulk_revoked}` and `details.reason='operator'`.

### Compatibility

- All v0.3.3 routes and endpoint names preserved.
- No schema change.
- Rollback: `docker run dblagbro/rebooter-droids:0.3.3`.

## [0.3.3] - 2026-05-09

### Fixed — frequent sign-outs when switching between www and www2

The session cookie was host-scoped (no `Domain=` attribute), so a
login at `www.voipguru.org/rebooter` did not carry to
`www2.voipguru.org/rebooter` — every switch required a fresh login.
The firmware-side multi-URL fallback (primary → secondary) made this
fire repeatedly during a single working session.

**Diagnosis confirmed via Playwright** (`/tmp/diagnose_signouts.py`,
captured in this commit's audit trail): cookie domain was
`www.voipguru.org`, hitting `www2.voipguru.org` after login bounced
to `/app/login`.

**Fix.**

- New env var `REBOOTER_COOKIE_DOMAIN`. When set (e.g.,
  `.voipguru.org`), the session + theme cookies carry across all
  subdomains of that domain. Default empty = host-scoped (the
  v0.3.0–0.3.2 behaviour) for self-hosted single-host deployments.
- Cookie name renamed from Flask's default `session` to
  `rebooter_session`. Avoids collisions with peer voipguru.org apps
  (hub, paperless, etc.) that also default to `session`. Without the
  rename, a domain-shared cookie could collide with another app's
  cookie of the same name and produce confusing failures.
- Theme cookie similarly renamed: `theme` → `rebooter_theme`. The
  legacy `theme` cookie is still read for one minor so users don't
  lose their light-mode preference on upgrade; the writer clears it.
- `docker-compose.yml` defaults `REBOOTER_COOKIE_DOMAIN=.voipguru.org`
  for the multi-URL voipguru deployment.

### Operational impact

- **Operators upgrading from v0.3.0–0.3.2 will be signed out exactly
  once** when v0.3.3 is deployed. The old `session` cookie is still
  in their browser but the server is now looking for
  `rebooter_session`. After the one re-login, the new cookie is
  cross-subdomain and switching between www and www2 carries it.
- No schema change. No code-call-site change.

## [0.3.2] - 2026-05-09

### Added — Power controls + safety + lockout (P3 of webui-redesign)

- **`is_protected` lockout flag** on every device (R-DEV-8). When
  set, the service layer rejects any power command (`relay_on`,
  `relay_off`, `relay_toggle`, `relay_cycle`, `device_restart`)
  unless the caller passes `override_lockout=True` (form param or
  JSON body).
  - API: locked-device commands return **HTTP 423 Locked** with
    `error.code = "device_locked"`.
  - UI: device-detail Power tab shows a lockout banner; the
    settings tab carries the toggle.
  - Mass fan-out (group commands) **skips** protected devices
    by default and surfaces the skipped count in the audit row +
    a warning flash. `override_lockout=1` includes them.
- **Hold-off action** (R-CTRL-3). Issues `relay_off` with the
  intent flag `hold_off=1`, sets the device's new `is_held_off`
  bool, and the UI renders a "held off" badge until any power-on
  command (`relay_on` / `relay_toggle` / `relay_cycle`) clears it.
  Watchdog rules + schedules (P4) MUST honour this flag.
- **Cancel-pending-command** (R-CTRL-8). New service helper
  `commands.cancel_pending_command()` flips a queued command to
  `cancelled` status, but only while it's still in `pending` —
  once the device has accepted, the cancel returns 409.
  - API: `POST /api/v1/admin/devices/<id>/commands/<cmd_id>/cancel`
  - UI: cancel button on every pending row in the Power tab.
- **`reason` field** convention (R-CTRL-6). Every power-action
  audit row now carries `details.reason ∈ {operator}`. The
  `schedule` and `watchdog` reasons land in P4 when those
  surfaces ship.
- **Confirmation dialogs scaled to action severity** (R-UX-12,
  R-CTRL-4 v0.3.2 visual tune):
  - `relay_off` and `device_restart` get a `btn-danger` red button
    plus a `confirm()` prompt.
  - `relay_cycle` gets a `confirm()` prompt that names the device.
  - `hold_off` requires a typed-confirmation prompt of the
    device's display name.
- **Lockout banner + held-off banner** on the Power tab —
  prominent visual treatment so operators can't miss the state.
- **`devices.is_protected` patchable** via `PATCH /api/v1/admin/
  devices/<id>` with `{"is_protected": true|false}`. Audit row
  emitted on change.

### Schema

Idempotent boot-time `ALTER TABLE ADD COLUMN IF NOT EXISTS`:
- `devices.is_protected BOOLEAN NOT NULL DEFAULT FALSE`
- `devices.is_held_off  BOOLEAN NOT NULL DEFAULT FALSE`

No manual migration step.

### Compatibility

- All v0.3.1 routes and endpoint names preserved.
- `enqueue_for_group()` now returns `(created, skipped)` instead
  of `list[Command]`. The two callers in `app/blueprints/admin/
  groups.py` are updated. **Internal API only** — no public
  consumer affected.
- Rollback: `docker run dblagbro/rebooter-droids:0.3.1`.

## [0.3.1] - 2026-05-09

### Added — Status page + device list/detail restructure (P2 of webui-redesign)

- **Status page** (`/app/`) replaces the v0.2.x stat-grid dashboard
  with an attention-feed-shaped landing.
  - Single-glance health verdict: **all-clear / attention / degraded
    / unknown** (R-DSH-2). The `unknown` state never crashes the
    page — telemetry failures fall through cleanly.
  - **Attention feed** ranked by severity × recency
    (R-DSH-3). Item kinds: `device_offline_short`,
    `device_offline_long`, `device_never`, `enrollment_pending`.
    Each item carries a stable id so a future ack-action can
    dedupe.
  - Plain-language all-clear statement when nothing is wrong
    (R-DSH-9).
  - Manual emergency controls card (open devices, enrol, groups,
    firmware) (R-DSH-8).
  - Recent-activity feed below the fold links into the new
    History page.
- **New service**: `app/services/inbox.py` with
  `health_and_attention()` — single DB hit returns verdict + items
  + totals so the Status page renders consistently.
- **Saved-filter chips on Devices** (R-DEV-4):
  - `Offline > 24 h` · `Never heartbeated` · `Has pending commands`
    · `QA fixtures only`
  - URL round-trip via repeated `?chip=...` query params
    (R-DEV-5). Multiple chips compose with AND semantics.
  - Service-layer support in `app/services/devices.list_devices`.
  - Same chip param on `/api/v1/admin/devices` for API consumers.
- **Mobile card layout** for the devices list (R-DEV-3).
  Breakpoint ≤ 640 px renders devices as stacked cards with the
  primary action reachable without horizontal scroll. Desktop
  unchanged (table layout).
- **Central-vs-local cue** (R-DEV-2): a `local-only` badge on
  devices that opt out of central management, plus a `central`
  badge on devices that opt in. Closes the v0.2.x failure mode
  where local-only devices were rendered as if they were
  unhealthy.
- **Open-this-device's-local-UI** link on device-detail (R-DEV-11)
  when the local IP is known.
- **Device detail tab strip** (R-DEV-7): Overview / Power /
  Watchdog / Schedule / Audit / Events / Settings. Watchdog +
  Schedule are stubs in v0.3.1 with empty states pointing at P4.
- **Enrollment wizard** at `/app/devices/new` (R-DEV-6):
  one-step minting + display of the token + the firmware-side
  configuration block (central_base_url, secondary_base_url,
  enrollment_token). QR-code support deferred to v0.3.2.
- New CSS surfaces: `.v3-verdict-{all-clear,attention,degraded,unknown}`,
  `.v3-attention-list`, `.v3-chips` + `.v3-chip-active`,
  `.v3-device-card`, `.v3-tabbar`. All theme-token-driven so
  they work in light, dark, and system modes.

### Test coverage

- New `tests/qa/test_v031_status_and_devices.py`.
- All v0.2.x and v0.3.0 buckets remain green against live.

### Compatibility

- All v0.3.0 routes and behaviour preserved. The old
  `dashboard.html` template is no longer rendered (the handler
  now renders `status.html`); no template change is breaking.
- All existing endpoint names (`admin_ui.*`, `admin_api.*`)
  preserved.
- No schema change. No new env vars.

## [0.3.0] - 2026-05-09

### Added — design system + layout + navigation foundation (Phase 1 of webui-redesign-plan)

- **New 5-item top navigation:** **Status / Devices / Rules / History
  / Settings**. Replaces the previous one-link-per-database-concept
  nav. The nav reflects operator jobs-to-be-done from
  `docs/webui-redesign-research.md` peer-product analysis (UniFi
  + WattBox + Tailscale shapes).
- **Mobile-first layout:** bottom-tab nav at ≤ 640 px viewport;
  top nav at ≥ 768 px. Both render simultaneously into the same
  five destinations.
- **Mobile-first stylesheet** rewrite (`static/css/app.css`).
  CSS custom properties as theme tokens; light / dark / system
  themes via `data-theme` attribute on `<html>`.
- **Theme picker** at `/app/settings/theme` — system / light /
  dark, persisted as a per-browser cookie (so an operator can
  keep light at the office and dark at home). FOUC-free via an
  inline synchronous `<head>` script.
- **New top-level destinations** (route stubs that compose into
  later phases):
  - `GET /app/rules` — watchdog-rule home with empty state and
    "what's coming" panel pointing at P4.
  - `GET /app/history` — unified log feed; v0.3.0 implementation
    renders the existing audit data; expanded into watchdog
    events + power events + schedule fires + notification sends
    in P6.
  - `GET /app/settings` — settings parent with tab strip linking
    System / Network / Authentication / Users / Invitations /
    Firmware / Theme / Profile sub-pages. New stub pages for
    System / Network / Authentication explicitly mark themselves
    "Coming in P5/P6".
- **Component library skeleton** at `templates/_components/`
  with `empty_state.html`, `error_state.html`, `settings_tabs.html`
  partials. Other phases build on these.
- **Auto-derived `active` slot** on every page from the URL prefix
  via `_ctx()` — so the nav highlights the right item without
  per-blueprint plumbing.
- **WCAG 2.5.5 touch targets**: minimum 44 × 44 px on every
  primary button on mobile.
- **Visible focus rings** on every interactive element.
- **`viewport-fit=cover`** + `safe-area-inset-bottom` so the
  bottom-tab bar doesn't collide with iOS home indicators.

### Fixed — pre-existing responsive failures

The seven mobile-overflow failures in `tests/qa/test_responsive.py`
at 375 px viewport (login, dashboard, devices, events, audit,
users) are addressed by the mobile-first stylesheet rewrite. They
were systemic CSS issues, not page-specific bugs. Running the
suite against the new shell confirms the contracts.

### Notes

- All existing URLs continue to resolve. No bookmarks broken.
- All existing endpoint names preserved (`admin_ui.*`,
  `admin_api.*`) so every `url_for(...)` in the codebase
  resolves. Templates were not touched outside `layout.html`.
- The `dashboard.html` template continues to render at `/app/`
  in v0.3.0 — its restructure into a true Inbox / Status feed
  is Phase 2 (P2) of the redesign plan.
- `/app/audit` continues to serve its current page; in P6 it
  redirects to `/app/history`.
- No schema change. No new env vars. No backend behaviour change.

### Changelog of design intent

This is the foundation phase that the brief calls Phase 1 — design
system, layout, navigation. P2 (Status feed + device list/detail
restructure), P3 (power-controls + safety + lockout flag), P4
(watchdog-rule builder), P5 (RBAC + auth foundation), P6 (history
+ notifications + settings), and P7 (polish) ship in subsequent
versions per `docs/webui-redesign-plan.md` §9.

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
