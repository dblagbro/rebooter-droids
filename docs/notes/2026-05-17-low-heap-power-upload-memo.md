# Rebooter-Droids Firmware Memo: Low-Heap Power Upload Findings (2026-05-17)

## Summary

The remaining ESP8266 blocker is now much narrower than the earlier
"central is unstable" diagnosis.

Current best truth on low-heap Sonoff S31 units (`.225`, `.69`):

- stable:
  - `central=true, power=false`
  - `central=false, power=true`
- unstable:
  - `central=true, power=true`

This means the local power monitor itself is not the remaining problem, and the
base central heartbeat/poll loop is not the remaining problem by itself.
The remaining failure path is the **standalone HTTPS power upload path** on
low-heap devices.

## Live evidence

Useful artifacts:

- `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\watch-225-69-0.1.38-2026-05-17-live.ndjson`
- `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\watch-225-0.1.39-central-power-live.ndjson`
- `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\watch-69-0.1.40-central-power-live.ndjson`

Observed progression:

1. `0.1.38-dev-central-safe`
   - compact-heartbeat change was real and valuable
   - `.225` and `.69` both survived when power uploads were disabled

2. `0.1.39-dev-central-safe`
   - added compact power uploads:
     - latest-sample-only on low heap
     - reduced power fields
     - minimum 60s upload interval
   - `.225` still dropped off the LAN during `central=true, power=true`

3. `0.1.40-dev-central-safe`
   - removed unnecessary response-body allocation from power uploads
   - reset stale power-upload scheduling when central is disabled/re-enabled
   - added extra startup delay before first compact power upload
   - `.69` still rebooted with `reset_reason="Exception"` during
     `central=true, power=true`

## Firmware conclusion

The remaining issue is likely beyond "trim a few more JSON fields" territory.
The constrained ESP8266 path appears not to tolerate a separate HTTPS
`/device/power-samples` transport reliably under current heap limits.

## Requested hub/product alignment

Please be ready to discuss or prototype one of these directions:

1. **Heartbeat-carried compact power summary for constrained devices**
   - firmware sends the latest power snapshot inside heartbeat
   - hub ingests it without requiring a separate power POST on ESP8266-class hardware

2. **Lighter dedicated ingest contract for constrained devices**
   - smaller success response
   - possibly lighter route semantics / less response parsing burden

3. **Class-based transport policy**
   - ESP8266-class devices use the lighter path
   - roomier devices can keep the standalone `/device/power-samples` path

## Operational note

- `.69` is back online and stable again with `power.enabled=false`
- `.225` dropped off the LAN during the `0.1.39` single-device power-upload soak
  and may need manual recovery if it does not return on its own
