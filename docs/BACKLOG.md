# Backlog

Last updated: **2026-05-09 PM** (post v0.4.9).

This is the canonical, ordered backlog for what comes next on
rebooter-droids. The pause-state doc captures recent history; this
doc captures what *is still owed*.

For redline-gated items (RFCs awaiting operator decisions) see
**§ Awaiting redline** at the bottom.

---

## P1 — operator-locked next sprint (post v0.4.0, 2026-05-09 PM)

These are the items the operator dictated at the end of the v0.4.0
session. They form the v0.4.1 + v0.4.2 release plan.

### B1. RBAC implementation (R-RBAC-* of RFC-003)

- Per-resource role enforcement on every blueprint (currently we have
  super_admin / admin / viewer at the route-decorator level only;
  R-RBAC asks for fine-grained per-resource gating).
- Site-as-scope: each role assignment is `(user, role, site_id?)` —
  `site_id NULL` means org-wide; otherwise scoped to that site.
- Unblockers: RFC-003 §RBAC redline #1–#4.
- Suggested first slice: ship the `(user, role, site_id?)` join table
  and audit-log the migration from the current flat-role model;
  enforcement comes in iteration 2.

### B2. Admin / super-admin invite via email (30-day token expiry)

✅ **Already shipped** — invitations service has been in place since
v0.2.x (per-user, signed token, single-use, audit-hooked). The
v0.4.1 cut bumped the default TTL **7 → 30 days** to match the
operator's instruction. Email body updated to match.

> Status: **DONE.** Operator can keep going through Settings →
> Invitations as today; recipient gets a fresh 30-day link.

### B3. Password-reset UI

✅ **Shipped in v0.4.1.** `/app/forgot-password` + `/app/reset-
password`, 1h TTL, single-use, bumps `tokens_valid_after` on
consume. "Forgot your password?" link added to the login page.
Audit hooks: `password_reset.requested` /
`password_reset.consumed`.

> Status: **DONE.**

### B4. SMTP from coordinator-hub creds

✅ **CLOSED — shipped in v0.4.25.** SMTP is now DB-backed via
`runtime_settings`: DB row → env-var fallback. Settings →
Notifications tab editor lets the operator rotate creds without a
container recreate. Audit hook `smtp.config_updated`. Test:
`tests/qa/test_v0425_runtime_smtp.py`.

### B5. Get devices online (firmware-team coordination)

- Operator handed off comms via
  `docs/notes/2026-05-09-to-firmware-team-get-devices-online.md`.
- Status: **awaiting firmware team reply**. No code work blocked
  yet — this is purely a comms/handoff item.
- When the firmware team replies, they may produce work for us
  (e.g., new claim-token shape, custom heartbeat fields, etc.).

---

## P2 — engineering carryover

These were on the backlog before v0.4.0 and remain queued.

### B6. Watchdog probe runtime (v0.4.2)

✅ **Shipped in v0.4.2.** APScheduler 10-second tick + probe
dispatcher (internet/tcp/ping(→tcp:80)/http/dns) + state machine
(failure/recovery streaks, cooldown) + action dispatch
(cycle/hold_off/notify_only) + probe-now diagnostic + per-rule
event log inline on the list page.

⏳ **Still queued (smaller v0.4.3+ items):**

- ✅ Native ICMP ping probes — **CLOSED in v0.5.13.** Real
  ICMP via `iputils-ping`; success details carry `rtt_ms`,
  failure carries `exit_code` + `stderr_tail`. Defensive
  fallback to TCP-80 when ping binary is unavailable with an
  explicit `fallback:tcp_80` marker.
- `gateway` probe — no-op until device firmware reports its LAN
  gateway in heartbeat.
- Tag-as-target dispatch — shape exists; resolution stubbed.
  **Deferred** pending an operator decision on whether
  `device_tags` is its own primitive or an extension of `groups`.
- ✅ Status inbox attention item for `watchdog.firing` — shipped
  in v0.4.7 (see B13 below).

> Status: **DONE.** Operators can create rules and they fire on
> threshold. Cooldown + recovery work. Operator-stop available via
> `REBOOTER_WATCHDOG_DISABLED=1`.

### B7. Maintenance windows + portal-wide maintenance mode (v0.4.7)

✅ **Shipped in v0.4.7.** Per-rule maintenance windows respected
by the runtime; portal-wide pause toggle on Status page (super-
admin) + `POST /api/v1/admin/maintenance` API. Audit hook
`maintenance_mode.toggled`. Runtime records `maintenance_skip`
events instead of firing during a window.

### B8. Schedules as a separate primitive (v0.4.8)

✅ **Shipped in v0.4.8.** New `schedules` table, service,
APScheduler tick at 30s. Two kinds (`power_cycle`,
`maintenance`), three recurrences (once/daily/weekly). UI at
`/app/schedules`. Cross-link from the Rules page header.

### B9. Watchdog rule advanced editor (v0.4.9)

✅ **Shipped in v0.4.9.** JSON editor folded into the existing
`/app/rules` page as a `<details>`. Same body shape as the
v0.4.0 API. Round-trip lossless. Audit-logged with
`via=json_editor`.

---

## P3 — RFCs awaiting redline (gated on operator response)

### B10. RFC-003 redlines #1–#4 — ✅ CLOSED 2026-05-10

All four redlines answered by operator:
- **Q1 scope cardinality** → `Site + Group + Device` (full expressiveness).
- **Q2 migration default** → super_admin global, admin all-current-sites
  (one-shot copy), operator no-scope (forced re-grant).
- **Q3 audit retention** → 365 days default, tunable via new
  `system.audit_retention_days` runtime setting. Nightly soft-prune
  into `audit_events_archive`.
- **Q4 invite shape** → invite carries role + scope (locked at
  send-time); site-multi-select + group/device selector on the
  invite form.

Decisions folded into RFC-003 §9.0. Unblocks B1 (TOTP) and B2 (OIDC).

### B11. RFC-004 architecture pick — ✅ CLOSED 2026-05-10

Operator picked **Option C (application-level event-log sync,
active-active)** over the RFC's original Option-B (Postgres logical
replication, active-passive) recommendation.

Design (locked):
- Per-hub `outbox_events` append-only table mirroring our audit pattern.
- Replicator daemon polls peers' `/api/v1/sync/since?seq=<n>` over
  HTTPS with HMAC bearer auth (reuses coordinator HMAC pattern).
- Idempotent apply on UUID-keyed rows; LWW on `event.at`; per-record
  audit retains both versions.
- Tombstone rows in `outbox_events` for deletes; receiver writes a
  `tombstones` row and refuses to recreate the UUID.
- Steady-state latency ~1–3s; symmetric peers, no failover procedure.

Decisions folded into RFC-004 §10b. **Implementation lands after the
B10 RBAC work** because outbox events must carry the new
site/group/device scope claims for receiver-side enforcement.

### B12. RFC-005 redlines (firmware-team Q1..Q9) — ✅ CLOSED 2026-05-10

Firmware team responded with detailed answers to all 9 questions.
Slot sizes locked (A=640KiB / B=1MiB / C=1MiB), Q3 success criteria
broader than just "heartbeat once" (local-OK qualifies),
Q4 canonical reason strings agreed, AP-mode captive portal shipped
in bootstrap-0.2.2, flash-time config = both serial + AP-mode,
LittleFS JSON not NVS, Python CLI flash tool first, hosting in
force with publish-integrity discipline.

Full reply: `docs/notes/2026-05-10-from-firmware-team-rfc005-redlines.md`.
RFC-005 §9 now records the final answers.



- Trial-window seconds, "main firmware healthy enough to promote"
  definition, fallback-fetch source under safe-bootstrap, etc.
- Blocks: any device-side OTA work; doesn't block the hub.

---

## P4 — small wins / housekeeping

### B13. Status inbox: surface `watchdog.firing` items (v0.4.7)

✅ **Shipped in v0.4.7.** Rules with `status='firing'` OR an
`action_fired` event in the last hour appear as attention items
on the Status page. Severity warn, rank 70 (between offline_long=60
and failsafe=80). Click target is `/app/rules#<rule-id>`.

### B14. Devices page: bulk-action audit log (v0.4.9)

✅ **Shipped in v0.4.9.** New `audit_service.record_per_device`
helper. Wired into bulk-delete + group-mass-command. Aggregate
meta-row still emits; these are *additional* per-device rows so
`/app/audit?target_id=<dev>` answers "what did this bulk action
actually do to this device?".

### B15. Settings → Sync tab content

- Replace the stub with real content once B11 is decided.

### B16. Power-usage monitoring + analytics — **NEW 2026-05-10 PM**

📐 **Full design doc**: `docs/B16-power-analytics-design.md`
(written 2026-05-10 PM after research pass; 4-tier architecture,
8-ship roadmap, privacy posture, firmware-team open questions).

Operator-added 2026-05-10 PM: the Sonoff S31 hardware ships with
a **CSE7766** chip (originally noted as HLW8032; corrected in the
design doc per Tasmota / ESPHome). Measures
**voltage / current / instantaneous power / cumulative energy +
frequency via zero-crossing**. We currently throw all of that
away. Add full ingestion + storage + analytics.

**Scope.**

- **Firmware-side (coordinate with firmware team).** Device must
  emit a periodic power-sample payload — voltage (V), current
  (mA), real power (W), reactive power (W), apparent power (VA),
  power factor, frequency (Hz), accumulated energy (Wh since last
  reset). Cadence proposal: 1 sample / 10 s in steady state,
  buffered locally for ≥ 1 hour to ride out central outages.
- **Hub-side ingestion.** New `POST /api/v1/device/power-samples`
  endpoint accepting a batch (same auth as `/device/events`).
  Up to 360 samples per batch (1 hour worth at 10 s cadence).
- **Storage.** New `device_power_samples` table:
  `(id, device_id, sampled_at, voltage_v, current_ma, real_power_w,
  reactive_power_w, apparent_power_va, power_factor, frequency_hz,
  energy_wh_since_boot, source)`. Indexed on
  `(device_id, sampled_at)` for time-series queries.
- **Rollups (perf).** Hourly + daily aggregates table
  `device_power_rollups(device_id, bucket, granularity, kwh,
  avg_w, peak_w, min_w, sample_count)`. Nightly job builds the
  prior-day rollup; on-demand fill for arbitrary ranges via a
  query view.
- **UI surfaces.**
  - Device detail page → new **Power** tab: live last-sample card +
    24h kWh / peak / avg chart + 7d rollup table.
  - Fleet-wide → new `/app/power` page: stacked-bar by-device for
    last 24h / 7d / 30d, sortable by kWh; top-N "biggest hogs".
  - Site-level rollup once site-as-scope ships (Tier A).
- **Export.** CSV / JSON download from any chart's range picker
  (max 90 days per request).
- **Cost calculation.** Operator-set `power.rate_per_kwh` runtime
  setting (DB → env → 0). Surface a "$XX this month" widget on
  device detail + fleet pages.
- **Alerting (later).** Threshold alerts ("device drawing > X W"
  for Y minutes; "device drawing 0 W when relay_on=true" → fault
  detection). Defer until base ingestion is stable.
- **Retention.** Raw samples kept for 30 days default (tunable
  via `power.sample_retention_days` runtime setting); rollups
  kept forever (cheap — ~365 rows/device/year).

**Effort estimate.** ~20-30 h of hub-side work + firmware-team
coordination. Probably 4-5 ships across a v0.6.x or v0.7.x sprint
(this slots into Tier C of redesign-continuation-plan-v2.md,
between notifications and webhooks).

**Dependencies.**
- Firmware team must add the device-side sampling + buffered
  upload (independent track; we can build hub-side ingestion +
  storage first against synthetic samples for testing).
- Pairs naturally with **C1** (history source extension) — power
  samples should be queryable via the unified history feed too.

**Open questions for later.**
- Calibration: does the HLW8032 need per-unit calibration, or is
  factory calibration good enough? (Firmware team owns this.)
- Multi-phase / 3-phase support: out of scope for v1 (S31 is
  single-phase only).
- Real-time streaming (WebSocket) for live dashboards: defer until
  there's a clear UX need; polling at 10 s is enough for most
  views.

### B18. Inline on/off toggle in the devices list — **FIXED in v0.5.14** (filed 2026-05-11)

Operator-flagged 2026-05-11 AM: the devices list at `/app/devices`
has no inline power control. Operator has to drill into the device
detail page to fire relay_on / relay_off. Every commercial smart-
plug app (Kasa, Shelly, Home Assistant, SmartThings) puts a toggle
on the row itself; we don't.

**This was in the redesign plan and never shipped.**
`docs/webui-redesign-plan.md` Phase 2 §9.2:

> Device list: card layout on mobile, table on desktop, saved-
> filter chips with URL round-trip (R-DEV-3, R-DEV-4, R-DEV-5).
> Device card: **inline switch + badge cluster**; QA badge from
> v0.2.8 preserved; central-vs-local cue (R-DEV-2).

R-DEV-2 was supposed to ship in P2 (around v0.3.1) and it didn't.
The list got chip filters, bulk-select, the firmware-version
breakdown card, and (much later) the upgrade button — but never
the inline switch. Operator is right to call this out.

#### Scope

Two parts:

1. **Backend**: surface `relay_on` + `mode` + `is_held_off` from
   the latest heartbeat in the device-list API response. Today
   `GET /api/v1/admin/devices` returns those fields as `None`
   because we serialise from the `Device` row, not from
   `DeviceHeartbeat`. Fix: join the latest heartbeat row per
   device in the list query. ~1h.

2. **Frontend**: add a 4th-column control on each list row:
   - Devices reporting `relay_on=true` → green "ON" toggle button
     that POSTs `relay_off` on click.
   - Devices reporting `relay_on=false` → grey "OFF" toggle that
     POSTs `relay_on`.
   - Devices in `is_held_off=true` → grey "HELD" badge (cannot
     toggle without clearing the hold-off first; tooltip
     explains).
   - Devices with `is_protected=true` → toggle present but
     guarded by the same typed-confirmation modal as the detail
     page (operator typed the device name to override).
   - Devices `online=false` → toggle disabled with "offline"
     tooltip (queueing a command at the hub still works, but
     the operator should know the device won't act on it until
     it reconnects).
   - Mobile (≤ 640 px) layout: toggle stays leftmost-after-name
     so it's thumb-reachable.

   Reuse existing `POST /app/devices/<id>/commands` form-submit
   path. No new endpoints. ~2h.

3. **Per-row response feedback**: the form-submit currently does
   a full-page redirect. Optional HTMX-ish enhancement: PATCH
   the row in place after submit using a JSON response. Defer to
   a follow-up; the redirect is fine for v1.

**Total effort**: ~3-4 h for parts 1 + 2.

#### Newly important now that the firmware bug is fixed

The relay command dispatcher only got fixed in 0.1.6-dev-central
(operator-verified 2026-05-10 PM end-to-end). Before that the
toggle would have been a footgun — clicking it on a 0.1.3 device
would have appeared to do nothing, blamed the hub, and frustrated
the operator into thinking the list-row toggle was the broken
piece when the real bug was 6 layers down. Now that the firmware
side actually executes relay_on / relay_off and reports back, the
inline toggle becomes the satisfying single-click action it should
have been at v0.3.1.

#### Where it lands

Slots cleanly into **Tier E polish or as a hotfix-style v0.5.x**
ship. Not blocked by anything. Reasonable next ship after A2
shadow-mode middleware, or interleaved with the Tier-1 power-
monitoring ingestion if we go synthetic-first.

#### Honest note on why this was missed

The redesign plan's Phase 2 shipped piecemeal across v0.2.x and
v0.3.x. The "inline switch" item was bundled with the larger card-
layout redesign, which itself was rescoped during the firmware
bring-up sprint (v0.4.0 onwards). It fell off the active checklist
and never made it back. The continuation plans (v1 and v2) both
inherited this miss without flagging it. Worth a once-over of
the rest of R-DEV-* to find other Phase-2 items that quietly
didn't ship.



Operator-added 2026-05-11 AM: the watchdog rules engine today fires
on **internal** signals (ICMP / HTTP probes, device events,
schedules). Extend it to fire on **external** signals so rules can
say things like:

> "If Roku in living room is on Spectrum TV between 7:00-7:30 PM
> weekdays (i.e., Jeopardy is on), turn off the speaker subwoofer."

The full goal — "detect Jeopardy specifically and turn off X" — is
a chain of inferences with different difficulty levels. The ideal
implementation isn't required; "as best as it could be implemented
is okay". We bake the easy 80% first, leave hooks for the hard
20%.

#### Layer 1 — Roku app-active sensor (~4-6 h)

Roku exposes a documented LAN-local HTTP API on port 8060
(Roku ECP — External Control Protocol). No auth, same-LAN only.
Useful endpoints:

- `GET /query/active-app` — returns the currently-foreground app
  (e.g., "Spectrum TV", "Netflix", "YouTube", or "Roku" if idle on
  the home screen). Updates in real time.
- `GET /query/device-info` — model, serial, name.
- `GET /query/media-player` — playback state (playing / paused /
  stopped) for media-aware apps.

**Hub-side build**:
- New "Roku source" registered on `/app/settings/integrations`
  taking the Roku's IP + a friendly name. We poll
  `/query/active-app` every 30 s and store last-known app + last-
  seen time per Roku in a new `external_sensor_samples` table.
- A new rule probe type: `roku_app_active`. Operator picks the
  Roku source + an app name (free-text or autocomplete from
  recently-seen apps). Probe outcome `success` when the named
  app is the current foreground app, `failure` otherwise.
- Wires into the existing watchdog rules surface — same trigger /
  action semantics, just a new probe source.

**What this gets you immediately**: "if Roku-A is on Spectrum TV
right now, do X." About 80% of the Jeopardy use case without
needing to know what's on the screen, because TV viewing is a
*time-bounded* activity — the operator can pair the app-active
probe with the existing schedule primitive (B8) to get
"Spectrum TV active AND it's currently in the Jeopardy time
window" rules.

#### Layer 2 — EPG (Electronic Program Guide) lookup (~8-12 h)

Gives the rules engine real "what show is on right now on channel
X" answers without an operator manually typing time windows.

**Source options**:
- **TVMaze API** (https://www.tvmaze.com/api) — free, no auth,
  has US schedule data, returns "what's airing now on this
  channel/network" via `GET /schedule?country=US&date=…`. Best
  starting point for a free integration.
- **Schedules Direct** (https://schedulesdirect.org) — $25/yr
  but is the canonical NA EPG source most DVRs use; way more
  accurate than free sources for cable-specific channel lineups.
- **Gracenote / TMS** — gold standard but enterprise pricing;
  out of scope.
- **Local OTA tuner scraping** (`zap2it` etc. — deprecated):
  skip.

**Hub-side build**:
- New `external_epg_cache` table: `(provider, channel_id,
  airing_start, airing_end, show_title, season, episode)`. We
  poll the EPG provider every 4-6 hours for the next 24 h of
  programming on whitelisted channels. Cache hits answer "what's
  on channel X right now" in O(1).
- New rule probe type: `epg_show_airing`. Operator picks a show
  title (e.g., "Jeopardy!") + a channel (e.g., "ABC affiliate
  for region 90210"); probe succeeds when the EPG says that show
  is currently airing on that channel.
- Combined with Layer 1: "Roku on Spectrum TV AND EPG says
  Jeopardy is airing on Spectrum channel 27" → 95% reliable
  Jeopardy detection.

**Open question**: Spectrum's channel numbering is regional. Need
operator to map "the channel name as Spectrum displays it" to "the
EPG provider's channel id". One-time setup per Roku location.

#### Layer 3 — Spectrum-specific (skip / very hard)

Spectrum doesn't expose a "what channel is the box on right now"
API. Roku knows the app is Spectrum TV but not which channel
inside the app. **Skip this layer**; rely on Layer 2 + time window
heuristics for the same outcome.

If we ever need true channel awareness, last-resort options would
be screen OCR (capture Roku screenshot via ECP, OCR the
channel-number overlay) — fragile and creepy. Defer indefinitely.

#### Adjacent external sources worth scoping in the same backlog

Same protocol shape (poll an external source, store samples,
expose as a rule probe). Each is roughly Layer-1 effort:

| Source | What rules can use it for |
|---|---|
| **Home Assistant** integration | Bridge to literally everything HA already supports — z-wave / zigbee / matter sensors, presence, door state, motion. If operator has HA running this is the highest-ROI integration. |
| **MQTT broker** | Generic pub/sub — any device that speaks MQTT can trigger rules. Pairs well with Tasmota / ESPHome devices. |
| **Plex / Jellyfin** | "If TV-watching session active on Plex, [do X]" — webhook-based, real-time. |
| **Google Calendar** | "If meeting on calendar AND device in office, ensure power-on" or "If 'do not disturb' calendar event, pause watchdogs". |
| **Weather** (NWS, OpenWeatherMap) | "If storm warning, cycle the modem proactively"; "If outside temp > 90°F, ensure AC plug stays on". |
| **Computer activity** (wake/sleep ping) | "If workstation has been awake 8 h, suggest break" — softer use case but compositional with calendar. |
| **iCal / WebCal feeds** | Same as Google Calendar but generic. |
| **Solar production** (Enphase / SolarEdge local APIs) | "If solar output > home load, run high-watt appliances" — energy-savings rules, pairs with B16 power monitoring. |
| **iOS Shortcuts / Apple Home webhooks** | Operator-side automation triggers if they have an iPhone. |

#### Effort estimate + dependencies

- Layer 1 (Roku ECP + new probe type): ~4-6 h. Independent of
  RBAC, fits cleanly into existing watchdog-rule machinery.
- Layer 2 (EPG cache + probe type): ~8-12 h. Independent.
- Adjacent integrations: each ~4-6 h, all share the
  `external_sensor_samples` table pattern.

Total for "comfortable v1 covering Roku + EPG + HA bridge":
~20-30 h.

#### Privacy + ethics

External-source data is at least as sensitive as power-monitoring
data:
- Roku-app-active reveals media consumption habits.
- Calendar integration leaks meeting subjects and timing.
- HA-bridged sensors leak motion / occupancy / sleep patterns.

Same posture as B16: local-first, aggregate only, no occupancy-
heatmap UI, hard-delete on integration removal.

#### Where this lands in the redesign plan

New **Tier G** (post Tier F / B16 power monitoring), or a sibling
to Tier C (notification rules) since both extend the rules engine.
Probably a separate doc — `docs/B17-external-integrations-design.md`
— once the operator wants to commit. Until then, this backlog
entry is the durable home for the idea.

#### Quickest demo path (if operator wants a single-evening proof)

Ship Layer 1 only against a single Roku for the Jeopardy use case:
1. Add an `external_rokus` table + nightly-cached app-active samples.
2. Wire `roku_app_active` probe type into watchdog rules.
3. Operator builds the rule by hand:
   "rule: ROKU(living-room) = 'Spectrum TV' AND schedule(weekdays 19:00-19:30) → relay_off (subwoofer)".

That's ~4 h of work and delivers 80% of the dream without needing
EPG, OCR, or Spectrum-specific anything.

### B19. Firmware scan misses content-changed binaries with same filename — **FIXED in v0.5.13** (filed 2026-05-11 PM)

Operator-hit 2026-05-11 PM: when the sub-firmware team replaces
`rebooter-0.1.9-dev-central.bin` with a new build (same filename,
different SHA — typical iterative-fix workflow), the scan in
`app/services/firmware.py::discover_on_disk_releases` skips it as
"already existing" because the dedupe key is `entry.name in
existing` (filename only).

Result: the hub registry still has the OLD SHA. Devices polling
`/device/firmware` get the new bytes from disk but the SHA in the
hub response is stale → device's SHA verification fails → OTA
refuses to flash, even though both sides are operating correctly.

Live evidence today: sub-FW team rebuilt 0.1.9 to fix BearSSL heap
exhaustion. New on-disk SHA `5002836883a4...`. Hub-registry SHA
still `a6b415b3aa28...` until I manually `UPDATE firmware_releases`.

**Fix scope** (~1 h):

In `discover_on_disk_releases`, when `entry.name in existing`,
also re-compute the SHA of the on-disk file and compare to the
existing row's `sha256`. If different:
  1. Update `firmware_releases.sha256` + `size_bytes` for that row
  2. Update all `firmware_release_mirrors` rows for that release_id
     to set `verified_sha256` + `last_probed_at`
  3. Return the row in a new `updated` (or `content_changed`) list
     in the scan response so the operator-visible flash message
     surfaces the change
  4. Audit event: `firmware.content_updated` with old + new SHA

Edge case: if `firmware_releases.size_bytes` differs but
`sha256` matches (unlikely but theoretically possible with
identical-payload-different-padding), still update size_bytes
for accuracy.

Additional defence the firmware-team can do too: when shipping a
new build, increment the version string (e.g. `0.1.9.1`, or
`0.1.9-r2`). That avoids the issue entirely because the new
filename hits the existing-by-name check correctly. But the hub
should be robust to content-change-with-same-name regardless.

Tracked together: `firmware.content_updated` should also surface
on the unified `/app/history` feed (C1 — already shipped) so
operators can audit "did anyone secretly swap a firmware on us?"

### B22. `discover_on_disk_releases` writes wrong `download_url` for scanned releases — **NEW 2026-05-13** — FIXED in v0.5.11

Firmware-team caught 2026-05-13 PM after end-to-end UI-driven OTA
test: device on 0.1.15 received a `/device/firmware` response
pointing at the canonical root URL
`https://www.voipguru.org/rebooter/firmware/rebooter-0.1.15-dev-central.bin`
and got HTTP 404 because the scanned `.bin` actually lives at
`https://www.voipguru.org/rebooter/firmware/stable/rebooter-0.1.15-dev-central.bin`
(per-channel subdirectory).

Root cause in `app/services/firmware.py::discover_on_disk_releases`:

```python
download_url = f"{base}/{entry.name}"          # ← root path, but...
per_channel_url = f"{base}/{channel}/{entry.name}"  # ← ...file is HERE
```

The scan loop only walks `firmware_dir/<channel>/`, so every
scanned artifact lives at the per-channel path. The upload path
(`save_release` ~:130) gets away with the same `{base}/{filename}`
shape because it copies the file to BOTH locations — the scan path
does no such copy. Device reads `release.download_url` verbatim
(`app/blueprints/device_api.py:230`), so the field-level mismatch
produces the 404.

**Fix scope**: one-line — set `download_url = per_channel_url` in
the scan path. Mirror rows (`local`, `local_per_channel`,
`local_channel_pointer`) keep their current shapes for operator
visibility into all three URLs. Existing scanned rows in the DB
need a one-shot UPDATE to rewrite their `download_url` for any
release where `download_url` points at root + no file is there.

Operational workaround applied 2026-05-13 PM by firmware team:
manually duplicated the current `.bin` at the canonical root so
the live bench test could continue while the hub fix lands.

### B21. Desired-config blob per device + drift detection + push-on-restore — **NEW 2026-05-13**

QA team flagged 2026-05-13 after the v0.5.7 restore-after-reflash
work: the hub restore preserves device identity (id + display_name
+ groups + schedules) but doesn't push the hub's intended config
back DOWN to the device. Reflashed Erica's Subwoofer kept local
`device_name="Rebooter"` even though hub-row display_name was
"Erica's Subwoofer".

v0.5.8 ships a short-term fix: on restore-success, auto-enqueue
`apply_config {device_name: <display_name>}`. Solves the
observed name drift; doesn't solve the broader "hub has no
desired-config to push" gap.

#### Scope (~6-8h)

Schema (additive nullable, no migration risk):
- `devices.desired_config` JSON — operator-set intended config
  matching the locked v0.1 apply_config schema (`device_name`,
  `relay_restore_behavior`, `monitor_interval_seconds`,
  `boot_warmup_seconds`, `manual_button_enabled`, `internet`,
  `device`, `notifications`)
- `devices.desired_mode` VARCHAR(40) — intended mode (smart_plug
  / internet_watchdog / device_watchdog)
- `devices.last_reported_config` JSON — device's most recent
  self-reported config; populated either by a new field on
  heartbeat payload OR by parsing apply_config command-result
  payloads (depends on firmware-team apply_config response shape)
- `devices.desired_config_updated_at` TIMESTAMPTZ
- `devices.last_config_pushed_at` TIMESTAMPTZ

Service module `app/services/device_config.py`:
- `get_desired_config(device_id)` → dict | None
- `set_desired_config(device_id, payload, by_user_id)` → audited
- `push_desired_config(device_id, *, source: 'restore'|'manual'|'drift_repair')`
  enqueues `apply_config` + optional `set_mode` derived from the
  desired_config; audit-logs
- `compute_drift(device_id)` → dict listing fields that differ
  between `desired_config` and `last_reported_config`

UI on `/app/devices/<id>`:
- New "Desired config" sub-card next to existing relay/status
  controls
- Per-field editors covering the locked apply_config schema
- "Push to device now" button
- Drift indicator badge ("3 fields out of sync") with details
- Optional toggle: "Auto-push on heartbeat-detected drift"
  (default OFF; for now only manual + restore-time auto-push)

Auto-push triggers:
- restore via `/pending-adoption/restore` (the v0.5.8 short-term
  short-circuit gets replaced by this richer path)
- operator clicking "Push to device now"
- optional drift_repair (gated on per-device opt-in flag)

Feature flag: `desired_config.enabled` runtime_setting, default
false through v0.6.x, flips on at v1.0.0 after operator
validation. Devices with no `desired_config` row behave identically
to today.

Tests (QA's Option C):
- Each top-level section round-trips: hub edit → push → device
  apply → device-reported config matches
- restore-after-reflash pushes display_name + mode + watchdog
  fields (the full desired blob, not just display_name)
- decommission-and-adopt-fresh does NOT push prior settings
  (clean slate by design)

Dependencies:
- Firmware-team has confirmed apply_config full-schema support;
  need exact nested-schema for `internet`, `device`,
  `notifications` (hub docs/API.md only names them as allowed
  top-level keys — substance is firmware-side).
- For drift detection: need either a richer heartbeat payload
  (current state of each apply_config field) OR a documented
  apply_config response-result shape we can parse.

Slots between Tier C and Tier D in plan v2 (operator-facing UX
work). Estimated v0.6.0 candidate.

### B20. MAC-based duplicate detection at adoption — restore-vs-fresh choice — **NEW 2026-05-12 PM**

Firmware team flagged 2026-05-12 PM after a real production incident
on 192.168.1.30 (MAC `C4:D8:D5:0C:F7:A5`): a reflashed device went
through announce → adopt → register cleanly, but got a FRESH
`device_id` (`dev_01KRH81ASVCMHZ7SXC72J0RHPH`, display_name
"Rebooter") even though the same MAC was already registered as
`dev_01KR8127W5XMP6MDF34J0TXQP9` ("Erica's Subwoofer"). Two
device_ids, one physical box. Orphan audit history + group
memberships + schedules + watchdog rules on the old row.

Firmware team observations (their summary):
1. ✅ Firmware sends MAC on both /announce and /register.
2. 🟡 Hub's `pending-adoption` service upserts announcements by MAC,
   but `adopt()` always mints a brand-new enrollment token, and
   `/device/register` always creates a new Device row. No
   replace-vs-fresh choice.
3. 🟡 `/app/pending-adoption` UI shows display_name + Adopt/Reject;
   no duplicate-MAC warning, no existing-device match card, no
   restore/replace path.
4. ✅ Firmware can support either outcome (replace OR restore) as
   long as the hub makes the choice explicit.

#### Fix scope (hub-side, ~3-4h)

Schema:
- Add `target_device_id` column to `enrollment_tokens` (varchar(40),
  nullable, FK to `devices.id` ON DELETE SET NULL). When set, tells
  `/device/register` to rebind the existing device row instead of
  creating a new one.

Service layer (`app/services/announcements.py` +
`app/services/enrollment.py`):
- `find_existing_devices_by_mac(mac)` → list[Device] (cheap query
  against `devices.mac_address`)
- `adopt(announcement_id, ..., mode='fresh'|'restore',
        target_device_id=None)` — when mode='restore', mints
  an enrollment token with `target_device_id` populated
- `mint_enrollment_token(..., target_device_id=None)` — passes
  through to the new column

Device API (`app/blueprints/device_api.py::register`):
- After validating the enrollment token, check if it has a
  `target_device_id`. If yes:
  - Look up that device row; verify MAC matches the registering
    payload (defensive — should always be true if operator picked
    restore correctly)
  - Update existing row's `last_ip`, `firmware_version`,
    `registration_state='active'`, `last_heartbeat_at=NULL` (will
    populate on first heartbeat)
  - Rotate `device_credentials.token_hash` to the new bearer
  - Return the EXISTING `device_id` (not a fresh one)

UI (`templates/pending_adoption.html` +
`app/blueprints/admin/pending_adoption.py`):
- For each announcement in the list, also compute and pass
  `existing_devices_with_same_mac` (list of matching device rows)
- Template renders: if matches exist, show a yellow "Duplicate
  MAC detected" card with each existing device's
  `display_name + id + last_heartbeat_at + firmware_version` and
  TWO new action buttons per match:
    - **Restore to this device** — calls `/app/pending-adoption/
      <announcement_id>/restore/<existing_device_id>`
    - **Decommission old + adopt fresh** — marks old row
      `registration_state='decommissioned'`, then standard adopt
- Existing **Adopt as new** stays available (with an inline
  warning when duplicates exist)

Audit events:
- `device.restored_from_reflash` — on successful restore path
- `device.adopted_with_mac_duplicate` — on fresh adoption while
  same-MAC device row existed (operator chose "Adopt as new"
  despite the duplicate warning)
- `device.decommissioned_for_replacement` — when old row marked
  decommissioned during "Decommission old + adopt fresh" flow

Tests:
- Restore preserves device_id + audit history + group memberships
- Fresh-with-warning path increments device count + leaves old
  row intact
- API endpoint `/device/register` rejects mismatched MAC against
  `target_device_id` (defensive)

#### Cleanup for the CURRENT production dupe (.30 / `C4:D8:D5:0C:F7:A5`)

Before B20 ships, the existing dupe needs operator-decision
handling. Options:

a) **Wait for B20 to ship, then run a one-shot reconciliation**:
   update `dev_01KRH81ASVCMHZ7SXC72J0RHPH` rows to use the old
   `dev_01KR8127W5XMP6MDF34J0TXQP9` id (rebind credentials,
   delete the new row). Requires brief device downtime for token
   re-issue.

b) **Manual SQL merge now** (no B20 dependency): copy
   `display_name + notes + central_management_enabled + site_id`
   from old row to new row; rename new row to "Erica's
   Subwoofer (reflashed 2026-05-12)"; mark old row
   `registration_state='decommissioned'`. Audit history stays
   split.

c) **Leave as-is** for now and clean up when B20 ships.

#### Sequencing

B20 lands BEFORE the 4 bricked Erica's speakers get reflashed and
re-adopted, otherwise we'll have 4 more dupes. Probably the next
ship after the immediate refactor/B18 sprint.

---

## How to consume this list

When the operator says "continue":

1. Check **P1** top-down — that's the operator-locked sprint.
2. If everything in P1 is blocked (e.g., waiting on firmware team or
   a redline), drop into **P2** and pick the top unblocked item.
3. **Never** start a P3 item without operator sign-off on the gating
   redline.
4. After completing an item, update this file (move to a "Done" log
   if useful, or just remove the line + add a CHANGELOG entry).

The pause-state doc (`docs/PROJECT-STATE-2026-05-09-FULL-SYNC.md`)
captures what's *been done*; this doc captures what's *next*.

### B23. Split "offline" into real operator-meaningful states on Devices / Status - **FIXED in v0.5.12** (filed 2026-05-14)

Observed live on `v0.5.11`:
- `192.168.1.225` was shown as **offline** in the hub UI
- local API still returned `200`
- device still reported `central_enabled=true`,
  `central_registered=true`
- `central_state="firmware_check_transport_failed"`

So "offline" currently conflates:
- truly unreachable device
- central heartbeat stale / transport failure
- potentially other central-side failure modes

Fix scope:
- Devices page and Status page should surface at least:
  - `online`
  - `central stale` / `transport failed`
  - `offline`
- If possible, include the device-reported `central_state` as a chip
  or secondary status sentence
- Long-term: helper/peer reachability can become another dimension,
  but not required for the first fix

Acceptance:
- A device like `.225` no longer presents identically to a truly dead
  device like `.69`
- Operators can tell "plug is alive but not talking to central" from
  "plug is gone"

### B24. Finish desired-name reconciliation beyond restore-after-reflash - **FIXED in v0.5.12** (filed 2026-05-14)

Observed live on `v0.5.11` + current firmware fleet:
- `.48` matches after manual `apply_config.device_name` push
- `.30` hub name != local device name
- `.225` hub name != local device name
- `.207` hub name != local device name

Interpretation:
- `v0.5.8` restore-name auto-push is real
- the broader desired-name contract is still incomplete

Fix scope:
- ordinary device rename must enqueue / reconcile `device_name`
- hub should be able to detect and optionally surface name drift
- this should eventually fold into B21 desired-config blob work

Acceptance:
- rename a device in the hub UI
- device local UI / `/api/status` / `/api/config` converge to the
  same name without requiring restore-after-reflash
- at least one regression test covers the non-restore path
