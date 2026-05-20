# Firmware Takeover Plan

**Date:** 2026-05-19
**Authority:** Operator (executive takeover package, D-002)
**Status:** ACTIVE — hub team takes lead on firmware design, writing, deployment

> The Rebooter-Droids hub team owns forward firmware progress as of 2026-05-19.
> The firmware team's role becomes consultative. This is a deliberate
> reorganization, not a criticism — the firmware team's 2026-05-19 dossier
> retroactive communication audit (lines 27–185 of the dossier) makes plain
> that the existing cross-team channel had degraded for ~4 days. Centralizing
> ownership inside the hub team's already-shared workflow restores forward
> motion.

---

## 1. Current firmware audit (state at takeover)

Compiled from the 2026-05-19 cold-start dossier, prior memos, and live device
data. Treat this as the **known-state baseline** the hub team inherits.

### Architecture

- **Platform:** PlatformIO, ESP8266 Arduino core.
- **Hardware target list (v1):** Sonoff S31 (Sonoff S31 Lite is functionally
  equivalent; treat both as the single S31 target).
- **Language:** C++ with Arduino libraries.
- **Major modules** (in `src/` + `include/`):
  - `PowerMonitor` — CSE7766 UART frame parser on GPIO3.
  - `central_client` — HTTPS heartbeat/poll loop against the hub.
  - `web_server_manager` — local `/api/...` HTTP server.
  - `config_manager` — NVS-backed config storage + protected backup/restore.
  - `wifi` manager — single-network connect today (multi-network is D-006).
  - `watchdog` — local hardware watchdog timer + boot-stability tracking.
  - `status_payload` (`include/status_payload.h`, `src/status_payload.cpp`) —
    shared serializer used by both the heartbeat POST path and the local
    `/api/system/heartbeat-preview` endpoint. This is the right place to land
    `power_compact` (D-011) cleanly.
- **Heap budget on ESP8266 Sonoff S31:** ~16–23 KB free heap in steady state
  (per dossier live snapshots). This is the binding constraint on every new
  feature.

### Repo / directory structure (current firmware team workstation)

```
C:\dev\rebooter-firmware\
├── include\          # headers, incl status_payload.h, types.h
├── src\              # implementation incl status_payload.cpp,
│                     # central_client.cpp, web_server_manager.cpp,
│                     # config_manager.cpp
├── scripts\          # qa-ota-stress.ps1 and related QA tooling
├── docs\             # firmware-side memos
└── .pio\build\sonoff_s31\firmware.bin    # build artifact
```

### Build / flashing / deployment workflow

- **Build:** `pio run -e sonoff_s31` (PlatformIO).
- **Bench flash (`.48` only):** serial via
  `pio run -e sonoff_s31 -t upload --upload-port COM11`.
- **Wall-device flash:** LAN OTA via `scripts/qa-ota-stress.ps1` (firmware
  team) or hub-managed OTA endpoint (production). LAN OTA is multipart-upload
  HTTP — proven on .67 / .30 / .225 / .69.
- **Hub firmware mirror:** artifacts copied to
  `S:\code\rebooter-droids\data\firmware\dev\` (dev channel) and
  `S:\code\rebooter-droids\data\firmware\stable\` (stable channel).

### Sonoff S31 pin mapping (locked baseline)

| GPIO | Function | Notes |
|---|---|---|
| GPIO0 | Button | Active-low; doubles as boot mode select |
| GPIO1 | UART TX | Reserved; do not use for I/O |
| GPIO3 | CSE7766 UART RX | Power chip telemetry frames @ 4800 baud |
| GPIO12 | Relay | Active-high; controls AC output |
| GPIO13 | LED (blue) | Active-low |

This is the Tasmota/ESPHome consensus pinout for the Sonoff S31. **The firmware
must continue to assume this pinout for any S31 target.** Any board with
different pin assignments (Sonoff Mini, Sonoff Pow R2, etc.) requires a new
PlatformIO env, not a runtime config switch.

### Current implemented features

- Smart-plug Mode 1 (manual relay control via local `/api/...` + hub commands).
- Central hub registration + heartbeat + poll-for-commands.
- Heartbeat carries: firmware_version, local_ip, mode, relay_on, wifi_connected,
  health_state, uptime, incident/hour cycles, recovery_mode,
  auto_recovery_triggered, last_known_good_restored,
  consecutive_unhealthy_boots, in_captive_portal, holdoff/cooldown remaining,
  central_enabled, central_registered, central_state, central_device_id,
  central_heartbeat_age_seconds, power_analytics_enabled, power_chip_type,
  power_sample_rate_hz, power_batch_seconds, `reported_config` (non-secret
  config snapshot).
- OTA via HTTP multipart upload to `/update`; hub-managed firmware fetch.
- Protected config backup / restore (device-side).
- Recovery mode + last-known-good restore.
- CSE7766 real telemetry (live on `.48`): voltage, current, power, apparent
  power, power factor, energy_wh, valid/invalid frame counts.
- Power-sample upload via standalone HTTPS POST to `/api/v1/device/power-samples`
  (**this is the path being replaced by D-011 Option A on constrained units**).
- AP-mode captive portal for first-run provisioning.
- Manual button (relay toggle, ≥ 1 s debounce).
- Local web UI at device IP root.

### Current unimplemented features (v1 firmware backlog)

1. **Multi-Wi-Fi fallback** (D-006). Currently single primary network +
   AP fallback only.
2. **Mode 2 / Mode 3 watchdog logic** as canned firmware-side fallbacks
   when the hub is unreachable. Today: probes run hub-side. v1: device-side
   minimal probe loop (ICMP) to ensure local-first guarantee when the hub is
   down. This is a real new firmware feature, scoped tight.
3. **`power_compact` in heartbeat** (D-011).
4. **Ordered hub-URL fallback list** (D-012). Today firmware has one
   `central_register_url`. v1: ordered list with bounded retry-and-fallthrough.
5. **`source_flags` bit dictionary** consumption (firmware emits the bits;
   hub publishes the dictionary).
6. **Mode-picker handshake** — on adoption, the device receives the
   canned-rule set from the hub; nothing on the firmware side strictly
   requires this, but the local UI should be able to render "current mode:
   Mode 2 — Internet auto-restarter" from `reported_config`.

### Current known bugs / blockers (firmware-side)

- **Low-heap power-upload exception** on `.225` / `.69` with
  `central=true + power=true`. **Resolution path:** D-011 Option A (this
  document).
- **`reset_reason=Exception` residual reboots** in safer profile on `.225` /
  `.69` / `.30`. **Observability gap.** Mitigation: capture ESP exception
  decoder output on next field reboot. Tooling work needed.
- **`.225` recurring boot_warmup loop** (~87–89 s uptime → reset to 5–6 s).
  Suspect: an early-boot allocation pattern that just under-clears free heap;
  worth instrumenting on the next firmware revision.

### Current config schema

- Owned: `docs/firmware-apply-config-schema-v01.md` (hub repo) +
  `ALLOWED_DESIRED_CONFIG_KEYS` in `app/services/device_config.py`.
- Drift: `apply_config.power` key is allowed in API.md and the hub but
  omitted from DEVICE_INTEGRATION.md. **Fix in Sprint 1 (D-016).**
- Top-level keys currently honored end-to-end:
  - `device_name` (verified)
  - `current_mode` (verified — used by mode-picker UX, D-003)
  - `relay_restore_behavior`
  - `monitor_interval_seconds`
  - `boot_warmup_seconds`
  - `manual_button_enabled`
  - `internet.*` watchdog tuning
  - `device.*` watchdog tuning
  - `notification.*` (non-secret fields)
  - `central.enabled`, `central.base_urls`, `central.device_alias`,
    `central.poll_interval_seconds`, `central.heartbeat_interval_seconds`
  - `power.enabled`, `power.heartbeat_path_enabled` (NEW per D-011),
    `power.sample_rate_hz`, `power.batch_seconds`
- New v1 additions:
  - `wifi.networks[]` (D-006) — array of `{ssid, psk, priority, is_hidden}`
  - `central.base_urls[]` (D-012) — ordered list

### Current web UI / API endpoints (device-side, local)

- `GET /` — local SPA shell
- `GET /app.js` — local SPA bundle
- `GET /api/status` — live state (auth optional in v1; auth-gated in v2)
- `GET /api/config` — current config (non-secret view)
- `POST /api/config/save` — partial config update (auth required)
- `GET /api/system/heartbeat-preview` — auth-gated; serves the exact
  heartbeat-shaped JSON
- `GET /api/system/central-diagnostic` — auth-gated; live central state
- `POST /api/system/reboot` — auth-gated
- `POST /api/relay/set` — auth-gated; relay control
- `POST /update` — auth-gated; OTA multipart upload
- AP-mode-only: captive portal at root with config form

### Current OTA strategy

- HTTP multipart upload to `/update` (auth-gated).
- Device writes inactive flash slot; reboots into new image.
- Rollback path exists via "last-known-good" — if 3 consecutive boots fail to
  reach `central_state=heartbeat_ok`, last image restored.
- Hub-managed OTA: hub stages firmware at `data/firmware/dev/` (or `stable/`)
  + emits OTA command to device → device fetches over HTTPS from hub.

### Current Wi-Fi provisioning strategy

- Single saved network in NVS (today).
- On boot, attempts saved network; on failure, enters AP mode
  `Rebooter-Setup-{LAST6OFMAC}` with captive portal at `192.168.4.1`.
- AP password: none in v1 (intentional — user is configuring, not connecting
  to a hostile network).
- **Replacement spec:** §3 of `2026-05-19-product-requirements-v1.md`.

### Current watchdog logic

- Hub-side: rules engine runs probes; rule fires action (relay control).
- Firmware-side: hardware watchdog timer; boot-stability tracker;
  consecutive-unhealthy-boots; holdoff / cooldown windows.
- The "local-first guarantee" — device continues Mode 2/3 logic when hub is
  unreachable — is **not yet implemented in firmware**. v1 spec: minimal
  device-side ICMP probe loop using the user's configured targets when hub is
  silent for > 3 heartbeat intervals.

### Current safety protections

- Holdoff after a power cycle (config: `holdoff_remaining_seconds`).
- Cooldown after max cycles reached.
- Relay restore-on-boot per `relay_restore_behavior`.
- OTA suspend of watchdog during `/update`.
- Manual button gating (`manual_button_enabled`).
- Protected config backup taken before destructive config push.

### Current notification behavior

- Firmware does **not** send notifications directly. Hub owns the notification
  transport (Pushover / webhook / etc.). Firmware emits events; hub
  rule-engine decides.

### Current firmware open questions (the dossier's "operator input needed")

| Question (from firmware) | Disposition (operator) |
|---|---|
| Low-heap power transport redesign | D-001 / D-011 — Option A. Sprint 1. |
| Same-LAN hub routing model | D-012 — ordered list with auto-detected internal URL. |
| Wi-Fi fallback design | D-006 — 5 slots, last-known-good first, AP fallback. Sprint 2. |
| Audit live runtime `public_base_url` | D-013 + S1-4 endpoint. Sprint 1. |
| `www`-stripping risk | D-013 startup warning + admin alert. Sprint 1. |
| Cross-team stability/reliability design | Hub team owns ongoing; this doc is the new shared baseline. |

## 2. Ownership transition

### What changes

| Before | After (2026-05-19) |
|---|---|
| Firmware team writes / builds / flashes; hub team consumes via heartbeat | Hub team writes / builds / flashes; firmware team consults on hardware constraints |
| Firmware repo at `C:\dev\rebooter-firmware` (firmware-team workstation) | Mirror into `firmware/` subtree in `rebooter-droids` for forward work |
| Cross-team handoff via local memos | Cross-team handoff via the shared rebooter-droids repo (notes + commits) |
| Firmware-team-owned dossier | Dossier preserved as historical baseline; forward state lives in the hub repo |

### What does **not** change

- The build target (PlatformIO `sonoff_s31`).
- The pinout assumptions.
- The OTA endpoint contract (`POST /update`, multipart, auth-gated).
- The firmware-version naming convention (`0.1.X-dev-central-safe` etc.).
- The device-side `status_payload` shared serializer pattern.
- The firmware-team's existing artifacts on their workstation — they remain
  the authoritative source for any history-resolution question.

### Operational mechanics

1. **First firmware change under new ownership** = D-011 Option A
   (`power_compact` in heartbeat). Sprint 2 firmware work.
2. **Build env:** hub team sets up a local PlatformIO toolchain (Docker
   container with PlatformIO is fine; matches the existing
   `C:\dev\rebooter-firmware` build).
3. **Source-of-truth migration:**
   - Sprint 2, before firmware work begins: mirror `C:\dev\rebooter-firmware\src\`
     and `include\` into `rebooter-droids/firmware/src/` and `firmware/include/`.
   - Preserve git history if practical (`git subtree add` or a one-shot
     import with attribution in the commit message).
   - The firmware repo on the firmware-team workstation becomes a historical
     branch; do not delete it.
4. **Build pipeline:**
   - PlatformIO `sonoff_s31` env builds via CI on every firmware change.
   - Artifact copy: `firmware/.pio/build/sonoff_s31/firmware.bin` →
     `data/firmware/dev/rebooter-{version}-dev-central-safe.bin` on tagged
     dev builds.
   - Stable promotion: separate manual `make promote-stable` step that copies
     dev → stable channel.
5. **Field rollout:**
   - `.48` (bench) is always first.
   - 24-hour soak with `central=true, power=true` (Option A heartbeat
     piggyback enabled) before promoting to wall devices.
   - Wall devices receive OTA only after `.48` soak passes; in priority
     order `.67` → `.30` → `.69` → `.225` (least settled last).

## 3. Build / deploy path — codified

The "deployable firmware update path into the correct directory" requirement
from the executive takeover prompt is resolved as follows:

```
firmware/                                  ← NEW subtree, mirror of C:\dev\rebooter-firmware
├── include/                               (src + include sync'd from C:\dev tree)
├── src/
├── platformio.ini
├── scripts/
└── .pio/build/sonoff_s31/firmware.bin     ← build output

data/firmware/dev/                         ← EXISTING dev-channel publish location
└── rebooter-0.1.X-dev-central-safe.bin    ← post-build copy lands here

data/firmware/stable/                      ← EXISTING stable-channel
└── rebooter-0.1.Y-central-safe.bin
```

A Sprint-2 task adds a `scripts/firmware-publish.sh`:

```
# pseudo
pio run -e sonoff_s31 -d firmware/
VERSION=$(extract-version firmware/src/version.h)
cp firmware/.pio/build/sonoff_s31/firmware.bin \
   data/firmware/dev/rebooter-${VERSION}-dev-central-safe.bin
sha256sum data/firmware/dev/rebooter-${VERSION}-dev-central-safe.bin > \
   data/firmware/dev/rebooter-${VERSION}-dev-central-safe.bin.sha256
```

This makes the path single-command, idempotent, and verifiable.

## 4. Sprint-1 firmware-related deliverables

None of Sprint 1 requires touching the firmware codebase. Sprint 1 is
**hub-side preparatory work** that lands the additive Option A read path so
the firmware change in Sprint 2 has somewhere to deliver to.

## 5. Sprint-2 firmware deliverables (preview)

1. Set up the `firmware/` subtree.
2. Implement `power_compact` emission in `status_payload` (D-011 Option A).
3. Implement multi-Wi-Fi fallback (D-006).
4. Implement `central.base_urls[]` ordered list traversal (D-012).
5. Soak `.48` 24 h with `central=true + power=true + heartbeat path enabled`.
6. Rollout to `.67`, `.30`, `.69`, `.225` in that order if soak passes.

## 6. Risks specific to firmware takeover

(Mirrored from executive package risk register, restated firmware-specific.)

- **R-01** Option A doesn't fully solve `.225` / `.69` exception reboots.
  Mitigation: keep Option B (lighter dedicated endpoint) on file; instrument
  ESP exception decoder output on next field crash.
- **R-04** Wall-device residual reboots are a deeper bug than transport.
  Mitigation: ship Sprint-2 firmware with exception-decoder logging enabled by
  default in dev channel; correlate decoder output with reboot timestamps to
  identify the root crash site.
- **Heap pressure of new features.** Multi-Wi-Fi (5 slots) + base-URL list +
  `power_compact` all need RAM headroom. Mitigation: each feature has a heap
  budget in its design note; reject features that push steady-state free heap
  below 14 KB on the S31.

## 7. Future hardware targets (post-v1)

Out of scope for v1 but worth noting so we don't paint ourselves into a
corner:

- **ESP32-based Sonoff variants** (Sonoff S31 Lite ZB, future S31-ESP32) —
  vastly more heap; bridge the Option C class-based router naturally.
- **Shelly Plug S / Shelly Plus** — different vendor, different toolchain,
  but the hub-side adoption + heartbeat contract is portable. Would require
  a new PlatformIO env.
- **DIY ESP32 reference design** — a "Rebooter Reference Build" the
  community can flash to off-the-shelf hardware. v2 candidate.
