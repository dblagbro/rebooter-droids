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

## Event log design rules

1. Persisted event history must stay interpretable across reboots.
2. Boot-relative time is acceptable if the payload also exposes enough
   context to separate one boot from another.

Current approach:
- `seq`
- `boot_id`
- `ts`
- `ts_basis = uptime_seconds`

## Central defaults

Firmware defaults should only include live, healthy central targets.
Broken fallback defaults create operator noise and mislead diagnostics.

## Testing design rules

1. Security regressions should be covered with direct API checks.
2. Local UI auth regressions should be covered at least by automated
   regression checks on the auth/header flow.
3. Runtime validation on the live bench device remains required before
   shipping firmware artifacts broadly.
