# Backlog

Last updated: **2026-05-09 PM** (post v0.4.0).

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

- Today the only way to add a user is the operator-only
  "create user" form. The operator wants admins/super-admins to
  invite peers via email.
- Token shape: `{user_id, email, role, expires_at}` signed with the
  hub's HMAC key. 30-day expiry on every invite.
- Land at `/auth/accept-invite?token=<...>` — landing page shows the
  invitee email + role and asks for password.
- One-time-use: redeemed tokens get marked consumed in DB.
- Audit hooks: `invite.created`, `invite.consumed`,
  `invite.expired_or_revoked`.
- Depends on **B4 (SMTP)** for actually sending the email.

### B3. Password-reset UI

- Forgot-password link on `/auth/login`.
- Same SMTP credentials as B2.
- Same 30-day-default token expiry but defaults to **1 hour** for
  password reset (security-sensitive).
- Reuses the audit hooks from B2.

### B4. SMTP from coordinator-hub creds

- Use `dblagbro@earthlink.net` as outgoing.
- Pull SMTP host/user/pass from the **coordinator hub** the same way
  the hub itself sends operator notifications. Specifically:
  - Coordinator-hub stores SMTP config in `hub_settings.smtp.*`.
  - Rebooter-droids should NOT hard-code these — read from the same
    backing store, or have the operator paste them once into Settings
    → Notifications.
  - First-class admin Settings → Notifications tab with editable
    fields, a "Send test email" button that posts a one-line test
    to the logged-in operator's email.
- Output is a single internal helper `email_service.send(...)` that
  B2 + B3 + future watchdog notifications all consume.

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

### B6. Watchdog probe runtime (v0.4.1+, P4 iteration 2)

- v0.4.0 ships data-model + UI + sentence render only. The probe
  runtime that actually fires the rules is the next slice.
- Components:
  - APScheduler job per enabled rule, cadence = window_seconds.
  - Per-rule probe dispatcher (one function per probe-kind).
  - Inserts rows into `watchdog_probe_events` on each result.
  - Threshold-cross logic + cooldown enforcement.
  - Action dispatcher: cycle / hold_off / notify_only.
- Surfaces:
  - Per-rule event log on the rule detail page.
  - "Probe now" + "Simulate trigger" buttons.
  - Status inbox attention items for `watchdog.firing`.

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
