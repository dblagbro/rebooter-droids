# B16 Research Reply And Firmware Findings

Date: 2026-05-15

## Shared direction after hub-team alignment

Accepted direction:

1. prioritize zero-hardware-cost sources ahead of new-hardware paths
2. keep plug firmware on direct HTTPS ingest; do not force MQTT into device firmware
3. use a common ingest envelope with modality-specific physical stores
4. keep SunSpec read-only in all v1.x work
5. treat SDR as advanced / opt-in and Ting as complementary

Updated hub/backend priority order:

1. P0: consume the richer firmware heartbeat / recovery / central-state / `reported_config` contract
2. P1: finish the power data path and trust signals
3. P2: solar -> router telemetry -> managed-switch telemetry -> deeper Home Assistant bridge
4. P3: lock cross-modal schema decisions with cross-modal correlation treated as a first-class use case

Exploratory items that should not be silently dropped:

- `A4` Enphase PLC link-quality discovery
- `E5` Theengs / free BLE covariates
- `G2` empirical cross-device clock-sync measurement

## Changed firmware baseline since the earlier research note

The analytics work is no longer blocked on synthetic-only plug telemetry.

What is real now:

- live CSE7766 voltage / power telemetry on field devices
- richer heartbeat / local status / `reported_config` payloads
- real loaded-power observations from non-bench devices

Observed field examples during the 2026-05-15 characterization pass:

- `.30` showed about `118.8-119.2V` and about `4.9-5.0W`
- `.225` showed about `118.8-119.0V` and between `0W` and about `2.2W`

## Current firmware findings that matter to analytics

### 1. Low-load current needs explicit semantics

Standby loads can legitimately produce:

- nonzero `p_w`
- near-mains `v_v`
- measured `i_ma = 0`

This is caused by the current clamp below about `50mA`, not by a dead meter.

Firmware response in `0.1.27-dev-central-safe`:

- keep `power_current_ma` as measured current
- add `power_current_estimated`
- add `power_estimated_current_ma`
- add `i_ma_est` on uploaded power rows when current is estimate-only

Analytics implication:

- do not treat `i_ma = 0` as equivalent to "no electrical activity" when
  `power_current_estimated = true`

### 2. Invalid-frame rate is material

20-second delta sample during the field characterization pass:

- `.30`: `127` valid / `86` invalid
- `.225`: `112` valid / `101` invalid

Analytics implication:

- invalid-frame rate should be preserved as a data-quality signal, not discarded

### 3. G2 timing required additive firmware instrumentation

Before today, firmware only emitted uptime-relative timing.

Added in `0.1.27-dev-central-safe`:

- `time_synced`
- `wall_clock_unix_ms`
- `power_last_sample_unix_ms`
- `sampled_unix_ms` on uploaded power rows when wall-clock sync is available

This is deliberately additive. Uptime-based timing remains present.

### 4. Post-OTA recovery behavior is the top firmware risk

Confirmed field problem on `0.1.25-dev-central-safe`:

- some devices can enter recovery on the first observed boot after OTA
- planned reboots from the recovery line can also be miscounted as early-boot failures

Firmware response in `0.1.27-dev-central-safe`:

- boot-state now ignores incomplete-boot strikes across firmware-version changes
- intentional restart paths now write a `planned_restart` marker so the next boot
  does not count that transition as a crash-like early boot

Live verification note:

- `.225` now verifies the new timing fields live on `0.1.27-dev-central-safe`
- the stale-runtime-state portion of the recovery bug is better understood, but
  the broader OTA/recovery instability is still under investigation

## Immediate analytics-facing asks

1. use the new low-load current semantics when interpreting standby loads
2. treat invalid-frame counts as a first-class quality signal
3. plan to consume the new wall-clock timing fields once a `0.1.26` device is live
4. write the next dev note against real plug data, not synthetic-only assumptions

## Immediate firmware-side next steps

1. keep the 24-hour capture running and review the trace
2. get one controlled device onto `0.1.26-dev-central-safe`
3. verify the new wall-clock and estimated-current fields live
4. keep the hub/research notes aligned with the real field behavior
