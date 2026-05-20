# Rebooter-Droids — Product Requirements v1

**Date:** 2026-05-19
**Authority:** Operator (executive takeover package, D-003 → D-010)
**Status:** ACTIVE for v1

> This document is the authoritative product spec for the Rebooter-Droids v1
> consumer-facing experience. Read alongside the master executive package.

---

## 1. Product mission

The best-in-class **safe, reliable, local-first, cloud-optional intelligent
rebooter** for home and small-business networks. Three things must be true at
the same time:

1. A non-technical user can plug it in, pick one of three modes, and get a
   working result in under 5 minutes.
2. A technical user can wire it into Home Assistant / Node-RED / MQTT /
   webhooks and treat it as a first-class smart-home citizen.
3. It never silently does something unsafe with a relay.

## 2. Operating modes (Mode 1 / 2 / 3)

The first-run wizard at `/app/setup` presents three choices in plain language.
Each mode is backed by a canned rule-set generated into the existing watchdog
+ rules engine. **No new core engine.** The advanced view exposes the
generated rules for editing.

### Mode 1 — Smart switch

- Purpose: "Use as a normal Wi-Fi switch."
- Behavior:
  - manual on/off via local UI, hub UI, or API
  - relay restore-on-boot configurable: `last_state` | `on` | `off`
    (default `last_state`)
  - no watchdog rules generated
- Safety:
  - manual button still works (firmware `manual_button_enabled=true`)
  - rapid-toggle debounce (≥1 s between commanded changes)

### Mode 2 — Internet / router / modem auto-restarter

- Purpose: "Restart my modem/router when internet dies."
- Targets:
  - configurable set of up to 10 public IPs / hostnames / URLs
  - default set (3): `1.1.1.1` (Cloudflare), `8.8.8.8` (Google),
    `9.9.9.9` (Quad9)
  - probe kinds: ICMP ping (default) with HTTP(S) GET fallback for hostnames
  - per-target probe interval: 10 s
- Failure condition:
  - **ALL** configured targets must be in failed state for X seconds before
    triggering a power cycle
  - per-target failure = 3 consecutive failed probes
  - prefer "all targets failed" over "any target failed" — this is non-negotiable
- Power cycle:
  - relay OFF for Y seconds (default 5)
  - relay ON
  - wait Z seconds for boot stabilization (default 60) before resuming probes
- Limits:
  - max N cycles before lockout (default 5)
  - cooldown between cycles: 30 s (prevents thrash within a cycle batch)
  - after N cycles → **lockout** (relay left ON, no further cycles)
  - lockout persists across reboot; requires explicit manual clear
  - 30-minute cooldown auto-clears lockout after N cycles
- Settings defaults:
  - X = 180 s, Y = 5 s, Z = 60 s, N = 5, lockout-cooldown = 30 min
  - all editable in advanced view; not in basic view

### Mode 3 — Single-device restarter

- Purpose: "Restart a device (NAS, camera, PC, appliance) when it stops
  responding."
- Target:
  - exactly one IP / hostname
  - probe kinds: ICMP ping or TCP connect (user-selectable; default ICMP)
- Failure condition:
  - X seconds of consecutive failed probes
- Power cycle:
  - relay OFF for Y seconds (default 5)
  - relay ON
  - wait Z seconds for boot stabilization (default 90) before resuming probes
- Limits:
  - max N cycles before lockout (default 3 — stricter than Mode 2 because a
    broken NAS should not be cycled endlessly)
  - lockout-cooldown auto-clear: 1 hour
- Settings defaults:
  - X = 60 s, Y = 5 s, Z = 90 s, N = 3, lockout-cooldown = 60 min

### Mode-picker UI requirements

- Three big buttons with one-sentence descriptions.
- No networking jargon in the basic flow.
- An "Advanced settings" link reveals the rule, the thresholds, and the probe
  details — and lets the user tweak them.
- A "What does this do?" link on every advanced field.

## 3. Multi-Wi-Fi fallback (firmware)

### Storage

- Up to **5 saved networks** in firmware NVS:
  - slot 1 (primary, required)
  - slots 2–5 (optional)
- Per slot: `ssid`, `psk_obfuscated`, `priority` (1–5), `is_hidden` (bool),
  `last_success_at` (epoch, hub-injected on rebind), `last_failure_at`,
  `failure_count`.
- PSK storage: simple at-rest XOR/scramble in NVS — **not** plaintext on flash
  dumps; **not** secure against physical attacker (NVS is recoverable). This
  is not a security promise, it is a no-casual-leak hygiene measure.

### Connect-attempt order

1. **Last-known-good** (`last_success_at` highest) — try first regardless of
   priority.
2. **Priority order** 1 → 5 for all remaining slots.
3. Each slot: try for **30 s** with up to 3 connect retries inside that
   window.
4. Full-pass failure → AP fallback.

### AP fallback

- SSID: `Rebooter-Setup-{LAST6OFMAC}` (existing).
- Captive portal: shows status of each saved network's last attempt + reason
  (e.g., "Trying 'HomeWiFi' — auth failed", "Trying 'Guest' — not found").
- Auto-exit: after **15 min** of no captive-portal client activity, retry
  saved networks once. If still failing, re-enter AP indefinitely.

### What the API never exposes

- `/api/config` returns `psk_masked: "●●●●●●"` per slot, never plaintext.
- `GET /api/config/wifi` returns slot metadata only — never PSK.
- Logs never contain PSK; we never write PSK to event log even on connect
  failures.

### What is **not** in v1

- Per-slot bandwidth/captive-portal heuristics.
- 802.1X / enterprise auth.
- Hidden-network discovery (we honor `is_hidden=true` but don't probe for
  hidden SSIDs).
- Hub-side bulk Wi-Fi configuration push (deferred to v2).

### Pre-seeded default Wi-Fi credentials

**None in production firmware.** All units start in AP mode out-of-the-box.
If a factory / warehouse / QA-provisioning SSID + PSK should be preloaded,
that requires human-owner sign-off — see executive package §10 Q1.

## 4. Safety mandates (non-negotiable)

These are inviolable. Any feature that violates one of these must be redesigned
before it ships.

- **No rapid power-cycle loops.** Lockout after N cycles is mandatory; no
  setting may exceed sensible upper bounds (Mode 2 max N = 10; Mode 3 max
  N = 5; firmware enforces these as ceiling regardless of what the user types).
- **No power cycling during firmware OTA.** Watchdog suspended for the
  duration of `/update` POST + post-OTA boot stabilization window.
- **No hidden cloud dependency.** Device must reach its programmed Mode 2/3
  targets via local network only. The hub is optional. If hub is unreachable
  for any reason, all local watchdog/rebooter logic continues unchanged.
- **No credential leakage.** PSK never in logs, API responses, or commits.
  Stripped at the serializer layer with a test asserting it.
- **No destructive updates without backup.** OTA writes to the inactive
  flash slot; rollback path exists; protected config backup taken before any
  schema-altering config push.
- **No log of secrets.** Auth header values, tokens, PSK — never logged.
- **Clear cooldown / lockout visibility.** UI shows countdown until next
  allowed cycle, current cycle-count, and lockout status. Operator never
  surprised.
- **Clear warnings for risky settings.** Setting X < 30 s in Mode 2, N > 5 in
  Mode 2, or N > 3 in Mode 3 shows an in-UI warning ("Aggressive setting:
  may power-cycle a router that just needs more time to recover").
- **Local-first guarantee.** Device continues to function as Mode 1 / 2 / 3
  even if hub is unreachable, internet is unreachable, or DNS is broken
  (targets that are IPs always work; hostname targets degrade gracefully if
  DNS is down — they count as failed, which is what the user wants for
  Mode 2).

## 5. AI / chat / talk path

### v1 stance

**Architecture-first; no LLM in v1.**

Build a stable **intent vocabulary** on top of the existing REST API. Every
configuration change and read expressible as one of:

| Intent | Example | API mapping |
|---|---|---|
| `get-status` | "Is my internet down?" | `GET /api/status` |
| `set-mode` | "Switch to device watchdog mode." | `POST /api/config/save` body `{mode: "device_watchdog"}` |
| `set-config` | "Change the reboot delay to 10 seconds." | `POST /api/config/save` body `{watchdog: {power_off_seconds: 10}}` |
| `list-events` | "Why did the rebooter restart?" | `GET /api/v1/admin/events?device_id=...` |
| `explain-event` | "Explain the last outage." | `GET /api/v1/admin/events/{id}` + structured rationale |
| `test-action` | "Test notifications." | `POST /api/v1/admin/devices/{id}/test` body `{action: "notification"}` |
| `safe-mode` | "Put the device in safe mode." | `POST /api/config/save` body `{mode: "smart_plug", power: {enabled: false}}` |

### Why this matters now (even without an LLM)

- Forces the API to have stable, documented verbs.
- Each verb is independently testable.
- A future natural-language layer (Claude API, HA Assist, or a local
  small-model) wraps these verbs without product redesign.
- Users get a documented automation vocabulary in v1 even with no chat UX.

### v2 candidates

- Optional chat panel in the hub UI backed by Claude API (operator-provided
  key, off by default).
- Home Assistant Assist integration that maps HA conversation intents onto
  our intent vocabulary natively.

## 6. Notification behavior

- Hub-side notification rules (existing): `send_on_trigger`, `send_on_recovery`,
  `send_on_max_cycles_reached`, `send_test_notification_enabled`.
- v1 transports (via the outbound-webhook templating engine — see
  `2026-05-19-ui-and-integration-plan-v1.md`):
  - Pushover
  - Discord webhook
  - Slack webhook
  - Generic outbound webhook
  - Future: native iOS / Android push (out of scope v1)
- Notification content must include: device name, event type, timestamp,
  current state, link back to the hub UI. Never include PSK or auth tokens.
- "Test notification" is one click in the UI and exercises the configured
  transport without changing relay state.

## 7. Backup / export / restore

- **Export** at `/app/devices/{id}/backup` → JSON config blob.
  - Includes: device_name, mode, targets, thresholds, notification config,
    Wi-Fi metadata (slot, SSID, priority — **never PSK**).
  - Includes hub-managed: assigned rules, schedules, scenes.
- **Restore** at `/app/devices/{id}/restore` → uploads JSON, validates schema,
  asks for confirmation, applies via desired-config push.
  - Restore never moves PSK back to a device; user re-enters PSK after
    restore (intentional, safer than encrypted credentials in JSON).
- **Factory reset** at `/app/devices/{id}/factory-reset` → device-side wipe
  with confirmation; relay forced ON during wipe (safe state); requires AP
  re-provisioning after.

## 8. Out of scope for v1

Explicitly **not** in v1; revisit for v2:

- Cloud relay / remote-access service.
- Native voice control (use HA Assist).
- LLM chat UI.
- Multi-device "scene" rebooter coordination beyond the existing
  `apply_scene` action.
- 802.1X / enterprise Wi-Fi.
- Hub-side bulk Wi-Fi config push.
- Per-device firmware variant management beyond `dev` / `stable` channels.
- Cross-modal multimodal analytics build (RFC-006 schema sections only).
- Mobile app (native). Web UI is mobile-first and sufficient.

## 9. Acceptance criteria for "v1 ready to ship"

- All three modes operable via the setup wizard with default targets.
- All three modes tested end-to-end with at least one bench device per mode.
- Multi-Wi-Fi fallback verified on `.48`: connect to slot 1, fail slot 1,
  verify slot 2 connects within 60 s.
- Safety lockout verified: Mode 2 with unreachable targets reaches lockout
  after N cycles and does not power-cycle again until cleared.
- No PSK appears in any of `/api/*`, hub UI, hub logs, device logs, or
  exported config JSON.
- Setup wizard usability tested with one non-technical user without coaching.
- Intent vocabulary documented at `/api/v1/intents` (machine-readable) and
  in `docs/API.md` (human-readable).
- One worked integration example each for Home Assistant, Node-RED, and
  Pushover.
- CI gate ≥ 850 tests passing (already at ~850; do not regress).
- All P1 safety mandates verified via automated tests.
