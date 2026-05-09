# RFC-005: Safe-firmware + fallback-firmware (self-healing OTA)

| Field | Value |
|---|---|
| Status | **Draft** (seeded 2026-05-09 from operator request) |
| Authors | rebooter-droids backend/web team |
| Targets | rebooter-firmware (https://github.com/dblagbro/rebooter-firmware), rebooter-droids backend hosting |
| Companion | `RFC-002-firmware-mirrors.md` (mirror chain), cross-team note `docs/notes/2026-05-09-from-firmware-team-rca-response.md` |
| Action | This RFC is the rebooter-droids team's draft to hand to the firmware team for redline. |

---

## 1. Operator's ask, verbatim

> "we should also have a 'safe firmware' flash point and a
> fallback firmware built in for failed flashes; write up the
> spec and give it to me for the firmware team who will get back
> to you on it - so the goal would be they give me a new script
> to flash serial first that hits the safe backup firmware which
> can pull over wifi, then from then on we can flash fast on
> wifi and if something fails on updates in future the devices
> will fail back to a basic one they can pull new ones which
> they try over and over - basically building it self-healing
> with safety built in to not brick devices and need serial
> re-flash."

Translated into requirements:

- **R1.** First-bring-up: a single serial flash of a small, never-
  changes "safe-bootstrap" image. After this, every subsequent
  update happens over Wi-Fi — never serial again.
- **R2.** The safe-bootstrap image is itself capable of pulling
  the next-stage main firmware over Wi-Fi from a known central
  URL (the rebooter-droids firmware library landing under
  `/rebooter/firmware/` per RFC-002).
- **R3.** Every update must be **fail-safe**. If a downloaded
  main-firmware image is corrupt, its SHA-256 mismatches, it
  fails to boot, or it crashes within N seconds of boot, the
  device must **automatically revert to a known-good image**
  without operator intervention.
- **R4.** The known-good image is permanent on the device. It
  cannot be overwritten by the OTA path. It is what the device
  reverts to. Operators NEVER need to re-flash over serial in
  the steady state.
- **R5.** After a fallback, the device keeps **retrying** the
  failed update with bounded backoff, so a transient upload
  problem (server hiccup, half-uploaded file, bad cert) self-
  heals once central is healthy again.
- **R6.** The fail-safe machinery has to be present in the
  safe-bootstrap image AT FLASH TIME. We can't OTA in fail-
  safety later — that's the whole point.

## 2. Why this matters

Today the bring-up loop is: serial flash → device runs main
firmware → main firmware does OTA from central. If anything
during the OTA fails badly enough to brick boot — corrupted
image, mid-flash power loss, signature check off, broken
partition table — the device needs **physical access + USB
serial cable + the operator's PlatformIO toolchain** to
recover. That cost is high.

Self-healing OTA brings the bring-up cost down to a single
serial flash, ever. Every subsequent update — whether it
succeeds or fails — is recoverable over Wi-Fi.

This is the same pattern used by every commercial OTA system
(Sonos, Tesla, every router with dual-bank firmware) and is
table-stakes for an embedded product that ships outside a
hobbyist environment.

## 3. Constraints

- **Hardware.** Sonoff S31 is ESP8266 (4 MB SPI flash). Memory
  budget is tight. Two complete firmware partitions + a small
  bootloader is the typical dual-bank layout for ESP8266; ~1.5
  MB per partition gives plenty of headroom for the
  rebooter-droids firmware which is well under 1 MB today.
- **Power-loss tolerance.** ESP8266 can brown out mid-write
  during an OTA. The fail-safe machinery must tolerate a flash
  that was 70% complete when power dropped.
- **No external watchdog hardware** in the BOM. All recovery
  must come from the on-board hardware watchdog plus software
  state.
- **Network.** Devices run on operator/customer Wi-Fi. Wi-Fi
  may flake. Central URL may flake. Both kinds of flake should
  be self-healing.
- **Operator-friendly.** No "type these 30 commands at a
  serial prompt" recovery procedure. The whole point is to
  remove the serial cable from the steady-state operations
  toolkit.

## 4. Proposed architecture

### 4.1 Three logical firmware "slots"

| Slot | Lives at | Mutable? | Purpose |
|---|---|---|---|
| **A** (safe-bootstrap) | flash partition `boot_a` (~256 KB) | **Never** after first serial flash | Always-bootable image. Joins Wi-Fi. Fetches main firmware over Wi-Fi if no usable main firmware is in slot B/C. Plays the role of "permanent recovery image." |
| **B** (main-current) | flash partition `main_b` (~1.5 MB) | OTA-writable | The currently-running main firmware. |
| **C** (main-previous / known-good) | flash partition `main_c` (~1.5 MB) | OTA-writable, but ONLY when an update is being attempted | The previous main firmware that booted successfully at least once. The device falls back here if the update in B doesn't boot. |

Layout target on the 4 MB ESP8266:

```
┌────────────────────────────┬────────────────┬────────────────┐
│  bootloader                │ slot A (safe)  │ slot B (main)  │
│  + partition table         │ ~256 KB        │ ~1.5 MB        │
├────────────────────────────┼────────────────┼────────────────┤
│  slot C (prev-main)        │  NVS / config  │ reserved/SPIFFS│
│  ~1.5 MB                   │  ~32 KB        │ remainder      │
└────────────────────────────┴────────────────┴────────────────┘
```

(Exact sizes are firmware-team's call; this RFC doesn't pin
flash offsets.)

### 4.2 Boot-time decision flow

```
Power-on / reset
       │
       ▼
┌────────────────────────────────────────────────────────────┐
│ Bootloader picks the boot slot per `boot_target` in NVS:   │
│   1. slot B (current-main)  if `boot_target = B`           │
│   2. slot C (previous-main) if `boot_target = C`           │
│   3. slot A (safe)          if `boot_target = A`           │
│                                                            │
│ If the picked image's CRC/signature fails, fall through    │
│ to the next slot. The waterfall is B → C → A. A is always  │
│ valid by construction (immutable + signed at flash time).  │
└──────────────────────┬─────────────────────────────────────┘
                       │
                       ▼
         Image runs. Sets `boot_streak` in NVS:
         on first boot of a new image, `boot_streak=0`.
         Each successful boot bumps it. Once `boot_streak >= N`
         (default N=3), the image is "promoted" — see §4.3.
```

### 4.3 The "promotion" / "demotion" state machine

Three states for slot B's image:

| State | Meaning | Transition trigger |
|---|---|---|
| **trial** | Just OTA'd. `boot_streak < N`. Failsafe armed. | OTA write completes → enter trial |
| **promoted** | Booted and stayed up at least N times → trusted. Failsafe disarms. | `boot_streak >= N` |
| **demoted** | This image previously failed too many times; the device fell back. Won't try again until central pushes a NEW image. | `boot_streak == 0` AND `consecutive_boot_failures >= F` |

Concretely, on every successful boot:

```
1. Check if slot B is in `trial`.
2. If yes:
     boot_streak++
     if boot_streak >= N:
         state = promoted
         copy the now-trusted slot B image into slot C
         (slot C is "always the last-known-good main firmware")
     else:
         state = still trial — failsafe armed for next boot
3. If no (already promoted), do nothing special.
```

If the device **fails to boot** the slot B image (boot loop, crash
within N seconds of power-on, hardware watchdog reset before the
"I'm alive" pulse fires):

```
1. The bootloader sees boot_streak hasn't bumped after the
   previous boot attempt. consecutive_boot_failures++.
2. If consecutive_boot_failures >= F (default F=3):
     boot_target = C   (revert to the last-known-good)
     mark slot B's state = demoted
     attempt to re-download the demoted version from central
       in case the original download was just corrupt
     OR wait for central to push a different version
```

The device never gets stuck — even if slot C is also bad
(operator pushed two bad versions in a row), the bootloader
falls through to slot A (the immutable safe-bootstrap), which
joins Wi-Fi and re-downloads slot B from central from
scratch.

### 4.4 The retry loop (R5)

When slot B is `demoted`, the device's main task pings central
periodically (default: every 60 seconds for the first 5 minutes,
then every 5 minutes). On each ping, it asks central:

```
GET /api/v1/device/firmware
   → {"target_version": "0.x.y", "download_url": "...",
      "sha256": "...", "force": false}
```

If the device's currently-installed `target_version` matches
what central wants, it stays put. If not, it tries to re-fetch
and write to slot B. If the new write succeeds and boots, slot
B exits `demoted` → enters `trial`.

If central's recommended version is **the same** version that
just failed (operator hasn't pushed a fix yet), the device
keeps retrying with backoff but does NOT replace slot C's
known-good — slot C stays the only thing that's actually
running.

### 4.5 What slot A (the safe-bootstrap) does

Slot A is **the smallest possible image that can do four things**:

1. **Read NVS** to figure out the configured Wi-Fi credentials,
   central URL list, and current `boot_target`.
2. **Join Wi-Fi.** Bounded retry (10 attempts × 5 seconds).
3. **Fetch the latest main firmware** from
   `<central>/firmware/main/stable.bin` (per RFC-002 §7.5
   layout). Tries the URL list in order; SHA-256-checks before
   write.
4. **Write into slot B**, set `boot_target = B`, `boot_streak = 0`,
   reset, hand off.

Slot A does NOT:
- Run the relay watchdog
- Listen on a local web UI
- Send heartbeats

Slot A is **only the rescue image**. It is the floor below
which the device cannot fall. Once slot A successfully writes
slot B and hands off, slot A goes idle until the next reset.

If slot A itself can't reach Wi-Fi or central — e.g., the
operator changed the Wi-Fi password and didn't tell the
device — slot A enters a low-power retry loop. If the retry
window exceeds say 30 minutes with no Wi-Fi, slot A could
optionally (firmware-team's call) start an AP-mode captive
portal so the operator can re-enter Wi-Fi credentials over a
phone — same flow as first bring-up.

### 4.6 What's burned to the device at first serial flash

A single bootstrap-image artifact (`rebooter-bootstrap-<v>.bin`
hosted at `/rebooter/firmware/bootstrap/latest.bin` per
RFC-002) is flashed at offset `0x00000`. That artifact contains:

- The bootloader
- The partition table
- Slot A (safe-bootstrap) image
- An empty slot B and slot C
- `boot_target = A` set in NVS
- Wi-Fi credentials and central URL list either embedded as
  build-time defaults OR set via captive portal on first
  boot (firmware-team picks)

The flashing operator's tool (the new `flash-rebooter.sh` /
`.py` script the operator wants firmware to ship) does:

1. Detect the device on a serial port.
2. Download the latest bootstrap artifact from
   `https://www.voipguru.org/rebooter/firmware/bootstrap/latest.bin`
   (with secondary URL fallback per RFC-002).
3. Verify SHA-256 against the central API's metadata.
4. Flash it via `esptool.py write_flash 0x00000 …`.
5. Optionally, prompt the operator for Wi-Fi creds + central URL
   and post-flash write them into the device's NVS via a one-time
   serial config sequence.

After that, the operator unplugs the serial cable. **They don't
need to plug it back in.** Every subsequent change is OTA.

## 5. Concrete API contracts

### 5.1 Central → device firmware-fetch (already exists; nothing new)

`GET /api/v1/device/firmware` (device-token-authenticated)
returns:

```json
{
  "ok": true,
  "data": {
    "assigned": true,
    "channel": "stable",
    "target_version": "0.x.y",
    "download_url": "https://.../firmware/main/stable.bin",
    "download_urls": ["...", "..."],
    "sha256": "<hex>",
    "force": false
  }
}
```

The `download_urls` array (RFC-002 P3) is the multi-mirror
fallback: device tries each in order until one delivers a
SHA-256-matching binary.

No central-side change required for this RFC.

### 5.2 Device → central failsafe report (NEW)

When a device falls back from slot B → slot C, it should tell
central so the operator can see "this version failed on this
device" without having to inspect each unit.

New device-API endpoint to add:

```
POST /api/v1/device/failsafe
  Authorization: Bearer <device-token>
  body: {
    "device_id": "...",
    "failed_version": "0.x.y",
    "fallback_to_version": "0.x.z",
    "reason": "boot_failure" | "sha256_mismatch" | "watchdog_reset" | "timeout",
    "details": { ...firmware-team-defined diagnostic blob... }
  }
  → 200 ok
```

Server-side: log this into a new `device_failsafe_events` table
(structured, separate from `device_events` for queryability).
Surface on the device-detail page Audit / Events tab AND on the
Status attention feed (new `device_failsafe` attention-item kind).

This is one round-trip per failed update; not high-volume.

### 5.3 Bootstrap flash-tool URLs (operator-facing)

Per RFC-002 §7.5, central will host:

```
/rebooter/firmware/bootstrap/<version>.bin
/rebooter/firmware/bootstrap/latest.bin    (channel pointer)
```

The flash-tool reads `latest.bin` for "freshest bootstrap" and
the corresponding `<version>.bin` for archival/rollback.

## 6. What the firmware team owns

(this is what we are asking the rebooter-firmware team to build)

1. **Dual-bank flash partitioning** + bootloader logic to pick
   between slot A / B / C per `boot_target` in NVS.
2. **Slot A safe-bootstrap image**, ~256 KB, with the four-step
   flow in §4.5.
3. **Boot-streak / consecutive-boot-failures bookkeeping** in
   NVS, with the state machine in §4.3.
4. **OTA write path** that targets slot B (never overwrites
   slot A, only overwrites slot C when slot B is being
   promoted).
5. **Failsafe report** to central on slot B → slot C fallback
   (§5.2).
6. **`flash-rebooter` operator script** that does §4.6 — fetches
   the latest bootstrap from central, verifies SHA, flashes
   over serial, optionally writes Wi-Fi config.

## 7. What the rebooter-droids team owns

1. **Host the bootstrap artifact** under
   `/rebooter/firmware/bootstrap/latest.bin` plus per-version
   archives. Same library mechanism as RFC-002 designs for the
   main firmware.
2. **Add `device_failsafe_events` table** + service +
   `POST /api/v1/device/failsafe` endpoint.
3. **Surface failsafe events** on the device-detail page and as
   a new `device_failsafe` attention-item kind on the Status
   inbox (R-DSH-3 style; high-priority severity).
4. **Add a "Firmware health" panel** to the per-device page:
   "current slot: B (promoted v0.3.7); previous: C (v0.3.6,
   known-good); last failsafe: never" — so operators can see at a
   glance whether a device is in a self-healed state.

## 8. Constitutional invariants (do not violate)

- **No firmware update can brick a device.** If the new image
  is broken, the device falls back. No exception. This is
  R3+R4 combined and is the entire point of the RFC.
- **Slot A (safe-bootstrap) is immutable after the first
  serial flash.** Nothing in the OTA path may ever overwrite
  it. The bootloader must refuse a write to slot A's
  partition.
- **Wi-Fi + central URL config survives a slot fallback.** A
  device that fell back to slot C must still reach central to
  retry. Storing those in NVS (not in the firmware partition)
  is what makes this work.
- **Forward-compatibility.** Future firmware versions must be
  able to reason about a slot A from a much older flash event.
  Slot A's contract — what it expects in NVS, what HTTP it
  speaks to central — must be stable.

## 9. Open questions for firmware team

1. **Slot sizes.** §4.1 has rough numbers. Confirm or amend
   based on actual main-firmware footprint + ESP8266 OTA
   library overhead.
2. **`force` flag** in the firmware-fetch response. Pre-RFC,
   what does the device do on `force=true`? With dual-bank,
   should `force=true` skip the trial period? Probably yes
   for hotfixes but it's the firmware team's call.
3. **Boot-streak threshold N.** Plan default N=3. Bigger = more
   conservative (longer "this is good" period). Smaller =
   accept new firmware faster.
4. **Consecutive-failures threshold F.** Plan default F=3.
   Same trade-off.
5. **Slot A AP-mode captive portal** for "lost Wi-Fi" recovery.
   Optional in §4.5 — confirm desired or skip.
6. **Configuration after flash.** Does `flash-rebooter` push
   Wi-Fi creds + central URL via serial, or via captive portal
   on first boot? Either works; pick one.
7. **NVS layout.** Strongly suggest reserving keys for future
   use now so we don't paint ourselves into a corner.
   Suggested keys: `boot_target`, `boot_streak`,
   `consecutive_boot_failures`, `slot_b_version`,
   `slot_c_version`, `slot_b_state`, `wifi_ssid`, `wifi_psk`,
   `central_urls` (comma-separated), `device_token`,
   `enrollment_token`, `last_failsafe_reason`,
   `last_failsafe_at`. Confirm.
8. **Flashing tool packaging.** Single-file Python script with
   `esptool` as a pip dep? Bundled binary? Operator UX matters.
9. **Firmware library hosting timeline.** RFC-002 P1+ work is
   queued on the rebooter-droids side. Coordinate so the
   firmware team has somewhere stable to publish bootstrap
   artifacts before the flash-tool ships.

## 10. Phased rollout

| Phase | Ships | Owner |
|---|---|---|
| **P0** | This RFC redlined and accepted. | both teams |
| **P1** | Backend: `/firmware/bootstrap/` library hosting per RFC-002 §7.5. `device_failsafe_events` table + endpoint. | rebooter-droids |
| **P2** | Firmware: dual-bank partition layout. Slot A safe-bootstrap with §4.5 flow. Bootloader picks slot per `boot_target`. | rebooter-firmware |
| **P3** | Firmware: boot-streak state machine (§4.3). Slot B → slot C fallback on too many consecutive boot failures. Failsafe report to central. | rebooter-firmware |
| **P4** | Operator: `flash-rebooter` script — fetches bootstrap, SHA-checks, flashes over serial, optionally writes Wi-Fi/central config. | rebooter-firmware |
| **P5** | Backend: surface failsafe events on Status inbox + device-detail Audit tab. | rebooter-droids |
| **P6** | Cutover: ship first build with this layout to the lab fleet. Operator does the one painful serial re-flash for the four lab devices. **From this point forward, no serial cable is needed.** | both teams |
| **P7** | Documentation: operator runbook, firmware-team README updates. | both teams |

## 11. Risks

| Risk | Mitigation |
|---|---|
| Slot A bug shipped at first serial flash → permanently bricked device | Audit slot A code with extreme care; keep it minimal; sign artifacts; SHA-verify at flash time; have firmware team ship a test-ridden v1 of the bootstrap and exercise it against a known-bad slot B in QA before any field-flash |
| Slot A's Wi-Fi or central-URL contract drifts over time → very-old slot A in field can't pull new bootstrap | Treat slot A's contract as a constitutional invariant (§8). Any breaking change is a v2 RFC. |
| Three-slot layout exhausts ESP8266 4MB flash | §4.1 sizes have headroom. Confirm with firmware team's actual byte counts. |
| Power-loss during slot B write | Bootloader + ESP-OTA library already CRC-validate on next boot; failed CRC → fall to slot C. |
| Operator pushes a known-bad firmware to fleet via central | Central side: `force=false` + trial period (R3) catches this naturally — devices fall back to slot C, central sees the failsafe events, operator sees the attention items, operator pushes a fix. |
| Bootstrap artifact at central goes 404 during a slot A re-fetch | Multi-mirror fallback per RFC-002. |

## 12. What lands NOW vs LATER

- **Now (this RFC + small backend prep):** ship the RFC. No
  code change yet. Operator hands the RFC to the firmware team
  for redline. After redline, P1 (backend hosting +
  failsafe-events surface) can start before firmware-side
  P2/P3/P4.
- **Later:** P1 → P7 per §10.
