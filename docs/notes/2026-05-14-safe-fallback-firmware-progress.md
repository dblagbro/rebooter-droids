# Safe Fallback Firmware Progress - 2026-05-14

## What changed today

I moved the current fallback candidate in a safer direction without widening the blast radius:

1. Manual 30-second recovery no longer wipes rollback state before reboot.
2. Manual recovery now marks the current boot as planned/healthy before restarting.
3. Auto-recovery now restores the last-known-good config before entering recovery mode.
4. Device status now reports whether the last-known-good config was restored.
5. The bootstrap candidate now has an explicit local build version:
   - `bootstrap-0.2.5-dev-safe`
6. The bootstrap flash helper text was updated to reflect the current main firmware line:
   - `0.1.17`

## Why these changes matter

The biggest safety problem in the in-flight recovery work was that the long-hold recovery path was too destructive. A "safe fallback" should preserve the best rollback information we have, not erase it right before rebooting into recovery.

The new behavior is better aligned with that principle:

- explicit recovery keeps the rollback path intact
- planned recovery reboots do not poison the unhealthy-boot counter
- repeated early boot failures now try to recover using the previous known-good config instead of only dropping into a stripped-down mode

This is still not the final dual-bank / immutable-rescue-slot architecture from RFC-005, but it is meaningfully safer than the prior behavior.

## Build verification

Built successfully on this workstation on 2026-05-14:

### Bootstrap
- environment: `sonoff_s31_bootstrap`
- version string: `bootstrap-0.2.5-dev-safe`
- RAM: `39.2%` (`32100 / 81920`)
- Flash: `44.9%` (`468631 / 1044464`)
- SHA256: `1841BA9B4DE465BD662C29A5DE873CA576CF8D1C724A833B44D1C7AB76FE40D8`

### Main
- environment: `sonoff_s31`
- version string: `0.1.17-dev-central`
- RAM: `49.2%` (`40324 / 81920`)
- Flash: `58.7%` (`612643 / 1044464`)
- SHA256: `C1B3156165497E4DC3FF91F4EAC7297B45607ED845CEA8071495E30BA280E7FF`

## Files touched in this pass

- `C:\dev\rebooter-firmware\include\config_manager.h`
- `C:\dev\rebooter-firmware\include\app_state.h`
- `C:\dev\rebooter-firmware\include\bootstrap_config.h`
- `C:\dev\rebooter-firmware\src\config_manager.cpp`
- `C:\dev\rebooter-firmware\src\main.cpp`
- `C:\dev\rebooter-firmware\src\web_server_manager.cpp`
- `C:\dev\rebooter-firmware\scripts\bootstrap-flash.ps1`

## Important remaining gaps

1. This is not yet the full RFC-005 architecture.
   - no immutable rescue slot enforced by partition layout
   - no true A/B/C boot-target state machine
   - no guaranteed firmware-image rollback after a bad OTA write

2. Bootstrap artifact drift still exists.
   - the local bootstrap build and the published `bootstrap/latest.bin` on `S:` are not the same binary
   - the published bootstrap versioning needs a careful release step, not a blind overwrite

3. Recovery visibility still needs work.
   - the device-side `/api/events` path has shown malformed/truncated output in field testing
   - that makes recovery diagnosis weaker than it should be

4. This candidate has been compile-verified, not field-validated on hardware yet.
   - the next real safety step is a sacrificial flash/recovery test on a bench unit

## Bench validation update on `.48`

I moved beyond compile-only validation and OTA-loaded the patched main firmware onto the bench S31 at `http://192.168.1.48/`.

### Device-side fixes included in this bench pass

1. Local UI refresh now uses `cache: 'no-store'` for API fetches.
2. Full-page refresh became tolerant of one bad endpoint instead of failing the whole screen.
3. Relay buttons now disable while a command is in flight and do an immediate plus delayed status follow-up.
4. Background status refresh errors are caught and surfaced instead of silently poisoning page behavior.
5. Event log storage is now hard-capped at `100` entries on-device, even if config asks for more.
6. Duplicate consecutive event entries within `120s` are suppressed.
7. Repetitive success-noise was removed from:
   - `Heartbeat accepted via ...`
   - `Firmware assignment already satisfied: 0.1.17-dev-central`

### Bench results after OTA

- OTA upload to `.48` was accepted over the local `/api/system/ota` endpoint.
- Device came back cleanly and remained reachable.
- Direct browser test on the device page now behaves materially better:
  - `Turn On` changed the visible relay state from `Off` to `On` within `500ms`
  - `Turn Off` changed the visible relay state from `On` to `Off` within `500ms`
  - relay state stayed correct after the delayed follow-up refresh
- `/api/status` remained healthy after the test:
  - `relay_on = false`
  - `health_state = healthy`
  - `central_state = idle`
  - `consecutive_unhealthy_boots = 0`

### What is still not fully clean

1. The persisted event log still contains old noisy entries from before this patch, so the Events panel is carrying historical clutter until those entries roll off.
2. Earlier browser console logs still showed prior `Failed to fetch` refresh errors from the unstable pre-patch period.
3. The main firmware still reports the same user-facing runtime version string:
   - `0.1.17-dev-central`
   This is expected because this bench pass changed behavior without bumping the semantic runtime label yet.

### Current conclusion

The local device UI is now in a much better place for the next stage. Relay control is no longer "click and hope." The next gate is to watch for any new serial crashes under this lighter event/logging profile, then proceed with the controlled poison-image fallback test.

### Short soak after patch

I ran a 2-minute status poll against `.48` at 5-second intervals immediately after the patched OTA:

- uptime increased monotonically from `143s` to `266s`
- `health_state` stayed `healthy`
- `central_state` stayed `idle`
- relay state stayed stable
- heap mostly sat around `25552`, with a few brief dips to `25416` and `23240`, then recovered

That is not a full-day soak, but it is a much cleaner immediate post-OTA result than the earlier unstable behavior.

## Controlled poison-image artifact is now real

I also implemented and compiled a dedicated bad-boot test environment for the next fallback proof:

- PlatformIO environment: `sonoff_s31_bad_boot_test`
- Runtime version string: `0.1.17-dev-central-badboot`
- Behavior:
  - logs that the intentional crash is armed in normal mode
  - waits `15s`
  - aborts before the normal healthy-boot mark
  - explicitly does **not** fire while in recovery mode
- RAM: `49.4%` (`40500 / 81920`)
- Flash: `58.7%` (`612979 / 1044464`)
- SHA256: `4D8A5CEF46FAA8FAA9F7A500B007A7E51A46EFB3AA39F84363F4E19ECBB2F580`

This means the next safe-fallback test can use a controlled image with a visible runtime label instead of an anonymous broken binary.

## Rollback-ready good artifact refreshed

After compiling the poison image, I rebuilt the normal main firmware again so the recovery target is ready on disk before we intentionally induce failure:

- PlatformIO environment: `sonoff_s31`
- Runtime version string: `0.1.17-dev-central`
- SHA256: `88DDE4C684BAB5CB286A650A79DB0524AF48D049FD070A1CD126400E324BA7C5`

That gives us both sides of the bench exercise ready at once:

- good rollback image
- deliberate bad-boot image

## Recommended next step

Do a controlled hardware validation of this candidate on a sacrificial S31:

1. serial-flash bootstrap candidate
2. let bootstrap pull main firmware
3. trigger explicit recovery
4. verify last-known-good preservation
5. induce early-boot failures and verify auto-recovery behavior
6. only then decide whether to publish a new bootstrap artifact to `S:\code\rebooter-droids\data\firmware\bootstrap\`

## Bench proof update: recovery is now less destructive

I patched the recovery path after the first bad-image run showed that it was clearing too much state.

### Firmware changes in this pass

- automatic recovery no longer clears:
  - saved Wi-Fi credentials
  - `central.enabled`
  - enrollment token
  - device ID
  - device token
- automatic recovery no longer forces the setup portal before trying normal Wi-Fi
- central runtime is now suppressed in recovery mode without rewriting the saved config
- monitor/watchdog logic is suspended in recovery mode
- setup AP is now configured as an open network in code

### New compiled artifacts after the recovery patch

- good main image (`sonoff_s31`)
  - SHA256: `813FDB377FE7640755FE5E554ECE16B12262C8B3B1D49CFA80A414CCECCB8386`
- bad-boot image (`sonoff_s31_bad_boot_test`)
  - SHA256: `C2469C610999D364316EB5772C62E0CEC062B77B5DC8EAB01A6C7048D5A2A082`
- bootstrap image (`sonoff_s31_bootstrap`)
  - SHA256: `9A9D1833C602857E5FD35A48F8168F478581E320F3FDB82484294820867545AA`

### What the second bad-image bench pass proved on `.48`

Using the patched good image as the known-good baseline, I installed the deliberate bad-boot image again on `192.168.1.48`.

This time, after the intentional early-boot crashes:

- the device returned to `192.168.1.48` automatically
- `wifi_connected` stayed `true`
- `in_captive_portal` stayed `false`
- `recovery_mode` became `true`
- `last_known_good_restored` became `true`
- `central_state` became `recovery_mode`

That is the first clean proof that the fallback path can now recover over the normal LAN path without forcing the operator back through provisioning.

### Important limitation still in play

This bench pass did **not** prove central-token preservation end to end on `.48`, because that device had already lost its central credentials before this patch landed.

Current live state after restoring the good image:

- firmware: `0.1.17-dev-central`
- relay: restored to `On`
- Wi-Fi: healthy on `192.168.1.48`
- central:
  - `enabled = true`
  - `enrollment_token = ""`
  - `device_id = ""`
  - state observed as either:
    - `registered_no_token`
    - `announce_transport_failed`

### Current diagnosis on the adoption problem

The missing-credentials behavior is now split into two different issues:

1. **device-side destructive recovery**
   - fixed in this pass for future runs

2. **hub/device rekey path after credentials are already gone**
   - still unresolved
   - the device does not cleanly re-enter a visible pending-adoption state on this hub
   - the hub appears to recognize the MAC as an already-known device and does not hand back a fresh credential path

### Additional improvement item found during this pass

Relay restore still depends on the persisted `last_relay_on` snapshot. During the recovery test, that stored value was temporarily `false`, so the recovered image came back with the relay off until I explicitly turned it back on and re-persisted the state.

That means we should treat **relay-state snapshot timing** as a separate improvement item alongside the fallback work.

## Follow-up firmware pass: button behavior + calmer identity-failure handling

I applied another firmware pass after the recovery proof to keep the bench moving forward.

### Button behavior changes

The firmware now assumes this physical-button mapping:

- short press: relay toggle
- 3-second hold: reboot the Rebooter
- 10-second hold: enter recovery mode
- 30-second hold: factory reset

I preserved a manual recovery path on `10s` so we do not lose the rescue lever while the fallback architecture is still being hardened.

### New API recovery hook

Added a local authenticated endpoint:

- `POST /api/system/recovery-boot`

This requests a recovery boot without requiring a long physical button hold.

### Central-state behavior improvement

When the hub reports `registered` but the device has no local device token, the firmware now:

- logs a clearer one-time message:
  - `Hub reports device already registered but local device token is missing`
- stays in `registered_no_token`
- backs off re-announce attempts much more aggressively instead of thrashing

This does **not** solve the missing-token problem by itself, but it makes the device much calmer and more diagnosable until the hub-side rekey/adoption path is repaired.

### Current good-image artifact after this pass

- good main image (`sonoff_s31`)
  - SHA256: `A505F2CC5433B0A1DCC60028C7BB50D6B201F0275F4B2F1E49E4908D28E0EAFE`

### Bench result after installing the updated good image on `.48`

After OTA-installing the new good image to `.48`:

- device came back on `192.168.1.48`
- relay stayed `On`
- Wi-Fi stayed connected
- health returned to `healthy`
- central remained unregistered, as expected
- event log showed the new calmer identity-failure message instead of the noisier old announce/register chatter

## Recovery overlay verification pass

I then patched `ConfigManager::restoreLastKnownGood()` to stop restoring a stale rollback snapshot blindly.

### What changed

Recovery still restores the behavioral config from the last-known-good file, but now overlays the freshest low-risk live fields from the currently active config before writing the recovered config back:

- `lastRelayOn`
- central enabled flag
- central base URLs
- central enrollment token
- central alias / site ID
- central device ID
- central device token
- central poll / heartbeat intervals

This is meant to stop recovery from regressing relay state or central identity simply because `config.lkg` was one save behind.

### New good-image artifact after this fix

- good main image (`sonoff_s31`)
  - SHA256: `94B816A461028D551FBC0C9490D3EBCC4B337A10168857D5366ACB707D12EFA8`

### New bad-image artifact after this fix

- bad-boot image (`sonoff_s31_bad_boot_test`)
  - SHA256: `836295541AAA1D6A27F66DCE3193F20D9AF74E1A9E495D1716AB13EF4790BB93`

### Bench proof on `.48`

I ran another full local OTA failure cycle:

1. good image on `.48`, healthy, relay on
2. OTA to the deliberate bad image
3. wait for the intentional crash loop
4. observe automatic recovery
5. OTA the good image back

Observed recovery state on the bad image:

- `firmware_version = 0.1.17-dev-central-badboot`
- `recovery_mode = true`
- `last_known_good_restored = true`
- `wifi_connected = true`
- `in_captive_portal = false`
- `relay_on = true`
- `central_state = recovery_mode`

Observed restored config during recovery:

- `last_relay_on = true`

This is the first pass where the recovered bad image stayed online **and** kept the relay-on state through recovery, which strongly suggests the stale-snapshot overlay fix worked as intended for relay-state preservation.

### After restoring the good image again

After OTA-installing the good image back onto `.48`:

- firmware returned to `0.1.17-dev-central`
- relay stayed `On`
- Wi-Fi stayed connected
- `last_relay_on` remained `true`
- current UI text and live `/app.js` both now correctly say the setup network is open by default

### What remains unresolved after this pass

Central identity is still not restored on `.48`, because the device is already operating from a no-token state:

- `central_enabled = true`
- `central_registered = false`
- `central_state` still settles into `registered_no_token`

So at this point:

- Wi-Fi preservation through fallback: **proven**
- relay-state preservation through fallback: **proven on this bench pass**
- recovery UX/message consistency: **updated and verified live**
- central token / re-adoption recovery: **still blocked on the rekey/adoption path**

## Additional mitigation: protected full-config backup path

Because the remaining blocker is central identity loss on already-known devices, I added a device-side mitigation for future upgrades:

### New endpoint

- `GET /api/system/config-backup`

Behavior:

- requires local auth
- additionally requires that an admin password is actually provisioned
- returns the full config including:
  - `central.device_id`
  - `central.device_token`

This avoids exposing the device token on the normal public `/api/config` endpoint, while still giving us a safe operator path to capture a real full backup before a risky upgrade.

### Restore compatibility

I also updated the existing authenticated `POST /api/config/save` path so it now accepts and restores:

- `central.device_id`
- `central.device_token`

That means a protected backup taken from `config-backup` can be posted back through the normal save path to restore central identity later if needed.

### Current artifact after this mitigation

- good main image (`sonoff_s31`)
  - SHA256: `67B17312A5C5EC6C67A2A295029DCAF133B853F27EB3ADC280A81A03AA43655D`

### Practical value

This does not solve the hub-side rekey path on its own, but it gives operators a better safety net for the next wave of device web-UI OTA upgrades:

1. set a local admin password
2. export protected full config backup
3. perform OTA
4. if central identity is lost, restore the saved full config directly
