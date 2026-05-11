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

✅ **Partially shipped in v0.4.1.** Settings → Notifications tab
shows env-var SMTP config + a "Send test email" form (audit-
logged). The single internal helper `email_service.send(...)` is
already in use by invitations + password-reset.

⏳ **Still open:** *runtime-editable* SMTP settings. Today values
come from env vars (`REBOOTER_SMTP_*`); the operator wanted them
seeded from the coordinator-hub at deploy time, **OR** editable
in the UI. Either of these is a follow-up — not blocking anything.

> Status: **partially DONE; runtime-editable settings deferred to a
> v0.4.2+ slice when an operator needs to change SMTP without a
> container recreate.**

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

- Native ICMP ping probes (today the runtime falls back to TCP-80).
- `gateway` probe — no-op until device firmware reports its LAN
  gateway in heartbeat.
- Tag-as-target dispatch — shape exists; resolution stubbed.
- Status inbox attention item for `watchdog.firing`.

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

### B17. External-source rule triggers (Roku / TV / calendar / weather / etc.) — **NEW 2026-05-11**

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
