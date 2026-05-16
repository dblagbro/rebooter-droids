# Rebooter - renamed test soak recheck - 2026-05-15

Scope:
- recheck the renamed-test device soak from the hub side
- compare current hub-side behavior against the documented device UI/API contract
- record only concrete regressions, reliability issues, or improved findings

Concrete findings:

1. No new rename-drift regression found in the repo-side evidence.
   - The name-sync path already shipped in `v0.5.12` (`device` rename now enqueues `apply_config.device_name`).
   - Desired-config / drift support shipped in `v0.5.22`.
   - The reconciled 2026-05-15 schema memo still lists `device_name` as the one key validated end-to-end for drift round-trip.
   - I found no newer repo note or changelog entry indicating the renamed-test device has fallen back to a hub-name vs local-name mismatch.

2. The meaningful new issue is power-telemetry quality, not rename coherence.
   - `CHANGELOG.md` `v0.5.55` records that the `.48` snapshot showed roughly 35% invalid power frames.
   - The same release adds hub-side `source_kind`, decoded `source_flags`, and synthetic-taint surfacing so that mixed or degraded power data is no longer silently averaged into charts.
   - This is a concrete reliability signal on the renamed-test device soak.

3. Hub/device contract alignment improved materially on 2026-05-15.
   - `v0.5.51` persists the richer heartbeat status/recovery fields.
   - `v0.5.52` maps those fields into explicit UI states (`central_disabled`, `recovery_mode`, `rebind_needed`, `transport_stale`) instead of collapsing them into generic offline.
   - `v0.5.53` reconciles the hub-side `apply_config` schema doc to the firmware-owned contract and keeps recovery re-push behind the desired-config feature flag.

Open reliability gap still visible:
- Beyond `device_name`, the remaining desired-config keys are documented as only "accepted", not yet individually validated end-to-end through `reported_config` drift echo.
- That is an implementation-confidence gap, but not a newly surfaced regression on the renamed-test soak.
