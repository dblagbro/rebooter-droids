# Executive Takeover Package — Rebooter-Droids

**Date:** 2026-05-19
**Author:** Operator (executive authority granted same date)
**Live version at takeover:** v0.5.102
**Status:** ACTIVE — supersedes all prior "waiting on operator" framings

> The 4-month stagnation ends today. This package is the new operating doctrine
> for the Rebooter-Droids project. Hub team owns forward progress including
> firmware direction. Decisions below are made; tasks are sized; the next
> sprint starts now. Companion docs are listed at the bottom.

---

## 1. Authority & scope

Executive operator authority covers: product direction, firmware direction,
firmware writing/deployment, UI direction, integration design, prioritization,
risk acceptance, and decision-making on anything that is not (a) a secret
credential, (b) legal/compliance, (c) manufacturing commitment, (d) paid
service signup, (e) irreversible destructive action, or (f) genuinely
human-only preference.

The minimal **Questions for Human Owner** list is §10. Everything else is
decided.

## 2. Current state — compressed

We are much further along than the older "saved-state" notes suggest. As of
v0.5.102:

- **P-REG (registrations), P-UI ("terrible UI"), P-QA (CI gate)** — all
  **closed**. UI walkthrough Tiers A–E shipped; CI runs ~850 tests behind
  nginx; in-process `tests/unit/` slices added.
- **B11 multi-hub sync — LIVE in production** since 2026-05-16. Bidirectional
  convergence preflight PASSED 2026-05-19 (create A→B 3 s, delete A→B 5 s).
  My earlier "B11 is a scaffold" finding is **obsolete** — they finished the
  applier + emission + bootstrap-seam in v0.5.70–.72.
- **B17 integrations — fully complete** (Google Calendar OAuth shipped v0.5.94).
- **TV-scheduling feature (Stages A–C, v0.5.90–.93)** shipped — level-triggered
  rules, `binding` rules, `apply_scene` action, scenes library, point-and-click
  EPG-driven scene rules.
- **B16 power analytics** — ingestion + Phases 1A–1D shipped; UI, rollups,
  cost, CSV, power-targeted watchdog probes all live.
- **Firmware baseline** — `0.1.40-dev-central-safe` deployed to .67/.69/.30/.225.
  Wall devices recovered entirely over LAN (no reopening). Real CSE7766
  telemetry live on `.48`.

**Open and material:**

- **Low-heap power-upload transport** — the firmware team's 2026-05-19 cold-start
  dossier formally requests the cross-team design engagement. The hub team has
  a pre-thought-out internal analysis on Option A (heartbeat-piggyback) that
  was marked HOLD pending operator sign-off. (See §3, D-001.)
- **Residual reboot instability on wall devices** even with `power=false` —
  `.225` in a recurring boot_warmup Exception loop, `.69` and `.30` rebooting
  during watches. Observability gap.
- **Hub-side `.225` announce_pending** — not a firmware issue; the hub-side
  adoption flow has a stuck state.
- **Stale `.69` hub row** — duplicate device row cleanup needed.
- **Doc drift** — enrollment-token revocation contract (ADMIN_GUIDE vs API.md);
  `apply_config` `power` key (API.md vs DEVICE_INTEGRATION.md).
- **Multi-Wi-Fi fallback** — explicitly not in current firmware; required by
  product direction.
- **Mode-picker UX (Mode 1/2/3)** — the underlying engine exists (watchdog +
  rules); the simple onboarding UX does not.
- **AI/chat path** — not yet started; architecture-first decision below.
- **Integration breadth** — REST + HA-as-probe + outbound webhooks exist;
  MQTT publish, HA native manifest, Pushover/Discord/Slack templates, Node-RED
  examples not yet shipped.

## 3. Decision log

Each decision is binding from 2026-05-19 unless explicitly revisited.

### D-001 — Lift the HOLD on the low-heap power-upload analysis. Ship Option A.

The firmware team's 2026-05-19 dossier *is* the follow-up memo the HOLD was
waiting on. Adopt **Option A — heartbeat-carried compact power summary** as
the constrained-device transport. (B) lighter dedicated endpoint and
(C) class-based router stay as **fallback** structures, not v1 builds.

The shareable cross-team design proposal lives at
`docs/notes/2026-05-19-cross-team-power-upload-design.md`. It is no longer
internal — send to firmware team.

### D-002 — Hub team takes over firmware ownership, effective today.

- Forward firmware design, writing, and deployment is owned by Rebooter-Droids
  going forward. Firmware team's role becomes consultative.
- **Firmware repo location going forward:** keep `C:\dev\rebooter-firmware` as
  historical reference; **create `firmware/` subtree inside `rebooter-droids`**
  for new firmware work. Mirror the existing source into it on next firmware
  engagement.
- **Build pipeline target:** PlatformIO `sonoff_s31` env; firmware artifacts
  drop into `data/firmware/dev/` (existing workflow — already validated).
- **OTA path stays as-is** — LAN multipart upload via
  `scripts/qa-ota-stress.ps1` (firmware team) / hub-managed OTA endpoint
  for production rollouts. Working well; don't touch.

Detail: `docs/notes/2026-05-19-firmware-takeover-plan.md`.

### D-003 — v1 mode-picker UX

Build a **first-run / settings-level mode picker**. Three modes:

- **Mode 1: Smart switch** — manual on/off + local UI + relay restore behavior.
- **Mode 2: Internet/router/modem auto-restarter** — probes a configurable
  target set; `ALL` failed for X seconds → power cycle.
- **Mode 3: Single-device restarter** — probes one target; failed for X
  seconds → power cycle with stricter caps.

Backed by canned rule sets in the existing watchdog/rules engine. Wizard
generates rules; advanced view exposes them for editing. **No new core engine
required.**

Detail: `docs/notes/2026-05-19-product-requirements-v1.md` §2.

### D-004 — Default Mode 2 targets

`1.1.1.1` (Cloudflare), `8.8.8.8` (Google), `9.9.9.9` (Quad9). Three
independent operators, ICMP-pingable, geographically distributed. Failure
condition: **ALL** three failing for the configured window before triggering a
power cycle. Single-target degradation logs only.

### D-005 — Default thresholds

| Mode | Fail window (X) | Power-off (Y) | Post-power-on wait (Z) | Max cycles | Cooldown |
|---|---|---|---|---|---|
| Mode 2 (router/modem) | 180 s | 5 s | 60 s | 5 | 30 min |
| Mode 3 (single device) | 60 s | 5 s | 90 s | 3 | 1 hr |

Lockout after max cycles requires manual clear via local UI or hub. Lockout
state survives reboot.

### D-006 — Multi-Wi-Fi fallback (firmware)

Up to **5 saved networks**. Ordered fallback:
1. last-known-good (boot-time preference)
2. priority slots 1 → 5

30 s per attempt; full-pass failure → AP fallback
`Rebooter-Setup-{LAST6OFMAC}` + captive portal (existing). AP auto-exit after
15 min idle, retry saved networks once, re-enter AP if still failing.

**No pre-seeded default Wi-Fi credentials in production firmware.** Storage:
at-rest obfuscated in NVS, never returned in API responses (mask as ●●●●●●),
never logged.

Open question to firmware team only if blocking: was there a previously
hardcoded "default" Wi-Fi for QA/factory provisioning we should preserve as a
factory-test SSID? Default answer: **no.**

Detail: `docs/notes/2026-05-19-product-requirements-v1.md` §3.

### D-007 — AI / chat / talk path

**Architecture-first in v1; no LLM integration in v1.** Build a stable intent
vocabulary on top of the existing REST API:

- `get-status` / `set-mode` / `set-config` / `list-events` / `test-action` /
  `explain-event`

A future natural-language layer (Home Assistant Assist, or a Claude-API chat
panel as an optional plugin) sits on top **without product redesign**. Do not
ship a chat UI in v1.

Detail: `docs/notes/2026-05-19-product-requirements-v1.md` §5.

### D-008 — Cloud relay

**Deferred to v2.** Local-first is the strategic moat; the active-active
multi-hub design already covers remote-access for self-hosters.

### D-009 — Voice control

**Via Home Assistant Assist or Node-RED.** Do not build a native voice layer in
v1. HA Assist already provides Alexa / Google Assistant integration when the
user already has those.

### D-010 — Integration v1 priorities (ordered)

1. **REST API** — exists; harden, version (`/api/v1/`), document, expose intent
   vocabulary from D-007.
2. **Outbound webhooks** — finish a templating engine that maps Discord, Slack,
   Pushover into one outbound-webhook framework.
3. **MQTT publish** — new; publish state + events to a broker; subscriber path
   optional.
4. **Home Assistant native integration manifest** — mDNS-discoverable;
   HA's "Add Integration" sees us and pulls in entities.
5. **Node-RED examples** — works automatically via REST; ship example flows.

Detail: `docs/notes/2026-05-19-ui-and-integration-plan-v1.md`.

### D-011 — Firmware power-upload contract

Per D-001: heartbeat gains an optional `power_compact` object. Hub-side change
ships **first** (additive, harmless on roomy devices). Firmware change ships
**next**. Per-device runtime gate `power.heartbeat_path_enabled` (default OFF)
flips per device during rollout. ESP8266-class devices migrate; ESP32-class
keeps the dedicated `/api/v1/device/power-samples` endpoint.

`power_compact` minimum: `{"p_w": <float>}`. Optional fields:
`v_v`, `i_ma`, `i_ma_estimated`, `i_ma_estimate`, `source_flags`,
`sampled_uptime_seconds`, **plus** `valid_frame_count` and
`invalid_frame_count` (which closes the firmware team's open ask about getting
frame counts into the heartbeat).

### D-012 — Same-LAN routing

Devices receive an **ordered list of hub base URLs** from the hub on
adoption/announce:

1. internal/private hub URL (if auto-detected — same /24 as the requesting device)
2. `https://www.voipguru.org/rebooter`
3. `https://www2.voipguru.org/rebooter`

Auto-detection: the hub uses the device's `X-Forwarded-For` / peer subnet
against its own LAN address. If not in the same /24, omit the internal URL.
Firmware tries each in priority order with bounded retries; falls through on
DNS/connect/TLS failure.

Hub adds `/api/v1/admin/network-truth` exposing the live `public_base_url`,
env override, DB override, and emitted `central_register_url` template.

### D-013 — www.voipguru.org is a required hostname

Hub adds startup validation: if `public_base_url` lacks `www.` for the
voipguru.org host, emit a hub-status warning **and** an admin-page alert. Do
not auto-rewrite — surface it so the operator catches a misconfigured override.

### D-014 — Fix the `.225` announce_pending hub bug

Sprint-1 task. The firmware-team dossier and the wall-stability memo both
confirm `.225` is a hub-side adoption flow stuck-state, not a device fault.

### D-015 — Stale `.69` hub row cleanup

Sprint-1 task. Add an admin action to merge/retire duplicate device rows. The
device is healthy on the newer row; retire the older one.

### D-016 — Doc drift fixes (from upgrade-sweep memo)

Sprint-1 tasks:
- Reconcile enrollment-token revocation between `ADMIN_GUIDE.md` (says cannot
  be revoked pre-redemption) and `API.md` (documents `DELETE
  /admin/enrollment-tokens/{token_id}`). The API endpoint exists — fix the
  guide.
- Reconcile `apply_config` schema: `API.md` lists `power` as allowed top-level
  key; `DEVICE_INTEGRATION.md` omits it. The hub accepts it — fix
  `DEVICE_INTEGRATION.md`.

### D-017 — Firmware-team open asks: disposition

- **`source_flags` bit dictionary** — hub team owns this now. Define the bit
  dictionary on the hub side as part of the P1.2 data-quality surface work;
  firmware adopts whatever the hub publishes. (Tracked in P1.2 → Sprint 2.)
- **Frame counts in heartbeat** — folded into D-011 (`power_compact`).
- **G2 cross-device time-sync measurement** — **deferred to v2**. RFC-006
  Decision 6 stays open; we will not block v1 on tight-window multimodal
  analytics.
- **`.69` device pre-0.1.19** — backlog claim is stale; firmware dossier
  confirms `.69` is on `0.1.40-dev-central-safe`. Mark stale and dismiss.

### D-018 — RFC-006 (multimodal ingest) — write the schema sections, defer the build

Lock the schema-shape decisions now (common envelope, modality-specific
stores, mixed transport, independent adapters), defer the cross-modal query
layer build to v2. Cheap to decide, expensive to retrofit later.

### D-019 — Doc-truthfulness pass

A doc reconciliation pass is **continuing**, not new. Recent commits already
retired the "sync.enabled is off" framing. Continue with the stale-docs
checklist from
`docs/notes/2026-05-15-hub-doc-reconciliation-checklist.md` plus the doc
drift fixes in D-016. Target: zero stale "do not implement" headers, zero
contradiction between ADMIN_GUIDE / API.md / DEVICE_INTEGRATION.md.

## 4. Owner assignments — Rebooter-Droids team

- **Forward firmware design + writing + deployment:** Rebooter-Droids hub team
  (lead). Firmware team is consultative.
- **Hub-side power-upload contract (D-011):** hub team — additive heartbeat
  read first, then per-device gate flag.
- **Same-LAN routing (D-012, D-013):** hub team — `/api/v1/admin/network-truth`
  + startup `www` validator.
- **`.225` adoption fix (D-014) + `.69` cleanup (D-015):** hub team — admin
  flow + UI.
- **Doc-drift fixes (D-016, D-019):** hub team — doc-only ship.
- **Mode-picker UX (D-003):** hub team — `/app/setup` wizard + canned
  rule generators.
- **Multi-Wi-Fi fallback (D-006):** hub team firmware-side after the takeover
  setup completes.
- **AI intent vocabulary (D-007):** hub team — define the intent shape; no UX
  in v1.
- **Integration v1 (D-010):** hub team.
- **RFC-006 schema sections (D-018):** hub team — write before any P2 source
  ships.

## 5. Sprint 1 task list (executable now)

Ordered by readiness. None of these requires the firmware team. **No deploy
required yet** — these are buildable in the hub repo without device contact.

| # | Task | Owner | Size | Notes |
|---|---|---|---|---|
| S1-1 | Lift HOLD; rename internal power-upload doc to a shareable cross-team design proposal (D-001) | hub | xs (doc) | Already produced by this package — `2026-05-19-cross-team-power-upload-design.md`. |
| S1-2 | Implement Option A hub-side: `ingest_compact_power_sample()` + heartbeat-read path + `source="heartbeat"` enum value (D-011) | hub | s | ~30 LOC + 3 tests. Additive, harmless on roomy devices. |
| S1-3 | Runtime setting `power.heartbeat_path_enabled` per-device (D-011) | hub | s | Default false. Per-device flip on rollout. |
| S1-4 | `/api/v1/admin/network-truth` endpoint (D-012) | hub | s | Reports live `public_base_url`, env, DB override, emitted register URL. |
| S1-5 | Startup `www` enforcement warning + admin-page banner (D-013) | hub | xs | Warn-only initially. |
| S1-6 | Adoption-flow fix for `.225` announce_pending (D-014) | hub | m | Add one-click "force adopt" action; clear the stuck state. |
| S1-7 | Duplicate-device admin action (D-015) | hub | s | Merge/retire UI button. |
| S1-8 | Doc drift: enrollment-token revocation + `apply_config.power` (D-016) | hub | xs | Doc-only. |
| S1-9 | Sketch `/app/setup` mode-picker wireframes (D-003) | hub | s | Wireframes only this sprint; build in S2. |
| S1-10 | Draft RFC-006 schema sections (D-018) | hub | m | Common envelope + modality stores + mixed transport. Defer query layer. |
| S1-11 | Outbound-webhook templating engine sketch (D-010 #2) | hub | s | Discord/Slack/Pushover under one templated framework. |

Sprint goal: ship the additive hub-side Option A read path, fix the two stuck
device states, close two doc-drift bugs, and lay out the v1 UX direction. No
firmware change required this sprint.

## 6. Sprint 2 preview

- Build the `/app/setup` mode-picker (D-003) end-to-end with the three modes
  wired to canned rule generators.
- Build the outbound-webhook templating engine + Discord/Slack/Pushover
  templates (D-010 #2).
- Multi-Wi-Fi fallback firmware work begins (D-006). This is the hub team's
  first formal firmware contribution under D-002.
- Begin MQTT publish stub (D-010 #3).
- Power-upload Option A firmware change is staged for OTA when the hub-side
  read path is on production for ≥7 days.

## 7. Risk register

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-01 | Option A heartbeat-piggyback doesn't fully solve `.225`/`.69` exception reboots — root cause is heartbeat itself, not transport stack | low–med | high | Fallback to Option B (lighter dedicated endpoint) is already on file; ESP32-class units stay on dedicated endpoint regardless. Soak 24 h before declaring victory. |
| R-02 | Multi-Wi-Fi storage exposes credentials | low | high | At-rest obfuscation in NVS; never return PSK in API responses; never log. Hard-coded API filter strips PSK in serializers. |
| R-03 | `www`-stripping runtime override on a hub silently breaks adopted devices | med | med | D-013 warn + alert + `/api/v1/admin/network-truth` audit endpoint. Tighten to hard-fail after a v1 cycle if no false positives surface. |
| R-04 | Wall-device residual Exception reboots are a deeper bug than transport alone | med | med | Observability work: capture ESP exception decoder output via local UART when a wall device cycles. Coordinate with firmware team. |
| R-05 | Hub-side adoption stuck-state recurs after S1-6 fix | low | low | Add a regression test in the adoption test suite. |
| R-06 | Mode-picker UX confuses users with too many advanced knobs | med | med | Advanced settings hidden by default; "What is this?" tooltips on every advanced field; user-test with one non-technical person before v1 cut. |
| R-07 | Firmware team push-back on hub team taking firmware ownership | low | med | Engagement is collaborative not adversarial; consultative role retained for firmware team; their dossier explicitly requested cross-team design help. |
| R-08 | Outbound-webhook template engine is a security footgun (SSRF, internal target abuse) | med | med | Restrict outbound webhook destinations to operator-allowlisted hosts; explicit deny on RFC1918 unless flagged "local-allowed." |
| R-09 | Multi-Wi-Fi attempt cycle delays initial connect dramatically (5 × 30 s = 150 s) | med | low | Skip slots with no SSID configured; last-known-good first; surface "trying network X of Y" in AP captive portal. |
| R-10 | Cloud relay deferral becomes a competitive gap if a major competitor ships one | low | low | v2 candidate. Architecture preserves the option via the same-LAN routing list (D-012). |

## 8. Operator log — entry 1

- 2026-05-19 — Executive authority assumed. Lifted HOLD on the internal
  low-heap power-upload analysis (D-001). Decided Option A as the constrained
  transport. Decided hub-team takes over firmware ownership (D-002). Confirmed
  Mode 1/2/3 product direction with concrete defaults (D-003 → D-005).
  Declared multi-Wi-Fi spec (D-006). Declared AI architecture-first stance
  (D-007). Declared v1 integration priorities (D-010). Documented same-LAN
  routing decision (D-012) and `www` enforcement (D-013). Sprint 1 task list
  set.

## 9. What needed firmware team / no longer does

Of the items in the firmware team's 2026-05-19 dossier "Specific Help
Requested" section:

| Firmware request | Disposition |
|---|---|
| Help redesign low-heap power ingest path | D-001 / D-011 — Option A. Cross-team design doc shipped. |
| Help with stability/reliability/resiliency design | Hub team takes lead on cross-cutting work going forward (D-002); firmware-team specifics flow through this package. |
| Configurable ordered Wi-Fi fallback chains | D-006 — hub team owns the firmware work for this. |
| Define correct same-LAN hub routing behavior | D-012 — decided. Ordered list, auto-detected internal URL. |
| Audit live runtime network/public-base settings | D-013 + S1-4 (`/api/v1/admin/network-truth`). |

All five firmware-team asks are now either decided or in Sprint 1.

## 10. Questions for the human owner (minimal)

Only items that genuinely require human-only information are below. Default
answers are recorded so work doesn't stall if no response comes.

1. **Pre-seeded production Wi-Fi credentials?** Should production firmware
   ship with any default factory/QA SSID + PSK preloaded for warehouse
   provisioning, or do all units start in AP mode? **Default decision (if no
   answer in 5 days): no pre-seeded credentials; all units start in AP mode.**
2. **Internal hub URL for `.18.x`-subnet devices?** What is the LAN-local
   address (e.g. `http://192.168.18.10/rebooter`) for hubs that serve devices
   on the `192.168.18.x` subnet, if applicable? **Default decision (if no
   answer in 5 days): auto-detect via same-/24 heuristic per D-012; no
   manually-configured internal URL.**
3. **Outbound webhook destinations — RFC1918 allowed?** Should the outbound
   webhook engine allow private/internal HTTP(S) targets (e.g. local
   Home Assistant, local Node-RED), or restrict to public destinations?
   **Default decision: allow RFC1918 destinations behind an explicit
   per-target "local-target=yes" flag; default-deny otherwise.**
4. **Cloud relay v2 — interest?** Is cloud-hosted optional remote access on
   the v2 roadmap, or is the active-active multi-hub story sufficient?
   **Default decision: defer indefinitely; revisit if a competitive gap
   surfaces.**

If any of these answers diverge from defaults, raise them at standup; nothing
in Sprint 1 is gated on a non-default answer.

## 11. Companion docs

This package is the master. Detail lives in:

- `docs/notes/2026-05-19-product-requirements-v1.md` — Mode 1/2/3 spec,
  multi-Wi-Fi spec, safety mandates, AI intent vocabulary.
- `docs/notes/2026-05-19-firmware-takeover-plan.md` — current firmware
  architecture audit, ownership transition, build/deploy/OTA pipeline.
- `docs/notes/2026-05-19-cross-team-power-upload-design.md` — the
  HOLD-lifted shareable design proposal for the firmware team.
- `docs/notes/2026-05-19-ui-and-integration-plan-v1.md` — UI v1 mode-picker +
  integration v1 priorities.
- Existing: `docs/notes/2026-05-19-cold-start-dossier-from-firmware.md` —
  authoritative firmware state at handoff.
- Existing: `docs/notes/2026-05-19-READ-THIS-FIRST-firmware-handoff.md` —
  pointer for future sessions.
- Stale-doc cleanup worklist (continuing):
  `docs/notes/2026-05-15-hub-doc-reconciliation-checklist.md`.

---

*This package is the active operating doctrine. Treat older
"saved-state" / "waiting on operator" framings as superseded.*
