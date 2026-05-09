# WebUI Redesign — Requirements

| Field | Value |
|---|---|
| Status | **Draft** (requirements-only deliverable; no implementation) |
| Authors | rebooter-droids design + product/architect track |
| Companion docs | `webui-redesign-research.md`, `webui-redesign-plan.md` |
| Depends on | research findings in `webui-redesign-research.md` §9 (product positioning sentence) |

> This document is the *requirements* half of a redesign trio. It
> states **what the product must do** in measurable terms,
> organised around the ten product domains in the brief plus
> cross-cutting UX/design and acceptance criteria. It does not
> specify *how*; that is the plan doc.

Conventions:

- **MUST** = required for v1 of the redesign.
- **SHOULD** = required for v1 unless explicitly deferred at sign-off.
- **MAY** = nice-to-have; add to backlog.
- Each requirement has a stable ID (`R-<domain>-<n>`) so we can
  cross-reference from the plan and tests.

---

## 0. Product positioning and non-goals

### 0.1 Positioning sentence

Per `webui-redesign-research.md` §9:

> *A consumer-friendly, mobile-first, open-source web portal for a
> fleet of local-first power-control devices, with watchdog rules
> powerful enough to satisfy AV-installer use cases and RBAC
> mature enough to serve multi-tenant deployments — built around
> the principle that the device works without the portal, and the
> portal works without the cloud.*

### 0.2 Constitutional invariants

These are not requirements; they are the laws every requirement
inherits. Any later requirement that conflicts with these is wrong.

- **C1.** A device MUST remain useful when the central portal is
  unreachable. Local web UI on the device is the source of truth
  for that device's *current* state.
- **C2.** The central portal MUST function entirely on a self-
  hosted deployment with no third-party cloud dependency.
- **C3.** Every dangerous action (power-off, power-cycle, mass
  fan-out, role change, device delete, rule delete) MUST require
  explicit confirmation proportional to its blast radius.
- **C4.** The portal MUST stay usable on a 375 px-wide phone screen
  for the most-common operator tasks (triage, manual cycle,
  acknowledge alert).
- **C5.** API stability: the existing `/api/v1/device/*` contract
  is frozen for the in-the-field firmware. Any breaking admin-API
  change must version-pin to `/api/v2/admin/*` with a one-minor
  parallel-run.
- **C6.** Open-source DIY-friendliness: the entire portal must be
  inspectable, modifiable, and self-hostable; configuration must
  be exportable as plain text; rules must round-trip between the
  visual editor and a human-readable text representation.

### 0.3 Non-goals

These are explicitly **out of scope** for the redesign:

- **N1.** Native mobile app (iOS/Android binary). The redesign
  ships a responsive PWA-shaped web UI that satisfies mobile use
  cases. A native app may follow but is not part of this work.
- **N2.** Onboarding for retail consumers buying a single Sonoff
  S31 off the shelf. The product is for hobbyists / sysadmins /
  AV installers / small-fleet operators. Mass-market consumer
  onboarding is a separate product question.
- **N3.** Voice-assistant integration (Alexa, Google Home).
- **N4.** Energy-billing or per-outlet kWh accounting (S31 supports
  the hardware metric; surfacing it in the dashboard is a v2
  feature, not v1).
- **N5.** Replacing the device-side firmware. This redesign is the
  *portal*; firmware lives in the sibling repo
  `dblagbro/rebooter-firmware`.
- **N6.** SAML SSO (deferred until a paying enterprise customer
  exists; see RFC-003 §10).

---

## 1. Dashboard requirements

### Goal

When an operator opens the portal, they must immediately know
**whether anything needs attention** and, if it does, **why and
where**. If nothing is wrong, the dashboard must say so plainly.

### Requirements

- **R-DSH-1 (MUST).** The default landing page on login is the
  Inbox / Dashboard view (renamed from today's stat-grid).
- **R-DSH-2 (MUST).** The dashboard MUST display a single-glance
  health verdict in one of four states with distinct visual
  treatment:
  - `all-clear` — every device online, every watchdog healthy, no
    pending alerts
  - `attention` — at least one watchdog rule has triggered or one
    device is offline > N minutes
  - `degraded` — multiple devices unreachable or central
    transport failing
  - `unknown` — telemetry stale (e.g., portal can't reach its
    own database)
- **R-DSH-3 (MUST).** The dashboard MUST show an **attention feed**
  ranked by recency × severity, including:
  - device just went offline (within last N minutes)
  - device offline > 24 h
  - watchdog rule triggered (with the device + rule named)
  - watchdog rule has triggered ≥ N times in the last hour
    (escalation candidate)
  - device booted with a new firmware version
  - deployment stuck > 30 min
  - newly enrolled device with no first heartbeat
- **R-DSH-4 (MUST).** Each attention item MUST be acknowledgeable;
  acknowledged items move to a separate "Acknowledged" tab and do
  not surface again unless the underlying condition recurs.
- **R-DSH-5 (MUST).** The dashboard MUST display an **internet
  health summary** card showing the portal's own outbound
  connectivity (separate from any individual device's watchdog
  state).
- **R-DSH-6 (MUST).** The dashboard MUST display a **last reboot
  events** card showing the last 5 power-cycle events across the
  fleet, with device + reason (operator / schedule / watchdog) +
  timestamp.
- **R-DSH-7 (MUST).** The dashboard MUST display an **active
  watchdog rules** card showing the count of currently-armed rules
  with a quick link into the rule list.
- **R-DSH-8 (MUST).** The dashboard MUST surface a **manual
  emergency controls** affordance — a clearly-marked "All off /
  All on / Cycle group X" surface gated behind the mass-action
  confirmation gate (R-CTRL-9).
- **R-DSH-9 (MUST).** When `all-clear`, the dashboard MUST display
  a confident, plain-language statement (e.g. *"All 12 devices
  online · all 4 watchdogs healthy · last incident 3 days ago"*)
  rather than empty cards.
- **R-DSH-10 (SHOULD).** The dashboard SHOULD support a per-user
  dismissed-banner state so onboarding tooltips do not re-appear.
- **R-DSH-11 (MAY).** Dashboard cards may be reorderable per-user
  (deferred; v2).

### Acceptance

- Operator with one offline device and one triggered rule sees
  both in the attention feed within 5 s of page load on a fresh
  session.
- Operator with no incidents sees the all-clear statement and no
  empty stat cards.

---

## 2. Devices requirements

### Goal

Operators must be able to see, find, configure, and act on devices
quickly, regardless of whether the device is centrally enrolled or
local-only.

### Requirements

- **R-DEV-1 (MUST).** The Devices page MUST list every device,
  scoped to the current site (when site-as-scope ships) or the
  whole fleet (today's flat behaviour, until R-RBAC-3 lands).
- **R-DEV-2 (MUST).** Each device row MUST display:
  - display name (linked to detail)
  - registration state (active / pending / disabled / revoked)
  - heartbeat state (online / offline / never) with R-DEV-9 colour
  - firmware version
  - last heartbeat timestamp
  - local IP
  - **central state** (centrally-enrolled vs local-only) as a
    distinct visual cue — operators must NOT be misled into
    thinking a healthy local-only device is broken
- **R-DEV-3 (MUST).** Devices SHOULD also surface in **card layout
  on mobile** (≤ 640 px), with the primary on/off/cycle action
  reachable without horizontal scroll.
- **R-DEV-4 (MUST).** The Devices page MUST support saved-filter
  chips above the list:
  - "Offline > 24 h"
  - "Never heartbeated"
  - "On firmware < latest stable"
  - "Has pending commands"
  - "Watchdog triggered in last 24 h"
  - "QA fixtures only"
- **R-DEV-5 (MUST).** Filter state MUST round-trip via URL query
  string so a saved view is shareable.
- **R-DEV-6 (MUST).** The device-add flow MUST mint an enrollment
  token, display the device-side enrol command + a QR code that
  encodes (token, central URL) for one-tap entry into the device's
  local web UI.
- **R-DEV-7 (MUST).** The device-detail page MUST present the
  following sections, each as a discrete tab on mobile and as a
  collapsible section on desktop:
  - **Overview** — name, model, firmware, location, current power
    state, last seen, signal/connectivity quality if available
  - **Power** — manual on/off/cycle controls (R-CTRL-*)
  - **Watchdog** — active rules targeting this device + per-rule
    last-trigger-reason
  - **Schedule** — recurring schedules targeting this device
  - **Audit** — per-record audit slice (already shipped v0.2.9)
  - **Events** — device-event timeline (heartbeats, errors)
  - **Settings** — name, location/site, notes, lockout/protection
    flag, central management toggle, danger-zone delete
- **R-DEV-8 (MUST).** Each device MUST have a **lockout flag**
  (`is_protected: bool`). When set, R-CTRL-1 / R-CTRL-2 / R-CTRL-3
  actions are blocked at the API and visually disabled in the UI;
  the lockout MUST be acknowledgeable in the same flow that issued
  the action so power users can override per-action.
- **R-DEV-9 (MUST).** Heartbeat state colours: `online` = green
  badge, `offline` = red badge, `never` = neutral badge with
  `never heartbeated` text (already shipped v0.2.7).
- **R-DEV-10 (MUST).** Device grouping MUST be supported at three
  levels:
  - **Site** (the data-scoping unit; see R-RBAC-3)
  - **Group** (the fan-out unit; the existing groups model)
  - **Tag** (free-form, multi-tag, used for ad-hoc filtering)
- **R-DEV-11 (SHOULD).** A device's local web UI MUST be reachable
  by one click from the device-detail page (link to
  `http://<local_ip>/`) when the local IP is known.
- **R-DEV-12 (SHOULD).** The device-detail page SHOULD show a
  signal-strength indicator if the heartbeat carries one.
- **R-DEV-13 (MAY).** Device cards on mobile MAY support a
  swipe-right "cycle" gesture (deferred; v2).

### Acceptance

- An operator can find a specific device by name, MAC, or filter
  chip in ≤ 3 actions on mobile and ≤ 2 actions on desktop.
- A locked device renders with a visible lock badge and the cycle
  button is disabled with a tooltip explaining why.
- A local-only device displays as healthy when its local UI says
  it is healthy, even if it has never registered with central.

---

## 3. Power controls requirements

### Goal

Power-off, power-on, and power-cycle actions are the most
operationally consequential things the portal does. They must be
unmissable, undoable where possible, and never accidental.

### Requirements

- **R-CTRL-1 (MUST).** Manual on / off / cycle MUST be available
  on the device detail page and on the device list (mobile card
  primary action).
- **R-CTRL-2 (MUST).** "Cycle" MUST accept a `power_off_seconds`
  parameter (default 5, min 1, max 60) and a `post_reboot_holdoff_seconds`
  parameter (default 180, min 0, max 3600) — the existing v0.2.x
  contract.
- **R-CTRL-3 (MUST).** "Hold power off until manually restored"
  MUST be available as a separate action distinct from a normal
  cycle. Behaviour: the device powers off and stays off until an
  operator explicitly issues a power-on. The UI MUST display the
  device as `held-off` while in this state.
- **R-CTRL-4 (MUST).** Every power-off / cycle / held-off action
  MUST require confirmation:
  - single-device default cycle: simple click-to-confirm
  - single-device hold-off (R-CTRL-3): typed-confirmation gate
  - mass action ≤ 5 targets: one click + simple confirm
  - mass action 5–20 targets: one click + simple confirm with
    target count visible
  - mass action > 20 targets: typed confirmation matching the
    group/site name
- **R-CTRL-5 (MUST).** Confirmation modals MUST display:
  - the action name
  - the exact target count and (for ≤ 5 targets) the device
    display names
  - the duration / hold-off implications in plain language
  - whether any target is locked (R-DEV-8) and how the lockout
    will be handled
- **R-CTRL-6 (MUST).** Every power action MUST emit an audit row
  with `target_type=device`, `target_id`, `action` (one of
  `device.power_on`, `device.power_off`, `device.cycle`,
  `device.hold_off`), and `details.reason` ∈ {`operator`,
  `schedule`, `watchdog`}.
- **R-CTRL-7 (MUST).** Per-device event log MUST include every
  power action with the same `reason` field — operators must be
  able to look at a device and see *who or what* power-cycled it.
- **R-CTRL-8 (MUST).** A "Cancel pending action" affordance MUST
  exist on the device-detail page for any command that is queued
  but not yet executed.
- **R-CTRL-9 (MUST).** Bulk actions (across a group or filter set)
  MUST go through the mass-action confirmation gate (already
  shipped v0.2.5) with the redesign making the *visual scariness*
  scale with the target count.
- **R-CTRL-10 (SHOULD).** A "Test" mode on cycle SHOULD allow the
  operator to preview the audit row and confirmation flow without
  actually issuing the command.
- **R-CTRL-11 (SHOULD).** Power-cycle SHOULD optionally accept a
  `note` field that is stored in the audit `details` and surfaced
  on the device timeline.

### Acceptance

- A first-time user cannot accidentally cycle a device by tapping
  twice — every dangerous action requires at least one explicit
  confirmation.
- Cycling 30 devices at once requires the operator to type the
  group name, matching today's typed-confirmation gate.

---

## 4. Watchdog rule requirements

### Goal

The watchdog feature is rebooter's headline differentiator. It must
be **easy enough that a non-technical user can build a rule that
reboots their modem when the internet drops** and **powerful enough
that an AV installer can express a multi-step recovery procedure**.

This domain has no existing implementation in either the portal or
the firmware (the device firmware has separate local watchdog
modes; this is the *centrally-managed* version).

### Requirements

- **R-WD-1 (MUST).** A watchdog rule MUST be expressible as **one
  human sentence** in the form:
  > *"If `<probe>` fails `<failure-threshold>` consecutive times
  > over `<window>`, cycle `<target-device-or-group>`, wait
  > `<recovery-delay>` and check `<recovery-threshold>` consecutive
  > successes before re-arming. Retry up to `<max-retries>` times
  > before `<escalation>`. Quiet hours: `<window>`."*
  > Each underlined span is editable inline.
- **R-WD-2 (MUST).** Supported probe types in v1:
  - `internet` — an outbound connectivity check from the portal
  - `ping` host or IP
  - `tcp` host:port reachable
  - `http` URL returns 2xx within timeout
  - `dns` resolve a hostname via a specific resolver
  - `gateway` — ping the device's known LAN gateway
  - `custom` — escape hatch via a webhook callback (post-v1)
- **R-WD-3 (MUST).** Each rule MUST have a `failure_threshold`
  (default 3) and `recovery_threshold` (default 2) over a
  configurable `window_seconds` (default 60) and `cooldown_seconds`
  (default 300) preventing thrash.
- **R-WD-4 (MUST).** Each rule MUST have a `target` that is one
  of: a single device, a group, or a tagged set. Mass fan-out
  through a watchdog rule MUST go through the mass-action gate
  (R-CTRL-9) at *rule-creation* time — once armed, the rule fires
  without per-event confirmation (that is the point).
- **R-WD-5 (MUST).** Each rule MUST have a `cycle_settings`
  block: `power_off_seconds`, `post_reboot_holdoff_seconds`,
  `max_retries` (default 3 with `retry_delay_seconds`), and an
  `escalation` action (one of: stop / notify / hold-off /
  webhook).
- **R-WD-6 (MUST).** Each rule MUST support **maintenance windows**
  — time ranges during which the rule does not fire (e.g.,
  scheduled router reboots overnight).
- **R-WD-7 (MUST).** Each rule MUST display its **last trigger**
  with timestamp + probe state at trigger time + recovery
  outcome.
- **R-WD-8 (MUST).** A per-rule event log MUST exist with every
  probe outcome (success, failure, threshold-crossed, action-
  fired, recovery, cooldown-skip, suspend-by-window).
- **R-WD-9 (MUST).** The rule list MUST display rules grouped
  by status: `armed` / `firing` / `cooled-down` / `suspended` /
  `disabled`.
- **R-WD-10 (MUST).** A **Plain-English builder** is the default
  rule editor; an **Advanced editor** (YAML or JSON) is
  available behind a toggle for power users — round-trip.
- **R-WD-11 (MUST).** A rule MUST be testable: a "Probe now"
  button that runs the probe once without affecting state, and
  a "Simulate trigger" button that fires the action against a
  dry-run-only target.
- **R-WD-12 (MUST).** A rule MUST be `enabled: bool` toggleable
  without deletion. Disabling preserves history and audit.
- **R-WD-13 (SHOULD).** Rules SHOULD be importable / exportable
  as JSON (R-WD-10's advanced view) so an operator can share a
  template.
- **R-WD-14 (SHOULD).** A rule SHOULD be linkable to a notification
  rule (Section 8) so a triggered probe can also notify even when
  the action is `notify-only`.
- **R-WD-15 (MAY).** Visual rule-builder showing the trigger /
  condition / action graph (HA-automation-style) as a v2 view.

### Acceptance

- A new user can create a working "reboot the modem when internet
  drops" rule in ≤ 5 fields and ≤ 60 s.
- An advanced user can express a 3-retry-with-escalation rule via
  the advanced editor in JSON and round-trip it back to the
  plain-English view.
- The rule fires reliably under simulated probe failures in test;
  the cooldown prevents loops.

---

## 5. Automations and schedules requirements

### Goal

A separate primitive from watchdog rules: **time-based** or
**condition-based** actions that aren't reactive incident
recovery. Examples: nightly router reboot at 3 AM, away-mode
power-down, vacation cycle.

### Requirements

- **R-AUTO-1 (MUST).** Time-based schedules MUST support recurring
  cron-shape (daily / weekly / monthly) targeting devices, groups,
  or tags.
- **R-AUTO-2 (MUST).** Each schedule MUST be one of: `power_on`,
  `power_off`, `cycle`, `hold_off_until_disarmed`, `disable_rule`,
  `enable_rule` (yes — schedules can disable watchdog rules during
  a maintenance window).
- **R-AUTO-3 (MUST).** Sunrise / sunset is **optional v2** and
  explicitly deferred. v1 ships clock-time only.
- **R-AUTO-4 (MUST).** Conditional automations (trigger-condition-
  action) MAY be merged with the watchdog rule shape — they share
  the trigger + action machinery. Final shape is a plan-doc
  decision.
- **R-AUTO-5 (MUST).** Maintenance mode MUST be a portal-wide
  toggle (per site) that suspends all watchdog rules and all
  schedules. Maintenance mode MUST emit an audit row when entered
  and exited.
- **R-AUTO-6 (MUST).** Vacation / away mode is a **named preset
  for maintenance mode** with optional pre-configured power-down
  of a "non-essential" device tag.
- **R-AUTO-7 (MUST).** Recurring power cycles MUST be expressible
  as a one-line schedule (e.g., *"Every Sunday at 03:00 UTC,
  cycle Office Routers"*).
- **R-AUTO-8 (SHOULD).** A schedule SHOULD respect maintenance
  windows configured on the watchdog rule shape (R-WD-6) — i.e.,
  schedules and rules share the same window primitive.
- **R-AUTO-9 (SHOULD).** Schedules SHOULD honour timezone-per-site
  rather than a single portal timezone.

### Acceptance

- An operator can create a "every night at 3 AM, cycle the office
  modem" schedule in ≤ 4 fields.
- Toggling maintenance mode immediately suspends all currently-armed
  rules and all queued schedules; toggling it off resumes them.

---

## 6. Users and RBAC requirements

### Goal

Multi-user is a first-class feature. Permission decisions must be
**data-gated** (you can only see what your scope shows) and
**action-gated** (you can only do what your role allows).

### Requirements

- **R-RBAC-1 (MUST).** Existing four roles preserved:
  `super_admin`, `admin`, `operator`, `viewer`. Names lock unless
  product redline changes them.
- **R-RBAC-2 (MUST).** A user MUST have a *platform role*
  (`super_admin` or `none`) plus zero-or-more **site memberships**
  each carrying its own per-site role.
- **R-RBAC-3 (MUST).** **Site is the unit of data scope.** Every
  resource (device, group, rule, schedule, audit row, deployment)
  MUST be assignable to exactly one site. RBAC list-queries MUST
  filter by `site_id IN (memberships of current_user)` unless the
  user is a platform super_admin.
- **R-RBAC-4 (MUST).** Permission matrix per site role:

  | Action | viewer | operator | admin | super_admin |
  |---|---|---|---|---|
  | View devices, groups, audit, events | ✓ | ✓ | ✓ | ✓ |
  | Manual power on / off / cycle | — | ✓ | ✓ | ✓ |
  | Hold-off / mass-action gate > 5 | — | — | ✓ | ✓ |
  | Edit / create watchdog rules | — | — | ✓ | ✓ |
  | Manage users, invites, roles (site) | — | — | ✓ | ✓ |
  | Manage firmware releases / deployments | — | — | ✓ | ✓ |
  | Manage platform settings | — | — | — | ✓ |
  | Cross-site / global super-admin ops | — | — | — | ✓ |

- **R-RBAC-5 (MUST).** Optional **custom roles** are deferred to
  v2 and explicitly out of scope for v1.
- **R-RBAC-6 (MUST).** Audit logging MUST capture every user
  action, including login (success + failure), logout, role
  change, invite issued / redeemed, mass-action issued (with
  target count + confirmation level), watchdog rule
  create/update/delete/toggle, schedule create/update/delete, and
  every power action with the `reason` field (R-CTRL-6).
- **R-RBAC-7 (MUST).** Invite flow:
  - Inviter selects email + (site_id, role) tuples + optional
    platform-super-admin toggle (super-admin-only).
  - Invitation expires (default 7 days, configurable).
  - Redemption sets the password and joins the listed sites.
  - Existing email-based invite primitive (already implemented) is
    extended with the per-site scoping payload.
- **R-RBAC-8 (MUST).** "Where am I signed in" surface — a profile
  page section listing every active server-side session
  (`user_sessions` table, shadow-mode shipped v0.2.10) with the
  ability to revoke individual sessions or "sign out everywhere"
  (existing `revoke_all_tokens` plumbing).
- **R-RBAC-9 (MUST).** Server-side session **enforcement** flips
  on (per RFC-003 §10 + REMEDIATION-PLAN §4 R7) so the v0.2.10
  shadow-mode actually rejects revoked cookies. Closes BUG-005.
- **R-RBAC-10 (MUST).** Migration of existing flat-permission
  fleets: a "Default" site is auto-created at upgrade; every
  existing device/group/audit/deployment is assigned to it; every
  existing user gets `admin` membership in Default. Behaviour is
  unchanged for anyone who never opens the site picker.
- **R-RBAC-11 (SHOULD).** A "transfer site ownership" action that
  reassigns site `owner_user_id` between two existing site members
  with both-party confirmation and an audit row.
- **R-RBAC-12 (SHOULD).** A "leave site" action for a non-owner.
- **R-RBAC-13 (MAY).** SCIM provisioning is post-v1.

### Acceptance

- Alice (admin Default, viewer Lab) sees devices in Default and
  Lab in her devices list, but the cycle button is disabled on
  Lab devices.
- Bob (operator Default) cannot access `/app/users`.
- A migrated fleet with one user behaves identically to pre-
  migration for that user.

---

## 7. Logs and history requirements

### Goal

Every state change in the system must leave a trace that an
operator can find later. Logs are not just for debugging — they
are the trust contract with the user.

### Requirements

- **R-LOG-1 (MUST).** A unified **history view** MUST exist
  spanning at least:
  - power events (R-CTRL-6)
  - watchdog probe outcomes + triggers (R-WD-8)
  - schedule fires
  - user actions (login, role changes, invite, revoke, etc.)
  - device status changes (online → offline, never → online)
  - firmware / deployment events
- **R-LOG-2 (MUST).** Each log row MUST have: timestamp, actor
  (user / schedule / watchdog / device / system), target_type,
  target_id, action, reason, and a `details` JSON blob.
- **R-LOG-3 (MUST).** Filterable by: device, group, site, user,
  action, reason, date range. Filter state round-trips via URL
  (consistent with R-DEV-5).
- **R-LOG-4 (MUST).** Per-record audit slice (already shipped
  v0.2.9) MUST extend to: site detail, deployment detail, rule
  detail, schedule detail, user detail.
- **R-LOG-5 (MUST).** **Export** (CSV + JSON) of the current
  filtered view. CSV for spreadsheet ops, JSON for programmatic
  consumption.
- **R-LOG-6 (MUST).** Retention policy is configurable in
  platform settings (default 365 days for audit, 90 days for
  watchdog probe events, 30 days for raw heartbeats — heartbeats
  may be aggregated after that window).
- **R-LOG-7 (SHOULD).** A "diff" view on user actions that
  changed a record MUST show before/after of the changed fields.
- **R-LOG-8 (SHOULD).** Log search: free-text search across the
  `details` JSON within a date window.
- **R-LOG-9 (MAY).** Streaming live tail (WebSocket / SSE) is v2.

### Acceptance

- Operator can answer "what cycled this device last week?" in ≤
  3 actions — open device detail, scroll to Audit / Events tab,
  see the event with reason annotated.
- Exported CSV opens cleanly in Excel / Google Sheets.

---

## 8. Notifications requirements

### Goal

When something happens that an operator should know about, the
operator should know about it — through the channels the operator
prefers, with controls to prevent alert fatigue.

### Requirements

- **R-NOTIF-1 (MUST).** Notification channels supported in v1:
  - **email** (uses existing SMTP plumbing)
  - **webhook** (POST a JSON payload to a configured URL)
  - **mobile push** via Web Push (browser-native; deferred to R8
    of REMEDIATION-PLAN if mobile-distribution decision delays)
- **R-NOTIF-2 (MUST).** Notification channels supported as
  configurable v2:
  - **MQTT** (publish event → topic for HA / OpenHAB consumption)
  - **Home Assistant native** (long-lived token integration)
- **R-NOTIF-3 (MUST).** Notification rule shape: `(condition,
  severity, channel-set, quiet-hours, recipient-set)`. A condition
  can target the same probe set as watchdog (Section 4) plus the
  derived events (`watchdog_triggered`, `device_offline_24h`,
  `firmware_deploy_complete`, etc).
- **R-NOTIF-4 (MUST).** Severity levels: `info`, `warn`, `critical`.
  `critical` ignores quiet-hours; `info` and `warn` honour them.
- **R-NOTIF-5 (MUST).** **Repeated-failure escalation**: a rule MAY
  specify "if condition still active after N minutes, escalate to
  `critical`" with the escalation potentially adding extra
  recipients.
- **R-NOTIF-6 (MUST).** **Recovery alerts**: a rule MAY emit a
  matching "all clear" notification when its condition recovers.
  This MUST be a per-rule toggle (default ON for `critical`,
  OFF for `info`).
- **R-NOTIF-7 (MUST).** **Quiet hours**: per-rule and per-user.
  Per-user quiet hours suppress *all* notifications to that user
  in the window unless the rule severity is `critical`.
- **R-NOTIF-8 (MUST).** Per-channel test send (e.g., "Send a test
  email to `alice@example.com`") gated to `admin+`.
- **R-NOTIF-9 (SHOULD).** Notification audit log: every send
  attempt, success / failure, recipient, channel.
- **R-NOTIF-10 (MAY).** Slack / Discord / Telegram channels via
  webhook templates (post-v1).

### Acceptance

- An operator gets an email **and** a browser push notification
  within 30 s of the modem-watchdog rule triggering (latency
  bounded by the watchdog probe cadence).
- Quiet hours suppress an `info` notification at 03:00 but pass a
  `critical` notification through.

---

## 9. Settings requirements

### Goal

Every operator-tunable knob has a UI surface. No more "set this
env var and restart" for things a normal operator should be able
to change.

### Requirements

- **R-SET-1 (MUST).** Settings page MUST be tabbed:
  - **System** — portal name, branding, retention windows,
    timezone defaults, maintenance-mode toggle (R-AUTO-5)
  - **Network** — public base URL, secondary URL, CORS allowlist
    (R8-CORS env var surfaced here), TLS reminder
  - **Authentication** — session idle timeout, MFA enrolment, OAuth
    provider settings (Google / GitHub at v1; see RFC-003 §10)
  - **Backup / restore** — config export, import, snapshot
  - **API tokens** — long-lived integration tokens
  - **Webhooks** — outbound notification webhook URLs + secrets
  - **Integrations** — MQTT, Home Assistant, etc. (v2)
  - **Theme** — light / dark / system
  - **Update / firmware** — firmware mirror chain config (RFC-002)
- **R-SET-2 (MUST).** Backup MUST export the entire portal
  configuration as a single JSON file: users, sites, groups,
  watchdog rules, schedules, notification rules, retention
  settings. Devices and audit logs are deliberately excluded
  (they are state, not config).
- **R-SET-3 (MUST).** Restore MUST accept the same JSON, validate
  the schema, and offer a dry-run diff before applying.
- **R-SET-4 (MUST).** API token issuance: an operator with
  `admin+` MUST be able to mint a long-lived JWT-style token
  scoped to (site_set, role, expiry-or-none). Tokens MUST be
  shown once at creation and never again. Token list MUST show
  prefix + last-used + revoke action.
- **R-SET-5 (MUST).** Webhook configuration: operator can register
  outbound notification URLs with optional HMAC signing secret.
  Per-webhook test send must succeed before the webhook is armed
  for production traffic.
- **R-SET-6 (MUST).** Theme: `light`, `dark`, `system` (defer to
  OS preference). User-scoped, persisted in profile.
- **R-SET-7 (MUST).** Update / firmware settings surfaces the
  RFC-002 mirror-chain configuration (primary host + GitHub
  Releases mirror) with status indicators per mirror.
- **R-SET-8 (SHOULD).** Settings changes MUST be audit-logged with
  before/after values (R-LOG-7).
- **R-SET-9 (MAY).** Per-site overrides for global settings (e.g.,
  per-site retention) — v2.

### Acceptance

- An operator can rotate a Home-Assistant API token without
  shelling into the container.
- A restore from a backed-up config produces a functionally
  identical portal in a fresh deployment.

---

## 10. Open-source / DIY emphasis

### Goal

Honour C2 and C6. Do not let the redesign accidentally introduce
cloud dependencies or undocumented behaviour.

### Requirements

- **R-OSS-1 (MUST).** **Local-first design.** Every page must
  load and the portal must function with zero outbound internet
  access (the operator's own LAN-only deployment is supported).
- **R-OSS-2 (MUST).** **No forced cloud.** OAuth / OIDC sign-in
  is opt-in (R-RBAC-* covers the local email+password path as
  always-available).
- **R-OSS-3 (MUST).** **Clear device behaviour.** Every device
  surface MUST link to the device's own local web UI when the
  IP is known.
- **R-OSS-4 (MUST).** **Export/import config.** R-SET-2 / R-SET-3.
- **R-OSS-5 (MUST).** **Human-readable rules.** Every watchdog
  rule and schedule MUST display its plain-English form (R-WD-1)
  on every list view, not just inside the editor.
- **R-OSS-6 (MUST).** **Transparent logs.** R-LOG-* covers this.
- **R-OSS-7 (MUST).** **API-first thinking.** Every page MUST
  consume the same JSON API a third-party integrator would. No
  internal-only endpoints. No internal-only authentication path.
- **R-OSS-8 (MUST).** **Self-hosting friendly.** Docker compose
  one-liner deployment, no DNS-rewriting, no captive licence
  check.
- **R-OSS-9 (MUST).** **Mobile and desktop responsive.** R-DEV-3
  + R-CTRL-1.
- **R-OSS-10 (SHOULD).** **API documentation** auto-generated
  from blueprints (OpenAPI 3) and served at `/api/docs`.
- **R-OSS-11 (SHOULD).** A **Readme-driven dev story** —
  `make dev`, `make test`, `make lint`, `make build` aliases for
  the existing pip + pytest + ruff + docker workflow.

---

## 11. Cross-cutting UX / design requirements

### Goal

The visual + interaction language of the portal must be modern,
trustworthy, and accessible. These apply to *every* screen.

### Requirements

- **R-UX-1 (MUST).** Mobile-first layout. Phone breakpoint
  (≤ 640 px) is the *primary* design target; desktop is the
  enhancement.
- **R-UX-2 (MUST).** Bottom-tab nav on mobile (5 destinations);
  top nav on desktop. Same five destinations regardless of
  breakpoint.
- **R-UX-3 (MUST).** Touch targets ≥ 44 × 44 CSS px on every
  mobile breakpoint.
- **R-UX-4 (MUST).** Light + dark + system theme. Default to
  system.
- **R-UX-5 (MUST).** WCAG 2.2 AA target — every interactive
  element keyboard-reachable; visible focus ring; contrast ≥ 4.5:1
  for body text.
- **R-UX-6 (MUST).** **Empty states** are designed, not just blank.
  Every list page has a useful empty state (e.g. *"No devices
  yet — [Enrol your first device]."*).
- **R-UX-7 (MUST).** **Error states** are designed. A failed API
  call surfaces with a retry button and a "Copy error details"
  affordance for support.
- **R-UX-8 (MUST).** **Offline state** of the portal itself: if
  the operator's browser loses connectivity, the UI displays a
  "You are offline — some actions may not work" banner, not
  silent failures.
- **R-UX-9 (MUST).** **Onboarding flow** for first-login: a 3-step
  tour pointing at Inbox / Add a device / Settings → API tokens.
  Dismissible.
- **R-UX-10 (MUST).** **Plain language**: no jargon in primary
  UI strings; technical terms allowed in advanced mode and tooltip
  details.
- **R-UX-11 (MUST).** **Progressive disclosure**: every advanced
  mode (advanced rule editor, danger-zone settings, raw JSON
  views) MUST be reachable but never the default.
- **R-UX-12 (MUST).** **Dangerous-action visual treatment**:
  destructive actions use a distinct colour (e.g. red); confirmation
  modals for destructive actions have clearly distinct styling
  from informational modals.
- **R-UX-13 (MUST).** **Never lie about state**. If we don't know
  whether a device is online, we say `unknown` — we don't say
  `offline`. (This is the v0.2.7 lesson recapitulated.)
- **R-UX-14 (SHOULD).** Command palette (`Cmd / Ctrl-K`) with
  fuzzy-search across devices, rules, schedules, settings.

---

## 12. Acceptance criteria — global

A redesign milestone (e.g. v1.0.0 of the redesigned portal) is
"done" when **all MUST requirements** above are implemented,
covered by automated tests where the requirement is testable, and
the manual UX checks below pass:

- A first-time operator can enrol one device, create one
  watchdog rule, and receive an email when that rule fires — in
  under 15 minutes, with no docs other than the in-app onboarding.
- A multi-site operator can give a colleague viewer access to one
  site without exposing any other site.
- A power user can express a 3-retry-with-escalation rule via the
  advanced editor and export it as JSON.
- A self-hosted instance survives a 24-hour internet outage with
  every local watchdog still functioning correctly (because
  central watchdog probes can fail open per maintenance-mode
  semantics).
