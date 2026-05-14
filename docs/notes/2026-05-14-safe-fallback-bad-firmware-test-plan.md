# Safe Fallback Bad Firmware Test Plan - 2026-05-14

## Goal

Prove, on the bench device at `http://192.168.1.48/`, that we can:

1. start from a known-good stable bootstrap + main firmware state
2. intentionally install a bad firmware build over OTA
3. watch the device fail in a controlled, repeatable way
4. verify recovery behavior
5. use the recovery path to OTA back to a good build
6. confirm the device returns to normal service without requiring another full manual rescue

## Important reality check

The current firmware is **safer than before**, but it is **not yet** the full RFC-005 architecture.

Today we have:

- bootstrap loader that can pull main firmware over Wi-Fi
- planned-restart boot-health marking
- unhealthy-boot counting
- auto-recovery mode after repeated early boot failures
- last-known-good **config** restore before recovery mode
- recovery-mode local UI / AP path

Today we do **not** yet have:

- true immutable rescue slot enforced by partition layout
- true A/B firmware image rollback
- guaranteed bootloader-level fallthrough from a corrupt main image into a permanent rescue image

That means the first bad-firmware test should be designed to validate the **current recovery model honestly**, not pretend we already have image-bank rollback.

## Bench target

- Device: `192.168.1.48`
- Name: `Rebooter - renamed test`
- Role: sacrificial bench unit only
- Reason: already on the workbench with local UI + serial visibility

## Stage 0: Pre-test stabilization

Do not run the bad-firmware exercise until these are true:

1. `0.1.17-dev-central` or newer good main build boots reliably on `.48`
2. local UI is reachable
3. relay commands work correctly from the UI
4. current unexplained main-firmware crashes are understood or fixed
5. serial logs are being captured during the test window

Current reason to hold here:

- the latest serial log shows a real main-firmware panic after central activity
- we should not confuse an existing instability with the intentional bad-firmware signal

## Stage 1: Known-good baseline snapshot

Before any poison test:

1. record good bootstrap artifact:
   - version string
   - SHA256
2. record good main artifact:
   - version string
   - SHA256
3. capture baseline:
   - `/api/status`
   - `/api/config`
   - `/api/events`
   - local UI screenshots
   - serial boot log

Baseline success conditions:

- `central_state = idle`
- `wifi_connected = true`
- `auto_recovery_triggered = false`
- `consecutive_unhealthy_boots = 0`
- relay control works

## Stage 2: Build a controlled bad firmware

Do **not** use a random broken binary.

Instead, build a **deliberate poison test image** with a compile-time flag, for example:

- `SAFE_FALLBACK_TEST_BAD_BOOT=1`

Recommended behavior of the poison image:

1. boot normally far enough to:
   - mount LittleFS
   - load config
   - start event log
   - run `beginBootSession()`
   - initialize Wi-Fi / web UI / recovery plumbing
2. if booting in **normal mode**:
   - log an explicit test message like:
     - `Intentional bad-firmware test crash armed`
   - wait a short predictable interval, e.g. `10-15s`
   - `abort()` or `panic()` before the normal `markBootHealthy()` point
3. if booting in **recovery mode**:
   - **do not crash**
   - keep recovery UI alive
   - allow OTA of a good build

This matters because a poison image that crashes too early gives us less signal, while a poison image that also crashes in recovery mode defeats the entire point of the bench test.

## Stage 3: OTA install the poison image

Install path:

- use the local OTA path from the device UI on `.48`
- optionally repeat via hub OTA after the local path is proven

Record:

- OTA upload result
- reboot timing
- first bad boot serial log
- second bad boot serial log

Expected behavior:

1. poison image installs
2. device reboots into poison main
3. poison image crashes before healthy mark
4. next boot increments unhealthy counter
5. after threshold (`2` currently), device enters auto-recovery path

## Stage 4: Validate current recovery behavior

What we expect from the current implementation:

1. `auto_recovery_triggered = true`
2. `recovery_mode = true`
3. `last_known_good_restored = true`
4. central disabled in recovery mode
5. local recovery UI still reachable
6. setup AP fallback still available if Wi-Fi path breaks

What we should capture:

- serial logs for both failed boots and the recovery boot
- `/api/status` poll every 3 seconds
- `/api/events`
- screenshots of the recovery UI

Current implementation details to validate:

- unhealthy threshold is `2`
- recovery mode marks boot healthy after `15s`
- normal mode marks boot healthy after `90s`

## Stage 5: OTA back to a good build from recovery

From the recovery-capable device:

1. upload the known-good main firmware
2. wait for reboot
3. verify:
   - stable boot
   - `auto_recovery_triggered = false`
   - `consecutive_unhealthy_boots = 0`
   - local UI healthy
   - central heartbeat returns to normal

This is the key practical proof for the current architecture:

- **a bad OTA can be recovered over OTA using the fallback-safe path**

## Stage 6: Optional second pass through hub OTA

Once local OTA recovery is proven:

1. assign the poison build through the hub
2. let `.48` take it through normal hub flow
3. validate the same failure / recovery behavior
4. use the recovery path to install the good build again

This tells us whether the recovery story works through the real fleet path, not just the local web UI.

## Safety guardrails

Use these rules during the test:

1. `.48` only
2. do not test on shared live devices
3. keep serial attached the whole time
4. keep known-good bootstrap and main binaries on hand locally
5. stop immediately if:
   - recovery UI does not come back after the expected threshold
   - device disappears from LAN for more than `10 minutes`
   - serial logs suggest filesystem corruption rather than the intentional crash path

## Success criteria for the current architecture

This test is successful if all of these happen:

1. poison image installs over OTA
2. poison image fails before healthy mark
3. unhealthy-boot threshold triggers recovery mode
4. recovery UI remains usable
5. known-good main image can be OTA-installed from recovery
6. device returns to stable normal operation without manual deep surgery

## What this test will NOT prove yet

Even if the above passes, it still does **not** prove:

- bootloader-level firmware-bank rollback
- rescue from an image that cannot reach enough application code to honor recovery logic
- rescue from fully corrupt image layouts without app-level help

That requires the later, stricter test below.

## Future stricter test once full fallback architecture exists

After the real immutable rescue / dual-bank design lands, run a second class of test:

1. install a truly bad main image that never reaches app initialization
2. verify the bootloader or rescue partition falls through automatically
3. verify the rescue image pulls a fresh good main image
4. verify no serial intervention is needed

That is the real final “bulletproof fallback” proof.

## Recommended next order of operations

1. fix the currently observed main-firmware instability on `.48`
2. clean up the local UI state-sync bugs found during relay testing
3. add the controlled poison-build flag and behavior
4. run the current-architecture bad-firmware bench test on `.48`
5. only after that, publish wider confidence claims

## Progress note after the second bench pass

Stages 1 through 5 are now substantially complete for the **Wi-Fi / recovery-path** part of the test:

- the poison image installs over OTA
- it fails before the healthy mark
- repeated early-boot failures trigger recovery mode
- the recovered device now comes back on the normal LAN IP without forcing the captive portal
- the good image can be reinstalled over OTA afterward

The remaining unproven item is **central credential preservation / rekey behavior**, because `.48` had already lost its hub credential state before the less-destructive recovery patch was installed.

That means the next full proof pass for central identity needs one of:

1. a freshly adopted `.48` with a known-good central credential state, or
2. a second sacrificial device that is still centrally healthy before the poison-image test begins
