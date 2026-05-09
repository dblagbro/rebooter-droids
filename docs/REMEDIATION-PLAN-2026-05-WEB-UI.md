# Remediation Plan — Web UI / Device Surface

| Field | Value |
|---|---|
| Status | **Draft for product approval** (seeded 2026-05-09 from product/firmware/PM acceptance of the remediation letter) |
| Authors | rebooter-droids backend/web team |
| Returns through | product/firmware/PM (Codex) |
| Related | RFC-001-presence (parallel track), RFC-002-firmware-mirrors (parallel track), RFC-003-web-ui-redesign (master design this plan executes against), v0.2.7 (in-flight, ship-pipeline blocked) |

> **About this document:** this is the *sequenced execution plan* for
> the five accepted remediation themes. RFC-003 is the master design;
> this document chooses the order, the gates, the rollout shape, and
> the ship-early-vs-wait-for-redesign call for each piece. It is
> approval-shaped, not code-shaped — there is no diff in this file.

---

## 1. Executive summary

The five accepted themes — (1) QA/test-fixture cleanup or isolation,
(2) corrected device-state semantics in the UI, (3) fleet/device-page
redesign, (4) broader IA improvements, (5) deeper RBAC/mobile
alignment — span work that is *already on disk and ready to ship*
through work that requires a one-way schema migration with a
multi-week rollout. We propose a **9-phase sequenced plan (R1–R9)**
that lands the safe additive pieces first, gates the heavy schema
migration behind a redline pass, and runs auth + mobile work in
parallel where blast radii allow.

The first three phases (R1, R2, R3) are *safe to ship in the current
release line*. Phases R4 and R5 are the IA restructure on top of the
existing stack and are reversible behind feature flags. Phases R6
through R9 are the broader redesign and are gated on product redlines
already enumerated in RFC-003 §13 and §15.

## 2. Scope as accepted

| # | Theme | Maps to |
|---|---|---|
| 1 | QA / test-fixture cleanup or isolation | RFC-003 F9 (no first-class test-data concept). v0.2.7 already purged the live data; isolation is the design follow-up. |
| 2 | Corrected device-state semantics in the UI | v0.2.7 already shipped on disk (three-state heartbeat: online / offline / never). Pipeline is paused awaiting commit greenlight. |
| 3 | Fleet / device-page redesign | RFC-003 §7 (Fleet tab in the new IA), §8 (workflows), §9.1 (per-record audit on device detail). |
| 4 | Broader IA improvements | RFC-003 §7 (5-item top nav). |
| 5 | Deeper RBAC / mobile alignment | RFC-003 §9 (site-as-scope), §10 (auth + OIDC), §11 (mobile API + CORS + push), §11.3 (mobile-first responsive). |

## 3. Ship-early vs wait-for-redesign matrix

This is the headline call for the PM. Each phase is classified
**SHIP EARLY** (low blast radius, additive, reversible) or **WAIT**
(needs a redline pass or carries a one-way schema change).

| Phase | Theme | Classification | Why |
|---|---|---|---|
| **R1** | Ship v0.2.7 (device-state semantics) | **SHIP EARLY — IMMEDIATELY** | Code is on disk, deployed, and tested green against live. Only the commit/tag/push/Docker steps remain. Out of compliance with `feedback_release_every_version.md` until shipped. |
| **R2** | Test-fixture isolation | **SHIP EARLY** | Additive schema (one boolean column). Reversible. Closes the live-data-pollution failure mode immediately. |
| **R3** | Per-record audit slice | **SHIP EARLY** | Additive query + UI tab. No schema change. High operator signal. |
| **R4** | 5-item nav (Inbox / Fleet / Releases / Site / Settings) | **SHIP EARLY (gated)** | Reversible behind a feature flag. Old URLs keep working. Inbox attention-feed *scoring* waits for R5 redline; the *scaffold* ships in R4. |
| **R5** | Fleet page redesign + saved filters + diagnostic tabs | **WAIT** | Sits on top of R4. Layout regression risk on mobile. Wait one minor version after R4 to bed in. |
| **R6** | Site-as-scope RBAC migration | **WAIT — needs redline** | One-way schema migration. Open redline #1 in RFC-003 (split-site tool day-one vs post-migration manual) must be answered first. |
| **R7** | Auth foundation (server-side sessions, password reset, TOTP, OIDC) | **PARTIAL — server-side session table SHIP EARLY** | Server-side `sessions` table closes BUG-005 (cookie revocation gap) and is independent of site scoping; can ship in the R3 timeframe. TOTP/OIDC/password-reset wait for R6. |
| **R8** | Mobile API + CORS + push | **PARTIAL — CORS policy SHIP EARLY** | CORS allowlist is a one-line policy change that unblocks staging mobile dev work. Push fan-out + mobile JWT scope wait for R7. |
| **R9** | Mobile-first responsive + Passkeys | **WAIT** | Final polish; depends on R4 (nav structure) and R7 (auth foundation) both being shipped. |

## 4. Phased work plan

For each phase: **scope boundary**, **sequence**, **dependencies**,
**risks**, **testing/QA expectations**, **rollout strategy**, and the
**ship-early-vs-wait** call from §3 expanded.

---

### R1 — Ship v0.2.7 (device-state semantics)

- **Scope boundary.** Commit the staged changes on
  `fix/devices-never-heartbeated`, tag `v0.2.7`, push branch + tag,
  cut a GitHub release, push `dblagbro/rebooter-droids:0.2.7` and
  `:latest`. *No new code in this phase.*
- **Sequence.** First. Unblocks every later phase by clearing the
  branch and getting the live UI fix into the changelog.
- **Dependencies.** Commit greenlight from product/PM. Already
  blocked here.
- **Risks.** None new. Code is already deployed and verified — both
  URLs return `0.2.7`, all 7 v027 QA tests pass against live.
- **Testing / QA.** Re-run the v027 test bucket post-tag against
  both `www` and `www2` to confirm nothing drifted in the gap. Curl
  `/api/v1/version` on both hosts for the public marker.
- **Rollout strategy.** Single tag, single GitHub release, two image
  pushes. No staged rollout needed — code is already live. The git
  tag is purely a record-of-truth snapshot.
- **Ship-early/wait.** **SHIP EARLY — IMMEDIATELY.** The only
  blocker is the commit greenlight per
  `feedback_release_every_version.md`.

### R2 — Test-fixture isolation

- **Scope boundary.** Add `is_qa_fixture: bool` to the `devices`
  table (default `false`). Update the QA fixture helpers in
  `tests/qa/conftest.py` and the device-register payload to mark
  test-suite-created devices via either an explicit
  `qa_fixture: true` register-time flag or a `display_name`-prefix
  auto-detect (`QA `, `qa-`). Default the admin devices view to
  hide rows where `is_qa_fixture = true`; expose a "Show QA
  fixtures" toggle. Audit-log every fixture-created device with the
  fixture flag set.
- **Sequence.** Second, immediately after R1. Independent of R3 —
  the two can run concurrently.
- **Dependencies.** R1 shipped (so the schema baseline is known).
- **Risks.** Schema migration is forward-only. Default-hide could
  surprise an operator who expected to see fixtures. Risk is
  bounded — the data is intentionally obscured, not deleted, and a
  toggle reveals it. Auto-detect by display-name prefix is heuristic;
  the explicit flag at register time is the trust-anchor and the
  prefix is a fallback.
- **Testing / QA.** New `tests/qa/test_v028_fixture_isolation.py`
  asserting (a) every device created by the QA suite carries
  `is_qa_fixture = true`, (b) the default `/app/devices` page does
  not render fixture rows, (c) the toggle reveals them, (d) the
  dashboard `devices_total` counter optionally honours the
  filter (open redline below). Pre-existing v027 suite remains green.
- **Rollout strategy.** One minor (v0.2.8) ships the column +
  fixture flag write-path + UI toggle, defaulting **OFF** (still
  visible) for one release window so operators see the new toggle
  without the data disappearing. Next minor (v0.2.9) flips the
  default **ON** (hidden) with a one-time info banner.
- **Open redline (small).** Should the dashboard `devices_total`
  honour the QA-fixture filter, or always include them? Default
  proposal: honour the filter on the dashboard but expose the raw
  total in a tooltip.
- **Ship-early/wait.** **SHIP EARLY.**

### R3 — Per-record audit slice

- **Scope boundary.** Add `GET /api/v1/admin/audit?target_type=&target_id=`
  query support. Embed an "Audit" tab on the device-detail page (and
  later on group-detail and deployment-detail pages, but device
  first). No schema change — the existing `audit_log` table already
  carries `target_type` and `target_id`.
- **Sequence.** After R1; can run in parallel with R2.
- **Dependencies.** Existing audit_log indexes — confirm
  `(target_type, target_id, created_at desc)` index exists; add if
  missing.
- **Risks.** Query performance regression if the index is missing
  on a fleet with > 1M audit rows. Bounded by the index check above.
- **Testing / QA.** Integration tests for the new query slice;
  Playwright test for the device-detail audit tab; load test against
  a synthetic 1M-row audit_log to confirm p95 < 200 ms.
- **Rollout strategy.** Ships on by default. No feature flag needed
  — purely additive read path.
- **Ship-early/wait.** **SHIP EARLY.**

### R4 — Information architecture: 5-item top nav

- **Scope boundary.** Implement the new top nav: Inbox / Fleet /
  Releases / Site / Settings. *Inbox v1 is a hand-tuned attention
  feed* (rules: never-heartbeated > 30 min, offline > 1 h, offline >
  24 h, deployment stuck > 30 min, recent enrollment without first
  heartbeat, release just shipped to fleet). Fleet tab presents the
  existing devices table with sub-tabs for Groups, Sites, Events,
  Unregistered. Releases is a rename + light reorganisation of
  today's `/app/firmware`. Site is a placeholder picker that always
  shows "Default" (no real scoping yet). Settings consolidates
  `/app/me`, `/app/users`, `/app/invitations`, and `/app/audit`. Old
  URLs continue to resolve.
- **Sequence.** After R3 has shipped, so the Fleet sub-tabs and the
  per-record audit feature are present in the new IA from day one.
- **Dependencies.** R3 shipped. Open redline #5 in RFC-003 (Inbox
  ranking configurability) — for v1 we lock the rules; the open
  redline becomes a v2 question.
- **Risks.** Layout regression on mobile and tablet. Operator
  confusion at first login. Discovery cost for power users who
  bookmarked old URLs.
- **Testing / QA.** Full responsive QA pass at 375 / 768 / 1024 /
  1440 widths. Smoke test asserting every old `/app/*` URL
  continues to resolve to the same data (even if rendered inside
  the new shell). New Playwright tests for each of the five top-nav
  destinations.
- **Rollout strategy.** Feature flag `NEW_NAV` per user. Three-stage
  rollout: (a) opt-in for operators who toggle it on (one minor); (b)
  opt-out — defaults on, link to revert (next minor); (c) default-on
  for everyone, flag removed (third minor). One-time tour banner
  appears the first time an operator sees the new nav.
- **Ship-early/wait.** **SHIP EARLY (gated by feature flag).**

### R5 — Fleet page redesign + saved filters + diagnostic tabs

- **Scope boundary.** Card-layout devices on mobile (matches
  RFC-003 §11.3); saved-filter chips above the devices table
  ("Offline > 24 h", "Never heartbeated", "On firmware < latest
  stable", "Has pending commands", "QA fixtures only"); collapse the
  Events and Unregistered pages into Fleet sub-tabs. Filter state
  becomes URL-shareable.
- **Sequence.** After R4 has bedded in for at least one minor
  version. Operators must be in the new IA before we restructure
  the highest-traffic page within it.
- **Dependencies.** R2 shipped (so the `is_qa_fixture` filter chip
  has a column to read), R4 shipped (so the Fleet tab exists).
- **Risks.** Filter URL stability — once saved-filter URLs go out,
  changing their query-string shape breaks bookmarks. Mitigation:
  version the query-string parser (`?v=1&...`).
- **Testing / QA.** Filter URL round-trip tests; mobile-card layout
  Playwright tests at 375 px; visual regression against pre-R5 layout
  using the existing tablet/mobile fixtures.
- **Rollout strategy.** Feature flag *within* the R4 cohort.
  Operators on `NEW_NAV` get card-layout-on-mobile and
  saved-filter chips by default. Desktop layout stays table-based.
- **Ship-early/wait.** **WAIT** until R4 has shipped one minor.

### R6 — Site-as-scope RBAC migration

- **Scope boundary.** Per RFC-003 §9: `site_memberships` table; one
  Default site auto-created at migration; every existing
  device/group assigned to Default; every existing user gets `admin`
  on Default; site-scoping middleware that filters every admin-API
  list query by `site_id IN (memberships of current_user)` unless
  the user is a platform super-admin. Decorators rewritten as
  `is_admin_of(site_id)`. Site-scoped invite (RFC-003 §9.3).
- **Sequence.** After R5. The Site picker in the top nav becomes a
  real picker in this phase.
- **Dependencies. PRODUCT INPUT REQUIRED.** Open redline #1 in
  RFC-003: split-site tool from day one (lets a fleet break
  Default into N sites at any time) vs post-migration manual
  reassignment (lower-cost, less flexible). The answer changes the
  shape of this phase. **R6 cannot start until this is answered.**
- **Risks. HIGHEST RISK PHASE.** One-way schema change. Migration
  window where both the old global-permission code and the new
  site-scoped code must agree on every query result. Wrong
  Default-site boundary forces awkward post-migration cleanup.
  Super-admin bypass code path must be exercised by every test
  in the suite.
- **Testing / QA.** Full migration rehearsal against a snapshot of
  production data; **data-parity assertion** — every device, group,
  audit row, deployment, command reachable to a super-admin
  pre-migration is reachable to that same super-admin post-migration;
  feature flag `SITE_SCOPING_ENFORCE`; **dual-read for ≥ 7 days**
  (server logs every query result twice, once with old code path
  and once with the new scoping middleware, comparing the result
  sets) before flipping enforcement on; explicit role-scope test
  matrix per fixture user (Alice = admin Default, Bob = viewer
  Default, Charlie = admin Default + admin TestSite, etc).
- **Rollout strategy.** Three-step rollout, separated by ≥ 7 days:
  (a) ship the schema + middleware in shadow mode (writes new
  table, runs both code paths, logs divergences); (b) flip
  `SITE_SCOPING_ENFORCE = true` for super-admins only; (c) flip
  globally. Per-step abort criteria: > 0 divergences in (a) blocks
  (b); any super-admin reports a parity failure in (b) blocks (c).
- **Ship-early/wait.** **WAIT — depends on product redline.**

### R7 — Auth foundation

- **Scope boundary.** Per RFC-003 §10: server-side `sessions` table
  + revoke-all writes `revoked_at`; magic-link primitive (used for
  both password-reset and email-only sign-in); TOTP enrollment;
  Google + GitHub OIDC sign-in. Email + password remains the
  always-available fallback.
- **Sequence.** Server-side `sessions` table can ship **before R6**
  as a security fix (closes BUG-005, independent of site scoping).
  Password-reset, TOTP, and OIDC sign-in wait for R6 because their
  rollout flips need site-scoped permission to make sense (e.g.,
  "MFA required for admins of site X").
- **Dependencies. PRODUCT INPUT REQUIRED.** Open redline #2 in
  RFC-003 (Microsoft as a third OIDC provider in v1, or
  Google+GitHub only). Open redline #3 (MFA mandatory-flip
  timing — which version makes super-admin TOTP required, then
  admin, then operator).
- **Risks.** OAuth callback misconfiguration; cookie/session
  double-source-of-truth during the cutover; mandatory-MFA flips
  locking operators out mid-flight if the rollout window is too
  short. Mitigations: shadow-mode for the new session table;
  warn-and-don't-enforce window for MFA on each tier.
- **Testing / QA.** Dedicated auth test bucket. Explicit
  "what's-still-valid-after-revoke-all" matrix per scope (cookie,
  access JWT, refresh JWT, OIDC session, magic-link unredeemed,
  magic-link redeemed). OIDC happy-path + edge cases (provider down,
  account-not-yet-linked, account-linked-to-different-email).
- **Rollout strategy.** (a) Server-side `sessions` ships
  shadow-mode in the R3-R5 window (writes the table, doesn't
  enforce); (b) enforces in next minor — closes BUG-005; (c)
  password-reset magic-link in R6 cycle; (d) TOTP opt-in next
  minor; (e) OIDC opt-in; (f) MFA mandatory per tier on the
  schedule the redline locks.
- **Ship-early/wait.** **PARTIAL — server-side sessions SHIP EARLY,
  rest WAIT.**

### R8 — Mobile API + CORS + push

- **Scope boundary.** Per RFC-003 §11: documented `/api/v1/auth/*`
  contract; `mobile` JWT scope claim that gates destructive ops;
  CORS allowlist; `push_tokens` table; notification fan-out
  service consuming the same Inbox attention feed; web-push
  opt-in.
- **Sequence.** CORS policy ships **before R6** (one-line nginx +
  Flask change, unblocks staging mobile dev). The rest of R8 ships
  after R6 + R7.
- **Dependencies. PRODUCT INPUT REQUIRED.** Open redline #4 in
  RFC-003 (mobile distribution model: PWA / native / webview);
  decides whether APNs cert work is needed at all.
- **Risks.** CORS policy errors blocking unrelated services if the
  allowlist is too narrow or too wide. Push-token leakage. APNs /
  FCM cert management.
- **Testing / QA.** Mobile-app smoke test against staging
  (depends on product picking the distribution model). CORS policy
  cross-origin test from a known-bad origin. Push delivery timing
  on a synthetic fleet event.
- **Rollout strategy.** (a) CORS policy in R3-R5 window; (b)
  `push_tokens` table ships dark; (c) web-push opt-in for desktop
  operators; (d) mobile JWT scope; (e) notification fan-out service
  enabled by default once at least one operator has registered a
  push token.
- **Ship-early/wait.** **PARTIAL — CORS SHIP EARLY, rest WAIT.**

### R9 — Mobile-first responsive + Passkeys

- **Scope boundary.** Per RFC-003 §11.3 + §10.1 option E: bottom-tab
  nav on mobile; card-layout tables (already partially in R5);
  ≥ 44 pt touch targets; Passkey/WebAuthn enrollment alongside
  TOTP.
- **Sequence.** Last. Depends on R4 (nav structure), R7 (auth
  foundation), and ideally R5 (Fleet card layout already shipped).
- **Dependencies.** All prior phases shipped.
- **Risks.** Cross-browser passkey support gaps; tablet-middle-zone
  layout regressions.
- **Testing / QA.** Lighthouse mobile-perf ≥ 90 on Inbox + Fleet.
  Cross-platform passkey test matrix (Chrome desktop + iOS Safari +
  Android Chrome + Firefox).
- **Rollout strategy.** Feature-flagged. Mobile-first layout
  default-on for mobile breakpoints; Passkeys opt-in alongside
  TOTP.
- **Ship-early/wait.** **WAIT — final phase.**

## 5. Sequencing graph (compressed)

```
R1 ──► R2 ─┬─► R4 ──► R5 ──► R6 ──► R7(rest) ──► R8(rest) ──► R9
           ├─► R3 ─┘
           ├─► R7(sessions, shadow) ──► R7(sessions, enforce)
           └─► R8(CORS only)
```

R2 and R3 are parallelisable. R7-shadow and R8-CORS can run in the
R3–R5 window — they are out-of-band from the IA work. R6 is the
single load-bearing gate after R5.

## 6. Cross-cutting concerns

### 6.1 Schema migrations

Three forward-only schema changes total: R2 (`is_qa_fixture`
column), R6 (`sites` becomes a scope; `site_memberships` table; `devices.site_id`
becomes non-null), R7 (`sessions` table; `mfa_secret` on user;
`oauth_identities` table). Every migration is rehearsed against a
production snapshot before landing.

### 6.2 Backwards compatibility

- Every old `/app/*` URL keeps resolving through R5.
- The JSON admin-API contract version-pins at R8 (move to
  `/api/v2/admin/*` for any breaking change; keep `/api/v1/admin/*`
  alive for one minor).
- The device-facing API (`/api/v1/device/*`) is **not modified by
  this plan at all**. Devices in the field continue to work
  unchanged.
- Backwards-compat for the heartbeat-state field shipped in v0.2.7
  is already preserved (legacy `online: bool` lives alongside the
  new `heartbeat_state` enum).

### 6.3 Observability

Each phase registers an operator-experience metric:

| Phase | Metric |
|---|---|
| R1 | % of devices showing distinct heartbeat states |
| R2 | % of QA-fixture devices hidden by default |
| R3 | p95 latency of per-record audit query |
| R4 | Time-to-first-action after login (Inbox click-through) |
| R5 | Time-to-find-device by saved filter vs hand-built filter |
| R6 | Migration divergence count (target: zero before enforce) |
| R7 | % of super-admins with TOTP enrolled |
| R8 | Push delivery p95 (synthetic event → ack) |
| R9 | Lighthouse mobile-perf score |

### 6.4 Communication

Every minor that ships a phase carries: (a) a CHANGELOG entry; (b)
a one-time in-app banner pointing the operator at the new
behaviour; (c) a docs/ page update where the surface changed.

## 7. Approval gates

The plan needs explicit product approval at four points:

1. **Approve this plan as written.** Authorises R1, R2, R3, R7-shadow,
   R8-CORS to begin in the order above.
2. **Approve R4 nav scaffold for production.** After R3 has shipped,
   confirm the 5-item nav and the Inbox v1 hand-tuned ranking rules.
3. **Answer RFC-003 open redline #1 (split-site tool day-one or
   not).** Unblocks R6.
4. **Answer RFC-003 open redlines #2–#4.** Unblocks R7-rest (OIDC
   provider list, MFA flip timing) and R8-rest (mobile distribution
   model).

Any one of these gates can be answered without unblocking the
others; R1–R3, R7-shadow, and R8-CORS need only gate #1.

## 8. Risk register (ranked)

| Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|
| R6 site-migration data divergence | medium | high | dual-read shadow mode ≥ 7 days; data-parity assertion | backend |
| R7 mandatory-MFA flip locks operators out | low | high | warn-and-don't-enforce window per tier; per-tier rollout | backend + product |
| R4 layout regression on mobile/tablet | medium | medium | feature flag; staged opt-in/opt-out/default; full responsive QA pass | web/UI |
| R2 default-hide surprises an operator | medium | low | one minor where flag is OFF (shows toggle without hiding); one-time banner when flipped ON | web/UI |
| R8 CORS misconfiguration | low | medium | cross-origin-from-bad-origin test; dry-run in staging | backend |
| R5 saved-filter URL stability | low | low | versioned query-string parser | web/UI |
| R3 audit-query performance | low | medium | confirm/add `(target_type, target_id, created_at desc)` index | backend |
| R9 cross-browser passkey gaps | medium | low | test matrix; Passkey is opt-in alongside TOTP | web/UI |

## 9. Testing / QA expectations summary

- Every phase ships with a dedicated `tests/qa/test_v0XX_*.py`
  module against the live deployment (one module per minor
  version).
- Pre-existing v027 suite remains green at every minor.
- R6 has an additional **migration-rehearsal** suite that runs
  against a production snapshot in CI.
- R7 has an additional **auth-state matrix** suite that exercises
  every combination of (cookie / JWT / OIDC / magic-link / TOTP /
  Passkey).
- R8 has an additional **mobile smoke** suite gated on the
  product's distribution-model decision.

The QA-fixture isolation in R2 is what makes all of the above
sustainable — every regression test creates fixtures that the
production view will never see, removing the ongoing risk of
test-data pollution.

## 10. What happens if approval is delayed

- **R1 alone** is already greenlight-able. If the rest of this
  plan is approved later, R1 still ships independently and we
  remain in compliance with `feedback_release_every_version.md`.
- **R2 + R3 + R7-shadow + R8-CORS** can ship as a follow-up minor
  (v0.2.8) without any further redlines.
- **R4** ships after the §7 gate #2 redline.
- **R5–R9** ship in sequence after the §7 gates #3 and #4
  redlines.

If product wants to defer the broader redesign indefinitely, R1
through R3 alone deliver the core remediation: device-state
correctness, test-fixture isolation, per-record audit. The IA
and RBAC work is the *better* outcome, but it is not the
*required* outcome.
