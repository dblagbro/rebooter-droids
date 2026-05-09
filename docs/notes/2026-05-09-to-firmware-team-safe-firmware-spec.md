# 2026-05-09 — Note to firmware team (round 2)

| Field | Value |
|---|---|
| **To** | rebooter-firmware team (`https://github.com/dblagbro/rebooter-firmware`) |
| **From** | rebooter-droids backend/web team |
| **Date** | 2026-05-09 |
| **Re** | Self-healing OTA — safe-bootstrap + dual-bank fallback |
| **Status** | Draft — operator to redline before sending |

---

Hi firmware,

Operator pushed a new direction this afternoon, paraphrased:

> One painful serial flash on first bring-up, ever. From then on,
> updates are over Wi-Fi. If an update fails (corrupt download,
> crashed firmware, mid-flash power loss), the device automatically
> falls back to a known-good image and keeps retrying the new one
> over Wi-Fi until central pushes a fix. Goal is **never** needing a
> serial re-flash again in the steady state.

We wrote this up as **RFC-005** in the backend repo:

`docs/RFC-005-safe-and-fallback-firmware.md`

Highlights:

- **Three logical firmware slots** on the ESP8266 (4 MB flash):
  - Slot A — safe-bootstrap (~256 KB). Immutable after first
    serial flash. Always-bootable last resort. Knows how to join
    Wi-Fi + pull a main image from central.
  - Slot B — current main firmware. OTA-writable.
  - Slot C — previous known-good main firmware. OTA-writable
    only when slot B is being promoted.
- **Boot-time waterfall.** Bootloader picks B → C → A based on
  `boot_target` in NVS, falling through any slot whose
  CRC/signature fails.
- **Trial-then-promote state machine.** A freshly OTA'd slot B
  starts in `trial`. On each successful boot, `boot_streak++`.
  After N successful boots (default 3), the image is `promoted`
  and copied into slot C as the new known-good. If
  `consecutive_boot_failures` crosses F (default 3), slot B is
  `demoted` and the device falls back to slot C automatically.
- **Slot A is a rescue image only.** It joins Wi-Fi and re-fetches
  a main firmware from central; it does not run the relay
  watchdog, local web UI, or heartbeats. Tiny attack/bug surface.
- **`POST /api/v1/device/failsafe`** — new device-API endpoint we
  agree to add on the backend so the device tells central when a
  slot B → slot C fallback happens. We surface these on the
  Status inbox + device-detail page so the operator sees "this
  version failed on this device" without having to inspect each
  unit.

Items we're asking the firmware team to redline:

1. **Slot sizes** — RFC §4.1 has rough numbers. Confirm or amend.
2. **Boot-streak / consecutive-failure thresholds** — RFC §9 Q3+Q4.
   Plan default N=3, F=3. Bigger = more conservative.
3. **`force` flag in firmware-fetch** — RFC §9 Q2. With dual-bank,
   should `force=true` skip the trial period?
4. **Captive-portal on lost-Wi-Fi recovery** — RFC §9 Q5. Slot A
   could optionally start an AP-mode captive portal if Wi-Fi is
   unreachable for >30 min. Optional.
5. **NVS key layout** — RFC §9 Q7. Concrete suggested keys; reserve
   what you'll need so we don't repaint later.
6. **Flash-tool packaging** — RFC §9 Q8. Single-file Python script
   with `esptool` as a pip dep? Bundled binary? Operator UX call.
7. **Phased rollout** — RFC §10. Backend P1 (host bootstrap library
   + failsafe-event surface) can start independently of firmware
   P2+. Coordinate so by the time the firmware-side cutover ships,
   central is ready to receive failsafe reports.

Constitutional invariants we'd like locked in (§8 of RFC):

- **No firmware update can brick a device.** This is the entire
  point — if it can brick, the design has failed.
- **Slot A is immutable after first serial flash.** Nothing in
  the OTA path may overwrite it.
- **Wi-Fi + central URL config survives a slot fallback.** Both
  live in NVS, not in any of the firmware partitions.

Cost framing for context: the operator hit `ERR_TOO_MANY_REDIRECTS`
on the central web UI today (since fixed in v0.3.7) and a
bulk-delete UI bug yesterday (fixed in v0.3.5). Each of those is
a "click button → bug → not bricked" failure mode that a re-deploy
fixes. **A firmware OTA that bricks a device is a dispatch-a-tech
failure mode** — orders of magnitude more painful. Self-healing OTA
buys us back into the cheap-failure regime.

This is connected to the cross-team work already in flight:

- RFC-002 (firmware-mirror chain) — bootstrap artifact lives in
  the same `/rebooter/firmware/` library this RFC ships.
- The earlier "no device shows online" RCA — once self-healing
  OTA is in, the operator can push a fix to `test-s31-01`'s
  central-transport bug without dispatching a serial cable to
  192.168.1.67.

No urgency on writing code. Read the RFC; redline §9. We'll align
on phasing once you've responded.

Thanks,
**rebooter-droids backend/web team**
