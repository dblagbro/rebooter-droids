# UI redesign — continuation plan **v2** (2026-05-10 evening)

Supersedes `redesign-continuation-plan.md` (written 2026-05-10 morning,
pre v0.4.24..v0.4.28 + pre B10/B11/B12 closure).

This v2 retires every Tier-1 item from v1 (all shipped today), folds
in the B10 RBAC scope decision (`Site + Group + Device`, not just
Site-as-scope), and re-prioritises the remaining work into shippable
sprints.

---

## What landed between v1 and v2 of this plan

Same-day work that turns over what v1 said was "queued":

| v1 item | v1 status | Current status |
|---|---|---|
| Tier-1 #1 SMTP runtime-editable | ⏳ queued (3 h) | ✅ **v0.4.24 / v0.4.25** |
| Tier-1 #2 Network runtime-editable | ⏳ queued (3 h) | ✅ **v0.4.26** |
| Tier-1 #3 System runtime-editable | ⏳ queued (3 h) | ✅ **v0.4.26** |
| Tier-1 #4 API.md refresh | ⏳ queued (2 h) | ✅ **v0.4.27** |
| Tier-3 #8 History page filter coverage | ⏳ queued (1 h) | ✅ **v0.4.27** (chip nav + `action_prefix`) |
| Tier-2 #5 B10 RFC-003 RBAC redlines | ❌ blocked on operator | ✅ **CLOSED 2026-05-10 PM** (RFC-003 §9.0) |
| Tier-2 #6 B11 RFC-004 architecture pick | ❌ blocked on operator | ✅ **CLOSED 2026-05-10 PM** (RFC-004 §10b — picked **Option C**, peer-to-peer outbox, overriding the doc's original Option-B recommendation) |
| Tier-2 #7 B12 RFC-005 firmware redlines | ✅ already closed in v1 | ✅ closed (no change) |

Plus an emergency fix:

- **v0.4.28** — restored the one-click Upgrade button on `/app/devices`
  (NameError regression introduced in v0.4.21).

So the v1 "Proposed next 4 ships" table is **100% retired**, and three
of the operator-input-gated items are **unblocked**.

---

## Status update — 2026-05-11 00:56 UTC (post v0.5.1)

**Currently active inside this v2 plan.** 10 ships since v2 was written:

| Ship | Tier | Plan item |
|---|---|---|
| v0.4.27 | C/E | History chip filters + API.md refresh (closed C1 prep + Tier-3 #8) |
| v0.4.28 | bug | Upgrade-button NameError hotfix |
| v0.4.29 | bug | Upgrade-button direction (offering downgrades) hotfix |
| v0.4.30 | **C1** | Unified history feed (audit + watchdog_probe + device_event + all) |
| v0.4.31 | **E5** | Device-add wizard polish (site selector + TTL + cross-link) |
| v0.4.32 | **C2 + C3** | History CSV/JSON export + free-text search |
| v0.4.33 | **D3** | Firmware UI moves under `/app/settings/firmware` |
| v0.4.34 | bug | Firmware scan `os.sync()` before walk (bind-mount cache miss) |
| v0.5.0 | **A1** | `role_bindings` table + one-shot backfill from legacy users columns |
| v0.5.1 | bug | v0.5.0 backfill over-granted bindings to operators; corrected |

**Current position**: just landed **Tier A / A1 (v0.5.0 + v0.5.1)**.
Backfill verified live: 1 super_admin global binding + 110 site-admin
bindings (22 admins × 5 sites), 0 incorrect operator/viewer bindings.
Legacy auth still authoritative; the table is shadow-only until A8.

**Next ship**: A2 shadow-mode middleware. The 7-day shadow soak before
A8 enforce flip begins on its deploy.

**Updated tier rollup**:

| Tier | Done | In flight | Queued | Notes |
|---|---|---|---|---|
| **Trailing v0.4.x quick wins** | C1, C2, C3, D1, D3, E5 | — | — | D2 deferred (no automated backup yet); arc complete |
| **Tier A — RBAC scoping** | A1 | A2 (next) | A3-A9 | 7-day shadow soak gate between A2 and A8 |
| **Tier B — Auth (TOTP / OIDC)** | — | — | B1-B5 | Gated on A1+A2 (now landed / next) |
| **Tier C — remaining** | (C1+C2+C3 above) | — | C4-C7 | C4-C5 = notifications surface; C6 = webhooks; C7 = API tokens (needs A1 for scope) |
| **Tier D — remaining** | (D1+D3 above) | — | D4 | D2 deferred; D4 backup/restore queued |
| **Tier E — remaining** | (E5 above) | — | E1-E4 | a11y CI, onboarding tour, command palette, pull-to-refresh |
| **Tier F — B16 power monitoring** | Design doc | — | F1-F8 ships | Hub-side Tier-1 ingestion can ship against synthetic samples; firmware-team metering driver in parallel |

**External blockers still live**:
- **Windows workstation OFFLINE** since 2026-05-09 (Chrome Remote Desktop stuck "starting up"). Firmware repo at `C:\dev\rebooter-firmware\` unreachable. Firmware iteration fully blocked. Hub iteration unblocked.

---

## Scope-shape correction from B10

The original `webui-redesign-plan.md` §6 + RFC-003 §9.1 modelled
scope as **Site only** (`site_memberships` table; per-site roles).
B10/Q1 explicitly upgraded that to **Site + Group + Device**.

Concrete consequence for the remaining Phase 5 work:

- `site_memberships` becomes a more general `role_bindings` table:
  `(user_id, scope_type ∈ {'site','group','device','global'}, scope_id, role)`.
- Scoping middleware now resolves the *effective set* of devices a
  user can act on by unioning:
  1. devices in any site they have a binding for
  2. devices in any group they have a binding for
  3. devices they have a direct binding to
  4. all devices, if `scope_type='global'`
- Migration table mapping (B10/Q2):
  - existing super_admin → one row `('global', NULL, 'super_admin')`
  - existing admin → one row per current `site_id`
  - existing operator → **no rows** (forced re-grant by an admin)
- Invite shape (B10/Q4): role + scope (one or more
  `(scope_type, scope_id)` tuples) baked into the invite token at
  send-time. The invite-redemption flow shows the scope before
  activation.
- Audit retention (B10/Q3): new `system.audit_retention_days`
  runtime setting (DB → env-var → 365). Nightly soft-prune job
  copies old rows into an `audit_events_archive` table; archive
  purge is a manual operator step.

These overrides supersede the original §6 of `webui-redesign-plan.md`
where they conflict.

---

## Remaining work, re-prioritised

### Tier A — RBAC scoping sprint (unblocked by B10)

This is the big one. The original Phase 5 modelled this as ≥ 2 weeks
of work with a 7-day shadow-read window. With current scope-shape
clarifications it still needs careful staging but is finally
*unblocked*.

| # | Item | Effort | Notes |
|---|---|---|---|
| A1 | `role_bindings` table + Alembic migration + auto-migrate existing users | 4 h | site/group/device/global scope rows; super_admin → global, admin → all-current-sites, operator → empty |
| A2 | Scoping middleware in **shadow mode** + per-request "would-have-denied" log | 3 h | every existing decorator (`admin_required`, `role_required_*`) gets a parallel scope check that *logs* but doesn't enforce. 7-day soak in production. |
| A3 | Devices `site_id` non-null enforcement + backfill | 1 h | site exists today as an optional FK; flip to non-null with backfill to a default "Default" site |
| A4 | Scope-aware list/detail queries on `/app/devices`, `/app/groups`, `/app/sites`, `/app/history` | 4 h | filter at the SQL layer using the role-binding union |
| A5 | Invite redesign: role + scope at send-time + redemption UI | 3 h | site-multi-select + group-multi-select + device-multi-select on the invite form |
| A6 | Users page: per-user binding editor (`/app/settings/users/<id>`) | 3 h | add/remove scope rows; visual cue for inherited vs direct |
| A7 | Sites page: per-site members tab (`/app/sites/<id>` already exists; add a "Members" sub-tab) | 2 h | super_admin only; CRUD onto `role_bindings` |
| A8 | Cut-over: flip shadow → enforce in one ship | 1 h | feature flag default flips after one full week of clean shadow logs |
| A9 | `system.audit_retention_days` runtime setting + nightly soft-prune job + `audit_events_archive` table | 2 h | already designed in B10/Q3 |

**Total: ~23 hours.** Ships across roughly v0.5.0 → v0.5.4 if we
group A1+A2 (v0.5.0), A3+A4 (v0.5.1), A5+A6 (v0.5.2), A7 (v0.5.3),
A9 + A8 cut-over (v0.5.4). The version bump to 0.5 reflects the
schema migration and the user-visible RBAC change.

**A8 cut-over gate.** No enforce flip until shadow mode is clean for
≥ 7 days. RFC-003 §6.3 already enshrines this; B10 doesn't change it.

### Tier B — Auth surface (now unblocked by A1..A2)

Once role_bindings is live, the auth roadmap from original Phase 5
can ship:

| # | Item | Effort | Notes |
|---|---|---|---|
| B1 | TOTP 2FA enrolment (`/app/me/2fa`) + login challenge | 6 h | super_admin opt-in first; widening covered by RFC-003 §10.1 redline #3 (already locked: open to all by default after self-enrolment) |
| B2 | Google OIDC sign-in | 4 h | redirect URI + state cookie + linkable to existing user by email |
| B3 | GitHub OIDC sign-in | 4 h | same shape as B2; matches RFC-003 §10.1's locked pair |
| B4 | Profile page: active-sessions list + revoke (`/app/me`) | 2 h | `user_sessions` table already exists; just needs the UI |
| B5 | Change-password flow polish + edge cases | 1 h | low-priority but the form predates several auth changes; verify happy + error paths |

**Total: ~17 hours.** Likely a single v0.6.0 sprint since OIDC + TOTP
ship together cleanly and share the auth-page redesign.

### Tier C — Notifications + History extension

Original Phase 6 work. Most of these are independent of A/B.

| # | Item | Effort | Notes |
|---|---|---|---|
| C1 | Unified `/app/history` extension — surface watchdog probe events, power events, schedule fires, notification sends alongside audit | 4 h | audit-only chips ship in v0.4.27; this expands the source set with a `source=` query param |
| C2 | CSV / JSON export on `/app/history?export=csv\|json` | 2 h | streaming response; cap at 50k rows |
| C3 | Free-text search across `details` JSON column | 2 h | Postgres `jsonb` path query; index on `details` if perf needs |
| C4 | Notification rules surface (`/app/notifications`) — distinct from `/app/settings/notifications` (SMTP) | 6 h | email + webhook + Web Push channels; per-event-type routing |
| C5 | Notification-send log (separate table or audit slice) | 2 h | answers "did the operator actually get notified?" for ops post-mortems |
| C6 | Webhook config UX (`/app/settings/webhooks`) | 3 h | basic POST endpoint config + HMAC sig + test-send button |
| C7 | API token issuance (`/app/settings/tokens`) | 3 h | scoped tokens reusing role_bindings; show-once secret |

**Total: ~22 hours.** Probably v0.7.0 sprint. C1..C3 are a single
ship; C4..C5 are a second ship; C6..C7 are a third. C7 *requires* A1
(scope on tokens too); the rest are independent.

### Tier D — Settings UX completion

Smaller wins; can land any time.

| # | Item | Effort | Notes |
|---|---|---|---|
| D1 | Theme picker (`/app/settings/theme`) | ✅ **shipped v0.3.3** | already on disk; no work to do |
| D2 | Backup config visibility on System tab — last-successful-backup-at, link to coordinator-hub | 1 h | **deferred** until there's an automated backup mechanism to track (today's backups are operator-driven; nothing to surface) |
| D3 | Firmware settings sub-page (`/app/settings/firmware`) — moves `/app/firmware` UI under Settings, leaves a redirect | 2 h | mirror chain config from RFC-002 lands here |
| D4 | Backup / restore flow with dry-run diff | 6 h | original Phase 6; defer until D2 has run for a while |

**Total: ~11 hours.** D1..D3 are quick wins; D4 is a project unto
itself.

### Tier E — Polish + Phase 7 carry-over

| # | Item | Effort | Notes |
|---|---|---|---|
| E1 | WCAG 2.2 AA pass with axe-core or pa11y in CI | 4 h | original Phase 7 |
| E2 | Onboarding tour skeleton (3-step, dismissible) | 3 h | original P1 carry-over (R-UX-9) |
| E3 | Command palette (`Cmd / Ctrl-K`) — fuzzy search across devices, rules, schedules, settings | 6 h | R-UX-14; mobile-omitted |
| E4 | Pull-to-refresh on mobile list pages | 2 h | R-DEV-PTR; standard mobile pattern |
| E5 | Device-add wizard (`/app/devices/new`) with QR-code + enrolment-token flow | 4 h | R-DEV-6; pending-adoption already covers the no-token case so this is the "I have a token already" path |

**Total: ~19 hours.** Order: E5 first (real operator-value-add), then
E1 (compliance), then E3 (productivity), then E4, then E2 (least
load-bearing).

### Tier F — Power-usage monitoring + analytics (BACKLOG B16)

Operator-added 2026-05-10 PM. Sonoff S31 hardware has a **CSE7766**
chip (HLW8032 was an early incorrect assumption) that measures
voltage / current / power / energy. **Shipped** — B16 Phases 1A–1D
(v0.5.26–v0.5.32) plus P1.1–P1.3 follow-through (v0.5.54–v0.5.59).
See `docs/BACKLOG.md` **B16** for scope.

Slots between Tier C and Tier D in the natural sprint order
because it benefits from C1 (history source extension) for
queryability and pairs nicely with C4-C5's notification surface
for "device drawing 0 W while relay_on=true → alert" anomaly
detection. **Hub-side ingestion + storage can ship before
firmware** emits the data, against synthetic samples for testing.

Effort: ~20-30 h split across 4-5 ships (v0.6.x or v0.7.x).
Gated on firmware-team coordination for the device-side
sampling + buffered-upload protocol.

### Deferred (no near-term operator demand)

- **MQTT integration** — RFC-003 v2.
- **Home Assistant native integration** — v2.
- **Passkeys / WebAuthn** — RFC-003 noted as post-v1; B-tier deferral.
- **Public Web Push** — gated on a notification rule consumer (C4).
- **Mobile apps (iOS / Android)** — separate codebase, separate
  product decision.

---

## Refactored sprint order (proposed)

The dependency edges resolve to:
- A1..A8 must land before B (auth flows depend on scope) and C7
  (token scoping)
- C1..C6 are mostly independent of A/B
- D1..D3 + E5 are fully independent
- E1..E4 + D4 are polish; land last

So the natural sprint order is:

| Sprint | Ships | Theme |
|---|---|---|
| **v0.4.x trailing** (now) | D1 Theme picker + D2 Backup visibility + E5 Device-add wizard + C1 History source extension | Operator-visible quick wins; no schema change; gathers feedback while A is in design |
| **v0.5.x** (1–2 weeks) | A1..A9 in 4–5 ships; A8 enforce flip gated on 7-day clean shadow soak | RBAC scoping. The big one. |
| **v0.6.x** (1 week) | B1..B5 in 2 ships | Auth: TOTP + OIDC + profile polish |
| **v0.7.x** (1 week) | C2..C7 in 3 ships | Notifications + webhooks + API tokens |
| **v0.8.x** (1 week) | D3 D4 + E1..E4 in 2–3 ships | Polish + a11y + command palette |
| **v1.0.0** | Doc pass + release-notes + remove deprecated routes | Original Phase 7 close-out |

Total: ~5 calendar weeks of focused work; ~90 engineering hours of
implementation across all of it.

---

## What v2 deliberately does NOT change

- Server-rendered Jinja + minimal JS stays the framework choice. No
  React island migration unless responsiveness data forces it.
- Five-item nav (Status / Devices / Rules / History / Settings)
  stays.
- API stability: every change is additive on `/api/v1/admin/*`.
- DB-backed-with-env-var-fallback pattern stays — the empty-DB-on-
  fresh-deploy story keeps working without any operator setup.
- "No paid SaaS, no new external dependency" stays
  (`feedback_open_source_only.md`).

---

## What this captures that v1 missed

- The B10 scope upgrade (Site + Group + Device) and what it means
  for the role-bindings shape.
- The B11 decision to ship Option C (peer-to-peer outbox) for
  multi-hub sync — gated on Tier A landing because outbox events
  need to carry scope claims.
- The fact that v1's entire Tier-1 sprint shipped same-day.
- The v0.4.28 hotfix and the systemic risk it surfaced (handlers
  carrying dead lines that import undeclared names — worth a
  lint-rule follow-up at some point).

---

## Open questions before the v0.5.x sprint starts

These don't need answering today but should be answered before the
first A-tier ship:

1. **Default "Default" site name** — keep it as literally "Default"
   or seed it from the install hostname (e.g. "voipguru.org")? The
   migration backfill needs a name.
2. **Group/device binding granularity** — does an admin-of-group
   imply admin-of-its-devices, or are bindings purely additive?
   Recommend additive (no implication) for explicitness; matches
   how site→device works today.
3. **`audit_events_archive` rotation** — do we keep archive rows
   forever, or add a `archive_retention_days` setting (default ∞)
   for paranoid-compliance environments to bound it too?
4. **Shadow-mode log location** — write `would_have_denied` log
   entries to `audit_events` with a special action (e.g.
   `rbac.shadow_deny`), or a separate `rbac_shadow_log` table that
   gets dropped post-cut-over? Recommend the audit-events
   route — simpler ops, easier to query, the cut-over deletes the
   shadow rows by action filter.
5. **Cut-over rollback plan** — feature flag for one minor (v0.5.x
   → v0.5.<n+1>) that lets us revert if the enforce flip surfaces
   unexpected denials in production. Drop the flag in v0.6.0.

None of these block A1; happy to defer to a redline pass.
