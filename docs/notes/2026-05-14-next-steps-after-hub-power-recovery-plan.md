# Next Steps After Hub Power + Recovery Planning - 2026-05-14

This planning pass is complete. The next concrete team moves should be:

1. Pick the first implementation wave:
   - `v0.5.24` power live-sample/query surface
   - `v0.5.25` first `/app/power`
   - `v0.5.27` rules/integrations contract normalization
2. Lock the recovery/status field contract with firmware for:
   - central enabled/registered truth
   - recovery mode
   - last-known-good restored
   - rebind-needed states
3. Decide whether desired-config schema reconciliation happens before or during
   the recovery-status wave, since protected-backup and safe-fallback work have
   raised its priority.
4. Keep doc updates in the same ships, not as a later cleanup fantasy.

The planning note to execute from is:

- `docs/notes/2026-05-14-hub-power-recovery-alignment-plan.md`
