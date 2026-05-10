# RFC-003: Web UI / Product Redesign

| Field | Value |
|---|---|
| Status | **Draft** (seeded 2026-05-09 from product/firmware/design directive after re-issue by PM) |
| Authors | rebooter-droids backend/web team; product-firmware-design |
| Targets | rebooter-droids web UI, RBAC layer, auth surface, future mobile app |
| Supersedes | — |
| Superseded by | — |

> **About this RFC:** internal-design-document sense. Lives in this
> repo for cross-team redlining. Comments belong as PRs against this
> file. This RFC is intentionally product-shaped, not code-shaped — no
> diff, no schema migration scripts, no template patches.

---

## 1. Summary

The current web UI is a *server-rendered admin console* — a thin
Jinja layer over the database. It correctly displays what the model
contains but does not represent what an operator actually needs to do.
The structural problems surfaced by recent live incidents (the
"100% offline" QA escalation, the QA-fixture pollution of the live
devices view, BUG-004 phantom-logout, BUG-005 cookie-revocation gap)
are symptoms of the same root cause: there is **no product layer**
between the model and the page.

This RFC proposes a redesign organised around three principles:

1. **Operator jobs first.** The home page is a "what needs attention"
   feed, not a stat grid. Every screen answers a specific question
   that maps to an operator job-to-be-done.
2. **Site is the unit of scope.** Devices, groups, deployments,
   audit, and access are scoped to a site. Multi-user / shared-access
   becomes a property of the site, not a property of the platform.
3. **One contract, two clients.** A formalised JSON+JWT API contract
   replaces the implicit "session cookie is the only auth" assumption,
   so the web UI and a future mobile app are first-class siblings of
   the same API rather than the API being a second-class export of
   the web UI.

The redesign is large but **strictly additive at the API layer** in
its first three phases — no destructive change to the device-facing
API or the JWT contract that already exists for human users. Phased
rollout starts with information-architecture restructuring on the
existing stack and lands the bigger pieces (site-scoping, OAuth,
mobile API) over multiple minor versions.

## 2. Why this RFC exists (motivation)

Three recent signals make this work load-bearing for the next quarter:

- **The "100% offline" escalation.** Operators saw a dashboard that
  said every device was offline, but the underlying truth was that
  *most of those rows were stale QA fixtures that had never sent a
  heartbeat at all.* The UI conflated two different states. v0.2.7
  fixes the immediate symptom (three-state heartbeat) but the
  conflation pattern is everywhere — the UI displays raw model state
  with no product judgement.
- **QA fixtures polluting production.** Nine fake devices were sitting
  in the live devices list because there is no separation between
  "test data" and "real fleet," and no operator-visible way to mark or
  filter the difference. The platform treats every row as equally
  important.
- **No multi-tenant story.** All admins see all devices, all groups,
  all sites. There is no concept of "Alice's office" vs "Bob's lab,"
  even though `Site` is already a model. The remediation plan flagged
  this as v0.3+ but never produced a concrete design.

Independently, the firmware team is asking for an eventual mobile
companion app, and the auth surface today (cookie session for the
web UI, JWT for the API, no OAuth, no MFA, no server-side cookie
revocation) is not the foundation we want to ship a mobile client
against.

## 3. Scope

In scope:

- Information-architecture redesign for the logged-in admin
  experience (`/app/*` and the `/api/v1/admin/*` API it uses).
- Site-scoped multi-user / RBAC redesign.
- Auth surface: MFA, server-side session revocation, OAuth/OIDC
  strategy, password-reset flow.
- Mobile-app compatibility: API contract formalisation, CORS,
  push-notification strategy.
- Mobile-first responsive layout (today's CSS does have media
  queries but the layout is desktop-first; we are flipping that).
- Operator-facing audit-log surfacing (per-record history, not just
  a global feed).

Out of scope (explicit non-goals):

- Replacing Jinja with a SPA front-end. Server-rendered HTML stays
  for the web UI; the mobile app is the SPA-shaped client. Premature
  SPA migration is a recurring failure mode for tools at this
  stage and we are not paying for it.
- Marketing site / public landing page. This RFC is about the
  authenticated admin product.
- Onboarding for end-consumers (households who buy a Sonoff S31
  off-the-shelf). The product-of-record is still
  installer-and-fleet-operator. A consumer onboarding flow is a
  separate RFC if/when it becomes a product direction.
- Real-time websocket UI. The current "refresh the page" model is
  acceptable for fleet sizes < 1000 devices. Revisit when fleet size
  warrants it.
- Replacing the existing device-facing API. Devices already have a
  stable contract; do not disturb it.

## 4. Current state — what we have on disk today

| Area | Today |
|---|---|
| Pages | 15 Jinja templates; one page per database concept (devices, groups, sites, firmware, users, invitations, audit, events, unregistered, me, login, invite_redeem). Dashboard is a stat-card grid plus a 25-item activity feed. |
| Routing | 14 admin-UI blueprints under `app/blueprints/admin/*` after the v0.2.6 split; matched 1:1 by `app/blueprints/admin_api/*` for the JSON contract. |
| Roles | `super_admin`, `admin`, `operator`, `viewer`. Action-gated, **not data-gated**: every role sees every device/group/site. |
| Sites | Model exists (`app/models/sites.py`); used as a *tag* on devices. Not a scope or a permission boundary. |
| Auth | Email + Argon2 password. Flask-Login session cookie (31-day) for the web UI; JWT access (1h) + refresh (14d) for the API. Rate-limited login (30/min, 200/hr). No 2FA, no OAuth, no password-reset flow, no server-side session revocation list (BUG-005). |
| Mobile responsive | `static/css/app.css` has `@media (max-width: 1024px)` and `@media (max-width: 640px)`. Tables get horizontal scroll; nav becomes a scrolling strip. No touch-target sizing concessions. Viewport meta is set. |
| Notifications to operators | None in-app. Cron-driven email alerts to the firmware team via the hub script; nothing in the web UI itself, no push. |
| Audit | Global page only (`/app/audit`). No "history of this device" link from a device detail page. |
| Mobile-app surface | JWT contract exists. No CORS policy. No push-notification token model. No documented "mobile-API subset." |

## 5. Identified product failures

Grouped by the dimension they break.

### 5.1 Information architecture

- **F1.** Dashboard answers "how many of each thing exist" (a
  database question), not "what should the operator do next today"
  (a product question). An operator who logs in to a healthy fleet
  cannot tell at a glance that nothing is wrong; they have to read
  numbers and infer.
- **F2.** "Devices" is a flat table. There is no by-site grouping,
  no saved filter, no by-group view, no by-firmware-version view.
  Every operational question requires the operator to assemble
  filters by hand each time.
- **F3.** Firmware deployment is buried under
  `/app/firmware`, even though "ship a release to fleet" is one of
  the three workflows the platform exists to support.
- **F4.** Per-record audit history is invisible. Audit lives on
  `/app/audit` as a global feed; you cannot ask "what happened to
  this device this week" without leaving the device page.
- **F5.** "Unregistered devices" and "Events" are presented as
  peer pages to "Devices," which is wrong — they are diagnostic
  views *of* the device population, not separate feature areas.

### 5.2 Workflows

- **F6.** No "incident response" surface. When a device misbehaves,
  the operator has no acknowledged-vs-open distinction; there is no
  way to mark "I am working on this," and no audit thread per
  incident.
- **F7.** Mass-action UX (group commands, fleet-wide deployment) is
  protected by a confirmation gate (good — BUG-012 fix) but is not
  visually distinct from normal actions until the second click.
  Operators cannot scan a screen and tell which buttons are scary.
- **F8.** Invite flow does not let the inviter scope the invite to
  a site or group. The operator is forced to choose between
  "give them admin to everything" or "do not invite them."
- **F9.** Test data has no first-class concept. There is no "this
  is a QA fixture" tag on devices, no toggle to hide them, no
  separation between dev/prod data on the same instance.

### 5.3 RBAC / shared access

- **F10.** Roles are global. There is no "Alice is an admin of
  Office site, viewer of Lab site." Every promotion is platform-
  wide.
- **F11.** Sites are tags, not scopes. A site has no member list,
  no per-site invite, no per-site audit, no per-site quota.
- **F12.** No transfer / hand-off model. When an operator leaves,
  there is no way to mark devices/groups as "owned by Alice" and
  reassign them to "owned by Charlie" — because nothing is
  per-user-owned to begin with.

### 5.4 Auth

- **F13.** No MFA / second factor. Compromised password = full
  fleet control.
- **F14.** Session cookie cannot be server-side revoked in the
  31-day window (BUG-005). "Revoke everywhere" only invalidates
  JWTs, not the cookie.
- **F15.** No password reset. A forgotten password requires a
  super-admin to deactivate + re-invite. This is not workable for
  a multi-user product.
- **F16.** No OAuth/OIDC. Every operator has to remember a
  rebooter-specific password.
- **F17.** No login-history / "where am I signed in" surface.

### 5.5 Mobile / API

- **F18.** No CORS policy on `/api/v1/*`. A mobile webview or
  cross-origin SPA cannot consume the API today.
- **F19.** No "mobile-API subset" defined. The admin API is
  permissive; the device API is narrow. Nothing in between for a
  human-on-a-phone-on-the-go.
- **F20.** No push-notification model. Operators cannot be alerted
  on their phone that a device went offline.
- **F21.** Layout is desktop-first; mobile breakpoints stack but
  don't restructure (nav is a horizontal-scroll strip rather than a
  bottom-tab bar; tables get horizontal-scroll rather than card
  layouts; touch targets are not enlarged).

## 6. Comparable products / patterns researched

| Product | Shape | What we are stealing | What we are not stealing |
|---|---|---|---|
| **UniFi Network Controller** | Pro / prosumer network admin | "Site" as the top-level scope; per-site role assignment; per-site invite. Mobile app uses the same JSON API as the web console. | The fully-realtime websocket-driven dashboard; we don't need that yet. |
| **Tailscale admin console** | SaaS, RBAC-heavy, OAuth-only sign-in | Tag-based ACL; OAuth as primary sign-in (Google / GitHub / Microsoft); SCIM-eventually; clear "who can do what" matrix per tag/scope. | SCIM in v1. |
| **Shelly Cloud** | Consumer IoT cloud | Mobile-first information architecture: rooms / scenes / quick actions, with pro features behind a "settings" stack. Operators use it from a phone, not a laptop. | Single-tenant household model — we need multi-tenant. |
| **Linear** | Project management, dense data | Command-K palette; saved views/filters with shareable URLs; the "what's actively wrong now" inbox metaphor. | The full project-management abstraction. |
| **GitHub** | Code-and-people | Per-repo (per-site) collaborator model; per-record audit; OAuth as sign-in option alongside email+password+2FA; "your activity" landing page. | Issues / PRs as primitives. |
| **Sonoff iHost / eWeLink** | Same hardware family, consumer cloud | Reference for what an operator coming from the consumer side expects (mobile-first, quick relay-on/off, scene tap). | Single-household RBAC; weak operator features. |

The strongest single influence is **UniFi**: site as a first-class
scope with its own roster, roles, and audit. The mobile-first
information architecture is borrowed from **Shelly + Linear**. The
auth strategy is borrowed from **Tailscale**.

## 7. Proposed information architecture

The top-level navigation collapses from 13 items to 5:

```
┌──────────────────────────────────────────────────────────┐
│  rebooter-droids                              [user▾]    │
├──────────────────────────────────────────────────────────┤
│  Inbox     Fleet     Releases     Site ▾     Settings    │
└──────────────────────────────────────────────────────────┘
```

- **Inbox** — *What needs attention right now.* Per-site by default.
  Items: device just went offline; device has been offline > 24h;
  device booted to a new firmware version; a deployment is stuck;
  a recent enrollment never sent its first heartbeat. Each item is
  acknowledgeable; acknowledged items move to a separate view.
  Replaces today's stat-grid dashboard.
- **Fleet** — devices, groups, sites, enrollments. Devices are the
  default tab; groups and sites are sibling tabs of the same
  surface, not separate top-level pages. Saved-filter chips above
  the table (e.g., "Offline > 24h," "Never heartbeated," "On
  firmware < latest stable"). The current "Events" and
  "Unregistered devices" pages collapse into diagnostic tabs of
  this surface.
- **Releases** — firmware releases, deployments, rollback. Promotes
  "ship a release" to a top-level workflow, not a sub-page of an
  admin section.
- **Site** — the *site picker*. A user with multiple sites switches
  here. Each site has its own member list, audit log, and settings.
  This is where the multi-user / RBAC story lives.
- **Settings** — the user's own profile, the site's settings (for
  members of that site), and the platform settings (super-admin
  only).

The 13-page model is preserved underneath as routes; the navigation
layer is what changes for v1, with deeper restructuring landing in
later phases.

## 8. Proposed key workflows

The platform exists to support these jobs-to-be-done. Each one gets
an explicit workflow in the redesign.

| Job | Today | Redesigned |
|---|---|---|
| **Triage what's wrong** | Read 5 stat tiles, click into devices, eyeball table | Open Inbox; see ranked attention items; click straight into the affected device with the audit thread already loaded |
| **Add a new device** | Mint enrollment token → copy/paste into firmware → wait → check devices list | "Enroll device" wizard from Inbox or Fleet; surfaces the QR code + token; shows live-progress as the device registers and first-heartbeats |
| **Ship a release** | Upload firmware → create deployment with target spec → manually verify | Releases tab → "New release" → upload → "Deploy to..." → progress bar across the chosen scope (with the mass-action gate) → green check when ≥X% of target scope reports the new version |
| **Investigate one device** | Devices list → click row → device detail → mentally cross-reference with global audit | Device detail with an embedded *per-record audit thread* (deploys, commands, ownership changes, role-on-this-device changes) |
| **Onboard a co-operator** | Invite by email + role → they accept → they see *everything* | Invite by email + role + **site scope** → they see only the sites they were invited to |
| **Hand off a site** | (no first-class flow) | Site → settings → "Transfer ownership" → confirms with both parties; logged in audit |
| **Recover from a bad release** | Upload a previous binary, deploy it manually | "Rollback" button on the deployment record itself; uses the mirror chain from RFC-002; does not need the primary host to be reachable |

## 9. RBAC + scoping redesign

### 9.0 DECIDED 2026-05-10 (B10 redlines)

Operator answered B10 Q1-Q4. The shape in 9.1 stands as a baseline
but is overridden where it conflicts with these locks:

- **Scope cardinality (Q1)**: `Site + Group + Device` — the role
  binding can target a `site_id`, a `group_id`, OR a `device_id`. A
  super_admin still bypasses scope. Policy table is wide; UI lets an
  admin pick scope from a typeahead. Worth the cost: it unlocks
  per-tenant admins, per-deployment operators, and per-rack
  helpers without a separate "department" abstraction.
- **Migration default (Q2)**: super_admins → global (unchanged);
  existing admins → an explicit row per current `site_id` (one-shot
  copy, not a wildcard); operators → no scope (must be re-granted
  by an admin before they can act). No admin gets locked out;
  operators get a useful nudge to the principle-of-least-privilege.
- **Audit retention (Q3)**: 365-day default, tunable via a new
  `system.audit_retention_days` runtime setting (DB → env-var →
  365). Nightly job soft-deletes older rows into
  `audit_events_archive`; archive purge is a manual operator step.
  The runtime-settings/UI work pairs with the System tab shipped
  in v0.4.26.
- **Invite shape (Q4)**: invite carries role + scope (locked at
  send-time). Invite redemption activates the user straight into a
  usable scoped role. Site-multi-select + group/device selector on
  the invite form.

These supersede the post-MVP-tier-1 framing in 9.1/9.2/9.3 below.

### 9.1 The shape

A user has a global *platform role* (super_admin / none) and zero or
more *site memberships*. Each site membership has its own role
(admin / operator / viewer). All resource queries go through a
"scoping middleware" that filters by the union of the current user's
site memberships unless the user is a platform super_admin.

```
User ─┬── platform_role: super_admin | none
      │
      └── site_memberships: [
              { site_id, role: admin | operator | viewer },
              ...
          ]

Site ─┬── id, name, slug, owner_user_id, created_at, ...
      ├── members: [SiteMembership]
      ├── devices: scoped by site_id
      ├── groups:  scoped by site_id
      ├── audit:   scoped by site_id
      └── settings: per-site
```

### 9.2 Migration from today's flat model

- Create one "Default" site at first migration; assign every existing
  device/group to it. Every existing user gets an `admin` membership
  in Default. Behaviour is unchanged for anyone who never opens the
  site picker.
- Super-admin role is preserved as the platform-wide escape hatch.
- All current "is_admin / is_super_admin" decorators are rewritten as
  "is_admin_of(current_site_id)" / "is_super_admin()."
- Devices have a non-nullable `site_id` after migration. This is the
  one schema change that has to land before the multi-user story can
  ship; everything else in the RBAC redesign is additive.

### 9.3 Invite redesign

Today: invite by email + role.
Redesigned: invite by email + (site_id, role) tuples, plus an
optional "platform super-admin" toggle that only an existing
super-admin can set. The invite redemption form shows the inviter's
identity, the sites they will be joining, and the role for each.

### 9.4 Audit per record

Every audit-log row already carries `target_type` + `target_id`. Add
a per-record audit slice (`/api/v1/admin/audit?target_type=device&target_id=...`)
and surface it in the device detail page as a tab. Same for groups,
sites, deployments, releases.

## 10. Auth + OAuth strategy

### 10.1 Five concrete options for human-user sign-in

| Option | Shape | Operator UX | Ops cost | Independence |
|---|---|---|---|---|
| **A. Email + password + TOTP MFA** | Status quo + 2FA | Familiar, friction-light | Low; one library | High |
| **B. Magic-link (email-only, no password)** | Email a one-time link; no password ever | Simple, no MFA needed if link is short-lived | Low; but depends on operator's email being reachable | Medium |
| **C. OIDC SSO (Google / GitHub / Microsoft)** | "Sign in with Google" + email/password as fallback | Best for operators who already have a Google/GitHub identity | Medium; per-provider OAuth app + redirect URIs | Lower (depends on OIDC IdP) |
| **D. SAML SSO** | Enterprise-grade IdP federation | Expected by enterprise buyers | High; per-customer IdP onboarding | n/a |
| **E. Passkeys / WebAuthn** | Phishing-resistant, no shared secret | Best long-term; consumer adoption uneven | Medium; library is mature | High |

**Recommendation:** ship A (email+password+TOTP MFA) as the v1
foundation, then layer C (Google + GitHub OIDC as primary social
sign-in) and B (magic-link as a no-password fallback) in P3. E
(passkeys) lands as a P5 add-on. D (SAML) is deferred until there is
a paying enterprise customer.

The Tailscale shape — *every primary sign-in option is OAuth, with
email+password as the local-only fallback for self-hosters* — is the
right end-state for an admin product. Magic-link is a stop-gap if
TOTP rollout is slower than expected.

### 10.2 Server-side session revocation (BUG-005)

Replace today's signed-cookie-only session with a server-side
session table:

```
sessions
├── id (ULID)
├── user_id (FK)
├── created_at, last_seen_at, expires_at
├── revoked_at (nullable)
├── user_agent, ip (snapshot for the "where am I signed in" page)
└── refresh_token_hash (for rotation)
```

The cookie carries only the session id. "Revoke everywhere" sets
`revoked_at` on every row for the user; a request with a revoked
session id is rejected immediately, regardless of cookie expiry.

### 10.3 Password reset

Magic-link flow: user enters email → server generates a one-shot
token → emailed link → reset form. Same primitive as B (magic-link
sign-in) — implementing one gets the other almost free. Rate-limited
identically to login.

## 11. Mobile-app compatibility

The redesign is shaped so the mobile app is a **first-class client
of the same JSON+JWT API the web UI now uses**, not a second-class
export.

### 11.1 API contract formalisation

- Promote the human-user JWT contract to a documented, versioned
  surface: `/api/v1/auth/login`, `/refresh`, `/logout`, `/me`,
  `/sessions` (list + revoke a single session). Already mostly
  exists; document and version-pin it.
- Add a `mobile` JWT scope claim and gate destructive operations on
  the absence of `mobile` for the first cut: a mobile-issued token
  can read everything its user can read and can send relay-cycle
  commands, but cannot delete devices or change roles. This bounds
  the blast radius of a stolen phone and lets the mobile app ship
  before all destructive flows have been redesigned for touch.
- CORS policy: allow the rebooter-droids web UI's origin and the
  mobile app's deep-link origin. Block everything else. Today there
  is no policy; this is what blocks a webview-based mobile dev loop.

### 11.2 Push notifications

- Add a `push_tokens` table: `(id, user_id, platform: apns|fcm,
  token, label, created_at, last_seen_at)`. Operators register
  their device when they sign in to the mobile app.
- Add a `notifications` fan-out service. Inbox items (the same
  attention feed surfaced in §7) become the source of truth; each
  inbox item with `priority >= high` fans out to every push token
  for every member of the affected site.
- Web UI gets a parallel browser-native push opt-in (Web Push) so
  desktop operators get the same alerts.

### 11.3 Mobile-first responsive on the web UI

- Bottom-tab nav on mobile (Inbox / Fleet / Releases / Site /
  Settings); top-bar on desktop. Same five items.
- Tables on mobile collapse to row-per-card with the primary
  action accessible without horizontal scroll.
- Touch targets ≥ 44 × 44 pt for any button on a mobile breakpoint.
- The Inbox is the default view on mobile; the stat-grid dashboard
  is desktop-only (or removed entirely).

### 11.4 Mirror chain reuse

The OTA mirror chain landing in RFC-002 is reused for any binary
the mobile app might want to fetch (release notes attachments,
firmware-package signatures). The mobile app does not become its own
mirror in v1, but the contract is shaped so it could in v2 (a phone
on the same LAN as a device could relay a firmware blob if the
device cannot reach the internet directly).

## 12. Phased implementation plan

| Phase | Ships | Reversible | Cuts over when |
|---|---|---|---|
| **P0** | This RFC, redlined and accepted. | Yes | Sign-off from product/firmware/design + backend/web. |
| **P1 — Information architecture (additive)** | New nav (Inbox/Fleet/Releases/Site/Settings); Inbox feed reading from the existing audit + heartbeat-state + deployment tables; saved-filter chips on Fleet; per-record audit slice; "Releases" rename of today's firmware page. | Yes — old pages are reachable by URL during P1, just not in nav. | Operator survey of one full week with a sample of fleets. |
| **P2 — RBAC + sites as scope** | `Site.member_id` model; "Default" site migration; site scoping middleware; site-scoped invite. Existing flat-permission super-admin escape hatch retained. | Hard to reverse; gated behind a feature flag during the migration window. | All existing fleets migrated to Default site; super-admin sees parity with pre-migration. |
| **P3 — Auth foundation** | Server-side session table + revoke-all; password-reset magic link; TOTP MFA opt-in; Google + GitHub OIDC sign-in. | Reversible per-feature behind flags. | TOTP enrolled by ≥1 super-admin per fleet; OIDC verified end-to-end. |
| **P4 — Mobile API + push** | Documented mobile JWT scope; CORS policy; `push_tokens` table; web-push opt-in; first cut of mobile-friendly Inbox. | Yes (additive). | Mobile app's smoke test against staging passes Inbox + Fleet read paths. |
| **P5 — Mobile-first responsive + passkeys** | Bottom-tab nav, card-layout tables, ≥44 pt touch targets; Passkey/WebAuthn sign-in; magic-link no-password sign-in. | Yes. | Lighthouse mobile-perf ≥ 90 on Inbox + Fleet. |

P1 and P3 are independent and can be parallelised by different
contributors. P2 must precede P4 because the mobile API leans on
site-scoped queries.

## 13. Risks + open redlines for product/firmware/design

1. **Site migration risk.** Forcing every existing device into a
   "Default" site is a one-way schema change. If we get the boundary
   wrong (e.g., a fleet wants two sites from day one) the migration
   is awkward to undo. *Open redline: should P2 ship a "split-site"
   tool from day one, or is post-migration manual reassignment
   acceptable?*
2. **OAuth provider choice.** Google + GitHub covers the operator
   demographic we know about. *Open redline: do we also want
   Microsoft (for enterprise IT operators) in P3, or defer?*
3. **MFA enrolment ramp.** Mandatory MFA on day one will lock out
   operators mid-flight. Recommended: opt-in for one minor version,
   then mandatory for super-admins, then mandatory for admins.
   *Open redline: which version flips the mandatory toggle?*
4. **Mobile-app distribution model.** PWA, native iOS+Android, or
   webview-wrapper? *Open redline: this is a product call that
   shapes whether we need APNs at all in P4.*
5. **Inbox scoring.** "What needs attention" requires a ranking
   function. v1 can be a hand-tuned set of rules (offline > 24h
   beats offline > 1h beats never-heartbeated > 30min). *Open
   redline: do we want the inbox to be configurable per operator
   in v1, or accept the default and add configurability later?*
6. **Test-data partition.** F9 (no first-class test-data concept)
   could land as a per-site `is_test` flag that hides the site from
   the default site picker, or as a per-device `is_qa_fixture` tag.
   *Open redline: which level of granularity does product want?*
7. **Per-page WebSocket vs poll.** Inbox could live-update via SSE
   without a full WebSocket stack. *Open redline: is SSE acceptable
   or do we want to defer realtime entirely to v2?*

## 14. Appendix — what does *not* change

- The device-facing API contract (`/api/v1/device/*`). Devices in
  the field continue to work unchanged.
- The OTA firmware-mirror chain landing in RFC-002 — this RFC
  consumes that work but does not modify it.
- The audit-log schema. We add new query slices, not new columns.
- The presence-automation surface from RFC-001. Presence rules
  remain server-side with mobile as the publisher of presence
  events; the redesigned Inbox is a natural display surface for
  presence-driven incidents.
- The four roles (super_admin / admin / operator / viewer). What
  changes is the *scope* they apply at, not the names or the action
  matrices.

## 15. Decision matrix (for the redline session)

| Decision | Default proposed | Open until |
|---|---|---|
| Top-level nav: 5 items? | Inbox / Fleet / Releases / Site / Settings | P1 sign-off |
| Site as scope unit? | Yes | P0 sign-off |
| MFA in P3? | TOTP, opt-in | P3 sign-off |
| OIDC providers in P3? | Google + GitHub | P3 sign-off |
| Mobile JWT scope gate? | `mobile` claim blocks destructive ops | P4 sign-off |
| Push transport in P4? | APNs + FCM + web-push | P4 sign-off |
| Inbox ranking in v1? | Hand-tuned rules, not configurable | P1 sign-off |
| Test-data partition shape? | Per-site `is_test` flag | P2 sign-off |
