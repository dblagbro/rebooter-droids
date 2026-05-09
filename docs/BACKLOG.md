# Backlog

Last updated: **2026-05-09 PM** (post v0.4.2).

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

### B7. Maintenance windows + portal-wide maintenance mode (v0.4.1+)

- Per-rule maintenance_windows JSON shape (cron-ish blocks).
- Portal-wide "suspend all rules" toggle so the operator can do a
  scheduled site reboot without false-positive firing.
- Audit hook: `maintenance_mode.toggled`.

### B8. Schedules as a separate primitive (v0.4.1+)

- Recurring power-cycles + recurring maintenance windows.
- Distinct from watchdog rules: rules fire on probe failure;
  schedules fire on time.

### B9. Watchdog rule advanced editor

- Today the form is a fixed-shape builder. Add a JSON editor for
  rules whose probe / target / escalation shape doesn't fit the
  builder.
- Round-trips: edit-form → JSON → save → edit-form (lossless).

---

## P3 — RFCs awaiting redline (gated on operator response)

### B10. RFC-003 redlines #1–#4 (P5/P6 unblockers)

- RBAC migration plan, site-as-scope contract, audit-log retention
  policy, invite shape.
- Blocks: B1, B2.

### B11. RFC-004 architecture pick

- Multi-hub sync. Five options scored; recommendation is **Option B
  (Postgres logical replication, active-passive)**.
- Operator hasn't picked yet. Until they do, the Settings → Sync tab
  remains a stub.

### B12. RFC-005 redlines (firmware-team Q1..Q9)

- Trial-window seconds, "main firmware healthy enough to promote"
  definition, fallback-fetch source under safe-bootstrap, etc.
- Blocks: any device-side OTA work; doesn't block the hub.

---

## P4 — small wins / housekeeping

### B13. Status inbox: surface `watchdog.firing` items

- Will need to land alongside B6 since rules don't fire yet.

### B14. Devices page: bulk-action audit log

- Today bulk actions audit the meta-action; the operator wants a
  per-device row for every device touched.

### B15. Settings → Sync tab content

- Replace the stub with real content once B11 is decided.

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
