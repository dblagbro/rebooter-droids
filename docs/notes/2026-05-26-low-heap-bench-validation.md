# Bench validation of firmware 0.2.0 vs the low-heap `central+power` crash

**Date:** 2026-05-26 (evening EDT) / 2026-05-27 UTC.
**Operator-driven test session.**
**Status:** **Bug is NOT closed by 0.2.0.** Wall-plugged + live CSE7766 frames reproduces the crash. 0.2.1 fix attempt was flawed and reverted.

## TL;DR (corrected)

- Bench (.188, no wall AC, CSE7766 silent) on 0.2.0 with `central+power=true`: **stable**. This is a false-positive — the dynamic frame-handling path was never exercised.
- Wall-plugged (.185, live CSE7766 frames) on 0.2.0 with `central+power=true`: **exception-cycle every ~250 s**.
- 0.2.0 ships heartbeat-piggyback power upload (`power_upload_mode: "heartbeat_piggyback"`) by default. The mechanism is the Option A approach the 2026-05-19 analysis recommended — but it doesn't go far enough.
- Disabling `power_analytics_enabled` on .185 was the only thing that stopped the cycle (and even then, only intermittently — see "After-effects" below).
- Two root causes identified in `central_client.cpp`. A first-pass 0.2.1 fix was built and pushed to .185 — it introduced its own instability and was reverted.

## What was done

1. Promoted `0.2.0-dev-central-safe` (`d8a1a36f…`, 697,184 bytes, built 2026-05-20) to the hub's stable channel:
   - `data/firmware/stable/latest.bin` swapped (rollback at `latest.pre-0.2.0.bak`).
   - Registry row added: `fwr_01KSKPDPA66T9F0FG4PXR7M041`.
   - Channel-pointer redirect `…/api/v1/firmware/stable/latest` resolves to the new bin.
2. **.188** — fresh Sonoff S31, serial-flashed full 0.2.0 directly (bench, no wall AC). Enrolled to hub via short-TTL token (`et_…twxglJUVmHYA`). Hub id `dev_01KSKMZR2XRP1Y24ZA0AT7YW2Q`. Flipped `central=true`, then `power=true` (the unstable combo).
3. **.185** — previously on 0.1.40 with central=true / power=false, 4.16-day uptime. OTA-upgraded to 0.2.0 via `POST /api/system/ota` (multipart upload). Came back on 0.2.0 with central config preserved; left `power=false` as the A/B control.

## Snapshot — fresh-boot device with central+power both on (.188)

| Field | Value |
|---|---|
| firmware_version | 0.2.0-dev-central-safe |
| central_enabled | true |
| central_registered | true |
| central_state | idle → heartbeat_ok (cycles) |
| power_analytics_enabled | true |
| power_upload_mode | heartbeat_piggyback |
| free_heap (pre-power) | 21,760 |
| free_heap (post-power) | 20,808 — ~952 B for PowerMonitor statics |
| reset_reason | Power On, then Software/System restart (controlled reboot only — no Exception) |
| consecutive_unhealthy_boots | 0 |
| health_state | healthy |

After a controlled reboot with both flags persisted: clean boot, both flags re-applied, no exception, heartbeating to hub within 60 s.

## Caveat: CSE7766 silent on bench

`power_chip_seen: false`, `power_valid_frame_count: 0`, `power_invalid_frame_count: 0`. The CSE7766 sits on the high-voltage side of the S31 and needs **live wall AC** to power up — bench setup using FTDI for the MCU side alone leaves the metering chip dark, so no UART bytes on GPIO3. That means today's run exercised the **static-allocation** half of the heap pressure (PowerMonitor's buffers persistent on `power.enabled=true`) but **not** the dynamic frame-handling path (frame parsing → bounded aggregation → heartbeat-envelope encode). Full validation needs a wall-plugged unit.

## Adjacent hub-side evidence

- `dev_01KRQBRSG1BZ5SR87QQ2KSSVFT` (0.1.40-dev-central-safe): last heartbeat 2026-05-22 18:13 UTC — silent ~4.5 days. Consistent with the historical 2026-05-17 crash finding on this version.
- `dev_01KRHTH2DQSTH1PAXBJD9P2XFY` (0.1.37-dev-central-safe): last heartbeat 2026-05-24 01:37 UTC — silent ~3 days.

## Soak in progress

Both devices polled every 60 s by `~/rebooter-soak-logs/soak-watch{,-185}.sh`. Looking for:
- `reset_reason: Exception` on .188
- `consecutive_unhealthy_boots > 0` on either unit
- Drift in `free_heap` past ~12,000 (event-log refuses growth threshold)
- Drop-off in hub-side `last_heartbeat_at`

If 24-h soak holds clean: the heartbeat-piggyback design closed the previously-unstable combo on bench, pending wall-AC validation.

## Root causes (in `central_client.cpp`)

1. **`postWithFallback` allocates a fresh `BearSSL::WiFiClientSecure` every call** (line 384). Under sustained low heap, repeated alloc/free fragments the heap until a future allocation throws an exception.
2. **`power_aggregate` is only reset on heartbeat SUCCESS** (line 1272). When heartbeats fail under transport pressure, the aggregate window grows unbounded — observed `sample_count` going 459 → 1223+ same `window_start_uptime_seconds` on .185. This grows the heartbeat body, which compounds (1).

## After-effects on .185

Today's cycle of OTA-upgrade + 0.2.1 attempt + revert + factory-reset destabilized .185 well past the original bug:

- Pre-today: 0.1.40-dev-central-safe, `central=true, power=false`, **4.16-day uptime**, heap 19,992.
- Mid-day: 0.2.0 fresh OTA, `central+power=true`, exception-cycle every ~250 s.
- After disabling power: brief monotonic uptime, then back to cycling (60-120 s cycle).
- After 0.2.1 push + revert: still cycling, heap reading ~13K (vs .188's fresh-flash 22K).
- After factory-reset via API: device unreachable on LAN. Likely in AP-provisioning mode or in a faster reboot cycle that misses LAN scans.

The ~7 KB standing-heap delta between .185 (LittleFS state carried forward) and .188 (fresh erase-flash) suggests the Tier-2 release's standing heap cost is at the edge of what an ESP8266 can sustain when LittleFS already holds prior-version state.

## What this doesn't tell us yet

- Whether a serial-reflash + erase-flash on .185 (full wipe of LittleFS) would put it on the same footing as .188 (~22 K free heap).
- Whether the right fix is just to drop the heap floor much lower (12 K) and accept that .185-class devices need a fresh-flash on the next upgrade.
- Whether other Tier-2 features (LAN discovery beacon, on-flash crash capture, multi-WiFi state machine) cumulatively contribute to the ~7 KB delta and could be selectively disabled on memory-constrained units.

## Stashed 0.2.1 fix attempt

`git stash@{0}` in `rebooter-firmware/` carries:
- `HEARTBEAT_HEAP_FLOOR = 14000` — skip heartbeat if free heap is below this.
- `AGGREGATE_FORCE_RESET_MULTIPLIER = 2` — force-reset the power aggregate if its window is older than 2× the configured heartbeat interval, even on failure.

The 14 K floor was too high for .185 (operating heap ~13 K) so heartbeats would never have fired on that unit. The aggregate force-reset is gated on `power.enabled` so it wouldn't have run on the power-off baseline. Despite that, 0.2.1 reboot-cycled .185 — root cause for 0.2.1's added instability still TBD; likely interaction between OTA-write boot logic + low heap, not the code changes themselves.

## Next iteration (when .185 is recoverable)

1. Once .185 reappears on LAN: capture baseline heap & uptime on whatever firmware it lands on.
2. If still on 0.2.0 with cycling: serial-reflash + erase-flash to fresh 0.2.0 — re-establish .185 as a wall-plugged baseline parallel to .188.
3. Build 0.2.2: just the heap-floor fix at a more conservative threshold (~12,500 — above the event-log refusal threshold of 12,000, below typical operating heap). Skip the aggregate force-reset for now; isolate one variable at a time.
4. OTA 0.2.2 to wall-plugged .185, enable power, observe.

## 2026-05-27 update: 0.2.3 closes the bug

**0.2.3-dev-central-safe** adds one structural change: the `BearSSL::WiFiClientSecure` used by the central client is now a long-lived member allocated once in `CentralClient::begin()`, reused for every HTTPS call. Eliminates the per-call alloc/free that was fragmenting the heap.

| | 0.2.0 baseline | 0.2.3 with pool |
|---|---|---|
| Free heap fresh boot (.188) | 22,592 | 15,408 |
| Free heap steady-state (.188) | ~20,500 | ~8,500 |
| Free heap steady-state (.185 wall + frames) | 16K, dropping under failure | ~9K, **stable** |
| Behaviour under `central+power=true` on .185 | Exception-cycle every ~250–500s | 8 minutes clean, 1 controlled reboot, then 9+ min clean (in progress) |
| `consecutive_unhealthy_boots` | 0 (didn't classify the cycle as unhealthy) | 0 |
| `reset_reason` after the one reboot in 15-min window | Exception | Software/System restart (no exception) |

The standing heap is **lower** under 0.2.3 (BearSSL session is permanently held instead of malloc'd per call) but it is **stable** — no fragmentation drift, no exception. The bug class changed from "memory exception every ~5–10 min" to "occasional reset every ~10 min", and the second class may be a soft-WDT during a long handshake — investigated as a follow-up.

Built artifact: `data/firmware/stable/rebooter-0.2.3-dev-central-safe.bin` (sha256 `1767ae46…`, 697,104 bytes). Source change: `rebooter-firmware` commit `092daac`. Live on .188 (bench) and .185 (wall + frames) as of 2026-05-27.

### Note on the one early reboot

In the 15-min observation immediately after enabling `power=true` on .185 at uptime ~520 s the device did a single `Software/System restart` (code 4). Investigation:

- Every `ESP.restart()` in the firmware is preceded by `prepareForPlannedRestart(reason)`, which writes to `last_planned_restart_reason`. That field was **empty** after the reboot — so no explicit code path fired.
- No scheduled-reboot policy exists; no `ESP.wdtFeed()` or `ESP.wdtDisable()` calls anywhere.
- The 30-min soak that followed (1571 → 4421 s of monotonic uptime, no reboots) and the 4-hour soak in progress show the event didn't recur.

## 2026-05-27 update: 0.2.3 isn't the full fix — 0.2.4 wdtFeed defense

The 4-hour soak surfaced a second failure mode 0.2.3 did **not** address: periodic `Software/System restart` (code 4, **not** Exception, **not** preceded by a `prepareForPlannedRestart` breadcrumb) roughly every ~60–130 minutes on .185 with `central+power=true + live CSE7766 frames`. The full soak log shows 46 reboots across the day, mostly during the chaos period of multiple OTA experiments but several in steady-state too. The state immediately before each reboot was always healthy (cstate=idle, healthy=yes, heap in normal range).

Because the reboots are code 4 with no breadcrumb, the most plausible cause is the soft-WDT (default ~3.5 s on Arduino-ESP8266) firing during a blocking BearSSL handshake. The 0.2.3 firmware has **zero** `ESP.wdtFeed()` calls anywhere — a slow handshake under low heap can starve the WDT enough to trip a reset.

**0.2.4-dev-central-safe** adds one defensive change: an `ESP.wdtFeed()` immediately before every blocking `http.POST` / `http.GET` that runs over BearSSL — three sites in `central_client.cpp` (postWithFallback, postWithoutResponseWithFallback, getWithFallback) and one in `web_server_manager.cpp`'s central-diagnostic probe. No behavioural change in the healthy path; strictly defensive. Built sha256 `6e0c6807…`, source `rebooter-firmware` commit `822c7b5`.

| | 0.2.3 wall+frames | 0.2.4 wall+frames (in flight) |
|---|---|---|
| BearSSL fragmentation exceptions | gone | gone (inherited) |
| Soft-WDT reboots during slow handshake | every ~60–130 min | TBD — soak ongoing |
| ESP.wdtFeed call sites | 0 | 4 |

Currently soaking on .185 (wall + frames + central+power) since 23:35:36 UTC. The 0.2.3 pattern would have rebooted by ~00:35 UTC; if 0.2.4 carries past that the wdtFeed is doing its job and 0.2.4 ships to stable.

Hub-side, the new `device.rebooted` event type (rebooter-droids `0.6.6`, commit `ff1d520`) now writes one event per detected uptime regression — operators can chart reboot cadence directly from `/app/events?type=device.rebooted` and compare 0.2.4 vs 0.2.3 across the fleet over time.
