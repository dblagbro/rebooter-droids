# Consolidated Decision Log — Rebooter-Droids

**Date:** 2026-05-20
**Author:** D. Blagbrough
**Scope:** All binding decisions for the Rebooter platform — the D-001..D-019
takeover-package decisions (2026-05-19) plus the Tier-2-era decisions that
followed from the hub and firmware design passes (2026-05-20) and the
intervening git history.

This log consolidates and supersedes scattered decision records. Each entry
states the decision, its rationale, and current status. Status values:
**Adopted** (decided and stable), **Shipped** (implemented and merged),
**In progress**, **Owner-gated** (decided but awaiting an owner action),
**Deferred**.

Sources: `2026-05-19-executive-takeover-package.md` §3,
`2026-05-20-hub-tier2-design.md`, `2026-05-20-organization-boundary-design.md`,
`rebooter-firmware/docs/2026-05-20-firmware-tier2-design.md`, and the git
history of both repositories.

---

## Part 1 — Takeover-package decisions (D-001..D-019, 2026-05-19)

### D-001 — Lift the HOLD on the low-heap power-upload analysis; ship Option A

- **Decision:** Adopt Option A — a compact power summary carried inside the
  device heartbeat — as the constrained-device power transport. Options B
  (lighter dedicated endpoint) and C (class-based router) remain on file as
  fallbacks only.
- **Rationale:** The firmware team's 2026-05-19 cold-start dossier was the
  follow-up memo the HOLD was waiting on. A second concurrent TLS session for
  a standalone power upload is the documented crash source on low-heap S31
  units; piggybacking on the existing heartbeat session avoids it.
- **Status:** Shipped. See also D-011 and the Tier-2 decision T-002.

### D-002 — Hub team takes over firmware ownership

- **Decision:** Forward firmware design, writing, and deployment is owned by
  the Rebooter-Droids hub team. The firmware team's role becomes consultative.
- **Rationale:** Ends the cross-team handoff latency; the firmware team's
  dossier explicitly requested cross-team design help.
- **Status:** Adopted. New firmware work proceeds under hub-team direction;
  the firmware repo remains `rebooter-firmware`.

### D-003 — v1 mode-picker UX (three modes)

- **Decision:** Build a first-run / settings-level mode picker with three
  modes — Mode 1 Smart switch, Mode 2 Internet/router auto-restarter, Mode 3
  Single-device restarter — backed by canned rule sets in the existing
  watchdog/rules engine. No new core engine.
- **Rationale:** The watchdog + rules engine already exists; the gap is a
  simple onboarding UX. A translator from plain-language answers to rules is
  cheap and avoids redesign.
- **Status:** In progress. Hub Tier-2 Feature 1 (`/app/setup` wizard +
  3-mode picker) builds this; setup-wizard branch open.

### D-004 — Default Mode 2 probe targets

- **Decision:** `1.1.1.1` (Cloudflare), `8.8.8.8` (Google), `9.9.9.9`
  (Quad9). Trigger only when **all three** fail for the configured window.
- **Rationale:** Three independent operators, ICMP-pingable, geographically
  distributed; ALL-failed avoids false positives from a single provider
  outage.
- **Status:** Adopted.

### D-005 — Default thresholds

- **Decision:** Mode 2 — 180 s fail window, 5 s power-off, 60 s post-power-on
  wait, 5 max cycles, 30 min cooldown. Mode 3 — 60 s / 5 s / 90 s / 3 cycles
  / 1 hr. Lockout after max cycles survives reboot and needs a manual clear.
- **Rationale:** Conservative defaults that recover real outages without
  fighting transient blips; persistent lockout prevents a stuck device from
  power-cycling indefinitely.
- **Status:** Adopted.

### D-006 — Multi-Wi-Fi fallback (firmware)

- **Decision:** Up to 5 saved networks, ordered fallback (last-known-good
  then priority slots 1→5), 30 s per attempt, full-pass failure → AP
  captive portal. No pre-seeded production Wi-Fi credentials; PSKs stored
  obfuscated in NVS, never returned in API responses, never logged.
- **Rationale:** Real deployments roam between networks; AP fallback keeps a
  stranded device recoverable.
- **Status:** Shipped (firmware). The firmware Tier-2 design refined the
  per-attempt timeout to 12–15 s with scan-gating to bound boot delay; see
  T-007.

### D-007 — AI / chat path: architecture-first, no LLM in v1

- **Decision:** Build a stable intent vocabulary (`get-status`, `set-mode`,
  `set-config`, `list-events`, `test-action`, `explain-event`) on the REST
  API. No chat UI in v1. A future natural-language layer sits on top without
  product redesign.
- **Rationale:** Locking the intent shape now is cheap; a chat UI is
  expensive and not needed for v1.
- **Status:** Adopted (architecture only).

### D-008 — Cloud relay deferred to v2

- **Decision:** No cloud relay in v1. Local-first remains the strategic moat;
  the active-active multi-hub design covers remote access for self-hosters.
- **Status:** Deferred to v2.

### D-009 — Voice control via Home Assistant Assist or Node-RED

- **Decision:** No native voice layer in v1. HA Assist already bridges
  Alexa/Google Assistant for users who have them.
- **Status:** Adopted (no v1 build).

### D-010 — Integration v1 priorities (ordered)

- **Decision:** (1) Harden + version the REST API (`/api/v1/`); (2) outbound
  webhook templating engine unifying Discord/Slack/Pushover; (3) MQTT
  publish; (4) Home Assistant native integration manifest; (5) Node-RED
  example flows.
- **Status:** In progress. Item 2 is hub Tier-2 Feature 6 (notifications /
  outbound webhooks).

### D-011 — Firmware power-upload contract

- **Decision:** The heartbeat gains an optional `power_compact` object. The
  hub-side read path ships first (additive, harmless on roomy devices), then
  the firmware change. A per-device runtime gate `power.heartbeat_path_enabled`
  (default OFF) flips per device during rollout. ESP8266-class devices
  migrate to the heartbeat path; ESP32-class keep the dedicated
  `/api/v1/device/power-samples` endpoint. `power_compact` minimum is
  `{"p_w": <float>}` plus optional voltage/current/flag fields and
  `valid_frame_count` / `invalid_frame_count`.
- **Rationale:** Additive-first means zero risk to existing devices; the
  per-device gate allows a controlled migration.
- **Status:** Shipped. Hub-side ingest landed Sprint 1 (S1-2/S1-3); firmware
  side landed in `0c85a6e`.

### D-012 — Same-LAN routing

- **Decision:** On adoption/announce the hub emits an ordered list of base
  URLs — internal/private hub URL (only if same /24 as the device), then
  `https://www.voipguru.org/rebooter`, then `https://www2.voipguru.org/rebooter`.
  Firmware tries each in order with bounded retries. The hub adds
  `/api/v1/admin/network-truth`.
- **Rationale:** Same-LAN devices should not route through the public
  internet; auto-detection avoids manual per-site config.
- **Status:** Shipped (hub side, S1-4). The firmware hub-URL cap was later
  raised to 10 — see T-008.

### D-013 — `www.voipguru.org` is a required hostname

- **Decision:** The hub validates at startup that `public_base_url` carries
  the `www.` prefix for the voipguru.org host; if not, emit a hub-status
  warning and an admin-page alert. Do not auto-rewrite.
- **Rationale:** A misconfigured override silently breaks adopted devices;
  surface it rather than mask it.
- **Status:** Shipped (warn-only; S1-5).

### D-014 — Fix the `.225` announce_pending hub bug

- **Decision:** Add a hub-side one-click "force adopt" action to clear the
  stuck announce_pending state. The fault is hub-side, not a device fault.
- **Status:** Shipped (S1-6: MAC normalization for announce/register
  cross-link).

### D-015 — Stale `.69` hub row cleanup

- **Decision:** Add an admin action to merge/retire duplicate device rows;
  retire the older `.69` row, keep the healthy newer one.
- **Status:** Shipped (S1-7: merge/retire duplicate devices + refuse
  duplicate fresh-adopt).

### D-016 — Doc-drift fixes

- **Decision:** Reconcile the enrollment-token revocation contract
  (`ADMIN_GUIDE.md` vs `API.md` — the `DELETE` endpoint exists, fix the
  guide) and the `apply_config.power` key (`API.md` vs
  `DEVICE_INTEGRATION.md` — the hub accepts it, fix `DEVICE_INTEGRATION.md`).
- **Status:** Adopted (doc-only; Sprint 1 item S1-8).

### D-017 — Firmware-team open asks: disposition

- **Decision:** `source_flags` bit dictionary owned by the hub (P1.2 work);
  frame counts folded into D-011; G2 cross-device time-sync deferred to v2;
  the stale `.69` pre-0.1.19 backlog claim dismissed.
- **Status:** Adopted.

### D-018 — RFC-006 (multimodal ingest): write the schema, defer the build

- **Decision:** Lock the schema-shape decisions now (common envelope,
  modality-specific stores, mixed transport, independent adapters); defer the
  cross-modal query layer build to v2.
- **Rationale:** Cheap to decide, expensive to retrofit.
- **Status:** Deferred (schema sections to be written; build is v2).

### D-019 — Doc-truthfulness pass (continuing)

- **Decision:** Continue the doc reconciliation pass — zero stale "do not
  implement" headers, zero contradiction between ADMIN_GUIDE / API.md /
  DEVICE_INTEGRATION.md.
- **Status:** In progress. The stale README multi-hub-sync claim and the
  `sync.enabled is off` framing were already retired in git history.

---

## Part 2 — Tier-2-era decisions (2026-05-20)

These decisions arise from the hub Tier-2 design pass, the hard-organization
boundary design pass, and the firmware Tier-2 design pass — all dated
2026-05-20 — plus the owner answers folded into those documents.

### T-001 — Hard `organization` multi-tenant boundary (replaces `site_id`-only isolation)

- **Decision:** Introduce a first-class top-level `organization` (tenant)
  entity. `organization` owns `site`; `site` owns `device`. A device's org is
  *derived* through its site, never stored on the device row. Tier-A tables
  (sites, groups, watchdog_rules, schedules, scenes, enrollment_tokens,
  external_sensor_sources, api_tokens, notification channels, etc.) get a
  `NOT NULL organization_id`; Tier-B tables derive org via a parent. Isolation
  is enforced structurally via a SQLAlchemy `do_orm_execute` global filter
  keyed on a tenant ContextVar — not "remember to filter every query."
- **Rationale:** The hub is a public multi-tenant SaaS with no tenant
  boundary today; `site_id` + RBAC is opt-in per call site and leaks on a
  forgotten filter. This answers the hub Tier-2 design's open question Q1
  (tenant boundary imminent?) with YES.
- **Status:** In progress. Phase 1 (org models, `TenantScoped` mixin,
  Alembic baseline, default-org backfill) and Phase 2 (tenant-scope read
  filter + write-stamping + shadow toggle, org-aware RBAC, cross-tenant
  isolation test suite) are merged to main. Phase 3 (the unscoped sync-applier
  path) is not yet built.

### T-002 — Power telemetry moved into the heartbeat envelope

- **Decision:** Retire the standalone HTTPS `/device/power-samples` upload for
  S31-class (ESP8266) units; carry a compact, bounded power aggregate
  (min/avg/max W, latest V/A/PF, energy Wh, sample counts) inside the
  heartbeat. Raw 1 Hz samples stay available locally via the device API but
  are not pushed off-device over a second TLS session.
- **Rationale:** This is the resolution of D-001/D-011, confirmed by the
  firmware Tier-2 design pass: the second concurrent TLS session is the
  documented low-heap crash source. Aggregates are fixed-size — no heap
  growth.
- **Status:** Shipped. Firmware side `0c85a6e`; hub side consumes the folded
  summary (`36d4386`). The `power_upload_mode` capability flag is exposed so
  the constraint is visible.

### T-003 — mDNS discovery is opt-in / off-by-default

- **Decision:** The firmware LAN discovery beacon ships mDNS as **opt-in**
  via a config flag, not default-on. The near-free UDP post-setup announce
  burst is the always-on discovery mechanism.
- **Rationale:** mDNS is the only Tier-2 firmware feature with a non-trivial
  standing heap cost (~1–2 KB against a ~20 KB free-heap floor on the worst
  units). Default-off protects the heap budget; the UDP burst covers the
  AP-setup handoff window where the app actually needs discovery.
- **Status:** Shipped (`63d5e23` — "opt-in mDNS plus UDP announce burst").

### T-004 — Pushover is in scope

- **Decision:** Pushover is an in-scope notification transport for the Tier-2
  work — both as a hub-side outbound notification channel (alongside
  generic webhook / Slack / Discord) and as a real firmware notification
  transport. The prior firmware state where `notifications.type` accepted
  `"pushover"` with no transport behind it is resolved by implementing it.
- **Rationale:** Pushover was already advertised in the firmware schema;
  D-010 item 2 names it explicitly. The expose-real-capabilities principle
  (T-006) requires either implementing or removing it — the decision is to
  implement.
- **Status:** Adopted. Hub-side: part of Feature 6 notification channels.
  Firmware-side: scoped under the Feature 6 expose-capabilities audit.

### T-005 — Firmware ship orders (Tier-2 rollout sequence)

- **Decision:** Ship the firmware Tier-2 features lowest-risk-first:
  (1) config-audit pass — expose unwired fields, remove fake capabilities;
  (2) hub-URL cap raise to 10; (3) multi-Wi-Fi; (4) on-flash crash capture;
  (5) power-metering completion (heartbeat piggyback); (6) LAN discovery
  beacon last, because mDNS is the one standing-heap risk.
- **Rationale:** Each feature was sized against the ~20 KB free-heap floor;
  the heaviest/riskiest feature ships last after real free-heap measurement.
- **Status:** Shipped. All six firmware Tier-2 features are merged to
  `master` in exactly this order (`8255766`, `efd3d20`, `f42e6c3`,
  `10719da`, `0c85a6e`, `63d5e23`).

### T-006 — Expose-all-device-features principle

- **Decision:** Every real hardware/firmware capability must be exposed in
  both the API and the WebUI. A memory-constrained capability is exposed
  *with the constraint visible* (a mode flag), never silently. A
  not-implemented capability is removed from the schema/UI rather than
  advertised as a fake. The recurring anti-pattern — struct/schema fields not
  wired through `sendConfigJson` + `config/save` — is closed by a single
  audit pass.
- **Rationale:** Advertised-but-fake fields (e.g. mains frequency hardwired
  invalid) and config-fields-with-no-surface mislead operators.
- **Status:** Shipped (`8255766` — "Expose unwired config fields and remove
  fake capabilities"). Mains frequency was dropped from schema/UI rather than
  faked.

### T-007 — Firmware owns the Wi-Fi credential list (drop tzapu as the store)

- **Decision:** The firmware owns the prioritized saved-network list in
  `AppConfig`; tzapu WiFiManager is kept only as the AP/captive-portal HTTP
  server. The two built-in `dev_wifi_config.h` networks remain a compile-time
  fallback tier and are not copied into the saved list.
- **Rationale:** tzapu can store only one SSID and its stored credential can
  disagree with the firmware config; owning the list makes the firmware
  config the single source of truth.
- **Status:** Shipped (`f42e6c3`).

### T-008 — Hub URL cap raised to 10 with last-good failover

- **Decision:** Raise the firmware `central.baseUrls` cap from 4 to 10,
  fully user-editable (self-hosters need it). Iterate failover starting from
  the last-known-good index, and cap attempted URLs per cycle at 3 to bound
  TLS-handshake cost.
- **Rationale:** D-012's ordered routing list, generalized; the per-cycle cap
  prevents a fully-down 10-entry list from doing 10 back-to-back TLS
  handshakes (the most expensive heap event).
- **Status:** Shipped (`efd3d20`).

### T-009 — Device-side vs hub-side webhook RFC1918 split

- **Decision:** Device-side webhooks (firmware `notifications.webhook_url`)
  **may** legitimately target private/LAN RFC1918 addresses — local Home
  Assistant, local Node-RED. Hub-side outbound webhooks (the Tier-2
  notification engine, a public SaaS) **must not** — a mandatory SSRF guard
  (`ssrf_guard.py`) resolves the target, rejects any IP in
  private/loopback/link-local/multicast/reserved/ULA/CGNAT ranges, and pins
  the connection to the validated IP to close DNS-rebinding.
- **Rationale:** The two webhook surfaces have opposite trust models. The
  firmware runs on the user's LAN and a local target is the normal case; the
  hub is internet-facing and a private-IP target is an SSRF attack. The
  takeover package's open question 3 (RFC1918 allowed?) is answered by this
  split — yes for device-side, no for hub-side.
- **Status:** Adopted. Hub-side SSRF guard is the load-bearing part of
  Tier-2 Feature 6; notifications-webhooks branch open.

### T-010 — Credential-purge dropped: guest-Wi-Fi passwords are non-secret

- **Decision:** The repo-cleanup secret scan does **not** blanket-purge
  every credential-shaped string from git history. A genuinely public value
  — a guest-Wi-Fi password already posted on a wall, a public API base URL,
  a sample/placeholder token — is not a security concern and does not warrant
  a destructive history rewrite. Only real secrets (production credentials,
  signing keys, private certs) are rotated then removed.
- **Rationale:** History rewrites are destructive and disruptive; applying
  them to non-secret values is unjustified churn. Judge sensitivity, do not
  blanket-purge.
- **Status:** Adopted (`2026-05-20-generic-repo-cleanup-checklist.md` §1).

### T-011 — Firmware 0.1.40 source recovered to git

- **Decision:** The previously-uncommitted firmware development work for
  versions 0.1.22 through 0.1.40 has been captured into git. The
  `0.1.40-dev-central-safe` source — the baseline deployed to the live fleet
  — is now version-controlled rather than living only as uncommitted working
  files.
- **Rationale:** The fleet baseline existed only as an uncommitted working
  tree; that is an unacceptable single point of failure. Committing it makes
  the deployed firmware reproducible and is the precondition for all Tier-2
  firmware branches.
- **Status:** Shipped (`9797704` — "Capture firmware 0.1.22-0.1.40 dev work
  (was uncommitted)"). All firmware Tier-2 work branches from this commit.

### T-012 — Adopt Alembic for schema migrations

- **Decision:** Adopt Alembic properly for the org-boundary change and
  forward. Generate a baseline revision of the current schema, `alembic
  stamp` every production DB, and ship the org change as ordered Alembic
  revisions. Freeze the hand-maintained `_PENDING_COLUMNS` /
  `_PENDING_CONSTRAINTS` lists for already-shipped columns; no new entries
  after the org release.
- **Rationale:** `_PENDING_COLUMNS` (60+ entries) cannot express NOT NULL FK
  columns on populated tables, column renames, or transactional data
  migrations — exactly what the org boundary needs. Alembic is already
  configured; only the discipline is new.
- **Status:** Shipped (`cb82d1f` — "Add Alembic baseline and org-boundary
  phase 1 migrations").

### T-013 — Org / RBAC enforce flips are owner-gated and sequenced apart

- **Decision:** Both the tenant-isolation filter and RBAC enforcement run in
  **shadow** mode first (count-and-log), require ≥7 days clean before the
  enforce flip, and the flip is a single runtime-setting toggle — no
  redeploy. The org enforce flip and the RBAC enforce flip must **not** run
  in the same window; sequence them weeks apart so any incident is
  attributable. Each enforce flip is an owner-gated action.
- **Rationale:** Mirrors the team's proven RBAC shadow→enforce playbook. Two
  simultaneous shadow→enforce cutovers make an incident un-debuggable.
- **Status:** Adopted. Shadow toggle merged (`6bd833f`); the enforce flips
  themselves are owner-gated and pending.
