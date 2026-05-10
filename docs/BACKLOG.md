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

Operator-added 2026-05-10 PM: the Sonoff S31 hardware ships with
an HLW8032 chip that measures **voltage / current / instantaneous
power / cumulative energy**. We currently throw all of that away.
Add full ingestion + storage + analytics.

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
