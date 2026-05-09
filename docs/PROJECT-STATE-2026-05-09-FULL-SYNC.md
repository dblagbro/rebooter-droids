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
