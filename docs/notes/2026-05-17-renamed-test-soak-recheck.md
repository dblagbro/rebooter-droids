# Rebooter - renamed test soak recheck

Date: 2026-05-17

## Scope

Recheck the renamed-test soak status, compare hub-side/device-side rename and recovery behavior where possible, and record only concrete regressions, reliability issues, or improved findings.

## Constraints this run

- Local shell access was unavailable in the Codex workspace (`CreateProcessWithLogonW failed: 267`).
- Because of that failure, this run could not reopen any prior local soak log, project memo, or in-app browser session for a fresh live UI scrape.
- Findings below are therefore limited to repo-backed documentation and shipped-history evidence.

## Concrete improved findings

1. Rename drift has an explicit shipped fix path now.
   - `v0.5.8` added restore-after-reflash display-name push to the device via `apply_config.device_name`.
   - `v0.5.12` extended that to ordinary hub-side renames, wiring both admin PATCH and the UI rename form to enqueue `apply_config.device_name`.
   - This directly addresses the earlier class of mismatch where the hub row name changed but the device UI stayed at `Rebooter` or another stale local name.

2. Recovery now has a re-push path instead of relying on first registration only.
   - `v0.5.22` added desired-config / reported-config tracking and drift visibility.
   - `v0.5.53` added recovery-aware config re-push after a recovery transition.
   - That is a reliability improvement for renamed or restored devices that recover from an incident but need hub intent re-asserted.

3. Low-load power telemetry interpretation is safer.
   - `v0.5.66` added estimated-current semantics so `i_ma=0` is no longer interpreted as "no activity" when firmware clamped a small standby load.
   - This matters for soak interpretation on always-on but low-draw devices.

## Concrete regressions found this run

- None proven from the accessible evidence.

## Reliability risks still open

1. This run did not prove the live hub UI, live device UI, and live device/API state are currently aligned for `Rebooter - renamed test`.
   - The environment failure prevented a fresh live comparison.

2. The rename/recovery path has multiple shipped fixes across `v0.5.8`, `v0.5.12`, `v0.5.22`, and `v0.5.53`.
   - That history is consistent with a previously real drift problem.
   - The repo evidence supports "improved" more strongly than "fully revalidated live."

## Current conclusion

No new concrete regression is established in this recheck. The strongest concrete update is that rename propagation and post-recovery config reassertion both have explicit shipped fixes, which improves confidence that the renamed-test mismatch class should now self-heal or be operator-pushable when the device is reachable.
