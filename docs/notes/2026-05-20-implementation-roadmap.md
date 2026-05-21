# Implementation Roadmap — Rebooter Platform

**Date:** 2026-05-20
**Author:** D. Blagbrough
**Scope:** The forward roadmap across the hub and firmware, in priority
tiers, synthesized from the 2026-05-20 design passes and the current git
state of both repositories.

This roadmap orders the remaining work into four priority tiers. Each item
notes its dependencies and flags whether it is **owner-gated** — meaning it
requires a deliberate human owner action (a deploy, an enforce-mode flip, or
hardware verification) and cannot be completed by build work alone.

Companion documents:
`2026-05-20-decision-log.md` (the decisions this roadmap executes) and
`2026-05-20-risk-register.md` (the risks each tier addresses).

---

## Current state (baseline for this roadmap)

**Hub** (`rebooter-droids`, ~v0.5.102):
- Sprint-1 takeover work shipped — power-upload Option A hub-side read path,
  `/api/v1/admin/network-truth`, `www` startup validator, the `.225`
  announce and `.69` duplicate-row fixes.
- Org boundary phase 1 (org models, `TenantScoped` mixin, Alembic baseline,
  default-org backfill) and phase 2 (tenant-scope read filter +
  write-stamping + shadow toggle, org-aware RBAC, cross-tenant isolation
  test suite) are **merged to main**.
- Tier-2 UI slice merged — friendly device-config form, mobile-first
  dashboard pass.
- Open feature branches: `feat/org-boundary-phase3`, `feat/setup-wizard`,
  `feat/backup-apitokens`, `feat/notifications-webhooks`.

**Firmware** (`rebooter-firmware`, 0.1.40 baseline):
- The `0.1.22`–`0.1.40` dev work is recovered into git (decision T-011).
- All six firmware Tier-2 features are **merged to `master`** — config-audit
  pass, hub-URL cap to 10, multi-Wi-Fi, on-flash crash capture,
  power-into-heartbeat, opt-in mDNS discovery beacon.
- The live fleet (`.67`, `.69`, `.30`, `.225`) runs `0.1.40` stable with
  `central=true, power=false`.

The remaining work is therefore: **finish and roll out the org boundary**,
**finish the hub Tier-2 feature set**, **deploy and stabilize the fleet**,
and **polish**.

---

## P1 — Organization-boundary completion and rollout

The hard multi-tenant `organization` boundary is the highest priority: it is
half-built, and the unbuilt half (phase 3) is the platform's largest open
security risk (risk R-2). Nothing in P2 that creates new tenant-scoped data
should land before P1 is structurally complete.

### P1.1 — Org boundary phase 3: scope the sync applier *(highest priority)*

- Extend `outbox_events.scope_claims` to carry `organization_id`.
- The `sync_replicator` applier must refuse any event whose `organization_id`
  does not exist locally, stamp the org onto every applied row, and run the
  apply under the correct tenant scope.
- Add dedicated cross-hub sync isolation tests — multi-hub sync is the one
  path where data crosses a trust boundary by design.
- **Dependency:** phase 2 (merged). **Addresses:** R-2.
- **Status:** branch `feat/org-boundary-phase3` exists but has no commits —
  this is unbuilt and is the single most important P1 item.

### P1.2 — Raw-SQL audit for tenant-table access

- Audit every `text(...)` / `conn.execute` call site for access to Tier-A
  (tenant-scoped) tables; the `do_orm_execute` filter does not catch raw SQL.
- Confirm each is legitimately system-scoped or route it correctly.
- **Dependency:** none (can run in parallel with P1.1). **Addresses:** R-5.

### P1.3 — Apply the `organization_id NOT NULL` constraints + per-org uniques

- Once the default-org backfill is confirmed complete on all production DBs,
  ship the Alembic revision that sets `organization_id NOT NULL`, adds the
  FKs, and swaps the global `unique` constraints on `sites.name`,
  `groups.name`, `scenes.name` to per-org `UNIQUE(organization_id, name)`.
- **Dependency:** the backfill (merged, `f51e29f`) confirmed on every DB.
- **Owner-gated:** the constraint migration must be deployed to each
  production DB; deploys are an owner action.

### P1.4 — Tenant filter: shadow soak

- Run the tenant-isolation filter in shadow (count-and-log) mode for ≥7 days.
- Resolve every `tenant.shadow_diff` audit row — each diff is either a query
  that needs scoping or a system path that needs an explicit bypass.
- **Dependency:** P1.1, P1.2 (a clean shadow soak is meaningless while a
  known unscoped path or unaudited raw SQL exists). **Addresses:** R-3.

### P1.5 — Org enforce flip *(owner-gated)*

- Flip `organization.enforce_mode` from shadow to enforce via the single
  runtime-setting toggle — no redeploy.
- Watch audit for `tenant.enforce_deny` rows.
- **Dependency:** P1.4 (≥7 clean shadow days).
- **Owner-gated:** the enforce flip is a deliberate owner decision. It must
  **not** be done in the same window as the RBAC enforce flip — sequence
  them weeks apart so any incident is attributable (decision T-013).

### P1.6 — Onboarding and org UI

- Org-switcher UI, public signup → org-creation flow, per-org settings
  (a separate `organization_settings` table for tenant-editable keys).
- Can land in parallel with P1.4–P1.5 since it only *uses* the now-correct
  org data.
- **Dependency:** phase 2 (merged).
- **Owner-gated input:** whether self-service signup launches *open* or
  *gated* is an owner decision (recommendation: gated at launch).

---

## P2 — Hub Tier-2 features

The six hub Tier-2 features. Per the org design's sequencing note, any
Tier-2 table that holds tenant data (`api_tokens`, `notification_channels`,
`notification_subscriptions`) must be born with `organization_id` — so these
should land after, or be built against, the P1 org boundary.

Suggested build order from the Tier-2 design: 2 → 1 → 5 → 6 → 4 → 3.

### P2.1 — Friendly device-config form *(Feature 2 — shipped)*

- Field-based form replacing the raw-JSON textarea on device-detail.
- **Status:** merged (`7cea750`). Listed for completeness.

### P2.2 — First-run setup wizard + 3-mode picker *(Feature 1)*

- `/app/setup` wizard and the reusable per-device 3-mode picker (Smart
  switch / Internet watchdog / Device watchdog), translating plain-language
  answers into `desired_config` + watchdog rules. Build the picker first —
  it is the reusable, higher-value half.
- **Dependency:** Feature 2's shared `_desired_config_form.html` partial
  (merged). Executes decision D-003.
- **Status:** branch `feat/setup-wizard` open.

### P2.3 — Mobile-first dashboard pass *(Feature 5 — shipped)*

- Priority-ordered single-column mobile layout + a "Needs attention" card.
- **Status:** merged (`0992c7b`). Listed for completeness.

### P2.4 — Notifications / outbound webhooks *(Feature 6)*

- Outbound-webhook engine + notification channels (generic webhook, Slack,
  Discord, Pushover), event subscriptions, an APScheduler-backed delivery
  queue with retry/backoff.
- **The SSRF guard (`ssrf_guard.py`) must be written and tested first** —
  before any sender code can call out. It must IP-pin the connection to
  close DNS-rebinding. Device-side webhooks are exempt (decision T-009).
- New tables (`notification_channels`, `notification_subscriptions`,
  `webhook_deliveries`) carry `organization_id` per the org design §7.
- **Dependency:** the org boundary (for the org-scoped tables) and Feature
  4b for its Settings UI. Executes decisions D-010 #2, T-004, T-009.
  **Addresses:** R-10.
- **Status:** branch `feat/notifications-webhooks` open.

### P2.5 — Stubbed Settings sub-pages: API tokens + Webhooks *(Feature 4)*

- First-class `api_tokens` (model + token-auth resolver + scope enforcement)
  and the Webhooks Settings UI (the management surface for Feature 6).
  Re-add Backup / API-tokens / Webhooks to the Settings tab strip; delete
  the dead `stub.html`.
- `api_tokens` is a Tier-A org-scoped table; the token-auth resolver must set
  the tenant ContextVar from the token's `organization_id`.
- **Dependency:** Feature 6 services (for the Webhooks page); the org
  boundary (for `api_tokens.organization_id`).
- **Status:** branch `feat/backup-apitokens` open.

### P2.6 — Backup / restore config UI *(Feature 3)*

- Export operator-managed config (runtime settings with secrets redacted,
  rules, schedules, scenes, sites/groups, per-device `desired_config` keyed
  by MAC) to a versioned JSON file; dry-run-first import. Lands last so it
  can already serialize the Feature-6 notification channels; becomes
  naturally per-org once everything it serializes is org-scoped.
- **Dependency:** Features 4 and 6; the org boundary.
- **Status:** branch `feat/backup-apitokens` open (shares the branch).

---

## P3 — Deploy and fleet stabilization

### P3.1 — Controlled soak of the heartbeat-piggyback power path *(owner-gated)*

- The firmware power-into-heartbeat change (T-002) is the structural fix for
  the `central+power` exception reboots (R-4). Run a controlled soak with
  `central=true` and the heartbeat power path enabled on the worst units
  (especially `.225`) before declaring the fleet stable.
- **Dependency:** firmware Tier-2 power feature (merged, `0c85a6e`) and the
  hub-side consumption of the folded power summary (`36d4386`).
- **Owner-gated:** requires deploying the new firmware to the live fleet and
  observing a multi-hour soak — a deploy + observation action.
- **Addresses:** R-4.

### P3.2 — Real free-heap re-measurement on a live S31 *(owner-gated)*

- After the config-audit pass (larger JSON = larger transient buffers) and
  with opt-in mDNS enabled, measure actual free heap on a real S31 with
  `central=true`. If the floor erodes below the ~20 KB compact-mode
  threshold, keep mDNS off by default (already the decision, T-003) and
  rely on the UDP burst.
- **Owner-gated:** requires hardware access to a live unit.
- **Addresses:** R-1.

### P3.3 — Per-device power-path migration

- Flip `power.heartbeat_path_enabled` per device as units are confirmed
  stable on the heartbeat power path. ESP32-class devices keep the dedicated
  `/api/v1/device/power-samples` endpoint.
- **Dependency:** P3.1. Executes decision D-011.
- **Owner-gated:** the per-device flip is a rollout action.

### P3.4 — Org-boundary constraint and enforce-flip deploys

- The deploy actions embedded in P1.3 and P1.5 — listed here too because
  they are fleet/production deploy events that an operator schedules.
- **Owner-gated.**

### P3.5 — Wall-device hands-off discipline

- The in-wall units (`.67`, `.30`, `.225`) are deliberately held on the
  safer `power=false` mode and should not be churned until the P3.1 soak
  confirms the new transport. Avoid further OTA churn on wall devices until
  then.
- **Status:** an operating constraint, not a build item.

---

## P4 — Polish

### P4.1 — Doc-truthfulness pass (continuing)

- Continue D-019 / D-016 — zero stale "do not implement" headers, zero
  contradiction between `ADMIN_GUIDE.md` / `API.md` / `DEVICE_INTEGRATION.md`;
  fix the enrollment-token revocation and `apply_config.power` drift.
- **Dependency:** none (doc-only).

### P4.2 — RFC-006 multimodal ingest schema sections

- Write the schema-shape sections (common envelope, modality-specific
  stores, mixed transport, independent adapters) per decision D-018; the
  cross-modal query-layer build stays deferred to v2.
- **Dependency:** none; should be written before any P2-class source that
  touches ingest.

### P4.3 — Integration v1 remainder

- MQTT publish stub, Home Assistant native integration manifest, Node-RED
  example flows — D-010 items 3–5, after the outbound-webhook engine (P2.4).
- **Dependency:** P2.4.

### P4.4 — Phase-2 org hardening: Postgres RLS

- Add Postgres Row-Level Security underneath the application-level tenant
  filter as defense-in-depth — RLS closes the raw-SQL gap (R-5) completely.
- Consider denormalizing `organization_id` onto the hottest Tier-B tables
  if post-rollout query plans demand it (R-9).
- **Dependency:** P1 complete and the enforce flip stable.

### P4.5 — Hardware-verification pass on the S31 pin mapping *(owner-gated)*

- Verify the provisional pin mapping (Relay GPIO12, LED GPIO13, Button
  GPIO0, CSE7766 RX GPIO3) against the exact board revision; update
  `HARDWARE_NOTES.md` and `pins.h` to drop the TODO.
- **Owner-gated:** requires bench hardware; never probe a board under mains.
- **Addresses:** R-7.

### P4.6 — Firmware follow-ups

- Bootstrap-loader optionally reading the saved Wi-Fi list from LittleFS
  (currently a deliberate follow-up, kept hardcoded as the disaster-recovery
  floor); preserving `wifi.savedNetworks` across auto-recovery rollback;
  the remaining items in the firmware `NEXT_STEPS.md` backlog.

---

## Dependency and owner-gating summary

| Tier | Build dependencies | Owner-gated items |
|---|---|---|
| P1 | P1.1+P1.2 → P1.4 → P1.5; P1.3 needs the backfill confirmed | P1.3 (constraint deploy), P1.5 (enforce flip), P1.6 (signup-openness decision) |
| P2 | org boundary precedes the org-scoped tables; SSRF guard precedes the webhook sender; 4 precedes 6's UI; 3 lands last | none (build-only) |
| P3 | P3.1 → P3.3; P3.1/P3.2 need the firmware merged | P3.1, P3.2, P3.3, P3.4 — all deploy / hardware actions |
| P4 | P4.3 needs P2.4; P4.4 needs P1 complete | P4.5 (hardware verification) |

**The critical path:** P1.1 (build the sync applier) → P1.2 (raw-SQL audit)
→ P1.4 (shadow soak) → P1.5 (owner-gated enforce flip). Everything in P2
that creates tenant-scoped data should be built against the org boundary;
fleet stabilization (P3) runs in parallel and is gated only on owner deploy
and hardware actions.
