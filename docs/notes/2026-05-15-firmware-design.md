# Firmware Design Notes

Last updated: 2026-05-14

## Local UI and API design rules

1. Public reads must be safe by default.
2. Protected writes and privileged diagnostics must require local auth.
3. The browser UI should work against the same local API contract that
   automation and operators use.
4. When auth is provisioned, the UI should make the locked/unlocked
   state obvious and should store the local auth secret only for the
   current browser tab session.

## Recovery design rules

1. Recovery should preserve operational state whenever safely possible.
2. Recovery should favor returning the device to a reachable LAN state.
3. Recovery behavior should be visible in both local status and event log.
4. Intentional restarts must not be treated like crash boots on the next boot.

## Event log design rules

1. Persisted event history must stay interpretable across reboots.
2. Boot-relative time is acceptable if the payload also exposes enough
   context to separate one boot from another.

Current approach:
- `seq`
- `boot_id`
- `ts`
- `ts_basis = uptime_seconds`

## Power telemetry design rules

1. Keep measured current and estimated current semantically distinct.
2. Low-load standby behavior may legitimately produce nonzero watts with
   estimated-only current.
3. Telemetry should preserve enough signal for analytics to decide whether to
   trust a field rather than forcing the firmware to over-normalize it.

## Time-sync design rules

1. G2 timing work needs real wall-clock timestamps, not only uptime seconds.
2. Wall-clock instrumentation should be additive and safe to omit when NTP is
   unavailable.
3. Power uploads should continue to include uptime-relative timing even after
   wall-clock fields are added.

## Central defaults

Firmware defaults should only include live, healthy central targets.
Broken fallback defaults create operator noise and mislead diagnostics.

## Testing design rules

1. Security regressions should be covered with direct API checks.
2. Local UI auth regressions should be covered at least by automated
   regression checks on the auth/header flow.
3. Runtime validation on the live bench device remains required before
   shipping firmware artifacts broadly.
4. For UI/auth fixes, proxy-served or static repo checks are necessary but
   not sufficient; the actual device-served LittleFS assets must also be
   exercised on the bench device.
