# Pass To Rebooter-Droids Team From Firmware

Date: 2026-05-15

## Short version

Firmware progress is still real, and the important update is that the current
center of gravity has moved from vague OTA/recovery suspicion to a more specific
ESP8266 heap-churn mitigation.

What is solid now:

- real CSE7766 telemetry exists on live devices
- richer heartbeat/status fields exist
- wall-clock timing instrumentation exists and is now being sampled live
- low-load current semantics are explicit in the newer dev line
- `.48` and `.69` are both verified on `0.1.29-dev-central-safe`
- `.48` survived central re-enable on `0.1.29` past the old crash window
- `.67` and `.30` have now also crossed from the old `0.1.25` recovery line to
  healthy `0.1.29` steady-state operation with live small-load power telemetry
- `.69` has been reconnected to the hub and is now online with real power rows,
  but it is currently doing so under a new live row (`lab-69`) because its old
  central identity had been lost from the device

What is not yet fully closed:

- `.225` is still the main outlier on `0.1.29`
- with both central and power enabled, `.225` still re-enters
  `recovery_mode` with `reset_reason = "Exception"`
- the hub now has a duplicate-identity cleanup question for `.69`:
  - live row: `dev_01KRQBRSG1BZ5SR87QQ2KSSVFT` (`lab-69`)
  - stale row: `dev_01KR9VZKGW72DS7DAQEFAV3T58` (`Erica's R.L. Speaker`)
- the long-running capture now shows:
  - `.48` healthy on `0.1.29`
  - `.67` healthy on `0.1.29`
  - `.69` healthy on `0.1.29`
  - `.30` healthy on `0.1.29`
  - `.225` still in `recovery_mode` on `0.1.29`
- `.48` central stability is now verified, but real CSE7766 visibility on that
  exact unit still needs a no-serial-adapter check

## Current firmware reality

### Healthy/verified direction

- `0.1.27-dev-central-safe`
  - proved one clean normal boot on `.225`
  - proved new timing fields can populate live
  - proved stale `RuntimeStatus` leakage was a real bug

- `0.1.28-dev-central-safe`
  - added `reset_reason` so unexpected restarts became more diagnosable

- `0.1.29-dev-central-safe`
  - event-log persistence is now deferred / coalesced instead of rewriting
    LittleFS on every event
  - explicit flushes remain before reboot / recovery / factory-reset transitions
  - routine successful power-upload messages are no longer persisted every batch
  - central heartbeat / poll / firmware-check / power-upload work is staggered so
    TLS-heavy actions do not burst back-to-back on ESP8266
  - `.48` OTA to `0.1.29` passed and survived central re-enable for 200+ seconds
    without reproducing the prior delayed crash window
  - `.69` OTA to `0.1.29` also passed cleanly
  - widening pass after that:
    - `.67` reached healthy `0.1.29` operation and is now reporting about
      `119.6V / 2.3W`
    - `.30` reached healthy `0.1.29` operation and is now reporting about
      `119.8V / 5.0W`
    - `.69` was re-enrolled into central and is now back online on the hub with
      live CSE7766 rows at about `119.1V / 0W`
    - `.225` remains the residual exception/recovery outlier and should be
      treated as the blocker for broader confidence

Current shared artifacts:

- `S:\code\rebooter-droids\data\firmware\dev\rebooter-0.1.27-dev-central-safe.bin`
- `S:\code\rebooter-droids\data\firmware\dev\rebooter-0.1.28-dev-central-safe.bin`
- `S:\code\rebooter-droids\data\firmware\dev\rebooter-0.1.29-dev-central-safe.bin`

## Important fields the hub team should be ready to consume

Already useful:

- `recovery_mode`
- `auto_recovery_triggered`
- `last_known_good_restored`
- `previous_boot_different_firmware`
- `central_state`
- `reported_config`
- `time_synced`
- `wall_clock_unix_ms`
- `power_last_sample_unix_ms`

New power semantics:

- `power_current_ma` is measured current
- `power_current_estimated` indicates standby-load estimate-only conditions
- `power_estimated_current_ma` gives the estimate when measured current is
  intentionally suppressed below the low-current clamp

Analytics / UI implication:

- do not treat `i_ma = 0` as equivalent to "no activity" when estimated-current
  semantics are present

## What firmware needs from hub/backend right now

1. Treat the `0.1.25` recovery-line devices as unstable for product conclusions.
2. Prefer `0.1.29` evidence when reasoning about the current device heartbeat /
   timing / recovery contract.
3. Treat `.225` as the remaining blocker instead of assuming the whole line is
   still suspect.
4. Clean up or intentionally preserve the stale pre-re-enrol `.69` row; do not
   leave the duplicate identity ambiguous by accident.
5. Keep cross-modal timing work pointed at the new wall-clock fields, not only
   uptime-relative timestamps.
6. Treat the current G2 sample as promising but early:
   - `.48` low-RTT samples are currently averaging about `+10.6 ms`
   - `.69` low-RTT samples are currently averaging about `-21.7 ms`
   - keep measuring before locking architecture assumptions
7. Keep `A4`, `E5`, and `G2` alive as research deliverables:
   - Enphase PLC link quality
   - Theengs / free BLE covariates
   - empirical timing measurement

## Recommended team alignment

Priority order still looks like:

1. absorb the richer firmware heartbeat / recovery / config contract
2. keep power-path work grounded in real device telemetry, not synthetic-only assumptions
3. prioritize zero-hardware-cost expansion paths before new hardware-heavy ones
4. design cross-modal query/storage with correlation as a first-class use case

## Firmware-side next steps

1. keep the 24-hour capture running
2. treat `.225` as the primary residual blocker on `0.1.29`
3. decide what to do with the duplicate `.69` hub identity
4. keep the G2 timing pass running on `.48` and `.69`
5. improve the local OTA harness so reboot-shortened connections are not
   mislabeled as upload failures
6. only after `.225` is better understood, decide whether to widen power-enabled
   rollout to more Erica speaker devices
