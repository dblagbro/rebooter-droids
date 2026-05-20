# Consolidated Risk Register — Rebooter Platform

**Date:** 2026-05-20
**Author:** D. Blagbrough
**Scope:** Active risks across the hub and firmware, synthesized from the
2026-05-20 design passes (`2026-05-20-hub-tier2-design.md`,
`2026-05-20-organization-boundary-design.md`,
`rebooter-firmware/docs/2026-05-20-firmware-tier2-design.md`) and the
firmware `bug-log.md` / `NEXT_STEPS.md`.

Severity scale: **Critical** (data breach / fleet outage),
**High** (significant functional or stability failure),
**Medium** (degraded behavior, recoverable),
**Low** (minor / cosmetic / well-bounded).

---

## R-1 — ESP8266 low-heap budget

- **Risk:** The Sonoff S31's ESP8266 has roughly 80 KB usable heap, and
  real-world steady-state free heap on the fleet sits near the **20 KB**
  compact-mode trigger once Wi-Fi, TLS, the web server, ArduinoJson, and the
  event log are live. Each outbound HTTPS call allocates a multi-KB
  `BearSSL::WiFiClientSecure`. Any new standing allocation that materially
  shrinks the floor, or any second concurrent TLS session, risks pushing
  units into exception reboots. The combined Tier-2 standing cost is ~3–4 KB
  if mDNS is always on — significant against a ~20 KB floor.
- **Severity:** High.
- **Mitigation / status:** Every Tier-2 firmware feature was sized against
  the floor: multi-Wi-Fi ~700 B, hub URLs ~760 B, power aggregates fixed/
  no-growth, crash capture RTC-only (zero heap at capture). mDNS — the one
  ~1–2 KB standing cost — was made **opt-in / off-by-default** (decision
  T-003) with the near-free UDP burst as the always-on path. Power telemetry
  was moved off a second TLS session into the heartbeat (R-4). Residual:
  real free-heap re-measurement is still required after the config-audit
  pass (larger JSON = larger transient buffers) and with mDNS enabled on a
  real unit. **Open — ongoing measurement.**

## R-2 — Multi-hub sync-applier is an unscoped tenant-isolation path (org phase 3)

- **Risk:** Multi-hub sync crosses a trust boundary by design. The
  `sync_replicator` applier currently runs outside the new tenant scope; a
  buggy or hostile peer hub could inject rows into the wrong organization.
  The `do_orm_execute` filter does nothing when the org ContextVar is unset,
  and the applier is exactly such an unscoped path. This is the largest
  remaining hole in the org-boundary work — it is **not yet built**
  (org-boundary phase 3 has no commits).
- **Severity:** Critical (cross-tenant data injection = a data breach).
- **Mitigation / status:** The org design (§3.7) specifies the fix: extend
  `outbox_events.scope_claims` to carry `organization_id`; the applier must
  refuse any event whose org does not exist locally, stamp the org onto every
  applied row, and run the apply under the correct tenant scope. Dedicated
  cross-hub test coverage is required. Phase 2 already relabelled the
  phase-3 TODOs (`b7f21a7`). **Open — phase 3 is unbuilt and gates the
  enforce flip.**

## R-3 — Org-isolation shadow→enforce rollout

- **Risk:** The tenant filter ships in shadow (count-and-log) mode first;
  the enforce flip changes it from log-only to actually-filter. An enforce
  flip done before all `tenant.shadow_diff` audit rows are resolved would
  either leak cross-org data (if a query needed scoping and was missed) or
  break a legitimate cross-org system path (if a needed bypass was missed).
  A related hazard: running the org enforce flip and the RBAC enforce flip
  in the same window makes any incident un-debuggable.
- **Severity:** High.
- **Mitigation / status:** Mirror the proven RBAC shadow→enforce playbook —
  ≥7 days clean in shadow before the flip; the flip is a single
  runtime-setting toggle, no redeploy (decision T-013). The two enforce
  flips are explicitly sequenced weeks apart. A static `TenantScoped` test
  fails CI if a Tier-A model is added without the mixin; a cross-tenant
  integration test, driven off the URL map, asserts no foreign-org id ever
  appears in any list/detail route. Shadow toggle is merged (`6bd833f`);
  the cross-tenant test suite is merged (`104caf6`). **Owner-gated — the
  enforce flip is a deliberate owner action, pending ≥7 clean shadow days.**

## R-4 — The fleet exception-reboots (`central+power` instability)

- **Risk:** Bug-log #14 (the current primary firmware blocker before Tier-2)
  and #1 / #10 / #11: low-heap S31 units crash with `reset_reason="Exception"`
  and auto-enter recovery mode when `central.enabled=true` and
  `power.enabled=true` together. The completed overnight soak showed `.48`,
  `.67`, `.69`, and `.30` all ending in `recovery_mode`; `.225` is the
  worst offender, returning to recovery even with `power=false`. Root cause:
  heap churn / fragmentation in the central TLS path (BearSSL validator
  allocation, `LittleFS.open`, event-log JSON serialization) — not a single
  leak.
- **Severity:** High.
- **Mitigation / status:** A sequence of firmware mitigations has narrowed
  this — `0.1.27` fixed boot-state / planned-restart accounting, `0.1.29`
  deferred event-log persistence, removed power-upload event spam, and
  staggered the TLS-heavy central operations. The Tier-2 power-into-heartbeat
  move (T-002, `0c85a6e`) is the structural fix: it removes the standalone
  power-upload TLS session entirely for S31-class units, which is the
  documented worst case. The `0.1.40` baseline is stable with
  `central=true, power=false`. **Partly mitigated — a controlled soak with
  the heartbeat-piggyback power path on the worst units (`.225`) is still
  required to declare victory.** Wall devices are deliberately held on the
  safer `power=false` mode until that soak confirms the fix.

## R-5 — Raw `text()` SQL bypasses the ORM tenant filter

- **Risk:** The `do_orm_execute` global tenant filter only catches ORM
  `select()` statements. The codebase has raw `text()` SQL —
  `bootstrap.py` (`UPDATE devices …`), `role_bindings.py` (the v0.5.1
  dedupe) — that touches tenant tables and is invisible to the filter. Any
  raw SQL against a Tier-A table that is not legitimately system-scoped is a
  silent cross-tenant hole.
- **Severity:** High.
- **Mitigation / status:** Every `text(...)` and `conn.execute` call site
  must be audited for tenant-table access; most are bootstrap/migration code
  that is legitimately system-scoped, but each must be confirmed. This is a
  real residual gap that the application-level filter cannot close. Postgres
  Row-Level Security (RLS), specified as a phase-2 defense-in-depth layer,
  would close it completely — RLS filters at the database level regardless
  of how the query is issued. **Open — audit pending; RLS is phase-2
  hardening.**

## R-6 — Firmware Bug 7: OTA intermittent `recovery_mode`

- **Risk:** Bug-log #7: an OTA reboot once returned with `recovery_mode=true`
  / `last_known_good_restored=true` / `central_state="recovery_mode"` until a
  normal authenticated reboot cleared it. The cause was never positively
  identified — a stale recovery flag or an OTA/restart edge case. Related
  bug-log #10 showed long-uptime field devices auto-entering recovery on the
  first post-OTA boot. An intermittent post-OTA recovery state undermines
  confidence in unattended fleet rollouts.
- **Severity:** Medium.
- **Mitigation / status:** Not reproduced in a 3-cycle OTA stress on
  `0.1.21-dev-central-safe`; the related layered boot-state issues from
  bug-log #10 were fixed in `0.1.27` (boot-state firmware-version-aware,
  intentional restarts explicitly marked, `RuntimeStatus` reinitialized at
  boot). The Tier-2 on-flash crash capture (`10719da`) gives a real
  diagnostic surface — a future recurrence will leave a retrievable crash
  record instead of a guess. **Open — monitoring; significantly reduced but
  not closed.**

## R-7 — Provisional Sonoff S31 pin mapping

- **Risk:** `HARDWARE_NOTES.md` and `include/pins.h` both carry an explicit
  TODO: the S31 pin mapping (Relay GPIO12, LED GPIO13, Button GPIO0,
  CSE7766 RX GPIO3) is the "common Sonoff S31 starter-scaffold" mapping and
  has **not** been verified against the exact board revision in use. A wrong
  relay or button pin on a mains-powered device is a safety and
  functional-correctness risk.
- **Severity:** Medium.
- **Mitigation / status:** The mapping is flagged provisional in both the
  hardware notes and the source. The fleet runs on it in practice
  (real CSE7766 telemetry is live on `.48`, relay control works), which is
  strong empirical confirmation — but it is not a documented per-revision
  verification. **Owner-gated — requires a deliberate hardware-verification
  pass against the exact board revision before the mapping is treated as
  confirmed; never probe a board under mains.**

## R-8 — ConnectVar / connection-pool tenant leakage

- **Risk:** The tenant scope is held in a `ContextVar`. If
  `tenant_scope.reset()` is missed in a teardown, a pooled worker serves the
  next request with the previous request's org — a silent cross-tenant leak.
- **Severity:** High.
- **Mitigation / status:** The reset must live in a `teardown_request` /
  `teardown_appcontext` hook (which runs even on exceptions), not just
  `after_request`. The org design calls for a forced-exception-mid-request
  test to confirm. **Open — covered by design; verify in test.**

## R-9 — High-volume Tier-B table join cost

- **Risk:** Deriving `organization_id` for the write-hot tables
  (`device_heartbeats`, `device_power_samples`, `device_events`,
  `audit_events`) through a 2–3 hop join on every query could regress query
  latency once the org filter is live.
- **Severity:** Medium.
- **Mitigation / status:** Index the join path (`sites.organization_id`);
  measure query plans post-rollout; denormalize `organization_id` onto the
  hottest 1–2 tables only as a deliberate read-optimization if plans demand
  it. The design recommends starting without denormalization. **Open —
  measure after rollout.**

## R-10 — Outbound-webhook SSRF (hub-side)

- **Risk:** The Tier-2 hub-side outbound-webhook engine on a public SaaS is
  a classic SSRF footgun — an operator-supplied URL could target internal
  services, cloud metadata endpoints (`169.254.169.254`), or be redirected
  to an internal host via DNS rebinding.
- **Severity:** High.
- **Mitigation / status:** A mandatory shared `ssrf_guard.py` is the
  load-bearing control: resolve all A/AAAA records, reject any
  private/loopback/link-local/multicast/reserved/ULA/CGNAT IP, **pin the
  connection to the validated IP** to close the TOCTOU rebinding gap,
  disable redirects by default, hard timeout + size cap + rate limit. The
  guard must be written and tested before any sender code can call out.
  Device-side webhooks are explicitly exempt (decision T-009 — they may
  target the LAN). **Open — Feature 6 in progress; SSRF guard precedes the
  sender.**

## R-11 — Org backfill picks a wrong / missing owner

- **Risk:** The default-organization backfill assigns `owner_user_id` from
  the first `is_super_admin` user. If an install has no such user, the
  default org is ownerless until an admin claims it.
- **Severity:** Low (the FK is `SET NULL`; functionally tolerable).
- **Mitigation / status:** The backfill logs loudly on a missing owner; a
  platform-staff tool allows assigning ownership later. Backfill merged
  (`f51e29f`). **Acceptable / bounded.**

## R-12 — Self-host UX regression from the org boundary

- **Risk:** A single-tenant self-hosted install must never surface the word
  "organization." An upgrading self-hoster should notice nothing.
- **Severity:** Low.
- **Mitigation / status:** The bootstrap ensures one
  `Organization(is_self_hosted_default=True)`; the signup/org-creation UI is
  hidden in single-tenant mode. Needs explicit self-host QA on an upgrade
  path. **Open — covered by design; verify in QA.**

## R-13 — Firmware multi-Wi-Fi boot delay

- **Risk:** Walking many saved networks at a full per-attempt timeout
  (5 saved + 2 dev × 20 s = up to 140 s) badly delays initial connect.
- **Severity:** Low.
- **Mitigation / status:** Required mitigations are shipped — a one-time
  boot scan (`WiFi.scanNetworks()`) skips absent SSIDs, the default
  per-attempt timeout is 12–15 s, and slots with no configured SSID are
  skipped. **Mitigated.**

---

## Summary by severity

| Severity | Risks |
|---|---|
| Critical | R-2 (sync-applier unscoped path — phase 3 unbuilt) |
| High | R-1, R-3, R-4, R-5, R-8, R-10 |
| Medium | R-6, R-7, R-9 |
| Low | R-11, R-12, R-13 |

The two risks gating the org-boundary enforce flip are **R-2** (phase 3
must be built) and **R-5** (raw-SQL audit). The two risks gating wider
firmware fleet rollout are **R-1** (heap re-measurement) and **R-4**
(controlled soak of the heartbeat-piggyback power path).
