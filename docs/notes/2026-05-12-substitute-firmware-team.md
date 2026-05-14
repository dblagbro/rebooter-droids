# Substitute Firmware Team — Status & Handoff Document

**Date:** 2026-05-12
**Team:** Substitute firmware team (workstation, Claude Code session)
**Context:** Original firmware team offline since 2026-05-09 (Windows workstation Chrome Remote Desktop stuck). Substitute team picked up firmware work on 2026-05-10.

---

## 1. Source Code

- **Original repo:** `github.com/dblagbro/rebooter-firmware` — last commit `1bf0be78` (2026-05-09), "Add bootstrap OTA flow and central client integration"
- **Working fork:** `/mnt/s/code/rebooter-firmware-flash-fork/` on workstation (192.168.18.150)
- **Fork not yet pushed to GitHub** — droids team has requested this

---

## 2. Firmware Versions Shipped

### Bootstrap 0.2.4
- **Location:** `/mnt/s/code/rebooter-droids/data/firmware/bootstrap/rebooter-bootstrap-0.2.4.bin`
- **Fix:** OTA URL was pointing to old path `https://www.voipguru.org/rebooter-firmware-main.bin` — changed to `https://www.voipguru.org/rebooter/firmware/stable/latest.bin` with `www2.voipguru.org` fallback
- **Status:** Deployed to hub, served via nginx static path

### Main Firmware 0.1.12-dev-central (CURRENT)
- **SHA256:** `c2db7360d25d7199c627c98da15eb0fd55a3579b3a883eac6e448947b7ea9831`
- **Size:** 605,008 bytes
- **Location:** `/mnt/s/code/rebooter-droids/data/firmware/stable/rebooter-0.1.12-dev-central.bin` and `latest.bin`
- **Hub registry:** `fwr_01KRCTVKXKQ9HGF6HK7Z4K0PQJ`
- **Status:** Registered, tested on bench device, NOT deployed to production fleet

---

## 3. Changes Made (0.1.7 through 0.1.12)

### 0.1.7 — WiFi Fallback Fix (root cause of original outage)
**Problem:** Main firmware only had GF's WiFi (SpectrumSetup-4D) baked in. After OTA from bootstrap, devices at operator's house dropped off because VoIPguru_wifi wasn't configured.

**Fix in `dev_wifi_config.h`:**
- Primary: VoIPguru_wifi / whowantstoknow
- Secondary: SpectrumSetup-4D / smalltruck536
- Open network fallback enabled (RSSI >= -80)
- AP mode captive portal after 2 minutes

**Fix in `wifi_manager.cpp`:** Complete rewrite of WiFi connection flow:
1. Try saved WiFiManager credentials
2. Try VoIPguru_wifi (primary)
3. Try SpectrumSetup-4D (secondary)
4. Scan for open networks
5. Retry loop for 2 minutes
6. Fall back to AP mode captive portal (stays open indefinitely)

### 0.1.8 — Command Dispatcher Fix (hub memo Priority 3)
**Problem:** `pollCommands()` in `central_client.cpp` received commands from hub but only logged them — never executed or posted results. This was the bug at the 0.1.0 GitHub source level that the original team supposedly fixed in 0.1.6 but never pushed upstream.

**Fix:** Added `executeCommand()` function handling:
- `relay_on` / `relay_off` — sets relay, persists state, posts result
- `relay_cycle` — power off for N seconds, back on, posts running + completed
- `set_mode` — smart_plug / internet_watchdog / device_watchdog
- `reboot` — posts result then ESP.restart()

Added `postCommandResult()` to post back to `/device/command-result`.

Updated `main.cpp` to pass `&g_relay` to `CentralClient::begin()`.

### 0.1.9 — Full Local Web UI + BearSSL Fix
**Full web UI per SPECS.md §8.2:**
- Internet Watchdog settings panel (9 fields: targets, failure threshold, power off, holdoff, max cycles incident/hour, cooldown, DNS refresh, recovery stability)
- Device Watchdog settings panel (8 fields)
- Notifications panel (enabled, type, webhook URL/method/token, event toggles)
- System panel (reboot + factory reset buttons)
- Tooltips on every field via `?` hover icons
- Mode-conditional panel visibility
- Unsaved-changes guard (beforeunload)
- Confirmation dialogs for destructive actions
- Central status on dashboard

**Config API fix:** Added missing `dns_refresh_seconds`, `recovery_stability_seconds`, and entire `notifications` block to `GET /api/config` JSON output.

**Status API:** Added `free_heap` and `firmware_version` fields.

**BearSSL heap fix:** Added `client->setBufferSizes(1024, 1024)` to both `postWithFallback()` and `getWithFallback()` in central_client.cpp. Without this, the larger firmware left ~25KB free heap and BearSSL's default 16KB RX buffer couldn't be allocated, causing silent TLS connection failure (HTTP -1). This was the cause of post-OTA silence on the 0.1.9 test device.

### 0.1.10 — LAN Management Endpoints
- `GET /api/lan/scan?start=X&end=Y` — scans local subnet for HTTP devices on port 80, identifies rebooters by checking `/api/status` response. Limited to 50 IPs per request to prevent WDT reset. Uses `ESP.wdtFeed()` + `yield()` between probes.
- `POST /api/lan/proxy` — forwards HTTP GET/POST to another LAN IP. Body: `{"ip":"x.x.x.x","path":"/api/status","method":"GET","body":"..."}`. Returns target's response.
- `POST /api/system/ota-pull` — device downloads firmware from given URL and self-flashes. Body: `{"url":"http://..."}`. Uses `ESPhttpUpdate`.

### 0.1.11 — Hub-Commanded LAN Operations
Added command types to central client dispatcher so hub can remotely trigger LAN operations:
- `lan_scan` — payload: `{"start":1,"end":50}`. Scans subnet, posts results back via command-result.
- `lan_proxy` — payload: `{"ip":"x.x.x.x","path":"/api/status","method":"GET","body":"..."}`. Forwards request to LAN peer, posts response.
- `lan_ota_push` — payload: `{"ip":"x.x.x.x","url":"http://..."}`. Tells peer device to pull firmware via its `/api/system/ota-pull`.

Added transport error logging (`central_transport` events with HTTP codes).

### 0.1.12 — command_id Bug Fix
**Bug:** Device was reading `cmd["id"]` but hub sends `cmd["command_id"]`. Result: command_id was always empty in command-result POST, hub rejected with 400 validation_failed, device retried in a loop.

**Fix:** Changed `cmd["id"]` to `cmd["command_id"]` in `pollCommands()`.

**Verified:** Full round-trip working — hub sends lan_scan → device executes → results posted back with correct command_id → hub stores in command_results table.

---

## 4. Test Device

- **Name:** "Devin's Lab Test rebooter 1"
- **IP:** 192.168.18.185 (VoIPguru_wifi LAN)
- **device_id:** `dev_01KRCAGQ30AY1B74JA2MX9EYV1`
- **Firmware:** 0.1.12-dev-central
- **Status:** Healthy, heartbeating, all features tested including hub-commanded LAN scan round-trip
- **Enrollment token used:** `et_mfl91W61QlV5vhvqJSlhZgVfZTSAfw4l` (consumed)

---

## 5. Production Fleet State (Erica's house, 192.168.1.x)

| Device | IP | Firmware | Status |
|--------|-----|----------|--------|
| R.R. Speaker | .225 | 0.1.6-dev-central | ONLINE, heartbeating. Only working production device. |
| F.L. Speaker | .67 | 0.1.x (unknown) | SILENT ~48h. Occasionally tries to register. |
| R.L. Speaker | unknown | 0.1.x (unknown) | SILENT ~48h |
| F.R. Speaker | unknown | 0.1.x (unknown) | SILENT ~48h |
| Subwoofer | unknown | 0.1.x (unknown) | SILENT ~48h |

**Why R.R. Speaker (.225) is the only one online:** It was the only device that had the command dispatcher fix in 0.1.6. The 4 silent devices have broken firmware that lost hub connectivity.

**Hub observation:** Silent devices occasionally try to register from Erica's NAT (47.230.251.21) — they ARE alive, just in long exponential backoff.

---

## 6. BLOCKED: Fleet Recovery

### The Problem (chicken-and-egg)
- .225 needs 0.1.12 for LAN recovery features (`lan_scan`, `lan_proxy`, `lan_ota_push`)
- 0.1.6 on .225 has NO OTA-fetch mechanism — it doesn't poll `/device/firmware` and doesn't implement firmware-update commands
- Hub deployment system relies on device polling `/device/firmware` — **no shipped firmware version has ever implemented this poll**
- The DEVICE_INTEGRATION.md spec defines it (§5) but it was never coded

### What Was Tried (2026-05-12 ~03:12 UTC)
1. Operator gave green-light to push 0.1.12 to .225
2. Hub un-paused deployment assignment `fwd_01KRCTYYVH3E1R5CE1MBX390HP`
3. .225 never polled `/device/firmware` — no download, no OTA
4. Hub tried sending `check_firmware` and `start_firmware_update` commands — both returned "Firmware update commands not implemented on device"
5. Deployment re-paused. No change to .225.

### Recovery Path (requires physical LAN access)
Someone on Erica's LAN (192.168.1.x network) must run:
```bash
curl -X POST -F "firmware=@rebooter-0.1.12-dev-central.bin" http://192.168.1.225/api/system/ota
```
Or open `http://192.168.1.225/` in a browser and use the Firmware Update form to upload `rebooter-0.1.12-dev-central.bin`.

**After .225 has 0.1.12:**
1. Hub sends `lan_scan` command to .225 → finds silent devices
2. Hub sends `lan_proxy` to .225 → checks each silent device's `/api/status`
3. Hub sends `lan_ota_push` to .225 → tells each silent device to pull 0.1.12
4. Silent devices flash, reboot, register with hub → fleet back online

The firmware binary is available at:
`https://www.voipguru.org/rebooter/firmware/rebooter-0.1.12-dev-central.bin`

---

## 7. Open Items / TODO

### Must-do
1. **Implement `/device/firmware` poll** (0.1.13) — the spec defines it but no firmware version has ever coded it. This enables hub-initiated firmware deployments. Without this, every future firmware push requires either LAN access or a pre-existing OTA-pull command handler on the device.
2. **Push fork to GitHub** — `github.com/dblagbro/rebooter-firmware`. Source is currently only on workstation.
3. **Physical access to Erica's LAN** — flash .225 to unblock fleet recovery.

### Should-do
4. **Hub memo response** — items 1-5 from Memo 1 (2026-05-11) not yet formally sent back.
5. **RFC-005 §9 slot architecture** — 3-slot flash (A=safe-bootstrap, B=main, C=fallback), boot-streak validation (N=3), automatic fallback (F=3). Currently single-slot OTA with no rollback. This is the safety gate blocking remote fleet OTA without LAN access.
6. **Version string discipline** — hub memo Priority 5.

### Nice-to-have
7. Add `apply_config` command handler for hub-driven config changes.
8. Add firmware SHA256 verification on OTA-pull.
9. More aggressive post-OTA first heartbeat (5s instead of 60s) for faster confirmation.

---

## 8. Tools & Environment

- **Workstation:** 192.168.18.150 (VoIPguru_wifi)
- **PlatformIO:** 6.1.19 (via pipx)
- **esptool:** 5.2.0 (via pipx)
- **Serial flash:** FTDI FT231X on direct USB port (not through hub — Genesys Logic USB hub is flaky, error -71/-110). Port: `/dev/ttyUSB0`. Baud: 19200.
- **Flash command:** `esptool --port /dev/ttyUSB0 --baud 19200 --no-stub write-flash --flash-size detect --no-compress 0x00000 <firmware.bin>`
- **Build:** `cd /mnt/s/code/rebooter-firmware-flash-fork && ~/.local/bin/pio run -e sonoff_s31`
- **Bootstrap build:** `~/.local/bin/pio run -e sonoff_s31_bootstrap`
- **LAN scanner script:** `sudo /mnt/s/code/rebooter-firmware-flash-fork/tools/scan-rebooters.sh`
- **Droids team contact:** `ssh tmrwww01 'claude --resume "Rebooter-Droids" --print "message"'`

---

## 9. Key Files Modified

| File | What changed |
|------|-------------|
| `include/dev_wifi_config.h` | Dual SSID + open fallback + AP mode timeout |
| `src/wifi_manager.cpp` | Complete WiFi fallback chain rewrite |
| `include/bootstrap_config.h` | Fixed OTA URL, added www2 fallback, bumped to 0.2.4 |
| `src/bootstrap_main.cpp` | Multi-SSID fallback, dual OTA URL with primary/secondary |
| `src/central_client.cpp` | Command dispatcher (execute + result), BearSSL buffers, transport logging, LAN commands, command_id fix |
| `include/central_client.h` | Added relay pointer, executeCommand, postCommandResult |
| `src/web_server_manager.cpp` | Full web UI (HTML/CSS/JS), LAN endpoints, config API fix, status API additions, ota-pull |
| `include/firmware_version.h` | Version bumps through 0.1.12 |
| `src/main.cpp` | Pass relay to central client |

---

## 10. Lessons Learned

1. **BearSSL heap exhaustion is silent.** ESP8266 with HTTPS needs ~22KB contiguous heap for TLS. Larger PROGMEM strings don't use heap directly, but additional code/data does. `setBufferSizes(1024, 1024)` reduces BearSSL buffers from 16KB to 1KB each — essential on firmware > ~550KB.

2. **LAN scan must feed WDT.** Scanning 254 IPs synchronously on ESP8266 triggers the watchdog timer. Limit to 50 IPs per request, call `ESP.wdtFeed()` + `yield()` between probes, create fresh `WiFiClient` per probe.

3. **Hub sends `command_id`, not `id`.** The field name in the commands array differs from what you might expect. Always verify against the actual wire format.

4. **OTA-fetch must be in the firmware BEFORE you need it.** The hub deployment system assumes devices poll `/device/firmware` — this was specced but never implemented. Without it, remote firmware updates are impossible. This should be Priority 1 for any future firmware version.

5. **USB hubs can be flaky.** The Genesys Logic GL3520 hub gave protocol errors (-71) and timeouts (-110). Plugging the FTDI adapter directly into the computer's USB port resolved it. Hub firmware is mask ROM — not updatable.
