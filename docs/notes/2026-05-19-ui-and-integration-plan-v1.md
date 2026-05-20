# UI Direction + Integration Plan — v1

**Date:** 2026-05-19
**Authority:** Operator (executive takeover package, D-003 / D-010)
**Status:** ACTIVE for v1

> This is the UI direction and integration plan for v1. It builds on the
> already-shipped Tier A–E UI walkthrough (which closed P-UI in v0.5.7x) —
> the UI is no longer in "terrible UI" territory. This document focuses on
> what is **next** for the UI, not a rebuild.

---

## Part I — UI direction v1

## 1. UI design principles

Mobile-first, opinionated, progressive disclosure. The simple path is the
default; advanced controls are one click away but never blocking.

- **One-screen onboarding.** From plug-in to working in three taps.
- **Plain language.** "Restart my modem when internet dies" beats "configure
  watchdog rules."
- **Live everywhere.** Every page that shows state shows it live (already the
  pattern; preserve it).
- **No networking jargon in basic flow.** "Internet target" not "ICMP probe
  endpoint." Jargon is fine inside Advanced.
- **Safety visible.** Cooldown countdowns, cycle counts, lockout state — all
  surfaced, never hidden.
- **Reversible by default.** Every destructive action has a confirmation +
  undo where possible (e.g., factory-reset is confirmed; a config change
  emits an undo toast for 30 s).

## 2. New UI surfaces in v1

### 2.1 `/app/setup` — first-run mode picker

The new front door. Replaces the existing first-screen-after-adoption
experience.

**Layout:**

- Big-title device name.
- Three mode cards:
  - **Mode 1 — Smart switch.** "Just an on/off switch I can control from
    here, my phone, or my smart home."
  - **Mode 2 — Restart my modem/router when internet dies.**
    "Watches for internet outages. Power-cycles your gear automatically."
  - **Mode 3 — Restart one specific device when it stops responding.**
    "Pick a device. Watches it. Reboots it if it stops responding."
- Selecting a card shows mode-specific options inline (no page change):
  - Mode 1: relay restore behavior (last_state | on | off).
  - Mode 2: target list (pre-filled with `1.1.1.1`, `8.8.8.8`, `9.9.9.9`),
    advanced threshold link.
  - Mode 3: target IP/hostname input, probe kind (ICMP / TCP), advanced
    threshold link.
- One "Save and start watching" button.

**On save:**

- Hub generates the canned rule set (Mode 2 → an "all-targets-failed" rule
  with default thresholds; Mode 3 → a single-target rule).
- Hub pushes desired-config to the device.
- Redirects to device detail with a green "Mode N active" badge.

### 2.2 `/app/setup/advanced/{mode}` — advanced view

Reached from the basic mode card. Lets a technical user tune everything:

- Fail window (X), power-off (Y), boot wait (Z), max cycles (N), cooldown.
- Probe kind, probe interval, per-target overrides.
- Notification settings (transports configured in `/app/integrations`).
- Edit the generated rule directly as a final escape hatch.

Every advanced field has a "What does this do?" tooltip.

### 2.3 `/app/devices/{id}` — device-detail upgrades

Already mostly in place after the Tier A–E walkthrough. Confirm + add:

- **Current Mode badge** at the top (Mode 1 / 2 / 3 / Custom). Click → goes
  to `/app/setup` or `/app/setup/advanced/{mode}`.
- **Power telemetry card** (already shipped via B16 Phase 1A) — confirm it
  shows `source` (heartbeat | dedicated | synthetic) per D-011.
- **Watchdog status card** showing live state, countdown to next probe,
  cycle counter, lockout state.
- **Recovery state** rendered distinctly: central disabled / recovery mode /
  registered-but-unhealthy / transport stale / rebind-needed /
  never-heartbeated / healthy.
- **Last-known-good restore** indicator.
- **Quick-action bar:** Reboot device, Test notification, Toggle relay
  (with cooldown gate), Open device's local UI.

### 2.4 `/app/integrations` — new

One page for everything cross-system:

- **REST API:** documentation pointer + API-token management (already shipped).
- **Outbound webhooks:** configured endpoints; "Test" button each.
- **MQTT:** broker URL, credentials, topic prefix, "Test publish" button.
- **Home Assistant:** "Add to HA" widget showing the mDNS service name +
  HA-integration deep-link.
- **Pushover / Discord / Slack:** configured shortcuts (templated webhooks).

### 2.5 `/app/setup/wifi` — multi-Wi-Fi management (post-firmware-takeover)

Reached from device detail. Manage the 5 saved network slots:

- Drag-to-reorder priority.
- Per-slot test ("Try this network now") if device is in AP mode.
- PSK is mask-only after save (`●●●●●●`); user can replace, not view.
- "Forget" per slot.
- "Save and rejoin" — applies and triggers reconnect.

### 2.6 `/app/help` — non-technical user help

New top-level page:

- "What is a Rebooter?" — 2-paragraph plain language.
- "Which mode should I use?" — decision tree.
- "Why did my Rebooter restart?" — explains lockout, cooldown, recovery.
- "How do I add this to Home Assistant / Node-RED?" — step-by-step.
- Searchable. Mobile-friendly. No assumed terminology.

## 3. UI features already shipped (preserve, do not regress)

These have been delivered through the Tier A–E walkthrough and post-refactor
work. Treat as load-bearing — verify any v1 change preserves them.

- Dashboard with live device tiles.
- Device-detail page with all major sections wired (Tier A–E).
- Rules edit form with structured EPG / binding / scene shapes (v0.5.95).
- Scenes library (v0.5.92 / v0.5.93).
- Live "active rules" count on status page (v0.5.96).
- Device-detail Watchdog / Schedule sections (v0.5.97).
- B16 power UI: telemetry card, /app/power fleet page, rollups, cost calc,
  CSV export, power-targeted probes.
- Mobile responsiveness (test_responsive in CI).
- Adoption flow end-to-end (gated by an adoption regression test).

## 4. UI work explicitly **not** in v1

- A native mobile app — web UI is sufficient.
- A chat panel — see AI direction (deferred to v2).
- A real-time WebSocket push for every page — current poll cadence is fine;
  the device-detail page can move to WS later if profiling shows it matters.
- Light/dark theme toggle — auto-detect via `prefers-color-scheme`; no manual
  toggle in v1.

---

## Part II — Integration plan v1

## 5. Integration priorities (ordered)

The v1 integration goal is "anything a typical self-hoster wants to wire up
should take less than 5 minutes." In strict priority order:

### 5.1 Local REST API (already shipped; harden in v1)

- Already at `/api/v1/...`.
- v1 deliverables:
  - **Stable intent vocabulary** per D-007: publish at `/api/v1/intents`
    machine-readable + in `docs/API.md`.
  - **API token management** in `/app/integrations` (already partially shipped).
  - **OpenAPI / Swagger spec** at `/api/v1/openapi.json` (auto-generated).
  - **Rate limiting** documented + enforced (already partially in place).
- Effort: small (mostly documentation + intent vocabulary surface).

### 5.2 Outbound webhooks + templating engine (v1 must-have)

The single highest-leverage v1 integration. One engine; many templates.

- Templating engine that maps internal event payloads → outbound HTTP POST
  to a configured URL with Jinja2-style template body.
- Built-in templates shipped:
  - **Pushover** — user/api-token; standard message format.
  - **Discord** — webhook URL; embed format with color-coded severity.
  - **Slack** — webhook URL; attachment format.
  - **Generic** — JSON body editable by user.
- Per-event subscription: which events trigger which templates.
- Test button each. Outbound destinations restricted by per-target
  "local-target=yes" flag for RFC1918 (executive package §10 Q3).
- SSRF protections: connect timeout 5 s, total timeout 10 s, redirect limit
  3, no metadata-IP destinations (169.254.169.254 etc.) by default.
- Effort: medium. Sprint 2 build target.

### 5.3 MQTT publish (v1 must-have)

State + events published to a broker. Subscriber path (`mqtt://` topic →
internal command) optional in v1.

- Connect to a user-configured MQTT broker (TLS supported).
- Publish topics:
  - `rebooter/{device_id}/state` — JSON state on every change (relay,
    mode, recovery, lockout).
  - `rebooter/{device_id}/event` — JSON event on every event (probe
    failure, power cycle, lockout).
  - `rebooter/{device_id}/power` — last `power_compact` snapshot if
    available (D-011).
- LWT (last will & testament) → `rebooter/{device_id}/state` with
  `{"online": false}`.
- Retain flag: on for state, off for events.
- Subscribe path: out of v1 unless trivial — focus is publish.
- Effort: medium. Sprint 2 / 3.

### 5.4 Home Assistant native integration manifest (v1 should-have)

The existing HA integration is a `ha_state_is` probe (i.e., the hub can read
HA state). v1 adds the reverse: HA sees us natively.

- **mDNS service:** publish `_rebooter._tcp.local.` advertising the hub's
  API root + version + device list endpoint.
- **HA integration manifest:** a documented HA-Custom-Component repo at
  `github.com/dblagbro/rebooter-ha` (v1 = community-custom integration,
  not blueprint-merged). Provides:
  - device discovery
  - switch entities (one per Rebooter device)
  - sensor entities (power, mode, recovery state)
  - service: `rebooter.power_cycle`
- HA users get full automation hooks without writing any YAML beyond adding
  the integration.
- Effort: medium-large. Sprint 3 / 4.

### 5.5 Node-RED examples (v1 nice-to-have)

Works automatically via REST + MQTT. v1 ships:

- **Example flow 1:** "Notify on power cycle" — REST subscribe → push to
  webhook.
- **Example flow 2:** "Coordinated reboot" — multiple devices via
  `apply_scene`.
- **Example flow 3:** "Internet-down alert" — MQTT → notification.
- Effort: small (just docs + JSON flow files).

### 5.6 Pushover / Discord / Slack (via 5.2)

Not a separate integration — falls out of the outbound-webhook templating
engine.

## 6. Integration features explicitly **not** in v1

- **Cloud relay** — D-008, deferred to v2.
- **Native voice assistants** — use HA Assist or Node-RED-routed Alexa
  custom skill; we don't ship our own.
- **Apple HomeKit native** — out of v1 scope (HomeKit Accessory Protocol +
  certification is heavy lift).
- **Matter / Thread** — out of v1; revisit when hardware supports.
- **Zapier / IFTTT** — covered by outbound webhooks + intent vocabulary.
- **AWS / Azure / GCP IoT** — covered by MQTT publish.

## 7. Acceptance for v1 integration ship

- A first-time user can configure an outbound webhook to their preferred
  service (Pushover or Discord) in under 3 minutes.
- A user can `mosquitto_sub -t 'rebooter/#'` and see state + events flow in
  under 2 minutes.
- A Home Assistant user can `Add Integration` → see their Rebooter devices
  imported as switches + sensors in under 5 minutes.
- Each integration has at least one worked example in `docs/integrations/`.
- `/api/v1/intents` returns a stable, documented intent vocabulary.

## 8. Long-form examples to ship in `docs/integrations/`

- `home-assistant.md` — full setup + example automations.
- `node-red.md` — three example flows.
- `pushover.md` — token setup + message format + threshold tuning.
- `discord.md` — webhook setup + role mentions.
- `mqtt.md` — broker config + topic layout + retain semantics.
- `rest-api.md` — already exists; expand intent-vocabulary section.

---

## 9. Summary — Sprint mapping

| Surface | Sprint | Owner | Size |
|---|---|---|---|
| `/app/setup` mode-picker wireframes | 1 | hub | s |
| `/app/setup` mode-picker build | 2 | hub | m |
| `/app/setup/advanced/{mode}` | 2 | hub | s |
| Device-detail Current Mode badge | 2 | hub | xs |
| `/app/integrations` page (scaffold) | 2 | hub | s |
| `/app/help` page + content | 3 | hub | m |
| `/app/setup/wifi` (depends on D-006 firmware) | 3 | hub | m |
| `/api/v1/intents` endpoint + vocabulary doc | 2 | hub | s |
| Outbound-webhook templating engine | 2 | hub | m |
| Outbound webhook built-in templates (Pushover/Discord/Slack) | 2 | hub | s |
| MQTT publish path | 3 | hub | m |
| HA Custom Component repo bootstrap | 3 | hub | m |
| Node-RED example flows | 4 | hub | s |
| mDNS `_rebooter._tcp.local.` advertise | 3 | hub | xs |
| OpenAPI spec generation | 3 | hub | s |
