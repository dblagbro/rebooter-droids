# Project State — Full Sync — 2026-05-09

| Field | Value |
|---|---|
| Status | **Authoritative pause state** (saved 2026-05-09 from product/firmware/PM full sync handover) |
| Saved by | rebooter-droids backend/web team on instruction from Codex acting for product/firmware/PM |
| Audience | backend/web team, firmware team, product, PM — anyone resuming this project |
| Supersedes | — |
| Companion files | `docs/sessions/20260509-pause-full-sync.md` (session-log pointer) |

> **Read this on resume.** This file is the *authoritative session-state
> handover* between product/firmware/PM and the backend/web team as of
> the 2026-05-09 pause. The 14 numbered sections below are recorded
> verbatim from the PM hand-off, followed by an engineering snapshot
> (§15) capturing the repo + live deployment state at save time.
> When this document conflicts with older session notes, **this
> document wins**.

---

## 1. Product goal and philosophy

Rebooter is intended to be a local-first, low-cost, self-contained
reboot appliance built initially on Sonoff S31 smart plugs. The core
product promise is not generic smart-home switching; it is reliable,
user-friendly automated reboot/recovery of home and lab equipment
that ordinary users cannot get cheaply today.

The product direction has three layers:

- **Layer 1:** minimal serial/bootstrap bring-up path
- **Layer 2:** full local-first firmware with local web UI, local
  control, local OTA, watchdog modes, and no cloud dependency
  required
- **Layer 3:** optional central coordination platform at
  `https://www.voipguru.org/rebooter/` for inventory, grouping,
  remote commands, firmware release management, multi-user
  administration, and eventual mobile app support

The device must remain useful without the central platform. Central
is an enhancement, not the basis of the product.

Target use cases remain:

- standard smart plug mode
- internet watchdog mode for modem/router rebooting
- device watchdog mode for rebooting a single attached device such
  as a PC, server, switch, or other lab gear

Longer-term product direction includes:

- mobile app support
- presence/automation work later
- fleet and group control
- local control plus central control
- open/shared API direction and extensibility

## 2. Hardware / lab status

We now have four Sonoff S31 test devices alive on the LAN and
updated to the newer schema-v2-capable firmware family.

Current device map:

| IP | MAC |
|---|---|
| 192.168.1.67 | c4-d8-d5-0c-f7-ca |
| 192.168.1.225 | c4-d8-d5-0c-f6-b3 |
| 192.168.1.207 | c4-d8-d5-0c-f7-59 |
| 192.168.1.30 | c4-d8-d5-0c-f7-a5 |

Physical chain constraint in the lab:

- These devices are wired in series in a power chain.
- First added = deepest downstream.
- Newest added = most upstream.
- Reset order rule for any power-interruptive testing is:
  1. if first-added must be reset, use second-added
  2. if second-added must be reset, use third-added
  3. if third-added must be reset, use newest upstream
- We have been intentionally preferring read-only checks and OTA
  over relay toggling because of that chain dependency.

Current live device states:

- **192.168.1.67:** healthy locally, relay on, Wi-Fi connected,
  central enabled, central registered, but central state is still
  failing with heartbeat/poll transport failure. *This is the real
  known firmware-side central-transport bug unit.*
- **192.168.1.225:** healthy locally, relay on, Wi-Fi connected,
  schema v2, central disabled.
- **192.168.1.207:** healthy locally, relay on, Wi-Fi connected,
  schema v2, central disabled.
- **192.168.1.30:** healthy locally, relay on, Wi-Fi connected,
  schema v2, central disabled.

Important interpretation:

- The hardware bring-up problem is no longer the main blocker.
- Serial/bootstrap/OTA path is working.
- The active real device-side issue is central communication
  reliability on the first enrolled unit only.

## 3. Serial / bootstrap / OTA history and current status

Important historical finding on the Sonoff S31 board revision used
here:

- The plain TX/RX pads were the actual ESP UART path on this unit,
  not D-TX/D-RX.
- Reliable flash mode required proper GPIO0 low-at-boot behavior.
- Long reads were flaky during backup attempts, but once we shifted
  strategy toward a minimal bootstrap image and OTA handoff,
  progress became practical.

We built and used a minimal bootstrap firmware that:

- joins known Wi-Fi
- fetches hosted main firmware from the website
- installs the main firmware
- hands off to the local-first main firmware

This bootstrap path worked repeatedly in the lab and is now the
approved first-flash strategy for new units rather than chasing
full-stock backup perfection.

We also built helper scripts on the firmware side to reduce
operator friction around stale build locks and repeated flash
attempts.

Main practical outcome: new units can be brought up by one painful
serial/bootstrap flash and then moved to OTA/local-web-based
iteration.

## 4. Firmware status

Firmware repo:

- GitHub repo now exists and is pushed:
  `https://github.com/dblagbro/rebooter-firmware`

Additional firmware safety/backups completed:

- local zip backup created
- local git bundle created
- copies placed on the shared voipguru documents path

Major firmware accomplishments in this session window:

- bootstrap OTA flow implemented
- local web UI implemented and served from device
- local OTA update path working
- local auth added
- central configuration model added
- central client integration added
- central base URL changed from a single URL to ordered fallback
  list:
  - `https://www.voipguru.org/rebooter`
  - `https://www2.voipguru.org/rebooter`
- firmware logic updated to re-enroll cleanly if cached central
  credentials become stale or unauthorized
- bootstrap and main firmware build/deploy path verified on
  multiple devices

Important live firmware limitation still open:

- first enrolled unit `test-s31-01` remains in a central transport
  failure state even while healthy locally. This is the most
  important remaining firmware integration bug in the lab right
  now.

Configuration/status distinction that matters for backend/web:

- only one real device is centrally enrolled right now:
  `test-s31-01` / `dev_01KR5HV2PY7CY1CD9WMWM3W1KS`
- the other three are real working devices but remain local-only /
  central-disabled for now
- backend UI must not imply that local-only devices are broken just
  because they are not centrally enrolled

## 5. Backend / central platform status

Canonical central platform path remains:

- `https://www.voipguru.org/rebooter/`

Multi-node direction remains:

- primary: `www.voipguru.org` / node 1
- secondary/fallback: `www2.voipguru.org` / node 2

Backend has already implemented and shipped multiple remediation
slices during this collaboration.

Notable progress milestones reached during this session series:

- device registration / heartbeat / command-poll contract aligned
  with firmware
- backend and firmware command payload schemas aligned
- v0.2.7 shipped to address device-state semantics around
  never-heartbeated devices
- broader remediation continued beyond that
- most recent reported live backend version from team at the last
  check-in: **v0.2.11 on both URLs**
- team reported all 28 tests for the shipped remediation slices
  green against live

Important central backend data-state observations from live audit:

- the devices page was polluted by QA fixtures inserted by test
  workflows
- many devices in the UI were not real fleet devices, but
  QA-created records with null heartbeats and no meaningful
  production identity
- this created a product-hostile and misleading fleet picture even
  when some backend logic was technically correct

## 6. Web UI / product UX status

This is the largest product issue on the backend/web side and was
explicitly escalated by product.

The live audits showed:

- the original devices view was terrible from a product standpoint
- QA junk polluted the production fleet list
- offline and never-heartbeated semantics were previously conflated
  or at least not product-clear enough
- the UI remained too operator-scaffold-like and not ready to serve
  as the real user-facing product shell
- the app remained too desktop-table-centric and not properly
  shaped for eventual mobile productization

A product remediation track was then approved in full.

Documents created by backend/web team in response to product
direction:

- `docs/RFC-002-firmware-mirrors.md`
- `docs/RFC-003-web-ui-redesign.md`
- `docs/REMEDIATION-PLAN-2026-05-WEB-UI.md`

What these represent:

- **RFC-002:** firmware hosting resilience / mirrors strategy
- **RFC-003:** broad web UI redesign / IA / RBAC / auth /
  mobile-compatibility direction
- **REMEDIATION-PLAN:** phased execution plan for cleaning up the
  current fleet/device UX and then growing into the larger
  redesign

Product direction already approved for them:

- full approval was granted to proceed with the remediation plan,
  not just the minimum slice
- expectation is phased safe execution and milestone reporting back
  through Codex/project management

## 7. Firmware hosting resilience direction

Important backlog/design item was explicitly raised and handed
over:

- canonical firmware binaries should move under the managed
  `/rebooter/` tree
- devices should support trying multiple firmware download
  locations in order
- at least one fallback hosting location must be operationally
  independent from the main business infrastructure so firmware
  recovery remains possible if the primary operation is unavailable
  or the business stops operating

Backend/web team returned RFC-002 with the recommended direction:

- move managed hosting under `/rebooter/firmware/`
- support ordered firmware mirrors
- use **GitHub Releases as the independent fallback mirror** rather
  than Google Drive
- preserve integrity verification via central-managed hashes

This direction is considered accepted as the planning basis unless
later changed by product redline.

## 8. Auth / RBAC / mobile direction

Current accepted direction at the planning level:

- multi-user support matters; this is not a single-user toy UI
- site/group/device access needs proper scoping and roles
- local device UI remains important, but central UI must grow into
  a true product shell
- optional OAuth later is desired, but provider commitments should
  be deliberate
- mobile app compatibility needs to shape the API and IA now, even
  if the native app ships later

The backend/web team's RFC-003 captured the broader design
direction for:

- inbox / fleet / releases / site / settings IA
- RBAC and site scoping
- auth / OAuth options
- mobile compatibility strategy
- phased rollout and open product redlines

## 9. QA / testing status

Backend/web QA status as last reported:

- remediation slices through v0.2.11: **all 28 relevant tests green
  against live**
- one broader mobile login overflow issue was identified earlier as
  pre-existing and slated for later responsive/mobile phases rather
  than the early remediation slice

Product expectation remains:

- Playwright or equivalent browser-level QA should continue to be
  used for real regression coverage, especially around the web UI
  and responsive states
- production / test separation must be validated, not just assumed

## 10. Backups / safety status

Firmware side:

- firmware repo now created and pushed to GitHub
- zip backup and git bundle backup created
- copies staged on shared storage

Backend side:

- backend repo already on GitHub
- live stack healthy in Docker at last check
- SQL dump of the backend Postgres state was created during this
  session window

In other words, the project is no longer in the unsafe state where
critical work exists in only one local working tree.

## 11. Current open issues / known real problems

### A. Real firmware bug still open

- `test-s31-01` remains locally healthy but centrally failing with
  heartbeat/poll transport failure
- this remains the most meaningful live firmware integration issue

### B. Only one real device centrally enrolled

- three additional real devices are ready but still
  central-disabled
- backend UI cannot yet reflect a healthy real fleet until central
  enrollment of additional units happens or the product
  intentionally models local-only fleet states better

### C. Product redlines still exist for the broader redesign phases

- although early remediation phases have been moving, the larger
  redesign documents still contain open strategic questions that
  eventually need product decisions

## 12. What has already been approved vs what still needs product input

### Already approved

- proceed with full remediation plan execution, not just minimal
  cleanup
- continue central coordination path
- continue multi-URL firmware-hosting/mirror planning
- continue product-quality UI remediation, not just visual patching

### Still eventually needs product answers later

- specific RFC-003 redlines around nav / inbox rules, site
  migration behavior, OIDC / MFA timing, mobile distribution
  direction, etc.
- those are not a stop-work order for everything, but they do
  affect later phases

## 13. What backend / web team should assume during pause

Assume the following are authoritative during pause/resume:

- product remains committed to local-first devices with optional
  central enhancement
- the web UI is not a throwaway admin tool; it is the future
  product shell and mobile-app seed
- the fleet/device experience must prioritize real user
  comprehension over raw database row exposure
- QA fixture isolation is a product requirement, not a nicety
- firmware hosting resilience with at least one independent
  fallback mirror is in scope and desired
- backend/web work should keep reporting milestone-by-milestone
  through Codex/project management, using DONE/BLOCKED discipline

## 14. Request for save (the action that produced this document)

This document is the durable on-disk capture of the full status
sync. The next resume should start from this document plus the
companion session pointer and the existing RFC/remediation
artifacts.

---

## 15. Engineering snapshot at save time (added by backend/web team)

This section is *not part of the PM hand-off* — it is the
backend/web team's ground-truth snapshot of repo + live deployment
state at the moment this document was saved, so the next resume can
pick up without having to re-derive it.

### 15.1 Live deployment

- **Live versions:** both `https://www.voipguru.org/rebooter` and
  `https://www2.voipguru.org/rebooter` serve **v0.2.11**
  (verified at the last check-in immediately before this save).
- **Container:** `dblagbro/rebooter-droids:0.2.11` /
  `dblagbro/rebooter-droids:latest` (Docker Hub digest
  `sha256:ce087abf4232ed127ddbec3947a062e69598bbddad57050434d875a55442ab3a`).
- **Postgres:** live; `user_sessions` table populated and being
  written/revoked correctly by the v0.2.10 shadow path; `devices`
  table holds only one real device (`test-s31-01` /
  `dev_01KR5HV2PY7CY1CD9WMWM3W1KS`) plus zero QA fixtures (purged
  in v0.2.7 and isolated in v0.2.8).

### 15.2 Repo state

- **Active branch:** `feat/v028-fixture-isolation` on
  `https://github.com/dblagbro/rebooter-droids`.
- **Tags shipped this session series:** `v0.2.6`, `v0.2.7`,
  `v0.2.8`, `v0.2.9`, `v0.2.10`, `v0.2.11`. All pushed to origin
  with annotated tags + GitHub Releases + Docker Hub images.
- **HEAD:** commit `798a1f0` ("v0.2.11: strict CORS allowlist for
  /api/v1/* (R8-CORS)").
- **`main`** still sits at v0.2.5 (`84a7b97`). The
  v0.2.6 → v0.2.11 line lives on `feat/v028-fixture-isolation`
  per the operator's prior pattern of branch-tag-release without a
  required main merge. Fast-forwarding `main` is a follow-up if/when
  the operator wants the convention.

### 15.3 Remediation phase status (per `docs/REMEDIATION-PLAN-2026-05-WEB-UI.md`)

| Phase | Status | Shipped as |
|---|---|---|
| R1 (ship v0.2.7 device-state semantics) | **DONE** | v0.2.7 |
| R2 (test-fixture isolation) | **DONE** | v0.2.8 |
| R3 (per-record audit slice on device + group detail) | **DONE** | v0.2.9 |
| R7-shadow (server-side `user_sessions` shadow mode) | **DONE** | v0.2.10 |
| R8-CORS (strict allowlist on `/api/v1/*`) | **DONE** | v0.2.11 |
| R4 (5-item Inbox/Fleet/Releases/Site/Settings nav scaffold) | **BLOCKED** on plan §7 gate (2) — approval to launch + lock Inbox v1 ranking rules |
| R5 (Fleet redesign + saved filters + diagnostic tabs) | waits one minor after R4 |
| R6 (site-as-scope RBAC migration) | **BLOCKED** on RFC-003 redline #1 (split-site tool day-one vs post-migration manual) |
| R7-rest (TOTP + OIDC + password-reset + mandatory-MFA flips) | **BLOCKED** on RFC-003 redlines #2 (OIDC providers) and #3 (MFA mandatory-flip timing) |
| R8-rest (push fan-out + mobile JWT scope + APNs/FCM) | **BLOCKED** on RFC-003 redline #4 (mobile distribution model) |
| R9 (mobile-first responsive + Passkeys) | gated on R4–R8 having shipped |

Engineering has shipped every phase that does not require product
input. Tier-1 SHIP-EARLY work is fully discharged. The next move on
the engineering side requires either (a) approval of the R4 nav
scaffold + Inbox v1 ranking rules, or (b) any of the RFC-003
redlines for R6 / R7-rest / R8-rest.

### 15.4 Known pre-existing failures NOT in scope of any shipped slice

- `tests/qa/test_responsive.py` reports 7 mobile-overflow failures
  at 375 px viewport across `/app/login`, `/app/dashboard`,
  `/app/devices`, `/app/events`, `/app/audit`, `/app/users`.
  Verified pre-existing (fail identically against v0.2.7); explicitly
  scheduled for **R5** (Fleet card-layout) + **R9** (mobile-first
  responsive) in the remediation plan.
- Group / site / firmware **create** paths do not currently call
  `audit_service.record()`. Surfaced by the v0.2.9 group-detail
  test; queued as a small follow-up minor (not in any current
  phase's scope).

### 15.5 Memory pointers

- Auto-memory pause-state entry written so future Claude sessions
  resume cold from this exact state. See
  `~/.claude/projects/-home-dblagbro/memory/MEMORY.md` for the
  index.
- This document supersedes the older `docs/remediation-plan.md`
  (legacy) for the active web-UI track. The active execution plan
  is `docs/REMEDIATION-PLAN-2026-05-WEB-UI.md`.

### 15.6 What backend/web is NOT doing during pause

- Not merging `main`. The release tags + Docker images on the
  feature branch are the canonical record-of-truth.
- Not opening any PRs (operator hasn't asked for one).
- Not running any further QA cycles unless explicitly asked.
- Not flipping `REBOOTER_SESSIONS_ENFORCE` from shadow → enforce.
  Per the plan §4 R7 rollout strategy, that flip is gated on at
  least one minor of live observation **plus** a product greenlight
  on the auth-foundation rollout schedule (which is itself blocked
  on RFC-003 redlines #2–#3).
- Not configuring `REBOOTER_CORS_ALLOWED_ORIGINS` on the live
  deployment. Allowlist is intentionally empty until product names
  the first cohort of operator-controlled origins (mobile-dev
  staging origin or similar).

### 15.7 Resume-here checklist

When the project resumes:

1. Read this document end-to-end.
2. Read `docs/REMEDIATION-PLAN-2026-05-WEB-UI.md` to recover the
   phase definitions.
3. Read `docs/RFC-002-firmware-mirrors.md` and
   `docs/RFC-003-web-ui-redesign.md` for the design context.
4. `git status` on `feat/v028-fixture-isolation` should be clean
   except for whatever new work is about to start.
5. `curl -s https://www.voipguru.org/rebooter/api/v1/version`
   should return `0.2.11` (or higher if a teammate shipped further
   in the interim).
6. Decide which approval gate is being acted on (see §15.3 BLOCKED
   rows) and pick up the corresponding phase.

---

## 16. Engineering snapshot — second pause point (post v0.3.5 + RFC-004)

Added 2026-05-09 (later same day) at the operator's "save state
and prepare for a pause" call. The §15 snapshot above captured
the v0.2.11 pause point; this section captures the v0.3.5 + RFC-
004 pause point. **Both are accurate at their respective
moments. Read both on resume.**

### 16.1 Live deployment

- Both `https://www.voipguru.org/rebooter` and
  `https://www2.voipguru.org/rebooter` serve **v0.3.5**.
  (Architecturally: one logical hub on two URLs — see §16.4.)
- Container image: `dblagbro/rebooter-droids:0.3.5` /
  `dblagbro/rebooter-droids:latest`
  (Docker Hub digest `sha256:c216b8c89e8f...`).
- Postgres: live; `devices` table is **empty** (operator hit the
  v0.3.4 bulk-delete bug while testing; the cascade wiped
  everything including `device_heartbeats`. Real fleet records
  need re-enrolment when the firmware-side work resumes.)

### 16.2 Repo state

- **Active branch:** `fix/v035-bulk-delete-pair-sync` on
  `https://github.com/dblagbro/rebooter-droids`.
- **HEAD:** commit `357c34e` ("docs: RFC-004 multi-hub sync +
  Settings/Sync stub"). Working tree clean, pushed to origin.
- **Tags shipped this extension of the session:** `v0.2.7` →
  `v0.2.11` (covered by §15) plus `v0.3.0`, `v0.3.1`, `v0.3.2`,
  `v0.3.3`, `v0.3.4`, `v0.3.5`. All annotated tags + GitHub
  Releases + Docker Hub `:tag` and `:latest` images.

### 16.3 WebUI redesign phase status (per `docs/webui-redesign-plan.md` §9)

| Phase | Description | Shipped as |
|---|---|---|
| P1 | Design system + 5-item nav (Status / Devices / Rules / History / Settings) | **v0.3.0** |
| P2 | Status feed + device list/detail restructure + saved-filter chips + mobile cards + enrollment wizard | **v0.3.1** |
| P3 | Power controls + safety + `is_protected` lockout + hold-off + cancel-pending + reason field | **v0.3.2** |
| Auth fix | Cookie domain + `rebooter_session` rename to fix frequent sign-outs | **v0.3.3** (out-of-band hotfix) |
| Bulk-action UI | Checkboxes + select-all + sticky bar on devices/groups/invitations/tokens | **v0.3.4** |
| Bulk-action hotfix | Pair-sync paired checkboxes + server-side dedupe + RCA writeup | **v0.3.5** |
| P4 (watchdog rules + schedules) | Net-new tables, plain-English rule editor, probe runtime | **NOT STARTED** |
| P5 (RBAC + auth foundation) | Site-as-scope migration, TOTP, OIDC, password reset | **BLOCKED on RFC-003 redlines #1–#3** |
| P6 (history / notifications / settings UX) | Unified history, notification rules, full settings surface | **BLOCKED on RFC-003 redline #4** |
| P7 (polish + a11y + Passkeys) | Final polish | last |

### 16.4 RFCs and design docs on disk

| File | Status |
|---|---|
| `docs/RFC-001-presence.md` | Draft, awaiting redline |
| `docs/RFC-002-firmware-mirrors.md` | Draft, ack'd in principle by firmware team (per `docs/notes/2026-05-09-from-firmware-team-rca-response.md`) |
| `docs/RFC-003-web-ui-redesign.md` | Draft; P1–P3 + bulk-action work executed against it; redlines #1–#4 still open |
| `docs/RFC-004-multi-hub-sync.md` | **NEW (this pause)** — multi-hub sync design; recommends Option B (Postgres logical replication, active-passive); awaiting operator pick of architecture before any P1+ implementation |
| `docs/REMEDIATION-PLAN-2026-05-WEB-UI.md` | R1, R2, R3, R7-shadow, R8-CORS shipped; R4–R9 superseded by the webui-redesign-plan above |
| `docs/webui-redesign-{research,requirements,plan}.md` | Trio shipped earlier in the session; plan §9 is the canonical phase definition |
| `docs/rca-2026-05-09-no-device-online.md` | RCA for "no device online"; §7 carries the firmware-team reply with corrected current lab state (all 4 devices reachable; only `test-s31-01` has central-transport bug remaining) |
| `docs/notes/2026-05-09-to-firmware-team-rca-and-hosting.md` | Outgoing cross-team note |
| `docs/notes/2026-05-09-from-firmware-team-rca-response.md` | Reply (acks RFC-002 in principle; bootstrap is just an ESP8266 .bin at offset 0x00000, can co-host; flash-time URL list is build-flag work) |

### 16.5 What's BLOCKED at this pause point

1. **Multi-hub sync (RFC-004)** — operator must redline
   §9 questions before P1+ starts. Recommended path: Option B.
2. **Webui redesign P4 (watchdog rules + schedules)** — net-new
   feature; needs scoping commitment before any code starts.
3. **Webui redesign P5 (site-as-scope, OIDC, MFA)** — gated on
   RFC-003 redlines #1–#3.
4. **Webui redesign P6 (history / notifications)** — gated on
   RFC-003 redline #4 + the watchdog data model from P4.
5. **Firmware-side `test-s31-01` central-transport bug** — only
   real device-side issue remaining per the firmware-team
   reply. Backend has nothing to fix here.
6. **Test fleet** — `devices` table is empty after the v0.3.4
   bulk-delete cascade. Resuming any QA work needs operator to
   re-enrol at least one device, OR the QA suite to self-create.

### 16.6 Diagnostic tooling shipped this extension

- `tools/diagnostics/diagnose_signouts.py` — Playwright
  reproduction of the v0.3.2 → v0.3.3 cookie-domain bug.
- `tools/diagnostics/README.md` — convention for what lives
  there and when to promote to `tests/qa/`.

### 16.7 Known pre-existing failures NOT in scope of any current phase

- Group / site / firmware **create** paths still don't
  audit-log. Surfaced by the v0.2.9 group-detail test; small
  follow-up minor; no current phase owns it.

### 16.8 Resume-here checklist (refreshed)

When the project resumes:

1. Read this document end-to-end. **§15 captures the v0.2.11
   pause; §16 captures the v0.3.5 + RFC-004 pause. Both are
   accurate at their respective moments.**
2. Read the latest auto-memory entry for rebooter-droids — it
   points at this file and any further updates since.
3. Read the cross-team notes under `docs/notes/`.
4. `curl -s https://www.voipguru.org/rebooter/api/v1/version`
   should return `0.3.5` (or higher if a teammate shipped
   further in the interim).
5. `git status` on `fix/v035-bulk-delete-pair-sync` should be
   clean. HEAD ≥ `357c34e`.
6. **Decide which blocked item from §16.5 to pick up** —
   typically the next move is RFC-003 or RFC-004 redline by the
   operator (which is product/PM work, not engineering), or
   re-enrolling at least one device for the firmware team to
   continue debugging the central-transport bug.

### 16.9 2026-05-14 live soak addendum

- The production fleet is no longer in the "zero real devices" state
  captured in the 2026-05-09 RCA. A live recheck on 2026-05-14 found
  multiple real devices online in the hub, including the renamed soak
  target at `192.168.1.48`.
- The renamed-test recovery path remains healthy end-to-end once the
  device answers HTTP: hub UI/API, local `/api/status`, local
  `/api/config`, and the device root UI all agree on
  `Rebooter - renamed test`.
- BUG-052 remains an intermittent presentation problem, not a currently
  stuck bad row. On the follow-up window, `.225` was back to
  `online`/`idle` while the true-offline control `.69` still timed out
  locally and showed `offline` in the hub.
- BUG-053 remains the main live functional gap: ordinary fleet devices
  still drift away from the hub `display_name` outside the
  restore-after-reflash path.
- A later recheck window around `2026-05-14T04:30Z` to `04:35Z`
  tightened the picture further:
  - `.48` stayed converged on the renamed identity across the
    authenticated hub UI, hub API, local `/api/status`, and local
    `/api/config`.
  - The earlier mixed `.48` firmware snapshots resolved to
    `0.1.17-dev-central` on both hub and device once repeated probes
    settled.
  - A one-off `.207 offline` snapshot did not persist; the same device
    was back to `online`/`idle` within the short soak loop.
- Reliability issue: `.48` intermittently times out on the first local
  HTTP probe before recovering to stable `200`s. That pattern
  reproduced again in the later soak loop and is now logged as BUG-054.
- Latest stabilization recheck around `2026-05-14T04:40Z` to `04:42Z`:
  - `.48` remained converged across hub UI/API and local status/config,
    and the earlier first-contact timeout did not reproduce in a fresh
    5-cycle loop.
  - `.30` and `.225` still have desired-name drift (BUG-053), but both
    now match the hub on firmware `0.1.17-dev-central`.
  - `.207` still shows hub `Erica's R.R. Speaker` vs local
    `Erica's ?.?. Speaker`, with hub UI showing `0.1.16-dev-central`
    and an upgrade path to `0.1.17-dev-central`.
  - `.69` remains the stable offline control: hub `offline`, local
    UI/API unreachable from this host.
- A later latency-focused recheck around `2026-05-14T04:52Z` tightened
  the renamed-test reliability picture again:
  - `.48` still matched on identity and firmware across the live hub
    devices page/API plus local `/api/status` and `/api/config`:
    `Rebooter - renamed test` on `0.1.17-dev-central`.
  - The hard timeout shape of BUG-054 did not reproduce in that pass:
    a fresh 5-cycle loop on `.48` returned `200` on `/`,
    `/api/status`, and `/api/config` every time.
  - `.48` still was not fully clean, though: one local root-page read
    stretched to about `1.1 s` and one local `/api/status` read
    stretched to about `3.2 s` before later cycles dropped back to
    normal. Treat BUG-054 as narrowed to intermittent local HTTP
    slowness, not yet resolved.
  - BUG-053 remained unchanged on `.30`, `.225`, and `.207`.
- A longer recheck around `2026-05-14T05:00Z` to `05:01Z` improved the
  renamed-test picture further without clearing the remaining fleet
  drift:
  - `.48` stayed fully converged across the live hub devices page, hub
    admin API, local `/api/status`, and local `/api/config`:
    `Rebooter - renamed test`, `online`, firmware
    `0.1.17-dev-central`.
  - `BUG-054` did not reproduce in a longer 10-cycle `.48` loop. Every
    cycle returned `200`; local root-page reads stayed roughly
    `0.11 s`-`0.39 s`, `/api/status` stayed about `0.02 s`-`0.04 s`,
    and `/api/config` stayed about `0.07 s`-`0.09 s`.
  - The live hub UI and hub API matched on all comparison targets in
    this pass, so there was no fresh central-side UI/API drift.
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`.
  - `.207` still shows `online` on `0.1.16-dev-central`, and the live
    hub devices page still exposes the one-click upgrade path to
    `0.1.17-dev-central`.
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host.
- The next live recheck around `2026-05-14T05:11Z` to `05:13Z` found no
  fresh hub UI/API drift, but it did re-strengthen `BUG-054`:
  - the live hub devices page and hub admin API still matched on all
    comparison targets, including `.48` on
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-053` still remained unchanged on `.30`, `.225`, and `.207`
  - `BUG-054` reproduced again on `.48` in a stronger first-contact
    form: local `/`, `/api/status`, and `/api/config` all timed out for
    the full 10 s window while the hub still showed the device
    `online`, then an immediate follow-up loop recovered to clean
    `200`s
  - the first successful post-recovery `.48` root-page fetch was still
    slow at about `1.9 s`, so treat the issue as intermittent local
    HTTP failure/latency, not resolved stability
- A subsequent recheck around `2026-05-14T05:20Z` to `05:22Z` improved
  the renamed-test picture again without clearing the remaining fleet
  drift:
  - the live hub devices page and hub admin API still matched on all
    comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - the hard-timeout shape of `BUG-054` did not reproduce in that
    window: the initial local sweep plus a 10-cycle `.48` loop both
    stayed at `200` on `/`, `/api/status`, and `/api/config`
  - `BUG-054` still is not closed, though: in that 10-cycle loop the
    local root page stayed roughly `0.14 s`-`0.54 s` for nine cycles,
    then stretched to about `2.16 s` on cycle 10 while `/api/status`
    and `/api/config` remained fast
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T13:10:45Z` to `13:12:30Z`
  still showed no fresh hub UI-vs-API drift, and it added a concrete
  post-`13:04Z` recovery window without clearing the standing issues:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    `.48`, `.30`, `.225`, `.207`, and `.69`, so there was still no
    fresh central-side UI/API drift
  - `.48` improved back out of the immediate-failure shape seen around
    `13:01Z`: hub UI/API still showed `Rebooter - renamed test`
    `online` on `0.1.17-dev-central`, local `/api/status` plus
    `/api/config` still matched that identity, the initial local
    root/status/config sweep returned clean `200` responses with local
    `/api/status` at `uptime_seconds=598`, and the immediate 5-cycle
    follow-up loop from `13:12:15Z` to `13:12:21Z` stayed clean while
    local `/api/status` climbed from `687` to `693`; only one
    root-page sample stretched to `1.12 s`, which is below the earlier
    timeout / connection-reset pattern
  - `.30` stayed improved with uptime continuity: hub UI/API still
    showed it `online` on `0.1.17-dev-central`, local root/status/config
    all returned `200`, and local `/api/status` reported
    `uptime_seconds=2434`, so this pass added no fresh `BUG-056`
    reboot evidence beyond the standing desired-name drift
  - `.225` stayed improved with uptime continuity: hub UI/API still
    showed it `online` on `0.1.17-dev-central`, local root/status/config
    all returned `200`, and local `/api/status` reported
    `uptime_seconds=7679`; keep only the standing desired-name drift
    plus watch status
  - `.207` improved back out of the immediate fresh-repro bucket: the
    hub row/API still showed `online` on `0.1.16-dev-central`, local
    `/api/status` and `/api/config` still exposed
    `Erica's ?.?. Speaker`, the initial local root/status/config sweep
    returned clean `200` responses with local `/api/status` at
    `uptime_seconds=301`, and the immediate 5-cycle follow-up loop from
    `13:12:23Z` to `13:12:30Z` stayed clean while local `/api/status`
    climbed from `399` to `404`; the first root-page sample at
    `2.48 s` did not coincide with a reset or local API failure
  - `BUG-053` still remained unchanged on `.30`, `.225`, and `.207`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host

### 2026-05-14 live recheck addendum - renamed soak target dropped again after a fresh reboot, `.207` rebooted, and `.225` drifted harder

- The next live recheck around `2026-05-14T10:30Z` to `10:36Z` was
  materially worse than the clean `10:20Z`-`10:21Z` confirmation:
  - `.48` (`Rebooter - renamed test`) first appeared converged in the
    hub as `online` on `0.1.17-dev-central`, but local
    `/api/status` already reported `uptime_seconds=16`, confirming a
    fresh reboot had just happened
  - the immediate 5-cycle `.48` follow-up loop then looked healthy
    again: root-page reads about `0.11 s`-`0.25 s`,
    `/api/status` about `0.02 s`-`0.03 s`, `/api/config` about
    `0.03 s`-`0.08 s`, and uptime climbed from `105` to `110`
  - however, a few minutes later local `/`, `/api/status`, and
    `/api/config` on `.48` all fell back to full `10 s` read timeouts,
    and the rendered hub Devices page plus `/api/v1/admin/devices` had
    flipped the row to `offline` with last heartbeat
    `2026-05-14T10:33:02Z`; this materially re-strengthens `BUG-054`
    into a reboot/recovery-then-drop sequence
  - `.207` first hit an even clearer bad window: hub still showed
    `online` on `0.1.16-dev-central`, but local `/`, `/api/status`, and
    `/api/config` all timed out for the full `10 s` window
  - the immediate 5-cycle `.207` follow-up loop then recovered cleanly
    with uptime climbing from `17` to `24`, which is fresh reboot
    evidence rather than just latency; a later confirming
    `/api/status` read still showed only `uptime_seconds=81` and took
    about `1.39 s`, while the hub still presented `.207` as `online`
    with last heartbeat `2026-05-14T10:36:34Z`
  - `.225` also left the old watch-only bucket. In the initial `10:30Z`
    sweep, the hub still showed it `online` on `0.1.17-dev-central`
    while local `/` took about `7.81 s` and local `/api/status` plus
    `/api/config` both timed out after about `10 s`. By the final
    confirmation window the local device had recovered cleanly
    (`/` about `0.19 s`, `/api/status` about `0.04 s`,
    `/api/config` about `0.08 s`, `uptime_seconds=8095`), but the hub
    Devices page/API had already flipped it to `offline` with last
    heartbeat `2026-05-14T10:31:58Z`
  - `.30` was the only comparison target that stayed boring in this
    pass: hub remained `online` on `0.1.17-dev-central`, local surfaces
    stayed fast, and only the standing `BUG-053` name drift remained
- Net of this addendum:
  - there is still no evidence that the hub page and hub admin API
    disagree with each other when sampled together
  - but the live fleet state worsened again in the renamed-test soak
    window, and the renamed target is no longer just "intermittently
    slow" - it is back in a repeatable reboot/recovery/offline pattern
- The latest live recheck around `2026-05-14T10:20Z` to `10:21Z`
  still showed no fresh hub UI-vs-API drift, kept `.30` and `.225`
  locally healthy, and improved both `.48` and `.207` relative to the
  prior failure-heavy windows:
  - the rendered hub devices page still matched `/api/v1/admin/devices`
    on all comparison targets, so there was still no fresh
    central-side UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` did not reproduce in this short window: the initial
    local `.48` sweep returned `200` with the root page at about
    `0.35 s`, `/api/status` about `0.02 s`, and `/api/config` about
    `0.07 s`, and the immediate 5-cycle loop then stayed clean with
    root-page reads about `0.11 s`-`1.35 s` while the JSON endpoints
    stayed fast
  - the recent `.48` reboot/recovery evidence also improved materially:
    local `/api/status` reported `uptime_seconds=1263` in the initial
    sweep and then climbed monotonically from `1331` to `1336` across
    the immediate loop, so there was no fresh short-uptime signal
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.30` and `.225` both stayed healthy locally in this pass:
    `.30` returned `200` with the root page at about `0.16 s`,
    `/api/status` about `0.02 s`, and `/api/config` about `0.08 s`;
    `.225` returned `200` with the root page at about `0.32 s`,
    `/api/status` about `0.03 s`, and `/api/config` about `0.07 s`
  - `.207` improved relative to the earlier truncated-body and
    reboot-watch windows: the initial local sweep returned `200` with
    the root page at about `0.27 s`, `/api/status` about `0.05 s`,
    and `/api/config` about `0.07 s`, while the immediate 5-cycle loop
    stayed clean with root-page reads about `0.13 s`-`1.59 s`,
    `/api/status` about `0.02 s`-`0.08 s`, `/api/config` about
    `0.07 s`-`0.10 s`, and uptime climbing from `922` in the initial
    sweep to `997`-`1002` in the loop. That keeps `BUG-055` open only
    as a watch item in this sample rather than a freshly reproduced
    failure
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- Another live recheck around `2026-05-14T05:30Z` to `05:32Z`
  tightened the picture again:
  - the live hub devices page and hub admin API still matched on all
    comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - the stronger timeout shape of `BUG-054` did not reproduce in the
    follow-up 5-cycle `.48` loop; every pass returned `200`, with local
    root-page reads about `0.14 s`-`0.94 s`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - a new reliability issue is now concrete on `.207`: the local root
    UI took about `6.29 s` in the initial sweep and about `2.93 s` in
    cycle 2 of the immediate follow-up loop while local `/api/status`
    and `/api/config` stayed fast and the hub still showed the device
    `online`; this is now logged as `BUG-055`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T05:40Z` to `05:42Z`
  improved the immediate reliability picture again without changing the
  underlying drift bugs:
  - the live hub devices page and hub admin API still matched on all
    comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` did not reproduce in this window: the initial `.48`
    local sweep plus a fresh 5-cycle loop all returned `200`, with
    root-page reads about `0.10 s`-`0.18 s`
  - `BUG-055` also did not reproduce in this window: the initial `.207`
    sweep plus a fresh 5-cycle loop all returned `200`, with
    root-page reads about `0.13 s`-`0.19 s` while the JSON endpoints
    stayed fast
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The next live recheck around `2026-05-14T05:50Z` to `05:52Z`
  tightened that picture further:
  - the live hub devices page and hub admin API still matched on all
    comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` again did not reproduce in that pass: the initial `.48`
    sweep plus a fresh 5-cycle loop all returned `200`, with
    root-page reads about `0.10 s`-`0.19 s`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `BUG-055` weakly reproduced instead of fully disappearing: `.207`
    stayed `online`, its local JSON APIs stayed fast, but the first
    local root-page read in the follow-up loop stretched to about
    `1.40 s` before later cycles dropped back to about
    `0.09 s`-`0.16 s`; this is materially better than the earlier
    `6.29 s` and `2.93 s` stalls, but it keeps the bug open
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T06:00Z` to `06:01Z`
  improved the immediate reliability picture again without changing the
  underlying fleet drift:
  - the live hub devices page still matched the hub admin API on all
    comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` again did not reproduce in that pass: the initial `.48`
    sweep plus a fresh 5-cycle loop all returned `200`, with
    root-page reads about `0.10 s`-`0.16 s`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `BUG-055` improved materially versus the prior pass: `.207` had one
    slower first local root-page read at about `1.30 s` in the initial
    sweep, but the immediate 5-cycle follow-up loop stayed clean at
    about `0.10 s`-`0.16 s` while the JSON endpoints remained fast
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The next live recheck around `2026-05-14T06:10Z` to `06:11Z`
  improved the short-window reliability picture again without changing
  the underlying fleet drift:
  - the live hub devices page still matched the hub admin API on all
    comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` again did not reproduce in that pass: the initial `.48`
    local sweep plus a fresh 5-cycle loop all returned `200`, with
    root-page reads about `0.15 s`-`0.29 s`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `BUG-055` also did not reproduce in that pass: `.207` returned
    `200` in the initial sweep and the immediate 5-cycle loop stayed
    clean at about `0.10 s`-`0.18 s` while the JSON endpoints remained
    fast
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T06:20Z` to `06:21Z`
  kept the fleet-state picture unchanged but re-strengthened
  `BUG-054` as a latency issue:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `BUG-054` weakly reproduced again instead of staying absent: the
    initial `.48` local root-page fetch stretched to about `3.03 s`
    while `/api/status` stayed about `0.02 s` and `/api/config` about
    `0.07 s`; the immediate 5-cycle follow-up loop stayed at `200`, but
    cycle 1 still took about `1.02 s` before cycles 2-5 dropped back to
    about `0.09 s`-`0.13 s`
  - `BUG-055` did not reproduce in that pass: `.207` returned `200` in
    the initial sweep and the immediate 5-cycle loop stayed clean at
    about `0.11 s`-`0.14 s` while the JSON endpoints remained fast
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T06:31Z` to `06:33Z`
  kept the hub-vs-device state picture unchanged but strengthened
  `BUG-054` again:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `BUG-054` reproduced again in a stronger latency-only form: the
    first local `.48` root-page fetch took about `10.29 s` before
    returning `200`, while local `/api/status` stayed about `0.02 s`
    and local `/api/config` about `0.06 s`; the immediate 5-cycle
    follow-up loop then stayed clean with root-page reads about
    `0.10 s`-`0.43 s`
  - `BUG-055` did not reproduce in that pass: `.207` returned `200` in
    the initial sweep and the immediate 5-cycle loop stayed clean at
    about `0.10 s`-`0.23 s`; only one `/api/status` read stretched to
    about `0.34 s`, which was not enough to call a fresh stall
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T06:40Z` to `06:42Z`
  improved the short-window reliability picture again without changing
  the underlying fleet drift:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `BUG-054` did not reproduce in that pass: the initial `.48` local
    sweep plus a fresh 5-cycle loop all returned `200`, with
    root-page reads about `0.10 s`-`0.37 s`, `/api/status` about
    `0.02 s`-`0.03 s`, and `/api/config` about `0.03 s`-`0.09 s`
  - `BUG-055` also did not reproduce in that pass: `.207` returned
    `200` in the initial sweep and the immediate 5-cycle loop stayed
    clean at about `0.11 s`-`0.20 s` while the JSON endpoints remained
    fast
  - `.225` had one slower initial local root-page fetch at about
    `1.41 s` while its JSON endpoints remained fast; not enough yet for
    a fresh bug, but worth watching if future passes repeat the pattern
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T06:50Z` to `06:51Z`
  kept the hub-vs-device state picture unchanged and improved the
  renamed-test path again, but it found a new `.207` latency shape:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` did not reproduce in that pass: the initial `.48` local
    sweep plus a fresh 5-cycle loop all returned `200`, with
    root-page reads about `0.10 s`-`0.35 s`, `/api/status` about
    `0.02 s`-`0.04 s`, and `/api/config` about `0.07 s`-`0.08 s`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` improved relative to the prior watch window: its initial
    local root-page read was about `0.31 s` instead of `1.41 s`, while
    its JSON endpoints remained fast
  - `BUG-055` shifted shape instead of staying fully absent: `.207`
    stayed `online` on `0.1.16-dev-central`, local root-page reads
    stayed about `0.10 s`-`0.31 s`, and local `/api/config` stayed
    about `0.07 s`-`0.09 s`, but cycle 1 of the immediate follow-up
    loop had a slower `/api/status` read at about `4.85 s` before
    cycles 2-5 returned to about `0.03 s`-`0.07 s`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T07:02Z` to `07:04Z`
  kept the hub-vs-device state picture unchanged and improved the
  short-window reliability picture again:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` again did not reproduce in that pass: the initial `.48`
    local root-page read returned `200` in about `0.63 s`, and the
    immediate 5-cycle follow-up loop stayed clean with root-page reads
    about `0.10 s`-`0.17 s`, `/api/status` about `0.02 s`-`0.04 s`,
    and `/api/config` about `0.06 s`-`0.08 s`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` stayed only mildly slow on first contact: its initial local
    root-page read was about `0.53 s` while its JSON endpoints remained
    fast, so the earlier watch item did not strengthen into a fresh bug
  - `BUG-055` improved relative to the prior `.207` `/api/status`
    slowdown but did not fully disappear: `.207` stayed `online` on
    `0.1.16-dev-central`, the hub devices row still showed the pending
    upgrade affordance toward `0.1.17-dev-central`, the initial local
    sweep stayed fast, but cycle 1 of the immediate follow-up loop had
    a weaker `1.46 s` root-page read before cycles 2-5 returned to
    about `0.10 s`-`0.16 s`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T07:10Z` to `07:12Z`
  kept the hub-vs-device state picture unchanged again, improved `.207`
  cleanly, and left only a weaker `.48` first-hit delay:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` improved relative to the prior watch window again: its
    initial local root-page read was about `0.22 s`, while its JSON
    endpoints remained fast
  - `BUG-055` improved in this pass: `.207` stayed `online` on
    `0.1.16-dev-central`, the hub row still advertised the pending
    upgrade toward `0.1.17-dev-central`, the initial local sweep stayed
    fast, and the immediate 5-cycle follow-up loop stayed clean with
    root-page reads about `0.10 s`-`0.27 s`, `/api/status` about
    `0.02 s`, and `/api/config` about `0.07 s`-`0.09 s`
  - `BUG-054` stayed narrower than the earlier timeout windows but did
    not fully clear: `.48` returned `200` in the initial local sweep
    with the root page at about `0.39 s`, `/api/status` about `0.04 s`,
    and `/api/config` about `0.06 s`, but cycle 1 of the immediate
    follow-up loop still hit a weaker `1.20 s` root-page read before
    cycles 2-5 returned to about `0.13 s`-`0.20 s`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T07:21Z` to `07:22Z`
  kept the hub-vs-device state picture unchanged and improved the
  short-window reliability picture again:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` did not reproduce in that pass: the initial `.48` local
    sweep returned `200` in about `0.17 s`, and the immediate 5-cycle
    follow-up loop stayed clean with root-page reads about
    `0.10 s`-`0.20 s`, `/api/status` about `0.02 s`-`0.03 s`, and
    `/api/config` about `0.07 s`-`0.08 s`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` stayed mild rather than strengthening into a new issue: its
    initial local root-page read was about `0.20 s`, while its JSON
    endpoints remained fast
  - `BUG-055` also did not reproduce in that pass: `.207` stayed
    `online` on `0.1.16-dev-central`, the hub row still advertised the
    pending upgrade toward `0.1.17-dev-central`, the initial local
    sweep stayed fast, and the immediate 5-cycle follow-up loop stayed
    clean with root-page reads about `0.10 s`-`0.23 s`, `/api/status`
    about `0.02 s`-`0.03 s`, and `/api/config` about `0.06 s`-`0.09 s`
  - `.30` also stayed locally healthy despite the ongoing name drift:
    its root page returned `200` in about `0.21 s`, `/api/status` in
    about `0.02 s`, and `/api/config` in about `0.08 s`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T07:33Z` to `07:35Z`
  kept the hub-vs-device state picture unchanged but re-strengthened
  `BUG-054` again:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` still converged across hub UI/API and local status/config on
    identity and firmware once it answered:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `BUG-054` reproduced again in the stronger first-contact timeout
    shape: while the hub still showed `.48` `online` on
    `0.1.17-dev-central`, the first local sweep timed out for the full
    10 s window on `/`, `/api/status`, and `/api/config`; an immediate
    5-cycle follow-up loop then recovered cleanly at `200`, with
    root-page reads about `0.10 s`-`0.14 s`, `/api/status` about
    `0.02 s`-`0.11 s`, and `/api/config` about `0.03 s`-`0.09 s`
  - `BUG-055` did not strengthen in that pass: `.207` stayed `online`
    on `0.1.16-dev-central`, the hub row still advertised the pending
    upgrade toward `0.1.17-dev-central`, the initial local sweep stayed
    fast, and the immediate 5-cycle follow-up loop stayed clean with
    root-page reads about `0.11 s`-`0.37 s`, `/api/status` about
    `0.02 s`-`0.04 s`, and `/api/config` about `0.07 s`-`0.09 s`
  - `.225` weakened slightly versus the immediately prior clean window
    but stayed only a watch item: its initial local root-page read was
    about `1.12 s` while its JSON endpoints remained fast
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T07:40Z` to `07:42Z`
  kept the hub-vs-device state picture unchanged again, left `.48` in
  the weaker latency-only bucket, re-strengthened `.207`, and
  strengthened the `.225` watch item:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `BUG-054` stayed weaker than the earlier full-timeout windows but
    still reproduced as first-hit local UI latency: the initial `.48`
    root-page read returned `200` in about `0.37 s`, then cycle 2 of
    the immediate 5-cycle follow-up loop stretched to about `1.54 s`
    before later cycles returned to about `0.16 s`-`0.26 s`; local
    `/api/status` and `/api/config` stayed fast
  - `BUG-055` shifted shape and strengthened again: while the hub still
    showed `.207` `online` on `0.1.16-dev-central` with the pending
    upgrade affordance toward `0.1.17-dev-central`, the first local
    root-page request failed after about `4.22 s` with a truncated-body
    read, while local `/api/status` and `/api/config` stayed fast; an
    immediate 5-cycle follow-up loop then recovered cleanly at about
    `0.12 s`-`0.23 s` on the root page
  - `.225` strengthened as a watch item rather than clearing: its first
    local root-page read stretched to about `2.10 s` while its JSON
    endpoints remained fast, but an immediate 5-cycle follow-up loop
    then returned to about `0.11 s`-`0.31 s`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T07:51Z` to `07:56Z`
  kept the hub-vs-device state picture unchanged again, improved `.48`,
  left `.207` in a weaker non-repeating bucket, and shifted the `.225`
  watch signal from root-page latency to intermittent `/api/status`
  latency:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` improved relative to the earlier timeout and `1.54 s`
    follow-up windows: the initial `.48` root-page read returned `200`
    in about `0.83 s`, then a focused 5-cycle loop stayed clean at
    about `0.10 s`-`0.22 s` on the root page, `0.02 s`-`0.03 s` on
    `/api/status`, and `0.07 s`-`0.09 s` on `/api/config`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.207` improved versus the earlier truncated-body failure: its
    initial local sweep stayed fast, one later focused timing loop hit
    a slower `2.53 s` root-page sample while `/api/status` and
    `/api/config` remained fast, and an immediate 3-cycle confirmation
    loop did not repeat the slowdown
  - `.225` now has repeated watch evidence on a different endpoint:
    the initial local sweep stayed mostly healthy, but a focused
    5-cycle loop hit a slower `/api/status` read at about `1.35 s` and
    the immediate 3-cycle confirmation loop hit another `/api/status`
    read at about `2.64 s` while the root page and `/api/config`
    remained much faster; a later 8-cycle `/api/status` loop then
    returned to about `0.02 s`-`0.13 s`
  - `.30` remained locally healthy despite the ongoing name drift
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T08:00Z` to `08:04Z`
  kept the hub-vs-device state picture unchanged again, improved `.48`
  and `.225`, and narrowed `.207` back to a weaker non-repeating wobble:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` improved again: the initial `.48` local sweep returned
    `200` in about `0.21 s` on the root page, about `0.02 s` on
    `/api/status`, and about `0.08 s` on `/api/config`; the immediate
    5-cycle follow-up loop then stayed clean at about `0.10 s`-`0.22 s`
    on the root page, `0.02 s`-`0.04 s` on `/api/status`, and
    `0.07 s`-`0.09 s` on `/api/config`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` improved versus the earlier repeated `/api/status` watch
    window: the initial local sweep returned `200` with the root page
    at about `0.31 s`, `/api/status` about `0.05 s`, and `/api/config`
    about `0.09 s`, and the immediate 5-cycle follow-up loop then
    stayed clean with `/api/status` about `0.02 s`-`0.03 s`
  - `.207` improved versus the prior truncated-body failure and did not
    re-strengthen in the extra confirmation loop, but it did not fully
    clear: the initial local sweep stayed healthy, the immediate
    5-cycle follow-up loop hit one slower root-page read at about
    `1.24 s` and one separate slower `/api/config` read at about
    `1.59 s`, and an immediate 8-cycle confirmation loop then stayed
    clean with root-page reads about `0.11 s`-`0.21 s`,
    `/api/status` about `0.02 s`-`0.05 s`, and `/api/config` about
    `0.04 s`-`0.08 s`
  - `.30` remained locally healthy despite the ongoing name drift
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T08:10Z` to `08:13Z`
  re-strengthened `.48`, caught a short central/device recovery wobble
  on `.225`, and improved `.207` back toward the weaker-latency bucket:
  - the rendered live Devices page still matched
    `/api/v1/admin/devices` on the recheck pass, so there was still no
    fresh hub UI-vs-API drift
  - `.48` re-strengthened again under `BUG-054`: the first local sweep
    timed out for the full `10 s` window on `/`, `/api/status`, and
    `/api/config`, while the first hub admin API sample still showed
    the row as `offline` with `last_heartbeat_at`
    `2026-05-14T08:05:17Z`; an immediate 5-cycle follow-up loop then
    recovered cleanly at about `0.10 s`-`0.16 s` on the root page,
    `0.02 s`-`0.04 s` on `/api/status`, and `0.07 s`-`0.09 s` on
    `/api/config`, and the next hub UI/API fetch around `08:12Z`
    converged back to `Rebooter - renamed test` / `online` /
    `0.1.17-dev-central`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` showed a stronger watch-only recovery wobble: the first hub
    admin API sample still held it at `offline` with
    `last_heartbeat_at` `2026-05-14T08:01:06Z`, while the local root
    page took about `6.04 s` and local `/api/status` still returned
    `200` in about `0.02 s` with `central_state="idle"` and
    `central_heartbeat_age_seconds=3`; the immediate 5-cycle follow-up
    loop then stayed clean and the later hub UI/API recheck had already
    converged back to `online`
  - `.207` improved relative to the prior truncated-body and
    `1.24 s`-`1.59 s` wobble window: the initial local sweep stayed
    healthy, and the immediate 5-cycle follow-up loop hit only one
    slower root-page read at about `1.46 s` before later cycles
    returned to about `0.10 s`-`0.13 s` with `/api/status` and
    `/api/config` staying fast
  - `.30` remained locally healthy despite the ongoing name drift
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T08:20Z` to `08:22Z`
  kept the hub-vs-device state picture unchanged again, improved `.48`
  and `.207` versus their earlier stronger failure shapes, and
  re-strengthened `.225` into the strongest watch window of the day:
  - the rendered live Devices page still matched
    `/api/v1/admin/devices` on all comparison targets, so there was
    still no fresh hub UI-vs-API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` improved relative to the earlier full-timeout windows:
    the initial `.48` local sweep returned `200` in about `0.39 s` on
    the root page, about `0.02 s` on `/api/status`, and about `0.09 s`
    on `/api/config`; the immediate 5-cycle follow-up loop then stayed
    clean at about `0.15 s`-`0.17 s` on the root page and
    `0.02 s`-`0.04 s` on `/api/status`, but cycle 5 of `/api/config`
    still stretched to about `2.58 s`, so the bug narrowed without
    fully clearing
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` materially worsened relative to the earlier watch-only
    windows while the hub still showed it `online` on
    `0.1.17-dev-central`: the initial local root page took about
    `8.12 s`, the first local `/api/status` probe then failed after
    about `9.55 s` with a connection reset, and local `/api/config`
    still returned `200` in about `0.08 s`; an immediate 5-cycle
    follow-up loop then recovered cleanly at about `0.10 s`-`0.19 s`
    on the root page, `0.03 s`-`0.04 s` on `/api/status`, and
    `0.07 s`-`0.12 s` on `/api/config`
  - `.207` improved relative to the earlier truncated-body and root
    timeout windows: the initial local sweep stayed healthy, and the
    immediate 5-cycle follow-up loop hit only one slower
    `/api/status` read at about `1.38 s` before later cycles returned
    to about `0.02 s`-`0.03 s` while the root page and `/api/config`
    stayed much faster
  - `.30` remained locally healthy despite the ongoing name drift
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T08:31Z` to `08:33Z`
  kept the hub-vs-device state picture unchanged again, improved `.48`
  versus the earlier timeout / slow-config windows, and weakened the
  prior `.225` and `.207` wobble signals back toward clean samples:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh hub UI-vs-API
    drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` improved again relative to the earlier timeout and
    `2.58 s` `/api/config` windows: the initial `.48` local sweep
    returned `200` with the root page at about `2.05 s`,
    `/api/status` about `0.04 s`, and `/api/config` about `0.07 s`;
    the immediate 5-cycle follow-up loop then stayed clean at about
    `0.10 s`-`0.64 s` on the root page, `0.02 s`-`0.04 s` on
    `/api/status`, and `0.07 s`-`0.09 s` on `/api/config`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` improved materially versus the prior `8.12 s` root-page /
    `9.55 s` `/api/status` watch window: the initial local sweep
    returned `200` with the root page at about `0.20 s`,
    `/api/status` about `0.02 s`, and `/api/config` about `0.07 s`,
    and the immediate 5-cycle follow-up loop then stayed clean with
    root-page reads about `0.10 s`-`0.19 s`, `/api/status` about
    `0.02 s`-`0.05 s`, and `/api/config` about `0.02 s`-`0.08 s`
  - `.207` also improved relative to the earlier truncated-body, root
    timeout, and `1.38 s` `/api/status` wobble windows: the initial
    local sweep returned `200` with the root page at about `0.18 s`,
    `/api/status` about `0.02 s`, and `/api/config` about `0.09 s`,
    and the immediate 5-cycle follow-up loop then stayed clean with
    root-page reads about `0.10 s`-`0.19 s`, `/api/status` about
    `0.02 s`, and `/api/config` about `0.06 s`-`0.08 s`
  - `.30` remained locally healthy despite the ongoing name drift
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T08:40Z` to `08:41Z`
  kept the hub-vs-device state picture unchanged again, improved
  `.225` back toward clean samples, and re-strengthened `.207` into a
  stronger first-hit root-page wobble:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh hub UI-vs-API
    drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` stayed in the weaker latency-only bucket: the initial
    `.48` local sweep returned `200` with the root page at about
    `0.31 s`, `/api/status` about `0.02 s`, and `/api/config` about
    `0.08 s`; the immediate 5-cycle follow-up loop then hit one slower
    root-page read at about `1.44 s` on cycle 1 before cycles 2-5
    returned to about `0.10 s`-`0.12 s` while the JSON endpoints stayed
    fast
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` improved materially relative to the prior `8.12 s`
    root-page / `9.55 s` `/api/status` watch window: the initial local
    sweep returned `200` with the root page at about `0.25 s`,
    `/api/status` about `0.02 s`, and `/api/config` about `0.02 s`,
    and the immediate 5-cycle follow-up loop then stayed clean
  - `.207` re-strengthened relative to the prior clean `08:31Z`-
    `08:33Z` window: the initial local sweep returned `200` with the
    root page at about `0.37 s`, `/api/status` about `0.03 s`, and
    `/api/config` about `0.08 s`, but cycle 1 of the immediate
    5-cycle follow-up loop hit a slower `3.77 s` root-page read before
    cycles 2-5 returned to about `0.10 s`-`0.11 s` while the JSON
    endpoints stayed fast
  - `.30` remained locally healthy despite the ongoing name drift
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T08:50Z` to `08:56Z`
  still showed no fresh hub UI-vs-API drift, but it did catch a short
  recovery wobble on both `.48` and `.207` before they reconverged:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` briefly re-strengthened again: the first `.48` local
    sweep hit about `9.82 s` on the root page, a full `10.02 s`
    timeout on `/api/status`, and about `2.69 s` on `/api/config`
    while the hub still showed the device `online`; an immediate retry
    sweep then recovered to about `0.19 s` on `/`, `0.02 s` on
    `/api/status`, and `0.03 s` on `/api/config`, and only cycle 1 of
    the immediate 5-cycle follow-up loop still stretched to about
    `2.80 s` on `/api/status` before later cycles and a final 3-cycle
    spot-check stayed clean
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` weakened back into a milder watch-only root delay instead
    of repeating the earlier root-plus-status failure shape: one retry
    sweep hit about `4.05 s` on the root page while `/api/status` and
    `/api/config` stayed fast, and the immediate 5-cycle follow-up loop
    then stayed clean
  - `.207` also re-strengthened briefly before collapsing again: the
    first local sweep hit a full `10.28 s` timeout on `/` and about
    `4.86 s` on `/api/status` while `/api/config` still returned `200`
    in about `0.08 s`; the immediate retry sweep, the 5-cycle
    confirmation loop, and the final 3-cycle spot-check then all stayed
    clean
  - `.30` remained locally healthy despite the ongoing name drift
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T09:00Z` to `09:01Z`
  kept the hub-vs-device state picture unchanged again and added a
  cleaner short window on all three watched live devices:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` did not reproduce in this short window: the initial `.48`
    local sweep returned `200` with the root page at about `0.25 s`,
    `/api/status` about `0.04 s`, and `/api/config` about `0.07 s`,
    and the immediate 5-cycle follow-up loop then stayed clean with
    root-page reads about `0.10 s`-`0.19 s` while the JSON endpoints
    stayed fast
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` improved materially relative to the prior `4.05 s`
    watch-only root delay: the initial local sweep returned `200` with
    the root page at about `0.20 s`, `/api/status` about `0.03 s`, and
    `/api/config` about `0.09 s`, and the immediate 5-cycle follow-up
    loop then stayed clean with only mild root-page variation
  - `.207` improved materially relative to the prior timeout and
    `/api/status` wobble window: the initial local sweep returned `200`
    with the root page at about `0.20 s`, `/api/status` about `0.02 s`,
    and `/api/config` about `0.07 s`, and the immediate 5-cycle
    follow-up loop then stayed clean with fast local UI/API samples
  - `.30` remained locally healthy despite the ongoing name drift
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T09:13Z` to `09:14Z`
  kept the hub-vs-device state picture unchanged again, added another
  clean short window on `.48` and `.207`, and surfaced weaker root-page
  latency on `.225` plus a new one-off root-page wobble on `.30`:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` did not reproduce in this window: the initial `.48` local
    sweep returned `200` with the root page at about `0.20 s`,
    `/api/status` about `0.02 s`, and `/api/config` about `0.07 s`,
    and the immediate 5-cycle follow-up loop then stayed clean with
    fast local UI/API samples
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` weakened only into a mild watch-only root delay: the
    initial local sweep stayed healthy, but cycle 1 of the immediate
    5-cycle follow-up loop hit a slower `3.01 s` root-page read before
    later cycles returned to normal while `/api/status` and
    `/api/config` stayed fast
  - `.207` stayed clean in both the initial sweep and immediate
    5-cycle follow-up loop, while the hub still kept it `online` on
    `0.1.16-dev-central`
  - `.30` stayed reachable despite the ongoing name drift, but the
    first local root-page read stretched to about `4.23 s` while local
    `/api/status` and `/api/config` stayed fast. Keep this below fresh
    bug level unless a later soak repeats it
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T09:20Z` to `09:21Z`
  kept the hub-vs-device state picture unchanged again, cleared the
  prior `.30` and `.225` watch-only root delays, and showed fresh
  evidence that `.207` likely rebooted recently but recovered cleanly:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` stayed in the weaker latency-only bucket: the initial
    `.48` local sweep returned `200` with the root page at about
    `1.33 s`, `/api/status` about `0.02 s`, and `/api/config` about
    `0.08 s`, and the immediate 5-cycle follow-up loop then stayed
    clean with root-page reads about `0.10 s`-`0.60 s`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` improved materially relative to the prior `3.01 s`
    watch-only root delay: the initial local sweep returned `200` with
    the root page at about `0.34 s`, `/api/status` about `0.02 s`, and
    `/api/config` about `0.07 s`, and the immediate 5-cycle follow-up
    loop then stayed clean
  - `.207` stayed fast throughout the initial sweep and immediate
    5-cycle follow-up loop while the hub still kept it `online` on
    `0.1.16-dev-central`, but local `/api/status` reported
    `uptime_seconds=24` and then `94` on a confirming fetch about a
    minute later. Treat that as fresh evidence of a recent
    reboot/recovery window even though the device had already
    reconverged by the time of the soak pass
  - `.30` improved materially relative to the prior one-off `4.23 s`
    root-page wobble: the first local root-page read returned in about
    `0.21 s` with fast local JSON endpoints, so the earlier delay did
    not repeat
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T09:29Z` to `09:31Z`
  kept the hub-vs-device state picture unchanged again, kept `.30` and
  `.225` in the improved bucket, but re-strengthened `BUG-054` in a new
  lighter-weight shape and reinforced the `.207` reboot signal:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` shifted out of the clean bucket again: the initial `.48`
    local sweep returned `200` with the root page at about `0.38 s`,
    `/api/status` about `0.03 s`, and `/api/config` about `0.07 s`,
    but cycle 4 of the immediate 5-cycle follow-up loop hit a
    `3.03 s` truncated-body `ChunkedEncodingError` on `/api/config`
    while `/` and `/api/status` stayed fast before and after
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` stayed improved: the initial local sweep returned `200`
    with the root page at about `0.28 s`, `/api/status` about `0.02 s`,
    and `/api/config` about `0.07 s`, and the immediate 5-cycle
    follow-up loop then stayed clean
  - `.207` stayed fast throughout the initial sweep and immediate
    5-cycle follow-up loop while the hub still kept it `online` on
    `0.1.16-dev-central`, but local `/api/status` reported
    `uptime_seconds=109` and then `206` on a confirming fetch about a
    minute later. That strengthens the evidence of a recent
    reboot/recovery window rather than clearing it
  - `.30` stayed improved relative to the prior one-off `4.23 s`
    root-page wobble: the first local root-page read returned in about
    `0.12 s` with fast local JSON endpoints
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T09:39Z` to `09:42Z`
  still showed no fresh hub UI-vs-API drift, kept `.48` and `.225` in
  the improved bucket, but re-strengthened `.207` and surfaced fresh
  recent-reboot evidence on `.30`:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` improved relative to the prior truncated-body
    `/api/config` failure: the initial `.48` local sweep hit a slower
    `2.97 s` root-page read, but `/api/status` stayed about `0.02 s`,
    `/api/config` stayed about `0.08 s`, and the immediate 5-cycle
    follow-up loop then stayed clean with root-page reads about
    `0.11 s`-`0.17 s`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` stayed improved: the initial local sweep returned `200` with
    the root page at about `0.43 s`, `/api/status` about `0.03 s`, and
    `/api/config` about `0.07 s`, and the immediate 5-cycle follow-up
    loop then stayed clean
  - `.207` re-strengthened again without a new reboot: the initial
    local sweep stayed healthy and local `/api/status` later confirmed
    `uptime_seconds=814`, but cycle 2 of the immediate 5-cycle
    follow-up loop hit a slower `4.23 s` root-page read before later
    cycles returned to normal while `/api/status` and `/api/config`
    stayed fast
  - `.30` stayed reachable despite the ongoing name drift, but local
    `/api/status` reported `uptime_seconds=155` and then `279` on a
    confirming fetch shortly afterward. Treat that as concrete evidence
    of a recent reboot/recovery window even though the hub still showed
    the device `online`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T09:50Z` to `09:53Z`
  still showed no fresh hub UI-vs-API drift and kept `.225` and `.207`
  in the improved bucket, but it materially re-strengthened `BUG-054`
  on the renamed soak target:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` stayed converged across hub UI/API and local status/config as
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, but
    the first local root-page probe timed out after about `10.26 s`
    while local `/api/status` then returned `200` in about `1.59 s`
    with `uptime_seconds=10` and local `/api/config` returned `200` in
    about `0.07 s`; the immediate 5-cycle follow-up loop then stayed
    clean with root-page reads about `0.10 s`-`0.19 s`, and a later
    confirming `/api/status` read reported `uptime_seconds=98`. That is
    concrete reboot/recovery evidence rather than just a slower first
    read
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.225` stayed improved: the initial local sweep returned `200`
    with the root page at about `0.19 s`, `/api/status` about `0.02 s`,
    and `/api/config` about `0.07 s`, with no fresh regression signal
  - `.207` also stayed improved relative to the earlier root wobble:
    the initial local sweep returned `200` with the root page at about
    `0.35 s`, `/api/status` about `0.02 s`, `/api/config` about
    `0.09 s`, and local `/api/status` reported `uptime_seconds=1362`
  - `.30` improved relative to the earlier short-uptime watch window:
    confirming `/api/status` reads reported `uptime_seconds=914` and
    then `949`, so the prior reboot evidence did not repeat in this pass
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T10:01Z` to `10:03Z`
  still showed no fresh hub UI-vs-API drift, kept `.30` and `.225`
  locally healthy, and gave `.48` a clean short follow-up window, but
  it strengthened `.207` into a more concrete response-integrity
  failure:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` did not reproduce in the immediate focused follow-up
    loop: the initial local sweep returned `200` with the root page at
    about `0.28 s`, `/api/status` about `0.03 s`, and `/api/config`
    about `0.08 s`, and the immediate 5-cycle loop then stayed clean
    with root-page reads about `0.12 s`-`0.22 s` plus fast JSON
    endpoints
  - the recent `.48` reboot evidence still did not clear, though:
    local `/api/status` reported `uptime_seconds=92` in the initial
    sweep, then `180`-`186` across the immediate 5-cycle loop, and
    `192` on a later confirming read. That means the hub continued to
    show the device simply `online` even though the reboot/recovery
    window was still only a few minutes old
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.30` and `.225` both stayed healthy locally in this pass:
    `.30` returned `200` with the root page at about `0.10 s`,
    `/api/status` about `0.05 s`, and `/api/config` about `0.08 s`;
    `.225` returned `200` with the root page at about `0.33 s`,
    `/api/status` about `0.03 s`, and `/api/config` about `0.08 s`
  - `BUG-055` strengthened beyond the prior latency-only root wobble:
    the live hub devices page and `/api/v1/admin/devices` still showed
    `.207` `online` on `0.1.16-dev-central`, while local
    `/api/status` and `/api/config` still exposed
    `Erica's ?.?. Speaker`, but the first local root-page probe failed
    after about `3.25 s` with a truncated-body `ChunkedEncodingError`
    (`IncompleteRead(2680 bytes read, 13343 more expected)`) before the
    immediate 5-cycle follow-up loop and a later `/api/status` confirm
    both stayed clean. That is stronger evidence of transient local UI
    response corruption than the earlier `4.23 s` root delay, although
    it still did not coincide with a fresh reboot
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T10:10Z` to `10:13Z`
  still showed no fresh hub UI-vs-API drift, kept `.30` and `.225`
  locally healthy, but re-strengthened `.48` in a more important
  non-reboot shape and surfaced fresh reboot evidence on `.207`:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config:
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` strengthened beyond the earlier reboot-window framing:
    local `/api/status` reported `uptime_seconds=711` on the first `.48`
    sweep and then `798`-`807` across the immediate 5-cycle follow-up,
    but cycle 4 of that same loop hit a `4.42 s` truncated-body
    `ChunkedEncodingError` on the root page (`14891 bytes read, 1132
    more expected`) while `/api/status` and `/api/config` still
    returned fast `200` responses. That is concrete evidence that the
    renamed soak target still has a local UI response-integrity failure
    even when it is not in a fresh reboot/recovery window
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.30` stayed locally healthy despite the ongoing name drift: the
    root page returned `200` in about `0.20 s`, `/api/status` in about
    `0.02 s` with `uptime_seconds=2013`, and `/api/config` in about
    `0.08 s`
  - `.225` stayed locally healthy overall: the first root-page read
    stretched to about `2.24 s`, but the immediate 5-cycle follow-up
    loop stayed at `200` with one slower first hit at about `1.22 s`
    and later root-page reads back around `0.12 s`-`0.22 s` while the
    JSON endpoints stayed fast
  - `BUG-055` did not reproduce as a truncated-body failure in that
    pass, but `.207` now shows fresh reboot evidence after the prior
    `10:01Z`-`10:03Z` window: the live hub devices page still showed
    `.207` `online` on `0.1.16-dev-central` with the pending-upgrade
    affordance, while local `/api/status` reported `uptime_seconds=372`
    on the first sweep and then `467`-`473` across the immediate loop.
    That means `.207` rebooted sometime after the earlier pass even
    though the hub never exposed a degraded state
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T10:39Z` to `10:42Z`
  showed the renamed soak target and the earlier `.225` drift target
  both recover back into the hub's `online` set, but the soak picture
  is still not clean:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` recovered from the earlier offline dip and re-converged
    across hub UI/API and local status/config as
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` stayed concrete in two ways at once: the first local
    `.48` `/api/status` read already showed only `uptime_seconds=232`,
    then the immediate 5-cycle local loop climbed from `298` to `326`
    but still hit a `3.14 s` truncated-body `ChunkedEncodingError` on
    the root page during cycle 3 while `/api/status` and `/api/config`
    stayed fast. That means the renamed soak target is still rebooting
    and still intermittently corrupting its local UI even after
    recovery
  - `.225` improved relative to the earlier hub-vs-local drift: the hub
    devices page/API and local status/config all returned to an
    `online` / `0.1.17-dev-central` state, and the immediate 5-cycle
    follow-up loop stayed clean
  - `.225` is not fully healthy, though: local `/api/status` first
    reported `uptime_seconds=90`, then climbed from `153` to `181`
    across the follow-up loop, with a later confirm at `217`. That is
    fresh reboot evidence even though the hub already looked healthy
    again
  - `.207` also strengthened back into a more concrete reboot/recovery
    masking problem: the hub devices page/API kept showing it `online`
    on `0.1.16-dev-central`, but the initial local root-page read took
    about `11.35 s` and local `/api/status` showed only
    `uptime_seconds=9`; the focused follow-up loop then climbed from
    `61` to `89`, with one more slower root-page sample at about
    `2.10 s`, and a later confirm at `128`
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.30` had no fresh bug-level regression in this pass beyond the
    standing name drift
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T10:49Z` to `10:52Z`
  showed no fresh hub UI-vs-API drift and improved the short-window
  reliability picture on the renamed soak target plus the two recent
  reboot-watch devices:
  - the live hub devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` stayed converged across hub UI/API and local status/config as
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`
  - `BUG-054` improved in this pass: the initial local `.48` sweep
    returned `200` with the root page at about `0.16 s`,
    `/api/status` about `0.03 s`, and `/api/config` about `0.08 s`,
    while local `/api/status` reported `uptime_seconds=828`; the
    immediate 5-cycle follow-up loop then kept returning `200` and
    climbed from `877` to `884` without a truncated body or timeout.
    Keep the bug open because cycle 1 of the loop still had a slower
    `2.20 s` root-page read before later cycles returned to about
    `0.12 s`-`0.15 s`
  - `.225` stayed in its standing desired-name drift shape, but its
    reliability improved: hub devices page/API still showed
    `Erica's F.R Speaker` `online` on `0.1.17-dev-central` while local
    `/api/status` + `/api/config` still exposed `Rebooter`; the initial
    local `/api/status` read reported `uptime_seconds=682`, then the
    immediate 5-cycle loop climbed from `731` to `739` with clean local
    root/API responses throughout
  - `BUG-055` improved in the same short window: hub devices page/API
    still showed `.207` `online` on `0.1.16-dev-central` with the
    pending-upgrade affordance, while local `/api/status` +
    `/api/config` still exposed `Erica's ?.?. Speaker`; however, local
    `/api/status` reported `uptime_seconds=591` on the initial sweep
    and then climbed from `640` to `647` across the immediate loop with
    root-page reads about `0.10 s`-`0.20 s` and no repeated timeout or
    response-integrity failure
  - `.30` still had no fresh regression beyond the standing desired-name
    drift; local `/api/status` returned `200` in about `0.02 s` with
    `uptime_seconds=4351`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T11:03Z` to `11:05Z`
  showed the hub Devices page still matching `/api/v1/admin/devices`
  on all comparison targets, but the device-side reliability picture
  worsened again on `.48` and `.225`:
  - there was still no fresh central-side hub UI/API drift
  - `.48` first looked converged again across hub UI/API and local
    status/config as `Rebooter - renamed test` / `online` /
    `0.1.17-dev-central`, with local `/api/status` reporting
    `uptime_seconds=1607`
  - `BUG-054` then re-strengthened immediately: cycle 1 of the focused
    local loop hit full `10 s` read timeouts on `/`, `/api/status`, and
    `/api/config`; cycle 2 hit another full `10 s` root-page timeout;
    cycle 4 hit a truncated-body `ChunkedEncodingError` on `/`; and the
    returning `/api/status` samples had already fallen to
    `uptime_seconds=10`, `10`, `14`, and `14`, with a later confirm at
    only `17` and `health_state="unknown"`. A post-loop hub refresh
    still showed `.48` `online` with last heartbeat
    `2026-05-14T11:04:35Z`, so the hub again masked the reboot/recovery
    plus local-UI corruption window
  - `.225` also regressed again as a reboot-watch device: the hub row
    stayed `online` on `0.1.17-dev-central`, but local `/api/status`
    already showed only `uptime_seconds=37` on the initial sweep and
    then climbed only from `114` to `115` in the focused loop, with a
    later confirm at `117`
  - `.207` improved materially in the same window: hub UI/API still
    showed `online` on `0.1.16-dev-central` while local root-page reads
    stayed at `200` and local `/api/status` held steady from
    `uptime_seconds=1371` to `1450` without a timeout or
    truncated-body repro
  - `BUG-053` still remained unchanged on `.30`, `.225`, and `.207`
  - `.30` still had no fresh regression beyond the standing desired-name
    drift; local `/api/status` returned `200` in about `0.03 s` with
    `uptime_seconds=5131`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T11:12:59Z` to `11:14:42Z`
  still showed no fresh hub UI-vs-API drift, and it changed the
  current interpretation of the reliability failures on `.48` and
  `.207`:
  - the live hub Devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - unlike the earlier masked-online windows, the initial hub view in
    this pass showed both `.48` and `.207` `offline` with stale
    heartbeats while `.30` and `.225` stayed `online` and `.69` stayed
    `offline`
  - `BUG-054` strengthened again on `.48`: the initial local root page
    still returned `200`, but only after about `4.73 s`, while local
    `/api/status` and `/api/config` both timed out at `10 s`; the
    immediate 5-cycle local follow-up loop then recovered fully, with
    fast `200` root/status/config responses and local `/api/status`
    showing `Rebooter - renamed test` / `0.1.17-dev-central` at only
    `uptime_seconds=63`-`64`; a post-loop hub refresh had already moved
    `.48` back to `online` with last heartbeat `2026-05-14T11:14:22Z`
  - `BUG-055` also strengthened again on `.207`: the initial local root
    page timed out after about `10 s`, local `/api/status` reset the
    connection after about `9.76 s`, and only local `/api/config`
    returned `200`, taking about `3.68 s` while still exposing
    `Erica's ?.?. Speaker`; the immediate 5-cycle local follow-up loop
    then recovered cleanly with fast `200` root/status/config
    responses and local `/api/status` holding around
    `uptime_seconds=49`-`50`; a post-loop hub refresh had already moved
    `.207` back to `online` with last heartbeat `2026-05-14T11:13:45Z`
  - `.225` improved materially in the same window: hub UI/API stayed
    `online` on `0.1.17-dev-central`, local root/status/config all
    returned `200`, local `/api/status` reported `uptime_seconds=637`,
    and the immediate 5-cycle loop stayed clean
  - `BUG-053` remained unchanged on `.30`, `.225`, and `.207`
  - `.30` still had no fresh regression beyond the standing desired-name
    drift; local `/api/status` returned `200` in about `0.02 s` with
    `uptime_seconds=5730`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T11:20:45Z` to `11:22:10Z`
  still showed no fresh hub UI-vs-API drift, and it materially
  improved the renamed soak target while adding fresh reboot evidence
  on `.207`:
  - the live hub Devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` improved materially versus the prior offline/recovery window:
    hub UI/API again showed `Rebooter - renamed test` `online` on
    `0.1.17-dev-central` with last heartbeat `2026-05-14T11:22:22Z`,
    the initial local root/status/config sweep returned clean fast
    `200` responses, and the immediate 5-cycle local follow-up loop
    stayed fully clean while local `/api/status` climbed from
    `uptime_seconds=525` to `534`
  - `.225` also stayed improved in the same window: hub UI/API still
    showed `online` on `0.1.17-dev-central`, local root/status/config
    all returned `200`, and local `/api/status` reported
    `uptime_seconds=1106`; keep only the standing desired-name drift
    plus watch status
  - `BUG-055` strengthened again on `.207`: the hub row/API had already
    returned to `online` on `0.1.16-dev-central` with last heartbeat
    `2026-05-14T11:22:06Z`, and the local root/status/config surfaces
    all returned clean `200` responses, but local `/api/status`
    reported only `uptime_seconds=194`, which is fresh reboot evidence
    relative to the prior `11:12:59Z`-`11:14:42Z` recovery loop
  - `BUG-053` still remained unchanged on `.30`, `.225`, and `.207`
  - `.30` still had no fresh regression beyond the standing desired-name
    drift; local `/api/status` returned `200` in about `0.02 s` with
    `uptime_seconds=6199`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T11:31:18Z` to `11:32:30Z`
  still showed no fresh hub UI-vs-API drift, and it improved the
  current interpretation of both the renamed soak target and `.207`:
  - the live hub Devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` stayed converged across hub UI/API and local status/config as
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
    the hub row/API showing last heartbeat `2026-05-14T11:32:22Z`
  - `BUG-054` did not reproduce in the focused follow-up loop. The
    initial local `.48` sweep returned clean `200` responses with the
    root page at about `0.39 s`, `/api/status` about `0.04 s`, and
    `/api/config` about `0.24 s`, while local `/api/status` reported
    `uptime_seconds=1154`; the immediate 5-cycle local loop from
    `11:31:57Z` to `11:32:09Z` then stayed fully clean and climbed from
    `uptime_seconds=1121` to `1131`
  - `.225` also stayed in the improved watch bucket. The hub row/API
    still showed `online` on `0.1.17-dev-central`, local
    `/api/status` and `/api/config` still exposed the standing
    desired-name drift as `Rebooter`, the first local root-page read
    stretched to about `2.97 s`, but the confirming local sweep
    returned `200` in about `0.16 s` and local `/api/status` reported
    `uptime_seconds=1785`, so there was no fresh reboot signal
  - `BUG-055` improved materially relative to the prior reboot-watch
    sample. The hub row/API still showed `.207` `online` on
    `0.1.16-dev-central` with last heartbeat `2026-05-14T11:32:06Z`,
    the local root/status/config surfaces all returned clean `200`
    responses, and local `/api/status` reported `uptime_seconds=873`.
    That uptime is consistent with continued survival since the prior
    `11:20Z` pass's `uptime_seconds=194`, so this recheck did not add a
    fresh reboot or response-integrity repro
  - `BUG-053` still remained unchanged on `.30`, `.225`, and `.207`
  - `.30` still had no bug-level change beyond the standing desired-
    name drift; the local root page stretched to about `2.37 s`, but
    `/api/status` and `/api/config` stayed fast and local `/api/status`
    reported `uptime_seconds=6879`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T11:42:29Z` to `11:43:06Z`
  still showed no fresh hub UI-vs-API drift, but it re-strengthened
  the renamed soak target as a masked reboot while `.225` and `.207`
  both stayed improved:
  - the live hub Devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` remained converged across hub UI/API and local status/config
    as `Rebooter - renamed test` / `online` / `0.1.17-dev-central`,
    with the hub row/API showing last heartbeat `2026-05-14T11:41:41Z`
  - `BUG-054` strengthened again in a narrower reboot-only shape: the
    initial local `.48` sweep returned clean `200` responses with the
    root page at about `0.24 s`, `/api/status` about `0.02 s`, and
    `/api/config` about `0.09 s`, but local `/api/status` reported only
    `uptime_seconds=114`, which is impossible without a fresh reboot
    relative to the prior `11:31:57Z` to `11:32:09Z` clean loop where
    `.48` had already climbed to `uptime_seconds=1121`-`1131`. The
    immediate 5-cycle local loop then stayed fully clean and climbed
    only from `145` to `150`, so the device had already recovered by
    the time of the focused follow-up while the hub never exposed a bad
    state
  - `.225` stayed in the improved watch bucket. The hub row/API still
    showed `online` on `0.1.17-dev-central`, local `/api/status` and
    `/api/config` still exposed the standing desired-name drift as
    `Rebooter`, and the local root/status/config surfaces all returned
    clean `200` responses with local `/api/status` reporting
    `uptime_seconds=2381`
  - `BUG-055` also improved in this pass. The hub row/API still showed
    `.207` `online` on `0.1.16-dev-central`, local `/api/status` and
    `/api/config` still exposed `Erica's ?.?. Speaker`, but the local
    root/status/config surfaces all returned clean `200` responses and
    local `/api/status` reported `uptime_seconds=1469`, which is
    consistent with uptime continuity since the prior `11:31Z` sample
  - `BUG-053` still remained unchanged on `.30`, `.225`, and `.207`
  - `.30` still had no bug-level change beyond the standing desired-
    name drift; the local root page returned `200` in about `0.17 s`,
    `/api/status` returned `200` in about `0.02 s`, and local
    `/api/status` reported `uptime_seconds=7475`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T11:50:46Z` to `11:52:22Z`
  still showed no fresh hub UI-vs-API drift, but it materially worsened
  the fleet-side reliability picture again:
  - the live hub Devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` re-strengthened again as another masked reboot on the renamed
    soak target: the initial local root/status/config sweep returned
    clean `200` responses with local `/api/status` at
    `uptime_seconds=593`, but the immediate 5-cycle local follow-up
    loop starting at `11:51:54Z` already found `.48`
    `uptime_seconds=74` and then only climbed to `90` by cycle 5, while
    the hub row/API kept showing `Rebooter - renamed test` `online` on
    `0.1.17-dev-central`
  - `.30` moved from name-drift-only into a fresh masked-reboot bucket:
    the hub row/API still showed `Erica's Subwoofer` `online` on
    `0.1.17-dev-central`, but local `/api/status` initially reported
    only `uptime_seconds=23` and then climbed only from `111` to `129`
    in the focused loop, with one slower local root-page read at about
    `3.50 s`
  - `.225` stayed improved in the same window: hub UI/API still showed
    `online` on `0.1.17-dev-central`, local root/status/config all
    returned `200`, and local `/api/status` climbed from
    `uptime_seconds=2861` to `2966`; keep only the standing desired-name
    drift plus watch status
  - `.207` worsened again behind another healthy-looking row: the hub
    row/API still showed `online` on `0.1.16-dev-central`, local
    `/api/status` initially reported only `uptime_seconds=143`, the
    focused loop first climbed from `231` to `242`, then cycle 5 hit a
    `10.49 s` local root-page stall while local `/api/status` dropped
    to `uptime_seconds=11` with `health_state=\"unknown\"`; a post-loop
    hub refresh still showed `.207` `online`
  - `BUG-053` still remained unchanged on `.30`, `.225`, and `.207`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T12:00:37Z` to `12:01:52Z`
  still showed no fresh hub UI-vs-API drift, and it improved the
  renamed soak target back out of the reboot bucket without fully
  clearing its local UI reliability issue:
  - the live hub Devices page still matched `/api/v1/admin/devices` on
    `.48`, so there was still no fresh central-side UI/API drift
  - `.48` stayed converged across hub UI/API and local status/config as
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
    the admin API reporting last heartbeat `2026-05-14T12:00:46Z`
  - `BUG-054` improved relative to the prior masked-reboot pass. The
    initial local root/status/config sweep returned clean `200`
    responses with local `/api/status` at `uptime_seconds=654`, and the
    immediate 10-cycle local follow-up loop stayed fully clean while
    local `/api/status` climbed to `uptime_seconds=671`
  - `BUG-054` still did not clear, though: cycle 8 of that same loop
    stretched the local root page to about `3.18 s`, and cycle 9 still
    took about `1.11 s`, while local `/api/status` and `/api/config`
    remained fast. That narrows the current live issue back to
    intermittent local-root latency rather than another fresh reboot in
    this specific pass
- The latest live recheck around `2026-05-14T12:12:34Z` to `12:13:14Z`
  still showed no fresh hub UI-vs-API drift, and it materially
  improved the renamed soak target again:
  - the live hub Devices page still matched `/api/v1/admin/devices` on
    all comparison targets, so there was still no fresh central-side
    UI/API drift
  - `.48` stayed converged across hub UI/API and local status/config as
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
    the hub row/API showing last heartbeat `2026-05-14T12:11:46Z`
    before the loop and `2026-05-14T12:12:46Z` after it
  - `BUG-054` did not reproduce in the focused follow-up loop. The
    initial local `.48` sweep returned clean `200` responses with the
    root page at about `0.29 s`, `/api/status` about `0.02 s`, and
    `/api/config` about `0.09 s`, while local `/api/status` reported
    `uptime_seconds=1282`; the immediate 10-cycle local loop from
    `12:12:51Z` to `12:13:02Z` then stayed fully clean and climbed from
    `uptime_seconds=1330` to `1341`
  - `.225` also stayed in the improved watch bucket. The hub row/API
    still showed `online` on `0.1.17-dev-central`, local
    `/api/status` and `/api/config` still exposed the standing
    desired-name drift as `Rebooter`, and the local root/status/config
    surfaces all returned clean `200` responses with local
    `/api/status` reporting `uptime_seconds=4155`
  - `BUG-055` improved materially relative to the prior bad window. The
    hub row/API still showed `.207` `online` on `0.1.16-dev-central`,
    local `/api/status` and `/api/config` still exposed
    `Erica's ?.?. Speaker`, but the local root/status/config surfaces
    all returned clean `200` responses and local `/api/status`
    reported `uptime_seconds=1190`, which is consistent with uptime
    continuity since the earlier reboot/stall sample
  - `BUG-053` still remained unchanged on `.30`, `.225`, and `.207`
  - `.30` still had no fresh bug-level change beyond the standing
    desired-name drift; the local root page returned `200` in about
    `0.17 s`, `/api/status` returned `200` in about `0.03 s`, and
    local `/api/status` reported `uptime_seconds=1318`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T12:23:58Z` to `12:24:54Z`
  still showed no fresh hub UI-vs-API drift, but the fleet-side
  reliability picture regressed again on `.48` and `.207`:
  - the live hub Devices page still matched `/api/v1/admin/devices` on
    `.48`, `.30`, `.225`, `.207`, and `.69`, so there was still no
    fresh central-side UI/API drift
  - `.48` re-strengthened immediately after the prior clean window: the
    hub row/API kept showing `Rebooter - renamed test` `online` on
    `0.1.17-dev-central`, but the initial local root request timed out,
    local `/api/status` took about `5.71 s` and reported only
    `uptime_seconds=14` with `health_state=\"unknown\"`, and the
    immediate 5-cycle follow-up loop from `12:24:46Z` to `12:24:50Z`
    recovered only from `uptime_seconds=45` to `50`
  - `.30` stayed improved in this pass: hub UI/API still showed it
    `online` on `0.1.17-dev-central`, local root/status/config all
    returned `200`, and local `/api/status` reported
    `uptime_seconds=2050`, so there was no fresh masked-reboot evidence
  - `.225` also stayed improved: hub UI/API still showed `online` on
    `0.1.17-dev-central`, local root/status/config all returned `200`,
    and local `/api/status` reported `uptime_seconds=4887`; keep only
    the standing desired-name drift plus watch status
  - `.207` worsened again behind another healthy-looking row: the hub
    row/API still showed `online` on `0.1.16-dev-central`, but local
    `/api/status` reported only `uptime_seconds=83` on the initial
    sweep and climbed only to `121` through the confirming 3-cycle loop
    from `12:24:51Z` to `12:24:53Z`, which is fresh reboot evidence
    relative to the prior `12:12Z` pass where the same device had
    already reached `uptime_seconds=1190`
  - `BUG-053` still remained unchanged on `.30`, `.225`, and `.207`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T12:30:34Z` to `12:32:29Z`
  still showed no fresh hub UI-vs-API drift, and it sharpened the
  current fleet picture further:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    `.48`, `.30`, `.225`, `.207`, and `.69`, so there was still no
    fresh central-side UI/API drift
  - `.48` improved relative to the prior `12:24Z` masked-reboot window:
    hub UI/API still showed `Rebooter - renamed test` `online` on
    `0.1.17-dev-central`, local `/api/status` plus `/api/config` again
    matched that identity, the initial local root/status/config sweep
    returned clean `200` responses, and the immediate 10-cycle local
    follow-up loop kept local `/api/status` climbing from
    `uptime_seconds=485` to `503`, so this pass did not add a fresh
    reboot sample
  - `BUG-054` still remained concrete, though, in its narrowed
    local-root integrity form: cycle 5 of the `.48` loop stretched the
    local root page to about `3.90 s`, and cycle 9 hit another
    truncated-body `ChunkedEncodingError` while local `/api/status` and
    `/api/config` remained fast
  - `.30` worsened again behind another healthy-looking row: the hub
    row/API still showed `Erica's Subwoofer` `online` on
    `0.1.17-dev-central`, but the first local `/api/status` read
    already reported only `uptime_seconds=22`, and the confirming
    3-cycle loop climbed only from `131` to `134`. Relative to the
    prior `12:24Z` pass where the same device had already reached
    `uptime_seconds=2050`, this is fresh masked-reboot evidence for
    `BUG-056`
  - `.225` stayed improved again: hub UI/API still showed `online` on
    `0.1.17-dev-central`, local root/status/config all returned `200`,
    and local `/api/status` reported `uptime_seconds=5267`; keep only
    the standing desired-name drift plus watch status
  - `.207` stayed in the masked-reboot bucket: the hub row/API still
    showed `online` on `0.1.16-dev-central`, but the initial local
    `/api/status` read reported only `uptime_seconds=122`, and the
    confirming 3-cycle loop later climbed only from `233` to `235`.
    Relative to the prior `12:24:51Z` to `12:24:53Z` pass where `.207`
    had already reached `uptime_seconds=121`, this is another fresh
    reboot sample for `BUG-055`
  - `BUG-053` still remained unchanged on `.30`, `.225`, and `.207`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T12:40:51Z` to `12:41:54Z`
  still showed no fresh hub UI-vs-API drift, and it improved the
  fleet-side picture again without clearing the standing issues:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    `.48`, `.30`, `.225`, `.207`, and `.69`, so there was still no
    fresh central-side UI/API drift
  - `.48` improved materially relative to the prior `12:30Z` to
    `12:32Z` local-root-corruption window: hub UI/API still showed
    `Rebooter - renamed test` `online` on `0.1.17-dev-central`, local
    `/api/status` plus `/api/config` again matched that identity, the
    initial local root/status/config sweep returned clean `200`
    responses with local `/api/status` at `uptime_seconds=1003`, and
    the immediate 5-cycle local follow-up loop from `12:41:30Z` to
    `12:41:38Z` kept local `/api/status` climbing from `1050` to
    `1058`. This pass did not add a fresh `BUG-054` timeout, reboot, or
    truncated-body repro
  - `.30` improved back out of the fresh-repro bucket. The hub row/API
    still showed `Erica's Subwoofer` `online` on `0.1.17-dev-central`,
    local root/status/config all returned `200`, and local
    `/api/status` reported `uptime_seconds=630` before the immediate
    5-cycle loop climbed from `678` to `686`. Relative to the prior
    `12:30Z` to `12:32Z` pass where the same device had already
    climbed from `131` to `134`, that is consistent with uptime
    continuity rather than a new masked reboot for `BUG-056`
  - `.225` stayed improved again: hub UI/API still showed `online` on
    `0.1.17-dev-central`, local root/status/config all returned `200`,
    and local `/api/status` reported `uptime_seconds=5875`; keep only
    the standing desired-name drift plus watch status
  - `.207` also improved back out of the fresh-repro bucket. The hub
    row/API still showed `online` on `0.1.16-dev-central`, local
    `/api/status` and `/api/config` still exposed
    `Erica's ?.?. Speaker`, and the local root/status/config surfaces
    all returned clean `200` responses with local `/api/status`
    reporting `uptime_seconds=731` before the immediate 5-cycle loop
    climbed from `777` to `785`. Relative to the prior `12:30Z` to
    `12:32Z` pass where `.207` had already climbed from `233` to `235`,
    that is consistent with uptime continuity rather than a fresh
    reboot or stall for `BUG-055`
  - `BUG-053` still remained unchanged on `.30`, `.225`, and `.207`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T12:50:43Z` to `12:51:23Z`
  still showed no fresh hub UI-vs-API drift, and it extended the
  current improvement window again:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    `.48`, `.30`, `.225`, `.207`, and `.69`, so there was still no
    fresh central-side UI/API drift
  - `.48` stayed clean through another short soak loop: hub UI/API
    still showed `Rebooter - renamed test` `online` on
    `0.1.17-dev-central`, local `/api/status` plus `/api/config` again
    matched that identity, the initial local root/status/config sweep
    returned clean `200` responses with local `/api/status` at
    `uptime_seconds=1603`, and the immediate 5-cycle follow-up loop
    from `12:51:16Z` to `12:51:21Z` kept local `/api/status` climbing
    from `1636` to `1640` without another timeout, stall, or
    truncated-body repro
  - `.30` improved further out of the fresh-repro bucket. The hub
    row/API still showed `Erica's Subwoofer` `online` on
    `0.1.17-dev-central`, local root/status/config all returned `200`,
    and local `/api/status` climbed from `uptime_seconds=1231` on the
    initial sweep to `1269` on the confirming re-read. One `1.20 s`
    root-page sample did not coincide with any status/config failure or
    reset, so this pass stayed in uptime-continuity territory rather
    than another masked reboot for `BUG-056`
  - `.225` stayed improved again: hub UI/API still showed `online` on
    `0.1.17-dev-central`, local root/status/config all returned `200`,
    and local `/api/status` reported `uptime_seconds=6476`; keep only
    the standing desired-name drift plus watch status
  - `.207` improved further out of the fresh-repro bucket. The hub
    row/API still showed `online` on `0.1.16-dev-central`, local
    `/api/status` and `/api/config` still exposed
    `Erica's ?.?. Speaker`, and the local root/status/config surfaces
    all returned clean `200` responses with local `/api/status`
    reporting `uptime_seconds=1331` before the later confirming re-read
    reached `1368`. That is still uptime continuity rather than a fresh
    reboot or stall for `BUG-055`
  - `BUG-053` still remained unchanged on `.30`, `.225`, and `.207`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest live recheck around `2026-05-14T13:00:56Z` to `13:04:04Z`
  still showed no fresh hub UI-vs-API drift, but it moved the current
  fleet picture back toward device-side reliability trouble:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    `.48`, `.30`, `.225`, `.207`, and `.69`, so there was still no
    fresh central-side UI/API drift
  - `.48` regressed again behind a healthy-looking row: hub UI/API
    still showed `Rebooter - renamed test` `online` on
    `0.1.17-dev-central`, but the first local root-page read stretched
    to about `4.03 s`, the first local `/api/status` call was
    connection-reset after about `9.56 s`, and the next successful
    local `/api/status` sample already showed only
    `uptime_seconds=93`; the immediate 5-cycle follow-up loop then
    recovered cleanly and climbed from `162` to `167`. Relative to the
    prior `12:50Z` clean window where the same device had already
    reached `1640`, this is fresh reboot evidence plus transient local
    UI/API instability for `BUG-054`
  - `.30` stayed improved again: hub UI/API still showed it `online` on
    `0.1.17-dev-central`, local root/status/config all returned `200`,
    and local `/api/status` reported `uptime_seconds=1846`, so this
    pass added no fresh `BUG-056` reboot evidence beyond the standing
    desired-name drift
  - `.225` stayed operationally improved: hub UI/API still showed
    `online` on `0.1.17-dev-central`, local `/api/status` reported
    `uptime_seconds=7094`, and the immediate 5-cycle loop later climbed
    from `7255` to `7260`; only the first root-page read hit a
    truncated-body failure, so keep `.225` as watch-only rather than a
    fresh reboot bucket
  - `.207` regressed again behind another healthy-looking row: the hub
    row/API still showed `online` on `0.1.16-dev-central`, but local
    `/api/status` first reported only `uptime_seconds=82`, a later
    confirming sample still reported only `161`, and the immediate
    5-cycle loop then climbed from `236` to `241`. Relative to the
    prior `12:50Z` pass where the same device had already reached
    `1368`, this is another fresh masked reboot for `BUG-055`
  - `BUG-053` still remained unchanged on `.30`, `.225`, and `.207`
  - `.69` remained the stable offline control: hub `offline`, local
    UI/API unreachable from this host
- The latest renamed-device soak recheck around `2026-05-14T13:22:34Z`
  to `13:22:44Z` pushed `.48` back into another clean continuity
  window:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    the renamed soak target, with both surfaces showing
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`,
    local IP `192.168.1.48`, and heartbeat `2026-05-14T13:21:56Z`
  - local `/api/status` plus `/api/config` still matched that identity,
    the initial local root/status/config sweep returned clean `200`
    responses (`0.14 s`, `0.02 s`, `0.07 s`), and local `/api/status`
    reported `health_state="healthy"` with `uptime_seconds=1307`
  - the immediate 5-cycle follow-up loop then stayed fully clean while
    local `/api/status` climbed from `1307` to `1314`; an earlier
    10-cycle local loop in the same recheck window also climbed from
    `1240` to `1253` without a timeout, reset, or truncated body, so
    this pass added another concrete recovery sample rather than a
    fresh masked reboot
  - the only remaining signal in this pass was intermittent local
    root-page latency, peaking at `1.34 s` in the timestamped loop and
    at `4.25 s` on the earlier first-hit read; `/api/status` and
    `/api/config` stayed fast throughout, so `BUG-054` remains open in
    latency-only form for now
- The latest renamed-device soak recheck around `2026-05-14T13:31:41Z`
  to `13:34:29Z` still showed no fresh hub list-vs-API drift, but it
  moved `.48` back into the fresh reboot bucket and re-strengthened the
  local-surface failure shape:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    the renamed soak target; both surfaces showed
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central` at
    local IP `192.168.1.48`, first with heartbeat
    `2026-05-14T13:31:41Z` and later `2026-05-14T13:33:46Z`
  - the first local root/status/config sweep still returned `200`
    responses, but local root had already slowed to `2.31 s` and local
    `/api/status` had already fallen back to `uptime_seconds=132`;
    relative to the prior `13:22:34Z` to `13:22:44Z` clean window
    where the same device had already climbed to `1314`, this is fresh
    masked reboot evidence for `BUG-054`
  - the local device then slipped back into the stronger failure shape
    within the same pass: a later direct local `/` fetch timed out at
    the full `10 s`, the next local `/api/status` fetch also timed out
    at `10 s`, and local `/api/config` returned `200` only after
    `7.30 s`
  - a full local `/api/status` payload fetched immediately after that
    stall showed `device_name="Rebooter - renamed test"`,
    `firmware_version="0.1.17-dev-central"`,
    `health_state="unknown"`, and `uptime_seconds=23`; the immediate
    3-cycle recovery loop then climbed only from `47` to `49`
  - the hub device detail page partially reflected the same event with
    last seen `2026-05-14T13:33:46Z`, `health: unknown`, and
    `uptime_s: 0`, but the hub list/API still continued to present the
    device as `online`, so the central fleet view remains too healthy
    relative to the reboot/recovery behavior now repeating on `.48`
- The latest renamed-device follow-up recheck around
  `2026-05-14T13:40:34Z` to `13:41:04Z` moved `.48` back into a short
  clean recovery window:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    the renamed soak target, with both surfaces showing
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central` at
    local IP `192.168.1.48`
  - the hub device detail page and `/api/v1/admin/devices/<id>` also
    reconverged, both showing last heartbeat `2026-05-14T13:40:53Z`,
    healthy state, and `uptime_s` / `uptime_seconds` at `306`
  - the first direct local root/status/config sweep returned clean
    `200` responses (`0.315 s`, `0.022 s`, `0.078 s`), local
    `/api/status` reported `health_state="healthy"` with
    `uptime_seconds=290`, and local `/api/config` still matched the
    renamed identity and central device id
  - the immediate 5-cycle local continuity loop then stayed fully clean
    while local `/api/status` climbed from `312` to `318`, with no
    timeout, reset, or truncated-body repro; treat this as another
    concrete `.48` recovery sample, while keeping `BUG-054` open
    historically because the prior `13:31:41Z`-`13:34:29Z` window still
    captured a fresh reboot plus root/status stall
- The latest renamed-device follow-up recheck around
  `2026-05-14T13:51:23Z` to `13:51:27Z` kept `.48` in another short
  converged continuity window:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    the renamed soak target, with both surfaces showing
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central` and
    heartbeat `2026-05-14T13:50:53Z`
  - the hub device detail page and `/api/v1/admin/devices/<id>` also
    stayed aligned: the detail page still rendered the same identity,
    firmware, and local IP `192.168.1.48`, while the detail API
    reported `latest_heartbeat.health_state="healthy"` and
    `latest_heartbeat.uptime_seconds=906`
  - the first direct local root/status/config sweep returned clean
    `200` responses (`0.773 s`, `0.026 s`, `0.074 s`), and local
    `/api/status` reported `health_state="healthy"` with
    `uptime_seconds=939`
  - the immediate 5-cycle local continuity loop then stayed fully clean
    while local `/api/status` climbed from `939` to `941`; root-page
    reads held at `0.103 s`-`0.233 s`, `/api/status` at
    `0.020 s`-`0.026 s`, and `/api/config` at `0.070 s`-`1.146 s`
  - this pass added no fresh reboot, timeout, connection-reset, or
    truncated-body repro; keep `BUG-054` open historically because of
    the earlier `13:31Z` stall/reboot window, but treat this recheck as
    another concrete recovery sample
- The latest renamed-device follow-up recheck around
  `2026-05-14T14:00:53Z` to `14:01:03Z` moved `.48` back into the fresh
  masked-reboot bucket:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    the renamed soak target; both surfaces again showed
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
    the list page/API carrying fresh heartbeats at
    `2026-05-14T14:00:53Z` and `2026-05-14T14:01:03Z`
  - but the first local reread had already fallen back to
    `health_state="unknown"` and `uptime_seconds=12`; relative to the
    prior `13:51:23Z`-`13:51:27Z` clean window where local
    `/api/status` had already climbed to `941`, this is fresh masked
    reboot evidence for `BUG-054`
  - the first direct local root-page request in the same pass timed out
    at the full `15 s`, while local `/api/status` and `/api/config`
    still returned `200` in `0.127 s` and `0.097 s`, respectively; so
    the post-reboot failure shape is back to root-timeout plus
    low-uptime recovery, not just the weaker latency-only bucket
  - the immediate 5-cycle local continuity loop then recovered cleanly
    and climbed only from `uptime_seconds=13` to `18`, with root-page
    reads at `0.101 s`-`0.256 s`, `/api/status` at `0.014 s`-`0.018 s`,
    and `/api/config` at `0.062 s`-`0.073 s`
  - a confirming reread a few seconds later showed the hub list still
    presenting the device as `online`, while `/api/v1/admin/devices/<id>`
    had already dropped to `latest_heartbeat.health_state="unknown"`
    and `latest_heartbeat.uptime_seconds=0`; local `/api/status` then
    recovered to `health_state="healthy"` at `uptime_seconds=38`, so
    this pass captured another short reboot/recovery window rather than
    a stale sample
- The latest renamed-device follow-up recheck around
  `2026-05-14T14:10:33Z` to `14:14:57Z` added three concrete changes:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    `.48`, `.30`, `.225`, `.207`, and `.69`, so there was still no
    fresh hub list-vs-API drift
  - `.48` strengthened `BUG-054` again in another masked-reboot shape:
    the first pass at `14:10:33Z` still showed
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`, with
    local `/api/status.uptime_seconds=579` and hub detail
    `latest_heartbeat.uptime_seconds=424`; but by `14:14:57Z`, the hub
    list/API still showed `.48` `online` with fresh heartbeat
    `2026-05-14T14:14:10Z` while hub detail had already fallen back to
    `latest_heartbeat.uptime_seconds=124` and local `/api/status` had
    reset to `uptime_seconds=142`. That is another fresh reboot within
    about four minutes behind a still-healthy-looking central row. The
    earlier `14:10:33Z` root-page read also stretched to `3.06 s`
    before the later reread recovered to `0.13 s`, so the weaker
    root-latency form remains in play too
  - `.225` moved back into the reboot bucket relative to the prior
    clean memo window: the earlier `13:00:56Z` to `13:04:04Z` pass had
    already climbed to about `uptime_seconds=7260`, but by
    `14:10:33Z`, local `/api/status` had dropped to `994` while the
    hub still showed the device `online` on `0.1.17-dev-central`;
    `14:14:57Z` then climbed to `1223`, so this was fresh reboot
    evidence rather than a second in-pass restart
  - `.30` and `.207` improved relative to the earlier reboot windows:
    both stayed `online` in the hub, local `/api/status` reported
    `uptime_seconds=6024` on `.30` and `1597` on `.207`, and neither
    device showed a fresh reset in this window
  - `.69` stopped being a clean offline control and became a central
    consistency bug: the hub devices page and `/api/v1/admin/devices`
    still showed `offline` with stale
    `last_heartbeat_at="2026-05-13T22:06:13Z"`, and the local device
    still timed out on `/`, `/api/status`, and `/api/config`, but
    `/api/v1/admin/devices/<id>` for the same device still exposed
    `latest_heartbeat.health_state="healthy"`,
    `latest_heartbeat.uptime_seconds=69`, and
    `latest_heartbeat.last_event_type="boot"` at that same stale
    timestamp. That means the hub detail surface is now materially more
    optimistic than both the hub list and the local ground truth
- The latest renamed-device follow-up recheck around
  `2026-05-14T14:22:53Z` to `14:25:18Z` added four concrete updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    `.48`, `.30`, `.225`, `.207`, and `.69`, so there was still no
    fresh hub list-vs-API drift. `.207` briefly converged `offline` in
    the first pass and then returned to `online` in the focused reread,
    with the page and list API staying aligned at each step
  - `.48` improved materially out of the prior `14:10:33Z` to
    `14:14:57Z` masked-reboot window: the rendered Devices row, the hub
    detail page, `/api/v1/admin/devices`, `/api/v1/admin/devices/<id>`,
    and local `/api/status` + `/api/config` all reconverged on
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`; the
    first local root/status/config sweep returned clean `200`
    responses (`0.233 s`, `0.023 s`, `0.071 s`) with local
    `/api/status.uptime_seconds=648`, and the immediate 5-cycle local
    loop then stayed fully clean while local `/api/status` climbed from
    `648` to `703`. A later focused reread still showed the hub list/API
    presenting `.48` `online` with heartbeat `2026-05-14T14:25:10Z`
    while local `/api/status` had continued up to `804`, so this pass
    added another concrete `.48` recovery sample rather than a fresh
    BUG-054 repro
  - `.225` improved further relative to the prior `14:10:33Z` to
    `14:14:57Z` reboot evidence: local root/status/config all returned
    clean `200` responses (`0.185 s`, `0.036 s`, `0.067 s`), local
    `/api/status` had climbed to `uptime_seconds=1730`, and the later
    hub detail reread showed `latest_heartbeat.uptime_seconds=1806`.
    `.30` also stayed improved, with local root/status/config all
    returning clean `200` and local `/api/status.uptime_seconds=6760`
    while the later hub detail reread reported `6844`
  - `.207` strengthened `BUG-055` again in a two-step shape. In the
    first sweep the hub page/API had already converged `offline` with
    stale `last_heartbeat_at="2026-05-14T14:18:15Z"`, but the local
    device still half-answered: root returned `200` only after
    `5.006 s`, local `/api/status` failed after `9.571 s` with a
    connection reset, and local `/api/config` still returned `200` in
    `0.091 s` with `Erica's ?.?. Speaker`. A focused reread a couple of
    minutes later then showed the hub page/API back to `online` on
    `0.1.16-dev-central` with heartbeat `2026-05-14T14:25:13Z`, while
    local `/api/status` returned `200` again but reported only
    `uptime_seconds=64`. That is another fresh masked reboot/recovery
    behind a healthy central row
  - `.69` kept BUG-057 intact and broadened it from a detail-API-only
    mismatch into a rendered-detail-page mismatch too: the hub devices
    page and `/api/v1/admin/devices` still showed `offline` with stale
    `last_heartbeat_at="2026-05-13T22:06:13Z"`, and the local device
    still timed out on `/`, `/api/status`, and `/api/config` after
    about `12 s`, but the hub detail page still rendered
    `health: healthy` / `uptime_s: 69`, matching the detail API's stale
    `latest_heartbeat` sample
- The latest renamed-device follow-up recheck around
  `2026-05-14T14:31:44Z` to `14:33:27Z` added three concrete updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    `.48`, `.30`, `.225`, `.207`, and `.69`, so there was still no
    fresh hub list-vs-API drift in this pass either
  - `.48` improved further out of the prior masked-reboot window and
    held a clean 5-cycle local continuity loop: hub list/detail plus
    local `/api/status` and `/api/config` all still showed
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`; the
    first local root/status/config sweep returned clean `200`
    responses (`0.225 s`, `0.021 s`, `0.078 s`) with local
    `/api/status.uptime_seconds=1180`, the immediate 5-cycle local loop
    then stayed fully clean while local `/api/status` climbed from
    `1275` to `1281`, and the later hub detail reread still reported
    `healthy` at `uptime_seconds=1264`. Cycle 5 did slow the local root
    page to `1.39 s`, but there was no fresh timeout, reset, or
    truncated-body failure, so this pass added only an improved
    BUG-054 recovery sample
  - `.207` also improved relative to the prior `14:22Z`-`14:25Z`
    masked-reboot sample: hub UI/API still showed it `online` on
    `0.1.16-dev-central` with heartbeat `2026-05-14T14:33:13Z`, the
    hub detail API reported `healthy` at `uptime_seconds=544`, and
    local `/api/status` reported `uptime_seconds=560` with clean local
    `/api/status` + `/api/config` `200` responses. One `1.006 s` local
    `/api/status` sample did not coincide with any reset, so treat this
    as a short BUG-055 recovery window rather than a fresh repro
  - `.30` and `.225` stayed operationally improved, with local
    `/api/status.uptime_seconds=7337` on `.30` and `2307` on `.225`
    alongside clean local root/status/config responses, while `.69`
    kept BUG-057 intact: hub list/API still showed `offline` at stale
    `2026-05-13T22:06:13Z`, local `/`, `/api/status`, and `/api/config`
    still timed out after about `15 s`, but the hub detail API still
    exposed `healthy` / `uptime_seconds=69`
- The latest renamed-device follow-up recheck around
  `2026-05-14T14:40:52Z` to `14:42:17Z` added four concrete updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    `.48`, `.30`, `.225`, `.207`, and `.69`, so there was still no
    fresh hub list-vs-API drift in this pass either
  - `.48` moved back into the fresh masked-reboot bucket immediately
    after the prior clean `14:31:44Z` to `14:33:27Z` window: the list
    page/API still showed `Rebooter - renamed test` `online` on
    `0.1.17-dev-central` with heartbeat `2026-05-14T14:40:16Z`, and
    the first direct local root/status/config sweep still returned
    clean `200` responses (`0.220 s`, `0.022 s`, `0.077 s`), but local
    `/api/status` had already fallen back to `uptime_seconds=42` while
    the hub detail API had already dropped to
    `latest_heartbeat.health_state="unknown"` and
    `uptime_seconds=0`. That is another fresh reboot behind a still
    healthy-looking central row
  - the immediate 5-cycle local `.48` follow-up loop from `14:42:02Z`
    to `14:42:07Z` then recovered cleanly: root-page reads stayed at
    `0.143 s` to `0.312 s`, `/api/status` at `0.019 s` to `0.057 s`,
    `/api/config` at `0.070 s` to `0.133 s`, and local
    `/api/status.uptime_seconds` climbed from `111` to `117`. A
    focused hub detail-page reread at `14:42:17Z` rendered
    `health: healthy` with `uptime_s: 124`, so this pass added another
    short reboot/recovery sample without reproducing the earlier
    timeout, reset, or truncated-body shapes
  - `.207` stayed improved in a short recovery window rather than
    re-strengthening `BUG-055`: hub detail page/API still showed
    `healthy` at `uptime_s` / `uptime_seconds=964`, local
    `/api/status` returned `uptime_seconds=1006`, and local `/` plus
    `/api/config` stayed clean. `.30` and `.225` also remained
    operationally improved at local `/api/status.uptime_seconds=7840`
    and `2809`, respectively, while `.69` kept BUG-057 fully unchanged:
    hub list/API still showed `offline` at stale
    `2026-05-13T22:06:13Z`, local `/`, `/api/status`, and `/api/config`
    still timed out after about `15 s`, but the rendered hub detail
    page still showed `health: healthy` / `uptime_s: 69`
- The latest renamed-device follow-up recheck around
  `2026-05-14T14:51:03Z` to `14:53Z` added four concrete updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    `.48`, `.30`, `.225`, `.207`, and `.69`, so there was still no
    fresh hub list-vs-API drift in this pass either
  - `.48` stayed in a clean recovery window after the prior
    `14:40:52Z` to `14:42:17Z` masked reboot: hub row/detail plus local
    `/api/status` and `/api/config` all still showed
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`; the
    first local root/status/config sweep returned clean `200`
    responses (`0.218 s`, `0.014 s`, `0.062 s`) with local
    `/api/status.uptime_seconds=251`, and the immediate 5-cycle local
    loop then stayed fully clean while local `/api/status` climbed from
    `297` to `302`. This pass added no fresh timeout, reset, or
    truncated-body repro, so it counts only as another improved
    BUG-054 recovery sample
  - `.30` regressed again behind a healthy-looking hub row. The hub
    row/detail still showed `Erica's Subwoofer` `online` on
    `0.1.17-dev-central` with heartbeat `2026-05-14T14:52:25Z`,
    `health: healthy`, and `uptime_s` / `uptime_seconds=664`, but the
    prior `14:40:52Z` pass had already climbed to local
    `/api/status.uptime_seconds=7840` while this pass's fresh local
    root/status/config sweep returned only `uptime_seconds=700` with
    clean `200` responses. That is another concrete BUG-056 masked
    reboot, not just the standing `.30` desired-name drift
  - `.225` and `.207` stayed improved, with local `/api/status`
    reaching `3536` on `.225` and `1732` on `.207` alongside clean
    local root/status/config responses, while `.69` kept BUG-057 fully
    unchanged: hub list/API still showed `offline` at stale
    `2026-05-13T22:06:13Z`, local `/`, `/api/status`, and `/api/config`
    still timed out after about `15 s`, but the rendered hub detail
    page plus detail API still showed `health: healthy` /
    `uptime_s: 69`
- The latest renamed-device follow-up recheck around
  `2026-05-14T15:00:54Z` to `15:02:15Z` added four concrete updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    `.48`, `.30`, `.225`, `.207`, and `.69`, so there was still no
    fresh hub list-vs-API drift in this pass either
  - `.48` stayed in another clean recovery window after the prior
    `14:51:03Z` to `14:53Z` clean pass: hub row/detail plus local
    `/api/status` and `/api/config` all still showed
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`; the
    first local root/status/config sweep returned clean `200`
    responses (`0.248 s`, `0.021 s`, `0.075 s`) with local
    `/api/status.uptime_seconds=760`, and the immediate 5-cycle local
    loop then stayed fully clean while local `/api/status` climbed from
    `826` to `833`. Cycle 5 slowed the local root page to `1.112 s`,
    but there was no fresh timeout, reset, or truncated-body failure,
    so this pass added only another improved BUG-054 recovery sample
  - `.30` and `.225` stayed operationally improved, with local
    `/api/status.uptime_seconds=1255` on `.30` and `4091` on `.225`
    alongside clean local root/status/config responses, so this pass
    added no fresh BUG-056 repro and kept `.225` in watch-only status
    despite the standing desired-name drift
  - `.207` moved back into the masked-reboot bucket. The hub row/detail
    still showed it `online` on `0.1.16-dev-central` with fresh
    heartbeats through `2026-05-14T15:01:59Z`, `health: healthy`, and
    `uptime_s` / `uptime_seconds=126`, but the prior `14:51:03Z` to
    `14:53Z` pass had already reached local
    `/api/status.uptime_seconds=1732` while this pass's fresh local
    root/status/config sweep returned only `uptime_seconds=142`. The
    immediate 5-cycle local `.207` loop then stayed responsive while
    local `/api/status.uptime_seconds` climbed only from `162` to
    `169`, so treat this as another fresh BUG-055 masked reboot behind
    a healthy-looking hub row. `.69` kept BUG-057 fully unchanged
- The latest renamed-device follow-up recheck around
  `2026-05-14T15:09:56Z` to `15:12:59Z` added four concrete updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    `.48`, `.30`, `.225`, `.207`, and `.69`, so there was still no
    fresh hub list-vs-API drift in this pass either
  - `.48` stayed in a meaningfully longer clean recovery window after
    the prior `15:00:54Z` to `15:02:15Z` sample: hub row/detail plus
    local `/api/status` and `/api/config` all still showed
    `Rebooter - renamed test` / `online` / `0.1.17-dev-central`; the
    first local root/status/config sweep returned clean `200`
    responses (`1.56 s`, `0.026 s`, `0.08 s`) with local
    `/api/status.uptime_seconds=1343`, and the immediate 5-cycle local
    loop then stayed fully clean while local `/api/status` climbed from
    `1423` to `1429`. Cycle 5 slowed the local root page to `1.163 s`,
    but there was no fresh timeout, reset, or truncated-body failure,
    so this pass added only another improved BUG-054 recovery sample
  - `.207` moved back out of the fresh-repro bucket and into a clean
    recovery window. The hub row/detail still showed it `online` on
    `0.1.16-dev-central` with fresh heartbeats through
    `2026-05-14T15:12:59Z`, `health: healthy`, and
    `uptime_s` / `uptime_seconds=726`, while the fresh local
    root/status/config sweep returned only clean `200` responses
    (`0.19 s`, `0.029 s`, `0.091 s`) and local
    `/api/status.uptime_seconds=683`. The immediate 5-cycle local
    `.207` loop then stayed responsive while local
    `/api/status.uptime_seconds` climbed from `762` to `767`; cycle 1
    did stretch the local root page to `3.475 s`, but there was no
    reset or API failure, so this pass added only a short improved
    BUG-055 recovery sample rather than another fresh masked reboot
  - `.30` and `.225` also stayed operationally improved, with local
    `/api/status.uptime_seconds=1792` on `.30` and `4630` on `.225` on
    the initial sweep, then `1903` and `4739` on later re-reads,
    alongside clean local root/status/config responses. `.225` did have
    another slower first root-page read (`2.832 s`), but it stayed
    below fresh-reboot level, while `.69` kept BUG-057 fully unchanged:
    hub list/API still showed `offline` at stale
    `2026-05-13T22:06:13Z`, local `/`, `/api/status`, and `/api/config`
    still timed out after about `15 s`, but the hub detail API still
    exposed `health: healthy` / `uptime_s: 69` from that stale
    heartbeat sample
- The latest renamed-device follow-up recheck around
  `2026-05-14T15:19:59Z` to about `15:21Z` added four concrete updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    `.48`, `.30`, `.225`, `.207`, and `.69`, so there was still no
    fresh hub list-vs-API drift in this pass either
  - `.48` moved back into the masked-reboot bucket after the prior
    longer clean window: hub row/detail plus local `/api/status` and
    `/api/config` still showed `Rebooter - renamed test` / `online` /
    `0.1.17-dev-central`, but the prior `15:09:56Z` to `15:12:59Z`
    pass had already reached local `/api/status.uptime_seconds=1429`
    while this pass's direct local root/status/config sweep returned
    clean `200` responses (`0.441 s`, `0.061 s`, `0.092 s`) at only
    `uptime_seconds=179`; the hub detail API had already reconverged to
    `health: healthy` with `uptime_s=128` at heartbeat
    `2026-05-14T15:20:05Z`, so treat this as another fresh BUG-054
    masked reboot/recovery sample
  - `.30` regressed harder than in the prior `.30` reboot sample and is
    the main new signal this run. The hub row/API still showed it
    `online` on `0.1.17-dev-central` with heartbeat
    `2026-05-14T15:20:02Z`, but the hub detail API had already dropped
    to `health: unknown` with `uptime_s=0` while the local
    root/status/config sweep still returned clean `200` responses
    (`0.184 s`, `0.042 s`, `0.085 s`) at only `uptime_seconds=58`.
    Relative to the prior `15:09:56Z` to `15:12:59Z` pass where local
    `/api/status.uptime_seconds` had already climbed to `1903`, this is
    another fresh BUG-056 reboot/recovery event behind a still-healthy
    list row
  - `.207` and `.225` both stayed operationally improved, with local
    `/api/status.uptime_seconds=1265` on `.207` and `5212` on `.225`
    alongside clean local root/status/config responses, while `.69`
    kept BUG-057 fully unchanged: hub list/API still showed `offline`
    at stale `2026-05-13T22:06:13Z`, local `/`, `/api/status`, and
    `/api/config` still timed out after about `15 s`, but the hub
    detail page/API still showed stale `health: healthy` /
    `uptime_s: 69`

- The latest renamed-device follow-up recheck around
  `2026-05-14T15:30:42Z` to `15:31:40Z` added two concrete updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    the renamed soak target, with the list API showing
    `Rebooter - renamed test` at `192.168.1.48`,
    `heartbeat_state="online"`, `online=true`, firmware
    `0.1.17-dev-central`, and last heartbeat
    `2026-05-14T15:31:11Z`, while the hub detail API still showed
    `health: healthy` and `uptime_s=484`
  - `.48` recovered back into a short clean window after the prior
    `15:19:59Z` to `15:21Z` masked-reboot sample: the direct local
    root/status/config sweep returned clean `200` responses
    (`0.20 s`, `0.022 s`, `0.071 s`) with local `/api/status`
    already back to `Rebooter - renamed test` /
    `0.1.17-dev-central` / `healthy` at `uptime_seconds=458`, and the
    immediate 5-cycle local continuity loop then kept the same identity
    while local `/api/status.uptime_seconds` climbed from `510` to
    `517`. Cycle 5 still slowed the local root page to `2.946 s`, so
    BUG-054 remains open historically, but this pass added no fresh
    reboot, timeout, reset, or truncated-body evidence and should be
    counted as another improved recovery sample
- The latest renamed-device follow-up recheck around
  `2026-05-14T15:42:27Z` to `15:42:35Z` added two concrete updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    the renamed soak target, and the hub detail API stayed converged as
    well: all three hub surfaces still showed `Rebooter - renamed test`
    at `192.168.1.48`, `online` / `heartbeat_state="online"`,
    firmware `0.1.17-dev-central`, heartbeat `2026-05-14T15:42:11Z`,
    and detail `latest_heartbeat.health_state="healthy"` with
    `uptime_s=1144`
  - `.48` strengthened further inside the recovery bucket instead of
    re-strengthening as another masked reboot. The direct local
    root/status/config sweep returned clean `200` responses
    (`0.203 s`, `0.034 s`, `0.057 s`) with local `/api/status`
    reporting `Rebooter - renamed test` / `0.1.17-dev-central` /
    `healthy` at `uptime_seconds=1162`, and the immediate 5-cycle local
    continuity loop then stayed fully clean while local
    `/api/status.uptime_seconds` climbed from `1163` to `1170`.
    Cycle 4 did stretch local `/api/config` to `1.173 s`, so BUG-054
    remains open in a narrower latency-watch shape, but this pass added
    no fresh reboot, timeout, reset, or truncated-body evidence
- The latest renamed-device follow-up recheck around
  `2026-05-14T15:51:47Z` to `15:51:56Z` added two concrete updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    the renamed soak target, and the hub detail page/API stayed
    converged as well: all hub surfaces still showed
    `Rebooter - renamed test` at `192.168.1.48`,
    `online` / `heartbeat_state="online"`, firmware
    `0.1.17-dev-central`, with list heartbeat
    `2026-05-14T15:50:11Z` and detail
    `latest_heartbeat.health_state="healthy"` at `uptime_s=1624`
  - `.48` improved again relative to the prior clean pass instead of
    re-strengthening as another masked reboot. The direct local
    root/status/config sweep returned clean `200` responses
    (`0.432 s`, `0.022 s`, `0.069 s`) with local `/api/status`
    reporting `Rebooter - renamed test` / `0.1.17-dev-central` /
    `healthy` at `uptime_seconds=1723`, and the immediate 5-cycle local
    continuity loop then stayed clean while local
    `/api/status.uptime_seconds` climbed from `1723` to `1729`.
    The prior cycle-4 `/api/config` spike did not repeat; cycle 5 did
    stretch local `/api/status` to `1.038 s`, so BUG-054 remains open
    only as a narrow latency watch, but this pass added no fresh
    reboot, timeout, reset, or truncated-body evidence
- The latest renamed-device follow-up recheck around
  `2026-05-14T16:01:20Z` to `16:03:02Z` added two concrete updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    the renamed soak target, with the rendered list page, list API, and
    rendered detail page all still showing `Rebooter - renamed test`
    `online` on `0.1.17-dev-central` at `192.168.1.48`, so there was
    still no fresh hub list/UI drift in this pass
  - `.48` re-entered the masked-reboot bucket immediately after that
    prior clean window: the fresh local root/status/config sweep still
    returned clean `200` responses (`0.369 s`, `0.021 s`, `0.072 s`),
    but local `/api/status` had already reset to
    `Rebooter - renamed test` / `0.1.17-dev-central` / `healthy` at
    only `uptime_seconds=63`, even though the prior clean pass had
    already reached `uptime_seconds=1729`. The hub detail API briefly
    reflected the restart with `received_at="2026-05-14T16:00:24Z"`,
    `health_state="unknown"`, `uptime_seconds=0`, and
    `wifi_connected=false`, then reconverged on the later rereads to
    `health_state="healthy"` with `uptime_seconds=64` and then `124`
    while the hub list row stayed `online` throughout. The immediate
    5-cycle local continuity loop still climbed from
    `uptime_seconds=63` to `73`, so this pass added another concrete
    BUG-054 reboot/recovery sample rather than a broad outage, but
    cycle 2 also stretched local `/api/status` to `4.029 s`, which
    re-strengthens the narrower latency-watch form of BUG-054
- The latest renamed-device follow-up recheck around
  `2026-05-14T16:11:46Z` to `16:12:45Z` added two concrete updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    the renamed soak target, and the rendered detail page still matched
    `/api/v1/admin/devices/<id>` as well: all four hub surfaces again
    showed `Rebooter - renamed test` at `192.168.1.48`,
    `online` / `heartbeat_state="online"` on
    `0.1.17-dev-central`, with the list heartbeat at
    `2026-05-14T16:12:24Z` and the detail API already reconverged to
    `latest_heartbeat.health_state="healthy"` at `uptime_s=724`
  - `.48` moved back out of the masked-reboot bucket and into another
    concrete recovery window. The immediate local 5-cycle
    root/status/config loop returned clean `200` responses on every
    cycle, local `/api/status` and `/api/config` both kept matching
    `Rebooter - renamed test` / `0.1.17-dev-central`, and local
    `/api/status.uptime_seconds` climbed steadily from `688` to `697`
    with `health_state="healthy"` throughout. This pass added no fresh
    reboot, timeout, reset, or truncated-body evidence, but it keeps
    BUG-054 open in a narrower local-root latency shape because cycle 4
    stretched the root page to `1.128 s` and cycle 5 stretched it to
    `3.547 s` while `/api/status` and `/api/config` stayed fast
- The latest renamed-device follow-up recheck around
  `2026-05-14T16:20:54Z` to `16:21:08Z` added two concrete updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    the renamed soak target, and the rendered detail page still matched
    `/api/v1/admin/devices/<id>` as well: all four hub surfaces kept
    showing `Rebooter - renamed test` at `192.168.1.48`,
    `online` / `heartbeat_state="online"` on
    `0.1.17-dev-central`, with the list heartbeat at
    `2026-05-14T16:20:24Z` and the detail page/API still showing
    `health: healthy` / `uptime_s=1204`
  - `.48` stayed out of the masked-reboot bucket but re-entered the
    stronger local-root integrity bucket. The initial local
    root/status/config sweep still returned clean `200` responses
    (`0.760 s`, `0.023 s`, `0.069 s`) with local `/api/status`
    reporting `Rebooter - renamed test` / `0.1.17-dev-central` /
    `healthy` at `uptime_seconds=1237`, then the immediate 5-cycle
    continuity loop failed on cycle 3 of local `/` after `4.186 s`
    with a truncated-body `ChunkedEncodingError`
    (`IncompleteRead(12211 bytes read, 3812 more expected)`) while
    `/api/status` and `/api/config` both stayed healthy and local
    `/api/status.uptime_seconds` still climbed from `1237` to `1248`.
    That means there was still no new hub UI/API drift and no fresh
    `.48` reboot in this pass, but BUG-054 materially re-strengthened
    again as a local root-response integrity failure rather than only a
    latency watch
- The latest renamed-device follow-up recheck around
  `2026-05-14T16:31:50Z` to about `16:32:20Z` added two concrete
  updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    the renamed soak target, and the rendered detail page still matched
    `/api/v1/admin/devices/<id>` as well: all four hub surfaces still
    showed `Rebooter - renamed test` at `192.168.1.48`,
    `online` / `heartbeat_state="online"` on
    `0.1.17-dev-central`, with the list heartbeat at
    `2026-05-14T16:31:29Z`; the detail API additionally showed the
    newest reboot heartbeat as `latest_heartbeat.last_event_type="boot"`
    with `health_state="healthy"` and `uptime_s=185`
  - `.48` moved back out of the truncated-body bucket and into another
    concrete reboot/recovery sample. The first local `/api/status` read
    in this pass had already reset to `uptime_seconds=207` even though
    the prior `16:20:54Z` to `16:21:08Z` pass had already reached
    `uptime_seconds=1248`, so a fresh reboot happened between runs. The
    immediate 5-cycle local root/status/config loop then stayed clean
    while `/api/status.uptime_seconds` climbed from `232` to `238` and
    `/api/config` kept matching `Rebooter - renamed test`. That means
    there was still no fresh hub identity drift and no repeat
    truncated-body failure in this pass, but BUG-054 remains concrete
    as another reboot/recovery sample plus a narrower local-root
    latency watch because cycle 5 still stretched local `/` to
    `1.205 s`
- The latest renamed-device follow-up recheck around
  `2026-05-14T16:40:42Z` to `16:42:45Z` added two concrete updates:
  - the rendered Devices page still matched `/api/v1/admin/devices` on
    the renamed soak target, and the rendered detail page still matched
    `/api/v1/admin/devices/<id>` as well: all four hub surfaces still
    showed `Rebooter - renamed test` at `192.168.1.48`,
    `online` / `heartbeat_state="online"` on
    `0.1.17-dev-central`, while the detail API additionally showed the
    newest reboot heartbeat as `latest_heartbeat.last_event_type="boot"`
    with `received_at="2026-05-14T16:40:44Z"` and
    `uptime_s=244`
  - `.48` immediately re-strengthened again after the prior
    `16:31:50Z` to `16:32:20Z` reboot sample. The first fresh local
    `/api/status` read had already reset to `uptime_seconds=244` even
    though the prior pass had already climbed to `238`, and the
    immediate 5-cycle local root/status/config loop then climbed only
    from `326` to `331`; cycles 1-4 stayed clean, but cycle 5 failed on
    local `/api/config` after `5.808 s` with a truncated-body
    `ChunkedEncodingError`
    (`IncompleteRead(949 bytes read, 119 more expected)`) while local
    `/` and `/api/status` stayed healthy. A confirming reread later
    showed local `.48` `/api/status.uptime_seconds=384`, so this pass
    added both another concrete masked reboot/recovery sample and a new
    local `/api/config` response-integrity repro for BUG-054
- The same run also added one fresh fleet-side reliability regression
  outside the renamed soak target:
  - `.225` briefly fell into a transient hub `offline` row at
    `2026-05-14T16:41:17Z` even though the first local sweep still had
    the device healthy and reachable at `uptime_seconds=1313`; by the
    next hub detail reread it had already reconverged to `online` with
    `latest_heartbeat.last_event_type="boot"` at
    `2026-05-14T16:42:37Z` and `uptime_s=64`, and the confirming local
    `/api/status` reread showed `uptime_seconds=90`. Treat that as a
    fresh `.225` reboot/recovery event rather than only the standing
    desired-name drift
- The latest renamed-device recheck around `2026-05-14T16:54:00Z` to
  `16:55:45Z`, with immediate local follow-up loops on `.48`, `.207`,
  and `.225`, split the live picture more cleanly again:
  - there was still no fresh hub identity drift on the renamed soak
    target: the hub Devices page, hub detail page,
    `/api/v1/admin/devices`, and `/api/v1/admin/devices/<id>` all kept
    showing `Rebooter - renamed test` at `192.168.1.48`,
    `online` / `heartbeat_state="online"` on `0.1.17-dev-central`;
    the detail API additionally showed
    `latest_heartbeat.last_event_type="boot"` with
    `received_at="2026-05-14T16:55:44Z"` and `uptime_seconds=1144`
  - `.48` improved materially relative to the prior masked-reboot plus
    truncated-`/api/config` sample: the initial local
    `/` + `/api/status` + `/api/config` sweep returned clean `200`
    responses with local `/api/status.uptime_seconds=1097`, and the
    immediate 5-cycle continuity loop then climbed cleanly from
    `1152` to `1158` without any timeout, reset, or truncated-body
    failure. Only a slower `2.209 s` root-page sample and a slower
    `1.725 s` `/api/config` sample remained, so this pass improved
    BUG-054 rather than strengthening it
  - `.225` also improved out of the earlier transient offline/recovery
    event: the hub kept it `online` on `0.1.17-dev-central`, local
    `/api/status` first reported `uptime_seconds=805`, and the
    immediate 5-cycle loop then climbed from `882` to `887` with `/`,
    `/api/status`, and `/api/config` all staying clean
  - the fresh fleet-side reliability regression in this pass was on
    `.207`, not `.48`: the hub still showed `.207` `online` on
    `0.1.16-dev-central` with heartbeat `2026-05-14T16:54:35Z`, but
    local `/api/status` had already reset to `uptime_seconds=208` even
    though the prior run had reached `1619`, and the immediate local
    loop climbed only from `271` to `283`; cycle 1 of local `/` failed
    with truncated-body `ChunkedEncodingError`, and cycle 3 of local
    `/` stretched to `5.945 s` while local `/api/status` and
    `/api/config` stayed healthy. Treat that as another concrete
    BUG-055 reboot/recovery-plus-local-root-failure sample behind a
    healthy hub row
  - `.30` showed no fresh change beyond the standing desired-name
    drift, and `.69` remained the stable offline control
