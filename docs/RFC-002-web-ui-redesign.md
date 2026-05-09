# RFC-002: Web UI Redesign + Auth Strategy

| Field | Value |
|---|---|
| Status | **Draft** (seeded 2026-05-09 in response to product/design directive) |
| Authors | backend / web team (initial draft); product / firmware / design team (review) |
| Targets | rebooter-droids admin web UI, device-owner end-user UX (future), mobile-app contract (future) |
| Supersedes | — |
| Superseded by | — |

> Research-first. No code yet. The point of this document is to study
> what good looks like, name what's wrong with the current UI, propose
> a direction, and lay out a phased plan with named risks before any
> implementation starts.

---

## 1. Research findings

### 1.1 Comparable products studied

We grouped reference products by how close their problem domain is to
ours. Closest first.

**Multi-device control / network recovery (closest to our domain):**

- **Cisco Meraki Dashboard** — gold standard for fleet device admin.
  Org → network → device hierarchy, status snapshot at the top of every
  page, "click anything that looks selectable", strong RBAC with
  predefined roles, audit trail surfaced inline, works well on tablet.
  Their *information architecture* is the single biggest takeaway: the
  user lands on health, drills into a problem, acts, returns to health.
- **Ubiquiti UniFi Network/Protect** — site-scoped, topology-first
  visualisation, single-page console with a left sidebar. Mobile app is
  a peer to the web UI, not a stripped-down version. Good lesson: the
  mobile app and web UI **share the same API contract** — which is
  exactly the rule we already have.
- **Shelly Cloud** — closest consumer parallel. Per-device cards in a
  responsive grid, room/group abstraction, automation rules, OAuth
  (Apple/Google/Microsoft) on top of email-password, multi-user sharing
  with role hints. Mobile-first with web UI auto-following.
- **TP-Link Kasa / Tapo** — pure mobile-first; web UI is an afterthought.
  Lesson: don't make our web UI feel like a port of a phone screen.
  Web should still be primary for admin work.
- **SmartThings** — rooms + automations + invited household members.
  Strong "guest" / "member" affordance.
- **AWS IoT Device Management** — fleet provisioning, jobs (mass
  actions), per-device shadow state. Audit + jobs UI is heavy but the
  *dry-run + cohort rollout* pattern is exactly what RFC-001 already
  proposes.
- **Particle Console** — small-fleet IoT admin. Light, well-organised.

**Multi-user admin platforms (for RBAC + invites + auth UX):**

- **Stripe Dashboard** — the gold standard. Three predefined roles
  (Owner/Developer/Read-only by default; can extend), email-invite
  acceptance flow, password + 2FA + SSO option, per-team-member
  audit trail, one-click revoke. Their settings IA: profile,
  team, security, billing, developers, integrations.
- **Linear** — workspace-first, members tab, invitation by email,
  no role explosion, very strong onboarding.
- **Notion** — sharing model is more granular (per-page) than ours
  needs; lesson: avoid over-modelling permissions in v1.
- **GitHub orgs** — owners/members/outside-collaborators; tab pattern
  for org settings is clean.
- **Auth0 / Clerk / WorkOS dashboards** — for *how* to ship auth
  flows: passwordless, magic links, social, MFA, SSO.

**Observability / fleet ops (for status visibility):**

- **PagerDuty / Datadog / Grafana** — header-pinned health summary, a
  feed of "what changed", drill into incidents. The "what changed
  recently" feed on the dashboard we already started is the right
  shape; needs to grow.
- **Sentry / Honeycomb** — auto-refreshing, sticky filters, saved
  views.

### 1.2 Cross-cutting patterns we'll borrow

| Pattern | From | Why it fits us |
|---|---|---|
| Org → Site → Device hierarchy | Meraki, UniFi | Maps cleanly to our future multi-tenant model |
| Health-first dashboard | Meraki, Datadog | We've started this; expand to "what to do today" |
| Member-tab with role columns + invite-by-email | Stripe, Linear | Our invitation flow is right; UX needs polish |
| Predefined role set, no per-resource ACL in v1 | Stripe | Already correct (super_admin/admin/operator/viewer); preserve |
| Passwordless / magic-link in addition to password | Auth0, Clerk | Lower friction; many phones, no LastPass |
| OAuth as *optional* layer over local accounts | Stripe (SSO add-on), Shelly Cloud | Customers want optionality; we shouldn't force a provider |
| Mobile + web peer-not-port | UniFi, Meraki | Future mobile app speaks same `/api/v1/*` |
| Dry-run + staged rollout | AWS IoT Jobs | RFC-001 already proposes this; UI must surface it |
| Toast notifications + sticky undo | Stripe, Linear | Better than the current Flask flash + page-reload |
| Command palette (`Cmd-K`) for power users | Linear, Notion | Solves "where is the firmware page" without bloating the nav |

### 1.3 Mobile-first specifics

- **Touch targets ≥44 px** (Apple HIG) / 48 px (Material Design).
  Several of our buttons are 26 px high.
- **Bottom navigation** on phone, **left rail** on tablet/desktop.
  Single component that swaps layout via media queries, not two
  templates.
- **Action sheet** > modal on phone for confirm flows.
- **Pull-to-refresh** is expected on lists.
- **Skeleton states** for the first paint; actual content streams in.
- **Sticky filter bar** at the top of long lists.
- **Swipe-to-action** (e.g. delete, send command) on phone — optional.

### 1.4 Auth UX trends (2025–2026)

- **Passwordless is now mainstream.** Magic-link or one-time-code by
  default; password optional or upgrade.
- **Passkeys** (WebAuthn / FIDO2) gaining adoption; supported by all
  major OSes and browsers.
- **2FA defaults-on for admin.** Stripe, GitHub, Google all default
  admins to MFA.
- **OAuth/SSO is *additive***, never the only path — customers self-
  host, customers without a Google account, customers with corporate
  identity (SAML/OIDC) all need a path in.
- **Session management as a feature**: list active sessions, sign out
  individual ones, "trusted devices".

---

## 2. Product / UI problems in the current web UI

Categorised by severity. "Severity" here means impact on real users
once we have any.

### 2.1 Severe — block bringing on real users

- **No mobile-first layout.** Tables overflow on 375 px, top-nav becomes
  unreadable. The QA suite catches this on `/app/login`, `/app/`,
  `/app/devices`, `/app/events`, `/app/audit`, `/app/users`.
- **No onboarding flow.** A new admin invitee redeems a token and lands
  on a sparse dashboard; no walkthrough, no "what to do first" hint, no
  "your account is set up — invite your team / add your first device"
  prompts.
- **Browser-native `confirm()` dialogs** for everything destructive.
  Inconsistent across OS/browser, hard to phrase, can't be styled,
  can't show context.
- **No safe destructive-action pattern.** "Delete device" is a single-
  click confirm; mass-action gate is server-only with a re-submit; no
  undo, no recently-deleted view.
- **Forms are uneven.** Spacing varies, error rendering uses Flask
  flashes mixed with inline `<p class="error">`, no validation until
  submit. No loading state on the submit button.

### 2.2 High — degrade UX once a second human uses the app

- **Dashboard is a stat grid + activity feed + jump list.** None of
  these answer the operator question "what needs my attention today?".
  Should be a triage view.
- **Top nav is alphabetical-ish, not task-ordered.** "Audit" is right
  next to "Profile" but they aren't related.
- **No global search.** Finding device `dev_01KR5HV…` requires
  scrolling.
- **No saved views / sticky filters.** The events page filter resets
  on every navigation.
- **No real-time refresh.** Heartbeats land every 60 s but the device
  list doesn't auto-refresh; the operator must reload.
- **Audit log is raw JSON in a `<code>`.** Not skimmable.
- **Per-device timeline** doesn't exist. To see "what happened to this
  device today" you cross-reference three pages.
- **Bulk actions** are command-only and hidden inside the group fan-out
  flow. No checkbox-select + apply on the devices list.
- **Help text is sparse.** New operator can't tell `operator` from
  `admin` without reading code.

### 2.3 Medium — polish

- **No empty-state guidance.** "No devices yet" is one line; should be
  a "here's how to enroll your first device" CTA.
- **No keyboard shortcuts.** Power-user friction.
- **No copy-to-clipboard helper** for enrollment tokens / device IDs.
  Currently long ULIDs make selection-copy painful.
- **Time formatting is raw UTC.** Most operators want local time with a
  UTC tooltip (RFC-001 §15 already locks "UTC server-side, render in
  user TZ").

### 2.4 Low — known/acceptable

- The current dark theme is fine; reuse the palette.
- Favicon, 404, robots — already shipped in v0.2.3.
- Server-side rendering is fine for our scale; no need to go SPA.

---

## 3. Proposed design direction

### 3.1 North-star principles

1. **Mobile-first, web-equal-peer-with-future-app** — the same UX
   ladders cleanly from a 375 px phone to a 27" monitor; the future
   native app is a *re-skin* of the same backend, not a different
   product.
2. **Health-first information architecture** — every page answers a
   question the operator was already asking: "is anything broken?",
   "did my change land?", "who did this and when?".
3. **Safe by default for destructive actions** — every mass action
   (already gated server-side) gets a UI affordance proportionate to
   blast radius. Bulk on tiny scope is one tap; bulk on big scope is a
   typed confirmation.
4. **Single design system** — primitives (button, card, table-cell,
   badge, modal, toast, form-field) live in one CSS module. No bespoke
   inline styles in templates.
5. **Auth that's local-default, OAuth-optional, never-locked-to-vendor.**
6. **Accessible by default** — keyboard nav, focus rings, contrast,
   labels. We're a small fleet today; doing this once now is cheaper
   than retrofitting for SOC2 / GDPR later.

### 3.2 Information architecture (proposed)

The current top-nav is "everything visible". The proposed nav is two
layers:

- **Bottom nav (phone) / left rail (tablet+)** — five primary
  destinations only. Picked for "what does this user want to do most":
  - **Health** (dashboard — the one people open in the morning)
  - **Devices** (the unit of work)
  - **Rollouts** (firmware + group commands — the highest-stakes flows;
    intentionally separated from device list)
  - **Activity** (events + audit + unregistered, unified timeline)
  - **Settings** (profile + users + invitations + sites + groups +
    enrollment tokens — anything you change once a week)
- **Action button** (centered FAB on phone, top-right on desktop) —
  context-aware. On Devices it's "Enroll a device"; on Rollouts it's
  "Deploy firmware"; on Settings/Users it's "Invite a user".
- **Global search bar** — `Cmd-K` opens a command palette: jump to a
  device by partial id/name/MAC, jump to a user by email, jump to a
  recent event.

### 3.3 Page-by-page redesign sketches (in words)

- **Health** — top: 4 vital cards (online %, devices needing attention,
  active rollouts, unregistered-attempt count). Middle: "needs your
  attention" — a list (offline > 5 min, failed deployment, unredeemed
  invitations, alerts). Bottom: chronological recent activity (the
  current feed, polished).
- **Devices** — list with sticky filter bar (search, site, group,
  status). Each row is a card on phone / row on desktop. Click → device
  detail with three tabs (*Status*, *Commands*, *History*). Multi-
  select via long-press on phone or checkbox on desktop with a sticky
  action bar that adopts the mass-action gate.
- **Device detail** — Status tab: live heartbeat with countdown to next
  expected, big "Send command" splitter (relay on/off/cycle/restart).
  Commands tab: pending + completed. History tab: per-device interleaved
  events + commands timeline.
- **Rollouts** — two sections: *Firmware releases* (cards with sha,
  size, channel, deploy button) and *Active rollouts* (each rollout is
  a progress bar by completion %, drill in for per-device state).
- **Activity** — unified timeline: device events + audit log +
  unregistered attempts on the same vertical line, filterable by
  source, with a saved-view drawer.
- **Settings → Members** — Stripe-like table; columns Email, Role
  (editable), Last login, Status, Actions. Top button "Invite". Side
  panel for the invitation form.
- **Settings → Profile** — current `/me` polished, plus *Sign-in
  methods* (password, OAuth providers if any), *Active sessions* with
  individual sign-out, *Two-factor* (when shipped).

### 3.4 Component primitives (the design system)

Single `static/css/system.css` with documented tokens:

- Colours (semantic: surface, surface-2, text, muted, accent,
  success, warning, danger).
- Spacing scale (4 px base).
- Type scale (4 sizes max).
- Radius scale (sm, md, lg).
- Shadows (one elevation, used sparingly).
- Component classes:
  `btn`, `btn-primary`, `btn-danger`, `btn-ghost`, `btn-icon`,
  `card`, `field`, `field-error`, `badge`, `badge-{green,amber,red}`,
  `table-wrap`, `table-row`, `cell-mono`,
  `nav-rail`, `nav-bottom`, `nav-link`,
  `toast`, `toast-{ok,error,info}`,
  `modal`, `modal-sheet` (phone),
  `skeleton`.

Templates compose these — no inline `style="…"` (currently 30+
inline-style sites; refactor target).

### 3.5 Real-time + responsiveness

- **Polling**, not SSE, for v0.3 — keeps the architecture simple. Lists
  refetch every 30 s when visible; detail pages every 10 s. Minimal
  delta endpoint optional later (`/api/v1/admin/devices?since=…`).
- **Skeleton on first paint**; subsequent updates are diff-only.
- **Loading states on submit buttons** — prevent double-submit.

---

## 4. Phased implementation plan

Each phase is independently shippable and reversible. No phase
"requires" the next.

### Phase 0 — Design system + responsive shell (~1 sprint)

- Build `static/css/system.css` with tokens + primitives. No template
  changes yet.
- Migrate `layout.html` to the new shell: bottom nav on phone, left
  rail on tablet+, header bar with search-stub (Cmd-K not yet wired).
- All 14 templates inherit the new shell unchanged otherwise.
- **Exit criteria**: `pytest tests/qa -m responsive` is green at
  375 × 667 + 768 × 1024. Today 7 of those tests fail.

### Phase 1 — Information-architecture rebuild (~1 sprint)

- Five-destination nav as proposed. Routes consolidate:
  `/app/activity` aggregates `/app/events` + `/app/audit` +
  `/app/unregistered-devices`.
- New `/app/health` replaces the current `/app/` dashboard; old route
  redirects.
- Global search box (server-rendered fallback first; Cmd-K palette
  later in Phase 5).
- **Exit criteria**: every existing template renders inside the new
  shell; `url_for(...)` calls in templates that point to merged routes
  use the new endpoint names with deprecation aliases for one release.

### Phase 2 — Devices + Rollouts page redesigns (~1.5 sprints)

- Devices list → sticky filter, card/row dual-mode, multi-select with
  sticky action bar.
- Device detail → three tabs.
- Rollouts → cards-and-progress.
- **Exit criteria**: a real operator can do "enroll a device, send a
  relay-cycle, watch it succeed" entirely on a phone without zooming.

### Phase 3 — Activity unified timeline (~0.5 sprint)

- One timeline data structure on the backend (`/api/v1/admin/timeline`)
  that interleaves events + audit + unregistered.
- One template on the frontend.
- Saved-view drawer (filters bookmarked in URL params).

### Phase 4 — Onboarding + invite-redeem polish (~0.5 sprint)

- First-run wizard for the bootstrap super-admin: "Welcome → set your
  display name → invite your team → enroll your first device". Each
  step is skippable; remembered in `users.onboarding_state`.
- Invite-redeem page redesigned with branded copy, role explanation,
  password requirements visible, "what you'll be able to do" list.

### Phase 5 — Power-user surface (~0.5 sprint)

- Cmd-K command palette (search across devices/users/groups/recent).
- Keyboard shortcuts on lists (`/` focus search, `j/k` move, `Enter`
  open).
- Copy-to-clipboard buttons on all `<code>` IDs.

### Phase 6 — Auth expansion (~1 sprint)

See §5. Magic-link + 2FA before any OAuth; OAuth behind a feature
flag with a pluggable provider abstraction.

### Phase 7 — Mobile native app (out of scope here, after Phase 2 lands)

The web UI redesign deliberately uses the *same component vocabulary*
the native app will. The native app gets to skip several phases
because phases 0–3 already produced their data shape.

### Estimated total

If a "sprint" is 3–5 focused hours of dev work for me + 2–3 days of
operator review per phase, the redesign through Phase 5 is roughly
**3–4 weeks of calendar time**. Phase 6 (auth) is its own track and
can run in parallel.

---

## 5. Auth / OAuth strategy options

Locked principle: **local password is always available; OAuth is
additive; no vendor lock-in**.

### 5.1 Layered identity model

```
┌────────────────────────────────────────────────────┐
│  rebooter-droids users table  (canonical)          │
│   id, email, role, password_hash (nullable),       │
│   onboarding_state, totp_secret (nullable), …      │
└──────────┬─────────────────────────────────────────┘
           │  many-to-one
           ▼
┌────────────────────────────────────────────────────┐
│  external_identities (new — Phase 6)               │
│   user_id, provider, subject, last_used_at,        │
│   profile_blob_jsonb                               │
└────────────────────────────────────────────────────┘
```

A user can have **0..N** `external_identities`. Local password is just
"provider = local"; the external table is for everything else.
Disconnect any provider any time as long as one sign-in path remains.

### 5.2 Sign-in mechanisms (in shipping order)

1. **Email + password (existing).** Polished UI; argon2 already in.
2. **Magic-link / one-time-code (Phase 6a).** Use existing SMTP
   plumbing. Single-use, 10-min TTL, rate-limited. Useful for users
   who forget passwords.
3. **TOTP 2FA (Phase 6b).** Optional per-user; required on
   `super_admin` once we have ≥1 such user beyond the bootstrap.
   `pyotp` library, no new dep weight.
4. **OAuth/OIDC (Phase 6c).** Pluggable provider list. Recommend
   first wave:
   - **Google** (broadest consumer/SMB coverage)
   - **Microsoft / Entra** (corporate)
   - **Apple** (privacy-first; required if we ship iOS)
   - **GitHub** (developer-flavoured customers)
   Behind feature flag per-provider. Library:
   `authlib` (Flask-OAuthlib's modern successor) — provider-agnostic.
5. **SAML / Enterprise SSO (Phase 6d, only if a customer asks).**
   Same `external_identities` table; library: `python-saml` or via
   WorkOS-style proxy. Defer until a real customer needs it.
6. **Passkeys / WebAuthn (Phase 6e).** Future; stays in the
   `external_identities` model.

### 5.3 What we're NOT doing

- Not committing to a specific OAuth provider yet. Per the directive,
  identify the realistic paths and keep the architecture extensible.
- Not running our own OAuth/OIDC provider for third parties to
  consume against rebooter-droids. (Different problem.)
- Not adopting Auth0 / Clerk / WorkOS as a SaaS dependency in v1.
  Reasoning: our locked rule says "all access via the
  rebooter-droids HTTPS API" and that includes auth. Adding a SaaS
  identity provider is fine *as a backing store for a provider's tokens*;
  not as the canonical user store.

### 5.4 Security additions in the same arc

- Server-side session JTI table (closes BUG-005).
- Refresh-token revocation list.
- Active-sessions UI on `/app/me` so users can see "you're signed in
  on 3 devices" and sign individual ones out.
- Rate limit by `(provider, IP)` not just IP for OAuth callbacks.

---

## 6. Risks / tradeoffs

| Risk | Severity | Mitigation |
|---|---|---|
| **Big-bang redesign breaks current operator workflow.** | high | Phased; every phase is independently shippable; old routes redirect; feature-flag the new shell for first 1–2 phases so operator can flip back. |
| **Mobile-first compromises desktop power-user efficiency.** | medium | Bring keyboard shortcuts + Cmd-K palette in Phase 5; bulk-select more efficient on desktop than current. |
| **Real-time polling load.** | low | Polling only when tab visible (`document.visibilityState`); 30 s default; cache headers; stop entirely on background. |
| **OAuth introduces external dependency + orphan-account scenarios.** | medium | Local password always remains as a fallback path; admin can disconnect a provider. `external_identities` table tracks last-used so we can detect dead links. |
| **Design system from scratch vs adopting Tailwind / Bulma.** | low | Stay with hand-rolled CSS — keeps zero build pipeline (currently zero JS bundler). One CSS file, ~300 lines, well-scoped. |
| **Switching to a SPA framework (React/Vue) midway through.** | high (if we did it) | Don't. Server-rendered + targeted JS island for the command palette is sufficient for this UX scope. Re-evaluate only if Phase 7 native app shares >50% of view code, which it won't. |
| **Real users find the new IA confusing.** | medium | Get one operator (the architect) and one firmware-team-member through Phase 1 before merging; collect verbatim feedback; iterate before Phase 2. |
| **2FA enforcement locks out the bootstrap super-admin.** | high | TOTP is opt-in until ≥2 super-admin accounts exist. Recovery codes printed at enrolment. Bootstrap-admin-via-env-var still bypasses (intentional). |
| **OAuth callback URL drift across www + www2 + future node-2.** | medium | Use a single canonical callback URL on www (primary); www2 transparently proxies to www1's callback today; v0.3 multi-node deploy must register both URLs with each provider. Document in `architecture.md`. |
| **GDPR/privacy data export-and-delete obligations.** | low today, real later | Phase 1+ surfaces an "Export my data" button on `/app/me`; implementation deferred but designed-for. |
| **Mobile app team disagrees with our IA later.** | medium | Treat RFC-002 as the contract. Mobile team redlines this doc before Phase 2 starts; their concerns become PR comments here. |

---

## Open questions for cross-team review

| # | Question | Owner |
|---|---|---|
| Q1 | Is the five-destination nav (Health, Devices, Rollouts, Activity, Settings) the right grouping, or do we want Devices + Groups as siblings? | design |
| Q2 | Should the device-owner end-user persona (a customer who has 1 Sonoff S31 in their home) get a *different* UI surface (read-mostly, no admin chrome), or is it a permission-restricted view of the same UI? | design + product |
| Q3 | When the native mobile app exists, do we ship login flows in-app (talking to `/api/v1/auth/*`) or open the web UI in a webview for sign-in? Affects OAuth callback design. | mobile (TBD) + backend |
| Q4 | OAuth providers in first wave: Google + Microsoft + Apple is my proposal. Push back if a customer cohort needs different. | product |
| Q5 | 2FA: opt-in per user, or required once a workspace has ≥2 super_admins? | security / product |
| Q6 | Do we want an "operator-style" workspace (single org) or true multi-tenant (multiple orgs each with their own users / fleet) in v1? Affects schema for users, devices, roles, billing. | product |
| Q7 | Branding pass — keep "Rebooter-Droids" name + dark theme + current accent palette, or open up a brand exploration? | design |

---

## Decision log

- **2026-05-09** — RFC seeded. Status = Draft. Awaiting product /
  firmware / design redline.

## References

- `docs/architecture.md` — current backend module layout (including
  the v0.2.6 admin-blueprint split).
- `docs/RFC-001-presence.md` — presence-automation RFC; UX surfaces
  here will be added in Phase 2/3 once data model lands.
- `docs/SPEC.md`, `docs/API.md`, `docs/DEVICE_INTEGRATION.md` —
  current contracts. The redesign does not change any of them.
- `docs/testing-split.md` — responsive test markers driving Phase 0
  exit criteria.
- `docs/bug-log.md` — open hardening items (BUG-005 logout
  revocation, BUG-006 rate-limit) intersect with Phase 6 auth work.
