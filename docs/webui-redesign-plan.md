# WebUI Redesign — Plan

| Field | Value |
|---|---|
| Status | **Draft** (planning-only deliverable; do not implement until product redline) |
| Authors | rebooter-droids design + product/architect track |
| Companion docs | `webui-redesign-research.md`, `webui-redesign-requirements.md` |
| Builds on | `RFC-003-web-ui-redesign.md`, `REMEDIATION-PLAN-2026-05-WEB-UI.md`, `RFC-002-firmware-mirrors.md`, `PROJECT-STATE-2026-05-09-FULL-SYNC.md` |

> This document is the *implementation plan* half of a redesign
> trio. It is the deliverable the team will execute against
> **after** product reviews the research and requirements docs.
> It does not contain code or configuration changes — only the
> architectural decisions that shape them.

---

## 1. Executive summary

The redesign is large but tractable because the backend is already
in good shape. The plan below ships the redesign in **seven phases
(P1–P7)** that map cleanly onto the brief's phase outline, and it
**replaces** the prior `REMEDIATION-PLAN-2026-05-WEB-UI.md` R4–R9
phases by incorporating their substance into the new structure.
R1–R3 and R7-shadow / R8-CORS from the prior plan have **already
shipped** (v0.2.7 → v0.2.11) and are the foundation this plan
builds on.

The single biggest decision in this plan is the **frontend
framework choice** (Section 8). The recommendation is **server-
rendered Jinja with HTMX + Alpine.js** for v1, with a deliberate
escape hatch to migrate the Inbox / device list / rule builder to
React-or-Svelte islands later if responsiveness needs warrant it.
Rationale: keeps the deployment shape (one Flask container, no
build step), preserves SEO-noise immunity, matches the team's
current Python competence, and avoids the "we accidentally became
a SPA shop" failure mode that haunts admin-tool redesigns.

The second-biggest decision is the **watchdog rule data model**
(Section 7). A normalised `watchdog_rules` table with a sibling
`watchdog_probe_events` event-log table covers v1 cleanly and
gives schedules a path to share the trigger-condition-action
machinery in a future merge.

Migration plan (Section 11) is **two one-way schema changes**
(site-as-scope `site_memberships` table + `watchdog_rules` /
`schedules` / `notification_rules` / `api_tokens` tables) plus
**one deprecation** (`/app/audit` becomes a tab inside the new
History view; the old URL keeps redirecting through v1.x).

Risks (Section 12) cluster around three things: the rule-builder
UX is genuinely novel and will need iteration; the site-as-scope
migration is one-way and needs the dual-read shadow window; and
the framework decision is reversible only at significant cost
once we ship UX on top of it.

## 2. Reconciliation with prior planning

### 2.1 What this plan replaces

| Prior planning artefact | Status |
|---|---|
| `REMEDIATION-PLAN-2026-05-WEB-UI.md` R1 (v0.2.7 ship) | **Done** — shipped 2026-05-09. Carried forward as-is. |
| Prior R2 (test-fixture isolation, v0.2.8) | **Done.** Carried forward; QA-fixture filter requirement R-DEV-4 honours it. |
| Prior R3 (per-record audit slice, v0.2.9) | **Done.** Carried forward; per-record extension to rules/schedules is R-LOG-4. |
| Prior R7-shadow (sessions table shadow mode, v0.2.10) | **Done.** Carried forward; this plan flips enforce in P5. |
| Prior R8-CORS (v0.2.11 allowlist) | **Done.** Carried forward as-is. |
| Prior R4 (5-item nav scaffold) | **Replaced** — incorporated into P1 design-system foundation with a 5-item nav whose set differs slightly from RFC-003 (see §3). |
| Prior R5 (Fleet redesign + saved filters) | **Replaced** — incorporated into P2. |
| Prior R6 (site-as-scope) | **Replaced** — incorporated into P5; same shape, more detail. |
| Prior R7-rest (TOTP/OIDC/password-reset) | **Replaced** — incorporated into P5. |
| Prior R8-rest (push, mobile JWT scope) | **Replaced** — incorporated into P6 + P7. |
| Prior R9 (mobile-first responsive + Passkeys) | **Replaced** — mobile-first is now P1 (foundational, not finishing); Passkeys defer to a post-v1 follow-up. |

### 2.2 What this plan adds that RFC-003 did not include

- **Watchdog rules** as a full feature surface (RFC-003 mentioned
  rule semantics in passing; this plan designs the data model,
  rule-builder UX, and probe runtime).
- **Schedules** as a separate primitive sharing the trigger /
  action machinery.
- **Notifications** as a configurable system with email + webhook +
  web-push channels (RFC-003 §11.2 sketched push only).
- **Settings page** with backup/restore, API tokens, webhook
  config, MQTT (v2), HA integration (v2).
- **Per-device lockout flag** (`is_protected`).
- **Reason field** on every power action audit row.
- **Plain-English rule statement** as the canonical rule
  representation.
- **API tokens** for headless integrations.

### 2.3 What RFC-003 specified that this plan honours unchanged

- Site as the unit of scope (RFC-003 §9; this plan §6).
- Server-side session enforcement closing BUG-005 (RFC-003 §10.2;
  this plan §10 + P5).
- Email + password + TOTP MFA + Google/GitHub OIDC as the v1 auth
  stack (RFC-003 §10.1; this plan P5).
- Per-record audit slice (RFC-003 §9.4; already shipped v0.2.9).
- API contract version-pin to `/api/v2/admin/*` for any breaking
  change (RFC-003 §6.2; this plan §11).

## 3. Information architecture

Replaces RFC-003's 5-item nav with a slightly tuned 5-item set
shaped by the requirements doc.

```
┌──────────────────────────────────────────────────────────┐
│  rebooter-droids                              [user▾]    │
├──────────────────────────────────────────────────────────┤
│  Status   Devices   Rules   History   Settings          │
└──────────────────────────────────────────────────────────┘
```

| Slot | Old (RFC-003) | New | Why changed |
|---|---|---|---|
| 1 | **Inbox** | **Status** | "Status" is the operator's mental model — they came to check status, not to read an inbox. The attention feed lives inside Status. Avoids the email-app metaphor that doesn't quite fit a fleet-monitoring product. |
| 2 | **Fleet** | **Devices** | "Fleet" sounds enterprise-y; "Devices" is plainer and matches every peer (Kasa, Shelly, HA all label it "Devices"). |
| 3 | **Releases** | **Rules** | This is the headline change. **The watchdog rule surface is more important to a power-control product than firmware releases.** Releases moves to a sub-tab of Settings (where firmware update is grouped with other system management). The Rules tab houses watchdog rules, schedules, and notification rules. |
| 4 | **Site** | **History** | A unified history / log surface (per R-LOG-1) is more frequently needed than a site picker. The site picker becomes a top-bar element (like a workspace picker) rather than a nav slot. |
| 5 | **Settings** | **Settings** | Unchanged. |

Resulting page set under each top-level destination:

- **Status** → dashboard with attention feed + health + emergency
  controls + last-events
- **Devices** → device list (with saved-filter chips) + device
  detail (with sub-tabs: Overview / Power / Watchdog / Schedule /
  Audit / Events / Settings)
- **Rules** → watchdog rules list + schedules list + notification
  rules list (sub-tabs); rule editor (plain-English + advanced)
- **History** → unified log feed (filterable per R-LOG-3); CSV /
  JSON export
- **Settings** → System / Network / Auth / Backup / API tokens /
  Webhooks / Integrations / Theme / Update & firmware

The **site picker** is a top-bar element — a small dropdown next
to the brand mark — that switches the active scope. A user with
one site never sees it.

## 4. Page list / routes

Mapped to URL routes. Existing v0.2.x URLs marked with **(stays
+ redirect)** continue to resolve until v1.0.0 ships, then redirect
to the new shape.

### 4.1 Public / unauth

| Route | Page | Status |
|---|---|---|
| `/app/login` | Login | Stays |
| `/app/invite/<token>` | Invite redemption | Stays |
| `/app/forgot-password` | Password reset entry | **New** (P5) |
| `/app/reset-password/<token>` | Password reset finish | **New** (P5) |

### 4.2 Status

| Route | Page | Status |
|---|---|---|
| `/app/` | Status / Dashboard | Replaces today's `/app/` stat-grid |
| `/app/status/acknowledged` | Acknowledged-attention archive | New |

### 4.3 Devices

| Route | Page | Status |
|---|---|---|
| `/app/devices` | Device list (with chips) | Stays + restyled |
| `/app/devices/<id>` | Device detail (tabbed) | Stays + restyled |
| `/app/devices/<id>/power` | Power tab | New (deep-link target) |
| `/app/devices/<id>/watchdog` | Watchdog tab on device | New |
| `/app/devices/<id>/schedule` | Schedule tab on device | New |
| `/app/devices/<id>/audit` | Audit tab (already in detail) | Stays |
| `/app/devices/<id>/events` | Events tab | Stays + tabbed |
| `/app/devices/<id>/settings` | Edit metadata + danger zone | Refactored |
| `/app/devices/new` | Enrolment wizard with QR | **New** |
| `/app/groups` | Groups list | Stays |
| `/app/groups/<id>` | Group detail | Stays |
| `/app/sites` | Sites list (super_admin) | Stays |
| `/app/sites/<id>` | Site detail (members, settings) | **New** (P5) |
| `/app/events` | **(redirected to `/app/devices?tab=events`)** | Deprecated |
| `/app/unregistered-devices` | **(redirected to `/app/devices?tab=unregistered`)** | Deprecated |

### 4.4 Rules

| Route | Page | Status |
|---|---|---|
| `/app/rules` | Watchdog rules list | **New** (P4) |
| `/app/rules/new` | Rule create (plain-English builder) | **New** (P4) |
| `/app/rules/<id>` | Rule detail + edit | **New** (P4) |
| `/app/rules/<id>/events` | Per-rule probe-event log | **New** (P4) |
| `/app/schedules` | Schedules list | **New** (P4) |
| `/app/schedules/new` | Schedule create | **New** (P4) |
| `/app/schedules/<id>` | Schedule detail | **New** (P4) |
| `/app/notifications` | Notification rules list | **New** (P6) |
| `/app/notifications/new` | Notification rule create | **New** (P6) |
| `/app/notifications/<id>` | Notification rule detail | **New** (P6) |

### 4.5 History

| Route | Page | Status |
|---|---|---|
| `/app/history` | Unified log feed | **New** (P6) replaces `/app/audit` |
| `/app/audit` | **(redirected to `/app/history?source=audit`)** | Deprecated |
| `/app/history/export` | CSV/JSON export | **New** (P6) |

### 4.6 Settings

| Route | Page | Status |
|---|---|---|
| `/app/settings` | Settings (default tab = System) | New entry |
| `/app/settings/system` | System settings | **New** (P6) |
| `/app/settings/network` | Network + CORS allowlist | **New** (P6) |
| `/app/settings/auth` | Authentication (MFA, OIDC) | **New** (P5) |
| `/app/settings/users` | Users + invitations (within site) | Replaces `/app/users` + `/app/invitations` (P5) |
| `/app/settings/users` (super_admin) | Cross-site super-admin user mgmt | **New** (P5) |
| `/app/settings/backup` | Backup / restore | **New** (P6) |
| `/app/settings/tokens` | API tokens | **New** (P6) |
| `/app/settings/webhooks` | Webhooks | **New** (P6) |
| `/app/settings/integrations` | MQTT, HA, etc. | **v2** |
| `/app/settings/theme` | Theme picker | **New** (P1) |
| `/app/settings/firmware` | Firmware releases + deployments + mirror chain | Replaces `/app/firmware` (P6) |
| `/app/me` | Profile (sessions list + revoke) | Stays + extended (P5) |
| `/app/users` | **(redirected to `/app/settings/users`)** | Deprecated |
| `/app/invitations` | **(redirected to `/app/settings/users?tab=invitations`)** | Deprecated |
| `/app/firmware` | **(redirected to `/app/settings/firmware`)** | Deprecated |
| `/app/enrollment-tokens` | **(merges into `/app/devices/new` wizard)** | Deprecated |

### 4.7 API surface

The admin API stays at `/api/v1/admin/*` for v1 of the redesign.
Any breaking change introduced by this redesign (e.g., new query
params, new resource types) is **additive** and does not break
existing consumers. If a future change must break, it ships on
`/api/v2/admin/*` per RFC-003 §6.2 with `/api/v1/admin/*` kept
alive for one minor.

New API resources (designed in §7):

```
/api/v1/admin/rules                       # watchdog rules CRUD
/api/v1/admin/rules/<id>/probe-now        # one-shot probe
/api/v1/admin/rules/<id>/simulate         # dry-run trigger
/api/v1/admin/rules/<id>/events           # per-rule events
/api/v1/admin/schedules                   # schedules CRUD
/api/v1/admin/notifications               # notification rules CRUD
/api/v1/admin/notifications/<id>/test     # send-a-test
/api/v1/admin/api-tokens                  # long-lived tokens
/api/v1/admin/webhooks                    # webhook config
/api/v1/admin/sites/<id>/members          # site membership CRUD
/api/v1/admin/system/backup               # config export
/api/v1/admin/system/restore              # config import
/api/v1/admin/history                     # unified log feed
```

## 5. Navigation model

### 5.1 Desktop (≥ 1024 px)

- Top bar: brand · site picker · five-item nav · user menu
- Side bar: none in v1 (avoid two-axis nav complexity; reserve
  for v2 power-user mode)
- Breadcrumbs: only on detail pages (Devices › Office Modem ›
  Power)
- Command palette: `Cmd / Ctrl-K` invokes a fuzzy-search overlay
  across devices, rules, schedules, settings (R-UX-14)

### 5.2 Mobile (≤ 640 px)

- Top bar: brand + site picker + user menu
- Bottom tab bar: five icons matching the desktop nav
- Detail pages: back arrow + title; tabs become horizontal
  scrolling chip strip
- No command palette (typing on mobile is a poor primary
  affordance)
- Pull-to-refresh on every list page

### 5.3 Tablet (640 px–1024 px)

- Treated as desktop layout with tighter padding. Bottom tab bar
  is suppressed at ≥ 768 px.

### 5.4 Active-scope behaviour

- Site picker is a workspace switcher. Switching sites:
  1. Updates the URL with `?site=<id>` (also persists in a cookie
     so direct visits respect the last-active site)
  2. Refreshes the data within the current page (HTMX swap; full
     reload as fallback)
  3. Audit-logs the switch (anti-confusion: the operator's actions
     are now scoped to a different site)

## 6. RBAC model

Designed end-to-end against R-RBAC-* in the requirements doc.

### 6.1 Database shape

```
users
├── id, email, password_hash, display_name, is_active
├── platform_role enum('super_admin','none')   -- replaces is_super_admin/is_admin
├── tokens_valid_after, mfa_enrolled_at, mfa_secret  -- mfa added P5
└── ... (existing columns preserved)

site_memberships  -- NEW (P5)
├── id (ULID prefix 'mem')
├── site_id (FK sites.id ON DELETE CASCADE)
├── user_id (FK users.id ON DELETE CASCADE)
├── role enum('admin','operator','viewer')
├── created_at, updated_at
└── UNIQUE (site_id, user_id)

sites
├── id, name, slug, description, owner_user_id  -- existing
├── timezone (IANA tz name; default UTC)        -- NEW (R-AUTO-9)
└── settings (JSON; per-site overrides)         -- NEW (R-SET-9)

devices
├── ... (existing columns)
├── site_id (now NOT NULL after migration)
├── is_protected bool default false             -- NEW (R-DEV-8)
└── tags JSON default []                        -- NEW (R-DEV-10)

oauth_identities  -- NEW (P5)
├── id, user_id, provider enum('google','github')
├── provider_subject (the OIDC `sub` claim)
├── created_at, last_used_at
└── UNIQUE (provider, provider_subject)

api_tokens  -- NEW (P6)
├── id (ULID prefix 'apit')
├── user_id (FK users.id)
├── name, prefix (first 8 chars of token, displayed)
├── token_hash (sha256)
├── site_scope JSON  -- ['*'] or [site_id, ...]
├── role enum('admin','operator','viewer')
├── expires_at (nullable)
├── last_used_at, created_at, revoked_at
```

### 6.2 Authorization middleware

- Today's `admin_required_ui` / `_api` decorators are joined by
  `site_required_ui(role='operator')` style decorators that
  consult the resolved `site_id` from the request scope.
- The scoping middleware applies to **every list query** at the
  service layer. Single-resource queries check `site_id` against
  the user's memberships in the resolver.
- Super-admin platform role bypasses scoping for cross-site ops
  (e.g., the super_admin /app/settings/users page).

### 6.3 Migration to site-as-scope

One-way schema migration, gated rollout per RFC-003 §9.2 +
REMEDIATION-PLAN R6:

1. Ship the new `site_memberships` + `oauth_identities` tables
   plus the `is_protected`, `tags`, and `timezone` columns (idempotent
   ADD COLUMN IF NOT EXISTS via the existing `_PENDING_COLUMNS`
   pattern).
2. Backfill: create one site `id=site_default, name='Default'`,
   assign every existing device + group to it, give every existing
   user `admin` membership in Default.
3. Make `devices.site_id` NOT NULL after backfill.
4. Ship the scoping middleware in **shadow mode**: it computes
   what the scope-filtered result would be, logs any divergence
   from the unscoped result, but returns the unscoped result.
5. Run shadow for ≥ 7 days. Zero divergences = ready.
6. Flip the enforce switch (`REBOOTER_SITE_SCOPING_ENFORCE=1`).
7. Remove the shadow-comparison code one minor later.

## 7. Data model — new tables

### 7.1 Watchdog rules

```
watchdog_rules
├── id (ULID 'wdr')
├── site_id (FK sites.id ON DELETE CASCADE)
├── name, description
├── enabled bool default true
├── status enum('armed','firing','cooled-down','suspended','disabled')
│         -- derived state, recomputed by the runtime; persisted for fast queries
├── probe (JSON) -- {kind: 'ping'|'tcp'|'http'|'dns'|'gateway'|'internet', ...args}
├── failure_threshold int default 3
├── recovery_threshold int default 2
├── window_seconds int default 60
├── cooldown_seconds int default 300
├── target (JSON) -- {kind: 'device'|'group'|'tag', id_or_tag: ...}
├── action (JSON) -- {kind: 'cycle'|'hold_off'|'notify_only', ...settings}
├── max_retries int default 3
├── retry_delay_seconds int default 60
├── escalation (JSON) -- {kind: 'stop'|'notify'|'hold_off'|'webhook', ...}
├── maintenance_windows (JSON array of cron-shape blocks)
├── created_by_user_id, created_at, updated_at
└── INDEX (site_id, enabled, status)

watchdog_probe_events
├── id BIGINT PK
├── rule_id (FK watchdog_rules.id ON DELETE CASCADE)
├── at TIMESTAMP
├── outcome enum('success','failure','threshold_crossed','action_fired',
│           'recovery','cooldown_skip','suspend_by_window','escalated')
├── details JSON
├── INDEX (rule_id, at desc)
```

### 7.2 Schedules

```
schedules
├── id (ULID 'sch')
├── site_id
├── name
├── enabled bool
├── cron_expr text  -- standard cron with TZ from sites.timezone
├── action (JSON) -- same shape as watchdog_rules.action plus
│                    'enable_rule'|'disable_rule' for rule-toggle schedules
├── target (JSON)
├── created_by_user_id, created_at, updated_at, last_fired_at, next_fire_at
└── INDEX (site_id, enabled, next_fire_at)
```

### 7.3 Notification rules

```
notification_rules
├── id (ULID 'nr')
├── site_id
├── name
├── enabled bool
├── condition (JSON) -- {kind, args}; can target watchdog events,
│                       device events, deployment events, etc.
├── severity enum('info','warn','critical')
├── channels (JSON array) -- [{kind: 'email'|'webhook'|'web_push', target, settings}]
├── quiet_hours (JSON; per-rule)
├── recovery_alert bool
├── escalation (JSON; optional escalate-to-critical-after-N-minutes)
├── created_by_user_id, created_at, updated_at
└── INDEX (site_id, enabled)

notification_send_log
├── id BIGINT PK
├── rule_id (FK)
├── at TIMESTAMP
├── recipient text, channel text, status enum('sent','failed','suppressed')
├── details JSON
└── INDEX (rule_id, at desc)
```

### 7.4 Webhooks (operator-configured outbound)

```
webhooks
├── id (ULID 'wh')
├── site_id
├── name, url
├── secret_hash  -- HMAC signing secret; never displayed after creation
├── enabled bool
├── created_by_user_id, created_at, last_used_at
```

### 7.5 Push tokens (for Web Push / future APNs / FCM)

```
push_tokens
├── id (ULID 'push')
├── user_id (FK)
├── platform enum('webpush','apns','fcm')
├── token text
├── label  -- "iPhone 15 (Safari)", etc.
├── created_at, last_seen_at, revoked_at
└── INDEX (user_id, revoked_at)
```

### 7.6 Existing models touched

| Model | Change | Phase |
|---|---|---|
| `devices` | + `is_protected`, `tags` | P3 |
| `devices` | `site_id` becomes NOT NULL | P5 |
| `sites` | + `timezone`, `settings` | P5 |
| `users` | + `platform_role`, `mfa_enrolled_at`, `mfa_secret` | P5 |
| `audit_events` | + `details.reason` convention enforced everywhere | P3 |
| `commands` | + `audit_id` link | P3 (small) |

## 8. Component structure & design system

### 8.1 Framework decision

**Recommendation: Server-rendered Jinja + HTMX + Alpine.js.**

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Pure Jinja (status quo) | Simplest deploy; matches team skills; SEO-immune; no build step | Hard to do partial-update interactivity (e.g., live filter chips) without page reload | Insufficient for the responsive UX target |
| **Jinja + HTMX + Alpine.js** | Keeps single-container deploy; partial swaps for filter chips, live updates, modal flows; small JS footprint (~25 KB combined); team already writes HTML | New idiom for the team; harder to share components across pages than a real component framework | **Recommended for v1** |
| React (Next.js) SPA | Best ecosystem; component reuse; real state mgmt | Two deploys (Flask API + Node SSR); build step; team is Python-first; SEO concerns minimal here but bundle size + dev complexity real | Overkill for v1; revisit if v2 requires it |
| Svelte / SvelteKit | Smaller bundle; closer to HTML; declarative | Same two-deploy + build-step + team-skill issues as React | Not v1 |
| Vue (Inertia) | Good middle ground; works with Flask via Inertia adapter | Inertia-Flask is a less mature adapter than Inertia-Laravel; risk | Not v1 |

The HTMX + Alpine choice is reversible: any single page can be
upgraded to a JS-island later without rewriting the others. This
preserves the option value the brief asks for.

### 8.2 CSS / design system

**Recommendation: Tailwind CSS (JIT) for utility classes + a
small set of project-defined component classes for repeating
shapes (button, card, badge, modal).**

- Tailwind JIT compiles a single small CSS file at build time; no
  CDN dependency.
- The current `static/css/app.css` (~600 LOC of hand-rolled CSS)
  is replaced; existing class names that the QA tests assert
  against (`.badge`, `.badge.green`, `.badge.red`, `.actions`,
  `.table-wrap`) are preserved as compatibility shims pointing at
  Tailwind utility composites.
- Dark mode via Tailwind's `dark:` variant + the user-scoped
  theme preference (R-SET-6).
- Design tokens (colours, spacing, font sizes) defined in
  `tailwind.config.js` per WCAG 2.2 AA contrast requirements.

### 8.3 Component inventory (new)

```
templates/
├── layout.html                # base; bottom-tab nav + top bar
├── _components/
│   ├── attention_card.html    # used on Status
│   ├── device_card.html       # mobile card; reused on Status + Devices
│   ├── filter_chip.html       # saved-filter chips
│   ├── confirm_modal.html     # base for the confirmation gate
│   ├── confirm_typed.html     # typed-confirmation variant
│   ├── empty_state.html       # standard empty state (R-UX-6)
│   ├── error_card.html        # error state with retry (R-UX-7)
│   ├── rule_sentence.html     # plain-English rule sentence with editable spans
│   ├── rule_form.html         # the rule editor (plain mode)
│   ├── rule_advanced.html     # advanced (JSON) editor
│   ├── command_palette.html   # Cmd-K
│   └── ... (~30 small partials)
├── status/                    # Status-tab pages
├── devices/                   # Devices-tab pages
├── rules/                     # Rules-tab pages
├── history/                   # History page
├── settings/                  # Settings sub-pages
└── ... (existing pages, refactored)
```

### 8.4 Frontend testing layer

Adding component tests **only after** the framework decision
beds in. Initial v1:

- Playwright for E2E across the responsive matrix
  (375 / 768 / 1024 / 1440)
- Pytest-flask for the Flask handlers that compose the templates
- A small dom-assertion helper around BeautifulSoup for inline
  template snapshot checks (extension of the v0.2.x QA pattern)

If the JS-island option is exercised in v2, jest / vitest joins
for component tests at that time.

## 9. Implementation phases

The brief's seven phases, mapped onto specific deliverables.

### Phase 1 — Design system + layout + navigation foundation

**Scope.**
- Tailwind setup; design tokens; theme picker (R-SET-6, R-UX-4).
- New base `layout.html` with the 5-item nav (Status / Devices /
  Rules / History / Settings); bottom tab bar on mobile.
- Site picker top-bar component (single-site users see it
  collapsed).
- Component library skeleton (`_components/` directory with empty
  state, error state, modal base, badge, button utility classes).
- Old URLs (audit, users, invitations, firmware,
  unregistered-devices, events) redirect to their new homes.
- Onboarding tour skeleton (3-step, dismissible) (R-UX-9).

**Out of scope.** No new feature surfaces yet. Existing pages
re-render under the new shell; they look better but behave the
same.

**Reversible?** Yes — feature flag `NEW_SHELL` gates the new
layout per user.

**Migration risk.** Low.

**Tests.**
- Playwright at all four breakpoints on every existing page; assert
  no horizontal overflow at 375 px.
- Snapshot test of the new nav.
- Old-URL redirects all yield 302 to the new URL with the same
  data context.

**Cuts over.** When the operator survey passes a single full week
with the flag flipped on for one fleet.

### Phase 2 — Dashboard + device list + device detail

**Scope.**
- Status page: attention feed, health verdict, last-events,
  emergency-controls card, all-clear empty state (R-DSH-*).
- Device list: card layout on mobile, table on desktop,
  saved-filter chips with URL round-trip (R-DEV-3, R-DEV-4,
  R-DEV-5).
- Device detail: tab bar (Overview / Power / Watchdog / Schedule /
  Audit / Events / Settings).
- Device card: inline switch + badge cluster; QA badge from
  v0.2.8 preserved; central-vs-local cue (R-DEV-2).
- Device-add wizard (`/app/devices/new`) with QR-code +
  enrolment-token flow (R-DEV-6).
- "Open device's local UI" link on device detail (R-DEV-11).

**Out of scope.** Watchdog, schedule, notification surfaces
exist as stubs ("Coming in P4 — no rules yet") on the device
detail page.

**Reversible?** Yes — same `NEW_SHELL` flag covers it.

**Tests.**
- Playwright filter-chip round-trip (URL ↔ DOM ↔ data).
- Snapshot of mobile device card.
- Empty-state assertions on every list page.

### Phase 3 — Power controls + safety confirmations

**Scope.**
- `is_protected` flag + lockout UI (R-DEV-8).
- Hold-off action (R-CTRL-3).
- Cancel-pending-action (R-CTRL-8).
- All confirmation modals tuned per R-CTRL-4 + R-UX-12.
- Reason field on every audit row (R-CTRL-6).
- Per-device event log shows reason annotation (R-CTRL-7).
- Mass-action gate's visual scariness scales with target count
  (R-CTRL-9 visual tune).
- Test mode for cycle (R-CTRL-10).

**Backend changes.** `devices.is_protected` column;
`audit_events.details.reason` field convention; new
`/api/v1/admin/devices/<id>/cancel-pending` endpoint.

**Tests.**
- Lock-flag prevents cycle through both UI and API; override
  flow works.
- Audit row carries `reason` for every power action.
- Mass-action gate at 30 targets requires typed confirmation.

### Phase 4 — Watchdog rule builder + schedules

**Scope.**
- New `watchdog_rules`, `watchdog_probe_events`, `schedules`
  tables (§7.1, §7.2).
- Probe runtime: a new APScheduler job per active rule that
  executes the probe at a cadence derived from `window_seconds`
  and writes events to `watchdog_probe_events`.
- Rule-builder UI: plain-English mode (R-WD-1) with editable
  spans; advanced mode (JSON) toggle (R-WD-10).
- Per-rule event log + last-trigger card (R-WD-7, R-WD-8).
- Maintenance windows (R-WD-6).
- Probe-now + simulate-trigger actions (R-WD-11).
- Schedules UI (R-AUTO-1 through R-AUTO-9).
- Maintenance-mode portal-wide toggle (R-AUTO-5).

**Out of scope.** Notification routing to a triggered rule still
goes to a webhook or the admin-only inbox; full notification
rules ship in P6.

**Reversible?** Database tables are additive; rule-builder UI
ships behind a feature flag.

**Tests.**
- Probe-now returns the live probe state.
- Simulate-trigger writes a `simulated=true` event but does not
  fire the action.
- A rule's plain-English sentence round-trips through the JSON
  representation.

### Phase 5 — Users / RBAC / audit logs (sites, OIDC, MFA)

**Scope.**
- Site-as-scope migration (§6.3): new tables, backfill, scoping
  middleware in shadow mode for ≥ 7 days, then enforce flip.
- Per-site invite (`(site_id, role)` tuples on the invite payload).
- Server-side session enforcement flips on (closes BUG-005).
- TOTP enrolment (super_admin opt-in first; widening covered by
  RFC-003 redline #3).
- Google + GitHub OIDC sign-in (RFC-003 redline #2 picks the
  list).
- Password reset flow (magic-link primitive).
- Profile page extends with active-sessions list + revoke.

**Backend changes.** All of §6.1 (oauth_identities,
site_memberships); user_sessions enforcement; the password-reset
+ MFA service modules.

**Migration risk.** Highest of all phases. Dual-read shadow window
+ data-parity assertion + per-step abort criteria as in
REMEDIATION-PLAN §4 R6.

**Tests.**
- Migration-rehearsal suite against a production snapshot.
- Auth-state matrix across (cookie, JWT, OIDC, magic-link, TOTP).
- A super-admin's view post-migration is byte-equivalent to
  pre-migration.

### Phase 6 — History / notifications / settings

**Scope.**
- Unified `/app/history` view consolidating audit + watchdog
  events + power events + schedule fires + notification sends.
- CSV / JSON export (R-LOG-5).
- Free-text search across details JSON (R-LOG-8).
- Notification rules surface (`/app/notifications`) with
  email + webhook + Web Push channels (R-NOTIF-*).
- Notification-send log.
- Settings sub-pages (System / Network / Backup / API tokens /
  Webhooks / Theme / Firmware-and-mirrors).
- API token issuance UX (R-SET-4).
- Webhook config UX (R-SET-5).
- Backup / restore flow with dry-run diff (R-SET-2, R-SET-3).
- RFC-002 firmware-mirror chain configuration surface
  (R-SET-7).

**Out of scope.** MQTT, Home Assistant native integration —
deferred to v2.

**Tests.**
- Backup → restore round-trips a full config.
- API token mints once, never displays again.
- Webhook test-send works before the webhook is armed.

### Phase 7 — Polish / accessibility / responsive testing / docs

**Scope.**
- WCAG 2.2 AA pass with axe-core or pa11y in CI.
- Responsive Lighthouse run ≥ 90 on Status + Devices.
- Command palette (Cmd-K) (R-UX-14).
- Onboarding-tour copy refinement.
- Auto-generated OpenAPI 3 spec at `/api/docs` (R-OSS-10).
- README + dev-docs Pinch (`make dev`, `make test`, `make lint`,
  `make build`) (R-OSS-11).
- v2 backlog scrub.

## 10. Testing strategy

### 10.1 Test buckets

| Bucket | Today | After redesign |
|---|---|---|
| Live-deployment QA | `tests/qa/` (everything) | `tests/qa/` (smoke + key flows) |
| In-process unit | none | `tests/unit/` (P1 introduces) |
| Component | none | `tests/component/` (P1 introduces; small, snapshot-based on Jinja partials) |
| E2E | `tests/qa/test_responsive.py` (Playwright) | `tests/e2e/` (Playwright; expanded to all key flows + responsive matrix) |

### 10.2 Mocked device states

Per the brief: every test that exercises the device surface MUST
be parameterised over the following mock-device states:

- `online`
- `offline`
- `rebooting`
- `pending power-on` (held-off → about to come back)
- `watchdog triggered` (action queued or in-flight)
- `locked / protected` (R-DEV-8)
- `permission denied` (viewer trying to cycle)

A `tests/_fixtures/devices.py` module ships in P2 to construct
these states without requiring round-trip enrolment.

### 10.3 Rate-limit fix

The `tests/qa/conftest.py` `admin_token` fixture is converted
to **session-scoped** so the suite logs in once. This eliminates
the rate-limit fallout described in `webui-redesign-research.md`
§8.

### 10.4 CI integration

- `make test` runs unit + component + Playwright in headless
  mode against a docker-compose-managed local stack.
- `make qa` runs the live-deployment bucket against the configured
  `REBOOTER_QA_BASE` (production OR a staging URL).
- Github Actions matrix runs `make test` per PR;
  `make qa` runs after a release tag.

## 11. Migration / backward compatibility

### 11.1 API

- `/api/v1/admin/*` stays. New resources are additive.
- Any breaking change introduced after sign-off ships on
  `/api/v2/admin/*` with `/api/v1/admin/*` kept alive for one
  minor.
- `/api/v1/device/*` is **frozen** for the in-the-field firmware
  per RFC-003 §6.2.

### 11.2 URLs

- All old `/app/*` URLs redirect to their new homes through v1.x.
  Removed in v1.0.0 + N (one minor later).
- Bookmarks / external links continue to work.

### 11.3 Database

Two one-way schema migrations:

- **P5 (site-as-scope):** dual-read shadow ≥ 7 days, then flip.
- **P5 (auth foundation):** server-side session enforcement is a
  separate flip on the same `user_sessions` table that already
  exists in shadow mode (v0.2.10).

Plus several additive table / column adds (P3, P4, P6) that use
the existing idempotent `_PENDING_COLUMNS` + `metadata.create_all()`
pattern. No manual operator migration step.

### 11.4 Operator-visible breaking changes

- `/app/audit` URL deprecates (redirected for one minor; removed in
  v1.1.x).
- `/app/users` deprecates (likewise).
- `/app/firmware` deprecates (likewise).
- `/app/events` deprecates (likewise).
- The **role names** stay the same; the **permission model**
  changes from action-only to action + data-scope. Every user's
  effective permissions on the Default site are equal to or
  greater than what they had pre-migration. **No user loses
  access** in the migration (R-RBAC-10 acceptance).

### 11.5 Operator-visible config changes

New env vars introduced; all default-empty / default-disabled so
existing deployments are unchanged on upgrade:

```
REBOOTER_SITE_SCOPING_ENFORCE          # P5 flip; default off
REBOOTER_SESSIONS_ENFORCE              # P5 flip; default off (already supported)
REBOOTER_OIDC_GOOGLE_CLIENT_ID         # P5
REBOOTER_OIDC_GOOGLE_CLIENT_SECRET     # P5
REBOOTER_OIDC_GITHUB_CLIENT_ID         # P5
REBOOTER_OIDC_GITHUB_CLIENT_SECRET     # P5
REBOOTER_MFA_REQUIRED_ROLES            # P5; comma-separated (e.g. "super_admin,admin")
REBOOTER_PUSH_VAPID_PUBLIC             # P6
REBOOTER_PUSH_VAPID_PRIVATE            # P6
REBOOTER_FIRMWARE_GH_RELEASE_TOKEN     # RFC-002 mirror; P6
```

The existing `REBOOTER_CORS_ALLOWED_ORIGINS` (v0.2.11) is
preserved.

## 12. Risks and open questions

### Risks (ranked)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Rule-builder UX is novel and may need iteration** | High | Medium | Ship plain-English mode + advanced JSON mode in P4; user-test with three operator personas before P5; iterate on the JSON shape (which is harder to change later) |
| **Site-scope migration data divergence** | Medium | High | Dual-read shadow ≥ 7 days; data-parity assertion; per-step abort criteria (REMEDIATION-PLAN §4 R6) |
| **HTMX learning curve costs more than expected** | Medium | Medium | Spike a representative non-trivial page (the Inbox) in P1 before committing the rest; if HTMX is wrong, abort to pure Jinja for P1 + P2 and revisit framework at P4 |
| **Watchdog probe runtime overload at scale** | Low | Medium | APScheduler one-job-per-rule limits the runtime to active rule count; document a soft cap of 200 active rules per portal in v1; revisit at the first user with > 100 |
| **Mandatory-MFA flip locks operators out** | Low | High | Warn-but-not-enforce window per role tier; per-tier rollout (super_admin → admin → operator) |
| **TLS / OIDC redirect-URI misconfig in self-hosted deployments** | Medium | Medium | Settings UI does a live OIDC discovery probe and shows a green/red status before the operator commits the config |
| **Backup/restore reintroduces removed records** | Low | Medium | Backup excludes audit + heartbeats by design (state vs config); restore offers a dry-run diff |
| **The "Rules" tab name confuses operators expecting "Automations"** | Medium | Low | A/B test the tab label in P1 cohort; switch if test data warrants |

### Open questions for product redline

These are the residual questions that this plan does not answer
and that need a product call before P-letter execution begins.
They map onto the existing RFC-003 redlines plus the new ones
this plan introduces.

1. **Rule statement copy.** Is the plain-English template in
   R-WD-1 the right shape, or do we want a different sentence
   form?
2. **Schedule + watchdog merge.** Should schedules and watchdog
   rules share a single `automations` table with a
   `trigger_type` discriminator, or stay split as designed in §7?
   (Plan default: split for v1, merge in v2 if patterns warrant.)
3. **Inbox vs Status tab name.** §3 swapped Inbox → Status. Does
   product agree?
4. **Releases tab demoted.** §3 moves firmware management under
   Settings. Does product agree, given how often releases are
   touched?
5. **OAuth provider list in v1.** Google + GitHub only, or
   include Microsoft? (carried from RFC-003 redline #2)
6. **MFA mandatory-flip timing.** Which version makes super_admin
   TOTP required, then admin, then operator? (carried from
   RFC-003 redline #3)
7. **Mobile distribution model.** PWA (preferred per this plan),
   native iOS+Android, or webview wrapper? Decides whether APNs
   cert work is needed at all in P6. (carried from RFC-003
   redline #4)
8. **Custom roles.** R-RBAC-5 defers to v2. Does product confirm?
9. **Default theme.** Light / dark / system? (Plan default:
   system.)
10. **Backup contents.** §11.3 + R-SET-2 exclude audit + heartbeats.
    Does product agree, or should backup be a *full* archive?
11. **Test framework upgrade.** Are we OK adopting Playwright +
    pytest-only (no jest/vitest) for v1 of the redesign?
12. **Site-as-scope timing.** Does product approve the dual-read
    shadow ≥ 7 days before flipping enforce? Faster (e.g., 48 h)
    is possible if product accepts the higher risk.

### Decisions this plan locks (reversible only at significant cost)

- Server-side rendering with HTMX + Alpine (§8.1). Reversible only
  by rewriting the framework layer.
- Tailwind for CSS (§8.2). Reversible by replacing the stylesheet
  layer.
- Sites as the data-scope unit (§6). Reversible only with a
  destructive migration once data is committed to the model.
- Watchdog rule data shape (§7.1). Reversible via a v2 schema
  migration; easier than the site model but still meaningful.

## 13. Final recommendation

Proceed in order:

1. **Product redlines.** Sit with this plan + the requirements
   doc; redline the 12 open questions in §12. *Do not start P1
   until the answers are recorded in this file as a sign-off
   amendment.*
2. **P1 (foundation)** ships first, behind a feature flag, on a
   single fleet; the foundation work itself is reversible and
   low risk.
3. **P2 + P3** ship together one or two minors after P1 — they
   compose into the operator-visible "the device experience is
   different now" milestone.
4. **P4 (watchdog)** is the first phase that ships *new
   functionality*, and is the hardest UX problem in the plan.
   Plan for two iterations after a user-test cohort.
5. **P5 (RBAC + auth)** ships next, on a longer fuse with the
   shadow-window discipline.
6. **P6 (history / notifications / settings)** is largely
   compositional once P4 + P5 land.
7. **P7 (polish)** wraps the v1.0.0 redesign release.

Estimated calendar: P1 ≈ 2 weeks, P2 ≈ 2 weeks, P3 ≈ 1 week,
P4 ≈ 3 weeks (two iterations), P5 ≈ 4 weeks (shadow window
included), P6 ≈ 3 weeks, P7 ≈ 1 week. Total: ≈ 16 weeks of
focused engineering, executable in parallel where the dependency
graph permits (specifically P3 || P4 starts can overlap, and
P5 shadow-window runs alongside P6 dev work).

This plan supersedes the prior `REMEDIATION-PLAN-2026-05-WEB-UI.md`
phase definitions for any phase not yet executed (R4 onwards).
The prior plan's R1–R3, R7-shadow, and R8-CORS remain shipped
ground truth and do not need re-execution.
